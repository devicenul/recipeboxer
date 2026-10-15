"""Directed pairwise correlation evidence engine (WP-GA, G10).

Computes raw pairwise features and evaluates collection-relative likelihood ratios:
1. Pixel features: paper LAB distance, ruling kind/spacing match, ink hue distance, aspect ratio
2. Capture features: temporal gap (seconds, signed), natural-sort adjacency including direction
3. Physical pairing: show-through, mirrored wear, and outline contour IoU via
   recipeboxer.vision.physical_pairing (skipped when images are absent; outline weighted weakly)
4. Header score: sequential continuation and header progression heuristics
5. Content features: cross-references, sentence continuity, step progression via
   recipeboxer.correlation.content.content_features (WP-GB concurrent module; lazy import)
"""

from __future__ import annotations

import math
from typing import Any, List, Optional, Protocol, Union

from recipeboxer.ingestion.capture import capture_gap_seconds
from recipeboxer.models.correlation import FeatureEvidence, PairEvidence
from recipeboxer.models.domain import VisualFingerprint
from recipeboxer.vision.heuristics import calculate_ruling_similarity, calculate_sequential_header_score


class ImageProvider(Protocol):
    """Protocol for retrieving raw card image bytes by card_id."""

    def get(self, card_id: str) -> bytes:
        ...


def _compute_paper_lab_distance(a: VisualFingerprint, b: VisualFingerprint) -> Optional[float]:
    """Computes Euclidean Delta E distance in CIE L*a*b* space."""
    lab_a = None
    if a.pixel and a.pixel.paper_lab:
        lab_a = a.pixel.paper_lab
    elif a.paper_color and hasattr(a.paper_color, "lab_l"):
        lab_a = (float(a.paper_color.lab_l), float(a.paper_color.lab_a), float(a.paper_color.lab_b))

    lab_b = None
    if b.pixel and b.pixel.paper_lab:
        lab_b = b.pixel.paper_lab
    elif b.paper_color and hasattr(b.paper_color, "lab_l"):
        lab_b = (float(b.paper_color.lab_l), float(b.paper_color.lab_a), float(b.paper_color.lab_b))

    if lab_a is None or lab_b is None:
        return None

    delta_e = math.sqrt(sum((x - y) ** 2 for x, y in zip(lab_a, lab_b)))
    return round(delta_e, 4)


def _compute_ruling_match(a: VisualFingerprint, b: VisualFingerprint) -> Optional[float]:
    """Computes ruling kind and line spacing match score in [0.0, 1.0]."""
    if a.pixel and b.pixel:
        kind_a = a.pixel.ruling_kind
        kind_b = b.pixel.ruling_kind
        if kind_a is not None and kind_b is not None:
            if kind_a != kind_b:
                return 0.0
            if kind_a == "none":
                return 1.0
            sp_a = a.pixel.ruling_spacing_px
            sp_b = b.pixel.ruling_spacing_px
            if sp_a is not None and sp_b is not None:
                diff = abs(sp_a - sp_b)
                return round(max(0.0, 1.0 - diff / 15.0), 4)
            return 0.90

    if a.ruling and b.ruling:
        return calculate_ruling_similarity(a.ruling, b.ruling)

    return None


def _compute_ink_hue_distance(a: VisualFingerprint, b: VisualFingerprint) -> Optional[float]:
    """Computes circular minimum hue distance in degrees [0.0, 180.0]."""
    hues_a = a.pixel.ink_hues if (a.pixel and a.pixel.ink_hues) else []
    hues_b = b.pixel.ink_hues if (b.pixel and b.pixel.ink_hues) else []

    if a.pixel and b.pixel:
        # Both uncolored/achromatic (e.g. black ink or graphite)
        if len(hues_a) == 0 and len(hues_b) == 0:
            return 0.0
        # One colored, one achromatic
        if len(hues_a) == 0 or len(hues_b) == 0:
            return 180.0

        min_circ = 180.0
        for ha in hues_a:
            for hb in hues_b:
                diff = abs(ha - hb) % 360.0
                circ = min(diff, 360.0 - diff)
                if circ < min_circ:
                    min_circ = circ
        return round(min_circ, 4)

    return None


def _compute_aspect_ratio_diff(a: VisualFingerprint, b: VisualFingerprint) -> Optional[float]:
    """Computes absolute difference in physical card aspect ratio."""
    if a.pixel and b.pixel:
        ar_a = a.pixel.aspect_ratio
        ar_b = b.pixel.aspect_ratio
        if ar_a and ar_b:
            return round(abs(ar_a - ar_b), 4)
    return None


def _compute_capture_gap(a: VisualFingerprint, b: VisualFingerprint) -> Optional[float]:
    """Computes signed capture time gap (t_b - t_a) in seconds."""
    meta_a = a.capture if a.capture else None
    meta_b = b.capture if b.capture else None
    if meta_a and meta_b:
        return capture_gap_seconds(meta_a, meta_b, signed=True)
    return None


def _compute_sort_adjacency(a: VisualFingerprint, b: VisualFingerprint) -> Optional[float]:
    """Computes natural-sort index adjacency including direction (idx_b - idx_a)."""
    meta_a = a.capture if a.capture else None
    meta_b = b.capture if b.capture else None
    if meta_a and meta_b:
        idx_a = getattr(meta_a, "natural_sort_index", None)
        idx_b = getattr(meta_b, "natural_sort_index", None)
        if idx_a is not None and idx_b is not None:
            return float(idx_b - idx_a)
    return None


def compute_raw_features(
    a: VisualFingerprint,
    b: VisualFingerprint,
    *,
    images: Optional[ImageProvider] = None,
) -> List[FeatureEvidence]:
    """Extracts raw pairwise features between candidate preceding card A and card B.

    Args:
        a: Preceding card visual fingerprint.
        b: Succeeding card visual fingerprint.
        images: Optional image provider for physical wear/bleed-through analysis.

    Returns:
        List of FeatureEvidence instances with populated `raw` and `log_lr=None`.
    """
    features: List[FeatureEvidence] = []

    # 1. Pixel substrate features
    features.append(FeatureEvidence(
        name="paper_lab",
        raw=_compute_paper_lab_distance(a, b),
        weight=1.0,
        note="paper LAB Euclidean distance Delta E",
    ))
    features.append(FeatureEvidence(
        name="ruling",
        raw=_compute_ruling_match(a, b),
        weight=1.0,
        note="ruling kind and spacing match [0, 1]",
    ))
    features.append(FeatureEvidence(
        name="ink_hue",
        raw=_compute_ink_hue_distance(a, b),
        weight=1.0,
        note="ink hue circular distance in degrees [0, 180]",
    ))
    features.append(FeatureEvidence(
        name="aspect_ratio",
        raw=_compute_aspect_ratio_diff(a, b),
        weight=1.0,
        note="aspect ratio absolute difference",
    ))

    # 2. Capture metadata features
    features.append(FeatureEvidence(
        name="capture_gap",
        raw=_compute_capture_gap(a, b),
        weight=1.0,
        note="capture time gap in seconds (signed b - a)",
    ))
    features.append(FeatureEvidence(
        name="sort_adjacency",
        raw=_compute_sort_adjacency(a, b),
        weight=1.0,
        note="natural-sort index delta (idx_b - idx_a)",
    ))

    # 3. Physical pairing features (show-through, mirrored wear, outline contour)
    raw_show_through: Optional[float] = None
    raw_mirrored_wear: Optional[float] = None
    raw_outline: Optional[float] = None

    if images is not None:
        try:
            bytes_a = images.get(a.card_id)
            bytes_b = images.get(b.card_id)
            if bytes_a and bytes_b:
                from recipeboxer.vision.physical_pairing import (
                    mirrored_wear_score,
                    outline_match_score,
                    show_through_score,
                )
                raw_show_through = show_through_score(bytes_a, bytes_b)
                raw_mirrored_wear = mirrored_wear_score(bytes_a, bytes_b)
                raw_outline = outline_match_score(bytes_a, bytes_b)
        except Exception:
            pass

    features.append(FeatureEvidence(
        name="show_through",
        raw=raw_show_through,
        weight=1.0,
        note="mirrored reverse-side ghost text residue correlation",
    ))
    features.append(FeatureEvidence(
        name="mirrored_wear",
        raw=raw_mirrored_wear,
        weight=1.0,
        note="mirrored stain and physical edge damage alignment",
    ))
    # Outline treated as weak: synthetic AUC of 1.0 is likely a generator artifact
    features.append(FeatureEvidence(
        name="outline",
        raw=raw_outline,
        weight=0.2,
        note="card border silhouette contour IoU (weak weight)",
    ))

    # 4. Header heuristics score
    raw_header = calculate_sequential_header_score(a, b)
    features.append(FeatureEvidence(
        name="header_score",
        raw=raw_header,
        weight=1.0,
        note="sequential header and narrative continuation heuristics",
    ))

    # 5. Content features via WP-GB (lazy import with graceful fallback)
    try:
        from recipeboxer.correlation.content import content_features
        c_feats = content_features(a, b)
        for cf in c_feats:
            features.append(FeatureEvidence(
                name=cf.name,
                raw=cf.raw,
                weight=getattr(cf, "weight", 1.0),
                note=getattr(cf, "note", "content continuity feature"),
            ))
    except (ImportError, AttributeError):
        pass

    return features


def pair_evidence(
    a: VisualFingerprint,
    b: VisualFingerprint,
    background: Any,
    *,
    images: Optional[ImageProvider] = None,
) -> PairEvidence:
    """Computes directed pairwise correlation evidence between card A and card B.

    Hypothesis: Card A directly precedes Card B (A -> B).

    Args:
        a: Preceding candidate card.
        b: Succeeding candidate card.
        background: DeckBackground model fitted on this collection.
        images: Optional image provider for physical show-through and wear extraction.

    Returns:
        PairEvidence model with total_log_lr and collection-relative feature log_lrs.
    """
    card_id_a = getattr(a, "card_id", str(a))
    card_id_b = getattr(b, "card_id", str(b))

    raw_features = compute_raw_features(a, b, images=images)

    evaluated_features: List[FeatureEvidence] = []
    content_evidence_used = False

    for feat in raw_features:
        log_lr = None
        if feat.raw is not None and background is not None:
            # Check if background supports card-level context
            log_lr = background.log_lr(feat.name, feat.raw, fp_a=a, fp_b=b)

        evaluated_features.append(FeatureEvidence(
            name=feat.name,
            raw=feat.raw,
            log_lr=log_lr,
            weight=feat.weight,
            note=feat.note,
        ))

        # Check if content features provided evidence
        if feat.name.startswith("content_") or feat.name in (
            "sentence_continuity",
            "step_continuity",
            "title_position",
            "embedding_sim",
            "ocr_cue",
        ):
            if feat.raw is not None:
                content_evidence_used = True

    total_log_lr = sum(
        f.weight * f.log_lr for f in evaluated_features if f.log_lr is not None
    )

    return PairEvidence(
        a=card_id_a,
        b=card_id_b,
        features=evaluated_features,
        total_log_lr=round(total_log_lr, 4),
        p_link=None,
        content_evidence_used=content_evidence_used,
    )
