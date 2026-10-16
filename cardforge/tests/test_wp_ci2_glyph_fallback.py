"""WP-CI2: the glyph self-check must still work when fontTools is unavailable (raster fallback).

Regression: CI lacked fontTools, get_font_cmap() returned None and the raster fallback only
flagged chars that render *blank*. Caveat's .notdef glyph is a visible box, so unmapped chars
(e.g. U+2603) passed silently. The fallback now also compares against the .notdef rendering.
"""

from __future__ import annotations

import pytest

import cardforge.generator as gen
from cardforge import MissingGlyphError, get_font, validate_font_glyphs


@pytest.fixture
def no_fonttools(monkeypatch):
    monkeypatch.setattr(gen, "TTFont", None)
    monkeypatch.setattr(gen, "_CMAP_CACHE", {})
    yield


def test_fallback_detects_unmapped_char_with_visible_notdef(no_fonttools):
    font = get_font("caveat", size=16)
    assert gen.get_font_cmap(font) is None  # really exercising the fallback
    with pytest.raises(MissingGlyphError) as exc_info:
        validate_font_glyphs("Recipe \u2603 Snow", font, "caveat")
    assert "U+2603" in str(exc_info.value)


def test_fallback_accepts_supported_glyphs(no_fonttools):
    font = get_font("caveat", size=16)
    validate_font_glyphs("1/2 cup butter, ½ ⅓ ¼", font, "caveat")


def test_fallback_detects_blank_missing_glyphs(no_fonttools):
    font = get_font("marck_script", size=16)
    with pytest.raises(MissingGlyphError):
        validate_font_glyphs("½ cup butter", font, "marck_script")


def test_fonttools_declared_dependency():
    """fontTools must be a declared cardforge dependency so CI gets the exact cmap check."""
    from pathlib import Path

    pyproject = (Path(gen.__file__).resolve().parent / "pyproject.toml").read_text(encoding="utf-8")
    assert "fonttools" in pyproject.lower()
