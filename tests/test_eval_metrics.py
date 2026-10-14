"""Comprehensive unit tests for evals.metrics.

Rewritten for WP-E5 to test the new pure-function evals.metrics suite:
- Text normalization, unicode NFKC, fractions, and edit distance.
- CER, WER, and case-handling.
- Grouping: B-cubed, ARI, exact-cluster accuracy, order Kendall tau, standalone accuracy,
  false-merge/split rates, Hungarian recipe matching.
- Extraction: title CER, ingredient Hungarian (CER <= 0.1), quantity/unit exact match with synonyms,
  direction F1 + tau, per-field CER, hallucination/omission rates, [illegible] precision.
- Calibration: Expected Calibration Error (ECE).
- Ops: ledger summary with token counts, latency, retries, and USD pricing.
- Aggregation: overall means and per-group breakdowns with explicit n.
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
# 1. LEVENSHTEIN DISTANCE & EDIT DISTANCE TESTS
# ============================================================================

class TestLevenshteinDistance:
    """Tests for exact Levenshtein edit distance computation."""

    def test_identical_sequences(self):
        assert levenshtein_distance("", "") == 0
        assert levenshtein_distance("recipe", "recipe") == 0
        assert levenshtein_distance(["a", "b", "c"], ["a", "b", "c"]) == 0

    def test_empty_sequence_boundaries(self):
        assert levenshtein_distance("", "sugar") == 5
        assert levenshtein_distance("butter", "") == 6
        assert levenshtein_distance([], ["flour", "sugar"]) == 2

    def test_single_edits(self):
        # Insertion
        assert levenshtein_distance("bake", "bakes") == 1
        # Deletion
        assert levenshtein_distance("flour", "four") == 1
        # Substitution
        assert levenshtein_distance("pan", "pot") == 2
        assert levenshtein_distance("stir", "star") == 1

    def test_transpositions_and_longer_edits(self):
        assert levenshtein_distance("kitten", "sitting") == 3
        assert levenshtein_distance("roast", "toast") == 1
        assert levenshtein_distance("chocolate", "caramel") == 7

    def test_asymmetric_sequence_lengths(self):
        assert levenshtein_distance("a", "abcdefghij") == 9
        assert levenshtein_distance("abcdefghij", "a") == 9


# ============================================================================
# 2. TEXT NORMALIZATION & FRACTION EXTRACTION TESTS
# ============================================================================

class TestTextNormalization:
    """Tests for text cleaning, unicode vulgar fractions, and punctuation handling."""

    def test_unicode_vulgar_fractions_conversion(self):
        raw = "Add ½ cup butter, ¼ tsp salt, ¾ cup brown sugar, and ⅛ tsp nutmeg."
        norm = normalize_text(raw)
        assert "1/2 cup butter" in norm
        assert "1/4 tsp salt" in norm
        assert "3/4 cup brown sugar" in norm
        assert "1/8 tsp nutmeg" in norm
        assert "½" not in norm

    def test_all_standard_vulgar_fractions(self):
        fractions_str = "½ ⅓ ⅔ ¼ ¾ ⅕ ⅖ ⅗ ⅘ ⅙ ⅚ ⅛ ⅜ ⅝ ⅞"
        norm = normalize_text(fractions_str)
        expected = [
            "1/2", "1/3", "2/3", "1/4", "3/4", "1/5", "2/5", "3/5", "4/5",
            "1/6", "5/6", "1/8", "3/8", "5/8", "7/8",
        ]
        for exp in expected:
            assert exp in norm

    def test_smart_quotes_dashes_degrees(self):
        raw = '“Grandma’s Special”—Bake at 375°F for 45 mins.'
        norm = normalize_text(raw)
        assert "375 f" in norm
        assert "grandma" in norm
        assert "“" not in norm
        assert "”" not in norm

    def test_whitespace_collapsing(self):
        raw = "   Mix   flour  \n\t  and   sugar.  "
        norm = normalize_text(raw)
        assert norm == "mix flour and sugar"

    def test_case_and_punctuation_preservation_options(self):
        raw = "Bake at 350°F!"
        norm_default = normalize_text(raw)
        assert norm_default == "bake at 350 f"

        norm_preserve = normalize_text(raw, case_sensitive=True, preserve_punctuation=True)
        assert "Bake" in norm_preserve
        assert "!" in norm_preserve

    def test_unicode_nfkc_normalization(self):
        # Ligature 'ﬁ' (U+FB01) should normalize to 'fi' under NFKC
        raw = "ﬁne flour"
        norm = normalize_text(raw)
        assert "fine flour" == norm

    def test_extract_fractions(self):
        text = "Use 1 1/2 cups flour, ½ cup sugar, 3/4 tsp cinnamon, and ⅛ tsp allspice."
        fracs = extract_fractions(text)
        assert "1 1/2" in fracs
        assert "1/2" in fracs
        assert "3/4" in fracs
        assert "1/8" in fracs

    def test_extract_fractions_hyphenated_and_slashes(self):
        text = "1-1/2 cups flour, 1⁄2 cup milk, 3∕4 tsp salt"
        fracs = extract_fractions(text)
        assert "1 1/2" in fracs
        assert "1/2" in fracs
        assert "3/4" in fracs


# ============================================================================
# 3. CER & WER CALCULATION TESTS
# ============================================================================

class TestErrorRates:
    """Tests for CER and WER metric accuracy and boundary conditions."""

    def test_cer_exact_match(self):
        assert cer("Preheat oven to 375°F", "Preheat oven to 375°F") == 0.0
        # Unicode fraction equivalence: ½ == 1/2
        assert cer("1/2 cup butter", "½ cup butter") == 0.0

    def test_cer_empty_strings(self):
        assert cer("", "") == 0.0
        assert cer(None, None) == 0.0
        assert cer("sugar", "") == 1.0
        assert cer("", "sugar") == 1.0

    def test_cer_single_character_corruption(self):
        # 1 edit over 4 normalized chars = 0.25
        c = cer("salt", "silt", normalize=False)
        assert pytest.approx(c, 0.001) == 0.25

    def test_cer_case_sensitivity_option(self):
        c_insensitive = cer("Flour", "flour", case_sensitive=False)
        assert c_insensitive == 0.0
        c_sensitive = cer("Flour", "flour", case_sensitive=True)
        assert c_sensitive > 0.0

    def test_wer_exact_match(self):
        assert wer("Combine flour sugar salt", "Combine flour sugar salt") == 0.0
        assert wer("Add 1/2 cup milk", "Add ½ cup milk") == 0.0

    def test_wer_empty_strings(self):
        assert wer("", "") == 0.0
        assert wer(None, None) == 0.0
        assert wer("one two", "") == 1.0
        assert wer("", "one two") == 1.0


# ============================================================================
# 4. UNIT & QUANTITY NORMALIZATION TESTS
# ============================================================================

class TestUnitAndQuantityNormalization:
    """Tests for ingredient unit synonyms and quantity equivalence."""

    def test_unit_synonyms(self):
        assert normalize_unit("c") == "cup"
        assert normalize_unit("c.") == "cup"
        assert normalize_unit("cup") == "cup"
        assert normalize_unit("cups") == "cup"

        assert normalize_unit("tsp") == "tsp"
        assert normalize_unit("t") == "tsp"
        assert normalize_unit("teaspoon") == "tsp"

        assert normalize_unit("tbsp") == "tbsp"
        assert normalize_unit("T") == "tbsp"
        assert normalize_unit("tablespoon") == "tbsp"

        assert normalize_unit("lb") == "lb"
        assert normalize_unit("pound") == "lb"
        assert normalize_unit("lbs") == "lb"

        assert normalize_unit("oz") == "oz"
        assert normalize_unit("ounce") == "oz"

    def test_quantities_match(self):
        assert quantities_match("1 1/2", "1-1/2") is True
        assert quantities_match("1 1/2", "1.5") is True
        assert quantities_match("1/2", "0.5") is True
        assert quantities_match("½", "1/2") is True
        assert quantities_match("3/4", "0.75") is True
        assert quantities_match("2", "2.0") is True
        assert quantities_match("1/2", "3/4") is False


# ============================================================================
# 5. GROUPING METRICS TESTS
# ============================================================================

class TestGroupingMetrics:
    """Tests for B-cubed, ARI, exact-cluster, order Kendall tau, and Hungarian matching."""

    def test_match_recipes_perfect_overlap(self):
        pred = [["c1", "c2"], ["c3"]]
        gold = [["c1", "c2"], ["c3"]]
        matches = match_recipes(pred, gold)
        assert len(matches) == 2
        # (0, 0, 1.0) and (1, 1, 1.0)
        for p, g, iou in matches:
            assert p is not None and g is not None
            assert iou == 1.0

    def test_match_recipes_unmatched_pred_and_gold(self):
        pred = [["c1"], ["c2"]]
        gold = [["c1"], ["c3"]]
        matches = match_recipes(pred, gold)
        # Matches: c1 matched (0, 0, 1.0), c2 unmatched (1, None, 0.0), c3 unmatched (None, 1, 0.0)
        assert (0, 0, 1.0) in matches
        assert (1, None, 0.0) in matches
        assert (None, 1, 0.0) in matches

    def test_score_grouping_identical(self):
        pred = [["c1", "c2"], ["c3"]]
        gold = [["c1", "c2"], ["c3"]]
        res = score_grouping(pred, gold)
        assert res["bcubed_p"] == 1.0
        assert res["bcubed_r"] == 1.0
        assert res["bcubed_f1"] == 1.0
        assert res["ari"] == 1.0
        assert res["exact_cluster_acc"] == 1.0
        assert res["order_kendall_tau"] == 1.0
        assert res["standalone_acc"] == 1.0
        assert res["false_merge_rate"] == 0.0
        assert res["false_split_rate"] == 0.0
        assert res["n_pred"] == 2
        assert res["n_gold"] == 2

    def test_score_grouping_face_order_kendall_tau(self):
        # Card order reversed in multi-card recipe
        pred = [["c2", "c1"]]
        gold = [["c1", "c2"]]
        res = score_grouping(pred, gold)
        # 1 discordant pair, 0 concordant -> tau = -1.0
        assert res["order_kendall_tau"] == -1.0
        # Cluster sets are identical: exact_cluster_acc is 1.0 (set based)
        assert res["exact_cluster_acc"] == 1.0

    def test_score_grouping_false_merge_and_split(self):
        # Over-clustering: merged two gold recipes
        pred = [["c1", "c2", "c3"]]
        gold = [["c1", "c2"], ["c3"]]
        res = score_grouping(pred, gold)
        assert res["false_merge_rate"] > 0.0
        assert res["false_split_rate"] == 0.0

        # Under-clustering: split one recipe into two singletons
        pred_split = [["c1"], ["c2"], ["c3"]]
        res_split = score_grouping(pred_split, gold)
        assert res_split["false_split_rate"] > 0.0
        assert res_split["false_merge_rate"] == 0.0


# ============================================================================
# 6. RECIPE EXTRACTION METRICS TESTS
# ============================================================================

class TestExtractionMetrics:
    """Tests for score_recipe and aggregate functions."""

    @pytest.fixture
    def sample_gold(self) -> dict:
        return {
            "title": "Grandma's Apple Crisp",
            "yield": "6 servings",
            "source_note": "1948 Handwritten Card",
            "ingredients": [
                {"raw": "5 medium apples, sliced", "quantity": "5", "unit": None, "item": "apples"},
                {"raw": "1/2 cup granulated sugar", "quantity": "1/2", "unit": "cup", "item": "sugar"},
                {"raw": "3/4 cup flour", "quantity": "3/4", "unit": "cup", "item": "flour"},
                {"raw": "1/3 cup butter", "quantity": "1/3", "unit": "cup", "item": "butter"},
                {"raw": "1/4 tsp salt", "quantity": "1/4", "unit": "tsp", "item": "salt"},
            ],
            "directions": [
                "1. Preheat oven to 375°F and grease dish.",
                "2. Toss apples with sugar.",
                "3. Combine flour, butter, and salt until crumbly.",
                "4. Spread topping over apples and bake 35 mins.",
            ],
            "notes": ["Serve warm with vanilla ice cream."],
        }

    def test_identical_recipe_scores(self, sample_gold: dict):
        res = score_recipe(sample_gold, sample_gold)
        assert res["title_cer"] == 0.0
        assert res["ingredient_p"] == 1.0
        assert res["ingredient_r"] == 1.0
        assert res["ingredient_f1"] == 1.0
        assert res["qty_unit_exact"] == 1.0
        assert res["direction_f1"] == 1.0
        assert res["direction_tau"] == 1.0
        assert res["hallucination_rate"] == 0.0
        assert res["omission_rate"] == 0.0
        assert res["illegible_precision"] is None

    def test_unit_synonym_matching_in_extraction(self, sample_gold: dict):
        pred = {
            "title": "Grandma's Apple Crisp",
            "ingredients": [
                {"raw": "5 medium apples, sliced", "quantity": "5", "unit": None, "item": "apples"},
                # "1/2 cup granulated sugar" with '½' and 'c.' synonym
                {"raw": "1/2 cup granulated sugar", "quantity": "½", "unit": "c.", "item": "sugar"},
                # "3/4 cup flour" with '0.75' and 'cups' synonym
                {"raw": "3/4 cup flour", "quantity": "0.75", "unit": "cups", "item": "flour"},
                # "1/3 cup butter"
                {"raw": "1/3 cup butter", "quantity": "1/3", "unit": "cup", "item": "butter"},
                # "1/4 tsp salt" with 't.' synonym for tsp
                {"raw": "1/4 tsp salt", "quantity": "1/4", "unit": "t.", "item": "salt"},
            ],
            "directions": sample_gold["directions"],
        }
        res = score_recipe(pred, sample_gold)
        # All 5 ingredients matched (CER <= 0.1) and all units matched via synonyms
        assert res["ingredient_f1"] == 1.0
        assert res["qty_unit_exact"] == 1.0

    def test_omitted_recipe_scores(self, sample_gold: dict):
        res = score_recipe(None, sample_gold)
        assert res["title_cer"] == 1.0
        assert res["ingredient_f1"] == 0.0
        assert res["direction_f1"] == 0.0
        assert res["omission_rate"] == 1.0
        assert res["hallucination_rate"] == 0.0

    def test_hallucination_rate(self, sample_gold: dict):
        pred = {
            "title": sample_gold["title"],
            "ingredients": sample_gold["ingredients"] + [
                {"raw": "1 cup chocolate chips (extra)", "quantity": "1", "unit": "cup", "item": "chocolate"}
            ],
            "directions": sample_gold["directions"],
        }
        res = score_recipe(pred, sample_gold)
        # 1 extra predicted line out of 10 total predicted lines = 0.1
        assert res["hallucination_rate"] == 0.1

    def test_illegible_marker_precision(self, sample_gold: dict):
        gold = dict(sample_gold)
        gold["ingredients"] = [
            {"raw": "1 [illegible] sugar", "quantity": "1", "unit": "[illegible]", "item": "sugar"}
        ]
        pred = {
            "title": sample_gold["title"],
            "ingredients": [
                {"raw": "1 [illegible: stain] sugar", "quantity": "1", "unit": None, "item": "sugar"}
            ],
            "directions": [],
        }
        res = score_recipe(pred, gold, gold_verbatim=["1 [illegible] sugar"])
        assert res["illegible_precision"] == 1.0


# ============================================================================
# 7. CALIBRATION & OPS TESTS
# ============================================================================

class TestCalibrationAndOps:
    """Tests for ECE and ledger summary with pricing."""

    def test_ece_perfect_calibration(self):
        # High confidence and correct; low confidence and incorrect
        confs = [0.95, 0.95, 0.1, 0.1]
        correct = [True, True, False, False]
        score = ece(confs, correct, n_bins=10)
        assert score < 0.15

    def test_ece_complete_miscalibration(self):
        # 100% confidence, but 0% correct
        confs = [1.0, 1.0, 1.0]
        correct = [False, False, False]
        score = ece(confs, correct, n_bins=10)
        assert score == 1.0

    def test_summarize_ledger_with_pricing(self):
        ledger = {
            "calls": [
                {
                    "stage": "transcribe",
                    "model": "gemini-2.5-flash",
                    "input_tokens": 1_000_000,
                    "output_tokens": 500_000,
                    "thinking_tokens": 0,
                    "latency_ms": 1200.0,
                    "attempts": 2,  # 1 retry
                },
                {
                    "stage": "structure",
                    "model": "gemini-2.5-pro",
                    "input_tokens": 500_000,
                    "output_tokens": 200_000,
                    "thinking_tokens": 100_000,
                    "latency_ms": 3000.0,
                    "attempts": 1,
                },
            ],
            "failures": [{"stage": "transcribe", "reason": "rate_limit"}],
        }
        pricing = {
            "gemini-2.5-flash": {
                "input_per_mtok": 0.10,
                "output_per_mtok": 0.40,
                "thinking_per_mtok": 0.40,
            },
            "gemini-2.5-pro": {
                "input_per_mtok": 1.25,
                "output_per_mtok": 5.00,
                "thinking_per_mtok": 5.00,
            },
        }

        res = summarize_ledger(ledger, pricing)
        assert res["total_calls"] == 2
        assert res["total_retries"] == 1
        assert res["total_latency_ms"] == 4200.0
        assert res["total_input_tokens"] == 1_500_000
        assert res["total_output_tokens"] == 700_000

        # Flash cost: (1M * 0.10 + 0.5M * 0.40) = 0.10 + 0.20 = $0.30
        # Pro cost: (0.5M * 1.25 + 0.2M * 5.00 + 0.1M * 5.00) = 0.625 + 1.00 + 0.50 = $2.125
        # Total: $2.425
        assert pytest.approx(res["total_cost_usd"], 0.001) == 2.425
        assert res["failures_by_stage"]["transcribe"] == 1


# ============================================================================
# 8. AGGREGATION TESTS
# ============================================================================

class TestAggregation:
    """Tests for aggregate function with overall means and category breakdowns."""

    def test_aggregate_means_and_breakdowns(self):
        rows = [
            {
                "deck_type": "homogeneous",
                "modality": "HANDWRITTEN_CURSIVE",
                "faces": 2,
                "title_cer": 0.05,
                "ingredient_f1": 0.90,
                "illegible_precision": None,  # should be excluded from illegible_precision count
            },
            {
                "deck_type": "homogeneous",
                "modality": "HANDWRITTEN_CURSIVE",
                "faces": 2,
                "title_cer": 0.15,
                "ingredient_f1": 0.70,
                "illegible_precision": 1.0,
            },
            {
                "deck_type": "mixed",
                "modality": "TYPEWRITTEN",
                "faces": 1,
                "title_cer": 0.00,
                "ingredient_f1": 1.00,
                "illegible_precision": "N/A",  # string N/A excluded
            },
        ]

        agg = aggregate(rows, by=["deck_type", "modality"])
        overall = agg["overall"]

        # title_cer mean: (0.05 + 0.15 + 0.00) / 3 = 0.0667
        assert pytest.approx(overall["title_cer"]["mean"], 0.001) == 0.0667
        assert overall["title_cer"]["n"] == 3

        # illegible_precision has only 1 valid value
        assert overall["illegible_precision"]["mean"] == 1.0
        assert overall["illegible_precision"]["n"] == 1

        # Breakdowns
        breakdowns = agg["breakdowns"]
        assert "deck_type" in breakdowns
        assert "homogeneous" in breakdowns["deck_type"]
        assert breakdowns["deck_type"]["homogeneous"]["count"] == 2
        assert breakdowns["deck_type"]["mixed"]["count"] == 1
