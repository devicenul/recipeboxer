"""Unit and integration tests for Deterministic Computer Vision Preprocessing Engine.

Covers:
- Auto-deskewing via projection profile variance analysis ([-45°, +45°], ±1° tolerance)
- Card border auto-cropping and dark scanner bed removal
- CLAHE contrast equalization and white-balance normalization
- CIELAB paper and ink colorimetric palette clustering
"""

import io
from pathlib import Path
from typing import Tuple

import pytest
from PIL import Image, ImageDraw

from recipeboxer.vision.heuristics import rgb_to_lab
from recipeboxer.vision.preprocessing import (
    apply_clahe,
    crop_card_borders,
    deskew_card_image,
    detect_skew_angle,
    enhance_card_readability,
    extract_paper_color_palette,
    lab_to_rgb,
    normalize_white_balance,
)


def _create_synthetic_text_card(
    width: int = 600, height: int = 400, bg_color: int = 245
) -> Image.Image:
    """Helper to generate a clean synthetic text card with horizontal ruling and text lines."""
    im = Image.new("L", (width, height), color=bg_color)
    draw = ImageDraw.Draw(im)
    for y in range(40, height - 40, 30):
        draw.line([(40, y), (width - 40, y)], fill=180, width=1)
        draw.text((50, y - 16), f"Sample Recipe Instruction Line at row {y}", fill=30)
    return im


class TestAutoDeskewing:
    """Tests for orientation skew detection and auto-deskewing rotation."""

    def test_detect_skew_angle_upright_image(self):
        card = _create_synthetic_text_card()
        detected_angle = detect_skew_angle(card)
        assert abs(detected_angle) <= 1.0

    @pytest.mark.parametrize("true_angle", [5.0, -8.0, 12.5, -15.0, 22.0, -3.5])
    def test_detect_skew_angle_known_rotations(self, true_angle: float):
        card = _create_synthetic_text_card()
        # Rotate image with expansion
        rotated = card.rotate(true_angle, resample=Image.BICUBIC, expand=True, fillcolor=255)
        detected_angle = detect_skew_angle(rotated)

        # Must correct within +-1.0 degree tolerance
        assert abs(detected_angle - true_angle) <= 1.0, (
            f"Expected skew ~{true_angle}, but detected {detected_angle}"
        )

    def test_deskew_card_image_returns_upright_and_expanded(self):
        card = _create_synthetic_text_card()
        rotated = card.rotate(10.0, resample=Image.BICUBIC, expand=True, fillcolor=255)

        deskewed, detected_angle = deskew_card_image(rotated)
        assert isinstance(deskewed, Image.Image)
        assert abs(detected_angle - 10.0) <= 1.0

        # Deskewing the result should yield near-zero angle
        second_pass_angle = detect_skew_angle(deskewed)
        assert abs(second_pass_angle) <= 1.0

    def test_deskew_card_image_from_bytes_and_path(self, tmp_path):
        card = _create_synthetic_text_card()
        rotated = card.rotate(-7.0, resample=Image.BICUBIC, expand=True, fillcolor=255)

        # Test from Path
        test_file = tmp_path / "test_rotated.png"
        rotated.save(test_file)
        deskewed_from_path, angle1 = deskew_card_image(test_file)
        assert abs(angle1 - (-7.0)) <= 1.0

        # Test from raw bytes
        raw_bytes = test_file.read_bytes()
        deskewed_from_bytes, angle2 = deskew_card_image(raw_bytes)
        assert abs(angle2 - (-7.0)) <= 1.0


class TestCardBorderAutoCropping:
    """Tests for rectangular card contour identification and scanner bed cropping."""

    def test_crop_dark_scanner_bed(self):
        # Create dark scanner bed (800x600, luminance 30) with a card (500x350, luminance 240) placed inside
        bed = Image.new("RGB", (800, 600), color=(25, 25, 30))
        card = Image.new("RGB", (500, 350), color=(245, 235, 210))
        draw = ImageDraw.Draw(card)
        draw.text((50, 50), "Grandma's Apple Crisp", fill=(20, 20, 20))

        card_x0, card_y0 = 150, 120
        bed.paste(card, (card_x0, card_y0))

        cropped, bbox = crop_card_borders(bed, padding_px=10)
        x_min, y_min, x_max, y_max = bbox

        # With 10px padding, detected box should encompass the card bounds closely
        assert abs(x_min - (card_x0 - 10)) <= 15
        assert abs(y_min - (card_y0 - 10)) <= 15
        assert abs(x_max - (card_x0 + 500 + 10)) <= 15
        assert abs(y_max - (card_y0 + 350 + 10)) <= 15
        assert cropped.width > 480
        assert cropped.height > 330

    def test_crop_card_already_full_frame(self):
        # Card that already fills scan frame completely
        card = Image.new("RGB", (600, 400), color=(245, 235, 210))
        draw = ImageDraw.Draw(card)
        draw.text((50, 50), "Full frame recipe card", fill=(20, 20, 20))

        cropped, bbox = crop_card_borders(card, padding_px=10)
        assert bbox == (0, 0, 600, 400)
        assert cropped.size == (600, 400)

    def test_crop_invalid_inputs_raise(self):
        with pytest.raises(FileNotFoundError):
            crop_card_borders(Path("/nonexistent/file.png"))
        with pytest.raises(ValueError):
            crop_card_borders(b"")


class TestAdaptiveContrastAndCLAHE:
    """Tests for CLAHE and white-balance substrate normalization."""

    def test_normalize_white_balance_stretches_dynamic_range(self):
        # Create low-contrast grayish image (all pixel values between 100 and 150)
        im = Image.new("L", (100, 100), color=120)
        draw = ImageDraw.Draw(im)
        draw.rectangle([(20, 20), (80, 80)], fill=105)
        draw.rectangle([(40, 40), (60, 60)], fill=145)

        balanced = normalize_white_balance(im)
        hist = balanced.histogram()
        # Non-zero counts should now span closer to 0 and 255
        min_val = next(i for i, v in enumerate(hist) if v > 0)
        max_val = 255 - next(i for i, v in enumerate(reversed(hist)) if v > 0)
        assert min_val <= 10
        assert max_val >= 245

    def test_apply_clahe_enhances_contrast_and_preserves_dimensions(self):
        card = _create_synthetic_text_card(width=300, height=200)
        enhanced = apply_clahe(card, clip_limit=2.0, tile_grid_size=(4, 4))

        assert isinstance(enhanced, Image.Image)
        assert enhanced.size == (300, 200)
        assert enhanced.mode == "L"

    def test_enhance_card_readability_rgb(self):
        rgb_card = Image.new("RGB", (320, 240), color=(240, 230, 200))
        draw = ImageDraw.Draw(rgb_card)
        draw.text((40, 40), "Faint pencil text", fill=(180, 175, 160))

        enhanced = enhance_card_readability(rgb_card, clip_limit=2.5, white_balance=True)
        assert enhanced.size == (320, 240)
        assert enhanced.mode == "RGB"


class TestPaperColorPaletteClustering:
    """Tests for CIELAB colorimetric palette clustering and color math."""

    def test_lab_rgb_roundtrip(self):
        test_colors = [
            (255, 255, 255),
            (0, 0, 0),
            (245, 235, 210),  # Cream paper
            (20, 30, 80),     # Blue ink
            (180, 120, 60),   # Kraft brown
        ]
        for r, g, b in test_colors:
            lab_l, lab_a, lab_b = rgb_to_lab(r, g, b)
            r_rec, g_rec, b_rec = lab_to_rgb(lab_l, lab_a, lab_b)
            # Roundtrip within rounding tolerance (±2)
            assert abs(r - r_rec) <= 2, f"R mismatch for ({r},{g},{b}) -> rec ({r_rec},{g_rec},{b_rec})"
            assert abs(g - g_rec) <= 2, f"G mismatch for ({r},{g},{b}) -> rec ({r_rec},{g_rec},{b_rec})"
            assert abs(b - b_rec) <= 2, f"B mismatch for ({r},{g},{b}) -> rec ({r_rec},{g_rec},{b_rec})"

    def test_extract_paper_color_palette_structure(self):
        # Create image with cream paper and blue ink
        im = Image.new("RGB", (400, 300), color=(245, 235, 210))
        draw = ImageDraw.Draw(im)
        draw.rectangle([(50, 50), (350, 250)], outline=(20, 40, 90), width=10)

        palette = extract_paper_color_palette(im, num_colors=3, sample_size=1000)

        assert "paper_centroid" in palette
        assert "ink_centroids" in palette
        assert "palette" in palette

        paper = palette["paper_centroid"]
        assert paper["lab"][0] >= 85.0  # Lightness of cream paper is > 85
        assert paper["proportion"] >= 0.50
        assert paper["hex"].startswith("#")

        assert len(palette["palette"]) <= 3

    def test_extract_palette_solid_color_image(self):
        # Edge case: solid white image
        solid_white = Image.new("RGB", (100, 100), color=(255, 255, 255))
        palette = extract_paper_color_palette(solid_white, num_colors=3)
        assert palette["paper_centroid"]["lab"][0] >= 95.0
        assert len(palette["ink_centroids"]) == 0

    def test_palette_clustering_determinism(self):
        card = _create_synthetic_text_card()
        rgb = card.convert("RGB")
        p1 = extract_paper_color_palette(rgb, num_colors=4, seed=123)
        p2 = extract_paper_color_palette(rgb, num_colors=4, seed=123)
        assert p1["paper_centroid"]["hex"] == p2["paper_centroid"]["hex"]
        assert len(p1["palette"]) == len(p2["palette"])


class TestCVPreprocessingEdgeCases:
    """Tests boundary values, extreme dimensions, and error paths."""

    def test_deskew_extreme_boundary_angles(self):
        card = _create_synthetic_text_card()
        # +35.0 degrees
        rot_pos = card.rotate(35.0, resample=Image.BICUBIC, expand=True, fillcolor=255)
        angle_pos = detect_skew_angle(rot_pos, max_angle=45.0)
        assert abs(angle_pos - 35.0) <= 1.5

        # -35.0 degrees
        rot_neg = card.rotate(-35.0, resample=Image.BICUBIC, expand=True, fillcolor=255)
        angle_neg = detect_skew_angle(rot_neg, max_angle=45.0)
        assert abs(angle_neg - (-35.0)) <= 1.5

    def test_clahe_on_extreme_uniform_images(self):
        # Pure white
        white = Image.new("L", (100, 100), color=255)
        enh_white = apply_clahe(white, clip_limit=2.0)
        assert enh_white.size == (100, 100)

        # Pure black
        black = Image.new("L", (100, 100), color=0)
        enh_black = apply_clahe(black, clip_limit=2.0)
        assert enh_black.size == (100, 100)

    def test_deskew_and_enhance_error_paths(self):
        with pytest.raises(FileNotFoundError):
            detect_skew_angle(Path("/nonexistent/card.png"))
        with pytest.raises(ValueError):
            detect_skew_angle(b"")
        with pytest.raises(TypeError):
            detect_skew_angle(12345)

        with pytest.raises(FileNotFoundError):
            enhance_card_readability(Path("/nonexistent/card.png"))

    def test_deskew_on_blank_and_solid_images(self):
        for bg in [(255, 255, 255), (245, 235, 210), (0, 0, 0), (128, 128, 128)]:
            im = Image.new("RGB", (400, 300), color=bg)
            angle = detect_skew_angle(im)
            assert abs(angle) <= 1.0, f"Expected 0.0 for blank image with bg={bg}, got {angle}"
            deskewed, d_angle = deskew_card_image(im)
            assert abs(d_angle) <= 1.0

    def test_clahe_and_wb_preserve_rgba_mode(self):
        rgba = Image.new("RGBA", (120, 80), color=(220, 180, 140, 200))
        draw = ImageDraw.Draw(rgba)
        draw.text((10, 10), "Recipe Text", fill=(30, 20, 10, 255))

        wb_res = normalize_white_balance(rgba)
        assert wb_res.mode == "RGBA"
        assert wb_res.size == (120, 80)

        clahe_res = apply_clahe(rgba, clip_limit=2.0)
        assert clahe_res.mode == "RGBA"
        assert clahe_res.size == (120, 80)

        enh_res = enhance_card_readability(rgba)
        assert enh_res.mode == "RGBA"
        assert enh_res.size == (120, 80)

    def test_clahe_and_wb_preserve_la_mode(self):
        la = Image.new("LA", (100, 100), color=(180, 220))
        draw = ImageDraw.Draw(la)
        draw.text((10, 10), "Sample Text", fill=(30, 255))

        wb_res = normalize_white_balance(la)
        assert wb_res.mode == "LA"
        assert wb_res.size == (100, 100)

        clahe_res = apply_clahe(la, clip_limit=2.0)
        assert clahe_res.mode == "LA"
        assert clahe_res.size == (100, 100)

    def test_deskew_rgba_and_la_fill_color(self):
        rgba = Image.new("RGBA", (200, 100), color=(240, 230, 210, 255))
        draw = ImageDraw.Draw(rgba)
        draw.text((20, 20), "Deskew fill test line", fill=(20, 20, 20, 255))
        rot = rgba.rotate(15, expand=True)

        deskewed, _ = deskew_card_image(rot, background_color=(255, 255, 255))
        assert deskewed.mode == "RGBA"
        # Corner pixel should be white with full alpha (255, 255, 255, 255)
        corner = deskewed.getpixel((0, 0))
        assert corner == (255, 255, 255, 255)

    def test_preprocessing_extreme_aspect_ratios_and_sizes(self):
        for size in [(10, 400), (400, 10), (6, 6), (50, 50)]:
            im = Image.new("RGB", size, color=(240, 230, 210))
            assert detect_skew_angle(im) == 0.0
            enh = enhance_card_readability(im)
            assert enh.size == size
            cr, bbox = crop_card_borders(im)
            assert cr.size == size

    def test_clahe_on_single_pixel_and_tiny_images(self):
        # 1x1 image should not collapse to zero
        single_px = Image.new("L", (1, 1), color=128)
        enh_single = apply_clahe(single_px, tile_grid_size=(8, 8))
        assert enh_single.size == (1, 1)
        assert enh_single.getpixel((0, 0)) > 0

        # 3x3 image
        tiny = Image.new("RGB", (3, 3), color=(200, 150, 100))
        enh_tiny = apply_clahe(tiny, tile_grid_size=(8, 8))
        assert enh_tiny.size == (3, 3)
        assert enh_tiny.mode == "RGB"

    def test_extract_palette_zero_and_negative_sample_size(self):
        im = Image.new("RGB", (100, 100), color=(245, 235, 210))
        p_zero = extract_paper_color_palette(im, sample_size=0)
        assert "paper_centroid" in p_zero
        assert len(p_zero["palette"]) >= 2

        p_neg = extract_paper_color_palette(im, sample_size=-10)
        assert "paper_centroid" in p_neg

    def test_detect_skew_negative_max_angle(self):
        card = _create_synthetic_text_card()
        # Should gracefully handle negative max_angle
        angle = detect_skew_angle(card, max_angle=-30.0)
        assert abs(angle) <= 1.0
