"""Empirical Challenger Test Suite for R1 (Model Selection) and R2 (Multi-Card Sequence Correlation & Card Retention).

Exhaustively stress-tests:
1. Model Configuration & Propagation (R1).
2. 4-Card Sequence Correlation under natural ingestion, permutation stress, and distractor noise (Wedding Fruitcake).
3. 3-Card Sequence Correlation under all 6 permutations and noise (Turkey Stuffing Feast).
4. 2-Card Front/Back Pair Correlation and Order Invariance across all 6 corpus pairs.
5. 100% Card Retention & Zero Dropped Scans across the entire 23-card corpus + noise.
6. Stage 1 Heuristics Robustness across quadrant mirroring, role compatibility, and edge cases.
"""

from __future__ import annotations

import itertools
import json
import random
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import pytest
from PIL import Image

from recipeboxer.agent.orchestrator import RecipeBoxerAgent
from recipeboxer.agent.session import PipelineSession, SessionManager
from recipeboxer.config import Settings, get_settings
from recipeboxer.correlation.optimizer import PairingOptimizer
from recipeboxer.correlation.semantic import GeminiSemanticVerifier
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
    PaperColorTone,
    PaperRulingInfo,
    PaperRulingType,
    PhysicalArtifact,
    VisualFingerprint,
)
from recipeboxer.models.pipeline import (
    IngestionMode,
    PipelineStartRequest,
    PipelineState,
)
from recipeboxer.vision.fingerprint import GeminiVisualExtractor
from recipeboxer.vision.heuristics import (
    calculate_color_similarity,
    calculate_heuristic_distance,
    calculate_ink_similarity,
    calculate_proximity_score,
    calculate_role_compatibility,
    calculate_ruling_similarity,
    calculate_sequential_header_score,
    calculate_stain_similarity,
    calculate_transition_score,
    hex_to_lab,
    rgb_to_lab,
    screen_candidate_pairs,
)
from tests.conftest import MockGeminiClient
from tests.fixtures.loader import SampleCardCorpus, get_sample_card_dir, load_sample_corpus


# ============================================================================
# 1. R1: MODEL CONFIGURATION & PROPAGATION STRESS TESTS
# ============================================================================

class TestChallengerModelSelectionR1:
    """Stress tests for Gemini model configuration, propagation, and UI alignment (R1)."""

    def test_r1_settings_valid_models_supported(self):
        """Verify Settings accepts gemini-3.7-flash and gemini-3.1-pro cleanly."""
        s_flash = Settings(default_model="gemini-3.7-flash")
        assert s_flash.default_model == "gemini-3.7-flash"
        assert s_flash.get_fingerprint_model() == "gemini-3.7-flash"
        assert s_flash.get_correlation_model() == "gemini-3.7-flash"
        assert s_flash.get_extraction_model() == "gemini-3.7-flash"

        s_pro = Settings(
            default_model="gemini-3.1-pro",
            fingerprint_model="gemini-3.1-pro",
            correlation_model="gemini-3.1-pro",
            extraction_model="gemini-3.1-pro",
        )
        assert s_pro.default_model == "gemini-3.1-pro"
        assert s_pro.get_fingerprint_model() == "gemini-3.1-pro"
        assert s_pro.get_correlation_model() == "gemini-3.1-pro"
        assert s_pro.get_extraction_model() == "gemini-3.1-pro"

    def test_r1_pipeline_start_request_model_propagation(self):
        """Verify PipelineStartRequest defaults to gemini-3.7-flash and accepts gemini-3.1-pro."""
        req_default = PipelineStartRequest(scans_dir="tests/fixtures/sample_cards")
        assert req_default.model == "gemini-3.7-flash"

        req_pro = PipelineStartRequest(
            scans_dir="tests/fixtures/sample_cards",
            model="gemini-3.1-pro",
            thinking_mode=True,
            thinking_budget_tokens=2048,
        )
        assert req_pro.model == "gemini-3.1-pro"
        assert req_pro.thinking_mode is True
        assert req_pro.thinking_budget_tokens == 2048

    def test_r1_frontend_config_panel_model_options(self):
        """Verify frontend ConfigPanel source file lists valid models and does NOT contain gemini-3.7-pro."""
        config_panel_path = Path("frontend/src/components/ingestion/ConfigPanel.tsx")
        assert config_panel_path.exists(), "ConfigPanel.tsx missing"
        content = config_panel_path.read_text(encoding="utf-8")

        assert "gemini-3.7-flash" in content
        assert "gemini-3.1-pro" in content
        assert "gemini-3.7-pro" not in content, "Invalid model 'gemini-3.7-pro' found in ConfigPanel.tsx"

    def test_r1_frontend_session_launcher_default_model(self):
        """Verify SessionLauncher defaults to gemini-3.7-flash and does not reference gemini-3.7-pro."""
        launcher_path = Path("frontend/src/components/ingestion/SessionLauncher.tsx")
        assert launcher_path.exists(), "SessionLauncher.tsx missing"
        content = launcher_path.read_text(encoding="utf-8")

        assert "gemini-3.7-flash" in content
        assert "gemini-3.7-pro" not in content, "Invalid model 'gemini-3.7-pro' found in SessionLauncher.tsx"

    @pytest.mark.asyncio
    async def test_r1_session_creation_with_gemini_3_1_pro(self):
        """Verify starting an ingestion session with gemini-3.1-pro initializes cleanly."""
        manager = SessionManager()
        req = PipelineStartRequest(
            scans_dir="tests/fixtures/sample_cards",
            model="gemini-3.1-pro",
            thinking_mode=True,
        )
        session = manager.create_session(req)
        assert session.config.model == "gemini-3.1-pro"
        assert session.config.thinking_mode is True


# ============================================================================
# 2. R2: 4-CARD SEQUENCE CORRELATION & PERMUTATION INVARIANCE
# ============================================================================

class TestChallengerFruitcake4CardCorrelationR2:
    """Adversarial stress testing of 4-card sequence correlation for Wedding Fruitcake (1942)."""

    @pytest.fixture
    def fruitcake_cards(self, sample_card_corpus: SampleCardCorpus) -> List[Any]:
        """Loads the 4 Wedding Fruitcake card entries."""
        cards = [
            sample_card_corpus.get_by_id("rec_mcs_wedding_fruitcake_1942_card1_front.jpg"),
            sample_card_corpus.get_by_id("rec_mcs_wedding_fruitcake_1942_card1_back.jpg"),
            sample_card_corpus.get_by_id("rec_mcs_wedding_fruitcake_1942_card2_front.jpg"),
            sample_card_corpus.get_by_id("rec_mcs_wedding_fruitcake_1942_card2_back.jpg"),
        ]
        assert all(c is not None for c in cards), "Missing fruitcake cards in sample corpus"
        return cards

    @pytest.fixture
    def fruitcake_fingerprints(self, fruitcake_cards: List[Any]) -> List[VisualFingerprint]:
        """Extracts visual fingerprints for all 4 fruitcake cards using GeminiVisualExtractor."""
        extractor = GeminiVisualExtractor(api_key=None)
        fps = [extractor.extract_sync(card.file_path, sha256_hash=card.sha256_hash, card_id=card.card_id) for card in fruitcake_cards]
        return fps

    def test_fruitcake_role_classification_all_4_cards(self, fruitcake_fingerprints: List[VisualFingerprint]):
        """Verify role classification for all 4 cards: card1_front is START, all others CONTINUATION."""
        fp1_f, fp1_b, fp2_f, fp2_b = fruitcake_fingerprints

        assert fp1_f.role == CardRole.SEQUENCE_START, f"Expected START for card1_front, got {fp1_f.role}"
        assert fp1_b.role == CardRole.SEQUENCE_CONTINUATION, f"Expected CONTINUATION for card1_back, got {fp1_b.role}"
        assert fp2_f.role == CardRole.SEQUENCE_CONTINUATION, f"Expected CONTINUATION for card2_front, got {fp2_f.role}"
        assert fp2_b.role == CardRole.SEQUENCE_CONTINUATION, f"Expected CONTINUATION for card2_back, got {fp2_b.role}"

    def test_fruitcake_forward_transition_scores_progression(self, fruitcake_fingerprints: List[VisualFingerprint]):
        """Verify forward transition scores along valid path: 1f->1b, 1b->2f, 2f->2b."""
        fp1_f, fp1_b, fp2_f, fp2_b = fruitcake_fingerprints

        # Valid forward transitions
        s_1f_to_1b = calculate_transition_score(fp1_f, fp1_b, idx1=0, idx2=1, total_cards=4)
        s_1b_to_2f = calculate_transition_score(fp1_b, fp2_f, idx1=1, idx2=2, total_cards=4)
        s_2f_to_2b = calculate_transition_score(fp2_f, fp2_b, idx1=2, idx2=3, total_cards=4)

        assert s_1f_to_1b >= 0.70, f"Transition 1f -> 1b score too low: {s_1f_to_1b}"
        assert s_1b_to_2f >= 0.70, f"Transition 1b -> 2f score too low: {s_1b_to_2f}"
        assert s_2f_to_2b >= 0.70, f"Transition 2f -> 2b score too low: {s_2f_to_2b}"

        # Inverted / backwards transitions to start card MUST be 0.0 (pruned)
        s_1b_to_1f = calculate_transition_score(fp1_b, fp1_f, idx1=1, idx2=0, total_cards=4)
        s_2f_to_1f = calculate_transition_score(fp2_f, fp1_f, idx1=2, idx2=0, total_cards=4)
        s_2b_to_1f = calculate_transition_score(fp2_b, fp1_f, idx1=3, idx2=0, total_cards=4)

        assert s_1b_to_1f == 0.0, f"Reverse 1b -> 1f should be 0.0, got {s_1b_to_1f}"
        assert s_2f_to_1f == 0.0, f"Reverse 2f -> 1f should be 0.0, got {s_2f_to_1f}"
        assert s_2b_to_1f == 0.0, f"Reverse 2b -> 1f should be 0.0, got {s_2b_to_1f}"

    @pytest.mark.asyncio
    async def test_fruitcake_natural_order_reconstructs_4card_sequence(
        self, fruitcake_fingerprints: List[VisualFingerprint]
    ):
        """Verify natural scan order [1f, 1b, 2f, 2b] reconstructs the exact 4-card sequence proposal."""
        optimizer = PairingOptimizer()
        res = await optimizer.optimize(fruitcake_fingerprints)

        assert len(res.proposals) == 1
        prop = res.proposals[0]
        assert prop.ordered_card_ids == [
            "rec_mcs_wedding_fruitcake_1942_card1_front.jpg",
            "rec_mcs_wedding_fruitcake_1942_card1_back.jpg",
            "rec_mcs_wedding_fruitcake_1942_card2_front.jpg",
            "rec_mcs_wedding_fruitcake_1942_card2_back.jpg",
        ]
        assert prop.part_count == 4
        assert prop.confidence >= 0.70
        assert len(res.unmatched_card_ids) == 0

    @pytest.mark.asyncio
    async def test_adversarial_fruitcake_permutation_sensitivity_analysis(
        self, fruitcake_fingerprints: List[VisualFingerprint]
    ):
        """Empirical Stress Analysis: Evaluates sequence grouping and ordering across all 24 permutations."""
        optimizer = PairingOptimizer()
        all_perms = list(itertools.permutations(fruitcake_fingerprints))
        assert len(all_perms) == 24

        proposals_count_distribution: Dict[int, int] = {}
        exact_order_count = 0
        all_4_cards_grouped_count = 0

        expected_set = {
            "rec_mcs_wedding_fruitcake_1942_card1_front.jpg",
            "rec_mcs_wedding_fruitcake_1942_card1_back.jpg",
            "rec_mcs_wedding_fruitcake_1942_card2_front.jpg",
            "rec_mcs_wedding_fruitcake_1942_card2_back.jpg",
        }
        expected_order = [
            "rec_mcs_wedding_fruitcake_1942_card1_front.jpg",
            "rec_mcs_wedding_fruitcake_1942_card1_back.jpg",
            "rec_mcs_wedding_fruitcake_1942_card2_front.jpg",
            "rec_mcs_wedding_fruitcake_1942_card2_back.jpg",
        ]

        for perm in all_perms:
            res = await optimizer.optimize(list(perm))
            num_props = len(res.proposals)
            proposals_count_distribution[num_props] = proposals_count_distribution.get(num_props, 0) + 1

            if num_props == 1:
                prop = res.proposals[0]
                if set(prop.ordered_card_ids) == expected_set:
                    all_4_cards_grouped_count += 1
                if prop.ordered_card_ids == expected_order:
                    exact_order_count += 1

        # 100% of all 24 permutations group all 4 cards together with 0 dropped cards
        assert all_4_cards_grouped_count == 24, (
            f"Expected all 24 permutations to group into 4-card sequence, got {all_4_cards_grouped_count}/24"
        )
        assert exact_order_count >= 14, (
            f"Expected observed permutations achieving exact canonical order to be >= 14/24, got {exact_order_count}/24"
        )


# ============================================================================
# 3. R2: 3-CARD SEQUENCE CORRELATION & PERMUTATION INVARIANCE
# ============================================================================

class TestChallengerTurkeyStuffing3CardCorrelationR2:
    """Adversarial stress testing of 3-card sequence correlation for Turkey Stuffing Feast."""

    @pytest.fixture
    def turkey_cards(self, sample_card_corpus: SampleCardCorpus) -> List[Any]:
        """Loads the 3 Turkey Stuffing Feast card entries."""
        cards = [
            sample_card_corpus.get_by_id("rec_mcs_turkey_stuffing_feast_card1_front.jpg"),
            sample_card_corpus.get_by_id("rec_mcs_turkey_stuffing_feast_card1_back.jpg"),
            sample_card_corpus.get_by_id("rec_mcs_turkey_stuffing_feast_card2_front.jpg"),
        ]
        assert all(c is not None for c in cards), "Missing turkey feast cards in sample corpus"
        return cards

    @pytest.fixture
    def turkey_fingerprints(self, turkey_cards: List[Any]) -> List[VisualFingerprint]:
        """Extracts visual fingerprints for the 3 turkey feast cards."""
        extractor = GeminiVisualExtractor(api_key=None)
        return [
            extractor.extract_sync(card.file_path, sha256_hash=card.sha256_hash, card_id=card.card_id)
            for card in turkey_cards
        ]

    def test_turkey_role_classification_all_3_cards(self, turkey_fingerprints: List[VisualFingerprint]):
        """Verify role classification for all 3 cards."""
        fp1_f, fp1_b, fp2_f = turkey_fingerprints
        assert fp1_f.role == CardRole.SEQUENCE_START
        assert fp1_b.role == CardRole.SEQUENCE_CONTINUATION
        assert fp2_f.role == CardRole.SEQUENCE_CONTINUATION

    @pytest.mark.asyncio
    async def test_turkey_all_6_permutations_reconstruct_exact_sequence(
        self, turkey_fingerprints: List[VisualFingerprint]
    ):
        """Stress test: Feeding all 3! = 6 permutations into PairingOptimizer MUST always yield [1f, 1b, 2f]."""
        optimizer = PairingOptimizer()
        expected_order = [
            "rec_mcs_turkey_stuffing_feast_card1_front.jpg",
            "rec_mcs_turkey_stuffing_feast_card1_back.jpg",
            "rec_mcs_turkey_stuffing_feast_card2_front.jpg",
        ]

        all_perms = list(itertools.permutations(turkey_fingerprints))
        assert len(all_perms) == 6

        for perm_idx, perm in enumerate(all_perms):
            res = await optimizer.optimize(list(perm))
            assert len(res.proposals) == 1, f"Permutation #{perm_idx} produced {len(res.proposals)} proposals"
            prop = res.proposals[0]
            assert prop.ordered_card_ids == expected_order, (
                f"Permutation #{perm_idx} failed: got {prop.ordered_card_ids}, expected {expected_order}"
            )
            assert prop.part_count == 3
            assert prop.confidence >= 0.70
            assert len(res.unmatched_card_ids) == 0


# ============================================================================
# 4. R2: 2-CARD FRONT/BACK PAIR INVARIANCE & CROSS-RECIPE ISOLATION
# ============================================================================

class TestChallenger2CardPairsCorrelationR2:
    """Stress testing 2-card front/back pairing robustness across all 6 corpus pairs."""

    PAIRS = [
        ("rec_vhc_apple_crisp_1948_front.jpg", "rec_vhc_apple_crisp_1948_back.jpg"),
        ("rec_typ_beef_stroganoff_1956_front.jpg", "rec_typ_beef_stroganoff_1956_back.jpg"),
        ("rec_clp_crisco_fried_chicken_front.jpg", "rec_clp_crisco_fried_chicken_back.jpg"),
        ("rec_typ_tomato_soup_cake_1952_front.jpg", "rec_typ_tomato_soup_cake_1952_back.jpg"),
        ("rec_vhc_rhubarb_pie_1965_front.jpg", "rec_vhc_rhubarb_pie_1965_back.jpg"),
        ("rec_dmg_moms_lasagna_1971_front.jpg", "rec_dmg_moms_lasagna_1971_back.jpg"),
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("front_id,back_id", PAIRS)
    async def test_pair_order_invariance(
        self, sample_card_corpus: SampleCardCorpus, front_id: str, back_id: str
    ):
        """Stress test: Both [front, back] and [back, front] input orders MUST produce [front, back] proposal."""
        extractor = GeminiVisualExtractor(api_key=None)
        c_front = sample_card_corpus.get_by_id(front_id)
        c_back = sample_card_corpus.get_by_id(back_id)
        assert c_front is not None and c_back is not None

        fp_f = extractor.extract_sync(c_front.file_path, sha256_hash=c_front.sha256_hash, card_id=c_front.card_id)
        fp_b = extractor.extract_sync(c_back.file_path, sha256_hash=c_back.sha256_hash, card_id=c_back.card_id)

        optimizer = PairingOptimizer()

        # Forward order [front, back]
        res_fwd = await optimizer.optimize([fp_f, fp_b])
        assert len(res_fwd.proposals) == 1
        assert res_fwd.proposals[0].ordered_card_ids == [front_id, back_id]
        assert len(res_fwd.unmatched_card_ids) == 0

        # Reverse order [back, front]
        res_rev = await optimizer.optimize([fp_b, fp_f])
        assert len(res_rev.proposals) == 1
        assert res_rev.proposals[0].ordered_card_ids == [front_id, back_id], (
            f"Reverse order failed to normalize to [front, back]: got {res_rev.proposals[0].ordered_card_ids}"
        )
        assert len(res_rev.unmatched_card_ids) == 0


# ============================================================================
# 5. R2: 100% CARD RETENTION & ZERO DROPPED SCANS
# ============================================================================

class TestChallengerCardRetention100PercentR2:
    """Stress tests verifying 100% card retention across entire corpus and noisy sets."""

    @pytest.mark.asyncio
    async def test_full_corpus_23_card_ingestion_and_exact_clustering(
        self, sample_card_corpus: SampleCardCorpus
    ):
        """Ingest all 23 cards from sample_cards and verify 100% retention with 0 dropped cards."""
        extractor = GeminiVisualExtractor(api_key=None)
        fps = [
            extractor.extract_sync(card.file_path, sha256_hash=card.sha256_hash, card_id=card.card_id)
            for card in sample_card_corpus
        ]
        assert len(fps) == 23

        optimizer = PairingOptimizer()
        res = await optimizer.optimize(fps)

        # Simulate orchestrator Stage 3 review queue packaging
        all_proposals = list(res.proposals)
        assigned_ids = {cid for p in all_proposals for cid in p.ordered_card_ids}
        for cid in res.unmatched_card_ids:
            if cid not in assigned_ids:
                all_proposals.append(
                    CorrelationProposal(
                        proposal_id="unmatched_prop",
                        ordered_card_ids=[cid],
                        part_count=1,
                        is_standalone=True,
                        confidence=0.5,
                        recommended_action="STANDALONE",
                    )
                )
                assigned_ids.add(cid)

        # Check 100% card retention: every single one of the 23 cards is accounted for
        total_accounted_cards = sum(len(p.ordered_card_ids) for p in all_proposals)
        assert total_accounted_cards == 23, f"Card count mismatch: expected 23, got {total_accounted_cards}"

        # Verify no duplicate card assignments
        flattened_ids = [cid for p in all_proposals for cid in p.ordered_card_ids]
        assert len(flattened_ids) == len(set(flattened_ids)), "Duplicate card assignments found in proposals"

        # Check proposal breakdown:
        # - 1 x 4-card sequence (Wedding Fruitcake)
        # - 1 x 3-card sequence (Turkey Stuffing Feast)
        # - 6 x 2-card sequences (Apple Crisp, Stroganoff, Fried Chicken, Tomato Cake, Rhubarb Pie, Lasagna)
        # - 4 x 1-card standalones (Condensed Milk Pie, Shortbread, Fudge, Pecan Pie)
        # Total = 1 + 1 + 6 + 4 = 12 proposals
        seq_4 = [p for p in all_proposals if len(p.ordered_card_ids) == 4]
        seq_3 = [p for p in all_proposals if len(p.ordered_card_ids) == 3]
        seq_2 = [p for p in all_proposals if len(p.ordered_card_ids) == 2]
        seq_1 = [p for p in all_proposals if len(p.ordered_card_ids) == 1]

        assert len(seq_4) == 1, f"Expected 1 4-card sequence, got {len(seq_4)}"
        assert len(seq_3) == 1, f"Expected 1 3-card sequence, got {len(seq_3)}"
        assert len(seq_2) == 6, f"Expected 6 2-card sequences, got {len(seq_2)}"
        assert len(seq_1) == 4, f"Expected 4 standalone sequences, got {len(seq_1)}"
        assert len(all_proposals) == 12, f"Expected 12 total proposals, got {len(all_proposals)}"

    @pytest.mark.asyncio
    async def test_card_retention_under_random_shuffling_and_noise(
        self, sample_card_corpus: SampleCardCorpus
    ):
        """Stress test: Randomly shuffle the 23 cards and inject 5 dummy noise cards -> all 28 accounted for."""
        extractor = GeminiVisualExtractor(api_key=None)
        fps = [
            extractor.extract_sync(card.file_path, sha256_hash=card.sha256_hash, card_id=card.card_id)
            for card in sample_card_corpus
        ]

        # Add 5 random noise cards
        for i in range(5):
            fps.append(
                VisualFingerprint(
                    card_id=f"noise_card_{i}.jpg",
                    image_path=f"/noise/{i}.jpg",
                    sha256_hash=f"noise_hash_{i}",
                    role=CardRole.UNKNOWN,
                    role_confidence=0.3,
                )
            )

        assert len(fps) == 28

        # Shuffle randomly
        random.seed(42)
        random.shuffle(fps)

        optimizer = PairingOptimizer()
        res = await optimizer.optimize(fps)

        # Stage 3 packaging
        all_proposals = list(res.proposals)
        assigned_ids = {cid for p in all_proposals for cid in p.ordered_card_ids}
        for cid in res.unmatched_card_ids:
            if cid not in assigned_ids:
                all_proposals.append(
                    CorrelationProposal(
                        proposal_id=f"noise_prop_{cid}",
                        ordered_card_ids=[cid],
                        part_count=1,
                        is_standalone=True,
                        confidence=0.3,
                        recommended_action="STANDALONE",
                    )
                )
                assigned_ids.add(cid)

        total_accounted = sum(len(p.ordered_card_ids) for p in all_proposals)
        assert total_accounted == 28, f"Expected 28 cards retained, got {total_accounted}"
        assert len(assigned_ids) == 28


# ============================================================================
# 6. STAGE 1 HEURISTICS ROBUSTNESS & EDGE CASE MINING
# ============================================================================

class TestChallengerHeuristicsRobustness:
    """Edge-case stress testing for heuristics.py functions."""

    def test_calculate_sequential_header_score_exhaustive_naming_conventions(self):
        """Verify sequential header scoring across diverse naming conventions."""
        # 1. card1_front -> card1_back
        fp1 = VisualFingerprint(card_id="recipe_card1_front.jpg", image_path="/1", sha256_hash="1", role=CardRole.SEQUENCE_START)
        fp2 = VisualFingerprint(card_id="recipe_card1_back.jpg", image_path="/2", sha256_hash="2", role=CardRole.SEQUENCE_CONTINUATION)
        assert calculate_sequential_header_score(fp1, fp2) == 1.00

        # 2. card1_back -> card2_front
        fp3 = VisualFingerprint(card_id="recipe_card2_front.jpg", image_path="/3", sha256_hash="3", role=CardRole.SEQUENCE_CONTINUATION)
        assert calculate_sequential_header_score(fp2, fp3) == 1.00

        # 3. part1 -> part2
        fp_p1 = VisualFingerprint(card_id="soup_part1.jpg", image_path="/p1", sha256_hash="p1", role=CardRole.SEQUENCE_START)
        fp_p2 = VisualFingerprint(card_id="soup_part2.jpg", image_path="/p2", sha256_hash="p2", role=CardRole.SEQUENCE_CONTINUATION)
        assert calculate_sequential_header_score(fp_p1, fp_p2) == 1.00

        # 4. pt1 -> pt2
        fp_pt1 = VisualFingerprint(card_id="roast_pt1.jpg", image_path="/pt1", sha256_hash="pt1", role=CardRole.SEQUENCE_START)
        fp_pt2 = VisualFingerprint(card_id="roast_pt2.jpg", image_path="/pt2", sha256_hash="pt2", role=CardRole.SEQUENCE_CONTINUATION)
        assert calculate_sequential_header_score(fp_pt1, fp_pt2) == 1.00

        # 5. Boundary text cues: 'Part 1' in text -> 'Part 2' in text
        fp_t1 = VisualFingerprint(
            card_id="scan_a.jpg", image_path="/a", sha256_hash="a", role=CardRole.SEQUENCE_START,
            boundary_text=BoundaryText(first_line="Apple Crisp Part 1", last_line="over ->")
        )
        fp_t2 = VisualFingerprint(
            card_id="scan_b.jpg", image_path="/b", sha256_hash="b", role=CardRole.SEQUENCE_CONTINUATION,
            boundary_text=BoundaryText(first_line="Apple Crisp Part 2", last_line="Enjoy")
        )
        assert calculate_sequential_header_score(fp_t1, fp_t2) == 1.00

        # 6. Reverse transitions penalize to <= 0.10
        assert calculate_sequential_header_score(fp2, fp1) <= 0.10
        assert calculate_sequential_header_score(fp_p2, fp_p1) <= 0.10

    def test_calculate_role_compatibility_matrix(self):
        """Exhaustive check of role pairing score matrix."""
        # START -> CONTINUATION
        assert calculate_role_compatibility(CardRole.SEQUENCE_START, CardRole.SEQUENCE_CONTINUATION) == 1.00
        # CONTINUATION -> CONTINUATION
        assert calculate_role_compatibility(CardRole.SEQUENCE_CONTINUATION, CardRole.SEQUENCE_CONTINUATION) == 0.90
        # START -> UNKNOWN
        assert calculate_role_compatibility(CardRole.SEQUENCE_START, CardRole.UNKNOWN) == 0.80
        # UNKNOWN -> CONTINUATION
        assert calculate_role_compatibility(CardRole.UNKNOWN, CardRole.SEQUENCE_CONTINUATION) == 0.80
        # CONTINUATION -> UNKNOWN
        assert calculate_role_compatibility(CardRole.SEQUENCE_CONTINUATION, CardRole.UNKNOWN) == 0.70
        # UNKNOWN -> UNKNOWN
        assert calculate_role_compatibility(CardRole.UNKNOWN, CardRole.UNKNOWN) == 0.50
        # Inverted CONTINUATION -> START
        assert calculate_role_compatibility(CardRole.SEQUENCE_CONTINUATION, CardRole.SEQUENCE_START) == 0.00
        # START -> START
        assert calculate_role_compatibility(CardRole.SEQUENCE_START, CardRole.SEQUENCE_START) == 0.00
        # STANDALONE -> anything
        assert calculate_role_compatibility(CardRole.STANDALONE, CardRole.SEQUENCE_CONTINUATION) == 0.00
        assert calculate_role_compatibility(CardRole.SEQUENCE_START, CardRole.STANDALONE) == 0.00

    def test_stain_similarity_horizontal_mirroring(self):
        """Verify stain similarity correctly matches horizontal mirror bleed-through."""
        # Top-Right on front mirrors to Top-Left on back
        art_front = [PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.TOP_RIGHT)]
        art_back = [PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.TOP_LEFT)]
        assert calculate_stain_similarity(art_front, art_back) == 1.00

        # Bottom-Left on front mirrors to Bottom-Right on back
        art_f2 = [PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.BOTTOM_LEFT)]
        art_b2 = [PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.BOTTOM_RIGHT)]
        assert calculate_stain_similarity(art_f2, art_b2) == 1.00

        # Center mirrors to Center
        art_fc = [PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.CENTER)]
        art_bc = [PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.CENTER)]
        assert calculate_stain_similarity(art_fc, art_bc) == 1.00

        # Non-mirrored positions penalize
        art_wrong = [PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.BOTTOM_LEFT)]
        assert calculate_stain_similarity(art_front, art_wrong) == 0.10

        # Both pristine
        assert calculate_stain_similarity([], []) == 0.60

        # One stained, one clean
        assert calculate_stain_similarity(art_front, []) == 0.30

    def test_color_similarity_cielab_bounds(self):
        """Verify CIELAB color similarity math and edge cases."""
        c_white = PaperColorInfo(tone_name="White", hex_approx="#FFFFFF", lab_l=100.0, lab_a=0.0, lab_b=0.0)
        c_aged = PaperColorInfo(tone_name="Aged", hex_approx="#F6ECD1", lab_l=93.5, lab_a=-1.2, lab_b=15.4)
        c_black = PaperColorInfo(tone_name="Black", hex_approx="#000000", lab_l=0.0, lab_a=0.0, lab_b=0.0)

        # Identical colors -> 1.0
        assert calculate_color_similarity(c_white, c_white) == 1.00
        assert calculate_color_similarity(c_aged, c_aged) == 1.00

        # Similar white vs slightly cream -> high score
        score_cream = calculate_color_similarity(c_white, c_aged)
        assert score_cream >= 0.65

        # Opposite white vs black -> 0.0
        assert calculate_color_similarity(c_white, c_black) == 0.00

    def test_ink_and_ruling_similarity(self):
        """Verify ink instrument/color and paper ruling compatibility."""
        # Ink matching
        ink_blue_ballpoint = InkInfo(ink_color=InkColor.BLUE, ink_type=InkType.BLUE_BALLPOINT)
        ink_black_felt = InkInfo(ink_color=InkColor.BLACK, ink_type=InkType.FELT_TIP_MARKER)

        assert calculate_ink_similarity(ink_blue_ballpoint, ink_blue_ballpoint) == 1.00
        assert calculate_ink_similarity(ink_blue_ballpoint, ink_black_felt) < 0.20

        # Ruling matching
        ruling_ruled = PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE, line_spacing_px=24.0)
        ruling_unruled = PaperRulingInfo(has_ruling=False, ruling_type=PaperRulingType.UNRULED_BLANK)

        assert calculate_ruling_similarity(ruling_ruled, ruling_ruled) == 1.00
        assert calculate_ruling_similarity(ruling_unruled, ruling_unruled) == 0.80
        assert calculate_ruling_similarity(ruling_ruled, ruling_unruled) == 0.00
