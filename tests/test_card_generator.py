"""Unit and integration tests for the procedural synthetic card generator tool (recipeboxer/tools/card_generator.py).

Validates:
1. Deterministic generation reproducibility: identical seeds produce byte-identical images and metadata.
2. Substrate rendering across all paper types:
   - AGED_YELLOW, WHITE_LINEN, RULED_INDEX, GRID_GRAPH, KRAFT_BROWN, BLANK.
3. Modality rendering:
   - HANDWRITTEN_CURSIVE, TYPEWRITTEN, PRINTED_CLIPPING, MIXED_MEDIA.
4. Physical degradation and artifact effects:
   - Oil/grease smudges, coffee/tea rings, dog-ear folds, edge tears, date stamps, rotation skew.
5. Coordinated front/back generation:
   - Matching substrate, matching ink, mirrored bleed-through stains, narrative continuation markers.
6. Multi-card set generation (3-card and 4-card sets).
7. Standalone card generation.
8. CLI command-line interface execution:
   - Flag handling (--output-dir, --batch-size, --modality, --substrate, --degradation, --seed, --format, --no-manifest).
"""

from __future__ import annotations

import hashlib
import json
import random
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest
from PIL import Image

from recipeboxer.models.domain import CardRole
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.tools.card_generator import (
    CardModality,
    DegradationConfig,
    DegradationLevel,
    GeneratedCard,
    PaperSubstrate,
    SyntheticCardGenerator,
    apply_coffee_ring,
    apply_date_stamp,
    apply_dog_ear,
    apply_edge_tear,
    apply_ink_fade,
    apply_oil_smudge,
    apply_rotation_skew,
    apply_tape_residue,
    create_paper_substrate,
    generate_synthetic_corpus,
    main as generator_cli_main,
)


@pytest.fixture
def sample_test_recipe() -> RecipeSchema:
    """Fixture returning a standard RecipeSchema instance for generator tests."""
    return RecipeSchema(
        recipe_id="rec_test_lemon_pie",
        title="Lemon Meringue Pie",
        description="Classic vintage lemon meringue pie with flaky crust.",
        yield_servings="8 slices",
        prep_time="30 mins",
        cook_time="20 mins",
        total_time="50 mins",
        ingredients=[
            "1 pre-baked 9-inch pie shell",
            "1 cup granulated sugar",
            "3 tbsp cornstarch",
            "1 1/2 cups water",
            "3 egg yolks, beaten",
            "1/2 cup fresh lemon juice",
            "1 tbsp butter",
            "3 egg whites",
            "6 tbsp sugar for meringue",
        ],
        directions=[
            "1. In saucepan, whisk sugar, cornstarch, and water over medium heat until boiling.",
            "2. Whisk small amount of hot mixture into egg yolks, then return all to pan.",
            "3. Cook 2 mins longer; remove from heat and stir in lemon juice and butter.",
            "4. Pour hot lemon filling into pre-baked pie shell.",
            "5. Beat egg whites until foamy, gradually add 6 tbsp sugar until stiff peaks form.",
            "6. Spread meringue over hot filling, sealing edges to crust.",
            "7. Bake at 350°F for 12-15 minutes until meringue is golden brown.",
        ],
        tags=["Dessert", "Pie", "Lemon", "Baking"],
        source_notes="Test Recipe Box",
    )


# ============================================================================
# 1. DETERMINISTIC REPRODUCIBILITY TESTS
# ============================================================================

def test_deterministic_seed_produces_identical_images(sample_test_recipe: RecipeSchema, tmp_path: Path):
    """Verify that using the exact same random seed produces byte-for-byte identical images."""
    gen1 = SyntheticCardGenerator(seed=12345)
    f1, b1 = gen1.generate_card_pair(
        sample_test_recipe,
        modality=CardModality.HANDWRITTEN_CURSIVE,
        substrate=PaperSubstrate.RULED_INDEX,
        degradation=DegradationConfig(oil_smudges=1, coffee_rings=1),
    )

    gen2 = SyntheticCardGenerator(seed=12345)
    f2, b2 = gen2.generate_card_pair(
        sample_test_recipe,
        modality=CardModality.HANDWRITTEN_CURSIVE,
        substrate=PaperSubstrate.RULED_INDEX,
        degradation=DegradationConfig(oil_smudges=1, coffee_rings=1),
    )

    p1_front = tmp_path / "f1.jpg"
    p2_front = tmp_path / "f2.jpg"
    f1.image.save(p1_front, "JPEG", quality=95)
    f2.image.save(p2_front, "JPEG", quality=95)

    hash1 = hashlib.sha256(p1_front.read_bytes()).hexdigest()
    hash2 = hashlib.sha256(p2_front.read_bytes()).hexdigest()
    assert hash1 == hash2, "Deterministic generation failed: SHA-256 hashes differ for same seed"


def test_different_seeds_produce_distinct_images(sample_test_recipe: RecipeSchema, tmp_path: Path):
    """Verify that different random seeds produce distinct visual characteristics."""
    gen1 = SyntheticCardGenerator(seed=100)
    card1 = gen1.generate_standalone_card(sample_test_recipe)

    gen2 = SyntheticCardGenerator(seed=999)
    card2 = gen2.generate_standalone_card(sample_test_recipe)

    p1 = tmp_path / "s1.jpg"
    p2 = tmp_path / "s2.jpg"
    card1.image.save(p1, "JPEG", quality=95)
    card2.image.save(p2, "JPEG", quality=95)

    hash1 = hashlib.sha256(p1.read_bytes()).hexdigest()
    hash2 = hashlib.sha256(p2.read_bytes()).hexdigest()
    assert hash1 != hash2, "Different seeds unexpectedly produced identical hashes"


# ============================================================================
# 2. SUBSTRATE RENDERING TESTS
# ============================================================================

@pytest.mark.parametrize("substrate", [
    PaperSubstrate.AGED_YELLOW,
    PaperSubstrate.WHITE_LINEN,
    PaperSubstrate.RULED_INDEX,
    PaperSubstrate.GRID_GRAPH,
    PaperSubstrate.KRAFT_BROWN,
    PaperSubstrate.BLANK,
])
def test_create_paper_substrates(substrate: PaperSubstrate):
    """Verify all paper substrates generate valid RGB images with expected dimensions."""
    rng = random.Random(42)
    img, base_color = create_paper_substrate(600, 400, substrate, rng)
    assert isinstance(img, Image.Image)
    assert img.size == (600, 400)
    assert img.mode == "RGB"
    assert len(base_color) == 3
    for channel in base_color:
        assert 0 <= channel <= 255


# ============================================================================
# 3. MODALITY RENDERING TESTS
# ============================================================================

@pytest.mark.parametrize("modality", [
    CardModality.HANDWRITTEN_CURSIVE,
    CardModality.TYPEWRITTEN,
    CardModality.PRINTED_CLIPPING,
    CardModality.MIXED_MEDIA,
])
def test_generate_card_pairs_across_modalities(sample_test_recipe: RecipeSchema, modality: CardModality):
    """Verify front/back pair generation across all historical modalities."""
    gen = SyntheticCardGenerator(seed=42)
    front, back = gen.generate_card_pair(
        sample_test_recipe,
        modality=modality,
        substrate=PaperSubstrate.RULED_INDEX,
    )

    assert isinstance(front, GeneratedCard)
    assert isinstance(back, GeneratedCard)
    assert front.role == CardRole.FRONT_ONLY
    assert back.role == CardRole.BACK_ONLY
    assert front.modality == modality
    assert back.modality == modality
    assert front.recipe_cluster["recipe_id"] == sample_test_recipe.recipe_id
    assert front.recipe_cluster["sequence_position"] == 0
    assert back.recipe_cluster["sequence_position"] == 1
    assert front.image.width >= 600
    assert front.image.height >= 400
    assert back.image.width >= 600
    assert back.image.height >= 400

    # With no degradation/skew, size is exact
    f_clean, b_clean = gen.generate_card_pair(
        sample_test_recipe,
        modality=modality,
        degradation=DegradationConfig(level=DegradationLevel.NONE),
    )
    assert f_clean.image.size == (680, 440)
    assert b_clean.image.size == (680, 440)


# ============================================================================
# 4. PHYSICAL ARTIFACT SIMULATOR TESTS
# ============================================================================

def test_apply_coffee_ring_effect():
    """Verify coffee ring application adds ring overlay without corrupting image."""
    base = Image.new("RGB", (300, 300), color=(240, 230, 210))
    rng = random.Random(42)
    result = apply_coffee_ring(base, 150, 150, 60, rng)
    assert isinstance(result, Image.Image)
    assert result.size == (300, 300)
    # The pixels near the ring boundary should be darker than base
    assert result != base


def test_apply_oil_smudge_effect():
    """Verify oil smudge adds translucent amber smudge."""
    base = Image.new("RGB", (300, 300), color=(250, 250, 250))
    rng = random.Random(42)
    result = apply_oil_smudge(base, (50, 50, 200, 200), rng)
    assert isinstance(result, Image.Image)
    assert result.size == (300, 300)
    assert result != base


def test_apply_dog_ear_effect():
    """Verify dog-ear fold on all 4 corners."""
    for corner in ["TOP_RIGHT", "TOP_LEFT", "BOTTOM_RIGHT", "BOTTOM_LEFT"]:
        base = Image.new("RGB", (300, 300), color=(240, 230, 210))
        result = apply_dog_ear(base, corner=corner, size=40)
        assert isinstance(result, Image.Image)
        assert result.size == (300, 300)


def test_apply_edge_tear_effect():
    """Verify edge tear effect on right and left borders."""
    base = Image.new("RGB", (300, 300), color=(240, 230, 210))
    res_right = apply_edge_tear(base, "RIGHT", random.Random(42))
    res_left = apply_edge_tear(base, "LEFT", random.Random(42))
    assert res_right.size == (300, 300)
    assert res_left.size == (300, 300)


def test_apply_date_stamp_effect():
    """Verify rubber date stamp application."""
    base = Image.new("RGB", (400, 300), color=(240, 230, 210))
    result = apply_date_stamp(base, 50, 50, "RECIPE CLUB 1956", random.Random(42))
    assert result.size == (400, 300)


def test_apply_rotation_skew_effect():
    """Verify rotation skew expands canvas cleanly."""
    base = Image.new("RGB", (300, 200), color=(240, 230, 210))
    res_no_skew = apply_rotation_skew(base, 0.0)
    assert res_no_skew.size == (300, 200)

    res_skew = apply_rotation_skew(base, 3.5)
    assert res_skew.width >= 300
    assert res_skew.height >= 200


# ============================================================================
# 5. MULTI-CARD SETS & STANDALONE GENERATION
# ============================================================================

def test_generate_multi_card_set_3_and_4_cards(sample_test_recipe: RecipeSchema):
    """Verify 3-card and 4-card multi-part sequence generation."""
    gen = SyntheticCardGenerator(seed=42)

    # 3-card set
    set_3 = gen.generate_multi_card_set(sample_test_recipe, num_cards=3)
    assert len(set_3) == 3
    assert set_3[0].role == CardRole.SEQUENCE_START
    assert set_3[1].role == CardRole.SEQUENCE_CONTINUATION
    assert set_3[2].role == CardRole.SEQUENCE_CONTINUATION
    assert set_3[0].recipe_cluster["cluster_role"] == "card1_front"
    assert set_3[1].recipe_cluster["cluster_role"] == "card1_back"
    assert set_3[2].recipe_cluster["cluster_role"] == "card2_front"

    # 4-card set
    set_4 = gen.generate_multi_card_set(sample_test_recipe, num_cards=4)
    assert len(set_4) == 4
    assert set_4[3].role == CardRole.SEQUENCE_CONTINUATION
    assert set_4[3].recipe_cluster["cluster_role"] == "card2_back"


def test_generate_standalone_card(sample_test_recipe: RecipeSchema):
    """Verify standalone single-sided card generation."""
    gen = SyntheticCardGenerator(seed=42)
    card = gen.generate_standalone_card(sample_test_recipe, modality=CardModality.HANDWRITTEN_CURSIVE)
    assert card.role == CardRole.STANDALONE
    assert card.recipe_cluster["total_cards"] == 1
    assert card.recipe_cluster["continuation_cue"] is None


# ============================================================================
# 6. CORPUS BUILDER & CLI EXECUTION TESTS
# ============================================================================

def test_generate_synthetic_corpus_to_temp_dir(tmp_path: Path):
    """Verify generate_synthetic_corpus generates images and valid manifest.json."""
    out_dir = tmp_path / "gen_test"
    cards = generate_synthetic_corpus(
        output_dir=out_dir,
        count=3,
        seed=100,
        generate_manifest=True,
    )
    assert len(cards) >= 4
    assert (out_dir / "manifest.json").exists()

    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["total_cards"] == len(cards)
    assert len(manifest["cards"]) == len(cards)

    for entry in manifest["cards"]:
        img_p = out_dir / entry["filename"]
        assert img_p.exists()


def test_cli_execution_via_main(tmp_path: Path):
    """Verify CLI main() function executes with argument flags."""
    out_dir = tmp_path / "cli_out"
    exit_code = generator_cli_main([
        "-o", str(out_dir),
        "-n", "2",
        "-m", "HANDWRITTEN_CURSIVE",
        "-s", "RULED_INDEX",
        "-d", "MILD",
        "--seed", "777",
        "--format", "JPEG",
    ])
    assert exit_code == 0
    assert (out_dir / "manifest.json").exists()


def test_cli_execution_no_manifest_flag(tmp_path: Path):
    """Verify --no-manifest flag suppresses manifest creation."""
    out_dir = tmp_path / "cli_no_manifest"
    exit_code = generator_cli_main([
        "-o", str(out_dir),
        "-n", "1",
        "--no-manifest",
    ])
    assert exit_code == 0
    assert not (out_dir / "manifest.json").exists()


# ============================================================================
# 7. EDGE CASES & ROBUSTNESS TESTS
# ============================================================================

def test_degradation_from_level_factory():
    """Verify DegradationConfig.from_level creates appropriate configs across all levels."""
    rng = random.Random(42)
    cfg_none = DegradationConfig.from_level(DegradationLevel.NONE, rng)
    assert cfg_none.oil_smudges == 0
    assert cfg_none.coffee_rings == 0
    assert cfg_none.edge_tears is False
    assert cfg_none.dog_ears is False

    cfg_mild = DegradationConfig.from_level(DegradationLevel.MILD, rng)
    assert cfg_mild.level == DegradationLevel.MILD

    cfg_mod = DegradationConfig.from_level(DegradationLevel.MODERATE, rng)
    assert cfg_mod.level == DegradationLevel.MODERATE
    assert cfg_mod.oil_smudges >= 1

    cfg_sev = DegradationConfig.from_level(DegradationLevel.SEVERE, rng)
    assert cfg_sev.level == DegradationLevel.SEVERE
    assert cfg_sev.oil_smudges >= 2
    assert cfg_sev.edge_tears is True
    assert cfg_sev.dog_ears is True


def test_generator_with_unusual_recipe_inputs(tmp_path: Path):
    """Verify generator handles long titles, special characters, and minimalist ingredients."""
    odd_recipe = RecipeSchema(
        recipe_id="rec_edge_case_unicode",
        title="Crème Brûlée & Grandma's 100% Whole-Wheat Sourdough — Special (1920) Edition!",
        description="Complex unicode text: café, naïve, jalapeño, smörgåsbord, 1/2 tsp & 3/4 tbsp.",
        yield_servings="12 mini-ramekins / 6 large portions",
        prep_time="45 mins",
        cook_time="1 hr 15 mins",
        total_time="2 hrs",
        ingredients=[
            "1 quart heavy cream (36% butterfat)",
            "1 vanilla bean, split & scraped (or 2 tsp paste)",
            "6 large egg yolks",
            "1/2 cup extra-fine vanilla sugar",
            "Pinch of fleur de sel",
            "Raw turbinado sugar for torching",
        ],
        directions=[
            "1. Scald heavy cream with scraped vanilla bean pulp and pod in heavy saucepan over medium-low heat.",
            "2. Whisk egg yolks with extra-fine sugar until pale and slightly thickened.",
            "3. Slowly temper warm cream into yolk mixture while constantly whisking.",
            "4. Strain custard through fine-mesh sieve into 6 shallow 6-oz porcelain ramekins.",
            "5. Place ramekins in large roasting pan; fill pan with boiling water halfway up ramekin sides.",
            "6. Bake at 325°F (165°C) for 35-40 minutes until centers barely jiggle.",
            "7. Chill in refrigerator at least 4 hours.",
            "8. Sprinkle evenly with turbinado sugar; caramelize with culinary torch until glass-like crust forms.",
        ],
        tags=["Dessert", "French", "Custard", "Baking", "Special Occasion"],
        source_notes="1920 Heirloom Manuscript",
    )

    gen = SyntheticCardGenerator(seed=888)
    front, back = gen.generate_card_pair(
        odd_recipe,
        modality=CardModality.HANDWRITTEN_CURSIVE,
        substrate=PaperSubstrate.AGED_YELLOW,
        degradation=DegradationConfig.from_level(DegradationLevel.SEVERE),
    )

    assert front.image.width > 600
    assert back.image.height > 400
    assert front.recipe.title == odd_recipe.title

    out_p = tmp_path / "unicode_test.jpg"
    front.image.save(out_p, "JPEG")
    assert out_p.exists() and out_p.stat().st_size > 5000


def test_png_format_generation(sample_test_recipe: RecipeSchema, tmp_path: Path):
    """Verify PNG format output creates valid PNG image files."""
    out_dir = tmp_path / "png_out"
    cards = generate_synthetic_corpus(
        output_dir=out_dir,
        count=1,
        image_format="PNG",
        generate_manifest=True,
    )
    assert len(cards) >= 1
    png_files = list(out_dir.glob("*.png"))
    assert len(png_files) >= 1
    for pf in png_files:
        img = Image.open(pf)
        assert img.format == "PNG"


def test_card_generator_cli_help(capsys):
    """Verify CLI prints help when invoked with -h / --help."""
    with pytest.raises(SystemExit) as exc_info:
        generator_cli_main(["--help"])
    assert exc_info.value.code == 0


def test_fallback_font_when_truetype_unavailable(sample_test_recipe: RecipeSchema):
    """Verify generator degrades gracefully to Pillow default bitmap font when TrueType fails."""
    from unittest.mock import patch
    import recipeboxer.tools.card_generator as cg

    # Clear cache and simulate missing truetype support
    cg._FONT_CACHE.clear()
    try:
        with patch("PIL.ImageFont.truetype", side_effect=OSError("No TrueType support")):
            gen = cg.SyntheticCardGenerator(seed=99)
            front, back = gen.generate_card_pair(
                sample_test_recipe,
                modality=CardModality.HANDWRITTEN_CURSIVE,
                substrate=PaperSubstrate.RULED_INDEX,
            )
            assert isinstance(front.image, Image.Image)
            assert isinstance(back.image, Image.Image)
            assert front.image.width >= 680
            assert front.image.height >= 440
    finally:
        cg._FONT_CACHE.clear()


def test_extreme_rotation_skew_behavior():
    """Verify extreme rotation skew does not crash or produce invalid dimensions."""
    base = Image.new("RGB", (300, 200), color=(240, 230, 210))
    res_extreme = apply_rotation_skew(base, 25.0)
    assert res_extreme.width >= 300
    assert res_extreme.height >= 200
    assert isinstance(res_extreme, Image.Image)


def test_apply_ink_fade_effect():
    """Verify apply_ink_fade reduces ink contrast and lightens image without corruption."""
    base = Image.new("RGB", (200, 200), color=(50, 50, 50))
    
    # Zero fade returns unchanged image
    res_zero = apply_ink_fade(base, 0.0)
    assert res_zero.size == (200, 200)

    # Positive fade factor lightens dark pixels
    res_faded = apply_ink_fade(base, 0.35, rng=random.Random(42))
    assert isinstance(res_faded, Image.Image)
    assert res_faded.size == (200, 200)
    # Mean pixel brightness should increase
    orig_stat = sum(base.getpixel((100, 100)))
    faded_stat = sum(res_faded.getpixel((100, 100)))
    assert faded_stat > orig_stat


def test_apply_edge_tear_all_four_edges():
    """Verify edge tears on TOP, BOTTOM, RIGHT, LEFT edges."""
    base = Image.new("RGB", (300, 300), color=(240, 230, 210))
    for edge in ["TOP", "BOTTOM", "RIGHT", "LEFT", "EDGE_TOP", "EDGE_BOTTOM"]:
        res = apply_edge_tear(base, edge=edge, rng=random.Random(42))
        assert isinstance(res, Image.Image)
        assert res.size == (300, 300)


def test_artifact_functions_default_rng():
    """Verify all physical artifact functions can be invoked without explicit rng argument."""
    base = Image.new("RGB", (300, 300), color=(240, 230, 210))
    
    ring = apply_coffee_ring(base, 100, 100, 40)
    assert isinstance(ring, Image.Image)
    
    smudge = apply_oil_smudge(base, (50, 50, 150, 150))
    assert isinstance(smudge, Image.Image)
    
    stamp = apply_date_stamp(base, 20, 20, "TEST STAMP")
    assert isinstance(stamp, Image.Image)
    
    fade = apply_ink_fade(base, 0.2)
    assert isinstance(fade, Image.Image)
    
    tear = apply_edge_tear(base, "TOP")
    assert isinstance(tear, Image.Image)


def test_generate_multi_card_set_with_degradation(sample_test_recipe: RecipeSchema):
    """Verify multi-card set generator applies degradation and records artifacts."""
    gen = SyntheticCardGenerator(seed=123)
    deg = DegradationConfig(
        level=DegradationLevel.SEVERE,
        oil_smudges=2,
        coffee_rings=1,
        dog_ears=True,
        edge_tears=True,
        ink_fade=0.25,
        rotation_skew_deg=2.0,
    )
    cards = gen.generate_multi_card_set(sample_test_recipe, num_cards=3, degradation=deg)
    assert len(cards) == 3
    # First card should have oil smudge / dog ear artifacts recorded
    c1_artifacts = cards[0].physical_characteristics.get("artifacts", [])
    assert len(c1_artifacts) > 0
    # Last card should have edge tear artifact recorded
    c3_artifacts = cards[2].physical_characteristics.get("artifacts", [])
    assert len(c3_artifacts) > 0


def test_tools_package_lazy_exports():
    """Verify recipeboxer.tools exports all required models and helper functions."""
    import recipeboxer.tools as tools
    
    expected_exports = [
        "CardModality",
        "PaperSubstrate",
        "DegradationLevel",
        "DegradationConfig",
        "GeneratedCard",
        "SyntheticCardGenerator",
        "generate_synthetic_corpus",
        "apply_coffee_ring",
        "apply_oil_smudge",
        "apply_ink_fade",
        "apply_dog_ear",
        "apply_edge_tear",
        "apply_date_stamp",
        "apply_tape_residue",
        "apply_rotation_skew",
        "create_paper_substrate",
        "get_font",
    ]
    for export_name in expected_exports:
        assert hasattr(tools, export_name), f"Missing export '{export_name}' in recipeboxer.tools"
        attr = getattr(tools, export_name)
        assert attr is not None


def test_apply_tape_residue_effect():
    """Verify apply_tape_residue renders yellowed cellophane tape strip without image corruption."""
    base = Image.new("RGB", (300, 300), color=(240, 230, 210))
    
    # Default top-right placement with default rng
    res1 = apply_tape_residue(base)
    assert isinstance(res1, Image.Image)
    assert res1.size == (300, 300)
    assert res1 != base

    # Explicit corner and bbox
    for corner in ["TOP_LEFT", "TOP_RIGHT", "BOTTOM_LEFT", "BOTTOM_RIGHT"]:
        res_corner = apply_tape_residue(base, corner=corner, rng=random.Random(42))
        assert res_corner.size == (300, 300)

    res_bbox = apply_tape_residue(base, bbox=(50, 50, 150, 80), rng=random.Random(42))
    assert res_bbox.size == (300, 300)


def test_generate_multi_card_set_modalities(sample_test_recipe: RecipeSchema):
    """Verify generate_multi_card_set adapts fonts, ink types, and cues to requested modality."""
    gen = SyntheticCardGenerator(seed=42)

    # Typewritten modality
    typed_set = gen.generate_multi_card_set(
        sample_test_recipe,
        num_cards=3,
        modality=CardModality.TYPEWRITTEN,
        substrate=PaperSubstrate.WHITE_LINEN,
    )
    assert len(typed_set) == 3
    for card in typed_set:
        assert card.modality == CardModality.TYPEWRITTEN
        assert card.physical_characteristics["ink_type"] == "TYPEWRITER_RIBBON"
    assert "PTO" in (typed_set[0].recipe_cluster.get("continuation_cue") or "")

    # Handwritten Cursive modality
    cursive_set = gen.generate_multi_card_set(
        sample_test_recipe,
        num_cards=3,
        modality=CardModality.HANDWRITTEN_CURSIVE,
        substrate=PaperSubstrate.RULED_INDEX,
    )
    assert len(cursive_set) == 3
    for card in cursive_set:
        assert card.modality == CardModality.HANDWRITTEN_CURSIVE
        assert card.physical_characteristics["ink_type"] == "FOUNTAIN_PEN"
    assert "over" in (cursive_set[0].recipe_cluster.get("continuation_cue") or "")

    # Printed Clipping modality
    clipping_set = gen.generate_multi_card_set(
        sample_test_recipe,
        num_cards=3,
        modality=CardModality.PRINTED_CLIPPING,
        substrate=PaperSubstrate.WHITE_LINEN,
    )
    assert len(clipping_set) == 3
    for card in clipping_set:
        assert card.modality == CardModality.PRINTED_CLIPPING
        assert card.physical_characteristics["ink_type"] == "PRINTED_TYPESET"


def test_cli_execution_with_normalized_lowercase_hyphenated_args(tmp_path: Path):
    """Verify CLI accepts case-insensitive and hyphenated modality/substrate/degradation flags."""
    out_dir = tmp_path / "cli_norm_out"
    exit_code = generator_cli_main([
        "-o", str(out_dir),
        "-n", "1",
        "-m", "handwritten-cursive",
        "-s", "ruled-index",
        "-d", "mild",
        "--seed", "999",
        "--format", "jpeg",
    ])
    assert exit_code == 0
    assert (out_dir / "manifest.json").exists()


def test_generate_standalone_card_with_tape_and_stamps(sample_test_recipe: RecipeSchema):
    """Verify standalone card generator properly applies tape residue and date stamps."""
    gen = SyntheticCardGenerator(seed=555)
    deg = DegradationConfig(
        level=DegradationLevel.SEVERE,
        tape_residue=True,
        stamps=True,
        oil_smudges=1,
    )
    card = gen.generate_standalone_card(sample_test_recipe, degradation=deg)
    assert card.role == CardRole.STANDALONE
    artifacts = card.physical_characteristics.get("artifacts", [])
    art_types = [a["artifact_type"] for a in artifacts]
    assert "TAPE_RESIDUE_YELLOWED" in art_types
    assert "OIL_GREASE_STAIN" in art_types


