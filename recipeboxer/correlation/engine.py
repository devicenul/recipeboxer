"""GroupingEngine: Main entry point for RecipeBoxer card correlation (Phase 3).

Orchestrates collection-relative background fitting (DeckBackground), top-k candidate
screening, multimodal set-level verification (SetVerifier), probability calibration
(LinkCalibrator), and global minimum-cost path cover optimization.
"""

from __future__ import annotations

import json
import logging
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
from recipeboxer.correlation.evidence import compute_raw_features
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


@runtime_checkable
class ImageProvider(Protocol):
    """Protocol for supplying raw card image bytes by card ID."""

    def get(self, card_id: str) -> bytes:
        ...


class FileImageProvider:
    """Default filesystem-backed implementation of ImageProvider."""

    def __init__(self, path_map: Optional[Dict[str, Union[Path, str]]] = None) -> None:
        self._map: Dict[str, Path] = {k: Path(v) for k, v in (path_map or {}).items()}

    def get(self, card_id: str) -> bytes:
        p = self._map.get(card_id)
        if p and p.is_file():
            try:
                return p.read_bytes()
            except Exception:
                pass
        # Fallback to card_id as a direct path
        p_direct = Path(card_id)
        if p_direct.is_file():
            try:
                return p_direct.read_bytes()
            except Exception:
                pass
        return b""


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
    """

    def __init__(
        self,
        calibrator: Optional[LinkCalibrator] = None,
        set_verifier: Optional[SetVerifier] = None,
    ) -> None:
        self.calibrator = calibrator
        self.set_verifier = set_verifier

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
            prior = completeness_prior(fp)
            is_comp = prior.get("is_complete", 0.90) or 0.90
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
                confidence=round(is_comp, 4),
                is_standalone=True,
                continuity_explanation="Single-card collection classified as standalone recipe.",
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

        # 3. Multimodal Set-Level Verification
        verifications: List[SetVerification] = []
        verification_by_anchor: Dict[str, SetVerification] = {}

        can_verify = eff_toggles.set_verify and llm_client is not None
        if can_verify:
            verifier = self.set_verifier or SetVerifier(llm_client=llm_client, settings=eff_settings)
            fp_map = {fp.card_id: fp for fp in fps}

            for anchor_id, ev_list in candidates_by_anchor.items():
                if not ev_list:
                    continue
                anchor_fp = fp_map.get(anchor_id)
                if not anchor_fp or not _is_candidate_anchor(anchor_fp):
                    continue
                cand_fps = [fp_map[ev.b] for ev in ev_list if ev.b in fp_map]
                if not cand_fps:
                    continue

                try:
                    ver = await verifier.verify(anchor_fp, cand_fps, images=images)
                    verifications.append(ver)
                    verification_by_anchor[anchor_id] = ver
                except BudgetExceeded:
                    raise
                except Exception as exc:
                    logger.warning(f"SetVerifier failed for anchor card {anchor_id}: {exc}")

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

        links: Dict[Tuple[str, str], float] = {}
        pair_ev_by_edge: Dict[Tuple[str, str], PairEvidence] = {}

        for ev in all_evidence:
            edge = (ev.a, ev.b)
            # Determine set-verification outcome
            set_verified_outcome: Optional[bool] = None
            if ev.a in verification_by_anchor:
                ver = verification_by_anchor[ev.a]
                if ev.b in ver.continuations:
                    set_verified_outcome = True
                elif ev.b in ver.rejected:
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

        # 7. Build CorrelationProposals and HITL Routing
        proposals: List[CorrelationProposal] = []
        needs_review_cards: Set[str] = set()
        fp_by_id = {fp.card_id: fp for fp in fps}

        for chain in chains:
            if len(chain) == 1:
                cid = chain[0]
                fp = fp_by_id.get(cid)
                prior = node_costs.get(cid, {})
                is_comp = prior.get("is_complete", 0.90)
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
                    confidence=round(is_comp, 4),
                    is_standalone=True,
                    continuity_explanation="Single-sided card classified as standalone recipe." if can_verify else "Unverified card proposal awaiting review.",
                    recommended_action=action,
                    semantic_verified=sem_verified,
                    degraded_reason=deg_reason,
                )
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

                chain_conf = min(s_scores) if s_scores else (min(t_scores) if t_scores else 0.50)
                has_low_confidence = bool(chain_conf < review_threshold)
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

                expl_parts = [
                    f"Linked {len(chain)}-card sequence ({' -> '.join(chain)}). Calibrated p={chain_conf:.3f}."
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
                    confidence=round(chain_conf, 4),
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
        }

        return GroupingResult(
            chains=chains,
            proposals=proposals,
            evidence=all_evidence,
            verifications=verifications,
            needs_review=sorted_needs_review,
            stats=stats,
        )
