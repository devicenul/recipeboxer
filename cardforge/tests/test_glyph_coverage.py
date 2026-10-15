"""Tests for font glyph coverage, character transliteration, and self-checks.

Verifies:
- Font cmap auditing across all bundled cardforge fonts.
- Self-check (MissingGlyphError) when rendered text lacks glyphs (no silent dropping/tofu).
- Consistent transliteration (e.g. ½ -> 1/2) for fonts lacking vulgar fraction glyphs.
- Verbatim text records exactly what is visually rendered on the card canvas.
- Fallback font rendering support.
"""

from __future__ import annotations

import random
from PIL import Image, ImageDraw

import pytest

from cardforge import (
    CardModality,
    CardStock,
    CardWriter,
    MissingGlyphError,
    RecipeSchema,
    SyntheticCardGenerator,
    adapt_text_for_font,
    draw_text_with_fallback,
    get_font,
    get_font_cmap,
    validate_font_glyphs,
)


def test_font_cmap_audit():
    """Verify cmap coverage for bundled fonts and identify known missing glyphs."""
    cmap_caveat = get_font_cmap("caveat")
    cmap_special_elite = get_font_cmap("special_elite")
    cmap_marck = get_font_cmap("marck_script")

    assert cmap_caveat is not None, "Caveat cmap must be available"
    assert cmap_special_elite is not None, "Special Elite cmap must be available"
    assert cmap_marck is not None, "Marck Script cmap must be available"

    # Caveat has all fractions
    for frac in ("¼", "½", "¾", "⅓", "⅔", "⅛", "⅜", "⅝", "⅞"):
        assert ord(frac) in cmap_caveat, f"Caveat should support {frac}"

    # Special Elite has 1/4, 1/2, 3/4, but misses thirds and eighths
    assert ord("½") in cmap_special_elite
    assert ord("¼") in cmap_special_elite
    assert ord("¾") in cmap_special_elite
    assert ord("⅓") not in cmap_special_elite
    assert ord("⅔") not in cmap_special_elite
    assert ord("⅛") not in cmap_special_elite

    # Marck Script misses all vulgar fractions
    for frac in ("¼", "½", "¾", "⅓", "⅔", "⅛", "⅜", "⅝", "⅞"):
        assert ord(frac) not in cmap_marck, f"Marck Script should lack {frac}"


def test_self_check_fails_on_missing_glyph_marck_script():
    """Verify cardforge self-check fails generation when a font lacks glyphs."""
    font = get_font("marck_script", size=16)

    # Valid ASCII passes
    validate_font_glyphs("1/2 cup butter, 1 tsp salt", font, "marck_script")

    # Vulgar fractions in Marck Script must fail with MissingGlyphError
    with pytest.raises(MissingGlyphError) as exc_info:
        validate_font_glyphs("½ cup butter", font, "marck_script")

    err = str(exc_info.value)
    assert "lacks glyph(s)" in err
    assert "U+00BD" in err or "½" in err


def test_self_check_fails_on_unmapped_character():
    """Verify an unknown or unsupported unicode character fails generation."""
    font = get_font("caveat", size=16)
    # Snowman symbol U+2603 is not in caveat
    with pytest.raises(MissingGlyphError) as exc_info:
        validate_font_glyphs("Recipe \u2603 Snow", font, "caveat")
    assert "U+2603" in str(exc_info.value)


def test_adapt_text_for_font_transliterates_fractions():
    """Verify adapt_text_for_font transliterates vulgar fractions for fonts lacking them."""
    # Marck Script lacks fractions -> transliterates to ASCII consistently
    raw = "½ cup sugar, ⅓ cup butter, ¼ tsp cinnamon, ¾ cup flour, ⅛ tsp clove"
    adapted = adapt_text_for_font(raw, "marck_script")
    assert "1/2 cup sugar" in adapted
    assert "1/3 cup butter" in adapted
    assert "1/4 tsp cinnamon" in adapted
    assert "3/4 cup flour" in adapted
    assert "1/8 tsp clove" in adapted

    # Adapted text must pass glyph validation for marck_script
    font_marck = get_font("marck_script", size=16)
    validate_font_glyphs(adapted, font_marck, "marck_script")


def test_adapt_text_for_font_preserves_supported_fractions():
    """Verify adapt_text_for_font preserves vulgar fractions when the font supports them."""
    raw = "½ cup sugar, ⅓ cup butter"
    adapted = adapt_text_for_font(raw, "caveat")
    assert "½ cup sugar" in adapted
    assert "⅓ cup butter" in adapted

    font_caveat = get_font("caveat", size=16)
    validate_font_glyphs(adapted, font_caveat, "caveat")


def test_render_handwritten_lines_enforces_self_check():
    """Verify render_handwritten_lines fails immediately when text has missing glyphs."""
    img = Image.new("RGBA", (400, 200), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    rng = random.Random(42)

    # Missing fraction in marck_script must fail
    from cardforge.generator import render_handwritten_lines
    with pytest.raises(MissingGlyphError):
        render_handwritten_lines(
            draw,
            ["½ cup butter"],
            40, 40, 300, 200,
            (0, 0, 0), 14, rng,
            font_style="marck_script",
        )

    # Transliterated fraction must succeed
    y = render_handwritten_lines(
        draw,
        ["1/2 cup butter"],
        40, 40, 300, 200,
        (0, 0, 0), 14, rng,
        font_style="marck_script",
    )
    assert y > 40


def test_render_typewritten_lines_enforces_self_check():
    """Verify render_typewritten_lines fails immediately on unsupported glyphs."""
    img = Image.new("RGBA", (400, 200), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    rng = random.Random(42)

    from cardforge.generator import render_typewritten_lines
    # Special elite lacks 1/3
    with pytest.raises(MissingGlyphError):
        render_typewritten_lines(
            draw,
            ["- ⅓ cup milk"],
            40, 40, 300, 200,
            (0, 0, 0), 13, rng,
            font_style="special_elite",
        )

    # Transliterated 1/3 must succeed
    y = render_typewritten_lines(
        draw,
        ["- 1/3 cup milk"],
        40, 40, 300, 200,
        (0, 0, 0), 13, rng,
        font_style="special_elite",
    )
    assert y > 40


def test_draw_text_with_fallback():
    """Verify fallback font draws characters when primary font lacks glyphs."""
    img = Image.new("RGBA", (300, 100), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    primary = get_font("marck_script", size=18)
    fallback = get_font("caveat", size=18)

    # With fallback font provided, drawing ½ succeeds
    draw_text_with_fallback(
        draw, (10, 10), "½ cup butter", primary, "marck_script", (0, 0, 0), fallback_font=fallback
    )

    # Without fallback font, drawing ½ fails
    with pytest.raises(MissingGlyphError):
        draw_text_with_fallback(
            draw, (10, 10), "½ cup butter", primary, "marck_script", (0, 0, 0), fallback_font=None
        )


def test_generate_recipe_card_faces_exact_verbatim_tracking():
    """Verify generate_recipe_card_faces adapts text and tracks exact verbatim on canvas."""
    gen = SyntheticCardGenerator(seed=12345)
    writer = CardWriter(writer_id="w_test", font_style="marck_script", modality=CardModality.HANDWRITTEN_CURSIVE)
    stock = CardStock(stock_id="s_test")

    recipe = RecipeSchema(
        recipe_id="r_test",
        title="Dutch Apple Cake",
        ingredients=["½ cup butter", "⅓ cup sugar", "1 cup flour"],
        directions=["Mix butter and sugar thoroughly.", "Bake 30 minutes."],
    )

    cards = gen.generate_recipe_card_faces(
        recipe=recipe,
        num_faces=1,
        stock=stock,
        writer=writer,
        continuation_cue=False,
    )

    assert len(cards) == 1
    card = cards[0]
    verbatim = card.verbatim_text

    # The card was rendered in marck_script, which lacks ½ and ⅓.
    # Text was adapted to 1/2 and 1/3, and verbatim MUST record 1/2 and 1/3!
    assert "1/2 cup butter" in verbatim
    assert "1/3 cup sugar" in verbatim
    assert "½" not in verbatim
    assert "⅓" not in verbatim


def test_title_body_consistency_catches_mismatch():
    """Verify check_title_body_consistency catches mismatched recipe titles vs bodies."""
    from evals.curation.build_synthetic import check_title_body_consistency

    # Bug case 1: Dutch Apple Cake II with Hot Cross Buns body (no apples)
    ok, err = check_title_body_consistency(
        "Dutch Apple Cake II",
        ["1 cup scalded milk", "1/3 cup butter", "1/2 cup raisins"],
        ["Shape like biscuits.", "Bake 20 minutes."],
    )
    assert not ok
    assert "apple" in err.lower()

    # Bug case 2: Oatmeal Muffins with Squash Biscuits body (no oatmeal)
    ok, err = check_title_body_consistency(
        "Raised Oatmeal Muffins (Modern)",
        ["1/2 cup squash", "1/4 cup sugar", "1 cup milk"],
        ["Add squash and sugar.", "Bake."],
    )
    assert not ok
    assert "oatmeal" in err.lower()

    # Valid case: Dutch Apple Cake with apples in ingredients
    ok, err = check_title_body_consistency(
        "Dutch Apple Cake II",
        ["1 cup scalded milk", "1/3 cup butter", "5 sour apples", "flour"],
        ["Pare apples and press into dough.", "Bake thirty minutes."],
    )
    assert ok
    assert err == ""


def test_make_near_duplicate_variant_consistency():
    """Verify make_near_duplicate_variant produces consistent variants."""
    from evals.curation.build_synthetic import make_near_duplicate_variant, check_title_body_consistency

    base = {
        "title": "Dutch Apple Cake",
        "ingredients": ["1 cup scalded milk", "⅓ cup butter", "⅓ cup sugar", "5 sour apples"],
        "directions": ["Mix first four ingredients.", "Bake in moderate oven thirty minutes."],
    }

    variant = make_near_duplicate_variant(base, "Dutch Apple Cake II", "numeric", seed=42)
    assert variant["title"] == "Dutch Apple Cake II"
    # Must contain apples
    assert any("apples" in ig for ig in variant["ingredients"])
    # Must pass consistency check
    ok, err = check_title_body_consistency(variant["title"], variant["ingredients"], variant["directions"])
    assert ok, f"Variant failed consistency: {err}"

