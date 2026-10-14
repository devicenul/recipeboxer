"""Comprehensive unit and contract tests for WP-X2 image preparation.

Validates WAVE 4 Contracts §1.8 and REMEDIATION_PLAN §8.3:
- Contract data types: PreparedFace schema, fields, and properties.
- EXIF orientation transposition.
- Card border auto-cropping.
- Auto-deskewing for small angular rotations.
- Long-edge capping per profile_params.get('image_long_edge', 1600).
- Content-based contrast evaluation (faded/low-contrast vs normal).
- Enhancement controls: enhance=None (auto), enhance=True, enhance=False.
- Exact label formats: 'Face i of N' and 'Face i of N (contrast-enhanced copy)'.
- Variant strings: 'standard' and 'enhanced_clahe'.
- Complete immunity to filename influence (never uses filename or modality guess from name).
- Bit-for-bit determinism across repeated runs.
- Correct MIME sniffing on encoded bytes (JPEG and PNG).
- Multi-face recipes with mixed contrast properties.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageOps
import pytest

from recipeboxer.ai.client import sniff_mime_type
from recipeboxer.extraction.image_prep import (
    PreparedFace,
    VARIANT_ENHANCED_CLAHE,
    VARIANT_STANDARD,
    apply_clahe,
    cap_long_edge,
    crop_card_borders,
    deskew_card_image,
    encode_image,
    is_faded_or_low_contrast,
    prepare_faces,
)


# ============================================================================
# Synthetic Image Generation Helpers (Pure Pillow / In-memory)
# ============================================================================

def make_normal_card_image(width: int = 600, height: int = 400) -> Image.Image:
    """Creates a high-contrast card with crisp black text on white paper."""
    im = Image.new("RGB", (width, height), (250, 248, 245))
    draw = ImageDraw.Draw(im)
    draw.text((40, 40), "Classic Chocolate Chip Cookies", fill=(20, 20, 20))
    for i, y in enumerate(range(80, height - 60, 30)):
        draw.line([(40, y), (width - 40, y)], fill=(30, 30, 35), width=2)
        draw.text((45, y + 4), f"Step {i + 1}: Mix dry ingredients thoroughly", fill=(25, 25, 25))
    return im


def make_faded_card_image(width: int = 600, height: int = 400) -> Image.Image:
    """Creates a low-contrast card with faint pencil handwriting on aged paper."""
    im = Image.new("RGB", (width, height), (230, 225, 215))
    draw = ImageDraw.Draw(im)
    draw.text((40, 40), "Grandma's Sugar Cookies", fill=(195, 190, 180))
    for i, y in enumerate(range(80, height - 60, 30)):
        draw.line([(40, y), (width - 40, y)], fill=(190, 185, 175), width=1)
        draw.text((45, y + 4), f"Step {i + 1}: Mix flour and butter gently", fill=(192, 187, 177))
    return im


def make_rotated_card_image(
    angle: float = 3.5, width: int = 500, height: int = 350
) -> Image.Image:
    """Creates a card rotated by a small angle with horizontal text lines."""
    card = Image.new("RGB", (width, height), (252, 250, 245))
    draw = ImageDraw.Draw(card)
    draw.text((30, 30), "Rotated Card Recipe Title", fill=(20, 20, 20))
    for y in range(70, height - 40, 30):
        draw.line([(30, y), (width - 30, y)], fill=(30, 30, 30), width=2)
    return card.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=(255, 255, 255))


def make_card_with_scanner_bed(
    card_w: int = 400, card_h: int = 280, bed_w: int = 600, bed_h: int = 450
) -> Image.Image:
    """Places a recipe card onto a dark scanner bed background."""
    bed = Image.new("RGB", (bed_w, bed_h), (25, 25, 28))
    card = make_normal_card_image(card_w, card_h)
    x = (bed_w - card_w) // 2
    y = (bed_h - card_h) // 2
    bed.paste(card, (x, y))
    return bed


# ============================================================================
# Unit Tests: Components
# ============================================================================

def test_contrast_metric_discrimination():
    """Verifies that the contrast metric reliably distinguishes faded from normal cards."""
    normal_im = make_normal_card_image()
    faded_im = make_faded_card_image()
    blank_im = Image.new("RGB", (300, 200), (240, 240, 240))

    assert not is_faded_or_low_contrast(normal_im), "Normal high-contrast card should not be judged faded"
    assert is_faded_or_low_contrast(faded_im), "Faint pencil card must be judged faded/low-contrast"
    assert is_faded_or_low_contrast(blank_im), "Solid blank card must be judged low-contrast"


def test_clahe_contrast_enhancement():
    """Verifies that vectorized NumPy CLAHE substantially increases dynamic contrast on faded images."""
    faded_im = make_faded_card_image(400, 300)
    arr_before = np.array(faded_im.convert("L"))
    std_before = float(np.std(arr_before))

    enhanced_im = apply_clahe(faded_im)
    assert enhanced_im.size == faded_im.size
    assert enhanced_im.mode == "RGB"

    arr_after = np.array(enhanced_im.convert("L"))
    std_after = float(np.std(arr_after))

    # Standard deviation / dynamic range should increase significantly after CLAHE
    assert std_after > std_before * 2.0, (
        f"CLAHE failed to expand contrast: before std={std_before:.2f}, after std={std_after:.2f}"
    )


def test_cap_long_edge():
    """Verifies that long edge capping preserves aspect ratio and never upscales."""
    # 1. Downscale large image
    huge_im = Image.new("RGB", (3000, 2000), (255, 255, 255))
    capped = cap_long_edge(huge_im, max_long_edge=1600)
    assert max(capped.size) == 1600
    assert capped.size == (1600, 1067)

    # 2. Portrait orientation
    portrait_huge = Image.new("RGB", (1800, 3600), (255, 255, 255))
    capped_p = cap_long_edge(portrait_huge, max_long_edge=1280)
    assert max(capped_p.size) == 1280
    assert capped_p.size == (640, 1280)

    # 3. Small image should NOT be upscaled
    small_im = Image.new("RGB", (800, 600), (255, 255, 255))
    kept = cap_long_edge(small_im, max_long_edge=1600)
    assert kept.size == (800, 600)


def test_card_border_cropping():
    """Verifies that card border cropping isolates cards from dark scanner beds."""
    bed_im = make_card_with_scanner_bed(card_w=400, card_h=280, bed_w=600, bed_h=450)
    cropped, box = crop_card_borders(bed_im, padding_px=0)

    # Card was at (100, 85, 500, 365)
    cw, ch = cropped.size
    assert 380 <= cw <= 420
    assert 260 <= ch <= 300


def test_deskew_small_angles():
    """Verifies that deskew corrects small angular rotations."""
    rotated = make_rotated_card_image(angle=3.5, width=400, height=300)
    deskewed, detected_angle = deskew_card_image(rotated, max_angle=10.0)

    # Deskew should detect angle close to 3.5°
    assert abs(detected_angle - 3.5) <= 1.0


def test_encode_image_format_and_mime():
    """Verifies that encode_image produces valid JPEG and MIME types."""
    im = make_normal_card_image(300, 200)
    data, mime = encode_image(im, jpeg_quality=90)

    assert mime in ("image/jpeg", "image/png")
    assert sniff_mime_type(data) == mime
    assert data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG")


# ============================================================================
# Contract Tests: prepare_faces Pipeline
# ============================================================================

def test_prepare_faces_normal_contrast(tmp_path: Path):
    """Verifies normal contrast card produces exactly 1 standard PreparedFace."""
    card = make_normal_card_image(600, 400)
    path = tmp_path / "card_normal.jpg"
    card.save(path, format="JPEG", quality=95)

    prepared = prepare_faces([path], profile_params={"image_long_edge": 1600}, enhance=None)

    assert len(prepared) == 1
    face = prepared[0]
    assert face.face_index == 0
    assert face.n_faces == 1
    assert face.face_number == 1
    assert face.variant == VARIANT_STANDARD
    assert face.label == "Face 1 of 1"
    assert face.long_edge_px <= 1600
    assert face.mime_type == "image/jpeg"
    assert sniff_mime_type(face.data) == "image/jpeg"
    assert not face.is_enhanced


def test_prepare_faces_faded_contrast_auto_enhancement(tmp_path: Path):
    """Verifies faded card under enhance=None triggers both standard and enhanced copies."""
    card = make_faded_card_image(600, 400)
    path = tmp_path / "card_faded.jpg"
    card.save(path, format="JPEG", quality=95)

    prepared = prepare_faces([path], profile_params={"image_long_edge": 1600}, enhance=None)

    assert len(prepared) == 2

    # Standard face
    std = prepared[0]
    assert std.face_index == 0
    assert std.n_faces == 1
    assert std.variant == VARIANT_STANDARD
    assert std.label == "Face 1 of 1"
    assert not std.is_enhanced

    # Enhanced copy
    enh = prepared[1]
    assert enh.face_index == 0
    assert enh.n_faces == 1
    assert enh.variant == VARIANT_ENHANCED_CLAHE
    assert enh.label == "Face 1 of 1 (contrast-enhanced copy)"
    assert enh.is_enhanced
    assert enh.long_edge_px == std.long_edge_px


def test_prepare_faces_explicit_enhance_flags(tmp_path: Path):
    """Verifies enhance=True forces enhancement and enhance=False suppresses it."""
    normal_card = make_normal_card_image(500, 350)
    faded_card = make_faded_card_image(500, 350)

    p_norm = tmp_path / "norm.jpg"
    p_fade = tmp_path / "fade.jpg"
    normal_card.save(p_norm, format="JPEG")
    faded_card.save(p_fade, format="JPEG")

    # 1. enhance=True forces enhanced copy even on normal cards
    res_forced = prepare_faces([p_norm], profile_params={}, enhance=True)
    assert len(res_forced) == 2
    assert res_forced[0].variant == VARIANT_STANDARD
    assert res_forced[1].variant == VARIANT_ENHANCED_CLAHE
    assert res_forced[1].label == "Face 1 of 1 (contrast-enhanced copy)"

    # 2. enhance=False suppresses enhancement even on faded cards
    res_suppressed = prepare_faces([p_fade], profile_params={}, enhance=False)
    assert len(res_suppressed) == 1
    assert res_suppressed[0].variant == VARIANT_STANDARD
    assert res_suppressed[0].label == "Face 1 of 1"


def test_prepare_faces_multi_face_labels_and_indices(tmp_path: Path):
    """Verifies multi-face recipes format exact 'Face i of N' labels and 0-based indices."""
    card1 = make_normal_card_image(500, 350)
    card2 = make_faded_card_image(500, 350)
    card3 = make_normal_card_image(500, 350)

    p1 = tmp_path / "face1.jpg"
    p2 = tmp_path / "face2.jpg"
    p3 = tmp_path / "face3.jpg"
    card1.save(p1)
    card2.save(p2)
    card3.save(p3)

    prepared = prepare_faces([p1, p2, p3], profile_params={"image_long_edge": 1600}, enhance=None)

    # Face 1: normal -> 1 entry
    # Face 2: faded -> 2 entries (std + enh)
    # Face 3: normal -> 1 entry
    # Total: 4 PreparedFace objects across 3 physical faces
    assert len(prepared) == 4

    labels = [pf.label for pf in prepared]
    expected_labels = [
        "Face 1 of 3",
        "Face 2 of 3",
        "Face 2 of 3 (contrast-enhanced copy)",
        "Face 3 of 3",
    ]
    assert labels == expected_labels

    indices = [pf.face_index for pf in prepared]
    assert indices == [0, 1, 1, 2]

    for pf in prepared:
        assert pf.n_faces == 3


def test_prepare_faces_huge_image_capping(tmp_path: Path):
    """Verifies that large images have their long edge capped per profile_params."""
    huge_im = Image.new("RGB", (3200, 2400), (250, 250, 250))
    draw = ImageDraw.Draw(huge_im)
    draw.text((100, 100), "Huge Resolution Card", fill=(20, 20, 20))
    p_huge = tmp_path / "huge.jpg"
    huge_im.save(p_huge, format="JPEG")

    # Test economy profile (1280px)
    res_1280 = prepare_faces([p_huge], profile_params={"image_long_edge": 1280})
    assert res_1280[0].long_edge_px == 1280
    with Image.open(io.BytesIO(res_1280[0].data)) as decoded:
        assert max(decoded.size) == 1280
        assert decoded.size == (1280, 960)

    # Test balanced profile (1600px default)
    res_1600 = prepare_faces([p_huge], profile_params={})
    assert res_1600[0].long_edge_px == 1600
    with Image.open(io.BytesIO(res_1600[0].data)) as decoded:
        assert max(decoded.size) == 1600
        assert decoded.size == (1600, 1200)

    # Test best profile (2000px)
    res_2000 = prepare_faces([p_huge], profile_params={"image_long_edge": 2000})
    assert res_2000[0].long_edge_px == 2000
    with Image.open(io.BytesIO(res_2000[0].data)) as decoded:
        assert max(decoded.size) == 2000
        assert decoded.size == (2000, 1500)


def test_prepare_faces_exif_orientation_transposition(tmp_path: Path):
    """Verifies that images with EXIF orientation metadata are properly transposed."""
    # Create image wider than tall (400x200)
    im = Image.new("RGB", (400, 200), (250, 250, 250))
    draw = ImageDraw.Draw(im)
    draw.text((30, 30), "Portrait Card Photographed Sideways", fill=(20, 20, 20))

    exif = im.getexif()
    # EXIF orientation 6 = 90° CW rotation (upright is 200x400)
    exif[0x0112] = 6

    p_exif = tmp_path / "exif_sideways.jpg"
    im.save(p_exif, format="JPEG", exif=exif)

    res = prepare_faces([p_exif], profile_params={"image_long_edge": 1600})
    with Image.open(io.BytesIO(res[0].data)) as decoded:
        # Should be transposed upright: height > width
        w, h = decoded.size
        assert h > w, f"Expected upright height > width after EXIF transpose, got ({w}, {h})"


def test_prepare_faces_no_filename_influence(tmp_path: Path):
    """Verifies that filenames NEVER influence whether a face is enhanced or not."""
    normal_im = make_normal_card_image(500, 350)
    faded_im = make_faded_card_image(500, 350)

    # Save images with explicit format so disk bytes are identical regardless of filename
    # Name the normal image deceptively as "faded_cursive_back.jpg"
    p_deceptive_normal = tmp_path / "faded_cursive_back.jpg"
    normal_im.save(p_deceptive_normal, format="JPEG", quality=95)

    # Name the faded image deceptively as "crisp_printed_high_contrast.png" (written as JPEG bytes)
    p_deceptive_faded = tmp_path / "crisp_printed_high_contrast.png"
    faded_im.save(p_deceptive_faded, format="JPEG", quality=95)

    # Neutral / opaque UUID names with identical image payloads
    p_opaque_normal = tmp_path / "8f2a1b9c.jpg"
    p_opaque_faded = tmp_path / "e4d3c2b1.jpg"
    normal_im.save(p_opaque_normal, format="JPEG", quality=95)
    faded_im.save(p_opaque_faded, format="JPEG", quality=95)

    res_deceptive = prepare_faces([p_deceptive_normal, p_deceptive_faded], profile_params={})
    res_opaque = prepare_faces([p_opaque_normal, p_opaque_faded], profile_params={})

    # Normal should have 1 entry regardless of filename
    assert len([pf for pf in res_deceptive if pf.face_index == 0]) == 1
    assert len([pf for pf in res_opaque if pf.face_index == 0]) == 1

    # Faded should have 2 entries (std + enhanced) regardless of filename
    assert len([pf for pf in res_deceptive if pf.face_index == 1]) == 2
    assert len([pf for pf in res_opaque if pf.face_index == 1]) == 2

    # Verify bit-for-bit equivalence regardless of filenames
    assert res_deceptive[0].data == res_opaque[0].data
    assert res_deceptive[1].data == res_opaque[1].data
    assert res_deceptive[2].data == res_opaque[2].data


def test_prepare_faces_bit_for_bit_determinism(tmp_path: Path):
    """Verifies that running prepare_faces repeatedly yields bit-for-bit identical bytes."""
    card1 = make_normal_card_image(500, 350)
    card2 = make_faded_card_image(500, 350)

    p1 = tmp_path / "det1.jpg"
    p2 = tmp_path / "det2.jpg"
    card1.save(p1)
    card2.save(p2)

    run_a = prepare_faces([p1, p2], profile_params={"image_long_edge": 1600})
    run_b = prepare_faces([p1, p2], profile_params={"image_long_edge": 1600})

    assert len(run_a) == len(run_b)
    for fa, fb in zip(run_a, run_b):
        assert fa.data == fb.data
        assert fa.face_index == fb.face_index
        assert fa.n_faces == fb.n_faces
        assert fa.variant == fb.variant
        assert fa.label == fb.label
        assert fa.mime_type == fb.mime_type
        assert fa.long_edge_px == fb.long_edge_px


def test_prepare_faces_empty_and_missing_paths(tmp_path: Path):
    """Verifies graceful handling of empty paths and FileNotFoundError on missing files."""
    assert prepare_faces([]) == []

    missing = tmp_path / "non_existent.jpg"
    with pytest.raises(FileNotFoundError):
        prepare_faces([missing])
