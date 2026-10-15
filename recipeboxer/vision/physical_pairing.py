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

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import io
from pathlib import Path
import threading
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


@dataclass
class _ShowThroughCardFeatures:
    ink_count: int
    r_norm: Optional[np.ndarray]
    f_norm_h: Optional[np.ndarray]
    f_norm_v: Optional[np.ndarray]


class _BoundedLRU:
    def __init__(self, maxsize: int = 1024):
        self.maxsize = maxsize
        self._data: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Any:
        with self._lock:
            if key not in self._data:
                return None
            self._data.move_to_end(key)
            return self._data[key]

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = value
            if len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_WEAR_CACHE = _BoundedLRU(maxsize=1024)
_SHOW_THROUGH_CACHE = _BoundedLRU(maxsize=1024)
_SILHOUETTE_CACHE = _BoundedLRU(maxsize=1024)


def clear_physical_pairing_cache() -> None:
    """Clears all in-memory LRU feature caches for physical pairing."""
    _WEAR_CACHE.clear()
    _SHOW_THROUGH_CACHE.clear()
    _SILHOUETTE_CACHE.clear()


def _get_cache_key(image: Union[Image.Image, Path, str, bytes]) -> str:
    """Generates a stable cache key for input image representations."""
    if isinstance(image, bytes):
        return hashlib.sha256(image).hexdigest()
    if isinstance(image, (str, Path)):
        p = Path(image)
        try:
            st = p.stat()
            return f"path:{p.resolve()}:{st.st_size}:{st.st_mtime_ns}"
        except Exception:
            return f"path:{p}"
    if isinstance(image, Image.Image):
        key = getattr(image, "_rb_cache_key", None)
        if key is not None:
            return key
        filename = getattr(image, "filename", None)
        if filename:
            try:
                p = Path(filename)
                st = p.stat()
                key = f"file:{p.resolve()}:{st.st_size}:{st.st_mtime_ns}"
                setattr(image, "_rb_cache_key", key)
                return key
            except Exception:
                pass
        try:
            raw = image.tobytes()
            key = f"im:{image.size}:{image.mode}:{hashlib.sha256(raw).hexdigest()}"
        except Exception:
            key = f"id:{id(image)}"
        setattr(image, "_rb_cache_key", key)
        return key
    return f"fallback:{id(image)}"


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

def _get_show_through_features(face: Union[Image.Image, Path, str, bytes]) -> Optional[_ShowThroughCardFeatures]:
    key = _get_cache_key(face)
    cached = _SHOW_THROUGH_CACHE.get(key)
    if cached is not None:
        return cached if not (isinstance(cached, str) and cached == "__NONE__") else None

    try:
        im = _to_pil_image(face)
    except Exception:
        _SHOW_THROUGH_CACHE.set(key, "__NONE__")
        return None

    if im.width < 10 or im.height < 10:
        _SHOW_THROUGH_CACHE.set(key, "__NONE__")
        return None

    target_w, target_h = 240, 160
    gray = np.array(im.convert("L").resize((target_w, target_h), Image.BILINEAR), dtype=np.float32)

    bg = grey_closing(gray, size=(13, 13))
    delta = np.maximum(0.0, bg - gray)
    rule = np.maximum(0.0, grey_opening(delta, size=(1, 21)) - grey_opening(delta, size=(3, 21)))
    delta = np.maximum(0.0, delta - rule)
    ink_count = int(np.sum(delta > 20.0))

    active_tgt = delta[delta > 5.0]
    tau_tgt = max(25.0, float(np.percentile(active_tgt, 70)) if active_tgt.size > 0 else 25.0)
    primary_mask = binary_dilation(delta > tau_tgt, iterations=2)

    res_tgt = np.where(~primary_mask, np.clip(delta, 0.0, 25.0), 0.0)
    res_tgt = res_tgt - gaussian_filter(res_tgt, sigma=8.0)
    r_std = float(np.std(res_tgt))
    r_norm = (
        (res_tgt - float(np.mean(res_tgt))) / (r_std * np.sqrt(res_tgt.size))
        if r_std >= 1e-4
        else None
    )

    if ink_count < 35:
        f_norm_h = None
        f_norm_v = None
    else:
        ink_src = np.clip(delta, 0.0, 100.0)
        ink_src = np.maximum(0.0, ink_src - gaussian_filter(ink_src, sigma=15.0))
        ink_src_blur = gaussian_filter(ink_src, sigma=1.8)

        flipped_src_h = np.fliplr(ink_src_blur)
        flipped_src_v = np.flipud(ink_src_blur)

        f_std_h = float(np.std(flipped_src_h))
        f_std_v = float(np.std(flipped_src_v))

        f_norm_h = (
            (flipped_src_h - float(np.mean(flipped_src_h))) / (f_std_h * np.sqrt(flipped_src_h.size))
            if f_std_h >= 1e-4
            else None
        )
        f_norm_v = (
            (flipped_src_v - float(np.mean(flipped_src_v))) / (f_std_v * np.sqrt(flipped_src_v.size))
            if f_std_v >= 1e-4
            else None
        )

    feats = _ShowThroughCardFeatures(
        ink_count=ink_count,
        r_norm=r_norm,
        f_norm_h=f_norm_h,
        f_norm_v=f_norm_v,
    )
    _SHOW_THROUGH_CACHE.set(key, feats)
    return feats


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
        fa = _get_show_through_features(face_a)
        fb = _get_show_through_features(face_b)
    except Exception:
        return None

    if fa is None or fb is None:
        return None

    if fa.ink_count < 35 and fb.ink_count < 35:
        return None

    target_w, target_h = 240, 160
    cy, cx = target_h // 2, target_w // 2

    def _correlate(r_norm: Optional[np.ndarray], f_norm: Optional[np.ndarray]) -> float | None:
        if f_norm is None:
            return None
        if r_norm is None:
            return 0.0

        corr_map = fftconvolve(r_norm, f_norm[::-1, ::-1], mode="same")
        sub = corr_map[cy - 6 : cy + 7, cx - 8 : cx + 9]
        raw_corr = float(np.max(sub))
        t = float(np.clip((raw_corr - 0.18) / 0.22, 0.0, 1.0))
        return 3.0 * (t ** 2) - 2.0 * (t ** 3)

    scores: list[float] = []
    # Flip "h":
    s_ba_h = _correlate(fa.r_norm, fb.f_norm_h)
    if s_ba_h is not None:
        scores.append(s_ba_h)
    s_ab_h = _correlate(fb.r_norm, fa.f_norm_h)
    if s_ab_h is not None:
        scores.append(s_ab_h)

    # Flip "v":
    s_ba_v = _correlate(fa.r_norm, fb.f_norm_v)
    if s_ba_v is not None:
        scores.append(s_ba_v)
    s_ab_v = _correlate(fb.r_norm, fa.f_norm_v)
    if s_ab_v is not None:
        scores.append(s_ab_v)

    if not scores:
        return None

    return round(float(np.clip(max(scores), 0.0, 1.0)), 4)


# ============================================================================
# 2. MIRRORED WEAR SCORE
# ============================================================================

def _extract_wear_field(arr_rgb: np.ndarray, gray: np.ndarray) -> np.ndarray:
    target_h, target_w = 160, 240
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


def _get_wear_field(face: Union[Image.Image, Path, str, bytes]) -> Optional[np.ndarray]:
    key = _get_cache_key(face)
    cached = _WEAR_CACHE.get(key)
    if cached is not None:
        return cached if not (isinstance(cached, str) and cached == "__NONE__") else None

    try:
        im = _to_pil_image(face)
    except Exception:
        _WEAR_CACHE.set(key, "__NONE__")
        return None

    if im.width < 10 or im.height < 10:
        _WEAR_CACHE.set(key, "__NONE__")
        return None

    target_w, target_h = 240, 160
    arr_a = np.array(im.convert("RGB").resize((target_w, target_h), Image.BILINEAR), dtype=np.float32)
    gray_a = np.array(im.convert("L").resize((target_w, target_h), Image.BILINEAR), dtype=np.float32)

    wear = _extract_wear_field(arr_a, gray_a)
    _WEAR_CACHE.set(key, wear)
    return wear


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
        wa = _get_wear_field(face_a)
        wb = _get_wear_field(face_b)
    except Exception:
        return None

    if wa is None or wb is None:
        return None

    # Return None when neither face exhibits detectable wear features
    if float(np.max(wa)) < 0.15 and float(np.max(wb)) < 0.15:
        return None

    target_w, target_h = 240, 160
    cy, cx = target_h // 2, target_w // 2
    scores: list[float] = []

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


def _get_silhouette(face: Union[Image.Image, Path, str, bytes]) -> Optional[Dict[str, Any]]:
    key = _get_cache_key(face)
    cached = _SILHOUETTE_CACHE.get(key)
    if cached is not None:
        return cached if not (isinstance(cached, str) and cached == "__NONE__") else None

    try:
        im = _to_pil_image(face)
    except Exception:
        _SILHOUETTE_CACHE.set(key, "__NONE__")
        return None

    if im.width < 10 or im.height < 10:
        _SILHOUETTE_CACHE.set(key, "__NONE__")
        return None

    res = _segment_card_silhouette(im)
    _SILHOUETTE_CACHE.set(key, res if res is not None else "__NONE__")
    return res


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
        ca = _get_silhouette(face_a)
        cb = _get_silhouette(face_b)
    except Exception:
        return None

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
