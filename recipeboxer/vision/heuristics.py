"""Stage 1 Heuristic Visual Distance and Screening Engine for RecipeBoxer.

Computes multi-dimensional physical substrate and contextual similarity between
scanned cards to rapidly screen candidate front/back pairs before multimodal LLM verification.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple, Union

from recipeboxer.config import get_settings
from recipeboxer.models.domain import (
    ArtifactType,
    CardQuadrant,
    CardRole,
    InkColor,
    InkInfo,
    InkType,
    PaperColorInfo,
    PaperRulingInfo,
    PaperRulingType,
    PhysicalArtifact,
    VisualFingerprint,
)

# Standard horizontal mirror mapping for physical artifacts (e.g. grease bleed-through)
QUADRANT_MIRROR_MAP: Dict[str, str] = {
    "TOP_LEFT": "TOP_RIGHT",
    "TOP_RIGHT": "TOP_LEFT",
    "BOTTOM_LEFT": "BOTTOM_RIGHT",
    "BOTTOM_RIGHT": "BOTTOM_LEFT",
    "EDGE_LEFT": "EDGE_RIGHT",
    "EDGE_RIGHT": "EDGE_LEFT",
    "CENTER": "CENTER",
    "EDGE_TOP": "EDGE_TOP",
    "EDGE_BOTTOM": "EDGE_BOTTOM",
}


def _get_enum_str(val: Any) -> str:
    """Extracts clean uppercase string from enums, strings, or object values."""
    if val is None:
        return ""
    v = getattr(val, "value", str(val))
    v_str = str(v).strip()
    if "." in v_str and any(v_str.startswith(p) for p in ("Card", "Paper", "Ink", "Artifact")):
        v_str = v_str.split(".")[-1]
    return v_str.upper()


def rgb_to_lab(r: int, g: int, b: int) -> Tuple[float, float, float]:
    """Converts 8-bit sRGB color coordinates to standard CIE L*a*b* coordinates.

    Args:
        r: Red channel (0-255).
        g: Green channel (0-255).
        b: Blue channel (0-255).

    Returns:
        Tuple of (L*, a*, b*) CIE coordinates.
    """
    # 1. Normalize sRGB to [0, 1]
    r_lin = r / 255.0
    g_lin = g / 255.0
    b_lin = b / 255.0

    # 2. Gamma linearization
    r_lin = r_lin / 12.92 if r_lin <= 0.04045 else ((r_lin + 0.055) / 1.055) ** 2.4
    g_lin = g_lin / 12.92 if g_lin <= 0.04045 else ((g_lin + 0.055) / 1.055) ** 2.4
    b_lin = b_lin / 12.92 if b_lin <= 0.04045 else ((b_lin + 0.055) / 1.055) ** 2.4

    # 3. Convert to CIE XYZ (D65 standard observer)
    x = r_lin * 0.4124564 + g_lin * 0.3575761 + b_lin * 0.1804375
    y = r_lin * 0.2126729 + g_lin * 0.7151522 + b_lin * 0.0721750
    z = r_lin * 0.0193339 + g_lin * 0.1191920 + b_lin * 0.9503041

    # 4. Normalize by D65 reference white
    xn, yn, zn = 0.95047, 1.00000, 1.08883
    xr, yr, zr = x / xn, y / yn, z / zn

    # 5. Non-linear mapping function
    def f(t: float) -> float:
        return t ** (1.0 / 3.0) if t > 0.008856 else (7.787 * t) + (16.0 / 116.0)

    fx, fy, fz = f(xr), f(yr), f(zr)

    lab_l = (116.0 * fy) - 16.0
    lab_a = 500.0 * (fx - fy)
    lab_b = 200.0 * (fy - fz)

    return (round(lab_l, 4), round(lab_a, 4), round(lab_b, 4))


def hex_to_lab(hex_str: str) -> Tuple[float, float, float]:
    """Converts a hexadecimal color string (e.g. '#F4E8C1') to CIE L*a*b* coordinates.

    Args:
        hex_str: Hex color string, with or without leading '#'.

    Returns:
        Tuple of (L*, a*, b*) CIE coordinates.
    """
    clean_hex = hex_str.strip().lstrip("#")
    if len(clean_hex) != 6:
        return (100.0, 0.0, 0.0)
    try:
        r = int(clean_hex[0:2], 16)
        g = int(clean_hex[2:4], 16)
        b = int(clean_hex[4:6], 16)
        return rgb_to_lab(r, g, b)
    except ValueError:
        return (100.0, 0.0, 0.0)


def calculate_color_similarity(
    c1: Union[PaperColorInfo, Tuple[float, float, float], Dict[str, Any]],
    c2: Union[PaperColorInfo, Tuple[float, float, float], Dict[str, Any]],
) -> float:
    """Computes perceptual color similarity between two substrate colors in CIELAB space.

    Formula:
        Delta_E = sqrt((L1 - L2)^2 + (a1 - a2)^2 + (b1 - b2)^2)
        S_color = max(0.0, 1.0 - Delta_E / 50.0)

    Args:
        c1: First paper color info (PaperColorInfo, (L,a,b) tuple, or dict).
        c2: Second paper color info (PaperColorInfo, (L,a,b) tuple, or dict).

    Returns:
        Similarity score in [0.0, 1.0].
    """
    def extract_lab(c: Any) -> Tuple[float, float, float]:
        if isinstance(c, PaperColorInfo) or hasattr(c, "lab_l"):
            return (float(c.lab_l), float(c.lab_a), float(c.lab_b))
        if isinstance(c, (tuple, list)) and len(c) >= 3:
            return (float(c[0]), float(c[1]), float(c[2]))
        if isinstance(c, dict):
            if "lab_l" in c:
                return (float(c.get("lab_l", 100.0)), float(c.get("lab_a", 0.0)), float(c.get("lab_b", 0.0)))
            if "hex_approx" in c:
                return hex_to_lab(str(c["hex_approx"]))
        return (100.0, 0.0, 0.0)

    l1, a1, b1 = extract_lab(c1)
    l2, a2, b2 = extract_lab(c2)

    delta_e = math.sqrt((l1 - l2) ** 2 + (a1 - a2) ** 2 + (b1 - b2) ** 2)
    score = max(0.0, 1.0 - delta_e / 50.0)
    return round(min(1.0, score), 4)


def calculate_proximity_score(
    idx1: int,
    idx2: int,
    total_cards: Optional[int] = None,
) -> float:
    """Computes physical scan sequence proximity score using exponential decay.

    Formula:
        S_prox = exp(-0.5 * |idx1 - idx2|)

    Args:
        idx1: Scan sequence index of first card (0-based or 1-based).
        idx2: Scan sequence index of second card.
        total_cards: Optional total count of cards in batch (for normalization/bounds).

    Returns:
        Proximity score in [0.0, 1.0].
    """
    delta_idx = abs(idx1 - idx2)
    score = math.exp(-0.5 * delta_idx)
    return round(max(0.0, min(1.0, score)), 4)


def _normalize_quadrant(quad: Any) -> str:
    """Normalizes quadrant representation to uppercase string."""
    v = _get_enum_str(quad)
    return v or "CENTER"


def _normalize_artifact_type(art_type: Any) -> str:
    """Normalizes artifact type for compatibility matching."""
    val = _get_enum_str(art_type)
    if not val:
        return "UNKNOWN"
    if val in ("OIL_STAIN", "OIL_GREASE_STAIN", "LIQUID_STAIN", "FOOD_SPLATTER_SAUCE"):
        return "LIQUID_OIL_STAIN"
    if val in ("TEAR", "TEAR_EDGE", "NOTCH"):
        return "TEAR"
    if val in ("HOLE_PUNCH", "PUNCH_HOLE_TOP", "PUNCH_HOLE_SIDE"):
        return "HOLE_PUNCH"
    if val in ("HORIZONTAL_CREASE", "VERTICAL_CREASE", "CORNER_DOG_EAR_FOLD"):
        return "CREASE_FOLD"
    return val


def calculate_stain_similarity(
    art1: Union[List[PhysicalArtifact], List[Dict[str, Any]]],
    art2: Union[List[PhysicalArtifact], List[Dict[str, Any]]],
) -> float:
    """Computes physical artifact & mirrored stain bleed-through similarity.

    Liquid grease and oil stains penetrate physical index cards, appearing at
    horizontally mirrored coordinates on the reverse side:
    - TOP_LEFT <-> TOP_RIGHT
    - BOTTOM_LEFT <-> BOTTOM_RIGHT
    - EDGE_LEFT <-> EDGE_RIGHT
    - CENTER <-> CENTER

    Scoring:
    - 1.00: Mirrored artifact pair found with matching type and reflected quadrant.
    - 0.60: Both cards are pristine / have no artifacts.
    - 0.30: One card has artifacts and the other has none.
    - 0.10: Both cards have artifacts, but none match mirrored coordinates.

    Args:
        art1: Artifact list on front card.
        art2: Artifact list on back card.

    Returns:
        Stain similarity score in [0.0, 1.0].
    """
    artifacts_1 = art1 or []
    artifacts_2 = art2 or []

    # Both cards pristine
    if len(artifacts_1) == 0 and len(artifacts_2) == 0:
        return 0.60

    # One card stained, other clean
    if (len(artifacts_1) == 0 and len(artifacts_2) > 0) or (len(artifacts_1) > 0 and len(artifacts_2) == 0):
        return 0.30

    def get_quad_and_type(art: Any) -> Tuple[str, str]:
        if isinstance(art, PhysicalArtifact) or hasattr(art, "quadrant"):
            return (_normalize_quadrant(art.quadrant), _normalize_artifact_type(art.artifact_type))
        if isinstance(art, dict):
            return (
                _normalize_quadrant(art.get("quadrant")),
                _normalize_artifact_type(art.get("artifact_type")),
            )
        return ("CENTER", "UNKNOWN")

    parsed_1 = [get_quad_and_type(a) for a in artifacts_1]
    parsed_2 = [get_quad_and_type(a) for a in artifacts_2]

    # Check for horizontally mirrored quadrant matches with matching type
    for q1, t1 in parsed_1:
        mirror_q1 = QUADRANT_MIRROR_MAP.get(q1, q1)
        for q2, t2 in parsed_2:
            if q2 == mirror_q1:
                # Direct quadrant mirror match!
                if t1 == t2 or t1 == "LIQUID_OIL_STAIN" or t2 == "LIQUID_OIL_STAIN":
                    return 1.00
                return 0.85

    # Artifacts present on both, but not in mirrored positions
    return 0.10


def _normalize_ruling_type(r_type: Any) -> str:
    """Normalizes ruling pattern string."""
    val = _get_enum_str(r_type)
    if not val or val in ("NONE", "UNRULED_BLANK", "BLANK"):
        return "UNRULED_BLANK"
    if val in ("STANDARD_RULED_BLUE", "HORIZONTAL_LINES", "NARROW_RULED", "WIDE_RULED", "RULED"):
        return "STANDARD_RULED_BLUE"
    if val in ("INDEX_CARD_HEADER_RULED", "HEADER_RULED"):
        return "INDEX_CARD_HEADER_RULED"
    if val in ("GRID", "GRID_GRAPH"):
        return "GRID"
    if val in ("DOTTED",):
        return "DOTTED"
    return val


def calculate_ruling_similarity(
    r1: Union[PaperRulingInfo, Dict[str, Any], str, None],
    r2: Union[PaperRulingInfo, Dict[str, Any], str, None],
) -> float:
    """Computes paper ruling pattern and grid similarity score.

    Args:
        r1: Ruling info for first card.
        r2: Ruling info for second card.

    Returns:
        Similarity score in [0.0, 1.0].
    """
    def parse_ruling(r: Any) -> Tuple[bool, str, Optional[float]]:
        if isinstance(r, PaperRulingInfo) or hasattr(r, "ruling_type"):
            return (bool(getattr(r, "has_ruling", False)), _normalize_ruling_type(r.ruling_type), getattr(r, "line_spacing_px", None))
        if isinstance(r, dict):
            return (
                bool(r.get("has_ruling", False)),
                _normalize_ruling_type(r.get("ruling_type")),
                r.get("line_spacing_px"),
            )
        if isinstance(r, (str, PaperRulingType)) or hasattr(r, "value"):
            norm = _normalize_ruling_type(r)
            return (norm != "UNRULED_BLANK", norm, None)
        return (False, "UNRULED_BLANK", None)

    has_r1, type_1, spacing_1 = parse_ruling(r1)
    has_r2, type_2, spacing_2 = parse_ruling(r2)

    # Both unruled
    if not has_r1 and not has_r2:
        return 0.80

    # One ruled, one unruled
    if has_r1 != has_r2:
        return 0.00

    # Both ruled
    if type_1 == type_2:
        if spacing_1 is not None and spacing_2 is not None:
            spacing_diff = abs(spacing_1 - spacing_2)
            if spacing_diff <= 4.0:
                return 1.00
            if spacing_diff <= 10.0:
                return 0.85
            return 0.70
        return 0.95

    # Compatible ruled types (e.g. Standard Ruled vs Index Card Header Ruled)
    if {type_1, type_2} <= {"STANDARD_RULED_BLUE", "INDEX_CARD_HEADER_RULED"}:
        return 0.75

    # Conflicting ruling types (e.g. Grid vs Ruled Lines)
    return 0.10


def _normalize_ink_type(i_type: Any) -> str:
    """Normalizes ink medium string."""
    val = _get_enum_str(i_type)
    if not val:
        return "UNKNOWN"
    if "BALLPOINT" in val:
        return "BALLPOINT"
    if "GEL" in val:
        return "GEL"
    if "FOUNTAIN" in val:
        return "FOUNTAIN"
    if "PENCIL" in val:
        return "PENCIL"
    if "TYPEWRITER" in val or "TYPED" in val:
        return "TYPEWRITER"
    if "FELT" in val or "MARKER" in val:
        return "FELT_TIP"
    if "PRINTED" in val or "TYPESET" in val:
        return "PRINTED"
    return val


def _normalize_ink_color(i_color: Any) -> str:
    """Normalizes ink color string."""
    val = _get_enum_str(i_color)
    if not val:
        return "UNKNOWN"
    if val in ("BLUE", "BLUE_BALLPOINT", "DARK_BLUE"):
        return "BLUE"
    if val in ("BLACK", "BLACK_BALLPOINT", "DARK_BLACK"):
        return "BLACK"
    if val in ("BLUE_BLACK",):
        return "BLUE_BLACK"
    if val in ("RED",):
        return "RED"
    if val in ("GREEN",):
        return "GREEN"
    if val in ("PURPLE",):
        return "PURPLE"
    if val in ("GRAPHITE", "GRAPHITE_GRAY", "GRAY", "GREY"):
        return "GRAPHITE_GRAY"
    return val


def calculate_ink_similarity(
    i1: Union[InkInfo, Dict[str, Any], None],
    i2: Union[InkInfo, Dict[str, Any], None],
) -> float:
    """Computes writing medium and ink color similarity.

    Formula:
        S_ink = 0.60 * S_type + 0.40 * S_color

    Args:
        i1: Ink info for first card.
        i2: Ink info for second card.

    Returns:
        Similarity score in [0.0, 1.0].
    """
    def parse_ink(i: Any) -> Tuple[str, str]:
        if isinstance(i, InkInfo) or hasattr(i, "ink_type"):
            return (_normalize_ink_type(i.ink_type), _normalize_ink_color(i.ink_color))
        if isinstance(i, dict):
            return (
                _normalize_ink_type(i.get("ink_type")),
                _normalize_ink_color(i.get("ink_color")),
            )
        return ("UNKNOWN", "UNKNOWN")

    type_1, color_1 = parse_ink(i1)
    type_2, color_2 = parse_ink(i2)

    # Ink type matching
    if type_1 == "UNKNOWN" or type_2 == "UNKNOWN":
        s_type = 0.50
    elif type_1 == type_2:
        s_type = 1.00
    else:
        s_type = 0.10

    # Ink color matching
    if color_1 == "UNKNOWN" or color_2 == "UNKNOWN":
        s_color = 0.50
    elif color_1 == color_2:
        s_color = 1.00
    elif {color_1, color_2} <= {"BLUE", "BLUE_BLACK"} or {color_1, color_2} <= {"BLACK", "BLUE_BLACK"}:
        s_color = 0.80
    else:
        s_color = 0.10

    score = 0.60 * s_type + 0.40 * s_color
    return round(score, 4)


def calculate_role_compatibility(
    role1: Union[CardRole, str, None],
    role2: Union[CardRole, str, None],
) -> float:
    """Computes functional role pairing compatibility for directed pair (C_i -> C_j).

    Compatibility Rules:
    - (SEQUENCE_START, SEQUENCE_CONTINUATION) -> 1.00 (Ideal start to continuation)
    - (SEQUENCE_CONTINUATION, SEQUENCE_CONTINUATION) -> 0.90 (Valid multi-part sequence continuation N >= 3)
    - (SEQUENCE_START, UNKNOWN)   -> 0.80 (Likely continuation is unclassified)
    - (UNKNOWN, SEQUENCE_CONTINUATION) -> 0.80 (Likely start is unclassified)
    - (SEQUENCE_CONTINUATION, UNKNOWN) -> 0.70 (Continuation to unclassified)
    - (UNKNOWN, UNKNOWN)          -> 0.50 (Uncertain)
    - (STANDALONE, *)             -> 0.00 (Single-sided card, do not chain)
    - (*, STANDALONE)             -> 0.00 (Single-sided card, do not chain)
    - (SEQUENCE_CONTINUATION, SEQUENCE_START) -> 0.00 (Inverted sequence flow)
    - (SEQUENCE_START, SEQUENCE_START) -> 0.00 (Two sequence starts cannot chain)

    Args:
        role1: Role of first (preceding) card.
        role2: Role of second (following) card.

    Returns:
        Compatibility score in [0.0, 1.0].
    """
    def _norm(r: Any) -> str:
        s = _get_enum_str(r) or "UNKNOWN"
        if s in ("FRONT_ONLY", "FRONT", "START", "SEQUENCE_START"):
            return "SEQUENCE_START"
        if s in ("BACK_ONLY", "BACK", "CONTINUATION", "SEQUENCE_CONTINUATION"):
            return "SEQUENCE_CONTINUATION"
        if s in ("STANDALONE", "SINGLE"):
            return "STANDALONE"
        return "UNKNOWN"

    r1 = _norm(role1)
    r2 = _norm(role2)

    if r1 == "STANDALONE" or r2 == "STANDALONE":
        return 0.00

    if r1 == "SEQUENCE_START" and r2 == "SEQUENCE_CONTINUATION":
        return 1.00

    if r1 == "SEQUENCE_CONTINUATION" and r2 == "SEQUENCE_CONTINUATION":
        return 0.90

    if (r1 == "SEQUENCE_START" and r2 == "UNKNOWN") or (r1 == "UNKNOWN" and r2 == "SEQUENCE_CONTINUATION"):
        return 0.80

    if r1 == "SEQUENCE_CONTINUATION" and r2 == "UNKNOWN":
        return 0.70

    if r1 == "UNKNOWN" and r2 == "UNKNOWN":
        return 0.50

    # Incompatible combinations (e.g. START + START, CONTINUATION + START)
    return 0.00


def calculate_sequential_header_score(
    fp1: VisualFingerprint,
    fp2: VisualFingerprint,
) -> float:
    """Detects sequential header and step progression between two cards (e.g. Part 1 -> Part 2, Card 1 -> Card 2).

    Args:
        fp1: Preceding card visual fingerprint.
        fp2: Following card visual fingerprint.

    Returns:
        Header and continuation score in [0.0, 1.0].
    """
    import re

    t1 = ""
    t2 = ""
    if fp1.boundary_text:
        t1 = f"{fp1.boundary_text.first_line} {fp1.boundary_text.last_line}".lower()
    if fp2.boundary_text:
        t2 = f"{fp2.boundary_text.first_line} {fp2.boundary_text.last_line}".lower()

    # Check for Part N / Card N / Page N progression
    m1 = re.search(r"(?:part|card|page|pt|p\.)\s*([0-9]+)", t1) or re.search(r"(?:part|card|page|pt|p\.)\s*([0-9]+)", fp1.card_id.lower())
    m2 = re.search(r"(?:part|card|page|pt|p\.)\s*([0-9]+)", t2) or re.search(r"(?:part|card|page|pt|p\.)\s*([0-9]+)", fp2.card_id.lower())
    if m1 and m2:
        try:
            num1 = int(m1.group(1))
            num2 = int(m2.group(1))
            if num2 == num1 + 1:
                return 1.00
            elif num2 > num1:
                return 0.85
            else:
                return 0.10
        except ValueError:
            pass

    score = 0.50
    if fp1.boundary_text and (fp1.boundary_text.has_continuation_symbol or fp1.boundary_text.is_cut_off_mid_sentence):
        score += 0.25
    if fp2.boundary_text and any(k in fp2.boundary_text.first_line.lower() for k in ("cont", "direct", "step", "bake", "over", "part 2", "card 2")):
        score += 0.25

    return round(min(1.0, score), 4)


def calculate_heuristic_distance(
    fp1: VisualFingerprint,
    fp2: VisualFingerprint,
    idx1: int = 0,
    idx2: int = 1,
    total_cards: Optional[int] = None,
) -> float:
    """Calculates composite Stage 1 directed heuristic transition score P(C1 -> C2).

    Evaluates:
    - S_role   (20%): Role/Sequence compatibility
    - S_prox   (25%): Sequential scan distance decay
    - S_color  (15%): CIELAB perceptual substrate color match (Delta E <= 5.0)
    - S_ruling (15%): Ruling grid / spacing similarity
    - S_ink    (10%): Pen type and ink color match
    - S_stain  (5%):  Mirrored physical grease/oil bleed-through
    - S_hdr    (10%): Explicit sequential header and narrative continuation

    If S_role is 0.0, the pair is immediately pruned (score = 0.0).

    Args:
        fp1: VisualFingerprint of candidate preceding card.
        fp2: VisualFingerprint of candidate following card.
        idx1: Scan sequence index of fp1.
        idx2: Scan sequence index of fp2.
        total_cards: Optional total card count.

    Returns:
        Composite heuristic similarity score in [0.0, 1.0].
    """
    s_role = calculate_role_compatibility(fp1.role, fp2.role)
    if s_role <= 0.0:
        return 0.00

    s_prox = calculate_proximity_score(idx1, idx2, total_cards)
    s_color = calculate_color_similarity(fp1.paper_color, fp2.paper_color)
    s_ruling = calculate_ruling_similarity(fp1.ruling, fp2.ruling)
    s_ink = calculate_ink_similarity(fp1.ink, fp2.ink)
    s_stain = calculate_stain_similarity(fp1.artifacts, fp2.artifacts)
    s_hdr = calculate_sequential_header_score(fp1, fp2)

    composite = (
        0.20 * s_role
        + 0.25 * s_prox
        + 0.15 * s_color
        + 0.15 * s_ruling
        + 0.10 * s_ink
        + 0.05 * s_stain
        + 0.10 * s_hdr
    )

    return round(max(0.0, min(1.0, composite)), 4)


calculate_transition_score = calculate_heuristic_distance


def screen_candidate_pairs(
    fingerprints: List[VisualFingerprint],
    threshold: Optional[float] = None,
) -> List[Tuple[VisualFingerprint, VisualFingerprint, float]]:
    """Screens all pairwise card combinations and returns candidate directed pairs.

    Filters out pairs below the threshold (default 0.50 from Settings), and sorts
    candidates in descending order of heuristic score.

    Args:
        fingerprints: List of VisualFingerprint objects.
        threshold: Minimum heuristic similarity threshold (defaults to settings).

    Returns:
        List of (front_fp, back_fp, heuristic_score) tuples sorted descending by score.
    """
    if threshold is None:
        threshold = get_settings().heuristic_screen_threshold

    n = len(fingerprints)
    if n < 2:
        return []

    candidates: List[Tuple[VisualFingerprint, VisualFingerprint, float]] = []

    for i in range(n):
        fp1 = fingerprints[i]
        r1 = _get_enum_str(fp1.role)
        if r1 == "STANDALONE":
            continue

        for j in range(n):
            if i == j:
                continue
            fp2 = fingerprints[j]
            r2 = _get_enum_str(fp2.role)
            if r2 == "STANDALONE":
                continue

            score = calculate_heuristic_distance(fp1, fp2, idx1=i, idx2=j, total_cards=n)
            if score >= threshold:
                candidates.append((fp1, fp2, score))

    # Sort descending by heuristic score
    candidates.sort(key=lambda item: item[2], reverse=True)
    return candidates
