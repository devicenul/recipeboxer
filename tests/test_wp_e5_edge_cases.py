"""Thorough edge-case tests for WP-E5 eval metrics.

Covers:
- Empty predictions / gold / both
- All singletons (extreme under-clustering)
- One giant cluster (extreme over-clustering)
- Identical partitions
- Permutation invariance of clusters and card IDs
- Unicode fractions, mixed numbers, and extreme text normalization
- Kendall tau of face order within matched clusters (exact, reversed, partial, singletons)
- Standalone accuracy edge cases
- False merge and false split boundary behaviors
- Extraction edge cases: empty fields, reversed lines, unit synonyms, hallucination / omission
- Illegible precision approximations
- Calibration (ECE) edge cases
- Ops ledger with custom pricing, missing models, and failure events
- Aggregation with missing / None / N-A values and nested metrics
"""

from __future__ import annotations

import math
import pytest

from evals.metrics import (
    aggregate,
    cer,
    ece,
    extract_fractions,
    levenshtein_distance,
    match_recipes,
    normalize_text,
    normalize_unit,
    quantities_match,
    score_grouping,
    score_recipe,
    summarize_ledger,
    wer,
)


# ============================================================================
# 1. GROUPING EDGE CASES
# ============================================================================

class TestGroupingEdgeCases:
    """Extreme edge cases for grouping metrics."""

    def test_both_empty(self):
        res = score_grouping([], [])
        assert res["bcubed_p"] == 1.0
        assert res["bcubed_r"] == 1.0
        assert res["bcubed_f1"] == 1.0
        assert res["ari"] == 1.0
        assert res["exact_cluster_acc"] == 1.0
        assert res["order_kendall_tau"] == 1.0
        assert res["standalone_acc"] == 1.0
        assert res["false_merge_rate"] == 0.0
        assert res["false_split_rate"] == 0.0
        assert res["n_pred"] == 0
        assert res["n_gold"] == 0

        matches = match_recipes([], [])
        assert matches == []

    def test_empty_pred_non_empty_gold(self):
        gold = [["c1", "c2"], ["c3"]]
        res = score_grouping([], gold)
        assert res["bcubed_p"] == 0.0
        assert res["bcubed_r"] == 0.0
        assert res["bcubed_f1"] == 0.0
        assert res["exact_cluster_acc"] == 0.0
        assert res["false_merge_rate"] == 0.0
        assert res["false_split_rate"] == 1.0  # Same-gold pair (c1, c2) is split
        assert res["n_pred"] == 0
        assert res["n_gold"] == 2

        matches = match_recipes([], gold)
        assert matches == [(None, 0, 0.0), (None, 1, 0.0)]

    def test_non_empty_pred_empty_gold(self):
        pred = [["c1", "c2"], ["c3"]]
        res = score_grouping(pred, [])
        assert res["bcubed_p"] == 0.0
        assert res["bcubed_r"] == 0.0
        assert res["bcubed_f1"] == 0.0
        assert res["exact_cluster_acc"] == 0.0
        assert res["false_merge_rate"] == 0.0  # No diff-gold pairs exist
        assert res["false_split_rate"] == 0.0
        assert res["n_pred"] == 2
        assert res["n_gold"] == 0

        matches = match_recipes(pred, [])
        assert matches == [(0, None, 0.0), (1, None, 0.0)]

    def test_all_singletons_predicted(self):
        # Gold has 2 recipes of 2 cards each
        gold = [["c1", "c2"], ["c3", "c4"]]
        pred = [["c1"], ["c2"], ["c3"], ["c4"]]
        res = score_grouping(pred, gold)

        # Every predicted cluster has size 1, completely pure -> Precision = 1.0
        assert res["bcubed_p"] == 1.0
        # Every gold cluster of size 2 is only half recovered -> Recall = 0.5
        assert res["bcubed_r"] == 0.5
        # F1 = 2 * 1.0 * 0.5 / 1.5 = 2/3 = 0.6667
        assert pytest.approx(res["bcubed_f1"], 0.001) == 2.0 / 3.0

        # Exact cluster accuracy is 0.0
        assert res["exact_cluster_acc"] == 0.0
        # No false merges (no two cards merged)
        assert res["false_merge_rate"] == 0.0
        # All same-gold pairs were split -> false split rate = 1.0
        assert res["false_split_rate"] == 1.0

    def test_one_giant_cluster_predicted(self):
        # Gold has 2 recipes of 2 cards each
        gold = [["c1", "c2"], ["c3", "c4"]]
        pred = [["c1", "c2", "c3", "c4"]]
        res = score_grouping(pred, gold)

        # In predicted cluster of size 4, 2 belong to gold -> Precision = 2/4 = 0.5
        assert res["bcubed_p"] == 0.5
        # All cards of gold cluster are in pred cluster -> Recall = 1.0
        assert res["bcubed_r"] == 1.0
        # F1 = 2 * 0.5 * 1.0 / 1.5 = 2/3 = 0.6667
        assert pytest.approx(res["bcubed_f1"], 0.001) == 2.0 / 3.0

        assert res["exact_cluster_acc"] == 0.0
        # Every pair of different gold recipes was merged -> false merge rate = 1.0
        assert res["false_merge_rate"] == 1.0
        # No same-gold pairs were split -> false split rate = 0.0
        assert res["false_split_rate"] == 0.0

    def test_permutation_invariance_of_cluster_order(self):
        gold = [["c1", "c2"], ["c3", "c4", "c5"], ["c6"]]
        pred = [["c6"], ["c1", "c2"], ["c3", "c4", "c5"]]  # permuted cluster order

        res = score_grouping(pred, gold)
        assert res["bcubed_f1"] == 1.0
        assert res["ari"] == 1.0
        assert res["exact_cluster_acc"] == 1.0
        assert res["order_kendall_tau"] == 1.0
        assert res["standalone_acc"] == 1.0
        assert res["false_merge_rate"] == 0.0
        assert res["false_split_rate"] == 0.0

    def test_permutation_invariance_of_cluster_order_and_items(self):
        # Transposed cluster lists
        gold = [["a", "b"], ["c", "d"]]
        pred1 = [["c", "d"], ["a", "b"]]
        pred2 = [["a", "b"], ["c", "d"]]

        r1 = score_grouping(pred1, gold)
        r2 = score_grouping(pred2, gold)
        assert r1 == r2

    def test_kendall_tau_face_order_subsets(self):
        # 3-card recipe completely reversed
        gold = [["c1", "c2", "c3"]]
        pred = [["c3", "c2", "c1"]]
        res = score_grouping(pred, gold)
        # All 3 pairs inverted: (c1, c2), (c1, c3), (c2, c3) -> tau = -1.0
        assert res["order_kendall_tau"] == -1.0

        # Partially reversed: 1 inverted pair (c2, c3) out of 3 pairs -> tau = (2 - 1)/3 = 0.3333
        pred_partial = [["c1", "c3", "c2"]]
        res_partial = score_grouping(pred_partial, gold)
        assert pytest.approx(res_partial["order_kendall_tau"], 0.001) == 1.0 / 3.0

        # Cluster of size 1 (no face order to check) -> tau = 1.0
        gold_single = [["c1"]]
        pred_single = [["c1"]]
        res_single = score_grouping(pred_single, gold_single)
        assert res_single["order_kendall_tau"] == 1.0

    def test_standalone_accuracy_boundary_cases(self):
        # Decks with only multi-card recipes
        gold_multi = [["c1", "c2"], ["c3", "c4"]]
        pred_multi = [["c1", "c2"], ["c3", "c4"]]
        res = score_grouping(pred_multi, gold_multi)
        # No standalones in either -> 1.0
        assert res["standalone_acc"] == 1.0

        # Model falsely predicts a standalone when none exist
        pred_err = [["c1"], ["c2"], ["c3", "c4"]]
        res_err = score_grouping(pred_err, gold_multi)
        assert res_err["standalone_acc"] == 0.0

        # Gold has standalones, model predicts none
        gold_with_sa = [["c1"], ["c2", "c3"]]
        pred_no_sa = [["c1", "c2", "c3"]]
        res_no_sa = score_grouping(pred_no_sa, gold_with_sa)
        assert res_no_sa["standalone_acc"] == 0.0


# ============================================================================
# 2. TEXT & FRACTION NORMALIZATION EDGE CASES
# ============================================================================

class TestTextAndFractionEdgeCases:
    """Edge cases for text normalization and fraction parsing."""

    def test_unicode_fractions_and_mixed_numbers(self):
        # Test all mixed combinations
        assert extract_fractions("1½ cups") == ["1 1/2"]
        assert extract_fractions("2-¾ tsp") == ["2 3/4"]
        assert extract_fractions("10 ⅛ grams") == ["10 1/8"]
        assert extract_fractions("3⁄4 cup") == ["3/4"]  # U+2044 fraction slash
        assert extract_fractions("1∕2 tsp") == ["1/2"]  # U+2215 division slash

    def test_quantities_match_edge_cases(self):
        assert quantities_match(None, None) is True
        assert quantities_match("1", None) is False
        assert quantities_match(None, "1") is False
        assert quantities_match("", "") is True
        assert quantities_match("1 1/2", "3/2") is True
        assert quantities_match("2 1/4", "2.25") is True
        assert quantities_match("0.33333", "1/3") is True
        assert quantities_match("gibberish", "gibberish") is True
        assert quantities_match("gibberish", "other") is False

    def test_normalize_unit_synonyms_exhaustive(self):
        # Teaspoon
        for u in ("tsp", "tsp.", "t", "t.", "teaspoon", "teaspoons"):
            assert normalize_unit(u) == "tsp"

        # Tablespoon
        for u in ("tbsp", "tbsp.", "tbs", "tbs.", "T", "T.", "TB", "TBS", "tablespoon", "tablespoons"):
            assert normalize_unit(u) == "tbsp"

        # Cup
        for u in ("c", "c.", "cup", "cups"):
            assert normalize_unit(u) == "cup"

        # Pound
        for u in ("lb", "lb.", "lbs", "lbs.", "pound", "pounds"):
            assert normalize_unit(u) == "lb"

        # Ounce
        for u in ("oz", "oz.", "ounce", "ounces"):
            assert normalize_unit(u) == "oz"

        # Empty
        assert normalize_unit(None) == ""
        assert normalize_unit("") == ""

    def test_cer_and_wer_extremes(self):
        # Normalization with case insensitive default
        assert cer("HELLO WORLD", "hello world") == 0.0
        # Unicode NFKC ligature handling
        assert cer("ﬂour", "flour") == 0.0

        # One empty, one non-empty
        assert cer("test", "") == 1.0
        assert cer("", "test") == 1.0
        assert wer("test word", "") == 1.0
        assert wer("", "test word") == 1.0


# ============================================================================
# 3. EXTRACTION EDGE CASES
# ============================================================================

class TestExtractionEdgeCases:
    """Edge cases for score_recipe and illegible precision."""

    def test_score_recipe_both_empty(self):
        gold = {"title": "", "ingredients": [], "directions": []}
        pred = {"title": "", "ingredients": [], "directions": []}
        res = score_recipe(pred, gold)
        assert res["title_cer"] == 0.0
        assert res["ingredient_f1"] == 1.0
        assert res["qty_unit_exact"] == 1.0
        assert res["direction_f1"] == 1.0
        assert res["direction_tau"] == 1.0
        assert res["hallucination_rate"] == 0.0
        assert res["omission_rate"] == 0.0
        assert res["illegible_precision"] is None

    def test_score_recipe_ingredients_reversed(self):
        gold = {
            "title": "Cookies",
            "ingredients": ["1 cup sugar", "2 cups flour"],
            "directions": ["1. Mix.", "2. Bake."],
        }
        # Pred provides ingredients in reversed order
        pred = {
            "title": "Cookies",
            "ingredients": ["2 cups flour", "1 cup sugar"],
            "directions": ["1. Mix.", "2. Bake."],
        }
        res = score_recipe(pred, gold)
        # Hungarian bipartite matching aligns them regardless of order
        assert res["ingredient_f1"] == 1.0
        assert res["qty_unit_exact"] == 1.0

    def test_direction_reversed_order_tau(self):
        gold = {
            "title": "Tea",
            "ingredients": ["1 teabag", "1 cup water"],
            "directions": ["1. Boil water.", "2. Steep teabag.", "3. Serve."],
        }
        pred = {
            "title": "Tea",
            "ingredients": ["1 teabag", "1 cup water"],
            "directions": ["1. Serve.", "2. Steep teabag.", "3. Boil water."],
        }
        res = score_recipe(pred, gold)
        assert res["direction_f1"] == 1.0
        # Completely inverted 3-step order -> tau = -1.0
        assert res["direction_tau"] == -1.0

    def test_100_percent_hallucination_and_omission(self):
        gold = {
            "title": "Cake",
            "ingredients": ["1 cup sugar"],
            "directions": ["1. Bake."],
        }
        pred = {
            "title": "Stew",
            "ingredients": ["1 lb beef"],
            "directions": ["1. Simmer."],
        }
        res = score_recipe(pred, gold)
        assert res["ingredient_f1"] == 0.0
        assert res["direction_f1"] == 0.0
        assert res["hallucination_rate"] == 1.0
        assert res["omission_rate"] == 1.0

    def test_illegible_marker_precision_variations(self):
        # Case 1: Multiple illegible markers where gold has illegible indicators
        gold = {
            "title": "Old Recipe",
            "ingredients": ["1 [illegible] milk", "2 tbsp [illegible]"],
            "directions": [],
        }
        pred = {
            "title": "Old Recipe",
            "ingredients": ["1 [illegible: stain] milk", "2 tbsp [illegible]"],
            "directions": [],
        }
        res = score_recipe(pred, gold)
        assert res["illegible_precision"] == 1.0

        # Case 2: False illegible marker (gold was crystal clear "sugar")
        gold_clear = {
            "title": "Sweet Tea",
            "ingredients": ["1 cup sugar"],
            "directions": ["1. Mix."],
        }
        pred_hallucinated_marker = {
            "title": "Sweet Tea",
            "ingredients": ["1 cup [illegible]"],
            "directions": ["1. Mix."],
        }
        res_false = score_recipe(pred_hallucinated_marker, gold_clear, gold_verbatim=["1 cup sugar"])
        assert res_false["illegible_precision"] == 0.0


# ============================================================================
# 4. CALIBRATION & OPS EDGE CASES
# ============================================================================

class TestCalibrationAndOpsEdgeCases:
    """Edge cases for ECE and ledger summarization."""

    def test_ece_edge_cases(self):
        # Empty
        assert ece([], []) == 0.0

        # Perfect zero calibration
        assert ece([0.0, 0.0], [False, False], n_bins=5) == 0.0

        # Perfect one calibration
        assert ece([1.0, 1.0], [True, True], n_bins=5) == 0.0

        # Mismatched length raises ValueError
        with pytest.raises(ValueError, match="equal length"):
            ece([0.5], [True, False])

        # Non-positive bins raises ValueError
        with pytest.raises(ValueError, match="positive"):
            ece([0.5], [True], n_bins=0)

    def test_summarize_ledger_empty(self):
        res = summarize_ledger({})
        assert res["total_calls"] == 0
        assert res["total_tokens"] == 0
        assert res["total_cost_usd"] is None
        assert res["by_stage"] == {}

    def test_summarize_ledger_failures_only(self):
        ledger = {
            "calls": [],
            "failures": [
                {"stage": "fingerprint", "reason": "timeout"},
                {"stage": "fingerprint", "reason": "timeout"},
                {"stage": "structure", "reason": "parse_error"},
            ],
        }
        res = summarize_ledger(ledger)
        assert res["total_calls"] == 0
        assert res["failures_by_stage"]["fingerprint"] == 2
        assert res["failures_by_stage"]["structure"] == 1
        assert res["by_stage"]["fingerprint"]["failures"] == 2


# ============================================================================
# 5. AGGREGATION EDGE CASES
# ============================================================================

class TestAggregationEdgeCases:
    """Edge cases for aggregate function."""

    def test_aggregate_empty_rows(self):
        agg = aggregate([])
        assert agg["count"] == 0
        assert agg["overall"] == {}
        assert "deck_type" in agg["breakdowns"]

    def test_aggregate_all_none_and_na(self):
        rows = [
            {"title_cer": None, "direction_tau": "N/A", "deck_type": "mixed"},
            {"title_cer": None, "direction_tau": "None", "deck_type": "mixed"},
        ]
        agg = aggregate(rows, by=["deck_type"])
        overall = agg["overall"]
        assert overall["title_cer"]["mean"] is None
        assert overall["title_cer"]["n"] == 0
        assert overall["direction_tau"]["mean"] is None
        assert overall["direction_tau"]["n"] == 0

    def test_aggregate_nested_fields(self):
        rows = [
            {
                "field_cer": {"title": 0.1, "ingredients": 0.2},
                "deck_type": "t1",
            },
            {
                "field_cer": {"title": 0.3, "ingredients": None},
                "deck_type": "t1",
            },
        ]
        agg = aggregate(rows, by=["deck_type"])
        field_summary = agg["overall"]["field_cer"]
        # title mean: (0.1 + 0.3) / 2 = 0.2
        assert pytest.approx(field_summary["title"]["mean"], 0.001) == 0.2
        assert field_summary["title"]["n"] == 2
        # ingredients mean: 0.2 / 1 = 0.2
        assert pytest.approx(field_summary["ingredients"]["mean"], 0.001) == 0.2
        assert field_summary["ingredients"]["n"] == 1
