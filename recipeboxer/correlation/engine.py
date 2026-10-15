"""GroupingEngine: Main entry point for RecipeBoxer card correlation (Phase 3).

Orchestrates collection-relative background fitting (DeckBackground), top-k candidate
screening, multimodal set-level verification (SetVerifier), probability calibration
(LinkCalibrator), and global minimum-cost path cover optimization.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Set, Tuple, Union, runtime_checkable

from pydantic import BaseModel, Field

from recipeboxer.ai.client import LLMClient
from recipeboxer.config import get_settings
from recipeboxer.correlation.background import DeckBackground
from recipeboxer.correlation.calibration import DEFAULT_CALIBRATION_PATH, LinkCalibrator
from recipeboxer.correlation.content import completeness_prior
from recipeboxer.correlation.evidence import compute_raw_features, pair_evidence
from recipeboxer.correlation.guards import (
    GATE_CAP,
    STRICT_MIN_SV_CONF,
    STRONG_MIN_SV_CONF,
    DomainSupport,
    is_nonrecipe,
    physical_conflict,
    starts_new_recipe,
    sv_positive_ok,
    sv_quality,
)
from recipeboxer.correlation.optimizer import path_cover
from recipeboxer.correlation.screening import top_k_candidates
from recipeboxer.correlation.semantic import SetVerifier
from recipeboxer.models.correlation import (
    CorrelationProposal,
    FeatureEvidence,
    PairEvidence,
    SetVerification,
)
from recipeboxer.models.domain import VisualFingerprint
from recipeboxer.models.failures import BudgetExceeded

logger = logging.getLogger(__name__)


class ImageNotFoundError(FileNotFoundError, KeyError):
    """Raised when an image cannot be located or read by an ImageProvider."""

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else "Image not found"


@runtime_checkable
class ImageProvider(Protocol):
    """Protocol for supplying raw card image bytes by card ID."""

    def get(self, card_id: str) -> Optional[bytes]:
        ...


class FileImageProvider:
    """Default filesystem-backed implementation of ImageProvider."""

    def __init__(self, path_map: Optional[Dict[str, Union[Path, str]]] = None) -> None:
        self._map: Dict[str, Path] = {k: Path(v) for k, v in (path_map or {}).items()}

    def get(self, card_id: str) -> Optional[bytes]:
        p = self._map.get(card_id)
        if p and p.is_file():
            try:
                return p.read_bytes()
            except Exception as exc:
                logger.warning("Failed to read image bytes for card %s: %s", card_id, exc)
                return None
        # Fallback to card_id as a direct path
        p_direct = Path(card_id)
        if p_direct.is_file():
            try:
                return p_direct.read_bytes()
            except Exception as exc:
                logger.warning("Failed to read image bytes for card %s: %s", card_id, exc)
                return None
        return None


class TempDirImageProvider:
    """Directory-backed implementation of ImageProvider for temporary or corpus directories."""

    def __init__(self, base_dir: Union[Path, str]) -> None:
        self.base_dir = Path(base_dir)

    def get(self, card_id: str) -> Optional[bytes]:
        candidates = [
            self.base_dir / card_id,
            self.base_dir / f"{card_id}.jpg",
            self.base_dir / f"{card_id}.png",
            self.base_dir / f"{card_id}.jpeg",
        ]
        for c in candidates:
            if c.is_file():
                try:
                    return c.read_bytes()
                except Exception as exc:
                    logger.warning("Failed to read image %s: %s", c, exc)
                    return None
        return None



class FeatureToggles(BaseModel):
    """Ablation and feature toggle configuration for the grouping pipeline."""

    pixels: bool = True
    capture: bool = True
    physical: bool = True
    content: bool = True
    embeddings: bool = False
    ocr: bool = False
    set_verify: bool = True

    @classmethod
    def from_env(cls) -> "FeatureToggles":
        """Instantiates FeatureToggles from the RB_FEATURE_TOGGLES JSON environment variable."""
        raw_env = os.environ.get("RB_FEATURE_TOGGLES")
        if raw_env:
            try:
                parsed = json.loads(raw_env)
                if isinstance(parsed, dict):
                    return cls.model_validate(parsed)
            except Exception as exc:
                logger.warning(f"Failed to parse RB_FEATURE_TOGGLES environment variable: {exc}")
        return cls()


class GroupingResult(BaseModel):
    """Structured output of the GroupingEngine presented to HITL review and extraction."""

    chains: List[List[str]] = Field(
        ...,
        description="Global min-cost path cover chains covering all cards exactly once",
    )
    proposals: List[CorrelationProposal] = Field(
        default_factory=list,
        description="Proposals (sequences and standalones) with calibrated confidence and evidence quotes",
    )
    evidence: List[PairEvidence] = Field(
        default_factory=list,
        description="Directed pairwise evidence evaluated during candidate screening",
    )
    verifications: List[SetVerification] = Field(
        default_factory=list,
        description="Multimodal LLM set verification outcomes per anchor card",
    )
    needs_review: List[str] = Field(
        default_factory=list,
        description="Card IDs requiring Human-in-the-Loop inspection",
    )
    stats: Dict[str, Any] = Field(
        default_factory=dict,
        description="Diagnostics and counters for the grouping execution",
    )


def _feature_is_active(feature_name: str, toggles: FeatureToggles) -> bool:
    """Checks whether a named feature is enabled by current FeatureToggles."""
    name_lower = feature_name.lower()
    if any(k in name_lower for k in ("paper", "ruling", "ink", "aspect", "pixel")):
        return toggles.pixels
    if any(k in name_lower for k in ("capture", "time", "gap", "sort", "exif")):
        return toggles.capture
    if any(k in name_lower for k in ("wear", "stain", "tear", "show_through", "outline", "mirrored")):
        return toggles.physical
    if any(k in name_lower for k in ("content", "xref", "sentence", "step", "header", "title", "continuation")):
        return toggles.content
    if "embed" in name_lower:
        return toggles.embeddings
    if "ocr" in name_lower:
        return toggles.ocr
    return True


def _is_candidate_anchor(fp: VisualFingerprint) -> bool:
    """Returns True if the card can act as an anchor card (recipe front/start)."""
    if getattr(fp, "looks_like_back", False) is True:
        return False
    role_str = str(getattr(fp, "role", "")).upper()
    if "CONTINUATION" in role_str or "BACK" in role_str:
        return False
    return True


class GroupingEngine:
    """Unified grouping and correlation engine for recipe cards.

    The single entry point used by the orchestrator and headless pipelines.
    Runs:
    1. DeckBackground.fit
    2. top_k_candidates screening
    3. SetVerifier multimodal verification
    4. LinkCalibrator prediction
    5. path_cover global min-cost optimization
    6. HITL routing based on calibrated confidence, unverified signals, and degraded inputs.

    WP-UP3: when fitted models are available (``calibration_default.json["up3"]``) steps 2/4 and the
    proposal confidences use them: fitted all-pairs prefilter + rank model with adaptive k (see
    ``ranking.py``), an orphan-fragment rescue verification pass, a fitted link model over SetVerifier
    outcomes, and fitted cluster models giving P(proposal is exactly one complete item) for both
    multi-card and single-card (standalone / distractor) proposals.
    """

    # Adaptive screening / rescue parameters (chosen on fit decks only; see WP-UP3.md).
    POOL_M = 10          # minimum pool size; effective M = clip(POOL_M_FRAC * N, POOL_M, POOL_M_MAX)
    POOL_M_FRAC = 0.2
    POOL_M_MAX = 30
    K_MIN = 3
    K_MAX = 10
    P_KEEP = 0.02
    RESCUE_MAX_FRAC = 0.15
    ANCHOR_EXT_P = 0.15
    RESCUE_CANDS = 6
    # The fitted up3 models were fit on decks of 30-260 cards. Tiny decks (where screening is moot
    # because every pair fits in the pool) are outside that domain: keep the legacy path there.
    MIN_CARDS_FITTED = 10
    # WP-UP4 guards (see correlation/guards.py). Disable only for ablations.
    GUARDS_ENABLED = True

    def __init__(
        self,
        calibrator: Optional[LinkCalibrator] = None,
        set_verifier: Optional[SetVerifier] = None,
        models: Optional[Any] = None,
    ) -> None:
        self.calibrator = calibrator
        self.set_verifier = set_verifier
        self.models = models
        # Feature rows of the last run (edge/proposal -> vector); consumed by evals/calibration_fit.py.
        self.last_rows: Dict[str, Any] = {}

    @staticmethod
    def _domain_support(up3: Any) -> DomainSupport:
        """Fitted support travels with the model bundle (explicit bundles without one: guard unavailable)."""
        return DomainSupport.from_meta(getattr(up3, "meta", None) or {})

    @staticmethod
    def _link_gate(edge, ev, fp_map, desc, sv_q, rescue_pos, strict_mode) -> Optional[str]:
        """WP-UP4: reason why a link is forbidden (probability capped at GATE_CAP), or None."""
        a_id, b_id = edge
        fa, fb = fp_map.get(a_id), fp_map.get(b_id)
        da, db = desc.get(a_id), desc.get(b_id)
        if fa is None or fb is None or da is None or db is None:
            return None
        if is_nonrecipe(da, fa) or is_nonrecipe(db, fb):
            return "nonrecipe"
        if physical_conflict(ev):
            return "physical_conflict"
        q = sv_q.get(edge)
        if starts_new_recipe(fa, fb, db) and not sv_positive_ok(q, STRONG_MIN_SV_CONF):
            return "new_heading"
        if edge in rescue_pos and not sv_positive_ok(q, STRICT_MIN_SV_CONF):
            return "rescue_unconfirmed"
        if strict_mode and not sv_positive_ok(q, STRICT_MIN_SV_CONF):
            return "strict_unconfirmed"
        return None

    def _rescue_jobs(self, fps, fp_map, screening, verifications) -> List[Tuple[str, VisualFingerprint, List[VisualFingerprint]]]:
        """Second-pass rescue for title-less fragments that no first-pass verification claimed.

        Each orphan is offered to its top-3 most likely predecessors (fitted rank model) that have not
        already judged it and that do not already have a verified successor. Anchors are grouped,
        ranked by their best candidate score and capped at RESCUE_MAX_FRACxN calls. Depends only on
        first-pass results + rank model (no filenames, hashes or capture order).
        """
        claimed: Set[str] = set()
        has_succ: Set[str] = set()
        seen: Set[Tuple[str, str]] = set()
        for v in verifications:
            seq = [v.anchor] + list(v.continuations or [])
            claimed.update(seq[1:])
            has_succ.update(seq[:-1])
            for c in list(v.continuations or []) + list(v.rejected or []):
                seen.add((v.anchor, c))
        incoming: Dict[str, List[Tuple[float, str]]] = {}
        for (a_id, b_id), z in screening.rank_logit.items():
            incoming.setdefault(b_id, []).append((z, a_id))
        per_anchor: Dict[str, List[Tuple[float, str]]] = {}
        for fp in fps:
            b_id = fp.card_id
            d = screening.desc.get(b_id) or {}
            if b_id in claimed or d.get("has_title", 0.0) >= 1.0:
                continue
            preds = sorted(incoming.get(b_id, []), key=lambda t: (-t[0], t[1]))
            for z, a_id in preds[:3]:
                if (a_id, b_id) in seen or a_id in has_succ:
                    continue
                per_anchor.setdefault(a_id, []).append((z, b_id))
        cap = max(1, int(self.RESCUE_MAX_FRAC * len(fps)))
        ranked = sorted(per_anchor.items(), key=lambda kv: (-max(z for z, _ in kv[1]), kv[0]))[:cap]
        jobs = []
        for a_id, lst in ranked:
            cands = [fp_map[b] for _, b in sorted(lst, key=lambda t: (-t[0], t[1]))[: self.RESCUE_CANDS] if b in fp_map]
            if a_id in fp_map and cands:
                jobs.append((a_id, fp_map[a_id], cands))
        return jobs

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
        """Groups a collection of card fingerprints into ordered recipe sequences and standalones.

        Args:
            fps: List of VisualFingerprints in the collection / batch.
            images: ImageProvider providing raw image bytes by card_id.
            llm_client: Optional multimodal LLMClient. If None, set_verify is skipped and proposals
                        are marked UNCERTAIN/unverified without fabricating scores.
            settings: Optional Settings instance. Defaults to get_settings().
            embedder: Optional text/multimodal embedder for top-k retrieval.
            toggles: Optional FeatureToggles. If None, reads RB_FEATURE_TOGGLES from environment.

        Returns:
            GroupingResult containing chains, proposals, evidence, verifications, and needs_review routing.
        """
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

            # INVARIANT (INV-AC4 / FIND-04): Standalone proposals carry no link transitions.
            # Confidence must be None (never a hardcoded prior like 0.90).
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

        from recipeboxer.correlation.linkmodel import default_models, link_feature_vector, rank_feature_vector

        up3 = self.models if self.models is not None else default_models()
        pre_m, rank_m, link_m = up3.get("pre"), up3.get("rank"), up3.get("link")
        multi_m, single_m = up3.get("multi"), up3.get("single")
        link_ood_m = up3.get("link_ood")
        # The fitted models were fit on SetVerifier-backed runs; without a verifier (offline / no LLM)
        # the legacy top-k + calibrator path is kept unchanged.
        _sv_possible = eff_toggles.set_verify and (llm_client is not None or self.set_verifier is not None)
        use_ranked = (
            pre_m is not None and rank_m is not None and _sv_possible
            and len(fps) >= self.MIN_CARDS_FITTED
        )

        def _feat_filter(name: str) -> bool:
            return _feature_is_active(name, eff_toggles)

        # 2. Candidate screening
        screening = None
        domain_report: Dict[str, Any] = {"available": False, "ood": False}
        strict_mode = False
        all_evidence: List[PairEvidence] = []
        if use_ranked:
            from recipeboxer.correlation.ranking import ranked_candidates

            screening = await asyncio.to_thread(
                ranked_candidates, fps, background, pre_m, rank_m,
                images=images, feature_filter=_feat_filter,
                pool_m=int(min(self.POOL_M_MAX, max(self.POOL_M, round(self.POOL_M_FRAC * len(fps))))),
                k_min=self.K_MIN, k_max=self.K_MAX, p_keep=self.P_KEEP,
            )
            candidates_by_anchor = screening.candidates
            all_evidence = list(screening.evidence.values())
            # WP-UP4 out-of-distribution guard: decks whose cards fall outside the fitted support run in
            # strict mode (every link needs validated SetVerifier confirmation; no orphan rescue).
            domain_report = self._domain_support(up3).assess(fps, screening.desc)
            strict_mode = bool(domain_report.get("ood"))
        else:
            active_embedder = embedder if eff_toggles.embeddings else None
            candidates_by_anchor = top_k_candidates(
                fps,
                background,
                k=5,
                embedder=active_embedder,
                images=images,
            )
            for ev_list in candidates_by_anchor.values():
                for ev in ev_list:
                    # Filter features based on active FeatureToggles and recompute total_log_lr
                    active_features = [f for f in ev.features if _feature_is_active(f.name, eff_toggles)]
                    new_total_log_lr = sum(
                        f.weight * f.log_lr
                        for f in active_features
                        if f.log_lr is not None
                    )
                    ev.features = active_features
                    ev.total_log_lr = round(new_total_log_lr, 4)
                    all_evidence.append(ev)

        # 3. Multimodal Set-Level Verification (bounded concurrency; single grouping code path)
        verifications: List[SetVerification] = []
        verification_by_anchor: Dict[str, SetVerification] = {}
        rescue_verifications: List[SetVerification] = []
        fp_map = {fp.card_id: fp for fp in fps}

        can_verify = eff_toggles.set_verify and (llm_client is not None or self.set_verifier is not None)
        verifier = None
        max_conc = max(1, int(getattr(eff_settings, "llm_max_concurrency", 8) or 8))
        sem = asyncio.Semaphore(max_conc)

        async def _verify_one(job: Tuple[str, VisualFingerprint, List[VisualFingerprint]]):
            anchor_id, anchor_fp, cand_fps = job
            async with sem:
                try:
                    return anchor_id, await verifier.verify(anchor_fp, cand_fps, images=images)
                except BudgetExceeded:
                    raise
                except Exception as exc:
                    logger.warning(f"SetVerifier failed for anchor card {anchor_id}: {exc}")
                    return anchor_id, None

        if can_verify:
            verifier = self.set_verifier or SetVerifier(llm_client=llm_client, settings=eff_settings)
            jobs: List[Tuple[str, VisualFingerprint, List[VisualFingerprint]]] = []
            ext_jobs: List[Tuple[str, VisualFingerprint, List[VisualFingerprint]]] = []
            for anchor_id, ev_list in candidates_by_anchor.items():
                if not ev_list:
                    continue
                anchor_fp = fp_map.get(anchor_id)
                if not anchor_fp:
                    continue
                is_ext = not _is_candidate_anchor(anchor_fp)
                if is_ext:
                    # WP-UP3: backs / continuation cards can precede another card (e.g. back of card 1 ->
                    # front of card 2). Verify them too when the fitted rank model finds a likely successor.
                    top_p = screening.rank_p((anchor_id, ev_list[0].b)) if screening is not None else None
                    if top_p is None or top_p < self.ANCHOR_EXT_P:
                        continue
                cand_fps = [fp_map[ev.b] for ev in ev_list if ev.b in fp_map]
                if not cand_fps:
                    continue
                (ext_jobs if is_ext else jobs).append((anchor_id, anchor_fp, cand_fps))

            # Extension anchors are skipped when every candidate pair was already offered in the reverse
            # direction by a primary anchor (the verifier judges set membership, so it has been examined).
            offered = {(a, c.card_id) for a, _, cs in jobs for c in cs}
            for anchor_id, anchor_fp, cand_fps in ext_jobs:
                if any((c.card_id, anchor_id) not in offered for c in cand_fps):
                    jobs.append((anchor_id, anchor_fp, cand_fps))

            results = await asyncio.gather(*(_verify_one(j) for j in jobs))
            for anchor_id, ver in results:  # deterministic order (job order)
                if ver is None:
                    continue
                verifications.append(ver)
                verification_by_anchor[anchor_id] = ver

            # 3b. WP-UP3 orphan rescue: title-less fragments that no verification claimed are offered to
            # their most likely predecessors (fitted rank model) in one extra, bounded verification round.
            # Skipped when every first-pass verification failed (verifier unavailable / unusable).
            verifier_ok = bool(verifications) or not jobs
            if use_ranked and screening is not None and self.RESCUE_MAX_FRAC > 0 and verifier_ok and not strict_mode:
                rescue_jobs = self._rescue_jobs(fps, fp_map, screening, verifications)
                if rescue_jobs:
                    r_results = await asyncio.gather(*(_verify_one(j) for j in rescue_jobs))
                    for anchor_id, ver in r_results:
                        if ver is None:
                            continue
                        rescue_verifications.append(ver)
                        verification_by_anchor.setdefault(anchor_id, ver)

        # 4. Link probabilities
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
        pos_direct: Set[Tuple[str, str]] = set()
        sv_seen: Set[Tuple[str, str]] = set()
        rescue_pos: Set[Tuple[str, str]] = set()
        pos_conf: Dict[Tuple[str, str], float] = {}
        sv_q: Dict[Tuple[str, str], Tuple[int, int, float]] = {}  # WP-UP4: quote quality of positive edges

        for ver, is_rescue in [(v, False) for v in verifications] + [(v, True) for v in rescue_verifications]:
            seq = [ver.anchor] + list(ver.continuations or [])
            for idx in range(len(seq) - 1):
                e = (seq[idx], seq[idx + 1])
                verified_positive_edges.add(e)
                if idx == 0:
                    pos_direct.add(e)
                if is_rescue:
                    rescue_pos.add(e)
                if ver.confidence is not None:
                    pos_conf[e] = max(pos_conf.get(e, 0.0), float(ver.confidence))
                q = sv_quality(ver)
                if e not in sv_q or (sv_positive_ok(q, 0.0), q[2]) > (sv_positive_ok(sv_q[e], 0.0), sv_q[e][2]):
                    sv_q[e] = q
            for c in list(ver.continuations or []) + list(ver.rejected or []):
                sv_seen.add((ver.anchor, c))
            for rej in (ver.rejected or []):
                for s_node in seq:
                    verified_negative_edges.add((s_node, rej))
                    verified_negative_edges.add((rej, s_node))
        n_into: Dict[str, int] = {}
        n_out: Dict[str, int] = {}
        for a_id, b_id in verified_positive_edges:
            n_into[b_id] = n_into.get(b_id, 0) + 1
            n_out[a_id] = n_out.get(a_id, 0) + 1

        links: Dict[Tuple[str, str], float] = {}
        pair_ev_by_edge: Dict[Tuple[str, str], PairEvidence] = {}
        rank_logit: Dict[Tuple[str, str], float] = dict(screening.rank_logit) if screening is not None else {}

        # Verified positive edges outside the screened pool still need evidence.
        ev_edges = {(ev.a, ev.b) for ev in all_evidence}
        for u_id, v_id in sorted(verified_positive_edges):
            if (u_id, v_id) not in ev_edges and u_id in fp_map and v_id in fp_map:
                ev = pair_evidence(fp_map[u_id], fp_map[v_id], background, images=images)
                ev.features = [f for f in ev.features if _feature_is_active(f.name, eff_toggles)]
                ev.total_log_lr = round(sum(f.weight * f.log_lr for f in ev.features if f.log_lr is not None), 4)
                if screening is not None:
                    rank_logit[(u_id, v_id)] = float(rank_m.logit(rank_feature_vector(
                        ev, screening.desc[u_id], screening.desc[v_id], fp_map[u_id], fp_map[v_id]))[0])
                all_evidence.append(ev)
                ev_edges.add((u_id, v_id))

        use_link_model = bool(use_ranked and can_verify and link_m is not None)
        # WP-UP4 hard link constraints (fitted path only; the legacy path is unchanged).
        guard_active = bool(use_ranked and can_verify and screening is not None and self.GUARDS_ENABLED)
        gate_counts: Dict[str, int] = {}
        link_rows: List[Tuple[Tuple[str, str], Any]] = []
        for ev in all_evidence:
            edge = (ev.a, ev.b)
            set_verified_outcome: Optional[bool] = None
            if edge in verified_positive_edges:
                set_verified_outcome = True
            elif edge in verified_negative_edges:
                set_verified_outcome = False
            if use_ranked and can_verify:
                vec = link_feature_vector(
                    rank_logit.get(edge, 0.0),
                    pos_direct=edge in pos_direct,
                    pos_chain=edge in verified_positive_edges,
                    neg=edge in verified_negative_edges,
                    seen=edge in sv_seen or edge in verified_positive_edges,
                    conf=pos_conf.get(edge),
                    rescue=edge in rescue_pos,
                    n_into_b=n_into.get(edge[1], 0),
                    n_out_a=n_out.get(edge[0], 0),
                    n_cards=len(fps),
                )
                link_rows.append((edge, vec))
            if use_link_model:
                # WP-UP4: in strict (out-of-support) mode use the link model fitted on manuscript-style
                # decks when shipped; the card-fitted model under-rates confirmed links there.
                lm = link_ood_m if (strict_mode and link_ood_m is not None) else link_m
                p = float(lm.predict_proba(vec)[0])
            else:
                p = calibrator.predict(ev, set_verified=set_verified_outcome)
            if guard_active:
                gate = self._link_gate(edge, ev, fp_map, screening.desc, sv_q, rescue_pos, strict_mode)
                if gate is not None:
                    gate_counts[gate] = gate_counts.get(gate, 0) + 1
                    # RT3-07: record the gate so HITL evidence does not present the cap as a probability
                    ev.gate = gate
                    ev.p_link_ungated = round(p, 4)
                    p = min(p, GATE_CAP)
            ev.p_link = round(p, 4)
            links[edge] = ev.p_link
            pair_ev_by_edge[edge] = ev
        verifications = verifications + rescue_verifications

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

        # Opt-in maintainer debug dump (RB_DEBUG_GROUPING=1)
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

        from recipeboxer.correlation.linkmodel import multi_feature_vector, single_feature_vector

        log_n = math.log(max(2, len(fps)))
        out_ps: Dict[str, List[Tuple[str, float]]] = {}
        in_ps: Dict[str, List[Tuple[str, float]]] = {}
        for (a_id, b_id), p in links.items():
            out_ps.setdefault(a_id, []).append((b_id, p))
            in_ps.setdefault(b_id, []).append((a_id, p))

        def _rank_p(edge: Tuple[str, str]) -> float:
            z = rank_logit.get(edge)
            return 0.0 if z is None else 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, z))))

        collect_cluster = bool(use_ranked and can_verify and screening is not None)
        single_rows: List[Tuple[List[str], Any]] = []
        multi_rows: List[Tuple[List[str], Any]] = []

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

                # INVARIANT (INV-AC4 / FIND-04): never a hard-coded prior. WP-UP3: when SetVerifier ran
                # and a fitted 'single' model exists, confidence is the FITTED probability that this card
                # is truly a standalone item (held-out calibrated); otherwise None.
                single_conf: Optional[float] = None
                if collect_cluster:
                    outs = out_ps.get(cid, [])
                    ins = in_ps.get(cid, [])
                    ctx = {
                        "max_out_link": max((p for _, p in outs), default=0.0),
                        "max_in_link": max((p for _, p in ins), default=0.0),
                        "max_out_rank": max((_rank_p((cid, b)) for b, _ in outs), default=0.0),
                        "max_in_rank": max((_rank_p((a, cid)) for a, _ in ins), default=0.0),
                        "n_out_above_half": float(sum(1 for _, p in outs if p >= 0.5)),
                        "sv_any_continuation": 1.0 if (verification_by_anchor.get(cid) and verification_by_anchor[cid].continuations) else 0.0,
                        "log_n": log_n,
                    }
                    vec = single_feature_vector(screening.desc[cid], ctx)
                    single_rows.append(([cid], vec))
                    if single_m is not None:
                        single_conf = round(float(single_m.predict_proba(vec)[0]), 4)
                prop = CorrelationProposal(
                    proposal_id=f"prop_{uuid.uuid4().hex[:8]}",
                    ordered_card_ids=[cid],
                    part_count=1,
                    transition_scores=[],
                    heuristic_score=0.0,
                    semantic_score=1.0 if can_verify else None,
                    confidence=single_conf,
                    is_standalone=True,
                    continuity_explanation=("Single-sided card classified as standalone recipe." if can_verify else "Unverified card proposal awaiting review.")
                    + (f" Fitted P(standalone)={single_conf:.3f}." if single_conf is not None else ""),
                    recommended_action=action,
                    semantic_verified=sem_verified,
                    degraded_reason=deg_reason,
                )
                if single_conf is not None and single_conf < review_threshold:
                    needs_review_cards.add(cid)
                proposals.append(prop)

            else:
                # Multi-card sequence (N >= 2)
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

                # Reported chain confidence (legacy) = minimum transition confidence along the chain.
                chain_conf = min(s_scores) if s_scores else (min(t_scores) if t_scores else None)
                # RT3-05: provenance of chain_conf for the explanation text
                conf_src = "sv_raw" if s_scores else ("link_min" if t_scores else None)
                if can_verify and all(
                    (chain[k], chain[k + 1]) in verified_positive_edges for k in range(len(chain) - 1)
                ):
                    all_transitions_verified = True
                if collect_cluster:
                    # WP-UP3: fitted P(this chain is exactly one complete recipe), held-out calibrated.
                    chain_set = set(chain)
                    ctx = {
                        "max_tail_out_other": max((p for b, p in out_ps.get(chain[-1], []) if b not in chain_set), default=0.0),
                        "max_head_in_other": max((p for a, p in in_ps.get(chain[0], []) if a not in chain_set), default=0.0),
                        "log_n": log_n,
                    }
                    sv_flags = [(chain[k], chain[k + 1]) in verified_positive_edges for k in range(len(chain) - 1)]
                    vec = multi_feature_vector([screening.desc[c] for c in chain], t_scores, sv_flags, ctx)
                    multi_rows.append((list(chain), vec))
                    if multi_m is not None:
                        chain_conf = float(multi_m.predict_proba(vec)[0])
                        conf_src = "fitted"
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

                # Check degraded input flags
                for cid in chain:
                    if getattr(fp_by_id.get(cid), "degraded", False):
                        needs_review_cards.add(cid)
                        action = "NEEDS_REVIEW"

                if chain_conf is None:
                    conf_str = "No evidence-based confidence."
                elif conf_src == "fitted":
                    conf_str = f"Calibrated p={chain_conf:.3f}."
                elif conf_src == "sv_raw":
                    conf_str = f"Uncalibrated SetVerifier confidence={chain_conf:.3f}."
                else:
                    conf_str = f"Minimum link probability={chain_conf:.3f}."
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


        # Deterministic needs_review sorting
        sorted_needs_review = sorted(list(needs_review_cards))

        stats = {
            "card_count": len(fps),
            "chains_count": len(chains),
            "pairs_evaluated": len(all_evidence),
            "proposals_count": len(proposals),
            "needs_review_count": len(sorted_needs_review),
            "unverified_proposals_count": sum(1 for p in proposals if not p.semantic_verified),
            "set_verify_calls": len(verifications),
            "rescue_calls": len(rescue_verifications),
            "screening": "up3_ranked" if use_ranked else "legacy_top_k",
            "link_model": "up3_fitted" if use_link_model else "legacy_calibrator",
            "domain_guard": dict(domain_report, strict_mode=strict_mode),
            "link_gates": dict(sorted(gate_counts.items())),
        }
        if screening is not None:
            stats.update({f"screen_{k}": v for k, v in screening.stats.items()})
        self.last_rows = {"link": link_rows, "single": single_rows, "multi": multi_rows}

        return GroupingResult(
            chains=chains,
            proposals=proposals,
            evidence=all_evidence,
            verifications=verifications,
            needs_review=sorted_needs_review,
            stats=stats,
        )
