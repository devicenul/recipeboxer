"""Unit and validity tests for WP-SV scorer remediation.

Tests verify:
1. Normalized edit distance / CER capping at 1.0:
   - Hypotheses much longer than reference or disjoint text cap at 1.0 (never > 1.0).
   - WER capped at 1.0.
2. Kendall tau behavior when undefined:
   - Card order kendall tau returns None (not 0.0 or 1.0) when no multi-card matched clusters exist.
   - Direction tau returns None when < 2 direction steps are matched.
   - Aggregation properly excludes None and tracks count n.
3. Principled content-similarity recipe matching:
   - Matches predicted recipes to gold recipes using token Jaccard similarity.
   - Bounded by min_overlap threshold (0.10).
4. Split fragments and unmatched predictions:
   - Fragments are tracked as n_unmatched_pred / hallucinated_recipe_rate.
   - Fragments are NOT averaged into per-field extraction metrics (CER, F1).
5. Omission accounting (matched-only vs end-to-end):
   - Unmatched gold recipes are counted as n_omitted_gold / omitted_recipe_rate.
   - 'matched-only' evaluates only successfully paired recipes.
   - 'end-to-end' includes omitted gold recipes as 0-score entries.
6. Category breakdowns strictly grounded in GT:
   - Deck types, faces, and modalities come exclusively from ground truth metadata.
"""

from __future__ import annotations

import pytest

from evals.metrics.extraction import (
    aggregate,
    match_recipes_by_content,
    recipe_content_similarity,
    score_recipe,
)
from evals.metrics.grouping import kendall_tau_order, score_grouping
from evals.metrics.text import cer, wer
from evals.scoring.evaluator import evaluate_deck, score_evaluation


# ============================================================================
# 1. CER & WER CAPPING AT 1.0
# ============================================================================

class TestCerWerCapping:
    """Verifies that CER and WER never exceed 1.0."""

    def test_cer_caps_at_one_when_hyp_much_longer_than_ref(self):
        # Gold is very short (3 chars), hypothesis is long (40+ chars)
        gold = "Pie"
        pred = "Delicious Homemade Apple Cinnamon Galactic Star Pie"
        score = cer(gold, pred)
        assert score == 1.0
        assert score <= 1.0

    def test_cer_caps_at_one_for_failed_extraction_stubs(self):
        gold = "Sticks"
        pred = "Extraction Failed (81899c429200.jpg, 3928238945ab.jpg, 749cd2b220a0.jpg)"
        score = cer(gold, pred)
        assert score == 1.0
        assert score <= 1.0

    def test_wer_caps_at_one(self):
        gold = "bake cake"
        pred = "stir simmer saute fry broil grill roast braise steam bake cake"
        score = wer(gold, pred)
        assert score == 1.0
        assert score <= 1.0

    def test_cer_normal_distance_preserved(self):
        # 1 edit on 10 chars -> 0.10
        assert pytest.approx(cer("abcdefghij", "abcdefghik"), 0.001) == 0.10
        assert cer("identical text", "identical text") == 0.0


# ============================================================================
# 2. KENDALL TAU BEHAVIOR WHEN UNDEFINED
# ============================================================================

class TestKendallTauUndefined:
    """Verifies that Kendall tau returns None when undefined."""

    def test_card_order_tau_none_when_no_multicard_clusters(self):
        # Gold has only singletons
        gold = [["card1.jpg"], ["card2.jpg"]]
        pred = [["card1.jpg"], ["card2.jpg"]]
        tau = kendall_tau_order(pred, gold)
        assert tau is None

    def test_card_order_tau_none_when_gold_multicard_but_pred_all_singletons(self):
        # Gold has a multi-card recipe, but model split them into singletons
        gold = [["card1.jpg", "card2.jpg", "card3.jpg"]]
        pred = [["card1.jpg"], ["card2.jpg"], ["card3.jpg"]]
        tau = kendall_tau_order(pred, gold)
        assert tau is None

    def test_score_grouping_order_tau_is_none(self):
        gold = [["c1.jpg"], ["c2.jpg"]]
        pred = [["c1.jpg"], ["c2.jpg"]]
        res = score_grouping(pred, gold)
        assert res["order_kendall_tau"] is None

    def test_direction_tau_none_when_fewer_than_two_steps(self):
        gold = {
            "title": "Cosmic Soup",
            "ingredients": ["1 asteroid onion"],
            "directions": ["Simmer until glowing."],
        }
        pred = {
            "title": "Cosmic Soup",
            "ingredients": ["1 asteroid onion"],
            "directions": ["Simmer until glowing."],
        }
        res = score_recipe(pred, gold)
        assert res["direction_f1"] == 1.0
        assert res["direction_tau"] is None


# ============================================================================
# 3. CONTENT SIMILARITY RECIPE MATCHING
# ============================================================================

class TestContentSimilarityMatching:
    """Verifies principled recipe matching on content similarity."""

    def test_recipe_content_similarity_identical(self):
        rec = {
            "title": "Nebula Nectar",
            "ingredients": ["2 cups starlight juice", "1 tsp comet dust"],
            "directions": ["Stir under aurora.", "Chill in vacuum."],
        }
        sim = recipe_content_similarity(rec, rec)
        assert pytest.approx(sim, 0.001) == 1.0

    def test_recipe_content_similarity_disjoint(self):
        rec_a = {
            "title": "Nebula Nectar",
            "ingredients": ["2 cups starlight juice"],
            "directions": ["Stir under aurora."],
        }
        rec_b = {
            "title": "Roast Plutonian Ham",
            "ingredients": ["10 lb frozen ham", "cloves"],
            "directions": ["Bake in reactor at 400 degrees."],
        }
        sim = recipe_content_similarity(rec_a, rec_b)
        assert sim < 0.10

    def test_match_recipes_by_content_threshold_filters_unrelated(self):
        pred_recipes = [
            {
                "recipe": {
                    "title": "Nebula Nectar",
                    "ingredients": ["2 cups starlight juice"],
                    "directions": ["Stir under aurora."],
                }
            },
            {
                "recipe": {
                    "title": "Extraction Failed (timeout)",
                    "ingredients": [],
                    "directions": [],
                }
            },
        ]
        gold_recipes = [
            {
                "recipe_id": "r1",
                "structured": {
                    "title": "Nebula Nectar",
                    "ingredients": ["2 cups starlight juice"],
                    "directions": ["Stir under aurora."],
                },
            },
            {
                "recipe_id": "r2",
                "structured": {
                    "title": "Meteorite Pie",
                    "ingredients": ["3 cups rock flour", "1 cup lava"],
                    "directions": ["Crush rocks.", "Bake until magma sets."],
                },
            },
        ]

        matched, unmat_p, unmat_g = match_recipes_by_content(
            pred_recipes, gold_recipes, min_overlap=0.10
        )

        # Nebula Nectar matches r1
        assert len(matched) == 1
        assert matched[0][0] == 0  # pred 0
        assert matched[0][1] == 0  # gold 0
        assert matched[0][2] >= 0.80

        # Pred 1 (Failed extraction stub) is rejected
        assert unmat_p == [1]

        # Gold 1 (Meteorite Pie, never extracted) is omitted
        assert unmat_g == [1]


# ============================================================================
# 4. SPLIT FRAGMENTS & SEPARATION OF GROUPING AND EXTRACTION
# ============================================================================

class TestFragmentAndGroupingSeparation:
    """Verifies that split fragments do not distort per-field extraction metrics."""

    def test_split_fragment_not_averaged_into_field_metrics(self):
        # 1 gold recipe with 2 cards
        deck_gt = {
            "deck_id": "d_test",
            "deck_type": "homogeneous",
            "images": [
                {"image_id": "face1.jpg", "modality": "TYPEWRITTEN"},
                {"image_id": "face2.jpg", "modality": "TYPEWRITTEN"},
            ],
            "recipes": [
                {
                    "recipe_id": "r_solar",
                    "faces": ["face1.jpg", "face2.jpg"],
                    "structured": {
                        "title": "Solar Flare Cake",
                        "ingredients": ["2 cups solar flour", "1 cup plasma milk", "3 sun eggs"],
                        "directions": ["Mix solar flour and plasma milk.", "Fold in sun eggs.", "Bake in solar oven."],
                    },
                }
            ],
        }

        # Model made a grouping error: split face1 and face2 into 2 separate recipes!
        # Pred 0 (face1) contains the full recipe.
        # Pred 1 (face2) contains a tiny 1-line direction fragment.
        deck_output = {
            "deck_id": "d_test",
            "recipes": [
                {
                    "pred_id": "p0",
                    "image_ids": ["face1.jpg"],
                    "confidence": 0.90,
                    "recipe": {
                        "title": "Solar Flare Cake",
                        "ingredients": ["2 cups solar flour", "1 cup plasma milk", "3 sun eggs"],
                        "directions": ["Mix solar flour and plasma milk.", "Fold in sun eggs.", "Bake in solar oven."],
                    },
                },
                {
                    "pred_id": "p1",
                    "image_ids": ["face2.jpg"],
                    "confidence": 0.40,
                    "recipe": {
                        "title": "Bake in solar oven [GUESS]",
                        "ingredients": [],
                        "directions": ["Bake in solar oven."],
                    },
                },
            ],
            "failures": [],
        }

        res = evaluate_deck(deck_output, deck_gt, min_overlap=0.10)
        ext = res["extraction"]

        # Pred 0 matched Gold
        assert ext["n_matched"] == 1
        # Pred 1 is flagged as unmatched fragment
        assert ext["n_unmatched_pred"] == 1
        assert ext["n_omitted_gold"] == 0
        assert ext["hallucinated_recipe_rate"] == 0.50

        # Matched-only extraction metrics evaluate ONLY Pred 0
        matched_only = ext["matched_only"]["overall"]
        assert matched_only["title_cer"]["mean"] == 0.0
        assert matched_only["ingredient_f1"]["mean"] == 1.0
        assert matched_only["direction_f1"]["mean"] == 1.0
        assert matched_only["title_cer"]["n"] == 1

        # End-to-end metrics also evaluate Pred 0 (0 omitted gold recipes)
        e2e = ext["end_to_end"]["overall"]
        assert e2e["ingredient_f1"]["mean"] == 1.0
        assert e2e["title_cer"]["mean"] == 0.0

        # Grouping metrics properly capture the grouping penalty (false split)
        grp = res["grouping"]
        assert grp["exact_cluster_acc"] == 0.0
        assert grp["false_split_rate"] == 1.0


# ============================================================================
# 5. OMISSION ACCOUNTING (MATCHED-ONLY VS END-TO-END)
# ============================================================================

class TestOmissionAccounting:
    """Verifies that omitted recipes are reflected in end-to-end but not matched-only."""

    def test_omitted_gold_recipe_reflected_in_e2e(self):
        deck_gt = {
            "deck_id": "d_omiss",
            "deck_type": "mixed",
            "images": [
                {"image_id": "c1.jpg", "modality": "CLIPPING"},
                {"image_id": "c2.jpg", "modality": "CLIPPING"},
            ],
            "recipes": [
                {
                    "recipe_id": "r_extracted",
                    "faces": ["c1.jpg"],
                    "structured": {
                        "title": "Lunar Loaf",
                        "ingredients": ["1 cup moon dust"],
                        "directions": ["Bake in vacuum."],
                    },
                },
                {
                    "recipe_id": "r_omitted",
                    "faces": ["c2.jpg"],
                    "structured": {
                        "title": "Comet Cookies",
                        "ingredients": ["2 cups ice"],
                        "directions": ["Freeze immediately."],
                    },
                },
            ],
        }

        # Model extracted Lunar Loaf, but completely missed Comet Cookies (timeout/error)
        deck_output = {
            "deck_id": "d_omiss",
            "recipes": [
                {
                    "pred_id": "p1",
                    "image_ids": ["c1.jpg"],
                    "confidence": 0.95,
                    "recipe": {
                        "title": "Lunar Loaf",
                        "ingredients": ["1 cup moon dust"],
                        "directions": ["Bake in vacuum."],
                    },
                }
            ],
            "failures": [{"stage": "EXTRACTION", "reason": "TIMEOUT", "image_ids": ["c2.jpg"]}],
        }

        res = evaluate_deck(deck_output, deck_gt, min_overlap=0.10)
        ext = res["extraction"]

        assert ext["n_matched"] == 1
        assert ext["n_omitted_gold"] == 1
        assert ext["omitted_recipe_rate"] == 0.50

        # Matched-only: evaluates only Lunar Loaf (n=1)
        m_overall = ext["matched_only"]["overall"]
        assert m_overall["ingredient_f1"]["mean"] == 1.0
        assert m_overall["ingredient_f1"]["n"] == 1
        assert m_overall["title_cer"]["mean"] == 0.0

        # End-to-end: evaluates both Lunar Loaf (1.0) and Comet Cookies (0.0 omission)
        e2e_overall = ext["end_to_end"]["overall"]
        assert e2e_overall["ingredient_f1"]["n"] == 2
        # Mean of 1.0 and 0.0 is 0.50
        assert pytest.approx(e2e_overall["ingredient_f1"]["mean"], 0.001) == 0.50
        # Title CER: mean of 0.0 and 1.0 is 0.50
        assert pytest.approx(e2e_overall["title_cer"]["mean"], 0.001) == 0.50


# ============================================================================
# 6. CATEGORY BREAKDOWN GROUNDING IN GT
# ============================================================================

class TestCategoryGroundingInGt:
    """Verifies category breakdowns use deck_type and modality exclusively from GT."""

    def test_category_metadata_comes_from_gt_never_sut(self):
        outputs_data = {
            "tier": "T1",
            "status": "ok",
            "decks": [
                {
                    "deck_id": "d01",
                    "recipes": [
                        {
                            "image_ids": ["img1.jpg"],
                            "confidence": 0.90,
                            "recipe": {
                                "title": "Starlight Scones",
                                "ingredients": ["2 cups flour"],
                                "directions": ["Bake."],
                            },
                        }
                    ],
                }
            ],
        }

        gt_data = {
            "tier": "T1",
            "decks": [
                {
                    "deck_id": "d01",
                    "deck_type": "homogeneous",
                    "images": [
                        {"image_id": "img1.jpg", "modality": "HANDWRITTEN_CURSIVE"}
                    ],
                    "recipes": [
                        {
                            "recipe_id": "r100",
                            "faces": ["img1.jpg"],
                            "structured": {
                                "title": "Starlight Scones",
                                "ingredients": ["2 cups flour"],
                                "directions": ["Bake."],
                            },
                        }
                    ],
                }
            ],
        }

        report = score_evaluation(outputs_data, gt_data)
        categories = report["categories"]

        # Must contain GT deck_type
        assert "homogeneous" in categories
        assert categories["homogeneous"]["title_cer"] == 0.0
        assert categories["homogeneous"]["ingredient_f1"] == 1.0

        # Must contain GT modality
        assert "HANDWRITTEN_CURSIVE" in categories
        assert categories["HANDWRITTEN_CURSIVE"]["ingredient_f1"] == 1.0

        # Must contain GT faces count
        assert "1-card" in categories
        assert categories["1-card"]["bcubed_f1"] is None
