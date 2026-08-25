"""Antigravity agent tool wrappers for deterministic computer vision and physical analysis."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from recipeboxer.models.domain import (
    ArtifactType,
    CardQuadrant,
    PhysicalArtifact,
    VisualFingerprint,
)
from recipeboxer.vision.heuristics import (
    QUADRANT_MIRROR_MAP,
    calculate_stain_similarity,
)
from recipeboxer.vision.preprocessing import (
    crop_card_borders,
    deskew_card_image,
    detect_skew_angle,
    enhance_card_readability,
    extract_paper_color_palette,
)


def _resolve_output_path(p: Path, suffix_label: str, output_path: Optional[str]) -> Path:
    """Safely determines output destination path, falling back to temp dir if parent is not writable."""
    ext = p.suffix if p.suffix else ".jpg"
    if output_path:
        out_p = Path(output_path)
        try:
            out_p.parent.mkdir(parents=True, exist_ok=True)
            return out_p
        except Exception:
            pass
    try:
        if os.access(p.parent, os.W_OK):
            target = p.parent / f"{p.stem}_{suffix_label}{ext}"
            target.parent.mkdir(parents=True, exist_ok=True)
            return target
    except Exception:
        pass
    import tempfile
    fallback = Path(tempfile.gettempdir()) / f"{p.stem}_{suffix_label}{ext}"
    fallback.parent.mkdir(parents=True, exist_ok=True)
    return fallback


def deskew_card_image_tool(
    image_path: str,
    max_angle: float = 45.0,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Auto-deskews a recipe card image, correcting orientation skew to horizontal alignment.

    Args:
        image_path: Filesystem path to the scanned recipe card image.
        max_angle: Maximum search angle range in degrees (default: 45.0).
        output_path: Optional destination path to save deskewed image. If omitted, saves to temporary path.

    Returns:
        Dictionary with detected skew angle, status, and output path.
    """
    p = Path(image_path)
    if not p.exists():
        return {
            "success": False,
            "error": f"Image file not found: {image_path}",
            "skew_angle_deg": 0.0,
        }

    deskewed_img, skew_angle = deskew_card_image(p, max_angle=max_angle)
    out_p = _resolve_output_path(p, "deskewed", output_path)
    deskewed_img.save(out_p)

    return {
        "success": True,
        "input_path": str(p),
        "output_path": str(out_p),
        "skew_angle_deg": skew_angle,
        "original_size": [deskewed_img.width, deskewed_img.height],
    }


def crop_card_borders_tool(
    image_path: str,
    padding_px: int = 10,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Identifies rectangular card contours and crops out dark scanner beds and shadows.

    Args:
        image_path: Filesystem path to the scanned recipe card image.
        padding_px: Pixel margin padding around detected card borders (default: 10).
        output_path: Optional destination path to save cropped image.

    Returns:
        Dictionary with detected bounding box, status, and output path.
    """
    p = Path(image_path)
    if not p.exists():
        return {
            "success": False,
            "error": f"Image file not found: {image_path}",
        }

    cropped_img, bbox = crop_card_borders(p, padding_px=padding_px)
    out_p = _resolve_output_path(p, "cropped", output_path)
    cropped_img.save(out_p)

    return {
        "success": True,
        "input_path": str(p),
        "output_path": str(out_p),
        "bounding_box": list(bbox),
        "cropped_size": [cropped_img.width, cropped_img.height],
    }


def enhance_card_readability_tool(
    image_path: str,
    clip_limit: float = 2.0,
    white_balance: bool = True,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Enhances faint pencil handwriting and low-contrast cursive using CLAHE and white-balance.

    Args:
        image_path: Filesystem path to the scanned recipe card image.
        clip_limit: Contrast clipping limit (default: 2.0).
        white_balance: Whether to apply white-balance normalization (default: True).
        output_path: Optional destination path to save enhanced image.

    Returns:
        Dictionary with enhancement status and output path.
    """
    p = Path(image_path)
    if not p.exists():
        return {
            "success": False,
            "error": f"Image file not found: {image_path}",
        }

    enhanced_img = enhance_card_readability(
        p, clip_limit=clip_limit, white_balance=white_balance
    )
    out_p = _resolve_output_path(p, "enhanced", output_path)
    enhanced_img.save(out_p)

    return {
        "success": True,
        "input_path": str(p),
        "output_path": str(out_p),
        "enhanced_size": [enhanced_img.width, enhanced_img.height],
    }


def extract_paper_palette_tool(
    image_path: str,
    num_colors: int = 4,
) -> Dict[str, Any]:
    """Extracts dominant paper substrate and ink color centroids in CIELAB space.

    Args:
        image_path: Filesystem path to the scanned recipe card image.
        num_colors: Number of color cluster centroids to extract (default: 4).

    Returns:
        Dictionary with paper_centroid, ink_centroids, and color palette.
    """
    p = Path(image_path)
    if not p.exists():
        return {
            "success": False,
            "error": f"Image file not found: {image_path}",
        }

    palette_result = extract_paper_color_palette(p, num_colors=num_colors)
    return {
        "success": True,
        "input_path": str(p),
        **palette_result,
    }


def check_stain_symmetry_tool(
    front_image_path: str,
    back_image_path: str,
) -> Dict[str, Any]:
    """Forensically analyzes physical stain and grease bleed-through symmetry across front and back card scans.

    Checks whether physical stains (such as oil/grease, tea/coffee rings, liquid spills)
    on the front card mirror onto the horizontally flipped quadrant of the candidate back card.

    Args:
        front_image_path: Filesystem path to the front card scan.
        back_image_path: Filesystem path to the candidate back card scan.

    Returns:
        Dictionary containing stain symmetry score, mirrored quadrants, and physical compatibility.
    """
    p_front = Path(front_image_path)
    p_back = Path(back_image_path)

    if not p_front.exists() or not p_back.exists():
        return {
            "success": False,
            "error": f"One or both image paths not found: front={front_image_path}, back={back_image_path}",
            "stain_similarity_score": 0.0,
        }

    # Extract color palettes to detect dark/stain centroids
    front_palette = extract_paper_color_palette(p_front, num_colors=4)
    back_palette = extract_paper_color_palette(p_back, num_colors=4)

    # Detect visual quadrant stains from images
    def _detect_quadrant_stains(img_path: Path) -> List[PhysicalArtifact]:
        with Image.open(img_path) as im:
            rgb = im.convert("RGB")
            w, h = rgb.size
            gray = rgb.convert("L")

            # Sample 4 main quadrants
            quad_boxes = {
                CardQuadrant.TOP_LEFT: (0, 0, w // 2, h // 2),
                CardQuadrant.TOP_RIGHT: (w // 2, 0, w, h // 2),
                CardQuadrant.BOTTOM_LEFT: (0, h // 2, w // 2, h),
                CardQuadrant.BOTTOM_RIGHT: (w // 2, h // 2, w, h),
            }

            artifacts: List[PhysicalArtifact] = []
            for quad_name, (x0, y0, x1, y1) in quad_boxes.items():
                tile = gray.crop((x0, y0, x1, y1))
                hist = tile.histogram()
                # Check for localized dark/grease stain concentrations
                dark_pixels = sum(hist[:120])
                total_tile = (x1 - x0) * (y1 - y0)
                if total_tile > 0 and dark_pixels > 0.05 * total_tile:
                    artifacts.append(
                        PhysicalArtifact(
                            artifact_type=ArtifactType.OIL_GREASE_STAIN,
                            quadrant=quad_name,
                            severity="MODERATE",
                            description=f"Detected stain in {quad_name.value}",
                        )
                    )
            return artifacts

    front_stains = _detect_quadrant_stains(p_front)
    back_stains = _detect_quadrant_stains(p_back)

    # Calculate stain symmetry using heuristics
    score = calculate_stain_similarity(front_stains, back_stains)

    # Identify matching mirrored pairs
    mirrored_matches = []
    for f_art in front_stains:
        f_quad = str(f_art.quadrant.value if hasattr(f_art.quadrant, "value") else f_art.quadrant).upper()
        expected_back_quad = QUADRANT_MIRROR_MAP.get(f_quad, f_quad)
        for b_art in back_stains:
            b_quad = str(b_art.quadrant.value if hasattr(b_art.quadrant, "value") else b_art.quadrant).upper()
            if b_quad == expected_back_quad:
                mirrored_matches.append({
                    "front_quadrant": f_quad,
                    "back_quadrant": b_quad,
                    "artifact_type": str(f_art.artifact_type.value if hasattr(f_art.artifact_type, "value") else f_art.artifact_type),
                })

    return {
        "success": True,
        "front_image_path": str(p_front),
        "back_image_path": str(p_back),
        "stain_similarity_score": score,
        "front_stains_count": len(front_stains),
        "back_stains_count": len(back_stains),
        "mirrored_matches": mirrored_matches,
        "bleed_through_detected": len(mirrored_matches) > 0,
    }


def classify_card_modality_tool(image_path: str) -> Dict[str, Any]:
    """Classifies the typography and substrate modality of a scanned recipe card.

    Args:
        image_path: Filesystem path to the scanned recipe card image.

    Returns:
        Dictionary with predicted modality, confidence score, and visual cues.
    """
    from recipeboxer.vision.router import classify_card_modality

    p = Path(image_path)
    if not p.exists():
        return {
            "success": False,
            "error": f"Image file not found: {image_path}",
        }

    res = classify_card_modality(p)
    return {
        "success": True,
        "image_path": str(p),
        "modality": res.modality.value if hasattr(res.modality, "value") else str(res.modality),
        "confidence": res.confidence,
        "reasoning": res.reasoning,
        "features": res.features,
    }


# Standard list of all callable agent tools for Google Antigravity SDK
ALL_AGENT_TOOLS = [
    deskew_card_image_tool,
    crop_card_borders_tool,
    enhance_card_readability_tool,
    extract_paper_palette_tool,
    check_stain_symmetry_tool,
    classify_card_modality_tool,
]
