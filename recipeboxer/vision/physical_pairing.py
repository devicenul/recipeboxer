"""Deterministic physical pairing evidence for two-sided recipe cards.

Implements WAVE4_CONTRACTS.md §1.6 and REMEDIATION_PLAN.md §9 (G12) / §9.1 item 3:
Physical pairing evidence that stays distinctive even in a homogeneous box:
1. show_through_score: Extracts faint mirrored ghost text / residue from the reverse
   side after suppressing the face's own primary ink and paper ruling lines, correlating
   with the opposing face's ink layout map across horizontal and vertical card-flip axes.
2. mirrored_wear_score: Detects localized physical wear and damage (corner dog-ears, edge
   tears, stain blobs, and punctures) and verifies mirrored spatial alignment between faces.
3. outline_match_score: Evaluates card boundary aspect ratio, corner cut / rounding profile,
   and silhouette contour shape when card boundaries are segmented from a surrounding
   background (returning None when card boundaries are absent / flush).

Card-Flip Axes:
Physical recipe cards are commonly rotated along one of two physical flip axes:
- Horizontal flip (np.fliplr): Book / page turn around the vertical y-axis (standard index card flip).
  Coordinates transform: (x, y) -> (width - 1 - x, y).
- Vertical flip (np.flipud): Calendar / tablet / flip-top turn around the horizontal x-axis.
  Coordinates transform: (x, y) -> (x, height - 1 - y).
Both hypotheses are evaluated, returning the maximum evidence score across valid orientations.

Pure numpy, scipy, and Pillow implementation. Production code must never import cardforge.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

import numpy as np
from PIL import Image
from scipy.ndimage import (
    binary_dilation,
    binary_fill_holes,
    gaussian_filter,
    grey_closing,
    grey_opening,
    median_filter,
    uniform_filter,
)
from scipy.signal import fftconvolve


def _to_pil_image(image: Union[Image.Image, Path, str, bytes]) -> Image.Image:
    """Helper to convert diverse input representations into a PIL Image."""
    if isinstance(image, Image.Image):
        return image
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
# 1. SHOW-THROUGH SCORE
# ============================================================================

def show_through_score(
    face_a: Image.Image,
    face_b: Image.Image,
) -> float | None:
    """Computes the show-through physical pairing evidence score in [0.0, 1.0].

    Extracts the low-contrast ghost residue on face A after masking A's primary ink
    and suppressing substrate ruling lines, then correlates it with face B's ink
    layout map at coarse scale with a small shift search over horizontal and vertical
    card-flip hypotheses (and vice versa).

    Args:
        face_a: Front or back card face image.
        face_b: Candidate matching card face image.

    Returns:
        Evidence score in [0.0, 1.0], or None if ink density is insufficient
        or images are uncomputable.
    """
    try:
        im_a = _to_pil_image(face_a)
        im_b = _to_pil_image(face_b)
    except Exception:
        return None

    if im_a.width < 10 or im_a.height < 10 or im_b.width < 10 or im_b.height < 10:
        return None

    # Downscale to fast canonical working resolution
    target_w, target_h = 240, 160
    gray_a = np.array(im_a.convert("L").resize((target_w, target_h), Image.BILINEAR), dtype=np.float32)
    gray_b = np.array(im_b.convert("L").resize((target_w, target_h), Image.BILINEAR), dtype=np.float32)

    # Estimate paper background via morphological closing (fills in dark text strokes)
    bg_a = grey_closing(gray_a, size=(13, 13))
    bg_b = grey_closing(gray_b, size=(13, 13))

    delta_a = np.maximum(0.0, bg_a - gray_a)
    delta_b = np.maximum(0.0, bg_b - gray_b)

    # Suppress continuous thin horizontal ruling lines (features spanning > 21 px horizontally but <= 2 px tall)
    rule_a = np.maximum(0.0, grey_opening(delta_a, size=(1, 21)) - grey_opening(delta_a, size=(3, 21)))
    rule_b = np.maximum(0.0, grey_opening(delta_b, size=(1, 21)) - grey_opening(delta_b, size=(3, 21)))
    delta_a = np.maximum(0.0, delta_a - rule_a)
    delta_b = np.maximum(0.0, delta_b - rule_b)

    # Validate that sufficient ink is present on at least one face
    ink_a_count = int(np.sum(delta_a > 20.0))
    ink_b_count = int(np.sum(delta_b > 20.0))
    if ink_a_count < 35 and ink_b_count < 35:
        return None

    def _correlate_direction(
        d_src: np.ndarray,
        d_tgt: np.ndarray,
        flip_mode: str,
    ) -> float | None:
        """Correlates source ink layout with target residue."""
        if int(np.sum(d_src > 20.0)) < 35:
            return None

        # Mask target's own primary ink (upper contrast tail)
        active_tgt = d_tgt[d_tgt > 5.0]
        tau_tgt = max(25.0, float(np.percentile(active_tgt, 70)) if active_tgt.size > 0 else 25.0)
        primary_mask = binary_dilation(d_tgt > tau_tgt, iterations=2)

        # Target residue in unmasked paper regions
        res_tgt = np.where(~primary_mask, np.clip(d_tgt, 0.0, 25.0), 0.0)
        # Remove low-frequency illumination gradients
        res_tgt = res_tgt - gaussian_filter(res_tgt, sigma=8.0)

        # Source ink layout map (smoothed with paper bleed PSF)
        ink_src = np.clip(d_src, 0.0, 100.0)
        ink_src = np.maximum(0.0, ink_src - gaussian_filter(ink_src, sigma=15.0))
        ink_src_blur = gaussian_filter(ink_src, sigma=1.8)

        flipped_src = np.fliplr(ink_src_blur) if flip_mode == "h" else np.flipud(ink_src_blur)

        r_std = float(np.std(res_tgt))
        f_std = float(np.std(flipped_src))
        if r_std < 1e-4 or f_std < 1e-4:
            return 0.0

        r_norm = (res_tgt - float(np.mean(res_tgt))) / (r_std * np.sqrt(res_tgt.size))
        f_norm = (flipped_src - float(np.mean(flipped_src))) / (f_std * np.sqrt(flipped_src.size))

        # 2D cross-correlation via FFT
        corr_map = fftconvolve(r_norm, f_norm[::-1, ::-1], mode="same")
        cy, cx = target_h // 2, target_w // 2
        # Search over small spatial shifts: dy in [-6, 6], dx in [-8, 8]
        sub = corr_map[cy - 6 : cy + 7, cx - 8 : cx + 9]
        raw_corr = float(np.max(sub))

        # Calibrate raw Pearson correlation to [0.0, 1.0] via smoothstep
        t = float(np.clip((raw_corr - 0.18) / 0.22, 0.0, 1.0))
        return 3.0 * (t ** 2) - 2.0 * (t ** 3)

    scores: list[float] = []
    for flip in ("h", "v"):
        # Ghost of B on A
        s_ba = _correlate_direction(delta_b, delta_a, flip)
        if s_ba is not None:
            scores.append(s_ba)
        # Ghost of A on B
        s_ab = _correlate_direction(delta_a, delta_b, flip)
        if s_ab is not None:
            scores.append(s_ab)

    if not scores:
        return None

    return round(float(np.clip(max(scores), 0.0, 1.0)), 4)


# ============================================================================
# 2. MIRRORED WEAR SCORE
# ============================================================================

def mirrored_wear_score(
    face_a: Image.Image,
    face_b: Image.Image,
) -> float | None:
    """Computes the mirrored wear and damage evidence score in [0.0, 1.0].

    Detects corner damage (dog-ears, folds), perimeter edge damage (tears, notches),
    and stain blobs (coffee rings, oil smudges). Verifies spatial alignment when
    face B is mirrored across horizontal and vertical card-flip axes.

    Args:
        face_a: Front or back card face image.
        face_b: Candidate matching card face image.

    Returns:
        Evidence score in [0.0, 1.0], or None if no wear or damage features
        are detected on either card face.
    """
    try:
        im_a = _to_pil_image(face_a)
        im_b = _to_pil_image(face_b)
    except Exception:
        return None

    if im_a.width < 10 or im_a.height < 10 or im_b.width < 10 or im_b.height < 10:
        return None

    target_w, target_h = 240, 160
    arr_a = np.array(im_a.convert("RGB").resize((target_w, target_h), Image.BILINEAR), dtype=np.float32)
    arr_b = np.array(im_b.convert("RGB").resize((target_w, target_h), Image.BILINEAR), dtype=np.float32)
    gray_a = np.array(im_a.convert("L").resize((target_w, target_h), Image.BILINEAR), dtype=np.float32)
    gray_b = np.array(im_b.convert("L").resize((target_w, target_h), Image.BILINEAR), dtype=np.float32)

    def _extract_wear_field(arr_rgb: np.ndarray, gray: np.ndarray) -> np.ndarray:
        # 1. Stain blobs: chromatic deviation (coffee/tea and grease absorb blue)
        # Median filter removes thin text strokes and ruling lines
        smooth_rgb = np.zeros_like(arr_rgb)
        for c in range(3):
            smooth_rgb[:, :, c] = median_filter(arr_rgb[:, :, c], size=9)

        chroma = (smooth_rgb[:, :, 0] + smooth_rgb[:, :, 1]) / 2.0 - smooth_rgb[:, :, 2]
        bg_chroma = float(np.percentile(chroma, 40))
        stain_field = uniform_filter(np.maximum(0.0, chroma - bg_chroma), size=7)

        # 2. Perimeter and corner damage (dog-ears, tears, notches)
        bg_gray = float(np.percentile(gray, 80))
        margin_mask = np.zeros((target_h, target_w), dtype=np.float32)
        margin_mask[:25, :] = 1.0
        margin_mask[-25:, :] = 1.0
        margin_mask[:, :25] = 1.0
        margin_mask[:, -25:] = 1.0

        edge_dev = np.abs(gray - bg_gray) * margin_mask
        edge_field = uniform_filter(edge_dev, size=5)

        # Normalized combination of stain and structural edge damage
        wear_field = np.clip(stain_field / 15.0, 0.0, 1.0) * 0.70 + np.clip(edge_field / 40.0, 0.0, 1.0) * 0.50
        return wear_field

    wa = _extract_wear_field(arr_a, gray_a)
    wb = _extract_wear_field(arr_b, gray_b)

    # Return None when neither face exhibits detectable wear features
    if float(np.max(wa)) < 0.15 and float(np.max(wb)) < 0.15:
        return None

    scores: list[float] = []
    cy, cx = target_h // 2, target_w // 2

    for flip in ("h", "v"):
        wb_flip = np.fliplr(wb) if flip == "h" else np.flipud(wb)
        corr = fftconvolve(wa, wb_flip[::-1, ::-1], mode="same")
        sub = corr[cy - 6 : cy + 7, cx - 8 : cx + 9]
        max_corr = float(np.max(sub))
        denom = 0.5 * (float(np.sum(wa ** 2)) + float(np.sum(wb_flip ** 2))) + 1e-4
        scores.append(float(np.clip(max_corr / denom, 0.0, 1.0)))

    return round(float(np.clip(max(scores), 0.0, 1.0)), 4)


# ============================================================================
# 3. OUTLINE MATCH SCORE
# ============================================================================

def _segment_card_silhouette(image: Image.Image) -> Optional[Dict[str, Any]]:
    """Segments card silhouette contour from a contrasting background border.

    Returns:
        Dict with 'aspect', 'corners', 'canvas' if a segmented card boundary
        is identified inside the frame; None if the image is flush or borderless.
    """
    w_dim, h_dim = 200, 150
    small = image.resize((w_dim, h_dim), Image.BILINEAR)
    gray = np.array(small.convert("L"), dtype=np.float32)

    # Sample border perimeter pixels
    border = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]])
    bg_val = float(np.median(border))
    bg_std = float(np.std(border))

    # Center sample
    center = gray[h_dim // 4 : 3 * h_dim // 4, w_dim // 4 : 3 * w_dim // 4]
    center_val = float(np.median(center))

    diff = abs(center_val - bg_val)
    # If border lacks contrast with center or has high variance, no boundary exists
    if diff < 15.0 or bg_std > 35.0:
        return None

    thresh = (center_val + bg_val) / 2.0
    card_mask = (gray > thresh) if center_val > bg_val else (gray < thresh)
    card_mask = binary_fill_holes(card_mask)

    # Check whether the mask touches frame borders extensively (flush image)
    border_card_fraction = (
        np.sum(card_mask[0, :]) + np.sum(card_mask[-1, :]) +
        np.sum(card_mask[:, 0]) + np.sum(card_mask[:, -1])
    ) / float(2 * w_dim + 2 * h_dim)
    if border_card_fraction > 0.35:
        return None

    rows = np.any(card_mask, axis=1)
    cols = np.any(card_mask, axis=0)
    if not np.any(rows) or not np.any(cols):
        return None

    rmin, rmax = int(np.where(rows)[0][0]), int(np.where(rows)[0][-1])
    cmin, cmax = int(np.where(cols)[0][0]), int(np.where(cols)[0][-1])

    card_h = rmax - rmin + 1
    card_w = cmax - cmin + 1
    if card_h < 30 or card_w < 30:
        return None

    card_patch = card_mask[rmin : rmax + 1, cmin : cmax + 1]
    aspect_ratio = float(max(card_w, card_h)) / float(min(card_w, card_h))

    # Canonical landscape orientation
    if card_h > card_w:
        card_patch = np.rot90(card_patch)
        card_h, card_w = card_w, card_h

    # Corner fill ratios (sharp 90° corner = 1.0, rounded corner ~ pi/4 = 0.785)
    cr = max(4, int(min(card_h, card_w) * 0.12))
    corners = {
        "TL": float(np.sum(card_patch[:cr, :cr])) / float(cr * cr),
        "TR": float(np.sum(card_patch[:cr, -cr:])) / float(cr * cr),
        "BL": float(np.sum(card_patch[-cr:, :cr])) / float(cr * cr),
        "BR": float(np.sum(card_patch[-cr:, -cr:])) / float(cr * cr),
    }

    # Normalize to 100x100 canvas preserving aspect ratio
    canvas = np.zeros((100, 100), dtype=bool)
    scale = 92.0 / max(card_h, card_w)
    nh = max(1, int(round(card_h * scale)))
    nw = max(1, int(round(card_w * scale)))
    m_im = Image.fromarray(card_patch.astype(np.uint8) * 255).resize((nw, nh), Image.NEAREST)
    m_arr = np.array(m_im) > 128
    oy = (100 - nh) // 2
    ox = (100 - nw) // 2
    canvas[oy : oy + nh, ox : ox + nw] = m_arr

    return {
        "aspect": aspect_ratio,
        "corners": corners,
        "canvas": canvas,
    }


def outline_match_score(
    face_a: Image.Image,
    face_b: Image.Image,
) -> float | None:
    """Computes the card outline match score in [0.0, 1.0].

    Evaluates card aspect ratio, corner cut / roundness profile, and contour shape
    agreement between segmented card boundaries.

    Args:
        face_a: Front or back card face image.
        face_b: Candidate matching card face image.

    Returns:
        Evidence score in [0.0, 1.0], or None if card boundaries cannot be
        reliably segmented from background (e.g. flush-cropped cards).
    """
    try:
        im_a = _to_pil_image(face_a)
        im_b = _to_pil_image(face_b)
    except Exception:
        return None

    if im_a.width < 10 or im_a.height < 10 or im_b.width < 10 or im_b.height < 10:
        return None

    ca = _segment_card_silhouette(im_a)
    cb = _segment_card_silhouette(im_b)

    # Return None when not computable (e.g. no card boundary)
    if ca is None or cb is None:
        return None

    # Aspect ratio match
    ar_match = (min(ca["aspect"], cb["aspect"]) / max(ca["aspect"], cb["aspect"])) ** 2

    # Corner roundness and contour shape match across card-flip axes
    scores_c: list[float] = []
    scores_iou: list[float] = []

    for flip in ("h", "v"):
        if flip == "h":
            dtl = abs(ca["corners"]["TL"] - cb["corners"]["TR"])
            dtr = abs(ca["corners"]["TR"] - cb["corners"]["TL"])
            dbl = abs(ca["corners"]["BL"] - cb["corners"]["BR"])
            dbr = abs(ca["corners"]["BR"] - cb["corners"]["BL"])
            cb_canvas_flip = np.fliplr(cb["canvas"])
        else:
            dtl = abs(ca["corners"]["TL"] - cb["corners"]["BL"])
            dtr = abs(ca["corners"]["TR"] - cb["corners"]["BR"])
            dbl = abs(ca["corners"]["BL"] - cb["corners"]["TL"])
            dbr = abs(ca["corners"]["BR"] - cb["corners"]["TR"])
            cb_canvas_flip = np.flipud(cb["canvas"])

        avg_d = (dtl + dtr + dbl + dbr) / 4.0
        scores_c.append(max(0.0, 1.0 - 4.0 * avg_d))

        inter = int(np.sum(ca["canvas"] & cb_canvas_flip))
        union = int(np.sum(ca["canvas"] | cb_canvas_flip))
        scores_iou.append(float(inter) / float(max(1, union)))

    c_score = max(scores_c)
    iou_score = max(scores_iou)

    total = 0.40 * ar_match + 0.30 * c_score + 0.30 * iou_score
    return round(float(np.clip(total, 0.0, 1.0)), 4)
