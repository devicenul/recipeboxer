"""Open-ended N-part sequence correlation engine and graph clustering for RecipeBoxer.

Constructs directed transition probability graphs P(C_i -> C_j) across visual fingerprints,
evaluating substrate colorimetry, ruling, ink, mirrored physical stains, scan sequence proximity,
explicit sequential header progression, and semantic continuation. Chains transitions into
maximal ordered multi-card sequences (N >= 1) for human review.
"""

from __future__ import annotations

import logging
import uuid
from typing import Dict, List, Optional, Set, Tuple, Union

from recipeboxer.config import get_settings
from recipeboxer.correlation.semantic import GeminiSemanticVerifier
from recipeboxer.models.correlation import CorrelationProposal, CorrelationResult
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.vision.heuristics import _get_enum_str, screen_candidate_pairs

logger = logging.getLogger(__name__)


class PairingOptimizer:
    """Solves open-ended directed sequence correlation and clustering for recipe digitization."""

    def __init__(
        self,
        min_pair_confidence: float = 0.50,
        verifier: Optional[GeminiSemanticVerifier] = None,
    ) -> None:
        """Initialize PairingOptimizer.

        Args:
            min_pair_confidence: Minimum transition confidence required to link adjacent cards.
            verifier: Optional GeminiSemanticVerifier instance for two-stage correlation.
        """
        self.settings = get_settings()
        self.min_pair_confidence = min_pair_confidence
        self.verifier = verifier or GeminiSemanticVerifier()

    async def optimize(
        self,
        fingerprints: List[VisualFingerprint],
        candidate_proposals: Optional[List[CorrelationProposal]] = None,
    ) -> CorrelationResult:
        """Runs the two-stage correlation pipeline and optimizes global card matching.

        Args:
            fingerprints: List of all VisualFingerprint cards in the session.
            candidate_proposals: Optional pre-computed CorrelationProposal items. If None,
                                 runs Stage 1 screening and Stage 2 verification.

        Returns:
            CorrelationResult with finalized proposals and unmatched cards.
        """
        if candidate_proposals is None:
            # Stage 1: Directed heuristic screening
            screened_pairs = screen_candidate_pairs(
                fingerprints,
                threshold=self.settings.heuristic_screen_threshold,
            )

            # Stage 2: Gemini semantic verification
            candidate_proposals = await self.verifier.verify_candidate_proposals(screened_pairs)

        return self.optimize_proposals(fingerprints, candidate_proposals)

    def optimize_proposals(
        self,
        fingerprints: List[VisualFingerprint],
        proposals: List[CorrelationProposal],
    ) -> CorrelationResult:
        """Chains verified candidate transitions into maximal ordered N-card sequence proposals.

        Args:
            fingerprints: List of all VisualFingerprint items in the dataset.
            proposals: Candidate CorrelationProposal items (pairwise or multi-card).

        Returns:
            CorrelationResult containing assigned sequences, standalones, and unmatched cards.
        """
        fp_by_id: Dict[str, VisualFingerprint] = {fp.card_id: fp for fp in fingerprints}
        assigned_card_ids: Set[str] = set()
        final_proposals: List[CorrelationProposal] = []
        unmatched_card_ids: List[str] = []

        # Sort candidate proposals descending by composite confidence score
        sorted_proposals = sorted(proposals, key=lambda p: p.confidence, reverse=True)

        # 1. First absorb any already-formed multi-part sequences (N >= 3)
        for prop in sorted_proposals:
            if len(prop.ordered_card_ids) >= 3 and prop.confidence >= self.min_pair_confidence and prop.recommended_action != "REJECT":
                if not any(cid in assigned_card_ids for cid in prop.ordered_card_ids):
                    for cid in prop.ordered_card_ids:
                        assigned_card_ids.add(cid)
                    # If semantic_score is None, treat as missing evidence (FIND-04)
                    if prop.semantic_score is None:
                        h_score = prop.heuristic_score if prop.heuristic_score is not None else 0.0
                        prop = prop.model_copy(update={
                            "confidence": round(h_score, 4),
                            "recommended_action": "UNCERTAIN",
                            "semantic_verified": False,
                            "degraded_reason": "MISSING_SEMANTIC_EVIDENCE",
                        })
                    final_proposals.append(prop)

        # 2. Directed Transition Graph Chaining for pairwise transitions
        # Graph adjacency: next_hop[u] = (v, transition_score, heuristic_score, semantic_score, explanation)
        next_hop: Dict[str, Tuple[str, float, float, Optional[float], str]] = {}
        prev_hop: Dict[str, str] = {}

        def would_create_cycle(start_node: str, end_node: str) -> bool:
            """Checks if adding edge start_node -> end_node creates a directed cycle."""
            curr = end_node
            visited = set()
            while curr in next_hop and curr not in visited:
                visited.add(curr)
                if curr == start_node:
                    return True
                curr = next_hop[curr][0]
            return curr == start_node

        for prop in sorted_proposals:
            if len(prop.ordered_card_ids) != 2:
                continue

            u, v = prop.ordered_card_ids[0], prop.ordered_card_ids[1]

            if u in assigned_card_ids or v in assigned_card_ids:
                continue

            if prop.confidence < self.min_pair_confidence or prop.recommended_action == "REJECT":
                continue

            # Ensure u has no outgoing link and v has no incoming link
            if u in next_hop or v in prev_hop:
                continue

            # Prevent cycles
            if would_create_cycle(u, v):
                continue

            # Accept directed transition link u -> v
            h_score = prop.heuristic_score if getattr(prop, "heuristic_score", None) is not None else prop.confidence
            s_score = getattr(prop, "semantic_score", None)
            expl = getattr(prop, "continuity_explanation", "Sequential continuation")
            next_hop[u] = (v, prop.confidence, h_score, s_score, expl)
            prev_hop[v] = u

        # 3. Assemble maximal linear paths [C_1, C_2, ..., C_N]
        # Start nodes have out-degree > 0 and in-degree == 0
        all_graph_nodes = set(next_hop.keys()) | set(prev_hop.keys())
        start_nodes = [node for node in next_hop.keys() if node not in prev_hop]

        # In case of disconnected cycles (should not happen with would_create_cycle), pick any
        visited_nodes: Set[str] = set()

        for start in start_nodes:
            if start in visited_nodes:
                continue

            chain_ids: List[str] = [start]
            visited_nodes.add(start)
            t_scores: List[float] = []
            h_scores: List[float] = []
            s_scores: List[float] = []
            explanations: List[str] = []

            curr = start
            while curr in next_hop:
                nxt, conf, h_s, s_s, exp = next_hop[curr]
                if nxt in visited_nodes:
                    break
                chain_ids.append(nxt)
                visited_nodes.add(nxt)
                t_scores.append(conf)
                h_scores.append(h_s)
                if s_s is not None:
                    s_scores.append(s_s)
                if exp:
                    explanations.append(exp)
                curr = nxt

            for cid in chain_ids:
                assigned_card_ids.add(cid)

            # Derive fallbacks from actual component fingerprints instead of hardcoding 0.85
            fp_start = fp_by_id.get(start)
            fallback_conf = (
                getattr(fp_start, "role_confidence", 0.0)
                if fp_start and getattr(fp_start, "role_confidence", None) is not None
                else 0.0
            )

            avg_conf = sum(t_scores) / len(t_scores) if t_scores else fallback_conf
            avg_h = sum(h_scores) / len(h_scores) if h_scores else fallback_conf

            # Missing semantic evidence handling (FIND-04)
            missing_semantic = len(s_scores) < len(h_scores) or not s_scores
            if missing_semantic:
                avg_s = sum(s_scores) / len(s_scores) if s_scores else None
                final_conf = avg_h
                action = "UNCERTAIN"
                sem_verified = False
                deg_reason = "MISSING_SEMANTIC_EVIDENCE"
            else:
                avg_s = sum(s_scores) / len(s_scores)
                final_conf = avg_conf
                action = "SEQUENCE"
                sem_verified = True
                deg_reason = None

            full_expl = " | ".join(explanations) if explanations else f"Connected {len(chain_ids)}-card sequence."

            seq_proposal = CorrelationProposal(
                proposal_id=f"prop_{uuid.uuid4().hex[:8]}",
                ordered_card_ids=chain_ids,
                part_count=len(chain_ids),
                transition_scores=t_scores,
                heuristic_score=round(avg_h, 4),
                semantic_score=round(avg_s, 4) if avg_s is not None else None,
                confidence=round(final_conf, 4),
                is_standalone=False,
                continuity_explanation=full_expl,
                recommended_action=action,
                semantic_verified=sem_verified,
                degraded_reason=deg_reason,
            )
            final_proposals.append(seq_proposal)

        # 4. Package STANDALONE cards (single-card self-contained recipes)
        for fp in fingerprints:
            card_id = fp.card_id
            if card_id in assigned_card_ids:
                continue

            role_str = _get_enum_str(fp.role)
            if role_str == "STANDALONE":
                standalone_prop = CorrelationProposal(
                    proposal_id=f"prop_{uuid.uuid4().hex[:8]}",
                    ordered_card_ids=[card_id],
                    part_count=1,
                    transition_scores=[],
                    heuristic_score=0.0,
                    semantic_score=0.0,
                    confidence=fp.role_confidence,
                    is_standalone=True,
                    continuity_explanation="Single-sided card classified as self-contained standalone recipe.",
                    recommended_action="STANDALONE",
                )
                final_proposals.append(standalone_prop)
                assigned_card_ids.add(card_id)

        # 5. Collect Unmatched Cards
        for fp in fingerprints:
            card_id = fp.card_id
            if card_id not in assigned_card_ids:
                unmatched_card_ids.append(card_id)

        return CorrelationResult(
            proposals=final_proposals,
            unmatched_card_ids=unmatched_card_ids,
        )
