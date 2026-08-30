"""Empirical Challenger Deep Adversarial Test Suite for N-Part Sequence Correlation.

Covers:
1. Directed Sequence Correlation Engine for 1-card, 2-card, 3-card (Thanksgiving Turkey),
   4-card (Dark Wedding Fruitcake), and N >= 5 card sequences (scaling up to 20 cards).
2. Edge cases: cycles (2-cycle, 3-cycle, N-cycle, self-loop), disconnected cards,
   missing intermediate cards / fragmented sequences, branching proposals / competing transitions.
3. Substrate heuristics: sequential headers, role compatibility matrix, colorimetry Delta E, ruling, stains.
4. SessionStore & API round-trip serialization of ordered_card_ids.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from recipeboxer.agent.orchestrator import PipelineSession
from recipeboxer.correlation.optimizer import PairingOptimizer
from recipeboxer.correlation.semantic import GeminiSemanticVerifier
from recipeboxer.extraction.extractor import GeminiRecipeExtractor
from recipeboxer.models.correlation import CorrelationProposal, CorrelationResult
from recipeboxer.models.domain import (
    ArtifactType,
    BoundaryText,
    CardQuadrant,
    CardRole,
    InkColor,
    InkInfo,
    InkType,
    PaperColorInfo,
    PaperRulingInfo,
    PaperRulingType,
    PhysicalArtifact,
    VisualFingerprint,
)
from recipeboxer.models.hitl import CardPairApproval, CardSequenceApproval, HITLDecision
from recipeboxer.models.pipeline import IngestionMode, PipelineStartRequest, PipelineState
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.storage.session_store import SessionStore
from recipeboxer.vision.heuristics import (
    calculate_color_similarity,
    calculate_heuristic_distance,
    calculate_ink_similarity,
    calculate_proximity_score,
    calculate_role_compatibility,
    calculate_ruling_similarity,
    calculate_sequential_header_score,
    calculate_stain_similarity,
    screen_candidate_pairs,
)
from tests.conftest import MockGeminiClient, SQLiteFingerprintCache, SyntheticDeckInfo
from tests.fixtures.loader import SampleCardCorpus, load_sample_corpus


def _create_test_fingerprint(
    card_id: str,
    role: CardRole = CardRole.SEQUENCE_START,
    role_confidence: float = 0.95,
    first_line: str = "",
    last_line: str = "",
    has_continuation: bool = False,
    is_cut_off: bool = False,
    paper_tone: str = "AGED_CREAM",
    hex_color: str = "#F6ECC5",
    ruling_type: PaperRulingType = PaperRulingType.STANDARD_RULED_BLUE,
    ink_type: InkType = InkType.FOUNTAIN_PEN,
    ink_color: InkColor = InkColor.BLUE,
) -> VisualFingerprint:
    """Helper to synthesize precise VisualFingerprint instances."""
    return VisualFingerprint(
        card_id=card_id,
        image_path=f"/tmp/scans/{card_id}",
        sha256_hash=hashlib.sha256(card_id.encode("utf-8")).hexdigest(),
        role=role,
        role_confidence=role_confidence,
        boundary_text=BoundaryText(
            first_line=first_line,
            last_line=last_line,
            has_continuation_symbol=has_continuation,
            is_cut_off_mid_sentence=is_cut_off,
        ),
        paper_color=PaperColorInfo(
            paper_tone=paper_tone,
            hex_approx=hex_color,
            lab_l=92.0,
            lab_a=1.5,
            lab_b=14.0,
        ),
        ruling=PaperRulingInfo(
            ruling_type=ruling_type,
            has_ruling=(ruling_type != PaperRulingType.UNRULED_BLANK),
            line_spacing_px=24.0,
        ),
        ink=InkInfo(
            ink_type=ink_type,
            ink_color=ink_color,
        ),
        artifacts=[],
    )


# ============================================================================
# 1. DIRECTED SEQUENCE ENGINE TESTS (1-card, 2-card, 3-card, 4-card, N>=5)
# ============================================================================

class TestDirectedSequenceCorrelationEngine:
    """Empirical verification of sequence chaining across varying card lengths."""

    @pytest.fixture
    def optimizer(self) -> PairingOptimizer:
        return PairingOptimizer(min_pair_confidence=0.50)

    def test_single_card_standalone_proposals(self, optimizer: PairingOptimizer):
        """1-card recipes produce standalone proposals with part_count=1 and is_standalone=True."""
        fp1 = _create_test_fingerprint("standalone_fudge.jpg", role=CardRole.STANDALONE, role_confidence=0.98)
        fp2 = _create_test_fingerprint("standalone_cookies.jpg", role=CardRole.STANDALONE, role_confidence=0.92)

        result = optimizer.optimize_proposals([fp1, fp2], proposals=[])
        assert len(result.proposals) == 2
        assert len(result.unmatched_card_ids) == 0

        for prop in result.proposals:
            assert prop.part_count == 1
            assert len(prop.ordered_card_ids) == 1
            assert prop.is_standalone is True
            assert prop.recommended_action == "STANDALONE"
            assert prop.transition_scores == []

    def test_two_card_sequence_chaining(self, optimizer: PairingOptimizer):
        """2-card sequence is cleanly formed from a single directed transition u -> v."""
        fp_a = _create_test_fingerprint("card_a_p1.jpg", role=CardRole.SEQUENCE_START)
        fp_b = _create_test_fingerprint("card_a_p2.jpg", role=CardRole.SEQUENCE_CONTINUATION)

        prop_ab = CorrelationProposal(
            proposal_id="p_ab",
            ordered_card_ids=["card_a_p1.jpg", "card_a_p2.jpg"],
            confidence=0.94,
            heuristic_score=0.92,
            semantic_score=0.96,
            recommended_action="SEQUENCE",
        )

        result = optimizer.optimize_proposals([fp_a, fp_b], [prop_ab])
        assert len(result.proposals) == 1
        assert len(result.unmatched_card_ids) == 0

        seq = result.proposals[0]
        assert seq.ordered_card_ids == ["card_a_p1.jpg", "card_a_p2.jpg"]
        assert seq.part_count == 2
        assert seq.confidence == 0.94
        assert seq.is_standalone is False
        assert seq.transition_scores == [0.94]

    def test_three_card_sequence_chaining_synthetic(self, optimizer: PairingOptimizer):
        """3-card sequence chains P(C1 -> C2) and P(C2 -> C3) into [C1, C2, C3]."""
        c1 = _create_test_fingerprint("turkey_c1.jpg", role=CardRole.SEQUENCE_START)
        c2 = _create_test_fingerprint("turkey_c2.jpg", role=CardRole.SEQUENCE_CONTINUATION)
        c3 = _create_test_fingerprint("turkey_c3.jpg", role=CardRole.SEQUENCE_CONTINUATION)

        prop_12 = CorrelationProposal(
            proposal_id="p12",
            ordered_card_ids=["turkey_c1.jpg", "turkey_c2.jpg"],
            confidence=0.92,
            heuristic_score=0.90,
            semantic_score=0.94,
            continuity_explanation="Step 1 to Step 2",
        )
        prop_23 = CorrelationProposal(
            proposal_id="p23",
            ordered_card_ids=["turkey_c2.jpg", "turkey_c3.jpg"],
            confidence=0.88,
            heuristic_score=0.86,
            semantic_score=0.90,
            continuity_explanation="Step 2 to Gravy",
        )

        result = optimizer.optimize_proposals([c1, c2, c3], [prop_12, prop_23])
        assert len(result.proposals) == 1
        assert len(result.unmatched_card_ids) == 0

        seq = result.proposals[0]
        assert seq.ordered_card_ids == ["turkey_c1.jpg", "turkey_c2.jpg", "turkey_c3.jpg"]
        assert seq.part_count == 3
        assert len(seq.transition_scores) == 2
        assert pytest.approx(seq.confidence, abs=0.01) == 0.90
        assert "Step 1 to Step 2" in seq.continuity_explanation
        assert "Step 2 to Gravy" in seq.continuity_explanation

    def test_four_card_sequence_chaining_synthetic(self, optimizer: PairingOptimizer):
        """4-card sequence chains P(C1 -> C2 -> C3 -> C4) into [C1, C2, C3, C4]."""
        cards = [
            _create_test_fingerprint(f"fruitcake_p{i}.jpg", role=CardRole.SEQUENCE_START if i == 1 else CardRole.SEQUENCE_CONTINUATION)
            for i in range(1, 5)
        ]

        proposals = [
            CorrelationProposal(
                proposal_id=f"p_{i}_{i+1}",
                ordered_card_ids=[f"fruitcake_p{i}.jpg", f"fruitcake_p{i+1}.jpg"],
                confidence=0.90 + (0.01 * i),
                heuristic_score=0.90,
                semantic_score=0.92,
                continuity_explanation=f"Part {i} to Part {i+1}",
            )
            for i in range(1, 4)
        ]

        result = optimizer.optimize_proposals(cards, proposals)
        assert len(result.proposals) == 1
        seq = result.proposals[0]
        assert seq.ordered_card_ids == ["fruitcake_p1.jpg", "fruitcake_p2.jpg", "fruitcake_p3.jpg", "fruitcake_p4.jpg"]
        assert seq.part_count == 4
        assert len(seq.transition_scores) == 3

    def test_large_n_sequence_scaling_5_to_20_cards(self, optimizer: PairingOptimizer):
        """Chains arbitrarily large N-part sequences (N=5, 8, 10, 20) in exact linear order."""
        for n in (5, 8, 10, 20):
            card_ids = [f"booklet_page_{i:02d}.jpg" for i in range(1, n + 1)]
            fps = [
                _create_test_fingerprint(cid, role=CardRole.SEQUENCE_START if idx == 0 else CardRole.SEQUENCE_CONTINUATION)
                for idx, cid in enumerate(card_ids)
            ]

            # Create pairwise proposals in random order to test sort & chaining independence
            props = [
                CorrelationProposal(
                    proposal_id=f"p_{i}_{i+1}",
                    ordered_card_ids=[card_ids[i], card_ids[i + 1]],
                    confidence=round(0.85 + (i * 0.005), 4),
                    heuristic_score=0.88,
                    semantic_score=0.90,
                    continuity_explanation=f"Hop {i}->{i+1}",
                )
                for i in range(n - 1)
            ]
            shuffled_props = list(props)
            random.Random(42).shuffle(shuffled_props)

            result = optimizer.optimize_proposals(fps, shuffled_props)
            assert len(result.proposals) == 1, f"Failed for N={n}"
            seq = result.proposals[0]
            assert seq.ordered_card_ids == card_ids, f"Ordering failed for N={n}"
            assert seq.part_count == n
            assert len(seq.transition_scores) == n - 1
            assert len(result.unmatched_card_ids) == 0

    def test_corpus_thanksgiving_turkey_and_fruitcake_sets(self, sample_card_corpus: SampleCardCorpus, optimizer: PairingOptimizer):
        """Verifies 3-card Turkey and 4-card Fruitcake sets in sample corpus produce verified sequence proposals."""
        multi_sets = sample_card_corpus.get_multi_card_sets()
        assert "rec_mcs_turkey_stuffing_feast" in multi_sets
        assert "rec_mcs_wedding_fruitcake_1942" in multi_sets

        turkey_cards = multi_sets["rec_mcs_turkey_stuffing_feast"]
        assert len(turkey_cards) == 3
        turkey_fps = [
            _create_test_fingerprint(
                c.card_id,
                role=CardRole.SEQUENCE_START if idx == 0 else CardRole.SEQUENCE_CONTINUATION,
                first_line=c.ground_truth_recipe.title if idx == 0 else f"Part {idx+1} directions",
            )
            for idx, c in enumerate(turkey_cards)
        ]

        prop_t1 = CorrelationProposal(
            proposal_id="prop_turkey_1",
            ordered_card_ids=[turkey_fps[0].card_id, turkey_fps[1].card_id],
            confidence=0.93,
        )
        prop_t2 = CorrelationProposal(
            proposal_id="prop_turkey_2",
            ordered_card_ids=[turkey_fps[1].card_id, turkey_fps[2].card_id],
            confidence=0.91,
        )

        res_turkey = optimizer.optimize_proposals(turkey_fps, [prop_t1, prop_t2])
        assert len(res_turkey.proposals) == 1
        assert res_turkey.proposals[0].ordered_card_ids == [c.card_id for c in turkey_cards]
        assert res_turkey.proposals[0].part_count == 3

        fruitcake_cards = multi_sets["rec_mcs_wedding_fruitcake_1942"]
        assert len(fruitcake_cards) == 4
        fruitcake_fps = [
            _create_test_fingerprint(
                c.card_id,
                role=CardRole.SEQUENCE_START if idx == 0 else CardRole.SEQUENCE_CONTINUATION,
            )
            for idx, c in enumerate(fruitcake_cards)
        ]

        fc_props = [
            CorrelationProposal(
                proposal_id=f"prop_fc_{i}",
                ordered_card_ids=[fruitcake_fps[i].card_id, fruitcake_fps[i + 1].card_id],
                confidence=0.95,
            )
            for i in range(3)
        ]

        res_fc = optimizer.optimize_proposals(fruitcake_fps, fc_props)
        assert len(res_fc.proposals) == 1
        assert res_fc.proposals[0].ordered_card_ids == [c.card_id for c in fruitcake_cards]
        assert res_fc.proposals[0].part_count == 4


# ============================================================================
# 2. ADVERSARIAL EDGE CASES & GRAPH TOPOLOGY
# ============================================================================

class TestAdversarialGraphEdgeCases:
    """Stress tests on directed cycle prevention, branching, and fragmented topologies."""

    @pytest.fixture
    def optimizer(self) -> PairingOptimizer:
        return PairingOptimizer(min_pair_confidence=0.50)

    def test_two_node_mutual_cycle_prevention(self, optimizer: PairingOptimizer):
        """When A -> B (conf 0.95) and B -> A (conf 0.90) exist, cycle is prevented and highest confidence edge wins."""
        fp_a = _create_test_fingerprint("card_A.jpg")
        fp_b = _create_test_fingerprint("card_B.jpg")

        prop_ab = CorrelationProposal(proposal_id="p1", ordered_card_ids=["card_A.jpg", "card_B.jpg"], confidence=0.95)
        prop_ba = CorrelationProposal(proposal_id="p2", ordered_card_ids=["card_B.jpg", "card_A.jpg"], confidence=0.90)

        result = optimizer.optimize_proposals([fp_a, fp_b], [prop_ab, prop_ba])
        assert len(result.proposals) == 1
        assert result.proposals[0].ordered_card_ids == ["card_A.jpg", "card_B.jpg"]
        assert len(result.unmatched_card_ids) == 0

    def test_three_node_directed_cycle_prevention(self, optimizer: PairingOptimizer):
        """When A -> B (0.95), B -> C (0.90), C -> A (0.85) exist, cycle is broken at weakest link."""
        fps = [_create_test_fingerprint(f"node_{c}.jpg") for c in ("A", "B", "C")]

        p_ab = CorrelationProposal(proposal_id="p1", ordered_card_ids=["node_A.jpg", "node_B.jpg"], confidence=0.95)
        p_bc = CorrelationProposal(proposal_id="p2", ordered_card_ids=["node_B.jpg", "node_C.jpg"], confidence=0.90)
        p_ca = CorrelationProposal(proposal_id="p3", ordered_card_ids=["node_C.jpg", "node_A.jpg"], confidence=0.85)

        result = optimizer.optimize_proposals(fps, [p_ab, p_bc, p_ca])
        assert len(result.proposals) == 1
        # Highest edges A -> B and B -> C should form [A, B, C]; C -> A rejected to prevent cycle
        assert result.proposals[0].ordered_card_ids == ["node_A.jpg", "node_B.jpg", "node_C.jpg"]
        assert result.proposals[0].part_count == 3
        assert len(result.unmatched_card_ids) == 0

    def test_self_loop_prevention(self, optimizer: PairingOptimizer):
        """Self loop A -> A is cleanly rejected."""
        fp_a = _create_test_fingerprint("card_A.jpg")
        p_aa = CorrelationProposal(proposal_id="p_self", ordered_card_ids=["card_A.jpg", "card_A.jpg"], confidence=0.99)

        result = optimizer.optimize_proposals([fp_a], [p_aa])
        # Self link skipped, card remains unmatched or standalone
        assert len(result.proposals) == 0
        assert "card_A.jpg" in result.unmatched_card_ids

    def test_branching_out_degree_conflict_resolution(self, optimizer: PairingOptimizer):
        """When A -> B (0.95) and A -> C (0.80) compete, A -> B is selected; C remains unmatched."""
        fp_a = _create_test_fingerprint("card_A.jpg")
        fp_b = _create_test_fingerprint("card_B.jpg")
        fp_c = _create_test_fingerprint("card_C.jpg")

        p_ab = CorrelationProposal(proposal_id="p1", ordered_card_ids=["card_A.jpg", "card_B.jpg"], confidence=0.95)
        p_ac = CorrelationProposal(proposal_id="p2", ordered_card_ids=["card_A.jpg", "card_C.jpg"], confidence=0.80)

        result = optimizer.optimize_proposals([fp_a, fp_b, fp_c], [p_ab, p_ac])
        assert len(result.proposals) == 1
        assert result.proposals[0].ordered_card_ids == ["card_A.jpg", "card_B.jpg"]
        assert "card_C.jpg" in result.unmatched_card_ids

    def test_branching_in_degree_conflict_resolution(self, optimizer: PairingOptimizer):
        """When A -> C (0.80) and B -> C (0.95) compete for incoming into C, B -> C is selected."""
        fp_a = _create_test_fingerprint("card_A.jpg")
        fp_b = _create_test_fingerprint("card_B.jpg")
        fp_c = _create_test_fingerprint("card_C.jpg")

        p_ac = CorrelationProposal(proposal_id="p1", ordered_card_ids=["card_A.jpg", "card_C.jpg"], confidence=0.80)
        p_bc = CorrelationProposal(proposal_id="p2", ordered_card_ids=["card_B.jpg", "card_C.jpg"], confidence=0.95)

        result = optimizer.optimize_proposals([fp_a, fp_b, fp_c], [p_ac, p_bc])
        assert len(result.proposals) == 1
        assert result.proposals[0].ordered_card_ids == ["card_B.jpg", "card_C.jpg"]
        assert "card_A.jpg" in result.unmatched_card_ids

    def test_missing_intermediate_card_creates_disjoint_subsequences(self, optimizer: PairingOptimizer):
        """When 4-card sequence is missing Part 2 (only A->B and D->E exist), engine creates 2 disjoint sequences [A,B] and [D,E]."""
        fps = [_create_test_fingerprint(f"c_{x}.jpg") for x in ("A", "B", "D", "E")]

        p_ab = CorrelationProposal(proposal_id="p1", ordered_card_ids=["c_A.jpg", "c_B.jpg"], confidence=0.92)
        p_de = CorrelationProposal(proposal_id="p2", ordered_card_ids=["c_D.jpg", "c_E.jpg"], confidence=0.91)

        result = optimizer.optimize_proposals(fps, [p_ab, p_de])
        assert len(result.proposals) == 2
        seq_cards = [tuple(p.ordered_card_ids) for p in result.proposals]
        assert ("c_A.jpg", "c_B.jpg") in seq_cards
        assert ("c_D.jpg", "c_E.jpg") in seq_cards
        assert len(result.unmatched_card_ids) == 0

    def test_interleaved_multi_recipe_deck(self, optimizer: PairingOptimizer):
        """Interleaved cards from two recipes and noise cards are cleanly separated into two distinct sequences."""
        fps = [
            _create_test_fingerprint("R1_1.jpg"),
            _create_test_fingerprint("R2_1.jpg"),
            _create_test_fingerprint("N1_noise.jpg", role=CardRole.UNKNOWN),
            _create_test_fingerprint("R1_2.jpg"),
            _create_test_fingerprint("S1_standalone.jpg", role=CardRole.STANDALONE),
            _create_test_fingerprint("R2_2.jpg"),
            _create_test_fingerprint("R1_3.jpg"),
            _create_test_fingerprint("R2_3.jpg"),
            _create_test_fingerprint("R2_4.jpg"),
        ]

        props = [
            CorrelationProposal(proposal_id="r1_12", ordered_card_ids=["R1_1.jpg", "R1_2.jpg"], confidence=0.90),
            CorrelationProposal(proposal_id="r1_23", ordered_card_ids=["R1_2.jpg", "R1_3.jpg"], confidence=0.88),
            CorrelationProposal(proposal_id="r2_12", ordered_card_ids=["R2_1.jpg", "R2_2.jpg"], confidence=0.95),
            CorrelationProposal(proposal_id="r2_23", ordered_card_ids=["R2_2.jpg", "R2_3.jpg"], confidence=0.94),
            CorrelationProposal(proposal_id="r2_34", ordered_card_ids=["R2_3.jpg", "R2_4.jpg"], confidence=0.92),
        ]

        result = optimizer.optimize_proposals(fps, props)
        assert len(result.proposals) == 3  # R1 (3 cards), R2 (4 cards), S1 (1 card)

        proposals_by_len = {p.part_count: p for p in result.proposals}
        assert 1 in proposals_by_len
        assert 3 in proposals_by_len
        assert 4 in proposals_by_len

        assert proposals_by_len[1].ordered_card_ids == ["S1_standalone.jpg"]
        assert proposals_by_len[3].ordered_card_ids == ["R1_1.jpg", "R1_2.jpg", "R1_3.jpg"]
        assert proposals_by_len[4].ordered_card_ids == ["R2_1.jpg", "R2_2.jpg", "R2_3.jpg", "R2_4.jpg"]

        assert "N1_noise.jpg" in result.unmatched_card_ids

    def test_low_confidence_and_rejected_proposals_pruned(self, optimizer: PairingOptimizer):
        """Proposals below min_pair_confidence (0.50) or with recommended_action='REJECT' are pruned."""
        fps = [_create_test_fingerprint(f"c_{i}.jpg") for i in range(1, 4)]

        p_low = CorrelationProposal(proposal_id="p_low", ordered_card_ids=["c_1.jpg", "c_2.jpg"], confidence=0.35)
        p_rej = CorrelationProposal(proposal_id="p_rej", ordered_card_ids=["c_2.jpg", "c_3.jpg"], confidence=0.90, recommended_action="REJECT")

        result = optimizer.optimize_proposals(fps, [p_low, p_rej])
        assert len(result.proposals) == 0
        assert len(result.unmatched_card_ids) == 3

    def test_diamond_graph_topology_resolution(self, optimizer: PairingOptimizer):
        """Diamond topology (A->B 0.95, A->C 0.85, B->D 0.90, C->D 0.96):
        Edges sorted: C->D (0.96), A->B (0.95), B->D (0.90), A->C (0.85).
        C->D accepted.
        A->B accepted.
        B->D rejected (D already has in-degree 1 from C).
        A->C rejected (A already has out-degree 1 to B).
        Yields two disjoint pairs: [A, B] and [C, D].
        """
        fps = [_create_test_fingerprint(f"dia_{x}.jpg") for x in ("A", "B", "C", "D")]

        p_cd = CorrelationProposal(proposal_id="p_cd", ordered_card_ids=["dia_C.jpg", "dia_D.jpg"], confidence=0.96)
        p_ab = CorrelationProposal(proposal_id="p_ab", ordered_card_ids=["dia_A.jpg", "dia_B.jpg"], confidence=0.95)
        p_bd = CorrelationProposal(proposal_id="p_bd", ordered_card_ids=["dia_B.jpg", "dia_D.jpg"], confidence=0.90)
        p_ac = CorrelationProposal(proposal_id="p_ac", ordered_card_ids=["dia_A.jpg", "dia_C.jpg"], confidence=0.85)

        result = optimizer.optimize_proposals(fps, [p_cd, p_ab, p_bd, p_ac])
        assert len(result.proposals) == 2
        seq_cards = [tuple(p.ordered_card_ids) for p in result.proposals]
        assert ("dia_A.jpg", "dia_B.jpg") in seq_cards
        assert ("dia_C.jpg", "dia_D.jpg") in seq_cards

    def test_mid_chain_cycle_prevention(self, optimizer: PairingOptimizer):
        """Chain A -> B -> C -> D with back-edge D -> B (conf=0.85).
        A->B (0.95), B->C (0.94), C->D (0.93), D->B (0.85).
        Back-edge D->B is rejected to prevent cycle, producing maximal chain [A, B, C, D].
        """
        fps = [_create_test_fingerprint(f"node_{x}.jpg") for x in ("A", "B", "C", "D")]

        p_ab = CorrelationProposal(proposal_id="p1", ordered_card_ids=["node_A.jpg", "node_B.jpg"], confidence=0.95)
        p_bc = CorrelationProposal(proposal_id="p2", ordered_card_ids=["node_B.jpg", "node_C.jpg"], confidence=0.94)
        p_cd = CorrelationProposal(proposal_id="p3", ordered_card_ids=["node_C.jpg", "node_D.jpg"], confidence=0.93)
        p_db = CorrelationProposal(proposal_id="p4", ordered_card_ids=["node_D.jpg", "node_B.jpg"], confidence=0.85)

        result = optimizer.optimize_proposals(fps, [p_ab, p_bc, p_cd, p_db])
        assert len(result.proposals) == 1
        assert result.proposals[0].ordered_card_ids == ["node_A.jpg", "node_B.jpg", "node_C.jpg", "node_D.jpg"]
        assert result.proposals[0].part_count == 4

    def test_permutation_invariance_of_sequence_assembly(self, optimizer: PairingOptimizer):
        """Random permutations of input fingerprints do not alter resulting sequence ordering."""
        correct_order = [f"page_{i:02d}.jpg" for i in range(1, 7)]
        fps = [_create_test_fingerprint(cid) for cid in correct_order]

        props = [
            CorrelationProposal(
                proposal_id=f"p_{i}",
                ordered_card_ids=[correct_order[i], correct_order[i + 1]],
                confidence=0.90 + (0.01 * i),
            )
            for i in range(len(correct_order) - 1)
        ]

        rng = random.Random(1337)
        for _ in range(10):
            shuffled_fps = list(fps)
            rng.shuffle(shuffled_fps)
            shuffled_props = list(props)
            rng.shuffle(shuffled_props)

            result = optimizer.optimize_proposals(shuffled_fps, shuffled_props)
            assert len(result.proposals) == 1
            assert result.proposals[0].ordered_card_ids == correct_order



# ============================================================================
# 3. SUBSTRATE HEURISTICS & SEQUENTIAL HEADERS
# ============================================================================

class TestSubstrateHeuristicsAndHeaders:
    """Stress tests on heuristic transition probabilities, Delta E colorimetry, and header detection."""

    def test_role_compatibility_matrix(self):
        """Verify role compatibility scoring across all valid and invalid transitions."""
        assert calculate_role_compatibility(CardRole.SEQUENCE_START, CardRole.SEQUENCE_CONTINUATION) == 1.00
        assert calculate_role_compatibility(CardRole.SEQUENCE_CONTINUATION, CardRole.SEQUENCE_CONTINUATION) == 0.90
        assert calculate_role_compatibility(CardRole.SEQUENCE_START, CardRole.UNKNOWN) == 0.80
        assert calculate_role_compatibility(CardRole.UNKNOWN, CardRole.SEQUENCE_CONTINUATION) == 0.80
        assert calculate_role_compatibility(CardRole.SEQUENCE_CONTINUATION, CardRole.UNKNOWN) == 0.70
        assert calculate_role_compatibility(CardRole.UNKNOWN, CardRole.UNKNOWN) == 0.50

        # Incompatible / Standalone
        assert calculate_role_compatibility(CardRole.STANDALONE, CardRole.SEQUENCE_CONTINUATION) == 0.00
        assert calculate_role_compatibility(CardRole.SEQUENCE_START, CardRole.STANDALONE) == 0.00
        assert calculate_role_compatibility(CardRole.SEQUENCE_CONTINUATION, CardRole.SEQUENCE_START) == 0.00
        assert calculate_role_compatibility(CardRole.SEQUENCE_START, CardRole.SEQUENCE_START) == 0.00

    def test_sequential_header_score_progression(self):
        """Verify sequential header scoring handles Part N, Card N, Page N, and reverse order."""
        # Forward Part 1 -> Part 2
        fp1 = _create_test_fingerprint("card1.jpg", first_line="Grandma's Pie - Part 1", last_line="(over to Part 2 ->)")
        fp2 = _create_test_fingerprint("card2.jpg", first_line="Part 2: Crust & Filling", last_line="Bake 40 mins")
        assert calculate_sequential_header_score(fp1, fp2) == 1.00

        # Forward Card 2 -> Card 3
        fp_c2 = _create_test_fingerprint("c2.jpg", first_line="Card 2 (cont)")
        fp_c3 = _create_test_fingerprint("c3.jpg", first_line="Card 3 directions")
        assert calculate_sequential_header_score(fp_c2, fp_c3) == 1.00

        # Inverted Part 2 -> Part 1
        assert calculate_sequential_header_score(fp2, fp1) <= 0.20

    def test_color_similarity_delta_e_boundary(self):
        """Substrate color Delta E calculation correctly scales in CIELAB space."""
        # Identical colors (Delta E = 0)
        c_cream = PaperColorInfo(paper_tone="AGED_CREAM", hex_approx="#F6ECC5", lab_l=92.0, lab_a=1.5, lab_b=14.0)
        assert calculate_color_similarity(c_cream, c_cream) == 1.00

        # Very close colors (Delta E <= 5.0)
        c_cream_close = PaperColorInfo(paper_tone="AGED_CREAM", hex_approx="#F5EBC4", lab_l=91.5, lab_a=1.4, lab_b=13.8)
        assert calculate_color_similarity(c_cream, c_cream_close) >= 0.95

        # Divergent colors (White vs Kraft Dark Brown)
        c_white = PaperColorInfo(paper_tone="BRIGHT_WHITE", hex_approx="#FFFFFF", lab_l=100.0, lab_a=0.0, lab_b=0.0)
        c_kraft = PaperColorInfo(paper_tone="KRAFT_BROWN", hex_approx="#5A3A1A", lab_l=30.0, lab_a=15.0, lab_b=25.0)
        assert calculate_color_similarity(c_white, c_kraft) == 0.00


# ============================================================================
# 4. STORAGE & REST API ROUND-TRIP SERIALIZATION
# ============================================================================

class TestStorageAndAPISerialization:
    """Verify SessionStore and API contracts preserve ordered_card_ids arrays."""

    @pytest.mark.asyncio
    async def test_session_store_ordered_card_ids_persistence(self, tmp_path: Path):
        """SessionStore persists and retrieves N-part proposal ordered_card_ids without data loss."""
        db_path = tmp_path / "test_session_store.db"
        store = SessionStore(db_path=db_path)

        session_id = "sess_npart_storage_test"
        proposals = [
            CorrelationProposal(
                proposal_id="prop_3card",
                ordered_card_ids=["turkey_p1.jpg", "turkey_p2.jpg", "turkey_p3.jpg"],
                part_count=3,
                transition_scores=[0.95, 0.92],
                heuristic_score=0.94,
                semantic_score=0.93,
                confidence=0.935,
                is_standalone=False,
                continuity_explanation="3-card Thanksgiving Turkey",
                recommended_action="SEQUENCE",
            ),
            CorrelationProposal(
                proposal_id="prop_1card",
                ordered_card_ids=["standalone_pecan_pie.jpg"],
                part_count=1,
                transition_scores=[],
                heuristic_score=0.0,
                semantic_score=0.0,
                confidence=0.99,
                is_standalone=True,
                continuity_explanation="Standalone Single Card",
                recommended_action="STANDALONE",
            ),
        ]

        store.save_proposals(session_id, proposals)
        loaded = store.get_proposals(session_id)

        assert len(loaded) == 2
        p3 = next(p for p in loaded if p.get("proposal_id") == "prop_3card")
        assert p3["ordered_card_ids"] == ["turkey_p1.jpg", "turkey_p2.jpg", "turkey_p3.jpg"]
        assert p3["part_count"] == 3
        assert p3["is_standalone"] is False

        p1 = next(p for p in loaded if p.get("proposal_id") == "prop_1card")
        assert p1["ordered_card_ids"] == ["standalone_pecan_pie.jpg"]
        assert p1["part_count"] == 1
        assert p1["is_standalone"] is True

    def test_card_sequence_approval_model_compatibility(self):
        """CardSequenceApproval / CardPairApproval validates ordered_card_ids, reverse_roles, and aliases."""
        approval = CardSequenceApproval(
            proposal_id="prop_4card",
            ordered_card_ids=["card1.jpg", "card2.jpg", "card3.jpg", "card4.jpg"],
            decision=HITLDecision.APPROVE,
        )
        assert approval.ordered_card_ids == ["card1.jpg", "card2.jpg", "card3.jpg", "card4.jpg"]
        # Legacy aliases
        assert approval.front_card_id == "card1.jpg"
        assert approval.back_card_id == "card2.jpg"
        assert approval.card_ids == ["card1.jpg", "card2.jpg", "card3.jpg", "card4.jpg"]

        # Alias CardPairApproval
        pair_app = CardPairApproval(
            proposal_id="prop_alias",
            ordered_card_ids=["c_a.jpg", "c_b.jpg"],
            decision=HITLDecision.APPROVE,
        )
        assert pair_app.ordered_card_ids == ["c_a.jpg", "c_b.jpg"]

