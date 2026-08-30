"""Integration tests for the curated sample recipe card fixture library and loader utilities.

Validates:
1. Dataset manifest schema conformance and ground-truth metadata completeness.
2. Physical image decodability and SHA-256 byte digest integrity across all sample scans.
3. Strict RecipeSchema validation for all reference ground-truth transcriptions.
4. Comprehensive modality coverage:
   - Vintage Handwritten Cursive (1940s-1970s index cards with aged paper, fountain pen bleed)
   - Typewritten & Stamped (manual typewriter ribbon wear, date stamps)
   - Magazine Clippings & Package Labels (glued newsprint, multi-column layout)
   - Stained / Damaged / Edge-Worn (oil spots, coffee rings, dog-ears, torn edges)
   - Multi-Card Sets (3-4 cards with continuous narrative flow)
   - Standalone Single-Sided Cards
5. Synchronized paired card properties (mirrored stains, matching substrate, narrative continuity).
6. Corpus query, filter, and image loading APIs.
7. Session-scoped pytest fixtures.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
from PIL import Image

from recipeboxer.models.domain import CardRole
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.tools.card_generator import CardModality
from tests.fixtures.loader import (
    CardEntry,
    SampleCardCorpus,
    get_manifest_path,
    get_sample_card_dir,
    load_sample_corpus,
)


# ============================================================================
# 1. DATASET INTEGRITY & MANIFEST SCHEMA VALIDATION
# ============================================================================

def test_manifest_file_exists_and_valid_json():
    """Verify that manifest.json exists in sample_cards directory and is valid JSON."""
    manifest_p = get_manifest_path()
    assert manifest_p.exists(), f"manifest.json missing at {manifest_p}"
    assert manifest_p.is_file(), f"manifest.json is not a file at {manifest_p}"

    sample_dir = get_sample_card_dir()
    assert sample_dir.exists() and sample_dir.is_dir()


def test_corpus_initialization_and_counts(sample_card_corpus: SampleCardCorpus):
    """Verify corpus loads all card scans and correctly registers distinct recipes."""
    assert len(sample_card_corpus) >= 20, f"Expected at least 20 sample cards, found {len(sample_card_corpus)}"
    assert len(sample_card_corpus.recipes) >= 10, f"Expected at least 10 unique recipes, found {len(sample_card_corpus.recipes)}"
    assert sample_card_corpus.manifest["version"] == "1.0.0"
    assert sample_card_corpus.manifest["total_cards"] == len(sample_card_corpus)


def test_all_card_images_exist_and_match_sha256(sample_card_corpus: SampleCardCorpus):
    """Verify every card in the corpus has an existing image file that matches its SHA-256 digest."""
    for card in sample_card_corpus:
        assert card.file_path.exists(), f"Image file missing for card {card.card_id}: {card.file_path}"
        assert card.file_path.stat().st_size > 1000, f"Image file is suspiciously small: {card.file_path}"

        actual_sha256 = card.compute_sha256()
        if card.sha256_hash:
            assert actual_sha256 == card.sha256_hash, (
                f"SHA-256 mismatch for {card.filename}: expected {card.sha256_hash}, got {actual_sha256}"
            )


def test_all_images_decode_cleanly_via_pillow(sample_card_corpus: SampleCardCorpus):
    """Verify every image decodes into a valid Pillow RGB image with expected dimensions."""
    for card in sample_card_corpus:
        img = card.load_image()
        assert isinstance(img, Image.Image)
        assert img.width >= 400, f"Width too small for {card.filename}: {img.width}"
        assert img.height >= 300, f"Height too small for {card.filename}: {img.height}"
        assert img.mode in ("RGB", "RGBA"), f"Unexpected color mode for {card.filename}: {img.mode}"


def test_corpus_validate_integrity_method(sample_card_corpus: SampleCardCorpus):
    """Verify the corpus.validate_integrity() method passes without errors."""
    report = sample_card_corpus.validate_integrity()
    assert report["is_valid"] is True, f"Corpus integrity validation failed: {report}"
    assert len(report["missing_files"]) == 0
    assert len(report["corrupted_files"]) == 0
    assert len(report["hash_mismatches"]) == 0
    assert len(report["invalid_recipes"]) == 0


# ============================================================================
# 2. RECIPE SCHEMA CONFORMANCE
# ============================================================================

def test_ground_truth_recipes_conform_to_recipe_schema(sample_card_corpus: SampleCardCorpus):
    """Verify every reference recipe conforms strictly to RecipeSchema validation."""
    for card in sample_card_corpus:
        rec = card.ground_truth_recipe
        assert isinstance(rec, RecipeSchema)
        assert rec.title.strip() != "", f"Empty title in recipe for card {card.card_id}"
        assert len(rec.ingredients) >= 3, f"Insufficient ingredients for {rec.title}"
        assert len(rec.directions) >= 2, f"Insufficient directions for {rec.title}"

        # Verify numbered directions normalization (1. ..., 2. ...)
        for idx, step in enumerate(rec.directions, 1):
            assert step.startswith(f"{idx}. "), f"Direction step {step} does not start with '{idx}. '"

        # Verify tags are non-empty and cleaned
        assert isinstance(rec.tags, list)
        for tag in rec.tags:
            assert tag.strip() != ""


# ============================================================================
# 3. MODALITY & HISTORICAL CLASSIFICATION COVERAGE
# ============================================================================

def test_handwritten_cursive_modality_present(sample_card_corpus: SampleCardCorpus):
    """Verify presence and attributes of Vintage Handwritten Cursive cards."""
    cursive_cards = sample_card_corpus.filter_by_modality(CardModality.HANDWRITTEN_CURSIVE)
    assert len(cursive_cards) >= 4, f"Expected >= 4 handwritten cursive cards, found {len(cursive_cards)}"

    # Check apple crisp and rhubarb pie cards
    apple_cards = [c for c in cursive_cards if "apple_crisp" in c.card_id]
    assert len(apple_cards) == 2
    assert {apple_cards[0].role, apple_cards[1].role} == {CardRole.FRONT_ONLY, CardRole.BACK_ONLY}


def test_typewritten_modality_present(sample_card_corpus: SampleCardCorpus):
    """Verify presence and attributes of Typewritten & Stamped cards."""
    typed_cards = sample_card_corpus.filter_by_modality(CardModality.TYPEWRITTEN)
    assert len(typed_cards) >= 4, f"Expected >= 4 typewritten cards, found {len(typed_cards)}"

    # Check beef stroganoff with stamp
    stroganoff_cards = [c for c in typed_cards if "beef_stroganoff" in c.card_id]
    assert len(stroganoff_cards) == 2
    assert any("stamp" in str(c.physical_characteristics).lower() or "typ" in c.card_id for c in stroganoff_cards)


def test_printed_clipping_modality_present(sample_card_corpus: SampleCardCorpus):
    """Verify presence and attributes of Magazine Clippings & Package Labels."""
    clipping_cards = sample_card_corpus.filter_by_modality(CardModality.PRINTED_CLIPPING)
    assert len(clipping_cards) >= 3, f"Expected >= 3 clipping cards, found {len(clipping_cards)}"

    # Check Crisco fried chicken and condensed milk pie
    chicken_cards = [c for c in clipping_cards if "crisco_fried_chicken" in c.card_id]
    assert len(chicken_cards) == 2

    condensed_milk_cards = [c for c in clipping_cards if "condensed_milk" in c.card_id]
    assert len(condensed_milk_cards) == 1
    assert condensed_milk_cards[0].role == CardRole.STANDALONE


def test_stained_damaged_modality_present(sample_card_corpus: SampleCardCorpus):
    """Verify presence and attributes of Stained / Damaged / Edge-Worn cards."""
    damaged_cards = [c for c in sample_card_corpus if "dmg" in c.card_id or "lasagna" in c.card_id or "shortbread" in c.card_id]
    assert len(damaged_cards) >= 3, f"Expected >= 3 damaged/stained cards, found {len(damaged_cards)}"

    # Check lasagna front/back pair with oil stain and coffee ring
    lasagna_cards = [c for c in damaged_cards if "lasagna" in c.card_id]
    assert len(lasagna_cards) == 2
    front_card = [c for c in lasagna_cards if c.role == CardRole.FRONT_ONLY][0]
    back_card = [c for c in lasagna_cards if c.role == CardRole.BACK_ONLY][0]

    front_art_types = [a["artifact_type"] for a in front_card.physical_characteristics.get("artifacts", [])]
    back_art_types = [a["artifact_type"] for a in back_card.physical_characteristics.get("artifacts", [])]

    assert "OIL_GREASE_STAIN" in front_art_types
    assert "OIL_GREASE_STAIN" in back_art_types


def test_multi_card_sets_present_and_continuous(sample_card_corpus: SampleCardCorpus):
    """Verify presence and narrative continuity of Multi-Card Sets (3-4 cards)."""
    multi_sets = sample_card_corpus.get_multi_card_sets()
    assert len(multi_sets) >= 2, f"Expected >= 2 multi-card sets, found {len(multi_sets)}"

    # Check 3-card Turkey Feast set
    turkey_cards = multi_sets.get("rec_mcs_turkey_stuffing_feast")
    assert turkey_cards is not None, "Turkey Feast 3-card set missing"
    assert len(turkey_cards) == 3
    assert turkey_cards[0].recipe_cluster["cluster_role"] == "card1_front"
    assert turkey_cards[1].recipe_cluster["cluster_role"] == "card1_back"
    assert turkey_cards[2].recipe_cluster["cluster_role"] == "card2_front"

    # Continuation cues
    assert turkey_cards[0].recipe_cluster["continuation_cue"] is not None
    assert turkey_cards[1].recipe_cluster["continuation_cue"] is not None

    # Check 4-card Fruitcake set
    fruitcake_cards = multi_sets.get("rec_mcs_wedding_fruitcake_1942")
    assert fruitcake_cards is not None, "Wedding Fruitcake 4-card set missing"
    assert len(fruitcake_cards) == 4
    roles = [c.recipe_cluster["cluster_role"] for c in fruitcake_cards]
    assert roles == ["card1_front", "card1_back", "card2_front", "card2_back"]


def test_standalone_cards_present(sample_card_corpus: SampleCardCorpus):
    """Verify presence and attributes of Standalone Single-Sided cards."""
    standalones = sample_card_corpus.get_standalones()
    assert len(standalones) >= 3, f"Expected >= 3 standalone cards, found {len(standalones)}"
    for s in standalones:
        assert s.role == CardRole.STANDALONE
        assert s.recipe_cluster["total_cards"] == 1
        assert s.recipe_cluster["continuation_cue"] is None


# ============================================================================
# 4. PAIRED CARDS COORDINATION & CONTINUITY
# ============================================================================

def test_paired_cards_have_matching_physical_attributes(sample_card_corpus: SampleCardCorpus):
    """Verify front/back pairs share matching substrate, paper tone, and recipe ID."""
    pairs = sample_card_corpus.get_pairs()
    assert len(pairs) >= 5, f"Expected >= 5 front/back pairs, found {len(pairs)}"

    for front, back in pairs:
        assert front.role == CardRole.FRONT_ONLY
        assert back.role == CardRole.BACK_ONLY

        # Same recipe cluster
        assert front.recipe_cluster["recipe_id"] == back.recipe_cluster["recipe_id"]
        assert front.ground_truth_recipe.recipe_id == back.ground_truth_recipe.recipe_id

        # Matching substrate tone
        f_tone = front.physical_characteristics.get("paper_tone")
        b_tone = back.physical_characteristics.get("paper_tone")
        assert f_tone == b_tone, f"Substrate tone mismatch between pair: {f_tone} vs {b_tone}"

        # Front continuation cue present
        assert front.recipe_cluster.get("continuation_cue") is not None


# ============================================================================
# 5. QUERYING, FILTERING & PYTEST FIXTURES
# ============================================================================

def test_get_by_id_and_image_loading(sample_card_corpus: SampleCardCorpus):
    """Verify querying cards by ID and loading corresponding Pillow images."""
    first_card = sample_card_corpus[0]
    retrieved = sample_card_corpus.get_by_id(first_card.card_id)
    assert retrieved is not None
    assert retrieved.card_id == first_card.card_id

    # Test loading image by card entry and by ID string
    img1 = sample_card_corpus.load_image(first_card)
    img2 = sample_card_corpus.load_image(first_card.card_id)
    assert img1.size == img2.size

    # Non-existent card ID returns None / raises KeyError on image load
    assert sample_card_corpus.get_by_id("non_existent_card.jpg") is None
    with pytest.raises(KeyError):
        sample_card_corpus.load_image("non_existent_card.jpg")


def test_filter_by_role_and_recipe_cluster(sample_card_corpus: SampleCardCorpus):
    """Verify filtering cards by role and recipe cluster query."""
    fronts = sample_card_corpus.filter_by_role(CardRole.FRONT_ONLY)
    backs = sample_card_corpus.filter_by_role(CardRole.BACK_ONLY)
    standalones = sample_card_corpus.filter_by_role(CardRole.STANDALONE)

    assert len(fronts) + len(backs) + len(standalones) == len(sample_card_corpus)

    # Test get_recipe_cluster
    rec_id = "rec_vhc_apple_crisp_1948"
    cluster = sample_card_corpus.get_recipe_cluster(rec_id)
    assert len(cluster) == 2
    assert cluster[0].recipe_cluster["sequence_position"] == 0
    assert cluster[1].recipe_cluster["sequence_position"] == 1


def test_pytest_fixtures_injection(
    sample_card_corpus: SampleCardCorpus,
    ground_truth_manifest: Dict[str, Any],
    sample_card_pairs: List[Tuple[CardEntry, CardEntry]],
    sample_card_standalones: List[CardEntry],
    sample_card_multi_sets: Dict[str, List[CardEntry]],
):
    """Verify that all session-scoped pytest fixtures inject properly and are fully populated."""
    assert isinstance(sample_card_corpus, SampleCardCorpus)
    assert isinstance(ground_truth_manifest, dict)
    assert "cards" in ground_truth_manifest
    assert len(sample_card_pairs) >= 5
    assert len(sample_card_standalones) >= 3
    assert len(sample_card_multi_sets) >= 2


# ============================================================================
# 6. EDGE CASES & ERROR PATH TESTS
# ============================================================================

def test_corpus_non_existent_manifest_raises(tmp_path: Path):
    """Verify loading a corpus from an empty directory without manifest raises FileNotFoundError."""
    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()
    with pytest.raises(FileNotFoundError):
        load_sample_corpus(directory=empty_dir)


def test_card_entry_missing_file_raises_on_load(tmp_path: Path):
    """Verify CardEntry raises FileNotFoundError when target image file is missing on disk."""
    fake_entry = CardEntry(
        card_id="fake_card.jpg",
        filename="fake_card.jpg",
        file_path=tmp_path / "does_not_exist.jpg",
        sha256_hash=None,
        modality=CardModality.HANDWRITTEN_CURSIVE,
        role=CardRole.STANDALONE,
        recipe_cluster={},
        physical_characteristics={},
        ground_truth_recipe=RecipeSchema(title="Fake Title", ingredients=["1 cup milk"], directions=["1. Drink"]),
    )
    with pytest.raises(FileNotFoundError):
        fake_entry.load_image()
    with pytest.raises(FileNotFoundError):
        fake_entry.compute_sha256()


def test_corpus_integrity_check_detects_corrupted_dataset(tmp_path: Path):
    """Verify validate_integrity accurately flags missing files and hash mismatches."""
    corrupted_dir = tmp_path / "corrupted_corpus"
    corrupted_dir.mkdir()

    # Create dummy image and write valid manifest
    dummy_img_path = corrupted_dir / "valid.jpg"
    img = Image.new("RGB", (100, 100), color=(255, 255, 255))
    img.save(dummy_img_path)

    manifest_data = {
        "version": "1.0.0",
        "cards": [
            {
                "card_id": "valid.jpg",
                "filename": "valid.jpg",
                "sha256_hash": "incorrect_hash_value",  # Intentional hash mismatch
                "modality": "HANDWRITTEN_CURSIVE",
                "role": "STANDALONE",
                "recipe_cluster": {"recipe_id": "rec_dummy"},
                "physical_characteristics": {},
                "ground_truth_recipe": {
                    "recipe_id": "rec_dummy",
                    "title": "Dummy Recipe",
                    "ingredients": ["1 cup water"],
                    "directions": ["1. Boil water."],
                },
            },
            {
                "card_id": "missing.jpg",
                "filename": "missing.jpg",  # Missing file on disk
                "modality": "TYPEWRITTEN",
                "role": "FRONT_ONLY",
                "recipe_cluster": {"recipe_id": "rec_missing"},
                "physical_characteristics": {},
                "ground_truth_recipe": {
                    "recipe_id": "rec_missing",
                    "title": "Missing Card Recipe",
                    "ingredients": ["1 apple"],
                    "directions": ["1. Eat apple."],
                },
            },
        ],
    }

    manifest_file = corrupted_dir / "manifest.json"
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

    bad_corpus = SampleCardCorpus(corpus_dir=corrupted_dir)
    report = bad_corpus.validate_integrity()

    assert report["is_valid"] is False
    assert "missing.jpg" in report["missing_files"]
    assert "valid.jpg" in report["hash_mismatches"]


def test_filter_by_unknown_modality_returns_empty(sample_card_corpus: SampleCardCorpus):
    """Verify filtering with a non-existent modality string returns an empty list."""
    results = sample_card_corpus.filter_by_modality("NON_EXISTENT_MODALITY")
    assert len(results) == 0


def test_case_insensitive_filtering(sample_card_corpus: SampleCardCorpus):
    """Verify filter_by_modality and filter_by_role work with lowercase and mixed case."""
    lower_cursive = sample_card_corpus.filter_by_modality("handwritten_cursive")
    upper_cursive = sample_card_corpus.filter_by_modality(CardModality.HANDWRITTEN_CURSIVE)
    assert len(lower_cursive) == len(upper_cursive)
    assert len(lower_cursive) >= 4

    lower_front = sample_card_corpus.filter_by_role("front_only")
    upper_front = sample_card_corpus.filter_by_role(CardRole.FRONT_ONLY)
    assert len(lower_front) == len(upper_front)


def test_get_by_id_with_stem(sample_card_corpus: SampleCardCorpus):
    """Verify get_by_id resolves both full filename and filename stem."""
    first = sample_card_corpus[0]
    stem = Path(first.filename).stem

    entry_by_filename = sample_card_corpus.get_by_id(first.filename)
    entry_by_stem = sample_card_corpus.get_by_id(stem)

    assert entry_by_filename is not None
    assert entry_by_stem is not None
    assert entry_by_filename.card_id == entry_by_stem.card_id


def test_uppercase_and_delimiter_insensitivity_queries(sample_card_corpus: SampleCardCorpus):
    """Verify filtering and get_by_id tolerate hyphens, spaces, and uppercase strings."""
    # Modality with hyphens and spaces
    hyphen_cursive = sample_card_corpus.filter_by_modality("handwritten-cursive")
    space_cursive = sample_card_corpus.filter_by_modality("handwritten cursive")
    standard_cursive = sample_card_corpus.filter_by_modality("HANDWRITTEN_CURSIVE")
    assert len(hyphen_cursive) == len(standard_cursive)
    assert len(space_cursive) == len(standard_cursive)

    # Role with hyphens and spaces
    hyphen_front = sample_card_corpus.filter_by_role("front-only")
    space_front = sample_card_corpus.filter_by_role("front only")
    standard_front = sample_card_corpus.filter_by_role("FRONT_ONLY")
    assert len(hyphen_front) == len(standard_front)
    assert len(space_front) == len(standard_front)

    # Upper case get_by_id lookup
    first = sample_card_corpus[0]
    upper_entry = sample_card_corpus.get_by_id(first.card_id.upper())
    assert upper_entry is not None
    assert upper_entry.card_id == first.card_id


def test_get_recipe_cluster_case_insensitive(sample_card_corpus: SampleCardCorpus):
    """Verify get_recipe_cluster works with uppercase and mixed case recipe IDs."""
    rec_id = "rec_vhc_apple_crisp_1948"
    standard_cluster = sample_card_corpus.get_recipe_cluster(rec_id)
    upper_cluster = sample_card_corpus.get_recipe_cluster(rec_id.upper())
    assert len(standard_cluster) == 2
    assert len(upper_cluster) == 2
    assert standard_cluster[0].card_id == upper_cluster[0].card_id


def test_card_entry_load_image_context_cleanup(sample_card_corpus: SampleCardCorpus):
    """Verify CardEntry.load_image returns a detached image copy and closes underlying descriptor."""
    card = sample_card_corpus[0]
    img = card.load_image()
    assert isinstance(img, Image.Image)
    assert img.size == (680, 440) or (img.width >= 400 and img.height >= 300)


def test_get_by_id_hyphenated_and_spaced_queries(sample_card_corpus: SampleCardCorpus):
    """Verify get_by_id resolves IDs with hyphens, spaces, and stem variations."""
    # Lookup using hyphenated filename
    hyphen_entry = sample_card_corpus.get_by_id("rec-vhc-apple-crisp-1948-front.jpg")
    assert hyphen_entry is not None
    assert hyphen_entry.card_id == "rec_vhc_apple_crisp_1948_front.jpg"

    # Lookup using hyphenated stem
    hyphen_stem_entry = sample_card_corpus.get_by_id("rec-vhc-apple-crisp-1948-front")
    assert hyphen_stem_entry is not None
    assert hyphen_stem_entry.card_id == "rec_vhc_apple_crisp_1948_front.jpg"


def test_get_recipe_cluster_hyphenated_queries(sample_card_corpus: SampleCardCorpus):
    """Verify get_recipe_cluster resolves cluster with hyphenated and spaced recipe IDs."""
    hyphen_cluster = sample_card_corpus.get_recipe_cluster("rec-vhc-apple-crisp-1948")
    assert len(hyphen_cluster) == 2
    assert hyphen_cluster[0].card_id == "rec_vhc_apple_crisp_1948_front.jpg"

    space_cluster = sample_card_corpus.get_recipe_cluster("rec vhc apple crisp 1948")
    assert len(space_cluster) == 2
    assert space_cluster[0].card_id == "rec_vhc_apple_crisp_1948_front.jpg"


def test_corpus_integrity_catches_invalid_recipe_schema(tmp_path: Path):
    """Verify validate_integrity detects invalid recipe fields and populates invalid_recipes."""
    bad_dir = tmp_path / "bad_schema_corpus"
    bad_dir.mkdir()

    img_path = bad_dir / "test_card.jpg"
    img = Image.new("RGB", (100, 100), color=(255, 255, 255))
    img.save(img_path)
    sha256 = hashlib.sha256(img_path.read_bytes()).hexdigest()

    manifest_data = {
        "version": "1.0.0",
        "cards": [
            {
                "card_id": "test_card.jpg",
                "filename": "test_card.jpg",
                "sha256_hash": sha256,
                "modality": "HANDWRITTEN_CURSIVE",
                "role": "STANDALONE",
                "recipe_cluster": {"recipe_id": "rec_bad_schema"},
                "physical_characteristics": {},
                "ground_truth_recipe": {
                    "recipe_id": "rec_bad_schema",
                    # Missing required 'title', 'ingredients', 'directions'
                },
            }
        ],
    }

    (bad_dir / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")
    corpus = SampleCardCorpus(corpus_dir=bad_dir)
    report = corpus.validate_integrity()

    assert report["is_valid"] is False
    assert len(report["invalid_recipes"]) == 1
    assert "rec_bad_schema" in report["invalid_recipes"][0]


# ============================================================================
# 7. EXPANDED CORPUS & HISTORICAL DIMENSIONS (24 RECIPES / 48 CARDS)
# ============================================================================

def test_expanded_corpus_exact_counts_and_all_six_historical_dimensions(sample_card_corpus: SampleCardCorpus):
    """Verify that the corpus contains exactly 48 cards across 24 recipes spanning all 6 historical dimensions."""
    assert len(sample_card_corpus) == 48, f"Expected exactly 48 cards, found {len(sample_card_corpus)}"
    assert len(sample_card_corpus.recipes) == 24, f"Expected exactly 24 unique recipes, found {len(sample_card_corpus.recipes)}"

    expected_recipes = {
        # Dim 1: Vintage Handwritten Cursive (1940s–1970s)
        "rec_vhc_apple_crisp_1948",
        "rec_vhc_rhubarb_pie_1965",
        "rec_vhc_strawberry_rhubarb_jam_1954",
        "rec_vhc_amish_cinnamon_bread_1973",
        # Dim 2: Typewritten & Stamped (1950s–1970s)
        "rec_typ_beef_stroganoff_1956",
        "rec_typ_tomato_soup_cake_1952",
        "rec_typ_swedish_meatballs_1961",
        "rec_typ_lemon_chiffon_pie_1958",
        # Dim 3: Magazine Clippings & Package Labels (1960s–1980s)
        "rec_clp_crisco_fried_chicken",
        "rec_clp_condensed_milk_pie_1962",
        "rec_clp_green_bean_casserole_1968",
        "rec_clp_toll_house_cookies_1975",
        # Dim 4: Stained / Damaged / Edge-Worn Substrates
        "rec_dmg_moms_lasagna_1971",
        "rec_dmg_butter_shortbread",
        "rec_dmg_buttermilk_pancakes_1969",
        "rec_dmg_beef_bourguignon_1974",
        # Dim 5: Multi-Card Narrative Sets
        "rec_mcs_turkey_stuffing_feast",
        "rec_mcs_wedding_fruitcake_1942",
        "rec_mcs_sunday_pot_roast_gravy",
        "rec_mcs_traditional_panettone_1955",
        # Dim 6: Standalone Single-Sided Cards
        "rec_sta_quick_chocolate_fudge",
        "rec_sta_southern_pecan_pie",
        "rec_sta_classic_banana_bread",
        "rec_sta_old_fashioned_lemonade",
    }

    found_recipes = {r.recipe_id for r in sample_card_corpus.recipes}
    assert found_recipes == expected_recipes, f"Recipe set mismatch: missing {expected_recipes - found_recipes}, unexpected {found_recipes - expected_recipes}"


def test_all_twelve_new_recipes_present_with_valid_metadata(sample_card_corpus: SampleCardCorpus):
    """Verify each of the 12 new recipes is present, complete, and contains authentic attribution metadata."""
    new_recipe_ids = [
        "rec_vhc_strawberry_rhubarb_jam_1954",
        "rec_vhc_amish_cinnamon_bread_1973",
        "rec_typ_swedish_meatballs_1961",
        "rec_typ_lemon_chiffon_pie_1958",
        "rec_clp_green_bean_casserole_1968",
        "rec_clp_toll_house_cookies_1975",
        "rec_dmg_buttermilk_pancakes_1969",
        "rec_dmg_beef_bourguignon_1974",
        "rec_mcs_sunday_pot_roast_gravy",
        "rec_mcs_traditional_panettone_1955",
        "rec_sta_classic_banana_bread",
        "rec_sta_old_fashioned_lemonade",
    ]

    for rid in new_recipe_ids:
        cluster = sample_card_corpus.get_recipe_cluster(rid)
        assert len(cluster) >= 1, f"No cards found for new recipe: {rid}"
        recipe = cluster[0].ground_truth_recipe
        assert recipe.title, f"Missing title for {rid}"
        assert len(recipe.ingredients) >= 3, f"Too few ingredients for {rid}"
        assert len(recipe.directions) >= 2, f"Too few directions for {rid}"
        assert recipe.source_notes, f"Missing source_notes attribution for {rid}"

        # Verify cluster attribution metadata
        cluster_info = cluster[0].recipe_cluster
        assert cluster_info.get("provenance"), f"Missing provenance for {rid}"
        assert cluster_info.get("archive_series"), f"Missing archive_series for {rid}"
        assert cluster_info.get("historical_date"), f"Missing historical_date for {rid}"
        assert cluster_info.get("dimension"), f"Missing dimension for {rid}"


def test_multi_card_expanded_sequences_continuity(sample_card_corpus: SampleCardCorpus):
    """Verify the 3-card pot roast and 4-card panettone sets have continuous sequences and valid roles."""
    # 3-card pot roast
    pot_roast_cards = sample_card_corpus.get_recipe_cluster("rec_mcs_sunday_pot_roast_gravy")
    assert len(pot_roast_cards) == 3
    assert pot_roast_cards[0].recipe_cluster["cluster_role"] == "card1_front"
    assert pot_roast_cards[1].recipe_cluster["cluster_role"] == "card1_back"
    assert pot_roast_cards[2].recipe_cluster["cluster_role"] == "card2_front"
    assert pot_roast_cards[0].role == CardRole.SEQUENCE_START
    assert pot_roast_cards[1].role == CardRole.SEQUENCE_CONTINUATION
    assert pot_roast_cards[2].role == CardRole.SEQUENCE_CONTINUATION

    # 4-card panettone
    panettone_cards = sample_card_corpus.get_recipe_cluster("rec_mcs_traditional_panettone_1955")
    assert len(panettone_cards) == 4
    roles = [c.recipe_cluster["cluster_role"] for c in panettone_cards]
    assert roles == ["card1_front", "card1_back", "card2_front", "card2_back"]
    assert panettone_cards[0].role == CardRole.SEQUENCE_START
    assert all(c.role == CardRole.SEQUENCE_CONTINUATION for c in panettone_cards[1:])


def test_historical_clipping_specimens_provenance_and_traits(sample_card_corpus: SampleCardCorpus):
    """Verify authentic historical clipping specimens (magazine, packaging box, can label) have complete metadata, dimensions, and traits."""
    clipping_cards = sample_card_corpus.filter_by_modality(CardModality.PRINTED_CLIPPING)
    assert len(clipping_cards) == 7, f"Expected 7 clipping cards, found {len(clipping_cards)}"

    # 1. Specimen coverage checks:
    # a. Vintage Magazine Advertisements/Clippings
    magazine_ad_recipes = ["rec_clp_crisco_fried_chicken", "rec_clp_green_bean_casserole_1968"]
    for rid in magazine_ad_recipes:
        cluster = sample_card_corpus.get_recipe_cluster(rid)
        assert len(cluster) == 2, f"Expected 2-card front/back pair for {rid}"
        assert cluster[0].role == CardRole.SEQUENCE_START
        assert cluster[1].role == CardRole.SEQUENCE_CONTINUATION

    # b. Food Product Box Packaging
    box_recipe_id = "rec_clp_toll_house_cookies_1975"
    box_cluster = sample_card_corpus.get_recipe_cluster(box_recipe_id)
    assert len(box_cluster) == 2
    assert box_cluster[0].role == CardRole.SEQUENCE_START
    assert box_cluster[1].role == CardRole.SEQUENCE_CONTINUATION

    # c. Canned Goods Labels
    can_label_id = "rec_clp_condensed_milk_pie_1962"
    can_cluster = sample_card_corpus.get_recipe_cluster(can_label_id)
    assert len(can_cluster) == 1
    assert can_cluster[0].role == CardRole.STANDALONE

    # 2. Detailed physical dimensions and traits validation across all clipping cards
    for card in clipping_cards:
        phys = card.physical_characteristics
        assert "dimensions" in phys, f"Dimensions missing for {card.filename}"
        dims = phys["dimensions"]
        assert dims.get("width_mm") is not None and dims["width_mm"] > 0
        assert dims.get("height_mm") is not None and dims["height_mm"] > 0
        assert dims.get("aspect_ratio") is not None and dims["aspect_ratio"] > 0
        assert dims.get("width_px") is not None and dims["width_px"] >= 400
        assert dims.get("height_px") is not None and dims["height_px"] >= 300

        # Physical traits
        assert phys.get("paper_type"), f"Missing paper_type for {card.filename}"
        assert phys.get("typography") or phys.get("font"), f"Missing typography/font for {card.filename}"
        assert phys.get("condition"), f"Missing condition for {card.filename}"
        assert phys.get("stains_creases") or len(phys.get("artifacts", [])) > 0, f"Missing stains/creases for {card.filename}"

        # Ground truth recipe definitions
        rec = card.ground_truth_recipe
        assert rec.title and rec.title.strip() != "", f"Empty title in recipe for {card.filename}"
        assert len(rec.ingredients) >= 3, f"Too few ingredients for {card.filename}"
        assert len(rec.directions) >= 2, f"Too few directions for {card.filename}"
        assert rec.yield_servings and rec.yield_servings.strip() != "", f"Missing yield for {card.filename}"
        assert rec.prep_time and rec.prep_time.strip() != "", f"Missing prep_time for {card.filename}"
        assert rec.cook_time is not None, f"Missing cook_time for {card.filename}"
        assert rec.total_time and rec.total_time.strip() != "", f"Missing total_time for {card.filename}"





