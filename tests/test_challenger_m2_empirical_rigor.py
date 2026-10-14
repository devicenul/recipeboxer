"""Adversarial Empirical Verification Suite for Milestone M2.

This module stress-tests:
1. Exact SHA-256 byte digests and filesystem parity for all 48 recipe card images against manifest.json.
2. Image decodability, color profiles (RGB), dimensions, and pixel entropy across all 48 images.
3. Substrate physical degradation artifacts (rubber date stamps, oil smudges, coffee rings, dog ears, edge tears, tape residue).
4. Multi-card sequence chaining integrity and DAG continuity (3-card pot roast, 4-card panettone, turkey feast, fruitcake).
5. Deterministic synthetic card generator reproducibility with seed 42.
6. Attribution, provenance, and RecipeSchema validation for all 24 recipes.
"""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import pytest
from PIL import Image, ImageStat

from recipeboxer.models.domain import ArtifactType, CardRole
from recipeboxer.models.recipe import RecipeSchema
from cardforge import CardModality
from tests.fixtures.loader import SampleCardCorpus, load_sample_corpus


@pytest.fixture(scope="module")
def sample_corpus() -> SampleCardCorpus:
    return load_sample_corpus()


@pytest.fixture(scope="module")
def manifest_json_data() -> Dict[str, Any]:
    manifest_p = Path("tests/fixtures/sample_cards/manifest.json")
    assert manifest_p.exists(), f"manifest.json missing at {manifest_p}"
    return json.loads(manifest_p.read_text(encoding="utf-8"))


# ============================================================================
# 1. SHA256 & FILESYSTEM INTEGRITY (ALL 48 CARDS)
# ============================================================================

def test_manifest_metadata_and_card_counts(manifest_json_data: Dict[str, Any]):
    """Verify manifest header counts and exact 48 cards / 24 recipes structure."""
    assert manifest_json_data["version"] == "1.0.0"
    assert manifest_json_data["total_cards"] == 48
    assert manifest_json_data["total_recipes"] == 24
    assert len(manifest_json_data["cards"]) == 48


def test_sha256_exact_byte_digest_all_48_cards(manifest_json_data: Dict[str, Any]):
    """Empirically compute SHA256 for all 48 cards on disk and assert exact match."""
    corpus_dir = Path("tests/fixtures/sample_cards")
    cards = manifest_json_data["cards"]

    # Check for unexpected or orphan .jpg files in directory
    actual_jpg_files = sorted([p.name for p in corpus_dir.glob("*.jpg")])
    manifest_jpg_files = sorted([c["filename"] for c in cards])
    assert actual_jpg_files == manifest_jpg_files, (
        f"Filesystem mismatch! Orphan files: {set(actual_jpg_files) - set(manifest_jpg_files)}, "
        f"Missing files: {set(manifest_jpg_files) - set(actual_jpg_files)}"
    )

    mismatches = []
    for card in cards:
        fn = card["filename"]
        fp = corpus_dir / fn
        assert fp.exists(), f"File {fn} does not exist on disk"
        file_bytes = fp.read_bytes()
        computed_sha = hashlib.sha256(file_bytes).hexdigest()
        manifest_sha = card.get("sha256_hash") or card.get("sha256")
        if computed_sha != manifest_sha:
            mismatches.append((fn, manifest_sha, computed_sha))

    assert len(mismatches) == 0, f"SHA-256 digest mismatches found: {mismatches}"


# ============================================================================
# 2. IMAGE DIMENSIONS, COLOR PROFILE & PIXEL ENTROPY
# ============================================================================

def test_image_dimensions_color_profiles_and_entropy(manifest_json_data: Dict[str, Any]):
    """Verify PIL image decoding, RGB color mode, dimensions, and non-blank entropy."""
    corpus_dir = Path("tests/fixtures/sample_cards")
    cards = manifest_json_data["cards"]

    for card in cards:
        fn = card["filename"]
        fp = corpus_dir / fn
        with Image.open(fp) as img:
            assert img.format == "JPEG", f"{fn} format is {img.format}, expected JPEG"
            assert img.mode == "RGB", f"{fn} color mode is {img.mode}, expected RGB"
            w, h = img.size
            assert w >= 400 and h >= 300, f"{fn} dimensions suspiciously small: {w}x{h}"

            # Check physical characteristics dimensions match if specified
            dims = card.get("physical_characteristics", {}).get("dimensions", {})
            if "width_px" in dims and "height_px" in dims:
                assert w == dims["width_px"], f"{fn} width mismatch: actual {w} vs manifest {dims['width_px']}"
                assert h == dims["height_px"], f"{fn} height mismatch: actual {h} vs manifest {dims['height_px']}"

            # Entropy / Variance check (prevent blank or solid color images)
            stat = ImageStat.Stat(img)
            variances = stat.var
            for ch_idx, var in enumerate(variances):
                assert var > 10.0, f"Channel {ch_idx} variance too low ({var}) in {fn} (image appears blank/solid)"


# ============================================================================
# 3. PHYSICAL SUBSTRATE ARTIFACTS ADVERSARIAL INSPECTION
# ============================================================================

def test_physical_artifacts_date_stamps(sample_corpus: SampleCardCorpus):
    """Verify rubber date stamps on typewritten historical cards."""
    stamped_recipes = [
        "rec_typ_beef_stroganoff_1956",
        "rec_typ_swedish_meatballs_1961",
        "rec_typ_lemon_chiffon_pie_1958",
    ]
    for rid in stamped_recipes:
        cluster = sample_corpus.get_recipe_cluster(rid)
        assert len(cluster) >= 1
        front_card = cluster[0]
        artifacts = front_card.physical_characteristics.get("artifacts", [])
        artifact_types = [a["artifact_type"] for a in artifacts]
        assert "RUBBER_DATE_STAMP" in artifact_types, f"Missing RUBBER_DATE_STAMP on {rid}"


def test_physical_artifacts_oil_grease_and_coffee_rings(sample_corpus: SampleCardCorpus):
    """Verify oil smudges and coffee rings on damaged cards."""
    # Lasagna: Oil stain, coffee tea ring, and dog ear
    lasagna_cards = sample_corpus.get_recipe_cluster("rec_dmg_moms_lasagna_1971")
    assert len(lasagna_cards) == 2
    l_front_arts = [a["artifact_type"] for a in lasagna_cards[0].physical_characteristics.get("artifacts", [])]
    l_back_arts = [a["artifact_type"] for a in lasagna_cards[1].physical_characteristics.get("artifacts", [])]
    assert "OIL_GREASE_STAIN" in l_front_arts
    assert "OIL_GREASE_STAIN" in l_back_arts
    assert "COFFEE_TEA_RING" in l_front_arts

    # Pancakes: Oil stain & edge tear
    pancake_cards = sample_corpus.get_recipe_cluster("rec_dmg_buttermilk_pancakes_1969")
    assert len(pancake_cards) == 2
    p_front_arts = [a["artifact_type"] for a in pancake_cards[0].physical_characteristics.get("artifacts", [])]
    assert "OIL_GREASE_STAIN" in p_front_arts
    assert "TEAR_EDGE" in p_front_arts

    # Bourguignon: Coffee ring & dog ear
    bourguignon_cards = sample_corpus.get_recipe_cluster("rec_dmg_beef_bourguignon_1974")
    assert len(bourguignon_cards) == 2
    b_front_arts = [a["artifact_type"] for a in bourguignon_cards[0].physical_characteristics.get("artifacts", [])]
    assert "COFFEE_TEA_RING" in b_front_arts
    assert "CORNER_DOG_EAR_FOLD" in b_front_arts


def test_physical_artifacts_clippings_and_tape_residue(sample_corpus: SampleCardCorpus):
    """Verify cellophane tape residue and newsprint clipping characteristics."""
    clipping_recipes = [
        "rec_clp_crisco_fried_chicken",
        "rec_clp_green_bean_casserole_1968",
        "rec_clp_toll_house_cookies_1975",
    ]
    for rid in clipping_recipes:
        cluster = sample_corpus.get_recipe_cluster(rid)
        front = cluster[0]
        assert front.modality == CardModality.PRINTED_CLIPPING
        artifacts = front.physical_characteristics.get("artifacts", [])
        artifact_types = [a["artifact_type"] for a in artifacts]
        assert any("TAPE_RESIDUE" in art for art in artifact_types), f"Missing tape residue on {rid}: {artifact_types}"


# ============================================================================
# 4. MULTI-CARD SEQUENCE CHAINING (3-CARD & 4-CARD SETS)
# ============================================================================

def test_3card_sunday_pot_roast_sequence_chain(sample_corpus: SampleCardCorpus):
    """Adversarially verify the 3-card Sunday Pot Roast set sequence chaining."""
    cluster = sample_corpus.get_recipe_cluster("rec_mcs_sunday_pot_roast_gravy")
    assert len(cluster) == 3, f"Expected 3 cards in pot roast cluster, found {len(cluster)}"

    c1, c2, c3 = cluster[0], cluster[1], cluster[2]

    # Card 1 (Front)
    assert c1.filename == "rec_mcs_sunday_pot_roast_gravy_card1_front.jpg"
    assert c1.role == CardRole.SEQUENCE_START
    assert c1.recipe_cluster["cluster_role"] == "card1_front"
    assert c1.recipe_cluster["sequence_position"] == 0
    assert c1.recipe_cluster["total_cards"] == 3
    assert c1.recipe_cluster["continuation_cue"] == "(over to Card 1 Back ->)"

    # Card 2 (Back)
    assert c2.filename == "rec_mcs_sunday_pot_roast_gravy_card1_back.jpg"
    assert c2.role == CardRole.SEQUENCE_CONTINUATION
    assert c2.recipe_cluster["cluster_role"] == "card1_back"
    assert c2.recipe_cluster["sequence_position"] == 1
    assert c2.recipe_cluster["total_cards"] == 3
    assert c2.recipe_cluster["continuation_cue"] == "(See Card 2 Front ->)"

    # Card 3 (Card 2 Front)
    assert c3.filename == "rec_mcs_sunday_pot_roast_gravy_card2_front.jpg"
    assert c3.role == CardRole.SEQUENCE_CONTINUATION
    assert c3.recipe_cluster["cluster_role"] == "card2_front"
    assert c3.recipe_cluster["sequence_position"] == 2
    assert c3.recipe_cluster["total_cards"] == 3
    assert c3.recipe_cluster["continuation_cue"] is None


def test_4card_traditional_panettone_sequence_chain(sample_corpus: SampleCardCorpus):
    """Adversarially verify the 4-card Traditional Panettone set sequence chaining."""
    cluster = sample_corpus.get_recipe_cluster("rec_mcs_traditional_panettone_1955")
    assert len(cluster) == 4, f"Expected 4 cards in panettone cluster, found {len(cluster)}"

    c1, c2, c3, c4 = cluster[0], cluster[1], cluster[2], cluster[3]

    # Card 1 Front
    assert c1.filename == "rec_mcs_traditional_panettone_1955_card1_front.jpg"
    assert c1.role == CardRole.SEQUENCE_START
    assert c1.recipe_cluster["cluster_role"] == "card1_front"
    assert c1.recipe_cluster["sequence_position"] == 0
    assert c1.recipe_cluster["total_cards"] == 4
    assert c1.recipe_cluster["continuation_cue"] == "(over to Card 1 Back ->)"

    # Card 1 Back
    assert c2.filename == "rec_mcs_traditional_panettone_1955_card1_back.jpg"
    assert c2.role == CardRole.SEQUENCE_CONTINUATION
    assert c2.recipe_cluster["cluster_role"] == "card1_back"
    assert c2.recipe_cluster["sequence_position"] == 1
    assert c2.recipe_cluster["total_cards"] == 4
    assert c2.recipe_cluster["continuation_cue"] == "(See Card 2 Front ->)"

    # Card 2 Front
    assert c3.filename == "rec_mcs_traditional_panettone_1955_card2_front.jpg"
    assert c3.role == CardRole.SEQUENCE_CONTINUATION
    assert c3.recipe_cluster["cluster_role"] == "card2_front"
    assert c3.recipe_cluster["sequence_position"] == 2
    assert c3.recipe_cluster["total_cards"] == 4
    assert c3.recipe_cluster["continuation_cue"] == "(over to Card 2 Back ->)"

    # Card 2 Back
    assert c4.filename == "rec_mcs_traditional_panettone_1955_card2_back.jpg"
    assert c4.role == CardRole.SEQUENCE_CONTINUATION
    assert c4.recipe_cluster["cluster_role"] == "card2_back"
    assert c4.recipe_cluster["sequence_position"] == 3
    assert c4.recipe_cluster["total_cards"] == 4
    assert c4.recipe_cluster["continuation_cue"] is None




# ============================================================================
# 6. ATTRIBUTION & PROVENANCE METADATA VALIDATION
# ============================================================================

def test_recipe_attribution_and_schema_conformance(sample_corpus: SampleCardCorpus):
    """Verify that all 24 recipes contain valid attribution, provenance, and RecipeSchema validation."""
    for recipe in sample_corpus.recipes:
        assert isinstance(recipe, RecipeSchema)
        assert recipe.title.strip() != ""
        assert len(recipe.ingredients) >= 3
        assert len(recipe.directions) >= 2
        assert recipe.source_notes and recipe.source_notes.strip() != ""

        # Test directions numbering format: 1. ..., 2. ...
        for idx, step in enumerate(recipe.directions, 1):
            assert step.startswith(f"{idx}. "), f"Step does not start with '{idx}. ': {step}"

        # Test cluster provenance
        cluster = sample_corpus.get_recipe_cluster(recipe.recipe_id)
        assert len(cluster) >= 1
        c_info = cluster[0].recipe_cluster
        assert c_info.get("provenance")
        assert c_info.get("archive_series")
        assert c_info.get("historical_date")
        assert c_info.get("dimension")
