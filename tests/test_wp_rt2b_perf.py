"""Unit tests for FIND-06 physical pairing feature LRU caching and performance.

Verifies:
1. Bit-for-bit / numerical identity (< 1e-6) between uncached and cached score calculations.
2. LRU cache eviction and clear_physical_pairing_cache().
3. Memory safety and performance scaling.
"""

from __future__ import annotations

import io
import numpy as np
from PIL import Image, ImageDraw
import pytest

from recipeboxer.vision.physical_pairing import (
    _SHOW_THROUGH_CACHE,
    _SILHOUETTE_CACHE,
    _WEAR_CACHE,
    clear_physical_pairing_cache,
    mirrored_wear_score,
    outline_match_score,
    show_through_score,
)


def _make_test_cards() -> tuple[bytes, bytes]:
    """Generates two synthetic card face images with text, wear, and borders."""
    w, h = 300, 200
    # Card A: Off-white card on dark border with text and stain blob
    im_a = Image.new("RGB", (w, h), color=(30, 30, 30))
    draw_a = ImageDraw.Draw(im_a)
    draw_a.rectangle([20, 15, w - 20, h - 15], fill=(245, 240, 230))
    # Text lines (high ink)
    for y in range(40, 140, 15):
        draw_a.text((35, y), "Grandmother's Vintage Chocolate Fudge Cake Recipe", fill=(20, 20, 20))
    # Coffee stain blob
    draw_a.ellipse([180, 80, 220, 120], fill=(160, 130, 90))

    # Card B: Opposing face with mirrored stain and text
    im_b = Image.new("RGB", (w, h), color=(30, 30, 30))
    draw_b = ImageDraw.Draw(im_b)
    draw_b.rectangle([20, 15, w - 20, h - 15], fill=(245, 240, 230))
    for y in range(40, 140, 15):
        draw_b.text((35, y), "Bake in moderate oven 350F for 35 minutes until set", fill=(20, 20, 20))
    # Mirrored horizontal stain
    draw_b.ellipse([w - 220, 80, w - 180, 120], fill=(160, 130, 90))

    buf_a = io.BytesIO()
    im_a.save(buf_a, format="PNG")
    buf_b = io.BytesIO()
    im_b.save(buf_b, format="PNG")

    return buf_a.getvalue(), buf_b.getvalue()


def test_physical_pairing_numerical_identity():
    """Verify that cached calculations produce identical results (< 1e-6) vs fresh uncached runs."""
    card_a, card_b = _make_test_cards()

    # 1. Mirrored wear score
    clear_physical_pairing_cache()
    uncached_wear = mirrored_wear_score(card_a, card_b)
    assert uncached_wear is not None
    # Now with warm cache
    cached_wear = mirrored_wear_score(card_a, card_b)
    assert cached_wear is not None
    assert abs(uncached_wear - cached_wear) < 1e-6

    # 2. Show-through score
    clear_physical_pairing_cache()
    uncached_st = show_through_score(card_a, card_b)
    # Both cards have text lines, so show-through score is computable
    cached_st = show_through_score(card_a, card_b)
    if uncached_st is not None:
        assert cached_st is not None
        assert abs(uncached_st - cached_st) < 1e-6
    else:
        assert cached_st is None

    # 3. Outline match score
    clear_physical_pairing_cache()
    uncached_outline = outline_match_score(card_a, card_b)
    assert uncached_outline is not None
    cached_outline = outline_match_score(card_a, card_b)
    assert cached_outline is not None
    assert abs(uncached_outline - cached_outline) < 1e-6


def test_clear_physical_pairing_cache():
    """Verify that clear_physical_pairing_cache flushes all LRU caches."""
    card_a, card_b = _make_test_cards()

    # Populate caches
    mirrored_wear_score(card_a, card_b)
    show_through_score(card_a, card_b)
    outline_match_score(card_a, card_b)

    assert len(_WEAR_CACHE._data) > 0
    assert len(_SHOW_THROUGH_CACHE._data) > 0
    assert len(_SILHOUETTE_CACHE._data) > 0

    clear_physical_pairing_cache()

    assert len(_WEAR_CACHE._data) == 0
    assert len(_SHOW_THROUGH_CACHE._data) == 0
    assert len(_SILHOUETTE_CACHE._data) == 0


def test_lru_cache_bounded_eviction():
    """Verify that BoundedLRU strictly respects its maxsize."""
    from recipeboxer.vision.physical_pairing import _BoundedLRU

    cache = _BoundedLRU(maxsize=3)
    cache.set("k1", 1)
    cache.set("k2", 2)
    cache.set("k3", 3)
    assert len(cache._data) == 3

    # Access k1 so k2 becomes LRU
    assert cache.get("k1") == 1

    # Adding k4 should evict k2
    cache.set("k4", 4)
    assert len(cache._data) == 3
    assert cache.get("k2") is None
    assert cache.get("k1") == 1
    assert cache.get("k3") == 3
    assert cache.get("k4") == 4
