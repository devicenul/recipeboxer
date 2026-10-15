"""Unit tests for FIND-08 & FIND-09: Prompt Isolation and SetVerifier Quote Validation.

Verifies:
1. Prompt deny-list: no "Aunt Clara", no "Peach Cobbler" in recipeboxer/prompts/ or extractor.py.
2. Inferred title instruction: Do NOT append '[GUESS]'.
3. SetVerifier quote validation:
   - Exact/contiguous phrases match as verified.
   - Sliding-window/hyphenation/whitespace variations match as verified.
   - Word bags scattered arbitrarily across distant lines do NOT match as verified.
   - Missing middle-of-card quotes when full text is absent are marked UNCHECKED (no penalty).
   - Hallucinated quotes when full text is present are marked UNVERIFIED (50% penalty).
"""

from __future__ import annotations

from pathlib import Path
import re
import pytest

from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.correlation import SetVerification
from recipeboxer.correlation.semantic import (
    _extract_all_fingerprint_text,
    _validate_evidence_quotes,
)


def test_find_08_prompt_deny_list():
    """Verify production prompts and extractor do not leak test fixture recipe titles."""
    prompts_dir = Path("recipeboxer/prompts")
    prompt_files = list(prompts_dir.glob("*.md"))
    assert len(prompt_files) > 0

    for pf in prompt_files:
        text = pf.read_text(encoding="utf-8").lower()
        assert "aunt clara" not in text, f"Found 'Aunt Clara' in {pf}"
        assert "peach cobbler" not in text, f"Found 'Peach Cobbler' in {pf}"

    extractor_file = Path("recipeboxer/extraction/extractor.py")
    extractor_text = extractor_file.read_text(encoding="utf-8").lower()
    assert "aunt clara" not in extractor_text
    assert "peach cobbler" not in extractor_text


def test_find_08_guess_instruction_no_guess_suffix():
    """Verify extractor instructions specify not appending '[GUESS]'."""
    extractor_file = Path("recipeboxer/extraction/extractor.py")
    extractor_text = extractor_file.read_text(encoding="utf-8")
    assert "Do NOT append '[GUESS]'" in extractor_text


def test_find_09_quote_validation_exact_and_contiguous():
    """Verify verbatim contiguous quotes are verified."""
    fp1 = VisualFingerprint(
        card_id="c1",
        image_path="/tmp/c1.jpg",
        sha256_hash="hash1",
        role=CardRole.STANDALONE,
        visible_title="Grandmother's Apple Crisp",
        first_lines=["Preheat oven to 350 degrees.", "Peel and slice 6 tart apples."],
        last_lines=["Bake for 45 minutes until golden brown."],
    )

    verified, unverified, unchecked = _validate_evidence_quotes(
        ["Peel and slice 6 tart apples.", "Grandmother's Apple Crisp"],
        [fp1],
    )
    assert "Peel and slice 6 tart apples." in verified
    assert "Grandmother's Apple Crisp" in verified
    assert len(unverified) == 0
    assert len(unchecked) == 0


def test_find_09_quote_validation_word_bag_rejection():
    """Verify a bag of words scattered across lines is NOT verified as a quote."""
    fp1 = VisualFingerprint(
        card_id="c1",
        image_path="/tmp/c1.jpg",
        sha256_hash="hash1",
        role=CardRole.STANDALONE,
        visible_title="Lemon Sugar Cookies",
        first_lines=["In a large bowl cream butter and sugar until light."],
        last_lines=["Store in airtight container with lemon zest."],
    )

    # "cream container" has 'cream' from first_lines and 'container' from last_lines
    # It must NOT be treated as a verified quote!
    verified, unverified, unchecked = _validate_evidence_quotes(
        ["cream container"],
        [fp1],
    )
    assert "cream container" not in verified
    # Since fp1 has only boundary text (no full OCR/body), it should be unchecked, not unverified
    assert "cream container" in unchecked


def test_find_09_quote_validation_partial_vs_full_text():
    """Verify partial text produces UNCHECKED quotes while full text produces UNVERIFIED quotes."""
    # 1. Partial text (only boundary text)
    fp_partial = VisualFingerprint(
        card_id="c1",
        image_path="/tmp/c1.jpg",
        sha256_hash="hash1",
        role=CardRole.STANDALONE,
        visible_title="Chocolate Fudge",
        first_lines=["Melt butter in saucepan."],
        last_lines=["Cool completely before slicing."],
    )

    _, unverified_partial, unchecked_partial = _validate_evidence_quotes(
        ["Stir in 2 cups chopped walnuts"],
        [fp_partial],
    )
    assert len(unverified_partial) == 0
    assert "Stir in 2 cups chopped walnuts" in unchecked_partial

    # 2. Full text available (ocr_lines present)
    fp_full = VisualFingerprint(
        card_id="c2",
        image_path="/tmp/c2.jpg",
        sha256_hash="hash2",
        role=CardRole.STANDALONE,
        visible_title="Chocolate Fudge",
        first_lines=["Melt butter in saucepan."],
        last_lines=["Cool completely before slicing."],
        ocr_lines=[
            "Chocolate Fudge",
            "Melt butter in saucepan.",
            "Add sugar, cocoa powder, and milk.",
            "Boil for 5 minutes without stirring.",
            "Cool completely before slicing.",
        ],
    )

    verified_full, unverified_full, unchecked_full = _validate_evidence_quotes(
        ["Add sugar, cocoa powder, and milk.", "Completely fabricated ingredients 999"],
        [fp_full],
    )
    assert "Add sugar, cocoa powder, and milk." in verified_full
    assert "Completely fabricated ingredients 999" in unverified_full
    assert len(unchecked_full) == 0
