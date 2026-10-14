"""Validation tests for WP-G2 deterministic pixel feature extraction.

Tests low-level physical feature recovery against cardforge cards generated
with known procedural parameters (paper colour, ruling spacing, ink colour, skew angle).
Asserts accuracy within tolerances, rotation/skew robustness, and execution performance.
"""

from __future__ import annotations

import colorsys
import random
import time
from pathlib import Path
from typing import Tuple

import numpy as np
import pytest
from PIL import Image, ImageDraw

# cardforge imported ONLY in tests per contracts
from cardforge.generator import (
    apply_coffee_ring,
    apply_oil_smudge,
    apply_rotation_skew,
    create_paper_substrate,
    render_handwritten_lines,
)

from cardforge.models import PaperSubstrate, RulingStyle
from recipeboxer.models.features import PixelFeatures
from recipeboxer.vision.heuristics import rgb_to_lab
from recipeboxer.vision.pixel_features import (
    clear_stain_cache,
    compute_pixel_features,
    get_stain_mask,
)


@pytest.fixture(autouse=True)
def reset_lru_cache():
    """Ensures stain mask cache is clear before and after each test."""
    clear_stain_cache()
    yield
    clear_stain_cache()


# ============================================================================
# 1. PAPER SUBSTRATE COLORIMETRY TESTS
# ============================================================================

class TestPaperColorimetry:
    """Verifies paper LAB mean and std recovery against known substrate colors."""

    @pytest.mark.parametrize(
        "substrate,base_rgb",
        [
            (PaperSubstrate.RULED_INDEX, (248, 240, 215)),
            (PaperSubstrate.WHITE_LINEN, (252, 252, 248)),
            (PaperSubstrate.KRAFT_BROWN, (215, 190, 160)),
            (PaperSubstrate.BLANK, (240, 235, 220)),
        ],
    )
    def test_paper_lab_recovery_within_tolerance(
        self,
        substrate: PaperSubstrate,
        base_rgb: Tuple[int, int, int],
        tmp_path: Path,
    ):
        rng = random.Random(42)
        card, _ = create_paper_substrate(
            width=680,
            height=440,
            substrate=substrate,
            rng=rng,
            ruling=RulingStyle.UNRULED,
            base_color_override=base_rgb,
        )
        draw = ImageDraw.Draw(card)
        # Add typical recipe text line
        draw.text((60, 100), "Grandma's Apple Pie Crust Recipe", fill=(25, 30, 40))

        feat = compute_pixel_features(card)
        true_lab = rgb_to_lab(*base_rgb)

        # Assert LAB mean is within tight tolerances
        assert abs(feat.paper_lab[0] - true_lab[0]) <= 2.5, "L* difference exceeds tolerance"
        assert abs(feat.paper_lab[1] - true_lab[1]) <= 1.5, "a* difference exceeds tolerance"
        assert abs(feat.paper_lab[2] - true_lab[2]) <= 2.5, "b* difference exceeds tolerance"

        # Assert paper standard deviation is non-negative and bounded
        assert all(std >= 0.0 for std in feat.paper_lab_std)
        assert feat.paper_lab_std[0] <= 8.0, "L* standard deviation abnormally high"


# ============================================================================
# 2. RULING LINE SPACING AND PATTERN KIND TESTS
# ============================================================================

class TestRulingDetection:
    """Verifies ruling line spacing and kind (lined, double, grid, none)."""

    @pytest.mark.parametrize(
        "ruling_style,expected_spacing,expected_kind",
        [
            (RulingStyle.STANDARD_RULED, 26.0, "lined"),
            (RulingStyle.WIDE_RULED, 32.0, "lined"),
            (RulingStyle.NARROW_RULED, 20.0, "lined"),
            (RulingStyle.DOUBLE_HEADER, 25.0, "double"),
            (RulingStyle.GRID_GRAPH, 20.0, "grid"),
            (RulingStyle.UNRULED, None, "none"),
        ],
    )
    def test_ruling_recovery_across_styles(
        self,
        ruling_style: RulingStyle,
        expected_spacing: float | None,
        expected_kind: str,
    ):
        rng = random.Random(101)
        sub = (
            PaperSubstrate.GRID_GRAPH
            if ruling_style == RulingStyle.GRID_GRAPH
            else PaperSubstrate.RULED_INDEX
        )
        card, _ = create_paper_substrate(
            width=680,
            height=440,
            substrate=sub,
            rng=rng,
            ruling=ruling_style,
        )

        feat = compute_pixel_features(card)

        assert feat.ruling_kind == expected_kind
        if expected_spacing is not None:
            assert feat.ruling_spacing_px is not None
            assert abs(feat.ruling_spacing_px - expected_spacing) <= 1.5
            assert feat.ruling_angle_deg is not None
            assert abs(feat.ruling_angle_deg) <= 1.5
        else:
            assert feat.ruling_spacing_px is None
            assert feat.ruling_angle_deg is None


# ============================================================================
# 3. INK HUE CLUSTERING TESTS
# ============================================================================

class TestInkHueClustering:
    """Verifies ink hue clustering accuracy on known writing colors."""

    @pytest.mark.parametrize(
        "ink_rgb,label",
        [
            ((25, 35, 115), "Blue fountain pen"),
            ((190, 40, 50), "Red correction ink"),
            ((30, 125, 60), "Green ledger ink"),
        ],
    )
    def test_known_ink_hue_clustering(
        self,
        ink_rgb: Tuple[int, int, int],
        label: str,
    ):
        rng = random.Random(77)
        card, _ = create_paper_substrate(
            width=680,
            height=440,
            substrate=PaperSubstrate.RULED_INDEX,
            rng=rng,
            ruling=RulingStyle.UNRULED,
        )
        draw = ImageDraw.Draw(card)
        for y in range(80, 360, 35):
            draw.text((60, y), f"Ingredient line at row {y} written in {label}", fill=ink_rgb)

        r, g, b = ink_rgb[0] / 255.0, ink_rgb[1] / 255.0, ink_rgb[2] / 255.0
        true_h, _, _ = colorsys.rgb_to_hsv(r, g, b)
        true_hue = true_h * 360.0

        feat = compute_pixel_features(card)

        assert len(feat.ink_hues) > 0, f"Expected ink hues for {label}"
        assert len(feat.ink_hues) <= 3, "k must be <= 3"

        # Circular angle difference
        min_diff = min(
            min(abs(h - true_hue), 360.0 - abs(h - true_hue))
            for h in feat.ink_hues
        )
        assert min_diff <= 15.0, f"Recovered hue diff {min_diff:.1f}° exceeds tolerance for {label}"

    def test_blank_card_returns_empty_ink_hues(self):
        rng = random.Random(42)
        card, _ = create_paper_substrate(
            width=600,
            height=400,
            substrate=PaperSubstrate.BLANK,
            rng=rng,
            ruling=RulingStyle.UNRULED,
        )
        feat = compute_pixel_features(card)
        assert feat.ink_hues == []


# ============================================================================
# 4. ROTATION AND SKEW ROBUSTNESS TESTS
# ============================================================================

class TestRotationSkewRobustness:
    """Verifies deskew angle detection and ruling recovery on rotated scans."""

    @pytest.mark.parametrize("skew_angle", [3.5, -5.0, 7.0, -2.5, 0.0])
    def test_deskew_and_ruling_on_rotated_cards(self, skew_angle: float):
        rng = random.Random(202)
        card, _ = create_paper_substrate(
            width=680,
            height=440,
            substrate=PaperSubstrate.RULED_INDEX,
            rng=rng,
            ruling=RulingStyle.STANDARD_RULED,
        )
        draw = ImageDraw.Draw(card)
        render_handwritten_lines(
            draw=draw,
            lines=[
                "Grandma's Cinnamon Cake",
                "2 cups all-purpose flour",
                "1 cup granulated sugar",
                "2 tsp baking powder",
                "1/2 cup butter, melted",
                "1 cup whole milk",
            ],
            start_x=60,
            start_y=86,
            max_width=600,
            max_height=400,
            ink_color=(25, 35, 115),
            font_size=18,
            rng=rng,
        )

        rotated = apply_rotation_skew(card, skew_angle)
        feat = compute_pixel_features(rotated)


        # Deskew angle accuracy within +-1.0 degree
        assert abs(feat.deskew_angle_deg - skew_angle) <= 1.0, (
            f"Detected deskew {feat.deskew_angle_deg} vs true skew {skew_angle}"
        )

        # Ruling lines correctly recovered despite rotation
        assert feat.ruling_kind == "lined"
        assert feat.ruling_spacing_px is not None
        assert abs(feat.ruling_spacing_px - 26.0) <= 2.0


# ============================================================================
# 5. CARD SEGMENTATION AND BORDER CROPPING TESTS
# ============================================================================

class TestCardSegmentation:
    """Verifies cropping card from background and aspect ratio calculation."""

    def test_card_on_dark_scanner_bed(self):
        bed = Image.new("RGB", (1000, 700), color=(25, 25, 30))
        card = Image.new("RGB", (680, 440), color=(248, 240, 215))
        draw = ImageDraw.Draw(card)
        draw.text((60, 60), "Grandma's Cinnamon Cake", fill=(20, 20, 30))
        bed.paste(card, (150, 120))

        feat = compute_pixel_features(bed)

        assert feat.crop_box is not None
        x0, y0, x1, y1 = feat.crop_box
        # Check that cropped bounding box closely wraps the card
        assert abs(x0 - 150) <= 20
        assert abs(y0 - 120) <= 20
        assert abs((x1 - x0) - 680) <= 30
        assert abs((y1 - y0) - 440) <= 30

        # Aspect ratio of the card ~ 680 / 440 = 1.545
        assert abs(feat.aspect_ratio - (680.0 / 440.0)) <= 0.15
        assert feat.long_edge_px == 1000

    def test_borderless_card_returns_none_crop_box(self):
        card = Image.new("RGB", (680, 440), color=(248, 240, 215))
        draw = ImageDraw.Draw(card)
        draw.text((60, 60), "Borderless Card Content", fill=(20, 20, 30))

        feat = compute_pixel_features(card)

        assert feat.crop_box is None
        assert abs(feat.aspect_ratio - (680.0 / 440.0)) <= 0.01
        assert feat.long_edge_px == 680


# ============================================================================
# 6. STAIN MASK & IN-PROCESS LRU CACHE TESTS
# ============================================================================

class TestStainMaskLRU:
    """Verifies stain mask extraction and in-process LRU caching."""

    def test_card_with_coffee_ring_stain_detected(self):
        rng = random.Random(42)
        card, _ = create_paper_substrate(
            width=680,
            height=440,
            substrate=PaperSubstrate.RULED_INDEX,
            rng=rng,
            ruling=RulingStyle.STANDARD_RULED,
        )
        stained = apply_coffee_ring(card, cx=300, cy=200, radius=75, rng=rng)

        feat = compute_pixel_features(stained)

        assert feat.stain_mask_ref is not None
        assert feat.stain_mask_ref.startswith("stain_")

        mask = get_stain_mask(feat.stain_mask_ref)
        assert mask is not None
        assert isinstance(mask, np.ndarray)
        assert mask.dtype == bool
        assert np.sum(mask) > 100, "Expected positive stain pixels in coffee ring"

    def test_clean_card_stain_mask_is_empty(self):
        rng = random.Random(42)
        card, _ = create_paper_substrate(
            width=680,
            height=440,
            substrate=PaperSubstrate.RULED_INDEX,
            rng=rng,
            ruling=RulingStyle.UNRULED,
        )
        feat = compute_pixel_features(card)
        mask = get_stain_mask(feat.stain_mask_ref)
        assert mask is not None
        assert np.sum(mask) == 0

    def test_stain_cache_retrieval_and_clear(self):
        im = Image.new("RGB", (200, 200), color=(240, 230, 210))
        feat = compute_pixel_features(im)
        ref = feat.stain_mask_ref
        assert get_stain_mask(ref) is not None

        clear_stain_cache()
        assert get_stain_mask(ref) is None


# ============================================================================
# 7. PERFORMANCE & MULTI-INPUT ROBUSTNESS TESTS
# ============================================================================

class TestPerformanceAndInputs:
    """Verifies execution performance (<= 300ms on 1600px image) and input formats."""

    def test_1600px_execution_under_300ms(self):
        card = Image.new("RGB", (1600, 1000), color=(248, 240, 215))
        draw = ImageDraw.Draw(card)
        for y in range(80, 920, 30):
            draw.line([(80, y), (1520, y)], fill=(160, 190, 220), width=2)
            draw.text((100, y - 22), f"Instruction step at y={y} with details", fill=(25, 35, 115))

        # Rotate card slightly to trigger deskew pathway
        rotated = card.rotate(2.5, resample=Image.BICUBIC, expand=True, fillcolor=(255, 255, 255))

        t0 = time.perf_counter()
        feat = compute_pixel_features(rotated)
        t1 = time.perf_counter()
        elapsed_ms = (t1 - t0) * 1000.0

        assert elapsed_ms <= 300.0, f"Execution took {elapsed_ms:.1f} ms (> 300 ms budget)"
        assert isinstance(feat, PixelFeatures)
        assert feat.ruling_kind == "lined"

    def test_input_from_path_and_bytes(self, tmp_path: Path):
        card = Image.new("RGB", (400, 300), color=(240, 235, 220))
        opaque_name = tmp_path / "7a8b9c0d.jpg"  # Opaque name (anticheat)
        card.save(opaque_name)

        # From Path
        feat_path = compute_pixel_features(opaque_name)
        assert isinstance(feat_path, PixelFeatures)

        # From bytes
        raw_bytes = opaque_name.read_bytes()
        feat_bytes = compute_pixel_features(raw_bytes)
        assert isinstance(feat_bytes, PixelFeatures)
        assert feat_path.paper_lab == feat_bytes.paper_lab
