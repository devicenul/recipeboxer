"""Deterministic Computer Vision Preprocessing Engine for RecipeBoxer.

Provides pure-Python and Pillow-based deterministic image transformations:
- Auto-deskewing via projection profile variance analysis ([-45°, +45°], ±1° tolerance)
- Rectangular card border auto-cropping (removing scanner beds and dark tabletop borders)
- Adaptive contrast equalization via CLAHE and white-balance substrate normalization
- CIELAB paper and ink colorimetric palette clustering
"""

from __future__ import annotations

import io
import math
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from recipeboxer.vision.heuristics import hex_to_lab, rgb_to_lab


def lab_to_rgb(lab_l: float, lab_a: float, lab_b: float) -> Tuple[int, int, int]:
    """Converts standard CIE L*a*b* coordinates back to 8-bit sRGB coordinates.

    Args:
        lab_l: Lightness (0.0 to 100.0).
        lab_a: a* axis (-128.0 to 128.0).
        lab_b: b* axis (-128.0 to 128.0).

    Returns:
        Tuple of (r, g, b) integers in [0, 255].
    """
    fy = (lab_l + 16.0) / 116.0
    fx = lab_a / 500.0 + fy
    fz = fy - lab_b / 200.0

    def inv_f(t: float) -> float:
        delta = 6.0 / 29.0
        return t**3.0 if t > delta else (t - 16.0 / 116.0) / 7.787

    xr, yr, zr = inv_f(fx), inv_f(fy), inv_f(fz)
    xn, yn, zn = 0.95047, 1.00000, 1.08883
    x, y, z = xr * xn, yr * yn, zr * zn

    # CIE XYZ to linear sRGB matrix conversion (D65)
    r_lin = x * 3.2404542 - y * 1.5371385 - z * 0.4985314
    g_lin = -x * 0.9692660 + y * 1.8760108 + z * 0.0415560
    b_lin = x * 0.0556434 - y * 0.2040259 + z * 1.0572252

    # Linear to gamma-corrected sRGB
    def gamma_corr(c: float) -> float:
        return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1.0 / 2.4)) - 0.055

    r = int(round(max(0.0, min(1.0, gamma_corr(r_lin))) * 255.0))
    g = int(round(max(0.0, min(1.0, gamma_corr(g_lin))) * 255.0))
    b = int(round(max(0.0, min(1.0, gamma_corr(b_lin))) * 255.0))
    return (r, g, b)


def _load_pil_image(
    image: Union[Image.Image, Path, str, bytes],
) -> Image.Image:
    """Helper to convert multiple input representations into a PIL Image."""
    if isinstance(image, Image.Image):
        return image.copy()
    if isinstance(image, (str, Path)):
        p = Path(image)
        if not p.exists():
            raise FileNotFoundError(f"Image path does not exist: {p}")
        with Image.open(p) as im:
            return im.copy()
    if isinstance(image, bytes):
        if len(image) == 0:
            raise ValueError("Cannot decode empty image bytes.")
        return Image.open(io.BytesIO(image)).copy()
    raise TypeError(f"Unsupported image input type: {type(image)}")


# ============================================================================
# 1. AUTO-DESKEWING
# ============================================================================

def detect_skew_angle(
    image: Union[Image.Image, Path, str, bytes],
    max_angle: float = 45.0,
    angle_step: float = 0.5,
    fine_step: float = 0.1,
) -> float:
    """Detects orientation skew angle in degrees using projection profile analysis.

    Searches across [-max_angle, max_angle] using coarse-to-fine projection
    gradient variance maximization.

    Args:
        image: PIL Image, path, or bytes.
        max_angle: Maximum search angle range in degrees (default: 45.0).
        angle_step: Coarse search step in degrees (default: 0.5).
        fine_step: Fine refinement search step in degrees (default: 0.1).

    Returns:
        Detected skew angle in degrees (positive = clockwise tilt).
    """
    im = _load_pil_image(image).convert("L")
    w, h = im.size
    max_angle = abs(max_angle)

    # Downsample for fast search
    target_dim = 400.0
    scale = min(1.0, target_dim / max(w, h))
    sw = max(50, int(w * scale))
    sh = max(50, int(h * scale))
    small = im.resize((sw, sh), Image.BILINEAR)

    # Check overall luminance variance: if image is solid or blank, skew is 0.0
    hist = small.histogram()
    total_pixels = sw * sh
    mean_lum = sum(i * cnt for i, cnt in enumerate(hist)) / float(total_pixels)
    std_lum = math.sqrt(
        sum(((i - mean_lum) ** 2) * cnt for i, cnt in enumerate(hist)) / float(total_pixels)
    )
    if std_lum < 5.0:
        return 0.0

    # Invert and threshold to isolate dark text/ink features
    running_sum = 0
    p20_val = 128
    for val, count in enumerate(hist):
        running_sum += count
        if running_sum >= 0.20 * total_pixels:
            p20_val = val
            break

    # Contrast stretch and binarize ink
    thresh = max(40, min(160, p20_val + 20))
    inv = ImageOps.invert(small)
    bin_img = inv.point(lambda p: 255 if p > (255 - thresh) else 0, mode="L")

    # If ink binarization is sparse, use edge filtering without canvas border artifacts
    if sum(bin_img.histogram()[1:]) < 0.005 * total_pixels:
        edge_img = small.filter(ImageFilter.FIND_EDGES).point(
            lambda p: 255 if p > 30 else 0, mode="L"
        )
        edge_draw = ImageDraw.Draw(edge_img)
        edge_draw.rectangle([(0, 0), (sw - 1, sh - 1)], outline=0, width=2)
        bin_img = edge_img

    # If image has virtually no ink or edges, return 0.0
    if sum(bin_img.histogram()[1:]) < 20:
        return 0.0

    best_angle = 0.0
    max_score = 0.0

    # Coarse search from -max_angle to +max_angle
    num_steps = int(math.ceil(2 * max_angle / angle_step))
    candidate_angles = [-max_angle + i * angle_step for i in range(num_steps + 1)]

    # Central crop box to avoid boundary rotation clipping artifacts
    cw, ch = bin_img.size
    crop_x0 = int(cw * 0.18)
    crop_y0 = int(ch * 0.18)
    crop_x1 = int(cw * 0.82)
    crop_y1 = int(ch * 0.82)
    crop_w = crop_x1 - crop_x0
    crop_h = crop_y1 - crop_y0

    if crop_w <= 10 or crop_h <= 10:
        return 0.0

    for a in candidate_angles:
        r_img = bin_img.rotate(a, resample=Image.NEAREST, expand=False, fillcolor=0)
        cropped = r_img.crop((crop_x0, crop_y0, crop_x1, crop_y1))
        rw, rh = cropped.size

        # Horizontal projection profile
        raw = cropped.tobytes()
        row_sums = [sum(raw[y * rw : (y + 1) * rw]) for y in range(rh)]

        # Adjacent row difference squared (projection profile gradient metric)
        score = sum((row_sums[y + 1] - row_sums[y]) ** 2 for y in range(rh - 1))
        if score > max_score:
            max_score = score
            best_angle = a

    if max_score <= 0.0:
        return 0.0

    # Fine search around best_angle
    fine_best = best_angle
    fine_range = int(math.ceil(angle_step * 2 / fine_step))
    for i in range(-fine_range, fine_range + 1):
        fa = best_angle + i * fine_step
        if abs(fa) > max_angle:
            continue
        r_img = bin_img.rotate(fa, resample=Image.NEAREST, expand=False, fillcolor=0)
        cropped = r_img.crop((crop_x0, crop_y0, crop_x1, crop_y1))
        rw, rh = cropped.size
        raw = cropped.tobytes()
        row_sums = [sum(raw[y * rw : (y + 1) * rw]) for y in range(rh)]
        score = sum((row_sums[y + 1] - row_sums[y]) ** 2 for y in range(rh - 1))
        if score > max_score:
            max_score = score
            fine_best = fa

    return round(-fine_best, 2)


def deskew_card_image(
    image: Union[Image.Image, Path, str, bytes],
    max_angle: float = 45.0,
    background_color: Tuple[int, int, int] = (255, 255, 255),
    angle_step: float = 0.5,
    fine_step: float = 0.1,
) -> Tuple[Image.Image, float]:
    """Auto-deskews a recipe card image, rotating it to true horizontal alignment.

    Detects the skew angle within [-max_angle, max_angle] and rotates the image
    with boundary expansion so text is never clipped.

    Args:
        image: PIL Image, path, or bytes.
        max_angle: Maximum search angle range in degrees (default: 45.0).
        background_color: RGB fill color for expanded background (default: white).
        angle_step: Coarse search step in degrees.
        fine_step: Fine refinement search step in degrees.

    Returns:
        Tuple of (deskewed_pil_image, detected_skew_angle_deg).
    """
    im = _load_pil_image(image)
    skew_angle = detect_skew_angle(
        im, max_angle=max_angle, angle_step=angle_step, fine_step=fine_step
    )

    if abs(skew_angle) < 0.2:
        # Virtually upright, return copy
        return im.copy(), skew_angle

    # Rotate counter to the detected skew angle with bicubic interpolation and expansion
    if im.mode == "RGB":
        fill: Any = background_color
    elif im.mode == "RGBA":
        fill = (*background_color, 255)
    elif im.mode == "LA":
        fill = (background_color[0], 255)
    elif im.mode == "L":
        fill = background_color[0]
    elif im.mode == "1":
        fill = 1 if background_color[0] > 128 else 0
    elif im.mode == "CMYK":
        fill = (0, 0, 0, 0)
    else:
        fill = 255

    deskewed = im.rotate(
        -skew_angle,
        resample=Image.BICUBIC,
        expand=True,
        fillcolor=fill,
    )
    return deskewed, skew_angle


# ============================================================================
# 2. CARD BORDER AUTO-CROPPING
# ============================================================================

def crop_card_borders(
    image: Union[Image.Image, Path, str, bytes],
    padding_px: int = 10,
    threshold_sigma: float = 2.0,
    min_area_ratio: float = 0.20,
) -> Tuple[Image.Image, Tuple[int, int, int, int]]:
    """Identifies card contours and crops out dark scanner beds and shadows.

    Args:
        image: PIL Image, path, or bytes.
        padding_px: Pixel padding to preserve around card borders (default: 10).
        threshold_sigma: Sensitivity multiplier for background thresholding.
        min_area_ratio: Minimum area ratio of card relative to original scan.

    Returns:
        Tuple of (cropped_image, bounding_box_tuple (x_min, y_min, x_max, y_max)).
    """
    im = _load_pil_image(image)
    w, h = im.size
    gray = im.convert("L")

    # Downsample for fast contour detection
    scale = min(1.0, 400.0 / max(w, h))
    sw = max(50, int(w * scale))
    sh = max(50, int(h * scale))
    small = gray.resize((sw, sh), Image.BILINEAR)

    # Sample border pixels (top, bottom, left, right edges) to determine background statistics
    border_pixels: List[int] = []
    for x in range(sw):
        border_pixels.append(small.getpixel((x, 0)))
        border_pixels.append(small.getpixel((x, sh - 1)))
    for y in range(sh):
        border_pixels.append(small.getpixel((0, y)))
        border_pixels.append(small.getpixel((sw - 1, y)))

    bg_mean = sum(border_pixels) / len(border_pixels)
    bg_std = math.sqrt(sum((p - bg_mean) ** 2 for p in border_pixels) / len(border_pixels))

    # Dark scanner bed detection (background mean is low, e.g. < 90)
    if bg_mean < 110:
        thresh = max(40, bg_mean + threshold_sigma * max(10.0, bg_std))
        bin_pixels = [1 if p > thresh else 0 for p in small.tobytes()]
    else:
        # Light / white background bed with possible dark shadow or border
        center_samples = [
            small.getpixel((x, y))
            for x in range(sw // 4, 3 * sw // 4, 5)
            for y in range(sh // 4, 3 * sh // 4, 5)
        ]
        center_mean = sum(center_samples) / len(center_samples) if center_samples else bg_mean

        if abs(center_mean - bg_mean) > 20:
            mid_val = (center_mean + bg_mean) / 2.0
            if center_mean > bg_mean:
                bin_pixels = [1 if p > mid_val else 0 for p in small.tobytes()]
            else:
                bin_pixels = [1 if p < mid_val else 0 for p in small.tobytes()]
        else:
            return im.copy(), (0, 0, w, h)

    # Project along columns and rows
    col_sums = [sum(bin_pixels[y * sw + x] for y in range(sh)) for x in range(sw)]
    row_sums = [sum(bin_pixels[y * sw + x] for x in range(sw)) for y in range(sh)]

    min_col_count = int(sh * 0.05)
    min_row_count = int(sw * 0.05)

    valid_cols = [i for i, v in enumerate(col_sums) if v > min_col_count]
    valid_rows = [i for i, v in enumerate(row_sums) if v > min_row_count]

    if not valid_cols or not valid_rows:
        return im.copy(), (0, 0, w, h)

    sx_min, sx_max = min(valid_cols), max(valid_cols)
    sy_min, sy_max = min(valid_rows), max(valid_rows)

    # Map back to original coordinate system
    inv_scale = 1.0 / scale
    x_min = max(0, int(sx_min * inv_scale) - padding_px)
    y_min = max(0, int(sy_min * inv_scale) - padding_px)
    x_max = min(w, int((sx_max + 1) * inv_scale) + padding_px)
    y_max = min(h, int((sy_max + 1) * inv_scale) + padding_px)

    crop_w = x_max - x_min
    crop_h = y_max - y_min
    crop_area = crop_w * crop_h
    total_area = w * h

    if crop_area < min_area_ratio * total_area or crop_w < 50 or crop_h < 50:
        return im.copy(), (0, 0, w, h)

    cropped = im.crop((x_min, y_min, x_max, y_max))
    return cropped, (x_min, y_min, x_max, y_max)


# ============================================================================
# 3. ADAPTIVE CONTRAST & SUBSTRATE NORMALIZATION (CLAHE & WHITE-BALANCE)
# ============================================================================

def normalize_white_balance(
    image: Image.Image,
    percentile_low: float = 1.0,
    percentile_high: float = 99.0,
) -> Image.Image:
    """Performs percentile-based white-balance normalization across color channels.

    Args:
        image: PIL Image in RGB or L mode.
        percentile_low: Lower percentile cutoff (default: 1.0).
        percentile_high: Upper percentile cutoff (default: 99.0).

    Returns:
        White-balanced PIL Image.
    """
    if image.mode in ("L", "LA"):
        im_l = image if image.mode == "L" else image.split()[0]
        hist = im_l.histogram()
        total = sum(hist)
        p_low_cnt = total * (percentile_low / 100.0)
        p_high_cnt = total * (percentile_high / 100.0)

        c_low = 0
        running = 0
        for val, count in enumerate(hist):
            running += count
            if running >= p_low_cnt:
                c_low = val
                break

        c_high = 255
        running = 0
        for val, count in enumerate(hist):
            running += count
            if running >= p_high_cnt:
                c_high = max(c_low + 1, val)
                break

        lut = [
            min(255, max(0, int(round(255.0 * (v - c_low) / (c_high - c_low)))))
            for v in range(256)
        ]
        balanced_l = im_l.point(lut)
        if image.mode == "LA":
            return Image.merge("LA", (balanced_l, image.split()[1]))
        return balanced_l

    rgb = image.convert("RGB")
    channels = rgb.split()
    balanced_channels: List[Image.Image] = []

    for ch in channels:
        hist = ch.histogram()
        total = sum(hist)
        p_low_cnt = total * (percentile_low / 100.0)
        p_high_cnt = total * (percentile_high / 100.0)

        c_low = 0
        running = 0
        for val, count in enumerate(hist):
            running += count
            if running >= p_low_cnt:
                c_low = val
                break

        c_high = 255
        running = 0
        for val, count in enumerate(hist):
            running += count
            if running >= p_high_cnt:
                c_high = max(c_low + 1, val)
                break

        span = max(1, c_high - c_low)
        lut = [
            min(255, max(0, int(round(255.0 * (v - c_low) / span))))
            for v in range(256)
        ]
        balanced_channels.append(ch.point(lut))

    if image.mode == "RGBA":
        alpha = image.split()[3]
        return Image.merge("RGBA", (*balanced_channels, alpha))
    return Image.merge("RGB", tuple(balanced_channels))


def apply_clahe(
    image: Image.Image,
    clip_limit: float = 2.0,
    tile_grid_size: Tuple[int, int] = (8, 8),
) -> Image.Image:
    """Applies Contrast Limited Adaptive Histogram Equalization (CLAHE).

    Dividing the image into a tile grid, limits local histogram spikes to clip_limit
    and applies bilinear interpolation between adjacent tiles to prevent block artifacts.

    Args:
        image: PIL Image (Grayscale 'L', RGB, or RGBA).
        clip_limit: Threshold factor for contrast limiting (default: 2.0).
        tile_grid_size: Grid division (M, N) (default: 8x8).

    Returns:
        Equalized PIL Image.
    """
    is_color = image.mode in ("RGB", "RGBA")
    work_im = image.convert("L")
    w, h = work_im.size
    grid_x = max(1, min(tile_grid_size[0], w))
    grid_y = max(1, min(tile_grid_size[1], h))

    tile_w = w / float(grid_x)
    tile_h = h / float(grid_y)
    tile_pixels = tile_w * tile_h
    clip_thresh = max(1, int(clip_limit * (tile_pixels / 256.0)))

    # Compute CDF for each tile
    cdfs: List[List[List[int]]] = []
    for j in range(grid_y):
        row_cdfs: List[List[int]] = []
        y0 = int(j * tile_h)
        y1 = int((j + 1) * tile_h) if j < grid_y - 1 else h
        for i in range(grid_x):
            x0 = int(i * tile_w)
            x1 = int((i + 1) * tile_w) if i < grid_x - 1 else w
            tile = work_im.crop((x0, y0, x1, y1))
            hist = tile.histogram()

            # Clip histogram
            clipped_excess = 0
            clipped_hist: List[float] = []
            for count in hist:
                if count > clip_thresh:
                    clipped_excess += count - clip_thresh
                    clipped_hist.append(float(clip_thresh))
                else:
                    clipped_hist.append(float(count))

            bonus = clipped_excess / 256.0
            total_p = sum(clipped_hist) + clipped_excess
            if total_p <= 0:
                total_p = 1.0

            cdf: List[int] = []
            running_sum = 0.0
            for c in clipped_hist:
                running_sum += c + bonus
                cdf.append(min(255, max(0, int(round(255.0 * running_sum / total_p)))))
            row_cdfs.append(cdf)
        cdfs.append(row_cdfs)

    # Precompute tile weights for bilinear interpolation
    x_weights: List[Tuple[int, int, float]] = []
    for x in range(w):
        gx = (x / tile_w) - 0.5
        i0 = int(math.floor(gx))
        wx = max(0.0, min(1.0, gx - i0))
        i0 = max(0, min(grid_x - 1, i0))
        i1 = max(0, min(grid_x - 1, i0 + 1))
        x_weights.append((i0, i1, wx))

    y_weights: List[Tuple[int, int, float]] = []
    for y in range(h):
        gy = (y / tile_h) - 0.5
        j0 = int(math.floor(gy))
        wy = max(0.0, min(1.0, gy - j0))
        j0 = max(0, min(grid_y - 1, j0))
        j1 = max(0, min(grid_y - 1, j0 + 1))
        y_weights.append((j0, j1, wy))

    raw = work_im.tobytes()
    out = bytearray(len(raw))

    idx = 0
    for y in range(h):
        j0, j1, wy = y_weights[y]
        cdf_j0 = cdfs[j0]
        cdf_j1 = cdfs[j1]
        w_top = 1.0 - wy
        w_bot = wy
        for x in range(w):
            val = raw[idx]
            i0, i1, wx = x_weights[x]
            w_left = 1.0 - wx
            w_right = wx

            v00 = cdf_j0[i0][val]
            v10 = cdf_j0[i1][val]
            v01 = cdf_j1[i0][val]
            v11 = cdf_j1[i1][val]

            v_out = int(
                round(
                    (v00 * w_left + v10 * w_right) * w_top
                    + (v01 * w_left + v11 * w_right) * w_bot
                )
            )
            out[idx] = min(255, max(0, v_out))
            idx += 1

    enhanced_l = Image.frombytes("L", (w, h), bytes(out))

    if is_color:
        # Blend enhanced L with original color channels to preserve ink hue
        orig_rgb = image.convert("RGB")
        orig_l = work_im
        r_ch, g_ch, b_ch = orig_rgb.split()
        r_raw = r_ch.tobytes()
        g_raw = g_ch.tobytes()
        b_raw = b_ch.tobytes()
        l_orig_raw = orig_l.tobytes()
        l_enh_raw = bytes(out)

        r_out = bytearray(len(r_raw))
        g_out = bytearray(len(g_raw))
        b_out = bytearray(len(b_raw))

        for k in range(len(r_raw)):
            lo = l_orig_raw[k]
            le = l_enh_raw[k]
            if lo > 0:
                ratio = le / float(lo)
                r_out[k] = min(255, max(0, int(round(r_raw[k] * ratio))))
                g_out[k] = min(255, max(0, int(round(g_raw[k] * ratio))))
                b_out[k] = min(255, max(0, int(round(b_raw[k] * ratio))))
            else:
                r_out[k] = le
                g_out[k] = le
                b_out[k] = le

        r_img = Image.frombytes("L", (w, h), bytes(r_out))
        g_img = Image.frombytes("L", (w, h), bytes(g_out))
        b_img = Image.frombytes("L", (w, h), bytes(b_out))
        if image.mode == "RGBA":
            alpha = image.split()[3]
            return Image.merge("RGBA", (r_img, g_img, b_img, alpha))
        return Image.merge("RGB", (r_img, g_img, b_img))

    if image.mode == "LA":
        alpha = image.split()[1]
        return Image.merge("LA", (enhanced_l, alpha))

    return enhanced_l


def enhance_card_readability(
    image: Union[Image.Image, Path, str, bytes],
    clip_limit: float = 2.0,
    tile_grid_size: Tuple[int, int] = (8, 8),
    white_balance: bool = True,
) -> Image.Image:
    """Enhances scanned card readability using CLAHE and white-balance normalization.

    Makes faint pencil handwriting and low-contrast cursive readable while
    preserving stroke integrity.

    Args:
        image: PIL Image, path, or bytes.
        clip_limit: Contrast clipping limit (default: 2.0).
        tile_grid_size: Grid tile division (default: 8x8).
        white_balance: Whether to apply white-balance normalization first.

    Returns:
        Enhanced PIL Image.
    """
    im = _load_pil_image(image)
    if white_balance:
        im = normalize_white_balance(im)
    return apply_clahe(im, clip_limit=clip_limit, tile_grid_size=tile_grid_size)


# ============================================================================
# 4. SUBSTRATE COLORIMETRIC PALETTE CLUSTERING
# ============================================================================

def extract_paper_color_palette(
    image: Union[Image.Image, Path, str, bytes],
    num_colors: int = 4,
    sample_size: int = 1500,
    seed: int = 42,
) -> Dict[str, Any]:
    """Extracts dominant paper and ink color centroids in CIELAB space.

    Args:
        image: PIL Image, path, or bytes.
        num_colors: Number of color clusters to extract (default: 4).
        sample_size: Number of pixel samples to evaluate (default: 1500).
        seed: Random seed for deterministic clustering initialization.

    Returns:
        Dictionary containing paper_centroid, ink_centroids, and complete palette:
        {
            "paper_centroid": {
                "hex": "#F4EBD0",
                "lab": (92.1, -1.5, 14.8),
                "rgb": (244, 235, 208),
                "proportion": 0.78,
                "tone_name": "Aged Cream",
            },
            "ink_centroids": [...],
            "palette": [...]
        }
    """
    im = _load_pil_image(image).convert("RGB")
    w, h = im.size

    # Sample random pixels
    sample_size = max(1, sample_size)
    n_samples = max(1, min(sample_size, w * h))
    rng = random.Random(seed)
    sampled_coords = [
        (rng.randint(0, w - 1), rng.randint(0, h - 1)) for _ in range(n_samples)
    ]
    samples_rgb = [im.getpixel(pt) for pt in sampled_coords]
    samples_lab = [rgb_to_lab(r, g, b) for r, g, b in samples_rgb]

    # K-Means clustering in CIELAB space
    k = max(2, min(8, num_colors))
    step = max(1, len(samples_lab) // k)
    centroids = [samples_lab[min(len(samples_lab) - 1, i * step)] for i in range(k)]

    for _ in range(20):
        clusters: List[List[Tuple[float, float, float]]] = [[] for _ in range(k)]
        for pt in samples_lab:
            best_idx = 0
            min_dist = float("inf")
            for ci, c in enumerate(centroids):
                d = (pt[0] - c[0]) ** 2 + (pt[1] - c[1]) ** 2 + (pt[2] - c[2]) ** 2
                if d < min_dist:
                    min_dist = d
                    best_idx = ci
            clusters[best_idx].append(pt)

        new_centroids: List[Tuple[float, float, float]] = []
        for ci in range(k):
            if clusters[ci]:
                n = len(clusters[ci])
                mean_l = sum(p[0] for p in clusters[ci]) / n
                mean_a = sum(p[1] for p in clusters[ci]) / n
                mean_b = sum(p[2] for p in clusters[ci]) / n
                new_centroids.append(
                    (round(mean_l, 2), round(mean_a, 2), round(mean_b, 2))
                )
            else:
                new_centroids.append(centroids[ci])

        shift = sum(
            (c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2 + (c1[2] - c2[2]) ** 2
            for c1, c2 in zip(centroids, new_centroids)
        )
        centroids = new_centroids
        if shift < 0.01:
            break

    # Calculate cluster proportions and RGB/Hex values
    palette_entries: List[Dict[str, Any]] = []
    for ci, centroid in enumerate(centroids):
        prop = len(clusters[ci]) / float(n_samples)
        r, g, b = lab_to_rgb(centroid[0], centroid[1], centroid[2])
        hex_str = f"#{r:02X}{g:02X}{b:02X}"

        lab_l, lab_a, lab_b = centroid
        if lab_l > 92.0 and abs(lab_a) < 4 and abs(lab_b) < 8:
            tone_name = "White"
        elif lab_b > 20.0 or (r > 190 and g < 180 and b < 140):
            tone_name = "Kraft Brown"
        elif lab_b > 10.0:
            tone_name = "Aged Cream"
        elif lab_l < 45.0:
            tone_name = "Dark Ink"
        else:
            tone_name = "Off-White"

        palette_entries.append({
            "hex": hex_str,
            "lab": centroid,
            "rgb": (r, g, b),
            "proportion": round(prop, 4),
            "tone_name": tone_name,
        })

    # Sort palette by lightness L* descending
    palette_entries.sort(key=lambda item: item["lab"][0], reverse=True)

    paper_candidates = [p for p in palette_entries if p["lab"][0] >= 65.0]
    if paper_candidates:
        paper_centroid = max(paper_candidates, key=lambda p: p["proportion"])
    else:
        paper_centroid = palette_entries[0]

    paper_l = paper_centroid["lab"][0]
    ink_centroids = [
        p
        for p in palette_entries
        if (paper_l - p["lab"][0]) >= 25.0 and p["proportion"] >= 0.005
    ]

    return {
        "paper_centroid": paper_centroid,
        "ink_centroids": ink_centroids,
        "palette": palette_entries,
    }
