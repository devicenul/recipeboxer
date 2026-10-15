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
import re
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import pytest
from PIL import Image

from recipeboxer.agent.orchestrator import RecipeBoxerAgent
from recipeboxer.agent.session import PipelineSession, SessionManager
from recipeboxer.ai import LLMClient, LLMRequest, LLMResponse
from recipeboxer.config import Settings, get_settings
from recipeboxer.correlation.optimizer import PairingOptimizer
from recipeboxer.correlation.semantic import GeminiSemanticVerifier, SetVerificationResponse
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
from tests.fixtures.loader import SampleCardCorpus, get_sample_card_dir, load_sample_corpus


# ============================================================================
# 1. R1: MODEL CONFIGURATION & PROPAGATION STRESS TESTS
# ============================================================================

class TestChallengerModelSelectionR1:
    """Stress tests for Gemini model configuration, propagation, and UI alignment (R1)."""

    def test_r1_settings_valid_models_supported(self):
        """Verify Settings accepts gemini-3.8-flash and gemini-3.1-pro cleanly."""
        s_flash = Settings(default_model="gemini-3.8-flash")
        assert s_flash.default_model == "gemini-3.8-flash"
        assert s_flash.get_fingerprint_model() == "gemini-3.8-flash"
        assert s_flash.get_correlation_model() == "gemini-3.8-flash"
        assert s_flash.get_extraction_model() == "gemini-3.8-flash"

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
        """Verify PipelineStartRequest defaults to gemini-3.8-flash and accepts gemini-3.1-pro."""
        req_default = PipelineStartRequest(scans_dir="tests/fixtures/sample_cards")
        assert req_default.model == "gemini-3.8-flash"

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

        assert "gemini-3.8-flash" in content
        assert "gemini-3.1-pro" in content
        assert "gemini-3.7-pro" not in content, "Invalid model 'gemini-3.7-pro' found in ConfigPanel.tsx"

    def test_r1_frontend_session_launcher_default_model(self):
        """Verify SessionLauncher defaults to gemini-3.8-flash and does not reference gemini-3.7-pro."""
        launcher_path = Path("frontend/src/components/ingestion/SessionLauncher.tsx")
        assert launcher_path.exists(), "SessionLauncher.tsx missing"
        content = launcher_path.read_text(encoding="utf-8")

        assert "gemini-3.8-flash" in content
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

class MockSequenceVerifierClient(LLMClient):
    """Mock semantic verifier client verifying sequential narrative continuity."""

    async def generate(self, request: LLMRequest) -> LLMResponse[Any]:
        text_parts = [p.text for p in request.parts if hasattr(p, "text")]
        prompt_text = "\n".join(text_parts).lower()

        anchor_section = ""
        candidate_section = ""
        if "candidate 1:" in prompt_text:
            parts = prompt_text.split("candidate 1:", 1)
            anchor_section = parts[0]
            candidate_section = parts[1]
        else:
            anchor_section = prompt_text

        is_seq = False
        if ("part 1" in anchor_section or "card1_front" in anchor_section) and ("part 2" in candidate_section or "card1_back" in candidate_section):
            is_seq = True
        elif ("part 2" in anchor_section or "card1_back" in anchor_section) and ("part 3" in candidate_section or "card2_front" in candidate_section):
            is_seq = True
        elif ("part 3" in anchor_section or "card2_front" in anchor_section) and ("part 4" in candidate_section or "card2_back" in candidate_section):
            is_seq = True
        elif ("see back" in anchor_section or "continued on back" in anchor_section) and ("cont" in candidate_section or "part 2" in candidate_section):
            is_seq = True

        if is_seq:
            resp = SetVerificationResponse(continuations=["Candidate 1"], confidence=0.98)
        else:
            resp = SetVerificationResponse(continuations=[], rejected=["Candidate 1"], confidence=0.10)

        return LLMResponse(
            model="mock-seq-verifier",
            parsed=resp,
        )


class MockPairVerifierClient(LLMClient):
    """Mock semantic verifier client verifying matching front/back card pairs."""

    async def generate(self, request: LLMRequest) -> LLMResponse[Any]:
        text_parts = [p.text for p in request.parts if hasattr(p, "text")]
        prompt_text = "\n".join(text_parts).lower()

        anchor_section = ""
        candidate_section = ""
        if "candidate 1:" in prompt_text:
            parts = prompt_text.split("candidate 1:", 1)
            anchor_section = parts[0]
            candidate_section = parts[1]
        else:
            anchor_section = prompt_text

        is_pair = False
        if ("see back" in anchor_section or "recipe title" in anchor_section) and ("cont" in candidate_section or "directions" in candidate_section):
            is_pair = True
        elif "front" in anchor_section and "back" in candidate_section and not ("noise" in anchor_section or "noise" in candidate_section):
            is_pair = True

        match_a = re.search(r"card_(\d+)", anchor_section)
        match_c = re.search(r"card_(\d+)", candidate_section)
        if match_a and match_c:
            num_a = int(match_a.group(1))
            num_c = int(match_c.group(1))
            if num_a % 2 == 0 and num_c == num_a + 1:
                is_pair = True

        if is_pair:
            resp = SetVerificationResponse(continuations=["Candidate 1"], confidence=0.95)
        else:
            resp = SetVerificationResponse(continuations=[], rejected=["Candidate 1"], confidence=0.10)

        return LLMResponse(
            model="mock-pair-verifier",
            parsed=resp,
        )


class TestChallengerFruitcake4CardCorrelationR2:
    """Adversarial stress testing of 4-card sequence correlation for Wedding Fruitcake (1942)."""

    @pytest.fixture
    def fruitcake_fingerprints(self) -> List[VisualFingerprint]:
        """Explicit visual fingerprints for the 4 fruitcake cards with roles, paper color, and boundary cues."""
        paper = PaperColorInfo(tone_name="Aged Cream", hex_approx="#F5E6D3", lab_l=90.0, lab_a=2.0, lab_b=15.0)
        ruling = PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE, line_spacing_px=24.0)
        ink = InkInfo(ink_type=InkType.FOUNTAIN_PEN, ink_color=InkColor.BLACK)
        return [
            VisualFingerprint(
                card_id="rec_mcs_wedding_fruitcake_1942_card1_front.jpg",
                image_path="/fruitcake/1f.jpg",
                sha256_hash="f1f",
                role=CardRole.SEQUENCE_START,
                paper_color=paper,
                ruling=ruling,
                ink=ink,
                boundary_text=BoundaryText(
                    first_line="Wedding Fruitcake 1942 Part 1",
                    last_line="continued on back ->",
                    has_continuation_symbol=True,
                ),
            ),
            VisualFingerprint(
                card_id="rec_mcs_wedding_fruitcake_1942_card1_back.jpg",
                image_path="/fruitcake/1b.jpg",
                sha256_hash="f1b",
                role=CardRole.SEQUENCE_CONTINUATION,
                paper_color=paper,
                ruling=ruling,
                ink=ink,
                boundary_text=BoundaryText(
                    first_line="Fruitcake Part 2 Cont.",
                    last_line="turn over to card 2 ->",
                    has_continuation_symbol=True,
                ),
            ),
            VisualFingerprint(
                card_id="rec_mcs_wedding_fruitcake_1942_card2_front.jpg",
                image_path="/fruitcake/2f.jpg",
                sha256_hash="f2f",
                role=CardRole.SEQUENCE_CONTINUATION,
                paper_color=paper,
                ruling=ruling,
                ink=ink,
                boundary_text=BoundaryText(
                    first_line="Wedding Fruitcake Part 3",
                    last_line="continued ->",
                    has_continuation_symbol=True,
                ),
            ),
            VisualFingerprint(
                card_id="rec_mcs_wedding_fruitcake_1942_card2_back.jpg",
                image_path="/fruitcake/2b.jpg",
                sha256_hash="f2b",
                role=CardRole.SEQUENCE_CONTINUATION,
                paper_color=paper,
                ruling=ruling,
                ink=ink,
                boundary_text=BoundaryText(
                    first_line="Directions Continued Part 4",
                    last_line="Bake 3 hours. Enjoy.",
                ),
            ),
        ]

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
        optimizer = PairingOptimizer(verifier=GeminiSemanticVerifier(client=MockSequenceVerifierClient()))
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
        optimizer = PairingOptimizer(verifier=GeminiSemanticVerifier(client=MockSequenceVerifierClient()))
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
    def turkey_fingerprints(self) -> List[VisualFingerprint]:
        """Explicit visual fingerprints for the 3 turkey feast cards."""
        paper = PaperColorInfo(tone_name="Kraft Brown", hex_approx="#D8C0A5", lab_l=78.0, lab_a=4.0, lab_b=18.0)
        ruling = PaperRulingInfo(has_ruling=False, ruling_type=PaperRulingType.UNRULED_BLANK)
        ink = InkInfo(ink_type=InkType.TYPEWRITER_RIBBON, ink_color=InkColor.BLACK)
        return [
            VisualFingerprint(
                card_id="rec_mcs_turkey_stuffing_feast_card1_front.jpg",
                image_path="/turkey/1f.jpg",
                sha256_hash="t1f",
                role=CardRole.SEQUENCE_START,
                paper_color=paper,
                ruling=ruling,
                ink=ink,
                boundary_text=BoundaryText(
                    first_line="Roast Turkey Stuffing Feast Part 1",
                    last_line="see back ->",
                    has_continuation_symbol=True,
                ),
            ),
            VisualFingerprint(
                card_id="rec_mcs_turkey_stuffing_feast_card1_back.jpg",
                image_path="/turkey/1b.jpg",
                sha256_hash="t1b",
                role=CardRole.SEQUENCE_CONTINUATION,
                paper_color=paper,
                ruling=ruling,
                ink=ink,
                boundary_text=BoundaryText(
                    first_line="Stuffing Directions Part 2 Cont.",
                    last_line="see card 2 ->",
                    has_continuation_symbol=True,
                ),
            ),
            VisualFingerprint(
                card_id="rec_mcs_turkey_stuffing_feast_card2_front.jpg",
                image_path="/turkey/2f.jpg",
                sha256_hash="t2f",
                role=CardRole.SEQUENCE_CONTINUATION,
                paper_color=paper,
                ruling=ruling,
                ink=ink,
                boundary_text=BoundaryText(
                    first_line="Turkey Feast Part 3",
                    last_line="Roast 4 hours until done.",
                ),
            ),
        ]

    @pytest.mark.asyncio
    async def test_turkey_all_6_permutations_reconstruct_exact_sequence(
        self, turkey_fingerprints: List[VisualFingerprint]
    ):
        """Stress test: Feeding all 3! = 6 permutations into PairingOptimizer MUST always yield [1f, 1b, 2f]."""
        optimizer = PairingOptimizer(verifier=GeminiSemanticVerifier(client=MockSequenceVerifierClient()))
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
    async def test_pair_order_invariance(self, front_id: str, back_id: str):
        """Stress test: Both [front, back] and [back, front] input orders MUST produce [front, back] proposal."""
        paper = PaperColorInfo(tone_name="Aged Cream", hex_approx="#F5E6D3", lab_l=90.0, lab_a=2.0, lab_b=15.0)
        ruling = PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE, line_spacing_px=24.0)
        ink = InkInfo(ink_type=InkType.BLUE_BALLPOINT, ink_color=InkColor.BLUE)

        fp_f = VisualFingerprint(
            card_id=front_id,
            image_path=f"/{front_id}",
            sha256_hash=f"hash_{front_id}",
            role=CardRole.SEQUENCE_START,
            paper_color=paper,
            ruling=ruling,
            ink=ink,
            boundary_text=BoundaryText(
                first_line=f"{front_id} Recipe Title",
                last_line="see back ->",
                has_continuation_symbol=True,
            ),
        )
        fp_b = VisualFingerprint(
            card_id=back_id,
            image_path=f"/{back_id}",
            sha256_hash=f"hash_{back_id}",
            role=CardRole.SEQUENCE_CONTINUATION,
            paper_color=paper,
            ruling=ruling,
            ink=ink,
            boundary_text=BoundaryText(
                first_line="Directions Cont.",
                last_line="Bake 30 minutes.",
            ),
        )

        optimizer = PairingOptimizer(verifier=GeminiSemanticVerifier(client=MockPairVerifierClient()))

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

    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_full_corpus_23_card_ingestion_and_exact_clustering(
        self, sample_card_corpus: SampleCardCorpus
    ):
        """Ingest all cards from sample_cards and verify 100% retention with 0 dropped cards."""
        extractor = GeminiVisualExtractor(api_key=None)
        fps = [
            extractor.extract_sync(card.file_path, sha256_hash=card.sha256_hash, card_id=card.card_id)
            for card in sample_card_corpus
        ]
        assert len(fps) in (23, 48)

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

        # Check 100% card retention: every single one of the cards is accounted for
        total_accounted_cards = sum(len(p.ordered_card_ids) for p in all_proposals)
        assert total_accounted_cards == len(fps), f"Card count mismatch: expected {len(fps)}, got {total_accounted_cards}"

        # Verify no duplicate card assignments
        flattened_ids = [cid for p in all_proposals for cid in p.ordered_card_ids]
        assert len(flattened_ids) == len(set(flattened_ids)), "Duplicate card assignments found in proposals"

        if len(fps) == 48:
            assert len(all_proposals) == 24, f"Expected 24 total proposals, got {len(all_proposals)}"
        else:
            assert len(all_proposals) == 12, f"Expected 12 total proposals, got {len(all_proposals)}"

    @pytest.mark.asyncio
    async def test_card_retention_under_random_shuffling_and_noise(self):
        """Stress test: Randomly shuffle all cards and inject 5 dummy noise cards -> all accounted for."""
        fps = []
        for i in range(20):
            fps.append(
                VisualFingerprint(
                    card_id=f"card_{i:02d}.jpg",
                    image_path=f"/cards/{i:02d}.jpg",
                    sha256_hash=f"hash_{i:02d}",
                    role=CardRole.SEQUENCE_START if i % 2 == 0 else CardRole.SEQUENCE_CONTINUATION,
                    paper_color=PaperColorInfo(tone_name="Aged", hex_approx="#F5E6D3", lab_l=90.0, lab_a=2.0, lab_b=15.0),
                )
            )

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

        expected_total = 25
        assert len(fps) == expected_total

        # Shuffle randomly
        random.seed(42)
        random.shuffle(fps)

        optimizer = PairingOptimizer(verifier=GeminiSemanticVerifier(client=MockPairVerifierClient()))
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
        assert total_accounted == expected_total, f"Expected {expected_total} cards retained, got {total_accounted}"
        assert len(assigned_ids) == expected_total


# ============================================================================
# 6. STAGE 1 HEURISTICS ROBUSTNESS & EDGE CASE MINING
# ============================================================================

class TestChallengerHeuristicsRobustness:
    """Edge-case stress testing for heuristics.py functions."""

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
