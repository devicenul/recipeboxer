"""Google Antigravity SDK agent orchestrator coordinating the RecipeBoxer digitization pipeline."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from recipeboxer.agent.session import PipelineSession
from recipeboxer.config import Settings, get_settings
from recipeboxer.correlation.optimizer import PairingOptimizer
from recipeboxer.correlation.semantic import GeminiSemanticVerifier
from recipeboxer.exporter.plan_to_eat import PlanToEatExporter
from recipeboxer.extraction.extractor import RecipeExtractor
from recipeboxer.ingestion.runner import (
    IngestionConfig,
    IngestionMode,
    IngestionProgress,
    IngestionRunner,
)
from recipeboxer.ingestion.scanner import ScannedCard, scan_card_directory
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.hitl import CardPairApproval, HITLDecision
from recipeboxer.models.pipeline import (
    PipelineStartRequest,
    PipelineState,
    ProgressMetrics,
)
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.storage.cache import FingerprintCache
from recipeboxer.vision.fingerprint import GeminiVisualExtractor

logger = logging.getLogger(__name__)


class RecipeBoxerAgent:
    """Coordinates multi-stage recipe card digitization using Google Antigravity patterns."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        cache: Optional[FingerprintCache] = None,
        visual_extractor: Optional[Any] = None,
        semantic_verifier: Optional[GeminiSemanticVerifier] = None,
        pairing_optimizer: Optional[PairingOptimizer] = None,
        recipe_extractor: Optional[Any] = None,
        csv_exporter: Optional[PlanToEatExporter] = None,
    ) -> None:
        """Initialize the agent orchestrator with required sub-stage components."""
        self.settings = settings or get_settings()
        self.cache = cache or FingerprintCache(self.settings.cache_db_path)
        self.visual_extractor = visual_extractor or GeminiVisualExtractor()
        self.semantic_verifier = semantic_verifier or GeminiSemanticVerifier()
        self.pairing_optimizer = pairing_optimizer or PairingOptimizer(verifier=self.semantic_verifier)
        self.recipe_extractor = recipe_extractor or RecipeExtractor()
        self.csv_exporter = csv_exporter or PlanToEatExporter()

    def run_in_background(self, session: PipelineSession) -> asyncio.Task:
        """Launches the complete digitization pipeline in a background asyncio task.

        Args:
            session: Configured PipelineSession instance.

        Returns:
            asyncio.Task running the background orchestration workflow.
        """
        task = asyncio.create_task(self.run_pipeline(session))
        session.pipeline_task = task
        return task

    def start_pipeline(self, session: PipelineSession) -> asyncio.Task:
        """Alias for run_in_background."""
        return self.run_in_background(session)

    async def run_pipeline(self, session: PipelineSession) -> None:
        """Executes all stages of the RecipeBoxer pipeline sequentially."""
        try:
            # -------------------------------------------------------------
            # Stage 1: Ingestion & Scanning
            # -------------------------------------------------------------
            session.state = PipelineState.INGESTING
            scans_dir = self._resolve_scans_dir(session.config)
            scans_path = Path(scans_dir)

            if not scans_path.exists() or not scans_path.is_dir():
                raise FileNotFoundError(f"Scans directory does not exist: '{scans_dir}'")

            cards = scan_card_directory(
                scans_path,
                supported_extensions=self.settings.supported_image_extensions,
            )

            if not cards:
                raise ValueError(f"No supported recipe card image scans found in '{scans_dir}'")

            session.cards = cards
            session.scanned_paths = [Path(c.file_path) for c in cards]
            session.progress_metrics.total_scans = len(cards)

            if session.is_cancelled:
                session.state = PipelineState.CANCELLED
                session.end_time = time.monotonic()
                return

            # -------------------------------------------------------------
            # Stage 2: Visual Fingerprinting
            # -------------------------------------------------------------
            session.state = PipelineState.FINGERPRINTING
            ingestion_config = self._resolve_ingestion_config(session.config)

            # Determine extractor callable
            extractor_fn = self._resolve_extractor_callable(session)

            async def _fingerprint_progress(prog: IngestionProgress) -> None:
                session.progress_metrics.fingerprinted = prog.processed_cards

            effective_cache = session.cache or self.cache
            runner = IngestionRunner(
                config=ingestion_config,
                cache=effective_cache,
                extractor_fn=extractor_fn,
            )

            ingestion_result = await runner.run(
                cards=cards,
                progress_callback=_fingerprint_progress,
                cancellation_token=session.cancellation_token,
            )

            if session.is_cancelled:
                session.state = PipelineState.CANCELLED
                session.end_time = time.monotonic()
                return

            session.fingerprints = {fp.card_id: fp for fp in ingestion_result.fingerprints}
            session.progress_metrics.fingerprinted = len(session.fingerprints)

            if not session.fingerprints:
                raise RuntimeError("Visual fingerprinting yielded 0 valid fingerprints.")

            # -------------------------------------------------------------
            # Stage 3: Two-Stage Front/Back Correlation & Pairing Optimization
            # -------------------------------------------------------------
            session.state = PipelineState.CORRELATING
            fingerprint_list = list(session.fingerprints.values())

            # Check if custom optimizer or verifier on session
            optimizer = session.optimizer or self.pairing_optimizer
            if session.verifier is not None:
                optimizer = PairingOptimizer(verifier=session.verifier)
            elif session.mock_client is not None and hasattr(session.mock_client, "verify_correlation"):
                # Use mock client verification directly if provided in test session
                optimizer = self._build_mock_optimizer(session, fingerprint_list)

            if isinstance(optimizer, PairingOptimizer):
                correlation_result = await optimizer.optimize(fingerprint_list)
            else:
                correlation_result = await self._run_custom_optimizer(optimizer, fingerprint_list)

            if session.is_cancelled:
                session.state = PipelineState.CANCELLED
                session.end_time = time.monotonic()
                return

            session.proposals = correlation_result.proposals
            session.unmatched_card_ids = correlation_result.unmatched_card_ids
            # Store unassigned cards as VisualFingerprints
            session.unassigned_cards = [
                session.fingerprints[cid]
                for cid in correlation_result.unmatched_card_ids
                if cid in session.fingerprints
            ]
            session.progress_metrics.proposals = len(session.proposals)

            # -------------------------------------------------------------
            # Stage 4: Human-in-the-Loop (HITL) Pause Boundary
            # -------------------------------------------------------------
            resumed = await session.pause_for_hitl()
            if not resumed or session.is_cancelled:
                session.state = PipelineState.CANCELLED
                session.end_time = time.monotonic()
                return

            # -------------------------------------------------------------
            # Stage 5: Multimodal Structured Recipe Extraction
            # -------------------------------------------------------------
            session.state = PipelineState.EXTRACTING
            recipes: List[RecipeSchema] = []

            for approval in session.approvals:
                if session.is_cancelled:
                    session.state = PipelineState.CANCELLED
                    session.end_time = time.monotonic()
                    return

                if approval.decision == HITLDecision.REJECT:
                    logger.info(f"[{session.session_id}] Skipping rejected proposal {approval.proposal_id}")
                    continue

                front_fp = session.fingerprints.get(approval.front_card_id)
                back_fp = (
                    session.fingerprints.get(approval.back_card_id)
                    if approval.back_card_id
                    else None
                )

                if not front_fp:
                    logger.warning(
                        f"[{session.session_id}] Missing front card '{approval.front_card_id}' in fingerprints."
                    )
                    continue

                try:
                    recipe = await self._extract_single_recipe(session, front_fp, back_fp)
                    if recipe:
                        recipes.append(recipe)
                        session.progress_metrics.extracted += 1
                except Exception as exc:
                    logger.error(
                        f"[{session.session_id}] Recipe extraction failed for {front_fp.card_id}: {exc}"
                    )
                    session.errors.append(f"Extraction failed for {front_fp.card_id}: {str(exc)}")

            session.recipes = recipes

            if not session.recipes and session.progress_metrics.approved > 0:
                raise RuntimeError("Recipe extraction failed for all approved cards.")

            # -------------------------------------------------------------
            # Stage 6: Plan To Eat CSV Serialization & Export
            # -------------------------------------------------------------
            session.state = PipelineState.EXPORTING
            self.settings.ensure_directories()

            export_file_name = f"recipeboxer_{session.session_id}.csv"
            export_path = self.settings.export_dir / export_file_name

            exporter = self.csv_exporter
            if hasattr(exporter, "export_to_csv_string"):
                csv_text = exporter.export_to_csv_string(session.recipes)
            elif hasattr(exporter, "export_to_csv"):
                csv_text = exporter.export_to_csv(session.recipes)
            else:
                from recipeboxer.exporter.plan_to_eat import serialize_recipes_to_plan_to_eat_csv
                csv_text = serialize_recipes_to_plan_to_eat_csv(session.recipes)

            if hasattr(exporter, "export_to_file"):
                exporter.export_to_file(session.recipes, export_path)
            else:
                export_path.write_text(csv_text, encoding="utf-8")

            session.csv_content = csv_text
            session.csv_output_path = str(export_path.resolve())

            session.progress_metrics.percent_complete = 100.0
            session.state = PipelineState.COMPLETED
            session.end_time = time.monotonic()
            logger.info(
                f"[{session.session_id}] Pipeline completed successfully. Export: {session.csv_output_path}"
            )

        except asyncio.CancelledError:
            session.state = PipelineState.CANCELLED
            session.end_time = time.monotonic()
            logger.info(f"[{session.session_id}] Pipeline execution cancelled.")

        except Exception as exc:
            session.state = PipelineState.FAILED
            session.end_time = time.monotonic()
            session.error_message = str(exc)
            session.errors.append(str(exc))
            logger.error(f"[{session.session_id}] Pipeline failed: {exc}", exc_info=True)

    def _resolve_scans_dir(self, config: Any) -> str:
        """Extract scans_dir from config object or dict."""
        if hasattr(config, "scans_dir") and config.scans_dir:
            return str(config.scans_dir)
        if hasattr(config, "directory_path") and config.directory_path:
            return str(config.directory_path)
        if isinstance(config, dict):
            if "scans_dir" in config and config["scans_dir"]:
                return str(config["scans_dir"])
            if "directory_path" in config and config["directory_path"]:
                return str(config["directory_path"])
        raise ValueError("Configuration missing required 'scans_dir' parameter.")

    def _resolve_ingestion_config(self, config: Any) -> IngestionConfig:
        """Extract IngestionConfig from configuration."""
        mode = IngestionMode.PARALLEL
        max_concurrency = self.settings.max_concurrency
        slow_batch_size = self.settings.slow_batch_size
        slow_batch_interval_seconds = self.settings.slow_batch_interval_seconds

        if hasattr(config, "mode") and config.mode:
            try:
                mode = IngestionMode(config.mode)
            except ValueError:
                mode = IngestionMode.PARALLEL
        elif isinstance(config, dict) and "mode" in config and config["mode"]:
            try:
                mode = IngestionMode(config["mode"])
            except ValueError:
                mode = IngestionMode.PARALLEL

        if hasattr(config, "max_concurrency") and config.max_concurrency is not None:
            max_concurrency = config.max_concurrency
        elif isinstance(config, dict) and config.get("max_concurrency") is not None:
            max_concurrency = config["max_concurrency"]

        if hasattr(config, "slow_batch_size") and config.slow_batch_size is not None:
            slow_batch_size = config.slow_batch_size
        elif isinstance(config, dict) and config.get("slow_batch_size") is not None:
            slow_batch_size = config["slow_batch_size"]

        if (
            hasattr(config, "slow_batch_interval_seconds")
            and config.slow_batch_interval_seconds is not None
        ):
            slow_batch_interval_seconds = config.slow_batch_interval_seconds
        elif isinstance(config, dict) and config.get("slow_batch_interval_seconds") is not None:
            slow_batch_interval_seconds = config["slow_batch_interval_seconds"]

        return IngestionConfig(
            mode=mode,
            max_concurrency=max_concurrency,
            slow_batch_size=slow_batch_size,
            slow_batch_interval_seconds=slow_batch_interval_seconds,
        )

    def _resolve_extractor_callable(self, session: PipelineSession) -> Callable[..., Any]:
        """Resolves the card fingerprint extraction callable."""
        ext = session.extractor or session.mock_client or self.visual_extractor

        if hasattr(ext, "analyze_fingerprint"):
            # Mock client interface (image_path: Path, sha256_hash: str)
            async def _mock_extractor(card: ScannedCard) -> VisualFingerprint:
                fp = await ext.analyze_fingerprint(Path(card.file_path), card.sha256_hash)
                if not isinstance(fp, VisualFingerprint) and hasattr(fp, "model_dump"):
                    return VisualFingerprint.model_validate(fp.model_dump(mode="json"))
                return fp

            return _mock_extractor

        if callable(ext):
            return ext

        if hasattr(ext, "extract_fingerprint"):
            return ext.extract_fingerprint

        return self.visual_extractor.extract_fingerprint

    def _build_mock_optimizer(
        self, session: PipelineSession, fingerprints: List[VisualFingerprint]
    ) -> PairingOptimizer:
        """Adapts mock client into PairingOptimizer with custom semantic verifier."""
        mock = session.mock_client

        class MockVerifierAdapter:
            async def verify_correlation(
                self, front_fp: VisualFingerprint, back_fp: VisualFingerprint, heuristic_score: float
            ) -> CorrelationProposal:
                prop = await mock.verify_correlation(front_fp, back_fp, heuristic_score)
                from recipeboxer.models.correlation import CorrelationProposal
                if not isinstance(prop, CorrelationProposal) and hasattr(prop, "model_dump"):
                    return CorrelationProposal.model_validate(prop.model_dump())
                return prop

            async def verify_candidate_proposals(
                self, candidate_pairs: List[Any]
            ) -> List[CorrelationProposal]:
                from recipeboxer.models.correlation import CorrelationProposal
                proposals = []
                for front_fp, back_fp, h_score in candidate_pairs:
                    prop = await mock.verify_correlation(front_fp, back_fp, h_score)
                    if not isinstance(prop, CorrelationProposal) and hasattr(prop, "model_dump"):
                        prop = CorrelationProposal.model_validate(prop.model_dump())
                    proposals.append(prop)
                return proposals

        return PairingOptimizer(verifier=MockVerifierAdapter())

    async def _run_custom_optimizer(
        self, optimizer: Any, fingerprints: List[VisualFingerprint]
    ) -> CorrelationResult:
        """Executes a custom optimizer callable or object."""
        if hasattr(optimizer, "optimize"):
            res = optimizer.optimize(fingerprints)
            if asyncio.iscoroutine(res):
                res = await res
            from recipeboxer.models.correlation import CorrelationResult
            if not isinstance(res, CorrelationResult) and hasattr(res, "model_dump"):
                return CorrelationResult.model_validate(res.model_dump())
            return res
        raise ValueError(f"Invalid optimizer instance: {optimizer}")

    async def _extract_single_recipe(
        self,
        session: PipelineSession,
        front_fp: VisualFingerprint,
        back_fp: Optional[VisualFingerprint] = None,
    ) -> Optional[RecipeSchema]:
        """Extracts a structured recipe schema using session extractor, mock, or default."""
        recipe = None
        if session.recipe_extractor_fn:
            res = session.recipe_extractor_fn(front_fp, back_fp)
            recipe = await res if asyncio.iscoroutine(res) else res
        elif session.mock_client and hasattr(session.mock_client, "extract_recipe"):
            recipe = await session.mock_client.extract_recipe(front_fp, back_fp)
        elif hasattr(self.recipe_extractor, "extract_recipe"):
            recipe = await self.recipe_extractor.extract_recipe(front_fp, back_fp)
        elif callable(self.recipe_extractor):
            res = self.recipe_extractor(front_fp, back_fp)
            recipe = await res if asyncio.iscoroutine(res) else res

        if recipe is not None and not isinstance(recipe, RecipeSchema) and hasattr(recipe, "model_dump"):
            return RecipeSchema.model_validate(recipe.model_dump())
        return recipe


# Alias for backward compatibility and design harmony
AgentOrchestrator = RecipeBoxerAgent
