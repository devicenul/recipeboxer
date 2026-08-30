"""Test Suite for Evaluation Filename Obfuscation & Model Anti-Cheating (Milestone 2 / R2).

Verifies:
1. Card corpus filename obfuscation across deterministic SHA-256, UUID4, and sequential strategies.
2. EXIF metadata stripping on sanitized scan images.
3. Bidirectional mapping and proposal translation back to ground-truth identifiers.
4. Zero lexical leaks in multimodal prompt construction for Gemini extractors and semantic verifiers.
5. End-to-end benchmark execution under active obfuscation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from recipeboxer.ai.gemini import (
    OPAQUE_CARD_ID_PATTERN,
    OPAQUE_SEQUENTIAL_PATTERN,
    sanitize_prompt_card_id,
    sanitize_prompt_text,
)
from recipeboxer.correlation.semantic import GeminiSemanticVerifier
from recipeboxer.evals.metrics import calculate_pairing_metrics
from recipeboxer.evals.obfuscation import (
    EvaluationObfuscator,
    ObfuscatedCard,
    ObfuscatedCardCorpus,
    ObfuscationStrategy,
    resolve_active_obfuscation_id,
    sanitize_image_exif,
)
from recipeboxer.evals.runner import BenchmarkReport, BenchmarkRunner
from recipeboxer.extraction.extractor import GeminiRecipeExtractor
from recipeboxer.models.correlation import CorrelationProposal, CorrelationResult
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.recipe import RecipeSchema
from tests.fixtures.loader import SampleCardCorpus, get_manifest_path, get_sample_card_dir


# ============================================================================
# 1. EXIF SANITIZATION TESTS
# ============================================================================

class TestExifSanitization:
    """Tests that EXIF metadata is completely stripped from sanitized evaluation images."""

    def test_sanitize_image_exif_strips_metadata(self, tmp_path: Path):
        source_img_path = tmp_path / "source_with_exif.jpg"
        sanitized_img_path = tmp_path / "sanitized.jpg"

        # Create an image with simulated EXIF metadata
        im = Image.new("RGB", (100, 100), color=(240, 230, 210))
        exif = im.getexif()
        exif[0x010E] = "Secret Recipe Title: Apple Crisp 1948"  # ImageDescription tag
        exif[0x0132] = "1948:10:15 12:00:00"  # DateTime tag
        im.save(source_img_path, "JPEG", exif=exif)

        # Verify source image contains EXIF
        with Image.open(source_img_path) as src_im:
            src_exif = src_im.getexif()
            assert 0x010E in src_exif or len(src_exif) > 0

        # Sanitize the image
        sanitize_image_exif(source_img_path, sanitized_img_path)

        # Verify sanitized image exists and has zero EXIF tags
        assert sanitized_img_path.exists()
        with Image.open(sanitized_img_path) as clean_im:
            clean_exif = clean_im.getexif()
            assert len(clean_exif) == 0
            # Dimensions and mode must be preserved
            assert clean_im.size == (100, 100)
            assert clean_im.mode == "RGB"


# ============================================================================
# 2. OBFUSCATED CORPUS STRATEGIES & MAPPINGS
# ============================================================================

class TestObfuscatedCardCorpus:
    """Tests ObfuscatedCardCorpus creation, strategy variants, and reverse mapping."""

    @pytest.fixture
    def corpus(self) -> SampleCardCorpus:
        return SampleCardCorpus(get_sample_card_dir(), get_manifest_path())

    def test_deterministic_sha256_strategy(self, corpus: SampleCardCorpus):
        with ObfuscatedCardCorpus(corpus.cards, strategy=ObfuscationStrategy.DETERMINISTIC_SHA256) as obf:
            assert len(obf.obfuscated_cards) == len(corpus.cards)
            for card in obf.obfuscated_cards:
                assert isinstance(card, ObfuscatedCard)
                assert OPAQUE_CARD_ID_PATTERN.match(card.obfuscated_id)
                assert card.file_path.exists()
                assert card.file_path.is_file()
                # Verify zero recipe names or dates in obfuscated identifier
                assert "rec_" not in card.obfuscated_id
                assert "crisp" not in card.obfuscated_id
                assert "front" not in card.obfuscated_id
                assert "back" not in card.obfuscated_id

                # Bidirectional mapping check
                assert obf.obfuscate_id(card.original_card_id) == card.obfuscated_id
                assert obf.resolve_id(card.obfuscated_id) == card.original_card_id

    def test_uuid4_strategy(self, corpus: SampleCardCorpus):
        with ObfuscatedCardCorpus(corpus.cards[:5], strategy=ObfuscationStrategy.UUID4) as obf:
            assert len(obf.obfuscated_cards) == 5
            for card in obf.obfuscated_cards:
                assert card.obfuscated_id.startswith("card_")
                assert obf.resolve_id(card.obfuscated_id) == card.original_card_id

    def test_sequential_strategy(self, corpus: SampleCardCorpus):
        with ObfuscatedCardCorpus(corpus.cards[:5], strategy=ObfuscationStrategy.SEQUENTIAL) as obf:
            assert len(obf.obfuscated_cards) == 5
            expected_ids = [f"card_{i:04d}.jpg" for i in range(1, 6)]
            actual_ids = [c.obfuscated_id for c in obf.obfuscated_cards]
            assert actual_ids == expected_ids
            for card in obf.obfuscated_cards:
                assert obf.resolve_id(card.obfuscated_id) == card.original_card_id

    def test_context_manager_cleanup(self, corpus: SampleCardCorpus):
        obf = ObfuscatedCardCorpus(corpus.cards[:3])
        temp_dir = obf.target_dir
        assert temp_dir.exists()

        with obf:
            assert temp_dir.exists()
            # Active registry should resolve during context
            test_obf_id = obf.obfuscated_cards[0].obfuscated_id
            assert resolve_active_obfuscation_id(test_obf_id) == obf.obfuscated_cards[0].original_card_id

        # After exit, temp directory and active registry should be cleaned up
        assert not temp_dir.exists()
        assert resolve_active_obfuscation_id(test_obf_id) == test_obf_id

    def test_map_proposals_to_ground_truth(self, corpus: SampleCardCorpus):
        with ObfuscatedCardCorpus(corpus.cards[:4]) as obf:
            c1 = obf.obfuscated_cards[0]
            c2 = obf.obfuscated_cards[1]

            proposal_with_opaque = CorrelationProposal(
                proposal_id="prop_test_001",
                ordered_card_ids=[c1.obfuscated_id, c2.obfuscated_id],
                part_count=2,
                confidence=0.98,
                heuristic_score=0.95,
                semantic_score=0.99,
                continuity_explanation="Strong narrative continuity",
                recommended_action="PAIR",
            )

            mapped_list = obf.map_proposals_to_ground_truth([proposal_with_opaque])
            assert len(mapped_list) == 1
            mapped = mapped_list[0]
            assert mapped.ordered_card_ids == [c1.original_card_id, c2.original_card_id]
            assert mapped.confidence == 0.98
            assert mapped.continuity_explanation == "Strong narrative continuity"

    def test_map_correlation_result(self, corpus: SampleCardCorpus):
        with ObfuscatedCardCorpus(corpus.cards[:4]) as obf:
            c1 = obf.obfuscated_cards[0]
            c2 = obf.obfuscated_cards[1]
            c3 = obf.obfuscated_cards[2]

            raw_res = CorrelationResult(
                proposals=[
                    CorrelationProposal(
                        proposal_id="prop_01",
                        ordered_card_ids=[c1.obfuscated_id, c2.obfuscated_id],
                        part_count=2,
                        confidence=0.95,
                    ),
                ],
                unmatched_card_ids=[c3.obfuscated_id],
            )

            mapped_res = obf.map_correlation_result(raw_res)
            assert mapped_res.proposals[0].ordered_card_ids == [c1.original_card_id, c2.original_card_id]
            assert mapped_res.unmatched_card_ids == [c3.original_card_id]


# ============================================================================
# 3. PROMPT SANITIZATION & ANTI-CHEATING TESTS
# ============================================================================

class TestPromptSanitization:
    """Tests that prompts contain zero lexical leaks of recipe titles, dates, or card roles."""

    def test_sanitize_prompt_card_id_replaces_raw_filenames(self):
        raw_names = [
            "rec_vhc_apple_crisp_1948_front.jpg",
            "rec_typ_beef_stroganoff_1956_back.jpg",
            "rec_clp_crisco_fried_chicken_front.jpg",
            "rec_dmg_moms_lasagna_1971_back.jpg",
        ]
        for raw in raw_names:
            sanitized = sanitize_prompt_card_id(raw)
            assert sanitized != raw
            assert "rec_" not in sanitized
            assert "apple_crisp" not in sanitized
            assert "1948" not in sanitized
            assert "stroganoff" not in sanitized
            assert "lasagna" not in sanitized
            assert OPAQUE_CARD_ID_PATTERN.match(sanitized)

    def test_sanitize_prompt_card_id_preserves_already_opaque_ids(self):
        opaque_names = [
            "card_a7b8c9d0.jpg",
            "card_12345678.png",
            "card_001.jpg",
            "card1.jpg",
            "card_front",
            "card_back",
        ]
        for op in opaque_names:
            assert sanitize_prompt_card_id(op) == op

    def test_sanitize_prompt_text_substitutes_all_rec_tokens(self):
        prompt_with_leaks = (
            "Transcribing recipe from rec_vhc_apple_crisp_1948_front.jpg and "
            "rec_vhc_apple_crisp_1948_back.jpg for archival records."
        )
        cleaned = sanitize_prompt_text(prompt_with_leaks)
        assert "rec_vhc_apple_crisp" not in cleaned
        assert "apple_crisp" not in cleaned
        assert "1948" not in cleaned
        assert "card_" in cleaned

    @pytest.mark.asyncio
    async def test_extractor_prompt_payload_has_zero_lexical_leaks(self):
        extractor = GeminiRecipeExtractor(api_key="test-key", model_name="gemini-3.7-flash")

        resolved_cards = [
            (b"fake_front_bytes", "/path/to/rec_vhc_apple_crisp_1948_front.jpg", "rec_vhc_apple_crisp_1948_front.jpg"),
            (b"fake_back_bytes", "/path/to/rec_vhc_apple_crisp_1948_back.jpg", "rec_vhc_apple_crisp_1948_back.jpg"),
        ]

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps({
                                    "title": "Apple Crisp",
                                    "ingredients": ["6 apples", "1 cup sugar"],
                                    "directions": ["Slice apples and bake."],
                                })
                            }
                        ]
                    }
                }
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            await extractor._call_gemini_extraction_api(resolved_cards=resolved_cards)

            assert mock_post.called
            call_json = mock_post.call_args.kwargs["json"]
            parts = call_json["contents"][0]["parts"]

            # Inspect all text parts sent to Gemini API
            for part in parts:
                if "text" in part:
                    text_content = part["text"]
                    assert "apple_crisp" not in text_content.lower()
                    assert "1948" not in text_content
                    assert "rec_vhc" not in text_content

    @pytest.mark.asyncio
    async def test_semantic_verifier_prompt_has_zero_lexical_leaks(self):
        verifier = GeminiSemanticVerifier(api_key="test-key", model_name="gemini-3.7-flash")

        fp1 = VisualFingerprint(
            card_id="rec_vhc_apple_crisp_1948_front.jpg",
            image_path="nonexistent_1.jpg",
            sha256_hash="eac7208bafef79eb02416e847979a611072d854a56e74a8d72f8c28821e530b7",
            role=CardRole.SEQUENCE_START,
        )
        fp2 = VisualFingerprint(
            card_id="rec_vhc_apple_crisp_1948_back.jpg",
            image_path="nonexistent_2.jpg",
            sha256_hash="43b26d6ee49257602325992850937a09c2a6bf2a2aa775c7bdf6e44b80b7c7ba",
            role=CardRole.SEQUENCE_CONTINUATION,
        )


        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps({
                                    "is_pair_match": True,
                                    "semantic_score": 0.95,
                                    "explanation": "Matching recipe directions.",
                                    "recommended_action": "PAIR",
                                })
                            }
                        ]
                    }
                }
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            await verifier._call_gemini_semantic_api(fp1, fp2, heuristic_score=0.90)

            assert mock_post.called
            call_json = mock_post.call_args.kwargs["json"]
            parts = call_json["contents"][0]["parts"]

            for part in parts:
                if "text" in part:
                    text_content = part["text"]
                    assert "apple_crisp" not in text_content.lower()
                    assert "1948" not in text_content
                    assert "rec_vhc" not in text_content


# ============================================================================
# 4. END-TO-END BENCHMARK INTEGRATION TESTS
# ============================================================================

class TestBenchmarkObfuscationIntegration:
    """Tests end-to-end benchmark execution with active obfuscation enabled."""

    @pytest.mark.asyncio
    async def test_benchmark_runner_under_obfuscation(self):
        runner = BenchmarkRunner(
            manifest_path=get_manifest_path(),
            corpus_dir=get_sample_card_dir(),
            stage="full",
            mock_judge=True,
            obfuscate_filenames=True,
        )

        report = await runner.run()
        assert isinstance(report, BenchmarkReport)
        assert report.obfuscation_enabled is True
        assert report.total_cards == 48
        assert report.total_recipes == 24

        # Verify pairing metrics evaluate accurately against ground truth
        assert report.pairing_metrics.f1_score >= 0.80
        assert report.pairing_metrics.precision >= 0.80
        assert report.pairing_metrics.recall >= 0.80

        # Verify markdown report mentions active anti-cheating obfuscation
        md = report.to_markdown()
        assert "Active (Anti-Cheating Enabled)" in md
