"""Google Antigravity SDK agent orchestrator coordinating the RecipeBoxer digitization pipeline."""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
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
from recipeboxer.models.correlation import CorrelationProposal, CorrelationResult
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.failures import (
    ExtractionUnavailable,
    FailureReason,
    FingerprintUnavailable,
    PipelineStage,
    SemanticVerificationUnavailable,
    StageFailure,
    StageUnavailableError,
)
from recipeboxer.models.hitl import CardPairApproval, HITLDecision, HitlItem, HitlItemType
from recipeboxer.models.pipeline import (
    FailedRecipe,
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

    def _has_usable_model_or_client(self, session: PipelineSession) -> bool:
        """Determines if a model client or usable API key is present for analysis (INV-AC6, D5)."""
        # Injected client or mock directly on session
        if session.mock_client is not None:
            return True
        if session.extractor is not None:
            return True
        if session.verifier is not None:
            return True
        if session.optimizer is not None:
            return True
        if session.recipe_extractor_fn is not None:
            return True
        if hasattr(session, "config") and session.config:
            if isinstance(session.config, dict):
                if session.config.get("mock_client") or session.config.get("gemini_api_key"):
                    return True
            elif getattr(session.config, "gemini_api_key", None):
                return True
        if hasattr(session, "settings") and session.settings is not None and not isinstance(session.settings, Settings):
            return True

        # Injected clients on agent components
        if getattr(self.visual_extractor, "client", None) is not None:
            return True
        if getattr(self.semantic_verifier, "client", None) is not None:
            return True
        if getattr(self.recipe_extractor, "client", None) is not None:
            return True

        # Custom / mock component instances passed to agent
        if self.visual_extractor is not None and not isinstance(self.visual_extractor, GeminiVisualExtractor):
            return True
        if self.recipe_extractor is not None and not isinstance(self.recipe_extractor, RecipeExtractor):
            return True

        # Usable Gemini API key
        candidate_keys = [
            getattr(session.settings, "gemini_api_key", None),
            getattr(self.settings, "gemini_api_key", None),
            getattr(self.visual_extractor, "api_key", None),
            getattr(self.recipe_extractor, "api_key", None),
            os.environ.get("RECIPEBOXER_GEMINI_API_KEY"),
            os.environ.get("GEMINI_API_KEY"),
        ]
        for k in candidate_keys:
            if k and str(k).strip():
                return True

        return False

    def _load_baseline(
        self, session: PipelineSession
    ) -> Tuple[Optional[Any], Dict[str, Any], List[RecipeSchema]]:
        """Loads baseline manifest, card hashes, and existing recipes for incremental duplicate skipping."""
        baseline_path = (
            session.baseline_project_path
            or getattr(session.config, "baseline_project_path", None)
            or (session.config.get("baseline_project_path") if isinstance(session.config, dict) else None)
        )
        baseline_sid = (
            session.baseline_session_id
            or getattr(session.config, "baseline_session_id", None)
            or (session.config.get("baseline_session_id") if isinstance(session.config, dict) else None)
        )

        if not baseline_path and not baseline_sid:
            return None, {}, []

        baseline_cards_by_hash: Dict[str, Any] = {}
        baseline_recipes: List[RecipeSchema] = []
        manifest_obj: Optional[Any] = None

        if baseline_path:
            p = Path(baseline_path)
            if not p.exists() or not p.is_file():
                raise FileNotFoundError(f"Baseline .recipeboxer project archive not found: '{baseline_path}'")

            import zipfile
            from recipeboxer.storage.archive import validate_archive_manifest

            try:
                with zipfile.ZipFile(p, "r") as zf:
                    if "manifest.json" not in zf.namelist():
                        raise ValueError(f"Archive '{baseline_path}' is missing required 'manifest.json'")
                    manifest_bytes = zf.read("manifest.json")
                    manifest_obj = validate_archive_manifest(manifest_bytes)
            except Exception as exc:
                raise ValueError(f"Failed to read baseline archive '{baseline_path}': {exc}") from exc

            for c in manifest_obj.cards:
                if c.sha256_hash:
                    baseline_cards_by_hash[c.sha256_hash.lower()] = c

            for r in manifest_obj.recipes:
                baseline_recipes.append(
                    RecipeSchema(
                        recipe_id=r.recipe_id,
                        title=r.title,
                        description=r.description,
                        yield_servings=r.yield_servings,
                        prep_time=r.prep_time,
                        cook_time=r.cook_time,
                        total_time=r.total_time,
                        ingredients=r.ingredients,
                        directions=r.directions,
                        tags=r.tags,
                        source_notes=r.source_notes,
                        card_ids=r.card_ids,
                        image_urls=r.image_urls,
                        created_at=r.created_at,
                        updated_at=r.updated_at,
                    )
                )

        elif baseline_sid:
            from recipeboxer.storage.session_store import SessionStore
            store = SessionStore(self.settings.cache_db_path)
            detail = store.get_session_detail(baseline_sid)
            if detail is None:
                raise ValueError(f"Baseline session '{baseline_sid}' not found in database.")

            for c in detail.cards:
                c_hash = c.get("sha256_hash") if isinstance(c, dict) else getattr(c, "sha256_hash", None)
                if c_hash:
                    baseline_cards_by_hash[str(c_hash).lower()] = c

            baseline_recipes = list(detail.recipes)

        return manifest_obj, baseline_cards_by_hash, baseline_recipes

    async def run_pipeline(self, session: PipelineSession) -> None:
        """Executes all stages of the RecipeBoxer pipeline sequentially."""
        try:
            # -------------------------------------------------------------
            # Stage 1: Ingestion, Baseline Loading & Duplicate Partitioning
            # -------------------------------------------------------------
            session.state = PipelineState.INGESTING

            manifest_obj, baseline_cards_by_hash, baseline_recipes = self._load_baseline(session)
            session.baseline_manifest = manifest_obj
            session.baseline_recipes = list(baseline_recipes)

            cards = list(session.cards)
            if not cards:
                # Check for remote source provider
                src_provider = getattr(session.config, "source_provider", None)
                if isinstance(session.config, dict) and not src_provider:
                    src_provider = session.config.get("source_provider")

                if src_provider and src_provider.lower() not in ("local", ""):
                    from recipeboxer.sources.base import SourceProviderConfig
                    from recipeboxer.sources.registry import get_source_provider
                    from recipeboxer.storage.cache import RemoteAssetCache

                    src_container = getattr(session.config, "source_container_id", None)
                    src_items = getattr(session.config, "source_item_ids", None)
                    src_cfg = getattr(session.config, "source_config", None)
                    if isinstance(session.config, dict):
                        src_container = src_container or session.config.get("source_container_id")
                        src_items = src_items or session.config.get("source_item_ids")
                        src_cfg = src_cfg or session.config.get("source_config")

                    cfg_obj = None
                    if isinstance(src_cfg, dict):
                        cfg_obj = SourceProviderConfig(**src_cfg)
                    elif isinstance(src_cfg, SourceProviderConfig):
                        cfg_obj = src_cfg

                    provider = get_source_provider(src_provider, config=cfg_obj)
                    staging_dir = self.settings.cache_dir / "sessions" / session.session_id / "scans"
                    staging_dir.mkdir(parents=True, exist_ok=True)

                    if not src_items:
                        list_res = await provider.list_items(container_id=src_container)
                        src_items = [it.id for it in list_res.items]

                    asset_cache = RemoteAssetCache(self.settings.cache_db_path)
                    cards = await provider.stream_download_items(
                        item_ids=src_items,
                        target_dir=staging_dir,
                        concurrency=self.settings.max_concurrency,
                        asset_cache=asset_cache,
                    )
                    if not cards:
                        raise ValueError(f"No recipe card scans could be retrieved from source provider '{src_provider}'")
                else:
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

            effective_cache = session.cache or self.cache

            # Partition cards into skipped duplicate cards vs new cards
            skipped_cards: List[ScannedCard] = []
            new_cards: List[ScannedCard] = []

            for card in cards:
                card_hash = card.sha256_hash.lower() if card.sha256_hash else ""
                if card_hash and card_hash in baseline_cards_by_hash:
                    skipped_cards.append(card)
                    base_c = baseline_cards_by_hash[card_hash]

                    fp = getattr(base_c, "fingerprint", None)
                    if fp is None and isinstance(base_c, dict):
                        fp = base_c.get("fingerprint") or base_c.get("metadata")

                    if fp is not None:
                        if isinstance(fp, dict):
                            fp = VisualFingerprint.model_validate(fp)
                        else:
                            fp = fp.model_copy()
                        fp.card_id = card.card_id
                        fp.image_path = str(card.file_path)
                        session.fingerprints[card.card_id] = fp
                        if effective_cache:
                            try:
                                effective_cache.put(fp)
                            except Exception:
                                pass
                    else:
                        cached_fp = effective_cache.get_by_hash(card_hash) if effective_cache else None
                        if cached_fp:
                            cached_fp = cached_fp.model_copy()
                            cached_fp.card_id = card.card_id
                            cached_fp.image_path = str(card.file_path)
                            session.fingerprints[card.card_id] = cached_fp
                else:
                    new_cards.append(card)

            session.cards = cards
            session.scanned_paths = [Path(c.file_path) for c in cards]
            session.skipped_cards = skipped_cards
            session.new_cards = new_cards
            session.progress_metrics.total_scans = len(cards)
            session.progress_metrics.skipped_cards = len(skipped_cards)
            session.progress_metrics.newly_processed_cards = len(new_cards)
            session.progress_metrics.baseline_recipes_count = len(baseline_recipes)
            session.progress_metrics.fingerprinted = len(session.fingerprints)

            logger.info(
                f"[{session.session_id}] Stage 1 Complete: {len(cards)} total scans, "
                f"{len(skipped_cards)} skipped duplicates, {len(new_cards)} new cards, "
                f"{len(baseline_recipes)} baseline recipes."
            )

            if session.is_cancelled:
                session.state = PipelineState.CANCELLED
                session.end_time = time.monotonic()
                return

            # -------------------------------------------------------------
            # INV-AC6, D5: Fail Fast if No Usable Key and No Injected Client
            # -------------------------------------------------------------
            if not self._has_usable_model_or_client(session):
                no_key_failure = StageFailure(
                    stage=PipelineStage.FINGERPRINT,
                    reason=FailureReason.NO_API_KEY,
                    message="No Gemini API key or injected LLM client available. Analysis is blocked. Please configure an API key to proceed.",
                    card_ids=[c.card_id for c in session.cards],
                    retryable=False,
                    details={"blocked_by": "INV-AC6"},
                )
                session.add_failure(no_key_failure)
                session.enqueue_hitl_failure(no_key_failure)
                session.state = PipelineState.FAILED
                session.error_message = no_key_failure.message
                session.errors.append(no_key_failure.message)
                session.end_time = time.monotonic()
                logger.warning(f"[{session.session_id}] Analysis blocked: {no_key_failure.message}")
                return

            # -------------------------------------------------------------
            # Stage 2: Visual Fingerprinting (Processed ONLY for new cards)
            # -------------------------------------------------------------
            if new_cards:
                session.state = PipelineState.FINGERPRINTING
                ingestion_config = self._resolve_ingestion_config(session.config)
                extractor_fn = self._resolve_extractor_callable(session)

                async def _fingerprint_progress(prog: IngestionProgress) -> None:
                    session.progress_metrics.fingerprinted = len(skipped_cards) + prog.processed_cards

                runner = IngestionRunner(
                    config=ingestion_config,
                    cache=effective_cache,
                    extractor_fn=extractor_fn,
                )

                try:
                    ingestion_result = await runner.run(
                        cards=new_cards,
                        progress_callback=_fingerprint_progress,
                        cancellation_token=session.cancellation_token,
                    )
                except StageUnavailableError as sue:
                    fail = sue.to_failure()
                    session.add_failure(fail)
                    session.enqueue_hitl_failure(fail)
                    ingestion_result = IngestionResult(
                        fingerprints=[],
                        failed_cards=[(c.card_id, str(sue)) for c in new_cards],
                        failures=[fail],
                    )
                except Exception as exc:
                    fail = StageFailure(
                        stage=PipelineStage.FINGERPRINT,
                        reason=FailureReason.UNKNOWN,
                        message=f"Fingerprinting failed: {exc}",
                        card_ids=[c.card_id for c in new_cards],
                        retryable=False,
                        details={"error_type": type(exc).__name__},
                    )
                    session.add_failure(fail)
                    session.enqueue_hitl_failure(fail)
                    ingestion_result = IngestionResult(
                        fingerprints=[],
                        failed_cards=[(c.card_id, str(exc)) for c in new_cards],
                        failures=[fail],
                    )

                if session.is_cancelled:
                    session.state = PipelineState.CANCELLED
                    session.end_time = time.monotonic()
                    return

                # Record stage failures from runner
                for failure in getattr(ingestion_result, "failures", []):
                    if failure not in session.failures:
                        session.add_failure(failure)
                        session.enqueue_hitl_failure(failure)

                for fp in ingestion_result.fingerprints:
                    session.fingerprints[fp.card_id] = fp
                session.progress_metrics.fingerprinted = len(session.fingerprints)

                # Count degraded fingerprints (degraded=True)
                degraded_count = sum(
                    1 for fp in session.fingerprints.values()
                    if getattr(fp, "degraded", False) is True or (isinstance(fp, dict) and fp.get("degraded") is True)
                )
                session.degraded_fingerprints_count = degraded_count
                session.progress_metrics.degraded_fingerprints = degraded_count

                if not session.fingerprints and not session.failures:
                    raise RuntimeError("Visual fingerprinting yielded 0 valid fingerprints.")
            else:
                logger.info(
                    f"[{session.session_id}] Stage 2 Fingerprinting bypassed: 0 new cards to process."
                )

            # -------------------------------------------------------------
            # Stage 3: Two-Stage Correlation & Pairing Optimization
            # -------------------------------------------------------------
            if new_cards:
                session.state = PipelineState.CORRELATING
                new_fps = [session.fingerprints[c.card_id] for c in new_cards if c.card_id in session.fingerprints]

                optimizer = session.optimizer or self.pairing_optimizer
                if session.verifier is not None:
                    optimizer = PairingOptimizer(verifier=session.verifier)
                elif session.mock_client is not None and hasattr(session.mock_client, "verify_correlation"):
                    optimizer = self._build_mock_optimizer(session, new_fps)

                try:
                    if isinstance(optimizer, PairingOptimizer):
                        correlation_result = await optimizer.optimize(new_fps)
                    else:
                        correlation_result = await self._run_custom_optimizer(optimizer, new_fps)
                except StageUnavailableError as sue:
                    fail = sue.to_failure()
                    session.add_failure(fail)
                    session.enqueue_hitl_failure(fail)
                    correlation_result = CorrelationResult(
                        proposals=[],
                        unmatched_card_ids=[fp.card_id for fp in new_fps],
                    )
                except Exception as exc:
                    fail = StageFailure(
                        stage=PipelineStage.CORRELATION,
                        reason=FailureReason.UNKNOWN,
                        message=f"Correlation optimization failed: {exc}",
                        card_ids=[fp.card_id for fp in new_fps],
                        retryable=False,
                        details={"error_type": type(exc).__name__},
                    )
                    session.add_failure(fail)
                    session.enqueue_hitl_failure(fail)
                    correlation_result = CorrelationResult(
                        proposals=[],
                        unmatched_card_ids=[fp.card_id for fp in new_fps],
                    )

                if session.is_cancelled:
                    session.state = PipelineState.CANCELLED
                    session.end_time = time.monotonic()
                    return

                session.proposals = list(correlation_result.proposals)
                session.unmatched_card_ids = list(correlation_result.unmatched_card_ids)

                assigned_ids = {cid for p in session.proposals for cid in p.ordered_card_ids}
                for cid in correlation_result.unmatched_card_ids:
                    if cid not in assigned_ids and cid in session.fingerprints:
                        fp = session.fingerprints[cid]
                        unmatched_proposal = CorrelationProposal(
                            proposal_id=f"prop_{uuid.uuid4().hex[:8]}",
                            ordered_card_ids=[cid],
                            part_count=1,
                            transition_scores=[],
                            heuristic_score=0.0,
                            semantic_score=0.0,
                            confidence=fp.role_confidence,
                            is_standalone=True,
                            continuity_explanation="Unmatched card presented as standalone candidate for human review.",
                            recommended_action="STANDALONE",
                        )
                        session.proposals.append(unmatched_proposal)
                        assigned_ids.add(cid)

                session.unassigned_cards = [
                    session.fingerprints[cid]
                    for cid in correlation_result.unmatched_card_ids
                    if cid in session.fingerprints
                ]

                # Surface degraded fingerprints and unverified proposals (INV-AC4)
                unverified_count = 0
                for p in session.proposals:
                    is_unverified = getattr(p, "semantic_verified", None) is False or (
                        isinstance(p, dict) and p.get("semantic_verified") is False
                    )
                    has_degraded_card = any(
                        getattr(session.fingerprints.get(cid), "degraded", False) is True or (
                            isinstance(session.fingerprints.get(cid), dict) and session.fingerprints[cid].get("degraded") is True
                        )
                        for cid in p.ordered_card_ids
                    )
                    if is_unverified:
                        unverified_count += 1

                    if is_unverified or has_degraded_card:
                        try:
                            object.__setattr__(p, "needs_review", True)
                        except Exception:
                            pass
                        if p.recommended_action != "REJECT":
                            p.recommended_action = "NEEDS_REVIEW"
                        flag_reasons = []
                        if is_unverified:
                            flag_reasons.append("unverified semantic continuity")
                        if has_degraded_card:
                            flag_reasons.append("degraded card fingerprint")
                        flag_str = f" [NEEDS REVIEW: {', '.join(flag_reasons)}]"
                        if flag_str not in p.continuity_explanation:
                            p.continuity_explanation = (p.continuity_explanation + flag_str).strip()
                    else:
                        try:
                            object.__setattr__(p, "needs_review", False)
                        except Exception:
                            pass

                session.unverified_proposals_count = unverified_count
                session.progress_metrics.unverified_proposals = unverified_count
                session.progress_metrics.proposals = len(session.proposals)

                # Populate HITL queue items for proposals
                for p in session.proposals:
                    reasons = []
                    if getattr(p, "semantic_verified", None) is False:
                        reasons.append("UNVERIFIED_SEMANTIC")
                    if any(getattr(session.fingerprints.get(cid), "degraded", False) is True for cid in p.ordered_card_ids):
                        reasons.append("DEGRADED_FINGERPRINT")

                    item = HitlItem(
                        item_id=p.proposal_id,
                        item_type=HitlItemType.PROPOSAL,
                        kind="PROPOSAL",
                        stage="CORRELATION",
                        card_ids=p.ordered_card_ids,
                        proposal_id=p.proposal_id,
                        proposal=p,
                        needs_review=getattr(p, "needs_review", False),
                        review_reasons=reasons,
                        message=p.continuity_explanation or "Correlation proposal awaiting review",
                    )
                    session.enqueue_hitl_item(item)
            else:
                session.proposals = []
                session.unmatched_card_ids = []
                session.unassigned_cards = []
                session.progress_metrics.proposals = 0
                logger.info(
                    f"[{session.session_id}] Stage 3 Correlation bypassed: 0 new cards to correlate."
                )

            # -------------------------------------------------------------
            # Stage 4: Human-in-the-Loop (HITL) Pause Boundary
            # -------------------------------------------------------------
            if session.state == PipelineState.FAILED or session.is_cancelled:
                return

            auto_approve = False
            if hasattr(session.config, "auto_approve"):
                auto_approve = bool(session.config.auto_approve)
            elif isinstance(session.config, dict):
                auto_approve = bool(session.config.get("auto_approve", False))

            if session.proposals:
                if auto_approve:
                    session.approvals = [
                        CardPairApproval(
                            proposal_id=p.proposal_id,
                            card_ids=p.ordered_card_ids,
                            decision=HITLDecision.APPROVE,
                        )
                        for p in session.proposals
                    ]
                    session.progress_metrics.approved = len(session.approvals)
                    session.state = PipelineState.EXTRACTING
                else:
                    resumed = await session.pause_for_hitl()
                    if not resumed or session.is_cancelled:
                        session.state = PipelineState.CANCELLED
                        session.end_time = time.monotonic()
                        return
            else:
                logger.info(
                    f"[{session.session_id}] Stage 4 HITL bypassed: No new proposals requiring human review."
                )

            # -------------------------------------------------------------
            # Stage 5: Multimodal Structured Recipe Extraction
            # -------------------------------------------------------------
            newly_extracted_recipes: List[RecipeSchema] = []

            if session.approvals:
                session.state = PipelineState.EXTRACTING
                session.progress_metrics.extracted = 0

                for approval in session.approvals:
                    if session.is_cancelled:
                        session.state = PipelineState.CANCELLED
                        session.end_time = time.monotonic()
                        return

                    if approval.decision == HITLDecision.REJECT or getattr(approval, "is_rejected", False):
                        logger.info(f"[{session.session_id}] Skipping rejected proposal {approval.proposal_id}")
                        continue

                    ordered_card_ids: List[str] = []
                    if getattr(approval, "ordered_card_ids", None):
                        ordered_card_ids = list(approval.ordered_card_ids)
                    elif getattr(approval, "card_ids", None):
                        ordered_card_ids = list(approval.card_ids)

                    ordered_fps = [
                        session.fingerprints[cid]
                        for cid in ordered_card_ids
                        if cid in session.fingerprints
                    ]

                    if not ordered_fps:
                        logger.warning(
                            f"[{session.session_id}] Missing cards for approval '{approval.proposal_id}' in fingerprints."
                        )
                        continue

                    try:
                        recipe = await self._extract_single_recipe(
                            session,
                            front_fp=ordered_fps[0] if ordered_fps else None,
                            back_fp=ordered_fps[1] if len(ordered_fps) > 1 else None,
                            ordered_fps=ordered_fps,
                        )
                        if recipe:
                            recipe.card_ids = ordered_card_ids if ordered_card_ids else [fp.card_id for fp in ordered_fps]
                            recipe.image_urls = [
                                f"/api/cards/{cid}/image?session_id={session.session_id}"
                                for cid in recipe.card_ids
                            ]

                            session_tags: List[str] = []
                            cfg = session.config
                            if hasattr(cfg, "default_tags") and cfg.default_tags:
                                session_tags = list(cfg.default_tags)
                            elif isinstance(cfg, dict) and cfg.get("default_tags"):
                                dt = cfg["default_tags"]
                                if isinstance(dt, list):
                                    session_tags = [str(t).strip() for t in dt if str(t).strip()]
                                elif isinstance(dt, str):
                                    session_tags = [t.strip() for t in dt.split(",") if t.strip()]
                            elif hasattr(session, "ingestion_config") and session.ingestion_config and session.ingestion_config.default_tags:
                                session_tags = list(session.ingestion_config.default_tags)

                            if session_tags:
                                combined_tags = list(session_tags) + list(recipe.tags or [])
                                recipe.tags = RecipeSchema.clean_and_deduplicate_tags(combined_tags)

                            newly_extracted_recipes.append(recipe)
                        else:
                            # Recipe extraction returned None -> StageFailure
                            fail = StageFailure(
                                stage=PipelineStage.EXTRACTION,
                                reason=FailureReason.INVALID_RESPONSE,
                                message=f"Recipe extraction returned empty for {ordered_card_ids}",
                                card_ids=ordered_card_ids,
                                proposal_id=approval.proposal_id,
                                retryable=True,
                                details={"card_ids": ordered_card_ids, "proposal_id": approval.proposal_id},
                            )
                            session.add_failure(fail)
                            session.enqueue_hitl_failure(
                                stage=PipelineStage.EXTRACTION,
                                card_ids=ordered_card_ids,
                                proposal_id=approval.proposal_id,
                                failure=fail,
                                message=f"Recipe extraction returned empty for {ordered_card_ids}",
                            )
                            failed_recipe = FailedRecipe(
                                recipe_id=f"rec_failed_{uuid.uuid4().hex[:8]}",
                                title=f"Extraction Failed ({', '.join(ordered_card_ids)})",
                                description=f"Recipe extraction returned empty for cards: {ordered_card_ids}",
                                card_ids=ordered_card_ids,
                                image_urls=[
                                    f"/api/cards/{cid}/image?session_id={session.session_id}"
                                    for cid in ordered_card_ids
                                ],
                                status="FAILED",
                                failure=fail,
                            )
                            newly_extracted_recipes.append(failed_recipe)
                    except Exception as exc:
                        logger.error(
                            f"[{session.session_id}] Recipe extraction failed for {ordered_card_ids}: {exc}"
                        )
                        session.errors.append(f"Extraction failed for {ordered_card_ids}: {str(exc)}")

                        if isinstance(exc, StageUnavailableError):
                            fail = exc.to_failure(proposal_id=approval.proposal_id)
                        else:
                            fail = StageFailure(
                                stage=PipelineStage.EXTRACTION,
                                reason=FailureReason.TIMEOUT if "timeout" in str(exc).lower() else FailureReason.UNKNOWN,
                                message=str(exc),
                                card_ids=ordered_card_ids,
                                proposal_id=approval.proposal_id,
                                retryable=True,
                                details={"card_ids": ordered_card_ids, "proposal_id": approval.proposal_id},
                            )
                        session.add_failure(fail)
                        session.enqueue_hitl_failure(
                            stage=PipelineStage.EXTRACTION,
                            card_ids=ordered_card_ids,
                            proposal_id=approval.proposal_id,
                            failure=fail,
                            message=f"Recipe extraction failed for {ordered_card_ids}: {str(exc)}",
                        )
                        failed_recipe = FailedRecipe(
                            recipe_id=f"rec_failed_{uuid.uuid4().hex[:8]}",
                            title=f"Extraction Failed ({', '.join(ordered_card_ids)})",
                            description=f"Recipe extraction failed: {str(exc)}",
                            card_ids=ordered_card_ids,
                            image_urls=[
                                f"/api/cards/{cid}/image?session_id={session.session_id}"
                                for cid in ordered_card_ids
                            ],
                            status="FAILED",
                            failure=fail,
                        )
                        newly_extracted_recipes.append(failed_recipe)

                session.progress_metrics.extracted = len(newly_extracted_recipes)
                session.progress_metrics.failed_extractions = sum(
                    1 for r in newly_extracted_recipes if getattr(r, "status", None) == "FAILED"
                )

            # Stage 5 Unification: Baseline Recipes + Newly Extracted Recipes
            merged_recipes: List[RecipeSchema] = []
            seen_ids: Set[str] = set()

            for r in baseline_recipes:
                merged_recipes.append(r)
                seen_ids.add(r.recipe_id)

            for r in newly_extracted_recipes:
                if r.recipe_id in seen_ids:
                    r.recipe_id = f"rec_{uuid.uuid4().hex[:8]}"
                merged_recipes.append(r)
                seen_ids.add(r.recipe_id)

            session.recipes = merged_recipes
            session.progress_metrics.extracted = len(newly_extracted_recipes)

            # -------------------------------------------------------------
            # Stage 6: Plan To Eat CSV Serialization & Persistence
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

            # Persist merged cards and recipes to SQLite SessionStore
            try:
                from recipeboxer.storage.session_store import SessionStore
                store = SessionStore(self.settings.cache_db_path)
                store.save_cards(session.session_id, session.cards)
                store.save_recipes(session.session_id, session.recipes)
                store.save_session(
                    session_id=session.session_id,
                    state=PipelineState.COMPLETED,
                    card_count=len(session.cards),
                    recipe_count=len(session.recipes),
                )
            except Exception as e:
                logger.warning(f"Could not persist completed session to store: {e}")

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
        if hasattr(config, "local_path") and config.local_path:
            return str(config.local_path)
        if hasattr(config, "source_config") and config.source_config:
            if isinstance(config.source_config, dict) and config.source_config.get("local_path"):
                return str(config.source_config["local_path"])
            if hasattr(config.source_config, "local_path") and config.source_config.local_path:
                return str(config.source_config.local_path)
        if isinstance(config, dict):
            if "scans_dir" in config and config["scans_dir"]:
                return str(config["scans_dir"])
            if "directory_path" in config and config["directory_path"]:
                return str(config["directory_path"])
            if "local_path" in config and config["local_path"]:
                return str(config["local_path"])
            if "source_config" in config and isinstance(config["source_config"], dict):
                if config["source_config"].get("local_path"):
                    return str(config["source_config"]["local_path"])
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
        default_tags = []
        if hasattr(config, "default_tags") and config.default_tags:
            default_tags = list(config.default_tags)
        elif isinstance(config, dict) and config.get("default_tags"):
            dt = config["default_tags"]
            if isinstance(dt, list):
                default_tags = [str(t).strip() for t in dt if str(t).strip()]
            elif isinstance(dt, str):
                default_tags = [t.strip() for t in dt.split(",") if t.strip()]

        return IngestionConfig(
            mode=mode,
            default_tags=default_tags,
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

        target = ext or self.visual_extractor
        if hasattr(target, "extract_fingerprint"):
            return target.extract_fingerprint
        if hasattr(target, "fingerprint_card"):
            return target.fingerprint_card
        if hasattr(target, "extract"):
            return target.extract
        if callable(target):
            return target

        if hasattr(self.visual_extractor, "extract_fingerprint"):
            return self.visual_extractor.extract_fingerprint
        if hasattr(self.visual_extractor, "extract"):
            return self.visual_extractor.extract
        if callable(self.visual_extractor):
            return self.visual_extractor

        raise AttributeError(f"Visual extractor '{target}' has no fingerprint extraction method.")

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
        front_fp: Optional[VisualFingerprint] = None,
        back_fp: Optional[VisualFingerprint] = None,
        ordered_fps: Optional[List[VisualFingerprint]] = None,
    ) -> Optional[RecipeSchema]:
        """Extracts a structured recipe schema using session extractor, mock, or default."""
        import inspect

        if ordered_fps is None:
            ordered_fps = []
            if front_fp is not None:
                ordered_fps.append(front_fp)
            if back_fp is not None:
                ordered_fps.append(back_fp)

        primary_front = ordered_fps[0] if ordered_fps else front_fp
        primary_back = ordered_fps[1] if len(ordered_fps) > 1 else back_fp

        recipe = None
        if session.recipe_extractor_fn:
            try:
                sig = inspect.signature(session.recipe_extractor_fn)
                if len(sig.parameters) == 1:
                    res = session.recipe_extractor_fn(ordered_fps)
                else:
                    res = session.recipe_extractor_fn(primary_front, primary_back)
            except Exception:
                res = session.recipe_extractor_fn(ordered_fps)
            recipe = await res if asyncio.iscoroutine(res) else res
        else:
            ext = getattr(session, "extractor", None) or session.mock_client or self.recipe_extractor
            if ext and hasattr(ext, "extract_cards"):
                recipe = await ext.extract_cards(ordered_fps)
            elif ext and hasattr(ext, "extract"):
                recipe = await ext.extract(ordered_fps)
            elif ext and hasattr(ext, "extract_recipe"):
                try:
                    recipe = await ext.extract_recipe(primary_front, primary_back)
                except Exception:
                    recipe = await ext.extract_recipe(ordered_fps)
            elif callable(ext):
                try:
                    res = ext(ordered_fps)
                except TypeError:
                    res = ext(primary_front, primary_back)
                recipe = await res if asyncio.iscoroutine(res) else res

        if recipe is not None and not isinstance(recipe, RecipeSchema) and hasattr(recipe, "model_dump"):
            return RecipeSchema.model_validate(recipe.model_dump())
        return recipe


# Alias for backward compatibility and design harmony
AgentOrchestrator = RecipeBoxerAgent
