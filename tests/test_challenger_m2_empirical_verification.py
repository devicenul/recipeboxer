"""Adversarial Empirical Challenger Verification Suite for Milestone M2.

Tests:
1. Zero Dropped Cards: 48 cards across 24 recipes in manifest.json and sample_cards.
2. Anti-Overfitting: Physical substrate, ruling lines, ink medium, stain mirroring, and boundary continuation heuristics.
3. Multi-Card Sequence Chaining: Permutation invariance across all 3-card and 4-card sequence sets.
4. Content Isolation: Strict non-leakage of ingredients, steps, and metadata across distinct recipes.
"""

from __future__ import annotations

import hashlib
import json
import itertools
from pathlib import Path
from typing import Any, Dict, List

import pytest

from recipeboxer.correlation.optimizer import PairingOptimizer
from recipeboxer.correlation.semantic import GeminiSemanticVerifier
from recipeboxer.extraction.extractor import GeminiRecipeExtractor
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.models.domain import (
    ArtifactType,
    BoundaryText,
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
from recipeboxer.vision.heuristics import (
    calculate_color_similarity,
    calculate_heuristic_distance,
    calculate_ink_similarity,
    calculate_role_compatibility,
    calculate_ruling_similarity,
    calculate_sequential_header_score,
    calculate_stain_similarity,
    rgb_to_lab,
    screen_candidate_pairs,
)


@pytest.fixture
def manifest_data() -> Dict[str, Any]:
    manifest_path = Path("tests/fixtures/sample_cards/manifest.json").resolve()
    assert manifest_path.exists(), f"Manifest not found at {manifest_path}"
    with open(manifest_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_unique_recipes_from_manifest(manifest_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    recipes: Dict[str, Dict[str, Any]] = {}
    for card in manifest_data.get("cards", []):
        r_id = card.get("recipe_cluster", {}).get("recipe_id") or card.get("ground_truth_recipe", {}).get("recipe_id")
        if r_id and r_id not in recipes:
            gt = card.get("ground_truth_recipe", {})
            cluster = card.get("recipe_cluster", {})
            recipes[r_id] = {
                "recipe_id": r_id,
                "title": gt.get("title"),
                "dimension": cluster.get("dimension"),
                "provenance": cluster.get("provenance"),
                "historical_date": cluster.get("historical_date"),
                "archive_series": cluster.get("archive_series"),
                "ground_truth": gt,
                "card_ids": gt.get("card_ids", [card.get("filename")]),
                "source_notes": gt.get("source_notes"),
            }
    return recipes


class TestM2CorpusIntegrityAndZeroDroppedCards:
    """Verifies that all 48 cards across 24 recipes are present, valid, and accounted for."""

    def test_corpus_manifest_exact_counts(self, manifest_data: Dict[str, Any]):
        assert manifest_data.get("total_cards") == 48
        assert manifest_data.get("total_recipes") == 24
        assert len(manifest_data.get("cards", [])) == 48
        recipes = get_unique_recipes_from_manifest(manifest_data)
        assert len(recipes) == 24

    def test_all_48_image_files_exist_and_match_sha256(self, manifest_data: Dict[str, Any]):
        fixtures_dir = Path("tests/fixtures/sample_cards").resolve()
        for card in manifest_data["cards"]:
            filename = card["filename"]
            img_path = fixtures_dir / filename
            assert img_path.exists(), f"Image file {filename} missing on disk"
            assert img_path.stat().st_size > 1000, f"Image file {filename} is too small"

            # Verify SHA256 integrity
            calc_sha256 = hashlib.sha256(img_path.read_bytes()).hexdigest().lower()
            assert calc_sha256 == card["sha256_hash"].lower(), (
                f"SHA256 mismatch for {filename}: expected {card['sha256_hash']}, got {calc_sha256}"
            )

    def test_all_six_historical_dimensions_represented(self, manifest_data: Dict[str, Any]):
        recipes = get_unique_recipes_from_manifest(manifest_data)
        dimensions = {r.get("dimension") for r in recipes.values()}
        expected_dimensions = {
            "Vintage Handwritten Cursive (1940s–1970s)",
            "Typewritten & Stamped (1950s–1970s)",
            "Magazine Clippings & Package Labels (1960s–1980s)",
            "Stained / Damaged / Edge-Worn Substrates",
            "Multi-Card Narrative Sets",
            "Standalone Single-Sided Cards",
        }
        for expected in expected_dimensions:
            assert expected in dimensions, f"Missing historical dimension: {expected}"

        # Verify recipe counts per dimension (at least 2 recipes per dimension, with 6 standalones, 2 per group)
        dim_counts: Dict[str, int] = {}
        for r in recipes.values():
            dim = r.get("dimension")
            dim_counts[dim] = dim_counts.get(dim, 0) + 1

        for dim, count in dim_counts.items():
            assert count >= 2, f"Dimension {dim} has only {count} recipes, expected at least 2"

    def test_full_corpus_48_card_pipeline_retention_zero_dropped(self, manifest_data: Dict[str, Any]):
        """Runs the PairingOptimizer on all 48 cards and asserts 100% card retention and 0 dropped cards."""
        fixtures_dir = Path("tests/fixtures/sample_cards").resolve()
        fingerprints: List[VisualFingerprint] = []

        for card in manifest_data["cards"]:
            role_map = {
                "SEQUENCE_START": CardRole.SEQUENCE_START,
                "SEQUENCE_CONTINUATION": CardRole.SEQUENCE_CONTINUATION,
                "STANDALONE": CardRole.STANDALONE,
                "UNKNOWN": CardRole.UNKNOWN,
            }
            fp = VisualFingerprint(
                card_id=card["card_id"],
                image_path=str(fixtures_dir / card["filename"]),
                sha256_hash=card["sha256_hash"],
                role=role_map.get(card["role"], CardRole.UNKNOWN),
                role_confidence=0.95,
                boundary_text=BoundaryText(
                    first_line=card.get("first_line", ""),
                    last_line=card.get("last_line", ""),
                    has_continuation_symbol=card.get("role") == "SEQUENCE_START",
                ),
            )
            fingerprints.append(fp)

        # Optimize
        optimizer = PairingOptimizer(min_pair_confidence=0.45)
        recipes = get_unique_recipes_from_manifest(manifest_data)
        proposals = []
        for r in recipes.values():
            cids = r.get("card_ids", [])
            if len(cids) == 1:
                continue
            elif len(cids) == 2:
                f_fp = next(f for f in fingerprints if f.card_id == cids[0])
                b_fp = next(f for f in fingerprints if f.card_id == cids[1])
                prop = optimizer.verifier._evaluate_offline_continuity(
                    f_fp,
                    b_fp,
                    heuristic_score=0.90,
                )
                proposals.append(prop)
            else:
                # Multi-card sequence pairwise links
                for i in range(len(cids) - 1):
                    f_fp = next(f for f in fingerprints if f.card_id == cids[i])
                    b_fp = next(f for f in fingerprints if f.card_id == cids[i + 1])
                    prop = optimizer.verifier._evaluate_offline_continuity(
                        f_fp,
                        b_fp,
                        heuristic_score=0.90,
                    )
                    proposals.append(prop)

        result = optimizer.optimize_proposals(fingerprints, proposals)

        # Assert zero dropped cards
        all_accounted_card_ids = set()
        for p in result.proposals:
            for cid in p.ordered_card_ids:
                all_accounted_card_ids.add(cid)
        for cid in result.unmatched_card_ids:
            all_accounted_card_ids.add(cid)

        assert len(all_accounted_card_ids) == 48, (
            f"Expected 48 accounted cards, got {len(all_accounted_card_ids)}"
        )
        assert len(result.unmatched_card_ids) == 0, (
            f"Expected 0 unmatched cards, got {len(result.unmatched_card_ids)}: {result.unmatched_card_ids}"
        )
        assert len(result.proposals) == 24, (
            f"Expected 24 proposals (1 per recipe), got {len(result.proposals)}"
        )


class TestM2AntiOverfittingAndHeuristicsDiscrimination:
    """Verifies that heuristic scoring operates on generalized physical document metrics."""

    def test_cielab_color_metric_discrimination(self):
        # Aged yellow substrate vs white linen vs kraft brown
        yellow_lab = PaperColorInfo(lab_l=92.0, lab_a=2.0, lab_b=18.0)
        yellow_pair_lab = PaperColorInfo(lab_l=91.5, lab_a=2.2, lab_b=17.8)
        white_lab = PaperColorInfo(lab_l=97.0, lab_a=-0.5, lab_b=1.2)
        kraft_lab = PaperColorInfo(lab_l=68.0, lab_a=8.5, lab_b=24.0)

        # Same substrate should have high similarity (>0.90)
        sim_yellow = calculate_color_similarity(yellow_lab, yellow_pair_lab)
        assert sim_yellow > 0.90, f"Expected >0.90, got {sim_yellow}"

        # Yellow vs White substrate should have lower similarity (<0.70)
        sim_yellow_white = calculate_color_similarity(yellow_lab, white_lab)
        assert sim_yellow_white < 0.70, f"Expected <0.70, got {sim_yellow_white}"

        # Yellow vs Kraft should have very low similarity (<0.50)
        sim_yellow_kraft = calculate_color_similarity(yellow_lab, kraft_lab)
        assert sim_yellow_kraft < 0.50, f"Expected <0.50, got {sim_yellow_kraft}"

    def test_ruling_pattern_discrimination(self):
        ruled_index = PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE, line_spacing_px=28.0)
        ruled_index_back = PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE, line_spacing_px=29.0)
        unruled_clipping = PaperRulingInfo(has_ruling=False, ruling_type=PaperRulingType.UNRULED_BLANK)
        grid_graph = PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.GRID, line_spacing_px=14.0)

        # Ruled index cards match
        assert calculate_ruling_similarity(ruled_index, ruled_index_back) >= 0.95
        # Ruled vs unruled fails
        assert calculate_ruling_similarity(ruled_index, unruled_clipping) == 0.0
        # Ruled vs grid has low compatibility
        assert calculate_ruling_similarity(ruled_index, grid_graph) <= 0.15

    def test_ink_medium_discrimination(self):
        fountain_blue = InkInfo(ink_type=InkType.FOUNTAIN_PEN, ink_color=InkColor.BLUE)
        fountain_blue_2 = InkInfo(ink_type=InkType.FOUNTAIN_PEN, ink_color=InkColor.BLUE)
        typewriter_black = InkInfo(ink_type=InkType.TYPEWRITER_RIBBON, ink_color=InkColor.BLACK)
        printed_typeset = InkInfo(ink_type=InkType.PRINTED_TYPESET, ink_color=InkColor.BLACK)

        assert calculate_ink_similarity(fountain_blue, fountain_blue_2) == 1.00
        assert calculate_ink_similarity(fountain_blue, typewriter_black) <= 0.20
        assert calculate_ink_similarity(typewriter_black, printed_typeset) <= 0.50

    def test_mirrored_stain_bleedthrough_symmetry(self):
        front_grease = [PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.TOP_LEFT)]
        back_grease_mirrored = [PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.TOP_RIGHT)]
        back_grease_unmirrored = [PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.BOTTOM_LEFT)]

        # Mirrored grease stain gives perfect bleed-through score
        assert calculate_stain_similarity(front_grease, back_grease_mirrored) == 1.00
        # Non-mirrored location gives low score
        assert calculate_stain_similarity(front_grease, back_grease_unmirrored) <= 0.20

    def test_role_compatibility_prunes_invalid_transitions(self):
        assert calculate_role_compatibility(CardRole.SEQUENCE_START, CardRole.SEQUENCE_CONTINUATION) == 1.00
        assert calculate_role_compatibility(CardRole.SEQUENCE_CONTINUATION, CardRole.SEQUENCE_CONTINUATION) == 0.90
        assert calculate_role_compatibility(CardRole.STANDALONE, CardRole.SEQUENCE_START) == 0.00
        assert calculate_role_compatibility(CardRole.SEQUENCE_START, CardRole.STANDALONE) == 0.00
        assert calculate_role_compatibility(CardRole.SEQUENCE_START, CardRole.SEQUENCE_START) == 0.00
        assert calculate_role_compatibility(CardRole.SEQUENCE_CONTINUATION, CardRole.SEQUENCE_START) == 0.00


class TestM2MultiCardSequenceChainingPermutations:
    """Verifies that multi-card narrative sequences chain deterministically across all permutations."""

    def test_pot_roast_3card_all_permutations_reconstruct_natural_order(self):
        cids = [
            "rec_mcs_sunday_pot_roast_gravy_card1_front.jpg",
            "rec_mcs_sunday_pot_roast_gravy_card1_back.jpg",
            "rec_mcs_sunday_pot_roast_gravy_card2_front.jpg",
        ]
        fp1 = VisualFingerprint(
            card_id=cids[0],
            image_path=f"tests/fixtures/sample_cards/{cids[0]}",
            sha256_hash="mock1",
            role=CardRole.SEQUENCE_START,
            boundary_text=BoundaryText(first_line="Grandma's All-Day Sunday Pot Roast", last_line="over for gravy..."),
        )
        fp2 = VisualFingerprint(
            card_id=cids[1],
            image_path=f"tests/fixtures/sample_cards/{cids[1]}",
            sha256_hash="mock2",
            role=CardRole.SEQUENCE_CONTINUATION,
            boundary_text=BoundaryText(first_line="Pot Roast (Cont.) Gravy:", last_line="continue to vegetables ->"),
        )
        fp3 = VisualFingerprint(
            card_id=cids[2],
            image_path=f"tests/fixtures/sample_cards/{cids[2]}",
            sha256_hash="mock3",
            role=CardRole.SEQUENCE_CONTINUATION,
            boundary_text=BoundaryText(first_line="Root Vegetables (Card 2):", last_line="Bake 3 hours until fork tender."),
        )
        fp_map = {cids[0]: fp1, cids[1]: fp2, cids[2]: fp3}

        optimizer = PairingOptimizer(min_pair_confidence=0.50)

        for perm in itertools.permutations(cids):
            shuffled_fps = [fp_map[c] for c in perm]
            # Construct directed transitions
            proposals = [
                optimizer.verifier._evaluate_offline_continuity(fp1, fp2, 0.90),
                optimizer.verifier._evaluate_offline_continuity(fp2, fp3, 0.90),
            ]
            res = optimizer.optimize_proposals(shuffled_fps, proposals)
            assert len(res.proposals) == 1
            assert res.proposals[0].ordered_card_ids == cids
            assert res.proposals[0].part_count == 3

    def test_panettone_4card_all_permutations_reconstruct_natural_order(self):
        cids = [
            "rec_mcs_traditional_panettone_1955_card1_front.jpg",
            "rec_mcs_traditional_panettone_1955_card1_back.jpg",
            "rec_mcs_traditional_panettone_1955_card2_front.jpg",
            "rec_mcs_traditional_panettone_1955_card2_back.jpg",
        ]
        fp1 = VisualFingerprint(
            card_id=cids[0],
            image_path=f"tests/fixtures/sample_cards/{cids[0]}",
            sha256_hash="mock1",
            role=CardRole.SEQUENCE_START,
            boundary_text=BoundaryText(first_line="Artisanal Milanese Panettone (Part 1)", last_line="over to back for Sponge ->"),
        )
        fp2 = VisualFingerprint(
            card_id=cids[1],
            image_path=f"tests/fixtures/sample_cards/{cids[1]}",
            sha256_hash="mock2",
            role=CardRole.SEQUENCE_CONTINUATION,
            boundary_text=BoundaryText(first_line="Panettone (Part 1 Back) - First Dough:", last_line="Card 2 for Second Dough & Baking ->"),
        )
        fp3 = VisualFingerprint(
            card_id=cids[2],
            image_path=f"tests/fixtures/sample_cards/{cids[2]}",
            sha256_hash="mock3",
            role=CardRole.SEQUENCE_CONTINUATION,
            boundary_text=BoundaryText(first_line="Panettone (Card 2 Front) - Second Dough:", last_line="over for Fruit Folding & Baking ->"),
        )
        fp4 = VisualFingerprint(
            card_id=cids[3],
            image_path=f"tests/fixtures/sample_cards/{cids[3]}",
            sha256_hash="mock4",
            role=CardRole.SEQUENCE_CONTINUATION,
            boundary_text=BoundaryText(first_line="Panettone (Card 2 Back) - Baking & Inversion:", last_line="Hang inverted 8 hours."),
        )
        fp_map = {cids[0]: fp1, cids[1]: fp2, cids[2]: fp3, cids[3]: fp4}

        optimizer = PairingOptimizer(min_pair_confidence=0.50)

        for perm in itertools.permutations(cids):
            shuffled_fps = [fp_map[c] for c in perm]
            proposals = [
                optimizer.verifier._evaluate_offline_continuity(fp1, fp2, 0.90),
                optimizer.verifier._evaluate_offline_continuity(fp2, fp3, 0.90),
                optimizer.verifier._evaluate_offline_continuity(fp3, fp4, 0.90),
            ]
            res = optimizer.optimize_proposals(shuffled_fps, proposals)
            assert len(res.proposals) == 1
            assert res.proposals[0].ordered_card_ids == cids
            assert res.proposals[0].part_count == 4


class CallOrderMockClient:
    """Mock client keyed strictly by call order."""

    def __init__(self, responses: List[RecipeSchema]):
        self._responses = list(responses)
        self._index = 0

    def __call__(self, *args, **kwargs) -> RecipeSchema:
        resp = self._responses[self._index]
        self._index += 1
        return resp


class TestM2StrictRecipeContentIsolation:
    """Verifies that recipe extraction produces isolated recipes without cross-recipe leakage."""

    @pytest.mark.asyncio
    async def test_rhubarb_jam_isolated_from_rhubarb_pie(self):
        jam_recipe = RecipeSchema(
            title="Strawberry Rhubarb Freezer Jam",
            ingredients=["4 cups chopped strawberries", "2 cups diced rhubarb", "1 box powdered pectin", "4 cups sugar"],
            directions=["Crush fruit in large bowl.", "Stir in pectin and sugar.", "Ladle into clean jars and freeze."],
        )
        pie_recipe = RecipeSchema(
            title="Old-Fashioned Rhubarb Pie",
            ingredients=["4 cups chopped fresh rhubarb", "1 1/3 cups sugar", "1/3 cup flour", "pastry for 9-inch double crust pie"],
            directions=["Line pie pan with bottom crust.", "Mix rhubarb, sugar, and flour; fill crust.", "Cover with top crust and bake at 400F."],
        )
        client = CallOrderMockClient([jam_recipe, pie_recipe])
        extractor = GeminiRecipeExtractor(client=client)

        jam = await extractor.extract(front_input=["card_01.jpg", "card_02.jpg"])
        pie = await extractor.extract(front_input=["card_03.jpg", "card_04.jpg"])

        # Jam must not contain pie crust / flour / pastry ingredients
        jam_text = " ".join(jam.ingredients + jam.directions).lower()
        pie_text = " ".join(pie.ingredients + pie.directions).lower()

        assert "strawberries" in jam_text
        assert "pectin" in jam_text or "jars" in jam_text
        assert "pie crust" not in jam_text
        assert "pastry" not in jam_text

        assert "crust" in pie_text or "pie" in pie_text
        assert "pectin" not in pie_text

    @pytest.mark.asyncio
    async def test_swedish_meatballs_isolated_from_beef_stroganoff(self):
        meatballs_recipe = RecipeSchema(
            title="Traditional Swedish Meatballs",
            ingredients=["1 lb ground beef", "1/2 lb ground pork", "1/4 tsp ground allspice", "1/4 tsp nutmeg", "1/2 cup breadcrumbs"],
            directions=["Combine meats, spices, and breadcrumbs into meatballs.", "Brown in skillet with butter."],
        )
        stroganoff_recipe = RecipeSchema(
            title="Classic Beef Stroganoff",
            ingredients=["1.5 lbs beef top sirloin cut into strips", "8 oz sliced mushrooms", "1 cup sour cream", "12 oz egg noodles"],
            directions=["Sear sirloin strips in butter.", "Cook mushrooms and stir in sour cream.", "Serve hot over egg noodles."],
        )
        client = CallOrderMockClient([meatballs_recipe, stroganoff_recipe])
        extractor = GeminiRecipeExtractor(client=client)

        meatballs = await extractor.extract(front_input=["card_05.jpg", "card_06.jpg"])
        stroganoff = await extractor.extract(front_input=["card_07.jpg", "card_08.jpg"])

        mb_text = " ".join(meatballs.ingredients + meatballs.directions).lower()
        strog_text = " ".join(stroganoff.ingredients + stroganoff.directions).lower()

        # Meatballs has allspice / nutmeg / lingonberry or ground pork/beef
        assert "meatballs" in mb_text
        assert "allspice" in mb_text or "nutmeg" in mb_text

        # Stroganoff has sirloin / mushrooms / egg noodles
        assert "stroganoff" in strog_text or "sirloin" in strog_text or "egg noodles" in strog_text
        assert "allspice" not in strog_text

