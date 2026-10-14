"""Comprehensive Adversarial Cross-Feature Challenge Suite (challenger_e2e).

Tests non-eval cross-feature interactions:
1. Gemini prompt sanitization and opaque ID generation against adversarial inputs.
2. Authentic clipping fixtures and T0 corpus integrity.
3. Resilience of ModalityClassifier to corrupted bytes, nonexistent paths, and blank images.
"""

from __future__ import annotations

from pathlib import Path
import pytest
from PIL import Image

from recipeboxer.ai.gemini import (
    OPAQUE_CARD_ID_PATTERN,
    sanitize_prompt_card_id,
    sanitize_prompt_text,
)
from recipeboxer.vision.router import (
    CardModality,
    ModalityClassifier,
)
from tests.fixtures.loader import SampleCardCorpus, get_manifest_path, get_sample_card_dir


# ============================================================================
# R1 & R4: PROMPT SANITIZATION & ADVERSARIAL INPUT RESILIENCE
# ============================================================================

class TestOAuthAndPlatformResilience:
    """Stress tests prompt sanitization against adversarial inputs and edge cases."""

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
# R3: CLIPPING FIXTURES INTEGRITY
# ============================================================================

class TestClippingFixturesAndMultiCardSequences:
    """Stress tests authentic clipping fixtures integrity."""

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


# ============================================================================
# R4: CORRUPTED INPUTS & MODALITY RESILIENCE
# ============================================================================

class TestResilienceToMaliciousAndCorruptedInputs:
    """Tests system resilience against corrupted files, truncated bytes, and non-image data."""

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
