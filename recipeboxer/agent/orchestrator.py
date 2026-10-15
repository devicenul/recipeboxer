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
from recipeboxer.config import Settings, get_settings, has_usable_gemini_key, is_usable_gemini_key
from recipeboxer.ai.client import LLMClient, LLMStage
from recipeboxer.correlation.engine import (
    FeatureToggles,
    FileImageProvider,
    GroupingEngine,
    GroupingResult,
    ImageProvider,
    _feature_is_active,
    _is_candidate_anchor,
)
from recipeboxer.correlation.background import DeckBackground
from recipeboxer.correlation.calibration import DEFAULT_CALIBRATION_PATH, LinkCalibrator
from recipeboxer.correlation.content import completeness_prior
from recipeboxer.correlation.evidence import compute_raw_features
from recipeboxer.correlation.optimizer import PairingOptimizer, path_cover
from recipeboxer.correlation.screening import top_k_candidates
from recipeboxer.correlation.semantic import GeminiSemanticVerifier, SetVerifier
from recipeboxer.exporter.plan_to_eat import PlanToEatExporter
from recipeboxer.extraction.extractor import RecipeExtractor
from recipeboxer.ingestion.runner import (
    IngestionConfig,
    IngestionMode,
    IngestionProgress,
    IngestionRunner,
)
from recipeboxer.ingestion.scanner import ScannedCard, scan_card_directory
from recipeboxer.models.correlation import (
    CorrelationProposal,
    CorrelationResult,
    PairEvidence,
    SetVerification,
)
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.failures import (
    BudgetExceeded,
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


class ConcurrentGroupingEngine(GroupingEngine):
    """GroupingEngine executing multimodal set-level verifications concurrently with bounded semaphore."""

    async def group(
        self,
        fps: List[VisualFingerprint],
        images: ImageProvider,
        *,
        llm_client: Optional[LLMClient] = None,
        settings: Optional[Any] = None,
        embedder: Optional[Any] = None,
        toggles: Optional[FeatureToggles] = None,
    ) -> GroupingResult:
        eff_settings = settings or get_settings()
        eff_toggles = toggles if toggles is not None else FeatureToggles.from_env()
        review_threshold = float(getattr(eff_settings, "grouping_review_threshold", 0.70))

        # Handle empty collection
        if not fps:
            return GroupingResult(
                chains=[],
                proposals=[],
                evidence=[],
                verifications=[],
                needs_review=[],
                stats={"card_count": 0, "chains_count": 0},
            )

        # Handle single card collection
        if len(fps) == 1:
            fp = fps[0]
            is_sem_verified = bool(llm_client is not None and eff_toggles.set_verify)
            is_deg = bool(getattr(fp, "degraded", False))
            action = "STANDALONE" if is_sem_verified else "UNCERTAIN"
            deg_reason = None if is_sem_verified else ("DEGRADED_FINGERPRINT" if is_deg else "NO_LLM_CLIENT")

            needs_rev = [fp.card_id] if (is_deg or not is_sem_verified) else []

            prop = CorrelationProposal(
                proposal_id=f"prop_{uuid.uuid4().hex[:8]}",
                ordered_card_ids=[fp.card_id],
                part_count=1,
                transition_scores=[],
                heuristic_score=0.0,
                semantic_score=1.0 if is_sem_verified else None,
                confidence=None,
                is_standalone=True,
                continuity_explanation="Single-card collection classified as standalone recipe." if is_sem_verified else "Unverified card proposal awaiting review.",
                recommended_action=action,
                semantic_verified=is_sem_verified,
                degraded_reason=deg_reason,
            )
            return GroupingResult(
                chains=[[fp.card_id]],
                proposals=[prop],
                evidence=[],
                verifications=[],
                needs_review=needs_rev,
                stats={"card_count": 1, "chains_count": 1},
            )

        # 1. DeckBackground fitting
        pair_fn = (lambda a, b: compute_raw_features(a, b, images=images)) if images else None
        background = DeckBackground.fit(fps, raw_pair_fn=pair_fn)

        # 2. Top-k candidate screening
        active_embedder = embedder if eff_toggles.embeddings else None
        candidates_by_anchor = top_k_candidates(
            fps,
            background,
            k=5,
            embedder=active_embedder,
            images=images,
        )

        all_evidence: List[PairEvidence] = []
        for ev_list in candidates_by_anchor.values():
            for ev in ev_list:
                active_features = [f for f in ev.features if _feature_is_active(f.name, eff_toggles)]
                new_total_log_lr = sum(
                    f.weight * f.log_lr
                    for f in active_features
                    if f.log_lr is not None
                )
                ev.features = active_features
                ev.total_log_lr = round(new_total_log_lr, 4)
                all_evidence.append(ev)

        # 3. Multimodal Set-Level Verification (CONCURRENT)
        verifications: List[SetVerification] = []
        verification_by_anchor: Dict[str, SetVerification] = {}

        can_verify = eff_toggles.set_verify and (llm_client is not None or self.set_verifier is not None)
        if can_verify:
            verifier = self.set_verifier or SetVerifier(llm_client=llm_client, settings=eff_settings)
            fp_map = {fp.card_id: fp for fp in fps}

            concurrency = getattr(eff_settings, "llm_max_concurrency", 8) or 8
            sem = asyncio.Semaphore(concurrency)

            candidate_tasks = []
            for anchor_id, ev_list in candidates_by_anchor.items():
                if not ev_list:
                    continue
                anchor_fp = fp_map.get(anchor_id)
                if not anchor_fp or not _is_candidate_anchor(anchor_fp):
                    continue
                cand_fps = [fp_map[ev.b] for ev in ev_list if ev.b in fp_map]
                if not cand_fps:
                    continue
                candidate_tasks.append((anchor_id, anchor_fp, cand_fps))

            async def _run_single_verify(aid: str, a_fp: VisualFingerprint, c_fps: List[VisualFingerprint]):
                async with sem:
                    try:
                        ver_res = await verifier.verify(a_fp, c_fps, images=images)
                        return aid, ver_res
                    except BudgetExceeded:
                        raise
                    except Exception as exc:
                        logger.warning(f"SetVerifier failed for anchor card {aid}: {exc}")
                        return aid, None

            if candidate_tasks:
                verify_results = await asyncio.gather(
                    *[_run_single_verify(aid, a_fp, c_fps) for aid, a_fp, c_fps in candidate_tasks]
                )
                for aid, ver in verify_results:
                    if ver is not None:
                        verifications.append(ver)
                        verification_by_anchor[aid] = ver

        # 4. Calibration
        calibrator = self.calibrator
        if calibrator is None:
            default_path = Path(DEFAULT_CALIBRATION_PATH)
            if default_path.exists():
                try:
                    calibrator = LinkCalibrator.load(default_path)
                except Exception as exc:
                    logger.warning(f"Failed to load default calibration from {default_path}: {exc}")
                    calibrator = LinkCalibrator()
            else:
                calibrator = LinkCalibrator()

        verified_positive_edges: Set[Tuple[str, str]] = set()
        verified_negative_edges: Set[Tuple[str, str]] = set()

        for ver in verifications:
            seq = [ver.anchor] + list(ver.continuations or [])
            for idx in range(len(seq) - 1):
                verified_positive_edges.add((seq[idx], seq[idx + 1]))
            for rej in (ver.rejected or []):
                for s_node in seq:
                    verified_negative_edges.add((s_node, rej))
                    verified_negative_edges.add((rej, s_node))

        links: Dict[Tuple[str, str], float] = {}
        pair_ev_by_edge: Dict[Tuple[str, str], PairEvidence] = {}

        for ev in all_evidence:
            edge = (ev.a, ev.b)
            set_verified_outcome: Optional[bool] = None
            if edge in verified_positive_edges:
                set_verified_outcome = True
            elif edge in verified_negative_edges:
                set_verified_outcome = False
            elif not can_verify:
                set_verified_outcome = None

            p = calibrator.predict(ev, set_verified=set_verified_outcome)
            ev.p_link = round(p, 4)
            links[edge] = ev.p_link
            pair_ev_by_edge[edge] = ev

        # 5. Completeness priors & node costs
        node_costs: Dict[str, Dict[str, Any]] = {}
        for fp in fps:
            prior = completeness_prior(fp)
            has_title = bool(getattr(fp, "visible_title", None))
            node_costs[fp.card_id] = {
                "expects_continuation": prior.get("expects_continuation", 0.50),
                "is_complete": prior.get("is_complete", 0.50),
                "is_start": prior.get("is_start", 0.50),
                "has_title": has_title,
                "visible_title": getattr(fp, "visible_title", None),
            }

        # 6. Global Min-Cost Path Cover Optimization
        node_ids = [fp.card_id for fp in fps]
        chains = path_cover(node_ids, links, node_costs)

        if os.environ.get("RB_DEBUG_GROUPING") == "1":
            try:
                debug_payload = {
                    "cards": node_ids,
                    "links": [
                        {"from": k[0], "to": k[1], "p_link": float(v)}
                        for k, v in sorted(links.items(), key=lambda x: -x[1])
                    ],
                    "node_costs": node_costs,
                    "chains": chains,
                    "verifications": [
                        {
                            "anchor": v.anchor,
                            "continuations": v.continuations,
                            "rejected": v.rejected,
                            "evidence_quotes": v.evidence_quotes,
                        }
                        for v in verifications
                    ],
                }
                Path("grouping_debug.json").write_text(json.dumps(debug_payload, indent=2), encoding="utf-8")
            except Exception as d_exc:
                logger.warning(f"Failed to write RB_DEBUG_GROUPING dump: {d_exc}")

        # 7. Build CorrelationProposals and HITL Routing
        proposals: List[CorrelationProposal] = []
        needs_review_cards: Set[str] = set()
        fp_by_id = {fp.card_id: fp for fp in fps}

        for chain in chains:
            if len(chain) == 1:
                cid = chain[0]
                fp = fp_by_id.get(cid)
                is_deg = bool(getattr(fp, "degraded", False))

                if not can_verify:
                    sem_verified = False
                    action = "UNCERTAIN"
                    deg_reason = "NO_LLM_CLIENT" if llm_client is None else "SET_VERIFY_DISABLED"
                    needs_review_cards.add(cid)
                else:
                    sem_verified = True
                    action = "STANDALONE"
                    deg_reason = None
                    if is_deg:
                        needs_review_cards.add(cid)

                prop = CorrelationProposal(
                    proposal_id=f"prop_{uuid.uuid4().hex[:8]}",
                    ordered_card_ids=[cid],
                    part_count=1,
                    transition_scores=[],
                    heuristic_score=0.0,
                    semantic_score=1.0 if can_verify else None,
                    confidence=None,
                    is_standalone=True,
                    continuity_explanation="Single-sided card classified as standalone recipe." if can_verify else "Unverified card proposal awaiting review.",
                    recommended_action=action,
                    semantic_verified=sem_verified,
                    degraded_reason=deg_reason,
                )
                proposals.append(prop)

            else:
                t_scores: List[float] = []
                s_scores: List[float] = []
                collected_quotes: List[str] = []
                top_features: List[str] = []
                all_transitions_verified = True
                has_low_confidence = False

                for k in range(len(chain) - 1):
                    edge = (chain[k], chain[k + 1])
                    p_link = links.get(edge, 0.50)
                    t_scores.append(round(p_link, 4))

                    ev = pair_ev_by_edge.get(edge)
                    if ev:
                        sorted_feats = sorted(
                            [f for f in ev.features if f.log_lr is not None],
                            key=lambda f: f.log_lr or 0.0,
                            reverse=True,
                        )
                        for f in sorted_feats[:2]:
                            top_features.append(f"{f.name} (log_lr={f.log_lr:.2f})")

                    ver = verification_by_anchor.get(chain[k]) or verification_by_anchor.get(chain[k + 1])
                    if ver:
                        cand_target = chain[k + 1] if chain[k] == ver.anchor else chain[k]
                        if cand_target in ver.continuations:
                            for q in ver.evidence_quotes:
                                if q not in collected_quotes:
                                    collected_quotes.append(q)
                            if ver.confidence is not None:
                                s_scores.append(ver.confidence)
                        else:
                            all_transitions_verified = False
                    else:
                        all_transitions_verified = False

                chain_conf = min(s_scores) if s_scores else (min(t_scores) if t_scores else None)
                has_low_confidence = bool(chain_conf is not None and chain_conf < review_threshold)
                avg_h = sum(t_scores) / len(t_scores) if t_scores else 0.50
                avg_s = sum(s_scores) / len(s_scores) if s_scores else (1.0 if all_transitions_verified else 0.0)

                if not can_verify:
                    sem_verified = False
                    action = "UNCERTAIN"
                    deg_reason = "NO_LLM_CLIENT" if llm_client is None else "SET_VERIFY_DISABLED"
                    for cid in chain:
                        needs_review_cards.add(cid)
                else:
                    sem_verified = all_transitions_verified
                    deg_reason = None if sem_verified else "UNVERIFIED_TRANSITION"
                    if not sem_verified or has_low_confidence:
                        action = "NEEDS_REVIEW"
                        for cid in chain:
                            needs_review_cards.add(cid)
                    else:
                        action = "SEQUENCE" if len(chain) > 2 else "PAIR"

                for cid in chain:
                    if getattr(fp_by_id.get(cid), "degraded", False):
                        needs_review_cards.add(cid)
                        action = "NEEDS_REVIEW"

                conf_str = f"Calibrated p={chain_conf:.3f}." if chain_conf is not None else "Uncalibrated."
                expl_parts = [
                    f"Linked {len(chain)}-card sequence ({' -> '.join(chain)}). {conf_str}"
                ]
                if collected_quotes:
                    expl_parts.append("Quotes: " + "; ".join(f'"{q}"' for q in collected_quotes[:3]))
                if top_features:
                    expl_parts.append("Evidence: " + ", ".join(top_features[:4]))
                explanation = " ".join(expl_parts)

                prop = CorrelationProposal(
                    proposal_id=f"prop_{uuid.uuid4().hex[:8]}",
                    ordered_card_ids=chain,
                    part_count=len(chain),
                    transition_scores=t_scores,
                    heuristic_score=round(avg_h, 4),
                    semantic_score=round(avg_s, 4) if can_verify else None,
                    confidence=round(chain_conf, 4) if chain_conf is not None else None,
                    is_standalone=False,
                    continuity_explanation=explanation,
                    recommended_action=action,
                    semantic_verified=sem_verified,
                    degraded_reason=deg_reason,
                )
                proposals.append(prop)

        sorted_needs_review = sorted(list(needs_review_cards))

        stats = {
            "card_count": len(fps),
            "chains_count": len(chains),
            "pairs_evaluated": len(all_evidence),
            "proposals_count": len(proposals),
            "needs_review_count": len(sorted_needs_review),
            "unverified_proposals_count": sum(1 for p in proposals if not p.semantic_verified),
        }

        return GroupingResult(
            chains=chains,
            proposals=proposals,
            evidence=all_evidence,
            verifications=verifications,
            needs_review=sorted_needs_review,
            stats=stats,
        )


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
        grouping_engine: Optional[GroupingEngine] = None,
    ) -> None:
        """Initialize the agent orchestrator with required sub-stage components."""
        self.settings = settings or get_settings()
        self.cache = cache or FingerprintCache(self.settings.cache_db_path)
        self.visual_extractor = visual_extractor or GeminiVisualExtractor()
        self.semantic_verifier = semantic_verifier or GeminiSemanticVerifier()
        self.pairing_optimizer = pairing_optimizer or PairingOptimizer(verifier=self.semantic_verifier)
        self.grouping_engine = grouping_engine or ConcurrentGroupingEngine()
        self.recipe_extractor = recipe_extractor or RecipeExtractor()
        self.csv_exporter = csv_exporter or PlanToEatExporter()
        try:
            from recipeboxer.ai.embeddings import create_embedder
            self.embedder = create_embedder(self.settings)
        except Exception:
            self.embedder = None

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
                if session.config.get("mock_client"):
                    return True
                if is_usable_gemini_key(session.config.get("gemini_api_key")):
                    return True
            elif is_usable_gemini_key(getattr(session.config, "gemini_api_key", None)):
                return True
        if hasattr(session, "settings") and session.settings is not None and not isinstance(session.settings, Settings):
            return True

        # Injected clients on agent components (LLMClient or mock client)
        for comp in (self.visual_extractor, self.semantic_verifier, self.recipe_extractor):
            if comp is not None:
                if getattr(comp, "llm_client", None) is not None:
                    return True
                if getattr(comp, "client", None) is not None:
                    return True

        # Custom / mock component instances passed to agent
        if self.visual_extractor is not None and not isinstance(self.visual_extractor, GeminiVisualExtractor):
            return True
        if self.recipe_extractor is not None and not isinstance(self.recipe_extractor, RecipeExtractor):
            return True

        # Usable Gemini API key
        extra_keys = [
            getattr(session.settings, "gemini_api_key", None),
            getattr(self.visual_extractor, "api_key", None),
            getattr(self.recipe_extractor, "api_key", None),
        ]
        return has_usable_gemini_key(self.settings, extra_keys=extra_keys)

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
            cards_to_fingerprint = [
                c for c in new_cards
                if c.card_id not in session.fingerprints
            ]
            if cards_to_fingerprint:
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
                        cards=cards_to_fingerprint,
                        progress_callback=_fingerprint_progress,
                        cancellation_token=session.cancellation_token,
                    )
                except BudgetExceeded as be:
                    logger.warning(f"[{session.session_id}] Budget exceeded during fingerprinting: {be}")
                    fail = be.to_failure()
                    session.add_failure(fail)
                    budget_item = HitlItem(
                        item_id=f"hitl_budget_{uuid.uuid4().hex[:8]}",
                        item_type=HitlItemType.BUDGET,
                        kind="budget",
                        stage="FINGERPRINT",
                        card_ids=[c.card_id for c in new_cards],
                        failure=fail,
                        needs_review=True,
                        message=str(be),
                    )
                    session.enqueue_hitl_item(budget_item)
                    resumed = await session.pause_for_hitl()
                    if not resumed or session.is_cancelled:
                        session.state = PipelineState.CANCELLED
                        session.end_time = time.monotonic()
                        return
                    ingestion_result = IngestionResult(fingerprints=[], failures=[fail])
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

                # Record stage failures from runner and check for budget exceeded
                budget_failures = [
                    f for f in getattr(ingestion_result, "failures", [])
                    if f.reason == FailureReason.BUDGET_EXCEEDED
                ]
                if budget_failures:
                    b_fail = budget_failures[0]
                    if b_fail not in session.failures:
                        session.add_failure(b_fail)
                    budget_item = HitlItem(
                        item_id=f"hitl_budget_{uuid.uuid4().hex[:8]}",
                        item_type=HitlItemType.BUDGET,
                        kind="budget",
                        stage="FINGERPRINT",
                        card_ids=b_fail.card_ids,
                        failure=b_fail,
                        needs_review=True,
                        message=b_fail.message or "Budget exceeded during fingerprinting",
                    )
                    session.enqueue_hitl_item(budget_item)
                    resumed = await session.pause_for_hitl()
                    if not resumed or session.is_cancelled:
                        session.state = PipelineState.CANCELLED
                        session.end_time = time.monotonic()
                        return

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
            if new_cards and not session.proposals:
                session.state = PipelineState.CORRELATING
                new_fps = [session.fingerprints[c.card_id] for c in new_cards if c.card_id in session.fingerprints]

                # 1. Build ImageProvider from session.cards and fingerprints
                path_map: Dict[str, Any] = {}
                for c in session.cards:
                    if getattr(c, "card_id", None) and getattr(c, "path", None):
                        path_map[c.card_id] = c.path
                        path_map[Path(c.path).name] = c.path
                for fp in new_fps:
                    if fp.card_id and getattr(fp, "image_path", None):
                        path_map[fp.card_id] = fp.image_path
                image_provider = FileImageProvider(path_map)

                # 2. Determine execution strategy: custom optimizer / mock adapter vs GroupingEngine
                use_custom_optimizer = False
                custom_optimizer = None

                if session.optimizer is not None and not isinstance(session.optimizer, PairingOptimizer):
                    use_custom_optimizer = True
                    custom_optimizer = session.optimizer
                elif session.mock_client is not None and hasattr(session.mock_client, "verify_correlation"):
                    use_custom_optimizer = True
                    custom_optimizer = self._build_mock_optimizer(session, new_fps)
                elif session.optimizer is not None and isinstance(session.optimizer, PairingOptimizer):
                    use_custom_optimizer = True
                    custom_optimizer = session.optimizer

                try:
                    if use_custom_optimizer and custom_optimizer is not None:
                        if isinstance(custom_optimizer, PairingOptimizer):
                            correlation_result = await custom_optimizer.optimize(new_fps)
                        else:
                            correlation_result = await self._run_custom_optimizer(custom_optimizer, new_fps)
                    else:
                        engine = session.grouping_engine or self.grouping_engine
                        eff_llm_client = session.mock_client
                        if eff_llm_client is None:
                            if hasattr(self.visual_extractor, "llm_client") and self.visual_extractor.llm_client:
                                eff_llm_client = self.visual_extractor.llm_client
                            elif hasattr(self, "llm_client") and getattr(self, "llm_client", None):
                                eff_llm_client = self.llm_client
                            elif has_usable_gemini_key(session.settings):
                                from recipeboxer.ai.client import create_llm_client
                                eff_llm_client = create_llm_client(settings=session.settings)

                        grouping_result = await engine.group(
                            fps=new_fps,
                            images=image_provider,
                            llm_client=eff_llm_client,
                            settings=session.settings,
                            embedder=getattr(self, "embedder", None),
                        )
                        session.grouping_result = grouping_result
                        correlation_result = CorrelationResult(
                            proposals=grouping_result.proposals,
                            unmatched_card_ids=[],
                        )
                except BudgetExceeded as be:
                    logger.warning(f"[{session.session_id}] Budget exceeded during correlation: {be}")
                    fail = be.to_failure()
                    session.add_failure(fail)
                    budget_item = HitlItem(
                        item_id=f"hitl_budget_{uuid.uuid4().hex[:8]}",
                        item_type=HitlItemType.BUDGET,
                        kind="budget",
                        stage="CORRELATION",
                        card_ids=[fp.card_id for fp in new_fps],
                        failure=fail,
                        needs_review=True,
                        message=str(be),
                    )
                    session.enqueue_hitl_item(budget_item)
                    resumed = await session.pause_for_hitl()
                    if not resumed or session.is_cancelled:
                        session.state = PipelineState.CANCELLED
                        session.end_time = time.monotonic()
                        return
                    if use_custom_optimizer and custom_optimizer is not None:
                        if isinstance(custom_optimizer, PairingOptimizer):
                            correlation_result = await custom_optimizer.optimize(new_fps)
                        else:
                            correlation_result = await self._run_custom_optimizer(custom_optimizer, new_fps)
                    else:
                        grouping_result = await engine.group(
                            fps=new_fps,
                            images=image_provider,
                            llm_client=eff_llm_client,
                            settings=session.settings,
                            embedder=getattr(self, "embedder", None),
                        )
                        session.grouping_result = grouping_result
                        correlation_result = CorrelationResult(
                            proposals=grouping_result.proposals,
                            unmatched_card_ids=[],
                        )
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
            elif not session.proposals:
                session.proposals = []
                session.unmatched_card_ids = []
                session.unassigned_cards = []
                session.progress_metrics.proposals = 0
                logger.info(
                    f"[{session.session_id}] Stage 3 Correlation bypassed: 0 new cards to correlate."
                )

            # Surface degraded fingerprints and unverified proposals (INV-AC4)
            if session.proposals:
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

                approvals_list = list(session.approvals)
                eff_concurrency = (
                    getattr(session.settings, "llm_max_concurrency", None)
                    or getattr(self.settings, "llm_max_concurrency", 8)
                    or 8
                )
                eff_concurrency = max(1, int(eff_concurrency))
                sem = asyncio.Semaphore(eff_concurrency)

                extracted_slots: List[Optional[Union[RecipeSchema, FailedRecipe]]] = [None] * len(approvals_list)

                async def _process_single_approval(idx: int, approval: Any) -> None:
                    if session.is_cancelled:
                        return

                    if approval.decision == HITLDecision.REJECT or getattr(approval, "is_rejected", False):
                        logger.info(f"[{session.session_id}] Skipping rejected proposal {approval.proposal_id}")
                        return

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
                        return

                    async with sem:
                        if session.is_cancelled:
                            return
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

                                extracted_slots[idx] = recipe
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
                                extracted_slots[idx] = failed_recipe
                        except BudgetExceeded as be:
                            logger.warning(
                                f"[{session.session_id}] Budget exceeded during recipe extraction for {ordered_card_ids}: {be}"
                            )
                            fail = be.to_failure(proposal_id=approval.proposal_id)
                            session.add_failure(fail)
                            budget_item = HitlItem(
                                item_id=f"hitl_budget_{uuid.uuid4().hex[:8]}",
                                item_type=HitlItemType.BUDGET,
                                kind="budget",
                                stage="EXTRACTION",
                                card_ids=ordered_card_ids,
                                proposal_id=approval.proposal_id,
                                failure=fail,
                                needs_review=True,
                                message=str(be),
                            )
                            session.enqueue_hitl_item(budget_item)
                            resumed = await session.pause_for_hitl()
                            if not resumed or session.is_cancelled:
                                session.state = PipelineState.CANCELLED
                                session.end_time = time.monotonic()
                                return
                            # If resumed with raised cap, retry this recipe extraction
                            try:
                                recipe = await self._extract_single_recipe(
                                    session,
                                    front_fp=ordered_fps[0] if ordered_fps else None,
                                    back_fp=ordered_fps[1] if len(ordered_fps) > 1 else None,
                                    ordered_fps=ordered_fps,
                                )
                                if recipe:
                                    recipe.card_ids = ordered_card_ids if ordered_card_ids else [fp.card_id for fp in ordered_fps]
                                    extracted_slots[idx] = recipe
                            except Exception as retry_exc:
                                logger.error(f"Retry extraction failed after budget resume: {retry_exc}")
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
                            extracted_slots[idx] = failed_recipe

                        # Call incremental extraction hook if configured on session
                        if extracted_slots[idx] is not None:
                            on_ext = getattr(session, "on_recipe_extracted", None)
                            if callable(on_ext):
                                try:
                                    on_ext(extracted_slots[idx])
                                except Exception as cb_exc:
                                    logger.warning(f"Error in on_recipe_extracted callback: {cb_exc}")

                        # Update metrics
                        session.progress_metrics.extracted = sum(
                            1 for r in extracted_slots if r is not None and getattr(r, "status", None) != "FAILED"
                        )
                        session.progress_metrics.failed_extractions = sum(
                            1 for r in extracted_slots if r is not None and getattr(r, "status", None) == "FAILED"
                        )

                await asyncio.gather(*[_process_single_approval(idx, app) for idx, app in enumerate(approvals_list)])

                if session.is_cancelled:
                    session.state = PipelineState.CANCELLED
                    session.end_time = time.monotonic()
                    return

                newly_extracted_recipes = [r for r in extracted_slots if r is not None]
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

        class _SessionVerifierAdapter:
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

        return PairingOptimizer(verifier=_SessionVerifierAdapter())

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
            ext = getattr(session, "extractor", None)
            if ext and not isinstance(ext, LLMClient) and (hasattr(ext, "extract_cards") or hasattr(ext, "extract") or hasattr(ext, "extract_recipe")):
                if hasattr(ext, "extract_cards"):
                    recipe = await ext.extract_cards(ordered_fps)
                elif hasattr(ext, "extract"):
                    recipe = await ext.extract(ordered_fps)
                elif hasattr(ext, "extract_recipe"):
                    try:
                        recipe = await ext.extract_recipe(primary_front, primary_back)
                    except Exception:
                        recipe = await ext.extract_recipe(ordered_fps)
            elif ext and callable(ext) and not isinstance(ext, LLMClient):
                try:
                    res = ext(ordered_fps)
                except TypeError:
                    res = ext(primary_front, primary_back)
                recipe = await res if asyncio.iscoroutine(res) else res
            else:
                eff_client = session.mock_client
                if eff_client is None and isinstance(ext, LLMClient):
                    eff_client = ext
                if eff_client is None and hasattr(self, "llm_client"):
                    eff_client = self.llm_client

                extractor = self.recipe_extractor
                if eff_client is not None and getattr(extractor, "client", None) is None and getattr(extractor, "llm_client", None) is None:
                    extractor = RecipeExtractor(
                        llm_client=eff_client,
                        model_name=session.settings.get_stage_model(LLMStage.STRUCTURE),
                        settings=session.settings,
                    )
                if hasattr(extractor, "extract_cards"):
                    recipe = await extractor.extract_cards(ordered_fps)
                elif hasattr(extractor, "extract"):
                    recipe = await extractor.extract(ordered_fps)
                elif hasattr(extractor, "extract_recipe"):
                    try:
                        recipe = await extractor.extract_recipe(primary_front, primary_back)
                    except Exception:
                        recipe = await extractor.extract_recipe(ordered_fps)

        if recipe is not None and not isinstance(recipe, RecipeSchema) and hasattr(recipe, "model_dump"):
            return RecipeSchema.model_validate(recipe.model_dump())
        return recipe


# Alias for backward compatibility and design harmony
AgentOrchestrator = RecipeBoxerAgent
