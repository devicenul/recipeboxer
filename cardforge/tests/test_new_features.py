"""Tests for extended CardForge features: rulings, stock, writer, degradations, and multi-face generation.

All tests use synthetic / lorem-style test recipes to ensure no external cookbook
source text is present in the repository test suite.
"""

from __future__ import annotations

import hashlib
from PIL import Image
import pytest

from cardforge.models import (
    CardModality,
    CardRole,
    CardStock,
    CardWriter,
    DegradationConfig,
    DegradationLevel,
    GeneratedCard,
    InkType,
    PaperSubstrate,
    RecipeSchema,
    RulingStyle,
)
from cardforge.generator import (
    SyntheticCardGenerator,
    apply_crease,
    apply_glare,
    apply_jpeg_noise,
    apply_perspective_warp,
    apply_show_through,
    create_paper_substrate,
    get_font,
)


@pytest.fixture
def lorem_recipe() -> RecipeSchema:
    """Returns a synthetic lorem-style test recipe with neutral text."""
    return RecipeSchema(
        recipe_id="rec_lorem_test_01",
        title="Lorem Ipsum Biscuit",
        yield_servings="6 servings",
        total_time="30 mins",
        ingredients=[
            "2 cups lorem flour",
            "1/2 cup ipsum butter",
            "1 tbsp dolor sugar",
            "1 tsp sit salt",
            "3/4 cup amet milk",
        ],
        directions=[
            "1. Combine dry lorem ingredients in bowl.",
            "2. Cut in ipsum butter until crumbly.",
            "3. Stir in amet milk to form soft dough.",
            "4. Roll out and cut into rounds.",
            "5. Bake at 400 degrees for 12 to 15 minutes.",
        ],
        tags=["Test", "Baking"],
        source_notes="Synthetic Test Note",
    )


def test_ruling_styles_render(lorem_recipe: RecipeSchema):
    """Verify that all RulingStyle enum values can be rendered without errors."""
    gen = SyntheticCardGenerator(seed=42)
    for ruling in RulingStyle:
        img, base_col = create_paper_substrate(
            width=680,
            height=440,
            substrate=PaperSubstrate.RULED_INDEX,
            rng=gen.rng,
            ruling=ruling,
        )
        assert isinstance(img, Image.Image)
        assert img.size == (680, 440)


def test_custom_stock_and_writer_integration(lorem_recipe: RecipeSchema):
    """Verify that CardStock and CardWriter parameters are respected during generation."""
    gen = SyntheticCardGenerator(seed=101)
    stock = CardStock(
        stock_id="stock_kraft_wide",
        name="Kraft Wide Ruled",
        substrate=PaperSubstrate.KRAFT_BROWN,
        ruling=RulingStyle.WIDE_RULED,
        width=700,
        height=450,
        aging_level=DegradationLevel.MILD,
    )
    writer = CardWriter(
        writer_id="writer_fountain_blue",
        name="Test Scribe",
        modality=CardModality.HANDWRITTEN_CURSIVE,
        font_style="caveat",
        ink_color=(20, 40, 140),
        ink_type=InkType.FOUNTAIN_PEN,
    )

    cards = gen.generate_recipe_card_faces(
        recipe=lorem_recipe,
        num_faces=2,
        stock=stock,
        writer=writer,
    )

    assert len(cards) == 2
    for card in cards:
        assert card.stock_id == "stock_kraft_wide"
        assert card.writer_id == "writer_fountain_blue"
        assert card.image.size == (700, 450)
        assert card.physical_characteristics["stock_id"] == "stock_kraft_wide"
        assert card.physical_characteristics["writer_id"] == "writer_fountain_blue"


def test_new_degradation_transforms():
    """Verify crease, perspective, glare, jpeg_noise, and show_through."""
    base_img = Image.new("RGB", (680, 440), (245, 240, 230))

    # Crease
    creased = apply_crease(base_img, count=2, orientation="HORIZONTAL")
    assert creased.size == (680, 440)
    assert hashlib.sha256(creased.tobytes()).hexdigest() != hashlib.sha256(base_img.tobytes()).hexdigest()

    # Perspective warp
    warped = apply_perspective_warp(base_img, magnitude=0.04)
    assert warped.size == (680, 440)

    # Glare
    glared = apply_glare(base_img, alpha=75)
    assert glared.size == (680, 440)

    # JPEG noise
    noisy = apply_jpeg_noise(base_img, quality=40)
    assert noisy.size == (680, 440)

    # Show-through
    other_img = Image.new("RGB", (680, 440), (200, 200, 200))
    front_st, back_st = apply_show_through(base_img, other_img, opacity=0.1)
    assert front_st.size == (680, 440)
    assert back_st.size == (680, 440)


def test_multi_face_distribution_1_to_5(lorem_recipe: RecipeSchema):
    """Verify that multi-face splitting generates correct counts and roles for 1 to 5 faces."""
    gen = SyntheticCardGenerator(seed=202)
    stock = CardStock(stock_id="stock_s1")
    writer = CardWriter(writer_id="writer_w1")

    for n in range(1, 6):
        cards = gen.generate_recipe_card_faces(
            recipe=lorem_recipe,
            num_faces=n,
            stock=stock,
            writer=writer,
        )
        assert len(cards) == n
        assert cards[0].total_faces == n
        assert cards[0].face_index == 0

        if n == 1:
            assert cards[0].role == CardRole.STANDALONE
        else:
            assert cards[0].role == CardRole.SEQUENCE_START
            for follower in cards[1:]:
                assert follower.role == CardRole.SEQUENCE_CONTINUATION


def test_verbatim_text_tracking(lorem_recipe: RecipeSchema):
    """Verify that every generated card carries exact verbatim text matching rendered lines."""
    gen = SyntheticCardGenerator(seed=303)
    cards = gen.generate_recipe_card_faces(
        recipe=lorem_recipe,
        num_faces=2,
    )
    for card in cards:
        assert card.verbatim_text is not None
        assert len(card.verbatim_text.strip()) > 0
        # Title should appear on face 1
        if card.face_index == 0:
            assert "Lorem Ipsum Biscuit" in card.verbatim_text


def test_continuation_cue_presence_and_absence(lorem_recipe: RecipeSchema):
    """Verify continuation cue presence when enabled and absence when disabled."""
    gen = SyntheticCardGenerator(seed=404)
    stock = CardStock(stock_id="s1")
    writer = CardWriter(writer_id="w1", modality=CardModality.HANDWRITTEN_CURSIVE)

    # With cue explicitly enabled
    cards_with_cue = gen.generate_recipe_card_faces(
        recipe=lorem_recipe,
        num_faces=2,
        stock=stock,
        writer=writer,
        continuation_cue=True,
    )
    assert "(over ->)" in cards_with_cue[0].verbatim_text

    # With cue explicitly disabled
    cards_no_cue = gen.generate_recipe_card_faces(
        recipe=lorem_recipe,
        num_faces=2,
        stock=stock,
        writer=writer,
        continuation_cue=False,
    )
    assert "(over ->)" not in cards_no_cue[0].verbatim_text


def test_deterministic_reproducibility_new_features(lorem_recipe: RecipeSchema):
    """Verify deterministic output for identical seeds with new multi-face generator."""
    gen1 = SyntheticCardGenerator(seed=777)
    stock = CardStock(stock_id="stock_det", ruling=RulingStyle.DOUBLE_HEADER)
    writer = CardWriter(writer_id="writer_det", font_style="special_elite")
    cards1 = gen1.generate_recipe_card_faces(lorem_recipe, num_faces=2, stock=stock, writer=writer)

    gen2 = SyntheticCardGenerator(seed=777)
    cards2 = gen2.generate_recipe_card_faces(lorem_recipe, num_faces=2, stock=stock, writer=writer)

    h1 = hashlib.sha256(cards1[0].image.tobytes()).hexdigest()
    h2 = hashlib.sha256(cards2[0].image.tobytes()).hexdigest()
    assert h1 == h2, "Deterministic reproducibility failed for generate_recipe_card_faces"
    assert cards1[0].verbatim_text == cards2[0].verbatim_text
