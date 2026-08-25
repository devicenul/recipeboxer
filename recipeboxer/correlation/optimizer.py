"""Global Pairing Optimizer for two-stage front/back card correlation.

Solves bipartite assignment across screened and verified candidate card pairs to
generate clean CorrelationProposals and identify standalone or unmatched cards.
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
    """Solves optimal global card assignment for recipe digitization jobs."""

    def __init__(
        self,
        min_pair_confidence: float = 0.50,
        verifier: Optional[GeminiSemanticVerifier] = None,
    ) -> None:
        """Initialize PairingOptimizer.

        Args:
            min_pair_confidence: Minimum composite confidence required to accept a pair proposal.
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
        all_card_ids = [fp.card_id for fp in fingerprints]
        fp_by_id = {fp.card_id: fp for fp in fingerprints}

        # 1. Generate candidate proposals if not provided
        if candidate_proposals is None:
            # Stage 1: Heuristic screening
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
        """Optimizes card pairings from a list of verified candidate proposals.

        Args:
            fingerprints: List of all VisualFingerprint items in the dataset.
            proposals: Candidate CorrelationProposal items.

        Returns:
            CorrelationResult containing assigned pairs, standalones, and unmatched cards.
        """
        fp_by_id: Dict[str, VisualFingerprint] = {fp.card_id: fp for fp in fingerprints}
        assigned_card_ids: Set[str] = set()
        final_proposals: List[CorrelationProposal] = []
        unmatched_card_ids: List[str] = []

        # Sort candidate proposals descending by composite confidence score
        sorted_proposals = sorted(proposals, key=lambda p: p.confidence, reverse=True)

        # 1. Greedy Bipartite Matching for Front/Back Pairs
        for prop in sorted_proposals:
            f_id = prop.front_card_id
            b_id = prop.back_card_id

            if not b_id:
                # Standalone proposal already packaged
                continue

            if f_id in assigned_card_ids or b_id in assigned_card_ids:
                # One of the cards was already matched to a higher-confidence pair
                continue

            if prop.confidence < self.min_pair_confidence or prop.recommended_action == "REJECT":
                # Does not meet minimum confidence threshold
                continue

            # Accept this front/back pairing
            assigned_card_ids.add(f_id)
            assigned_card_ids.add(b_id)
            final_proposals.append(prop)

        # 2. Package STANDALONE cards
        for fp in fingerprints:
            card_id = fp.card_id
            if card_id in assigned_card_ids:
                continue

            role_str = _get_enum_str(fp.role)
            if role_str == "STANDALONE":
                standalone_prop = CorrelationProposal(
                    proposal_id=f"prop_{uuid.uuid4().hex[:8]}",
                    front_card_id=card_id,
                    back_card_id=None,
                    heuristic_score=0.0,
                    semantic_score=0.0,
                    confidence=fp.role_confidence,
                    is_standalone=True,
                    continuity_explanation="Single-sided card classified as self-contained standalone recipe.",
                    recommended_action="STANDALONE",
                )
                final_proposals.append(standalone_prop)
                assigned_card_ids.add(card_id)

        # 3. Collect Unmatched Cards (e.g. UNKNOWN noise tabs, unpartnered fronts or backs)
        for fp in fingerprints:
            card_id = fp.card_id
            if card_id not in assigned_card_ids:
                unmatched_card_ids.append(card_id)

        return CorrelationResult(
            proposals=final_proposals,
            unmatched_card_ids=unmatched_card_ids,
        )
