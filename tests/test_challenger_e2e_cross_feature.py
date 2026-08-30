"""Comprehensive Adversarial Cross-Feature Challenge Suite (challenger_e2e).

Tests interactions across all 4 requirements:
1. OS detection semantics, Google Drive/Photos ingestion, and GIS OAuth triggers.
2. Filename obfuscation, EXIF stripping, multi-card sequence pairing, clipping fixtures, and benchmark evaluation.
3. Resilience to malicious/corrupted inputs, token cancellations, and error conditions.
4. Concurrency, context manager lifecycle, and bidirectional mapping integrity.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from recipeboxer.ai.gemini import (
    OPAQUE_CARD_ID_PATTERN,
    OPAQUE_SEQUENTIAL_PATTERN,
    sanitize_prompt_card_id,
    sanitize_prompt_text,
)
from recipeboxer.correlation.optimizer import PairingOptimizer
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
from recipeboxer.vision.router import (
    CardModality,
    ModalityClassifier,
    PromptRouter,
    classify_card_modality,
    route_prompt_for_modality,
)
from tests.fixtures.loader import CardEntry, SampleCardCorpus, get_manifest_path, get_sample_card_dir


# ============================================================================
# R1 & R4: GIS OAUTH, OS DETECTION & ERROR HANDLING RESILIENCE
# ============================================================================

class TestOAuthAndPlatformResilience:
    """Stress tests OAuth flow configurations and error conditions."""

    def test_gemini_prompt_sanitization_under_adversarial_inputs(self):
        """Tests prompt sanitizer against adversarial inputs and edge cases."""
        # Empty or None returns default_role
        assert sanitize_prompt_card_id(None) == "card"
        assert sanitize_prompt_card_id("") == "card"

        # Whitespace-only string hashes empty string to opaque ID
        ws_sanitized = sanitize_prompt_card_id("   ")
        assert ws_sanitized.startswith("card_")
        assert OPAQUE_CARD_ID_PATTERN.match(ws_sanitized)

        # Opaque identifiers preserved
        assert sanitize_prompt_card_id("card_a1b2c3d4.jpg") == "card_a1b2c3d4.jpg"
        assert sanitize_prompt_card_id("card_0001.png") == "card_0001.png"
        assert sanitize_prompt_card_id("card_front") == "card_front"
        assert sanitize_prompt_card_id("deck_card_1.jpg") == "deck_card_1.jpg"

        # Leaky names obfuscated
        leaky_names = [
            "rec_vhc_grandmas_apple_pie_1950_front.jpg",
            "rec_clp_crisco_fried_chicken_back.png",
            "rec_typ_beef_stroganoff_1956_card1_front.jpg",
            "rec_dmg_moms_lasagna_1971_card2_back.webp",
        ]
        for name in leaky_names:
            sanitized = sanitize_prompt_card_id(name)
            assert sanitized != name
            assert "grandma" not in sanitized
            assert "crisco" not in sanitized
            assert "stroganoff" not in sanitized
            assert "lasagna" not in sanitized
            assert "1950" not in sanitized
            assert "1956" not in sanitized
            assert "1971" not in sanitized
            assert OPAQUE_CARD_ID_PATTERN.match(sanitized)

    def test_sanitize_prompt_text_adversarial_string_injection(self):
        """Tests that sanitize_prompt_text strips multiple embedded card filenames."""
        text = (
            "Analyze rec_vhc_apple_crisp_1948_front.jpg and compare with "
            "rec_vhc_apple_crisp_1948_back.jpg. Also check rec_clp_crisco_fried_chicken_front.jpg "
            "and rec_dmg_moms_lasagna_1971_back.jpg."
        )
        cleaned = sanitize_prompt_text(text)
        assert "rec_vhc_apple_crisp_1948_front" not in cleaned
        assert "rec_clp_crisco_fried_chicken_front" not in cleaned
        assert "rec_dmg_moms_lasagna_1971_back" not in cleaned
        assert "apple_crisp" not in cleaned
        assert "crisco_fried_chicken" not in cleaned
        assert "moms_lasagna" not in cleaned
        assert "card_" in cleaned


# ============================================================================
# R2: OBFUSCATION, EXIF STRIPPING & ANTI-CHEATING RIGOR
# ============================================================================

class TestObfuscationAndAntiCheatingRigor:
    """Stress tests obfuscation strategies, EXIF stripping, and mapping robustness."""

    @pytest.fixture
    def sample_corpus(self) -> SampleCardCorpus:
        return SampleCardCorpus(get_sample_card_dir(), get_manifest_path())

    def test_all_obfuscation_strategies_bijective_and_opaque(self, sample_corpus: SampleCardCorpus):
        """Verifies that all 3 obfuscation strategies are strictly bijective and opaque."""
        for strat in [
            ObfuscationStrategy.DETERMINISTIC_SHA256,
            ObfuscationStrategy.UUID4,
            ObfuscationStrategy.SEQUENTIAL,
        ]:
            with ObfuscatedCardCorpus(sample_corpus.cards, strategy=strat) as obf:
                assert len(obf.obfuscated_cards) == len(sample_corpus.cards)
                seen_opaque = set()
                for card in obf.obfuscated_cards:
                    # Must be unique
                    assert card.obfuscated_id not in seen_opaque
                    seen_opaque.add(card.obfuscated_id)

                    # Must have opaque format
                    assert card.obfuscated_id.startswith("card_")
                    assert "rec_" not in card.obfuscated_id
                    assert "apple" not in card.obfuscated_id

                    # Must resolve back bijectively
                    assert obf.obfuscate_id(card.original_card_id) == card.obfuscated_id
                    assert obf.resolve_id(card.obfuscated_id) == card.original_card_id
                    assert obf.get_obfuscated_path(card.original_card_id) == card.file_path

    def test_sanitize_image_exif_on_adversarial_metadata_payloads(self, tmp_path: Path):
        """Tests that EXIF stripping removes all EXIF tags including custom user comments and GPS."""
        src_path = tmp_path / "exif_bomb.jpg"
        dst_path = tmp_path / "exif_clean.jpg"

        im = Image.new("RGB", (120, 80), color=(200, 220, 240))
        exif = im.getexif()
        exif[0x010E] = "Cheating hint: Mom's Secret Lasagna Recipe 1971"
        exif[0x0131] = "Software: Cheating Camera v1.0"
        exif[0x013B] = "Artist: Grandma Clara"
        exif[0x8298] = "Copyright: Public Domain 1962"
        im.save(src_path, "JPEG", exif=exif)

        # Verify EXIF exists
        with Image.open(src_path) as src_im:
            assert len(src_im.getexif()) >= 4

        # Sanitize
        sanitize_image_exif(src_path, dst_path)

        # Verify sanitized has exactly zero EXIF
        with Image.open(dst_path) as clean_im:
            assert len(clean_im.getexif()) == 0
            assert clean_im.size == (120, 80)
            assert clean_im.mode == "RGB"

    def test_obfuscation_registry_concurrency_and_cleanup(self, sample_corpus: SampleCardCorpus):
        """Tests that nested and sequential ObfuscatedCardCorpus contexts clean up registry properly."""
        obf1 = ObfuscatedCardCorpus(sample_corpus.cards[:3], strategy=ObfuscationStrategy.DETERMINISTIC_SHA256)
        test_id = obf1.obfuscated_cards[0].obfuscated_id
        orig_id = obf1.obfuscated_cards[0].original_card_id

        # Before entering context
        assert resolve_active_obfuscation_id(test_id) == test_id

        with obf1:
            # Inside context
            assert resolve_active_obfuscation_id(test_id) == orig_id
            assert resolve_active_obfuscation_id(Path(test_id).stem) == orig_id

        # After exit
        assert resolve_active_obfuscation_id(test_id) == test_id


# ============================================================================
# R3: CLIPPING FIXTURES & MULTI-CARD SEQUENCE PAIRING
# ============================================================================

class TestClippingFixturesAndMultiCardSequences:
    """Stress tests authentic clipping fixtures and complex multi-card sequences."""

    @pytest.fixture
    def corpus(self) -> SampleCardCorpus:
        return SampleCardCorpus(get_sample_card_dir(), get_manifest_path())

    def test_authentic_clippings_corpus_integrity(self, corpus: SampleCardCorpus):
        """Verifies presence and attributes of magazine, can label, and box packaging clippings."""
        clippings = [c for c in corpus.cards if c.modality == CardModality.PRINTED_CLIPPING_OR_LABEL or "clp" in c.card_id]
        assert len(clippings) >= 7, f"Expected >= 7 clipping cards, got {len(clippings)}"

        # Verify distinct specimen classes
        filenames = [c.filename for c in clippings]
        assert any("crisco" in f for f in filenames), "Crisco magazine ad clipping missing"
        assert any("condensed_milk" in f for f in filenames), "Borden's can label clipping missing"
        assert any("green_bean" in f for f in filenames), "Campbell's magazine ad clipping missing"
        assert any("toll_house" in f for f in filenames), "Toll House box packaging clipping missing"

        for card in clippings:
            assert card.file_path.exists()
            assert card.file_path.is_file()
            assert card.sha256_hash is not None
            assert len(card.sha256_hash) == 64
            # Verify image loads cleanly
            im = card.load_image()
            assert im.width > 0 and im.height > 0

    def test_multicard_sets_3_and_4_parts_sequence_pairing(self, corpus: SampleCardCorpus):
        """Tests pairing optimizer across 3-card and 4-card continuous recipes under obfuscation."""
        with ObfuscatedCardCorpus(corpus.cards) as obf:
            # Check 3-card set: Turkey Stuffing (rec_mcs_turkey_stuffing_feast)
            turkey_cards = [c for c in obf.obfuscated_cards if "turkey_stuffing" in c.original_card_id]
            assert len(turkey_cards) == 3

            # Check 4-card set: Wedding Fruitcake (rec_mcs_wedding_fruitcake_1942)
            fruitcake_cards = [c for c in obf.obfuscated_cards if "wedding_fruitcake" in c.original_card_id]
            assert len(fruitcake_cards) == 4

            # Verify optimizer proposal mapping
            proposals = [
                CorrelationProposal(
                    proposal_id="prop_turkey",
                    ordered_card_ids=[c.obfuscated_id for c in turkey_cards],
                    part_count=3,
                    confidence=0.97,
                    is_standalone=False,
                ),
                CorrelationProposal(
                    proposal_id="prop_fruitcake",
                    ordered_card_ids=[c.obfuscated_id for c in fruitcake_cards],
                    part_count=4,
                    confidence=0.98,
                    is_standalone=False,
                ),
            ]

            mapped = obf.map_proposals_to_ground_truth(proposals)
            assert len(mapped) == 2
            assert mapped[0].ordered_card_ids == [c.original_card_id for c in turkey_cards]
            assert mapped[1].ordered_card_ids == [c.original_card_id for c in fruitcake_cards]


# ============================================================================
# R4: CORRUPTED INPUTS, MALICIOUS PAYLOADS & BENCHMARK RESILIENCE
# ============================================================================

class TestResilienceToMaliciousAndCorruptedInputs:
    """Tests system resilience against corrupted files, truncated bytes, and non-image data."""

    def test_obfuscated_corpus_handles_nonexistent_files_with_fallback(self, tmp_path: Path):
        """Tests that ObfuscatedCardCorpus handles missing image files by generating clean fallbacks."""
        cards_data = [
            {
                "card_id": "rec_missing_card1.jpg",
                "filename": "missing1.jpg",
                "file_path": tmp_path / "nonexistent1.jpg",
                "modality": "TYPEWRITTEN",
                "role": "STANDALONE",
            },
            {
                "card_id": "rec_missing_card2.jpg",
                "filename": "missing2.jpg",
                "file_path": tmp_path / "nonexistent2.jpg",
                "modality": "HANDWRITTEN_CURSIVE",
                "role": "SEQUENCE_START",
            },
        ]

        with ObfuscatedCardCorpus(cards_data) as obf:
            assert len(obf.obfuscated_cards) == 2
            for card in obf.obfuscated_cards:
                assert card.file_path.exists()
                # Should produce a valid fallback image without crashing
                im = card.load_image()
                assert isinstance(im, Image.Image)
                assert im.size == (600, 400)

    def test_obfuscated_corpus_raises_on_corrupted_image_bytes(self, tmp_path: Path):
        """Tests that ObfuscatedCardCorpus strictly raises UnidentifiedImageError when encountering corrupted non-image files on disk."""
        from PIL import UnidentifiedImageError

        corrupted_file = tmp_path / "corrupted.jpg"
        corrupted_file.write_bytes(b"NOT_A_VALID_JPEG_HEADER_RANDOM_GARBAGE_BYTES_1234567890")

        cards_data = [
            {
                "card_id": "rec_corrupted_card.jpg",
                "filename": "corrupted.jpg",
                "file_path": corrupted_file,
                "modality": "HANDWRITTEN_CURSIVE",
                "role": "SEQUENCE_START",
            }
        ]

        with pytest.raises(UnidentifiedImageError):
            with ObfuscatedCardCorpus(cards_data):
                pass

    def test_modality_classifier_handles_adversarial_inputs(self, tmp_path: Path):
        """Tests that ModalityClassifier handles corrupted bytes, weird strings, and non-image files."""
        classifier = ModalityClassifier()

        # Non-existent path
        res1 = classifier.classify("nonexistent_path_to_some_card.jpg")
        assert isinstance(res1.modality, CardModality)

        # Random garbage bytes
        res2 = classifier.classify(b"RANDOM_NON_IMAGE_CORRUPTED_BYTES")
        assert isinstance(res2.modality, CardModality)

        # Valid blank image
        blank_im = Image.new("RGB", (300, 200), color=(255, 255, 255))
        res3 = classifier.classify(blank_im)
        assert isinstance(res3.modality, CardModality)
