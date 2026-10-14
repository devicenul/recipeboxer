"""Image preparation pipeline for recipe extraction (WP-X2).

Provides deterministic image normalization, border cropping, deskewing,
long-edge capping, contrast evaluation, and CLAHE enhancement per WAVE 4 Contracts §1.8.

Pipeline stages per card face:
1. EXIF orientation normalization via PIL ImageOps.exif_transpose.
2. Card border auto-cropping (stripping dark scanner beds and borders).
3. Auto-deskewing for small angular misalignments ([-10°, +10°]).
4. Long-edge capping per profile_params.get('image_long_edge', 1600).
5. Measured pixel contrast evaluation (faded/low-contrast detection).
6. Optional or auto CLAHE contrast equalization (vectorized NumPy implementation).
7. High-quality JPEG q~90 re-encoding (with PNG fallback for low-color typewritten cards).
"""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from pydantic import BaseModel, ConfigDict, Field

# Constants for variants
VARIANT_STANDARD = "standard"
VARIANT_ENHANCED_CLAHE = "enhanced_clahe"

# Optional import of preprocessing helpers; fallbacks provided below for self-sufficiency
try:
    from recipeboxer.vision.preprocessing import (
        crop_card_borders as _external_crop_card_borders,
        deskew_card_image as _external_deskew_card_image,
    )
except Exception:
    _external_crop_card_borders = None
    _external_deskew_card_image = None


class PreparedFace(BaseModel):
    """Normalized, cropped, deskewed, and encoded card face ready for LLM extraction.

    Attributes:
        face_index: 0-based face index corresponding to card sequence (0 .. n_faces - 1).
        n_faces: Total number of physical card faces in the recipe cluster.
        data: Encoded binary image bytes (JPEG or PNG).
        mime_type: Valid MIME type matching data magic bytes ("image/jpeg" or "image/png").
        variant: "standard" for primary card face, or "enhanced_clahe" for contrast-enhanced copy.
        long_edge_px: Length of the longest edge in pixels after capping.
        label: Exact label string 'Face i of N' or 'Face i of N (contrast-enhanced copy)'.
    """

    model_config = ConfigDict(extra="ignore")

    face_index: int = Field(..., description="0-based face index (0 to n_faces - 1)")
    n_faces: int = Field(..., description="Total card faces in this recipe")
    data: bytes = Field(..., description="Encoded image bytes")
    mime_type: str = Field(..., description="Image MIME type (image/jpeg or image/png)")
    variant: str = Field(
        ..., description="Face representation variant: 'standard' or 'enhanced_clahe'"
    )
    long_edge_px: int = Field(..., description="Image long edge dimension in pixels")
    label: str = Field(
        ...,
        description="Prompt-ready face label ('Face i of N' or 'Face i of N (contrast-enhanced copy)')",
    )

    @property
    def face_number(self) -> int:
        """1-based face number i matching 'Face i of N'."""
        return self.face_index + 1

    @property
    def is_enhanced(self) -> bool:
        """Returns True if this face is a contrast-enhanced copy."""
        return self.variant == VARIANT_ENHANCED_CLAHE


# ============================================================================
# 1. BORDER AUTO-CROPPING (Self-sufficient with external fallback)
# ============================================================================

def crop_card_borders(
    image: Image.Image,
    padding_px: int = 10,
    min_area_ratio: float = 0.20,
) -> Tuple[Image.Image, Tuple[int, int, int, int]]:
    """Crops dark scanner beds and high-contrast margins around the recipe card.

    Delegates to recipeboxer.vision.preprocessing.crop_card_borders if available,
    or runs an internal projection-based fallback.

    Args:
        image: PIL Image to crop.
        padding_px: Pixel padding to preserve around the detected card boundaries.
        min_area_ratio: Minimum area fraction of detected card relative to canvas.

    Returns:
        Tuple of (cropped_image, bounding_box_tuple (x_min, y_min, x_max, y_max)).
    """
    if _external_crop_card_borders is not None:
        try:
            return _external_crop_card_borders(
                image, padding_px=padding_px, min_area_ratio=min_area_ratio
            )
        except Exception:
            pass

    # Self-sufficient fallback implementation
    w, h = image.size
    gray = image.convert("L")

    # Downsample for speed
    scale = min(1.0, 400.0 / max(w, h))
    sw = max(50, int(w * scale))
    sh = max(50, int(h * scale))
    small = gray.resize((sw, sh), Image.BILINEAR)

    # Sample boundary pixels
    border_pixels: List[int] = []
    for x in range(sw):
        border_pixels.append(small.getpixel((x, 0)))
        border_pixels.append(small.getpixel((x, sh - 1)))
    for y in range(sh):
        border_pixels.append(small.getpixel((0, y)))
        border_pixels.append(small.getpixel((sw - 1, y)))

    bg_mean = sum(border_pixels) / len(border_pixels)
    bg_std = math.sqrt(
        sum((p - bg_mean) ** 2 for p in border_pixels) / len(border_pixels)
    )

    if bg_mean < 110:
        thresh = max(40.0, bg_mean + 2.0 * max(10.0, bg_std))
        bin_pixels = [1 if p > thresh else 0 for p in small.tobytes()]
    else:
        center_samples = [
            small.getpixel((x, y))
            for x in range(sw // 4, 3 * sw // 4, 5)
            for y in range(sh // 4, 3 * sh // 4, 5)
        ]
        center_mean = (
            sum(center_samples) / len(center_samples) if center_samples else bg_mean
        )
        if abs(center_mean - bg_mean) > 20:
            mid_val = (center_mean + bg_mean) / 2.0
            if center_mean > bg_mean:
                bin_pixels = [1 if p > mid_val else 0 for p in small.tobytes()]
            else:
                bin_pixels = [1 if p < mid_val else 0 for p in small.tobytes()]
        else:
            return image.copy(), (0, 0, w, h)

    col_sums = [sum(bin_pixels[y * sw + x] for y in range(sh)) for x in range(sw)]
    row_sums = [sum(bin_pixels[y * sw + x] for x in range(sw)) for y in range(sh)]

    min_col_count = int(sh * 0.05)
    min_row_count = int(sw * 0.05)

    valid_cols = [i for i, v in enumerate(col_sums) if v > min_col_count]
    valid_rows = [i for i, v in enumerate(row_sums) if v > min_row_count]

    if not valid_cols or not valid_rows:
        return image.copy(), (0, 0, w, h)

    sx_min, sx_max = min(valid_cols), max(valid_cols)
    sy_min, sy_max = min(valid_rows), max(valid_rows)

    inv_scale = 1.0 / scale
    x_min = max(0, int(sx_min * inv_scale) - padding_px)
    y_min = max(0, int(sy_min * inv_scale) - padding_px)
    x_max = min(w, int((sx_max + 1) * inv_scale) + padding_px)
    y_max = min(h, int((sy_max + 1) * inv_scale) + padding_px)

    crop_w = x_max - x_min
    crop_h = y_max - y_min
    if crop_w * crop_h < min_area_ratio * (w * h) or crop_w < 50 or crop_h < 50:
        return image.copy(), (0, 0, w, h)

    return image.crop((x_min, y_min, x_max, y_max)), (x_min, y_min, x_max, y_max)


# ============================================================================
# 2. AUTO-DESKEWING (Small angles [-10°, +10°])
# ============================================================================

def deskew_card_image(
    image: Image.Image,
    max_angle: float = 10.0,
    background_color: Tuple[int, int, int] = (255, 255, 255),
) -> Tuple[Image.Image, float]:
    """Detects and corrects small angular tilt using horizontal projection profile variance.

    Restricted to small angles (default ±10°) to prevent spurious 90°/180° rotations
    and preserve document geometry.

    Args:
        image: PIL Image to deskew.
        max_angle: Maximum search range in degrees (default: 10.0°).
        background_color: Fill color for expanded border regions.

    Returns:
        Tuple of (deskewed_pil_image, detected_skew_angle_deg).
    """
    if _external_deskew_card_image is not None:
        try:
            return _external_deskew_card_image(
                image, max_angle=max_angle, background_color=background_color
            )
        except Exception:
            pass

    # Self-sufficient fallback deskewer
    w, h = image.size
    gray = image.convert("L")

    scale = min(1.0, 400.0 / max(w, h))
    sw = max(50, int(w * scale))
    sh = max(50, int(h * scale))
    small = gray.resize((sw, sh), Image.BILINEAR)

    # Isolated ink binarization
    hist = small.histogram()
    total_p = sw * sh
    mean_lum = sum(i * c for i, c in enumerate(hist)) / float(total_p)
    std_lum = math.sqrt(
        sum(((i - mean_lum) ** 2) * c for i, c in enumerate(hist)) / float(total_p)
    )
    if std_lum < 5.0:
        return image.copy(), 0.0

    inv = ImageOps.invert(small)
    bin_img = inv.point(lambda p: 255 if p > 100 else 0, mode="L")
    if sum(bin_img.histogram()[1:]) < 20:
        return image.copy(), 0.0

    cw, ch = bin_img.size
    crop_box = (int(cw * 0.18), int(ch * 0.18), int(cw * 0.82), int(ch * 0.82))
    if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
        return image.copy(), 0.0

    best_angle = 0.0
    max_score = 0.0
    # Search coarse steps
    step = 0.5
    angles = [-max_angle + i * step for i in range(int(math.ceil(2 * max_angle / step)) + 1)]

    for a in angles:
        r_img = bin_img.rotate(a, resample=Image.NEAREST, expand=False, fillcolor=0)
        cropped = r_img.crop(crop_box)
        rw, rh = cropped.size
        raw = cropped.tobytes()
        row_sums = [sum(raw[y * rw : (y + 1) * rw]) for y in range(rh)]
        score = sum((row_sums[y + 1] - row_sums[y]) ** 2 for y in range(rh - 1))
        if score > max_score:
            max_score = score
            best_angle = a

    detected_angle = round(-best_angle, 2)
    if abs(detected_angle) < 0.2:
        return image.copy(), 0.0

    fill_val: Any = background_color if image.mode == "RGB" else 255
    deskewed = image.rotate(
        -detected_angle, resample=Image.BICUBIC, expand=True, fillcolor=fill_val
    )
    return deskewed, detected_angle


# ============================================================================
# 3. MEASURED CONTRAST METRIC (Faded / Low-Contrast Detection)
# ============================================================================

def is_faded_or_low_contrast(
    image: Image.Image,
    michelson_threshold: float = 0.28,
    diff_threshold: float = 70.0,
) -> bool:
    """Evaluates whether an image face is faded or low-contrast via measured pixel statistics.

    Strictly content-derived; never inspects filenames, metadata, or path strings.
    Measures the dynamic separation between the estimated paper substrate (90th percentile
    luminance) and the ink/graphite strokes (median of the darkest 0.1% pixels).

    A card is judged faded/low-contrast if either:
    1. Absolute luminance contrast diff < diff_threshold (e.g. 70 out of 255 levels).
    2. Michelson contrast (L_paper - L_ink) / (L_paper + L_ink) < michelson_threshold (0.28).

    Args:
        image: PIL Image to evaluate.
        michelson_threshold: Minimum acceptable Michelson contrast ratio.
        diff_threshold: Minimum acceptable absolute luminance span.

    Returns:
        True if the card face is judged faded or low-contrast, False otherwise.
    """
    gray = np.array(image.convert("L"), dtype=np.float32)
    flat = gray.ravel()
    if len(flat) == 0:
        return True

    # Background substrate luminance level (90th percentile)
    paper_l = float(np.percentile(flat, 90))

    # Ink luminance level (median of the darkest 0.1% pixels, min 10, max 100)
    k = max(10, min(100, len(flat) // 1000))
    if len(flat) <= k:
        darkest = flat
    else:
        darkest = np.partition(flat, k)[:k]
    ink_l = float(np.median(darkest))

    diff = max(0.0, paper_l - ink_l)
    denom = paper_l + ink_l + 1e-5
    michelson = diff / denom

    return bool(diff < diff_threshold or michelson < michelson_threshold)


# ============================================================================
# 4. ADAPTIVE CONTRAST EQUALIZATION (Vectorized NumPy CLAHE)
# ============================================================================

def apply_clahe(
    image: Image.Image,
    clip_limit: float = 2.0,
    grid_size: Tuple[int, int] = (8, 8),
) -> Image.Image:
    """Applies Contrast Limited Adaptive Histogram Equalization (CLAHE) in pure NumPy.

    Splits color images into YCbCr, applies adaptive histogram equalization with
    contrast clipping and dynamic range expansion to the luminance channel (Y),
    and bilinearly interpolates between tile centers to eliminate grid boundary artifacts.

    Args:
        image: PIL Image (RGB or L).
        clip_limit: Contrast clip limit threshold (default: 2.0).
        grid_size: Tile grid resolution (columns, rows) (default: 8x8).

    Returns:
        Enhanced PIL Image with sharpened stroke legibility.
    """
    is_color = image.mode in ("RGB", "RGBA")
    if is_color:
        work_rgb = image.convert("RGB")
        ycbcr = work_rgb.convert("YCbCr")
        y, cb, cr = ycbcr.split()
        img_arr = np.array(y, dtype=np.uint8)
    else:
        work_l = image.convert("L")
        img_arr = np.array(work_l, dtype=np.uint8)

    h, w = img_arr.shape
    if w < 16 or h < 16:
        # Fallback to standard Pillow histogram equalization for tiny images
        if is_color:
            y_eq = ImageOps.equalize(y)
            return Image.merge("YCbCr", (y_eq, cb, cr)).convert("RGB")
        return ImageOps.equalize(image.convert("L"))

    gx = max(2, min(grid_size[0], w // 8))
    gy = max(2, min(grid_size[1], h // 8))

    x_edges = np.linspace(0, w, gx + 1).astype(int)
    y_edges = np.linspace(0, h, gy + 1).astype(int)

    # Compute clipped CDFs for each grid tile
    cdfs = np.zeros((gy, gx, 256), dtype=np.float32)
    for j in range(gy):
        for i in range(gx):
            tile = img_arr[y_edges[j] : y_edges[j + 1], x_edges[i] : x_edges[i + 1]]
            tile_p = tile.size
            if tile_p == 0:
                cdfs[j, i] = np.arange(256, dtype=np.float32)
                continue

            hist, _ = np.histogram(tile, bins=256, range=(0, 256))
            clip_thresh = max(1.0, clip_limit * (tile_p / 256.0))
            clipped = np.minimum(hist, clip_thresh)
            excess = hist.sum() - clipped.sum()
            hist = clipped + (excess / 256.0)
            cdf = np.cumsum(hist)

            t_min = int(tile.min())
            t_max = int(tile.max())
            c_min = float(cdf[t_min])
            c_max = float(cdf[t_max])
            if c_max > c_min:
                scaled = (cdf - c_min) * (255.0 / (c_max - c_min))
            else:
                scaled = np.arange(256, dtype=np.float32)
            cdfs[j, i] = np.clip(scaled, 0.0, 255.0)

    # Precompute tile center coordinates for bilinear interpolation
    x_centers = (x_edges[:-1] + x_edges[1:]) / 2.0
    y_centers = (y_edges[:-1] + y_edges[1:]) / 2.0

    x_coords = np.arange(w)
    y_coords = np.arange(h)

    gx_idx = np.searchsorted(x_centers, x_coords) - 1
    gx_idx = np.clip(gx_idx, 0, gx - 2)
    x0 = x_centers[gx_idx]
    x1 = x_centers[gx_idx + 1]
    wx = np.clip((x_coords - x0) / (x1 - x0 + 1e-6), 0.0, 1.0).astype(np.float32)

    gy_idx = np.searchsorted(y_centers, y_coords) - 1
    gy_idx = np.clip(gy_idx, 0, gy - 2)
    y0 = y_centers[gy_idx]
    y1 = y_centers[gy_idx + 1]
    wy = np.clip((y_coords - y0) / (y1 - y0 + 1e-6), 0.0, 1.0).astype(np.float32)

    # Vectorized bilinear interpolation of pixel values across surrounding tile CDFs
    c00 = cdfs[gy_idx[:, None], gx_idx[None, :], img_arr]
    c01 = cdfs[gy_idx[:, None], (gx_idx + 1)[None, :], img_arr]
    c10 = cdfs[(gy_idx + 1)[:, None], gx_idx[None, :], img_arr]
    c11 = cdfs[(gy_idx + 1)[:, None], (gx_idx + 1)[None, :], img_arr]

    w_top = (1.0 - wy)[:, None]
    w_bot = wy[:, None]
    w_left = (1.0 - wx)[None, :]
    w_right = wx[None, :]

    top = c00 * w_left + c01 * w_right
    bot = c10 * w_left + c11 * w_right
    res = top * w_top + bot * w_bot
    res_uint8 = np.clip(np.round(res), 0, 255).astype(np.uint8)

    enh_y = Image.fromarray(res_uint8, mode="L")
    if is_color:
        return Image.merge("YCbCr", (enh_y, cb, cr)).convert("RGB")
    return enh_y


# ============================================================================
# 5. RESOLUTION RESIZING & ENCODING
# ============================================================================

def cap_long_edge(image: Image.Image, max_long_edge: int = 1600) -> Image.Image:
    """Downsamples image so its longest dimension does not exceed max_long_edge.

    Preserves aspect ratio using high-quality Lanczos resampling. Never upsamples.

    Args:
        image: PIL Image.
        max_long_edge: Maximum allowed long-edge size in pixels.

    Returns:
        Resized PIL Image or original if already within limits.
    """
    w, h = image.size
    long_edge = max(w, h)
    if long_edge > max_long_edge:
        scale = max_long_edge / float(long_edge)
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        return image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    return image


def encode_image(
    image: Image.Image,
    jpeg_quality: int = 90,
) -> Tuple[bytes, str]:
    """Encodes PIL Image to binary bytes with MIME type.

    Re-encodes to JPEG at quality ~90. For low-color or typewritten documents,
    evaluates PNG compression and selects PNG if it produces a smaller payload.

    Args:
        image: PIL Image to encode.
        jpeg_quality: JPEG compression quality factor (default: 90).

    Returns:
        Tuple of (encoded_bytes, mime_type).
    """
    # Normalize mode for JPEG compatibility
    if image.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", image.size, (255, 255, 255))
        bg.paste(image, mask=image.split()[-1])
        im_clean = bg
    elif image.mode not in ("RGB", "L"):
        im_clean = image.convert("RGB")
    else:
        im_clean = image

    # Primary encoding: JPEG q~90
    jpeg_buf = io.BytesIO()
    im_clean.save(jpeg_buf, format="JPEG", quality=jpeg_quality, optimize=True)
    jpeg_bytes = jpeg_buf.getvalue()

    # Optional PNG check for low-color typewritten cards
    colors = im_clean.getcolors(maxcolors=256)
    if colors is not None and len(colors) <= 64:
        png_buf = io.BytesIO()
        im_clean.save(png_buf, format="PNG", optimize=True)
        png_bytes = png_buf.getvalue()
        if len(png_bytes) < len(jpeg_bytes):
            return png_bytes, "image/png"

    return jpeg_bytes, "image/jpeg"


# ============================================================================
# 6. PUBLIC PIPELINE API: prepare_faces
# ============================================================================

def prepare_faces(
    paths: Sequence[Union[Path, str]],
    profile_params: Optional[Dict[str, Any]] = None,
    enhance: Optional[bool] = None,
) -> List[PreparedFace]:
    """Prepares card face images for extraction per WAVE 4 Contracts §1.8.

    Transforms each input card face through:
    1. EXIF orientation transposition.
    2. Border cropping (scanner bed isolation).
    3. Auto-deskewing for small angular misalignments.
    4. Long-edge capping per profile_params.get('image_long_edge', 1600).
    5. Measured contrast evaluation.
    6. Contrast equalization (CLAHE) when faded or explicitly requested.
    7. Re-encoding to JPEG q~90 (or smaller PNG for low-color text).

    Args:
        paths: Sequence of filesystem paths to the card face images in reading order.
        profile_params: Configuration dictionary containing quality profile parameters
            (e.g. {'image_long_edge': 1600}).
        enhance: Explicit enhancement control:
            - True: Unconditionally adds a CLAHE-enhanced variant for each face.
            - False: Never adds an enhanced variant.
            - None (default, auto): Adds a CLAHE-enhanced copy only for faces judged
              faded/low-contrast by the content contrast metric.

    Returns:
        List of PreparedFace instances, with labels formatted exactly as:
        - Standard: 'Face i of N'
        - Enhanced copy: 'Face i of N (contrast-enhanced copy)'
    """
    if not paths:
        return []

    params = profile_params or {}
    max_long_edge = int(params.get("image_long_edge", 1600))
    n_faces = len(paths)

    prepared: List[PreparedFace] = []

    for idx, raw_path in enumerate(paths):
        path = Path(raw_path)
        if not path.exists():
            raise FileNotFoundError(f"Image face file not found: {path}")

        # 1. Load image and apply EXIF transposition
        with Image.open(path) as raw_im:
            im = ImageOps.exif_transpose(raw_im).copy()

        # 2. Crop to card borders
        cropped_im, _ = crop_card_borders(im)

        # 3. Deskew small angles
        deskewed_im, _ = deskew_card_image(cropped_im, max_angle=10.0)

        # 4. Cap long edge
        capped_im = cap_long_edge(deskewed_im, max_long_edge=max_long_edge)

        # 5. Contrast evaluation (never from filename or metadata)
        is_faded = is_faded_or_low_contrast(capped_im)
        if enhance is True:
            should_enhance = True
        elif enhance is False:
            should_enhance = False
        else:
            should_enhance = is_faded

        # 6. Encode standard face
        std_data, std_mime = encode_image(capped_im)
        face_num = idx + 1
        std_label = f"Face {face_num} of {n_faces}"

        prepared.append(
            PreparedFace(
                face_index=idx,
                n_faces=n_faces,
                data=std_data,
                mime_type=std_mime,
                variant=VARIANT_STANDARD,
                long_edge_px=max(capped_im.size),
                label=std_label,
            )
        )

        # 7. Encode CLAHE-enhanced copy if triggered
        if should_enhance:
            enh_im = apply_clahe(capped_im)
            enh_data, enh_mime = encode_image(enh_im)
            enh_label = f"Face {face_num} of {n_faces} (contrast-enhanced copy)"

            prepared.append(
                PreparedFace(
                    face_index=idx,
                    n_faces=n_faces,
                    data=enh_data,
                    mime_type=enh_mime,
                    variant=VARIANT_ENHANCED_CLAHE,
                    long_edge_px=max(enh_im.size),
                    label=enh_label,
                )
            )

    return prepared
