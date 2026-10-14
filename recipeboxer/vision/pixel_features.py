"""Pixel-level computer vision feature extraction for physical recipe cards (WP-G2).

Extracts low-level physical card properties directly from scanned/captured images:
- Card segmentation and border cropping
- Orientation deskew angle detection
- Paper substrate colorimetry (CIELAB mean and std on non-ink pixels)
- Ruling line detection (spacing via autocorrelation/FFT of row profile, angle, kind)
- Dominant ink hue clustering (circular k-means on ink pixels)
- Aspect ratio and long edge dimensions
- In-process LRU-cached stain mask keyed by image SHA256
"""

from __future__ import annotations

import collections
import hashlib
import io
import math
from pathlib import Path
from typing import Any, List, Optional, Tuple, Union

import numpy as np
from PIL import Image
from scipy.ndimage import binary_opening
from scipy.signal import find_peaks

from recipeboxer.models.features import PixelFeatures
from recipeboxer.vision.heuristics import rgb_to_lab
from recipeboxer.vision.preprocessing import (
    crop_card_borders,
    deskew_card_image,
    detect_skew_angle,
)


# ============================================================================
# 1. IN-PROCESS LRU CACHE FOR STAIN MASKS
# ============================================================================

class StainMaskLRU:
    """Thread-safe in-process LRU cache for 2D binary stain masks."""

    def __init__(self, maxsize: int = 256) -> None:
        self.maxsize = maxsize
        self._cache: collections.OrderedDict[str, np.ndarray] = collections.OrderedDict()

    def get(self, key: str) -> Optional[np.ndarray]:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, key: str, mask: np.ndarray) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = mask
        if len(self._cache) > self.maxsize:
            self._cache.popitem(last=False)

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)

    def __contains__(self, key: str) -> bool:
        return key in self._cache


_STAIN_CACHE = StainMaskLRU(maxsize=256)


def get_stain_mask(ref: str) -> Optional[np.ndarray]:
    """Retrieves a cached stain mask by its session reference key."""
    return _STAIN_CACHE.get(ref)


def store_stain_mask(ref: str, mask: np.ndarray) -> None:
    """Stores a stain mask into the in-process LRU cache."""
    _STAIN_CACHE.put(ref, mask)


def clear_stain_cache() -> None:
    """Clears all cached stain masks from the in-process LRU."""
    _STAIN_CACHE.clear()


# ============================================================================
# 2. COLOR SPACE CONVERSION UTILITIES
# ============================================================================

def rgb_array_to_lab(rgb: np.ndarray) -> np.ndarray:
    """Vectorized conversion of sRGB array (N, 3) in [0, 255] to CIE L*a*b* coordinates.

    Matches `recipeboxer.vision.heuristics.rgb_to_lab` standard observer equations.

    Args:
        rgb: Numpy float array of shape (N, 3) with values in range [0, 255].

    Returns:
        Numpy float array of shape (N, 3) with (L*, a*, b*) coordinates.
    """
    rgb_lin = rgb.astype(np.float32) / 255.0
    mask = rgb_lin <= 0.04045
    rgb_lin = np.where(mask, rgb_lin / 12.92, ((rgb_lin + 0.055) / 1.055) ** 2.4)

    # Conversion matrix to CIE XYZ (D65 standard observer)
    M = np.array([
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ], dtype=np.float32)

    xyz = rgb_lin @ M.T
    white = np.array([0.95047, 1.00000, 1.08883], dtype=np.float32)
    xyz_r = xyz / white

    mask_f = xyz_r > 0.008856
    f_xyz = np.where(mask_f, xyz_r ** (1.0 / 3.0), (7.787 * xyz_r) + (16.0 / 116.0))

    fx, fy, fz = f_xyz[:, 0], f_xyz[:, 1], f_xyz[:, 2]
    lab_l = (116.0 * fy) - 16.0
    lab_a = 500.0 * (fx - fy)
    lab_b = 200.0 * (fy - fz)

    return np.column_stack([lab_l, lab_a, lab_b])


def _load_image(image: Union[Image.Image, Path, str, bytes]) -> Image.Image:
    """Normalizes input representation into a PIL Image instance."""
    if isinstance(image, Image.Image):
        return image.copy()
    if isinstance(image, (str, Path)):
        p = Path(image)
        if not p.exists():
            raise FileNotFoundError(f"Image path not found: {p}")
        with Image.open(p) as im:
            return im.copy()
    if isinstance(image, bytes):
        if len(image) == 0:
            raise ValueError("Empty image bytes passed.")
        return Image.open(io.BytesIO(image)).copy()
    raise TypeError(f"Unsupported image input type: {type(image)}")


# ============================================================================
# 3. RULING LINE ANALYSIS & KIND DETECTION
# ============================================================================

def analyze_ruling(
    deskewed_card: Image.Image,
    deskew_angle: float,
) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """Detects ruling spacing, orientation angle, and pattern kind.

    Uses autocorrelation and FFT of horizontal row profiles and vertical
    column profiles, combined with header line detection.

    Args:
        deskewed_card: Deskewed PIL Image of the card.
        deskew_angle: Angle in degrees by which the card was rotated.

    Returns:
        Tuple of (ruling_spacing_px, ruling_angle_deg, ruling_kind).
        ruling_kind is one of: "none", "lined", "grid", "double".
    """
    dw, dh = deskewed_card.size
    if dw < 40 or dh < 40:
        return None, None, "none"

    # Scale width down to max 400 for speed while preserving exact vertical height
    r_scale_w = min(1.0, 400.0 / float(dw))
    r_w = max(40, int(dw * r_scale_w))
    r_img = deskewed_card.resize((r_w, dh), Image.BILINEAR)

    rgb = np.array(r_img.convert("RGB"), dtype=np.float32)
    gray = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]

    # Row profile across central columns (excluding margins)
    margin_w = max(5, int(r_w * 0.08))
    center_crop = gray[:, margin_w:r_w - margin_w]
    row_profile = np.mean(center_crop, axis=1)

    # Detrend row profile using moving average high-pass filter
    kernel_size = min(51, len(row_profile) // 3 * 2 + 1)
    if kernel_size >= 7:
        kernel = np.ones(kernel_size) / float(kernel_size)
        trend = np.convolve(row_profile, kernel, mode="same")
        pad = kernel_size // 2
        trend[:pad] = trend[pad]
        trend[-pad:] = trend[-pad - 1]
        highpass = -(row_profile - trend)
    else:
        highpass = -(row_profile - np.mean(row_profile))

    sig_std = float(np.std(highpass))
    row_period: Optional[float] = None
    row_peak_val: float = 0.0

    if sig_std > 0.08:
        norm_sig = (highpass - np.mean(highpass)) / sig_std
        ac = np.correlate(norm_sig, norm_sig, mode="full")[len(norm_sig) - 1:]
        if ac[0] > 0:
            ac /= ac[0]

        min_lag = 12
        max_lag = min(120, len(ac) - 2)
        if max_lag > min_lag:
            search_slice = ac[min_lag:max_lag]
            peaks, _ = find_peaks(search_slice, height=0.20, distance=6)
            if len(peaks) > 0:
                first_lag = peaks[0] + min_lag
                # Subpixel refinement using parabolic interpolation
                i = first_lag
                if 0 < i < len(ac) - 1:
                    y0, y1, y2 = ac[i - 1], ac[i], ac[i + 1]
                    denom = 2.0 * (y0 - 2.0 * y1 + y2)
                    if abs(denom) > 1e-6:
                        first_lag = i + (y0 - y2) / denom
                row_period = round(float(first_lag), 2)
                row_peak_val = float(ac[int(round(first_lag))])

    # Check vertical column profile to identify grid patterns
    col_crop = gray[int(dh * 0.12):int(dh * 0.88), :]
    col_profile = np.mean(col_crop, axis=0)
    col_kernel_size = min(51, len(col_profile) // 3 * 2 + 1)
    if col_kernel_size >= 7:
        col_kernel = np.ones(col_kernel_size) / float(col_kernel_size)
        col_trend = np.convolve(col_profile, col_kernel, mode="same")
        cpad = col_kernel_size // 2
        col_trend[:cpad] = col_trend[cpad]
        col_trend[-cpad:] = col_trend[-cpad - 1]
        col_hp = -(col_profile - col_trend)
    else:
        col_hp = -(col_profile - np.mean(col_profile))

    col_std = float(np.std(col_hp))
    is_grid = False
    if col_std > 0.10:
        col_norm = (col_hp - np.mean(col_hp)) / col_std
        col_ac = np.correlate(col_norm, col_norm, mode="full")[len(col_norm) - 1:]
        if col_ac[0] > 0:
            col_ac /= col_ac[0]
        col_min_lag = max(10, int(12 * r_scale_w))
        col_max_lag = min(int(100 * r_scale_w), len(col_ac) - 2)
        if col_max_lag > col_min_lag:
            col_peaks, _ = find_peaks(col_ac[col_min_lag:col_max_lag], height=0.30, distance=4)
            if len(col_peaks) > 0 and row_period is not None:
                is_grid = True

    if row_period is None:
        return None, None, "none"

    if is_grid:
        return row_period, deskew_angle, "grid"

    # Check for double header lines in top 30% of card
    top_h = int(min(140, dh * 0.35))
    header_rgb = rgb[:top_h, margin_w:r_w - margin_w]
    # Red line prominence: R exceeds max(G, B)
    redness = header_rgb[:, :, 0] - np.maximum(header_rgb[:, :, 1], header_rgb[:, :, 2])
    red_row = np.mean(redness > 15, axis=1)

    red_peaks, _ = find_peaks(red_row, height=0.15, distance=4)
    is_double = False
    if len(red_peaks) >= 2:
        for p_idx in range(len(red_peaks) - 1):
            gap = red_peaks[p_idx + 1] - red_peaks[p_idx]
            if 6 <= gap <= 20:
                is_double = True
                break

    kind = "double" if is_double else "lined"
    return row_period, deskew_angle, kind


# ============================================================================
# 4. INK HUE CLUSTERING
# ============================================================================

def cluster_ink_hues(
    card_rgb: np.ndarray,
    paper_median_lum: float,
    max_k: int = 3,
) -> List[float]:
    """Clusters dominant ink hues on ink pixels using deterministic circular statistics.

    Uses weighted circular histogram peak detection and circular basin refinement
    to isolate true pigment hues while filtering antialiased edge blending.

    Args:
        card_rgb: Card image RGB array (H, W, 3) in [0, 255].
        paper_median_lum: Estimated median luminance of the paper substrate.
        max_k: Maximum number of hue clusters to return (<= 3).

    Returns:
        List of dominant ink hues in degrees [0.0, 360.0).
    """
    lum = 0.299 * card_rgb[:, :, 0] + 0.587 * card_rgb[:, :, 1] + 0.114 * card_rgb[:, :, 2]
    # Ink pixels: substantially darker than substrate
    ink_mask = lum < max(20.0, paper_median_lum - 20.0)
    ink_pixels = card_rgb[ink_mask]

    if len(ink_pixels) < 25:
        return []

    # Deterministic subsampling without random state
    if len(ink_pixels) > 5000:
        step = max(1, len(ink_pixels) // 5000)
        ink_pixels = ink_pixels[::step][:5000]

    r = ink_pixels[:, 0] / 255.0
    g = ink_pixels[:, 1] / 255.0
    b = ink_pixels[:, 2] / 255.0

    cmax = np.maximum(np.maximum(r, g), b)
    cmin = np.minimum(np.minimum(r, g), b)
    delta = cmax - cmin

    # Saturation
    sat = np.where(cmax > 0, delta / cmax, 0.0)

    # Hue computation
    hue = np.zeros_like(r)
    mask_r = (cmax == r) & (delta > 1e-4)
    mask_g = (cmax == g) & (delta > 1e-4)
    mask_b = (cmax == b) & (delta > 1e-4)

    hue[mask_r] = (60.0 * ((g[mask_r] - b[mask_r]) / delta[mask_r]) + 360.0) % 360.0
    hue[mask_g] = (60.0 * ((b[mask_g] - r[mask_g]) / delta[mask_g]) + 120.0) % 360.0
    hue[mask_b] = (60.0 * ((r[mask_b] - g[mask_b]) / delta[mask_b]) + 240.0) % 360.0

    # Filter for chromatic pixels and prioritize core ink strokes over edge blending
    chromatic = sat > 0.15
    if np.sum(chromatic) >= 20:
        target_hue = hue[chromatic]
        target_sat = sat[chromatic]
        sat_thresh = float(np.percentile(target_sat, 30))
        core_mask = target_sat >= sat_thresh
        target_hue = target_hue[core_mask]
        target_sat = target_sat[core_mask]
    else:
        target_hue = hue
        target_sat = np.ones_like(hue)

    if len(target_hue) == 0:
        return []

    # Weighted circular histogram (72 bins, 5 degrees each)
    num_bins = 72
    bin_size = 360.0 / num_bins
    bin_indices = np.floor(target_hue / bin_size).astype(int) % num_bins
    weights = np.bincount(bin_indices, weights=target_sat, minlength=num_bins)

    # Circular smoothing with 3-tap filter [1, 2, 1]
    kernel = np.array([1.0, 2.0, 1.0], dtype=np.float32) / 4.0
    padded = np.concatenate([weights[-1:], weights, weights[:1]])
    smoothed = np.convolve(padded, kernel, mode="same")[1:-1]

    # Peak detection across circular bins
    max_val = float(np.max(smoothed))
    if max_val <= 0:
        return []

    peaks: List[Tuple[int, float]] = []
    for i in range(num_bins):
        prev_i = (i - 1) % num_bins
        next_i = (i + 1) % num_bins
        if smoothed[i] >= smoothed[prev_i] and smoothed[i] >= smoothed[next_i]:
            if smoothed[i] >= 0.08 * max_val:
                peaks.append((i, float(smoothed[i])))

    if not peaks:
        return []

    peaks.sort(key=lambda item: item[1], reverse=True)

    selected_hues: List[float] = []
    for bin_idx, p_val in peaks:
        peak_deg = (bin_idx + 0.5) * bin_size
        # Minimum angular separation between distinct color clusters (30 degrees)
        if any(
            min(abs(peak_deg - sel), 360.0 - abs(peak_deg - sel)) < 30.0
            for sel in selected_hues
        ):
            continue

        # Refine hue using circular mean of pixels in peak basin (+- 20 degrees)
        dists = np.minimum(np.abs(target_hue - peak_deg), 360.0 - np.abs(target_hue - peak_deg))
        in_basin = dists <= 20.0
        if np.sum(in_basin) > 0:
            rads = np.radians(target_hue[in_basin])
            basin_w = target_sat[in_basin]
            sin_mean = float(np.sum(np.sin(rads) * basin_w))
            cos_mean = float(np.sum(np.cos(rads) * basin_w))
            refined_deg = (np.degrees(np.arctan2(sin_mean, cos_mean)) + 360.0) % 360.0
            selected_hues.append(round(refined_deg, 1))
        else:
            selected_hues.append(round(peak_deg, 1))

        if len(selected_hues) >= max_k:
            break

    return selected_hues


# ============================================================================
# 5. STAIN MASK COMPUTATION
# ============================================================================

def compute_stain_mask(
    card_rgb: np.ndarray,
    paper_lab: Tuple[float, float, float],
    paper_lab_std: Tuple[float, float, float],
) -> np.ndarray:
    """Computes a 2D binary stain mask isolating liquid rings and grease smudges.

    Args:
        card_rgb: Card RGB array (H, W, 3) in [0, 255].
        paper_lab: Mean (L, a, b) of the clean paper substrate.
        paper_lab_std: Standard deviation of paper substrate.

    Returns:
        2D boolean array of shape (H, W) where True represents stain regions.
    """
    card_lum = 0.299 * card_rgb[:, :, 0] + 0.587 * card_rgb[:, :, 1] + 0.114 * card_rgb[:, :, 2]
    # Estimate baseline substrate color from high-luminance pixels
    bright_mask = card_lum >= np.percentile(card_lum, 40)
    if np.sum(bright_mask) > 100:
        paper_rgb = np.median(card_rgb[bright_mask], axis=0)
    else:
        paper_rgb = np.median(card_rgb.reshape(-1, 3), axis=0)

    # Euclidean color difference from clean substrate
    color_diff = np.sqrt(np.sum((card_rgb - paper_rgb) ** 2, axis=2))

    # Stains are darker or discolored relative to clean paper, but not black ink
    # Typically: diff > 13.0 and lum >= 55.0 (excluding dark ink text)
    stain_candidates = (color_diff > 13.0) & (card_lum >= 55.0)

    # Morphological noise cleanup (removes single-pixel noise and isolated ruling lines)
    cleaned = binary_opening(stain_candidates, structure=np.ones((3, 3), dtype=bool))
    return cleaned



# ============================================================================
# 6. MAIN HIGH-LEVEL API: COMPUTE_PIXEL_FEATURES
# ============================================================================

def compute_pixel_features(
    image: Union[Image.Image, Path, str, bytes],
) -> PixelFeatures:
    """Extracts deterministic physical pixel features from a recipe card image.

    Implements WAVE4_CONTRACTS.md §1.4 specifications:
    - Card segmentation / auto-crop from scanner or tabletop borders
    - Orientation deskew detection
    - Paper CIELAB mean and std on non-ink pixels
    - Ruling spacing, angle, and kind (none|lined|grid|double)
    - Ink hue clustering (k <= 3)
    - Aspect ratio and long edge dimensions
    - In-process LRU-cached stain mask keyed by image SHA256

    Args:
        image: PIL Image, path, or bytes.

    Returns:
        PixelFeatures instance.
    """
    pil_im = _load_image(image)
    orig_w, orig_h = pil_im.size
    long_edge_px = max(orig_w, orig_h)

    # 1. Deterministic image SHA256 hash for stain mask caching
    raw_bytes = pil_im.tobytes()
    image_sha = hashlib.sha256(raw_bytes).hexdigest()

    # 2. Card segmentation & border cropping
    cropped_card, crop_box_coords = crop_card_borders(pil_im)
    is_cropped = (
        crop_box_coords[0] > 0
        or crop_box_coords[1] > 0
        or crop_box_coords[2] < orig_w
        or crop_box_coords[3] < orig_h
    )
    crop_box: Optional[Tuple[int, int, int, int]] = (
        crop_box_coords if is_cropped else None
    )

    card_w, card_h = cropped_card.size
    aspect_ratio = round(card_w / float(card_h), 4)

    # 3. Deskew orientation detection
    deskew_angle = detect_skew_angle(cropped_card)
    if abs(deskew_angle) >= 0.2:
        deskewed_card = cropped_card.rotate(
            -deskew_angle,
            resample=Image.BILINEAR,
            expand=True,
            fillcolor=(255, 255, 255),
        )
    else:
        deskewed_card = cropped_card


    # 4. Ruling line detection on deskewed card
    ruling_spacing, ruling_angle, ruling_kind = analyze_ruling(
        deskewed_card=deskewed_card,
        deskew_angle=deskew_angle,
    )

    # 5. Paper LAB mean/std and ink hue clustering on cropped card
    # Subsample pixels directly with deterministic striding rather than bilinear resizing
    # to avoid color bleeding between thin ink strokes and paper substrate.
    stride = max(1, int(math.ceil(max(card_w, card_h) / 600.0)))
    card_rgb = np.array(cropped_card.convert("RGB"), dtype=np.float32)[::stride, ::stride]
    card_lum = 0.299 * card_rgb[:, :, 0] + 0.587 * card_rgb[:, :, 1] + 0.114 * card_rgb[:, :, 2]
    paper_median_lum = float(np.median(card_lum))


    # Non-ink mask: pixels close to the background substrate
    non_ink_mask = card_lum >= max(40.0, paper_median_lum - 12.0)
    paper_pixels = card_rgb[non_ink_mask]

    if len(paper_pixels) > 5000:
        step = max(1, len(paper_pixels) // 5000)
        paper_sample = paper_pixels[::step][:5000]
    elif len(paper_pixels) > 0:
        paper_sample = paper_pixels
    else:
        paper_sample = card_rgb.reshape(-1, 3)

    labs = rgb_array_to_lab(paper_sample)
    paper_lab = (
        round(float(np.mean(labs[:, 0])), 2),
        round(float(np.mean(labs[:, 1])), 2),
        round(float(np.mean(labs[:, 2])), 2),
    )
    paper_lab_std = (
        round(float(np.std(labs[:, 0])), 2),
        round(float(np.std(labs[:, 1])), 2),
        round(float(np.std(labs[:, 2])), 2),
    )

    # 6. Ink hue clustering (k <= 3)
    ink_hues = cluster_ink_hues(
        card_rgb=card_rgb,
        paper_median_lum=paper_median_lum,
        max_k=3,
    )

    # 7. Stain mask generation and caching
    stain_mask_ref = f"stain_{image_sha}"
    stain_mask = compute_stain_mask(
        card_rgb=card_rgb,
        paper_lab=paper_lab,
        paper_lab_std=paper_lab_std,
    )
    store_stain_mask(stain_mask_ref, stain_mask)

    return PixelFeatures(
        paper_lab=paper_lab,
        paper_lab_std=paper_lab_std,
        ruling_spacing_px=ruling_spacing,
        ruling_angle_deg=ruling_angle,
        ruling_kind=ruling_kind,
        ink_hues=ink_hues,
        aspect_ratio=aspect_ratio,
        long_edge_px=long_edge_px,
        deskew_angle_deg=deskew_angle,
        crop_box=crop_box,
        stain_mask_ref=stain_mask_ref,
    )
