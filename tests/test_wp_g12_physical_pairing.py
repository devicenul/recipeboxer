"""Unit and calibration tests for physical pairing evidence (WP-G12).

Tests adhere to WAVE4_CONTRACTS.md §1.6 and WAVE3_CONTRACTS.md §0:
- Deterministic synthetic card pairs via cardforge.
- Tests may import cardforge; production code does not.
- Evaluates ROC AUC on homogeneous stationery stock (same stock negatives).
- Verifies return None on uncomputable inputs (flush cards, blank cards, pristine cards).
- Benchmarks execution latency <= 400 ms per pair.
"""

from __future__ import annotations

import time
import numpy as np
import pytest
from PIL import Image, ImageDraw
from scipy.stats import rankdata

from cardforge.generator import SyntheticCardGenerator
from cardforge.models import (
    CardModality,
    CardStock,
    CardWriter,
    DegradationConfig,
    PaperSubstrate,
    RecipeSchema,
)
from cardforge.templates_t0 import ARCHIVAL_RECIPE_TEMPLATES
from recipeboxer.vision.physical_pairing import (
    mirrored_wear_score,
    outline_match_score,
    show_through_score,
)


def _compute_roc_auc(y_true: list[int], y_scores: list[float | None]) -> float | None:
    valid = [(yt, ys) for yt, ys in zip(y_true, y_scores) if ys is not None]
    if not valid:
        return None
    y_t = np.array([v[0] for v in valid])
    y_s = np.array([v[1] for v in valid])
    n_pos = np.sum(y_t == 1)
    n_neg = np.sum(y_t == 0)
    if n_pos == 0 or n_neg == 0:
        return None
    ranks = rankdata(y_s)
    u = np.sum(ranks[y_t == 1]) - (n_pos * (n_pos + 1)) / 2.0
    return float(u / (n_pos * n_neg))


# ============================================================================
# 1. CONTRACT & BOUNDARY TESTS
# ============================================================================

def test_contract_return_types_and_ranges():
    """Verifies that all three functions return float in [0.0, 1.0] or None."""
    blank = Image.new("RGB", (300, 200), (250, 245, 230))
    tiny = Image.new("RGB", (5, 5), (255, 255, 255))

    # Tiny images return None
    assert show_through_score(tiny, blank) is None
    assert mirrored_wear_score(tiny, blank) is None
    assert outline_match_score(tiny, blank) is None

    # Blank images without ink return None for show-through
    assert show_through_score(blank, blank) is None

    # Pristine cards without damage return None for mirrored wear
    assert mirrored_wear_score(blank, blank) is None

    # Flush borderless images return None for outline match
    assert outline_match_score(blank, blank) is None


def test_outline_match_computability():
    """outline_match_score returns None when no card boundary exists, float when present."""
    # Flush card without background
    card_flush = Image.new("RGB", (680, 440), (242, 235, 215))
    assert outline_match_score(card_flush, card_flush) is None

    # Card with scanner bed background
    bed_a = Image.new("RGB", (800, 600), (25, 25, 28))
    card_a = Image.new("RGB", (550, 360), (242, 235, 215))
    bed_a.paste(card_a, (120, 110))

    bed_b = Image.new("RGB", (800, 600), (25, 25, 28))
    bed_b.paste(card_a, (125, 105))

    score_same = outline_match_score(bed_a, bed_b)
    assert score_same is not None
    assert 0.85 <= score_same <= 1.0

    # Different card aspect ratio on scanner bed
    bed_diff = Image.new("RGB", (800, 600), (25, 25, 28))
    card_diff = Image.new("RGB", (380, 380), (242, 235, 215))  # Square card
    bed_diff.paste(card_diff, (150, 100))

    score_diff = outline_match_score(bed_a, bed_diff)
    assert score_diff is not None
    assert score_diff < score_same


# ============================================================================
# 2. PHYSICAL PAIRING ON CARDFORGE HOMOGENEOUS DECK
# ============================================================================

@pytest.fixture(scope="module")
def homogeneous_deck():
    """Generates synthetic card pairs sharing identical stock, writer, and aging."""
    gen = SyntheticCardGenerator(seed=888)
    stock = CardStock(stock_id="ruled_cream_index", substrate=PaperSubstrate.RULED_INDEX)
    writer = CardWriter(writer_id="author_1", modality=CardModality.HANDWRITTEN_CURSIVE)

    num_recipes = 12
    card_pairs = []
    for i in range(num_recipes):
        tmpl = ARCHIVAL_RECIPE_TEMPLATES[i % len(ARCHIVAL_RECIPE_TEMPLATES)]
        rec = RecipeSchema(**tmpl)
        deg = DegradationConfig(
            oil_smudges=1 if i % 2 == 0 else 0,
            coffee_rings=1 if i % 3 != 0 else 0,
            dog_ears=(i % 2 == 0),
            edge_tears=(i % 3 == 0),
            bleed_through=True,
        )
        faces = gen.generate_recipe_card_faces(
            rec,
            num_faces=2,
            stock=stock,
            writer=writer,
            degradation=deg,
        )
        card_pairs.append((faces[0].image, faces[1].image))

    return card_pairs


def test_show_through_discrimination_on_same_stock(homogeneous_deck):
    """Verifies that show_through_score discriminates same-card pairs from same-stock negatives."""
    pos_scores = []
    neg_scores = []

    n = len(homogeneous_deck)
    for i in range(n):
        front_i, back_i = homogeneous_deck[i]
        s_pos = show_through_score(front_i, back_i)
        assert s_pos is not None
        pos_scores.append(s_pos)

        # Same stock negative: front of recipe i vs back of recipe (i+1)%n
        _, back_j = homogeneous_deck[(i + 1) % n]
        s_neg = show_through_score(front_i, back_j)
        if s_neg is not None:
            neg_scores.append(s_neg)

    mean_pos = float(np.mean(pos_scores))
    mean_neg = float(np.mean(neg_scores))
    assert mean_pos > mean_neg, f"Expected pos ({mean_pos:.3f}) > neg ({mean_neg:.3f})"

    y_true = [1] * len(pos_scores) + [0] * len(neg_scores)
    y_scores = pos_scores + neg_scores
    auc = _compute_roc_auc(y_true, y_scores)
    assert auc is not None and auc >= 0.70, f"show_through AUC {auc} below target 0.70"


def test_mirrored_wear_discrimination_on_same_stock(homogeneous_deck):
    """Verifies that mirrored_wear_score distinguishes mirrored damage from same-stock negatives."""
    pos_scores = []
    neg_scores = []

    n = len(homogeneous_deck)
    for i in range(n):
        front_i, back_i = homogeneous_deck[i]
        s_pos = mirrored_wear_score(front_i, back_i)
        if s_pos is not None:
            pos_scores.append(s_pos)

        _, back_j = homogeneous_deck[(i + 1) % n]
        s_neg = mirrored_wear_score(front_i, back_j)
        if s_neg is not None:
            neg_scores.append(s_neg)

    assert len(pos_scores) > 0
    assert len(neg_scores) > 0
    mean_pos = float(np.mean(pos_scores))
    mean_neg = float(np.mean(neg_scores))
    assert mean_pos > mean_neg, f"Expected pos wear ({mean_pos:.3f}) > neg wear ({mean_neg:.3f})"

    y_true = [1] * len(pos_scores) + [0] * len(neg_scores)
    y_scores = pos_scores + neg_scores
    auc = _compute_roc_auc(y_true, y_scores)
    assert auc is not None and auc >= 0.70, f"mirrored_wear AUC {auc} below target 0.70"


def test_card_flip_axes_both_supported():
    """Verifies that both horizontal and vertical flip hypotheses are supported."""
    from PIL import ImageFilter

    w, h = 300, 200
    front = Image.new("RGB", (w, h), (245, 240, 225))
    draw_f = ImageDraw.Draw(front)
    # Draw ink text lines in top-right
    for y in range(40, 100, 12):
        draw_f.line([(180, y), (270, y)], fill=(30, 40, 70), width=2)

    # Horizontal flip counterpart: text lines in top-left
    back_h = Image.new("RGB", (w, h), (245, 240, 225))
    draw_bh = ImageDraw.Draw(back_h)
    for y in range(40, 100, 12):
        draw_bh.line([(30, y), (120, y)], fill=(30, 40, 70), width=2)

    # Apply authentic faint show-through of back_h onto front_h
    ghost_h = back_h.transpose(Image.FLIP_LEFT_RIGHT).filter(ImageFilter.GaussianBlur(1.5))
    front_h = Image.blend(front, ghost_h, 0.08)

    # Vertical flip counterpart: text lines in bottom-right
    back_v = Image.new("RGB", (w, h), (245, 240, 225))
    draw_bv = ImageDraw.Draw(back_v)
    for y in range(120, 180, 12):
        draw_bv.line([(180, y), (270, y)], fill=(30, 40, 70), width=2)

    # Apply authentic faint show-through of back_v onto front_v
    ghost_v = back_v.transpose(Image.FLIP_TOP_BOTTOM).filter(ImageFilter.GaussianBlur(1.5))
    front_v = Image.blend(front, ghost_v, 0.08)

    score_h = show_through_score(front_h, back_h)
    score_v = show_through_score(front_v, back_v)

    assert score_h is not None and score_h >= 0.70
    assert score_v is not None and score_v >= 0.70


def test_performance_latency_budget(homogeneous_deck):
    """Verifies that computing physical pairing evidence takes <= 400 ms per pair."""
    front, back = homogeneous_deck[0]

    # Warmup
    show_through_score(front, back)
    mirrored_wear_score(front, back)

    t0 = time.perf_counter()
    for _ in range(5):
        s_st = show_through_score(front, back)
        s_wear = mirrored_wear_score(front, back)
    t1 = time.perf_counter()

    avg_ms = ((t1 - t0) / 5.0) * 1000.0
    assert avg_ms <= 400.0, f"Physical pairing latency {avg_ms:.2f} ms exceeded 400 ms budget"
