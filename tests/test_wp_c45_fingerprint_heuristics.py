"""Unit and regression tests for WP-C4 and WP-C5 remediation.

Tests:
- Offline pixel-only visual fingerprint extraction (no filename heuristics).
- Rename invariance: Identical pixels with different filenames yield identical fingerprints.
- Degraded mode marking when offline or on Gemini API failure.
- Purely text-driven sequential header scoring:
  - 'continued on back' vs neutral text.
  - Printed page/card/part markers in boundary text.
  - Card ID / filename changes never alter heuristic header scores.
- Ruling detection via projection profiles.
- Ink color classification via CIELAB clustering.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw

from recipeboxer.models.domain import (
    BoundaryText,
    CardRole,
    InkColor,
    InkType,
    PaperColorTone,
    PaperRulingType,
    VisualFingerprint,
)
from recipeboxer.vision.fingerprint import GeminiVisualExtractor
from recipeboxer.vision.heuristics import calculate_sequential_header_score


@pytest.fixture
def sample_cream_ruled_card(tmp_path: Path) -> Path:
    """Creates a sample aged cream card with horizontal ruling lines and blue ink strokes."""
    img_path = tmp_path / "source_card.png"
    img = Image.new("RGB", (600, 400), color=(246, 236, 205))
    draw = ImageDraw.Draw(img)

    # Draw regular horizontal ruling lines
    for y in range(60, 360, 25):
        draw.line([(30, y), (570, y)], fill=(160, 190, 220), width=1)

    # Draw dark blue text-like strokes
    for y in range(70, 250, 25):
        draw.line([(60, y), (500, y)], fill=(15, 30, 175), width=3)

    img.save(img_path)
    return img_path


@pytest.mark.asyncio
async def test_fingerprint_rename_invariance(sample_cream_ruled_card: Path, tmp_path: Path):
    """Verifies that identical pixels with radically different filenames produce identical fingerprints.

    Specifically tests 'rec_vhc_x_card1_front.jpg' vs 'IMG_0001.jpg'.
    Filename semantics must not influence role, ruling, ink, artifacts, or boundary text.
    """
    raw_bytes = sample_cream_ruled_card.read_bytes()

    path_semantic = tmp_path / "rec_vhc_x_card1_front.jpg"
    path_semantic.write_bytes(raw_bytes)

    path_opaque = tmp_path / "IMG_0001.jpg"
    path_opaque.write_bytes(raw_bytes)

    extractor = GeminiVisualExtractor(api_key=None)

    fp_semantic = await extractor.extract(path_semantic)
    fp_opaque = await extractor.extract(path_opaque)

    # Identifiers and paths should reflect input arguments
    assert fp_semantic.card_id == "rec_vhc_x_card1_front.jpg"
    assert fp_opaque.card_id == "IMG_0001.jpg"
    assert fp_semantic.image_path != fp_opaque.image_path
    assert fp_semantic.sha256_hash == fp_opaque.sha256_hash

    # All visual and domain attributes must be identical
    assert fp_semantic.role == fp_opaque.role == CardRole.UNKNOWN
    assert fp_semantic.role_confidence == fp_opaque.role_confidence == 0.0
    assert fp_semantic.degraded is True
    assert fp_opaque.degraded is True
    assert fp_semantic.degraded_reason == fp_opaque.degraded_reason

    # Paper color
    assert fp_semantic.paper_color.hex_approx == fp_opaque.paper_color.hex_approx
    assert fp_semantic.paper_color.lab_l == fp_opaque.paper_color.lab_l
    assert fp_semantic.paper_color.tone_enum == fp_opaque.paper_color.tone_enum

    # Ruling
    assert fp_semantic.ruling.has_ruling == fp_opaque.ruling.has_ruling
    assert fp_semantic.ruling.ruling_type == fp_opaque.ruling.ruling_type
    assert fp_semantic.ruling.line_spacing_px == fp_opaque.ruling.line_spacing_px

    # Ink
    assert fp_semantic.ink.ink_color == fp_opaque.ink.ink_color
    assert fp_semantic.ink.ink_type == fp_opaque.ink.ink_type

    # Artifacts and boundary text must be empty offline
    assert fp_semantic.artifacts == fp_opaque.artifacts == []
    assert fp_semantic.boundary_text.first_line == fp_opaque.boundary_text.first_line == ""
    assert fp_semantic.boundary_text.last_line == fp_opaque.boundary_text.last_line == ""
    assert fp_semantic.boundary_text.has_continuation_symbol is False
    assert fp_semantic.boundary_text.is_cut_off_mid_sentence is False


def test_header_score_continuation_vs_neutral_text():
    """Verifies that 'continued on back' boundary text scores higher than neutral text."""
    fp_cont = VisualFingerprint(
        card_id="card_alpha.jpg",
        image_path="/cards/card_alpha.jpg",
        sha256_hash="hash_a",
        role=CardRole.UNKNOWN,
        boundary_text=BoundaryText(
            first_line="Old-Fashioned Apple Crisp",
            last_line="continued on back",
        ),
    )

    fp_neutral_front = VisualFingerprint(
        card_id="card_alpha.jpg",
        image_path="/cards/card_alpha.jpg",
        sha256_hash="hash_a",
        role=CardRole.UNKNOWN,
        boundary_text=BoundaryText(
            first_line="Old-Fashioned Apple Crisp",
            last_line="Serve warm with fresh vanilla bean ice cream.",
        ),
    )

    fp_neutral_back = VisualFingerprint(
        card_id="card_beta.jpg",
        image_path="/cards/card_beta.jpg",
        sha256_hash="hash_b",
        role=CardRole.UNKNOWN,
        boundary_text=BoundaryText(
            first_line="Preheat oven to 375 degrees.",
            last_line="Bake for 35 minutes until bubbly.",
        ),
    )

    score_cont = calculate_sequential_header_score(fp_cont, fp_neutral_back)
    score_neutral = calculate_sequential_header_score(fp_neutral_front, fp_neutral_back)

    assert score_cont == 0.75
    assert score_neutral == 0.50
    assert score_cont > score_neutral


def test_card_id_changes_never_change_header_score():
    """Verifies that modifying card_id or filename never alters calculate_sequential_header_score."""
    fp1 = VisualFingerprint(
        card_id="card1_front.jpg",
        image_path="/cards/card1_front.jpg",
        sha256_hash="hash_1",
        role=CardRole.UNKNOWN,
        boundary_text=BoundaryText(
            first_line="Grandma's Rolls",
            last_line="(cont. on back)",
            has_continuation_symbol=True,
        ),
    )

    fp2 = VisualFingerprint(
        card_id="card1_back.jpg",
        image_path="/cards/card1_back.jpg",
        sha256_hash="hash_2",
        role=CardRole.UNKNOWN,
        boundary_text=BoundaryText(
            first_line="Directions continued: Knead dough for 10 minutes",
            last_line="Yield: 24 rolls.",
        ),
    )

    base_score = calculate_sequential_header_score(fp1, fp2)

    # Invert filenames to back -> front
    fp1_inverted = fp1.model_copy(update={"card_id": "card2_back.jpg"})
    fp2_inverted = fp2.model_copy(update={"card_id": "card1_front.jpg"})
    assert calculate_sequential_header_score(fp1_inverted, fp2_inverted) == base_score

    # Random opaque identifiers
    fp1_opaque = fp1.model_copy(update={"card_id": "7b3f9c01a2.png"})
    fp2_opaque = fp2.model_copy(update={"card_id": "99e8210ba4.png"})
    assert calculate_sequential_header_score(fp1_opaque, fp2_opaque) == base_score


def test_header_score_printed_markers():
    """Verifies that printed markers in text (Page 1 -> Page 2) advance score correctly."""
    fp_p1 = VisualFingerprint(
        card_id="img_a.jpg",
        image_path="/a.jpg",
        sha256_hash="h1",
        role=CardRole.UNKNOWN,
        boundary_text=BoundaryText(
            first_line="Roast Turkey - Page 1",
            last_line="Season turkey cavity with salt and pepper.",
        ),
    )

    fp_p2 = VisualFingerprint(
        card_id="img_b.jpg",
        image_path="/b.jpg",
        sha256_hash="h2",
        role=CardRole.UNKNOWN,
        boundary_text=BoundaryText(
            first_line="Page 2 - Stuffing and Roasting Directions",
            last_line="Roast at 325 F until internal temp reaches 165 F.",
        ),
    )

    # Page 1 -> Page 2 forward progression
    score_fwd = calculate_sequential_header_score(fp_p1, fp_p2)
    assert score_fwd == 1.00

    # Page 2 -> Page 1 reverse sequence
    score_rev = calculate_sequential_header_score(fp_p2, fp_p1)
    assert score_rev == 0.10


def test_header_score_empty_boundary_text():
    """Verifies that empty or None boundary text defaults to 0.50 neutral score."""
    fp1 = VisualFingerprint(
        card_id="card1.jpg",
        image_path="/1.jpg",
        sha256_hash="h1",
        role=CardRole.UNKNOWN,
        boundary_text=BoundaryText(),
    )
    fp2 = VisualFingerprint(
        card_id="card2.jpg",
        image_path="/2.jpg",
        sha256_hash="h2",
        role=CardRole.UNKNOWN,
        boundary_text=BoundaryText(),
    )

    assert calculate_sequential_header_score(fp1, fp2) == 0.50


@pytest.mark.asyncio
async def test_offline_extractor_sets_degraded_status(sample_cream_ruled_card: Path):
    """Verifies that offline extraction marks fingerprint as degraded with explanation."""
    extractor = GeminiVisualExtractor(api_key=None)
    fp = await extractor.extract(sample_cream_ruled_card)

    assert fp.degraded is True
    assert "offline" in fp.degraded_reason.lower()
    assert fp.role == CardRole.UNKNOWN
    assert fp.role_confidence == 0.0


@pytest.mark.asyncio
async def test_live_gemini_failure_fallback_sets_degraded(sample_cream_ruled_card: Path):
    """Verifies that Gemini API failure logs warning and falls back to degraded pixel fingerprint."""
    extractor = GeminiVisualExtractor(api_key="valid_looking_key_for_test")

    with patch.object(extractor, "_call_gemini_api", side_effect=RuntimeError("503 Service Unavailable")):
        fp = await extractor.extract(sample_cream_ruled_card)

    assert fp.degraded is True
    assert fp.degraded_reason is not None
    assert "Gemini API fingerprint call failed" in fp.degraded_reason
    assert "503 Service Unavailable" in fp.degraded_reason
    assert fp.role == CardRole.UNKNOWN


def test_ruling_detection_projection_profile(tmp_path: Path):
    """Verifies ruling line detection from projection profile variance."""
    # 1. Ruled card with blue horizontal lines
    ruled_path = tmp_path / "card_ruled.png"
    img_ruled = Image.new("RGB", (600, 400), color=(250, 250, 250))
    d = ImageDraw.Draw(img_ruled)
    for y in range(60, 360, 25):
        d.line([(20, y), (580, y)], fill=(160, 190, 220), width=1)
    img_ruled.save(ruled_path)

    # 2. Blank unruled card
    blank_path = tmp_path / "card_blank.png"
    img_blank = Image.new("RGB", (600, 400), color=(250, 250, 250))
    img_blank.save(blank_path)

    extractor = GeminiVisualExtractor(api_key=None)

    fp_ruled = extractor.extract_sync(ruled_path)
    assert fp_ruled.ruling.has_ruling is True
    assert fp_ruled.ruling.ruling_type == PaperRulingType.STANDARD_RULED_BLUE
    assert fp_ruled.ruling.line_spacing_px is not None
    assert 18.0 <= fp_ruled.ruling.line_spacing_px <= 32.0

    fp_blank = extractor.extract_sync(blank_path)
    assert fp_blank.ruling.has_ruling is False
    assert fp_blank.ruling.ruling_type == PaperRulingType.UNRULED_BLANK
    assert fp_blank.ruling.line_spacing_px is None


def test_ink_color_clustering(tmp_path: Path):
    """Verifies ink color detection via CIELAB clustering on handwritten strokes."""
    # 1. Blue ink card
    blue_path = tmp_path / "card_blue_ink.png"
    img_blue = Image.new("RGB", (400, 300), color=(250, 250, 250))
    d_blue = ImageDraw.Draw(img_blue)
    for y in range(50, 250, 20):
        d_blue.line([(40, y), (360, y)], fill=(10, 30, 185), width=3)
    img_blue.save(blue_path)

    # 2. Black ink card
    black_path = tmp_path / "card_black_ink.png"
    img_black = Image.new("RGB", (400, 300), color=(250, 250, 250))
    d_black = ImageDraw.Draw(img_black)
    for y in range(50, 250, 20):
        d_black.line([(40, y), (360, y)], fill=(15, 15, 15), width=3)
    img_black.save(black_path)

    # 3. Clean blank card
    clean_path = tmp_path / "card_no_ink.png"
    img_clean = Image.new("RGB", (400, 300), color=(250, 250, 250))
    img_clean.save(clean_path)

    extractor = GeminiVisualExtractor(api_key=None)

    fp_blue = extractor.extract_sync(blue_path)
    assert fp_blue.ink.ink_color == InkColor.BLUE
    assert fp_blue.ink.ink_type == InkType.BLUE_BALLPOINT

    fp_black = extractor.extract_sync(black_path)
    assert fp_black.ink.ink_color == InkColor.BLACK
    assert fp_black.ink.ink_type == InkType.BLACK_BALLPOINT

    fp_clean = extractor.extract_sync(clean_path)
    assert fp_clean.ink.ink_color == InkColor.UNKNOWN
    assert fp_clean.ink.ink_type == InkType.UNKNOWN
