"""Comprehensive unit tests for RecipeBoxer quantitative evaluation metrics.

Tests:
1. Levenshtein distance dynamic programming (strings, word sequences, edge cases).
2. Text normalization and unicode vulgar fraction standardization.
3. Character Error Rate (CER) and Word Error Rate (WER) mathematical accuracy.
4. Recipe-level transcription error rates across ingredients, directions, fractions, and metadata.
5. Pairing metrics (Precision, Recall, F1, multi-card groupings, standalone cards).
6. Schema conformity validator (Plan To Eat 9-column, Schema.org JSON-LD, ISO 8601 durations).
"""

from __future__ import annotations

import math
import pytest

from recipeboxer.evals.metrics import (
    PairingMetricResult,
    SchemaConformityResult,
    TranscriptionMetricResult,
    calculate_direction_sequence_score,
    calculate_fraction_accuracy,
    calculate_pairing_metrics,
    calculate_transcription_metrics,
    character_error_rate,
    extract_fractions,
    levenshtein_distance,
    normalize_text,
    validate_schema_conformity,
    word_error_rate,
)
from recipeboxer.models.correlation import CorrelationProposal, CorrelationResult
from recipeboxer.models.recipe import RecipeSchema


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
        # Test that O(min(N, M)) swapping works identically in both orderings
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
        expected = ["1/2", "1/3", "2/3", "1/4", "3/4", "1/5", "2/5", "3/5", "4/5", "1/6", "5/6", "1/8", "3/8", "5/8", "7/8"]
        for exp in expected:
            assert exp in norm

    def test_smart_quotes_dashes_degrees(self):
        raw = '“Grandma’s Special”—Bake at 375°F for 45 mins.'
        norm = normalize_text(raw)
        assert "375 f" in norm or "375" in norm
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

        norm_preserve = normalize_text(raw, preserve_case=True, preserve_punctuation=True)
        assert "Bake" in norm_preserve
        assert "!" in norm_preserve

    def test_extract_fractions(self):
        text = "Use 1 1/2 cups flour, ½ cup sugar, 3/4 tsp cinnamon, and ⅛ tsp allspice."
        fracs = extract_fractions(text)
        assert "1 1/2" in fracs or "1/2" in fracs
        assert "3/4" in fracs
        assert "1/8" in fracs

    def test_extract_fractions_unicode_slashes_and_whitespace(self):
        # Test fraction slash U+2044 and division slash U+2215 and spaces around slashes
        text = "Add 1⁄2 cup milk, 3∕4 tsp baking powder, and 1 / 4 cup butter."
        fracs = extract_fractions(text)
        assert "1/2" in fracs
        assert "3/4" in fracs
        assert "1/4" in fracs

    def test_extract_fractions_hyphenated_mixed_numbers(self):
        text = "1-1/2 cups flour, 2-1/4 tsp sugar, and 3 - 3/4 cups water."
        fracs = extract_fractions(text)
        assert fracs == ["1 1/2", "2 1/4", "3 3/4"]

    def test_extract_fractions_empty_or_none(self):
        assert extract_fractions("") == []
        assert extract_fractions(None) == []
        assert extract_fractions("No numbers here") == []


# ============================================================================
# 3. CER & WER CALCULATION TESTS
# ============================================================================

class TestErrorRates:
    """Tests for CER and WER metric accuracy and boundary conditions."""

    def test_cer_exact_match(self):
        assert character_error_rate("Preheat oven to 375°F", "Preheat oven to 375°F") == 0.0
        assert character_error_rate("1/2 cup butter", "½ cup butter") == 0.0

    def test_cer_empty_strings(self):
        assert character_error_rate("", "") == 0.0
        assert character_error_rate(None, None) == 0.0
        assert character_error_rate("sugar", "") == 1.0
        assert character_error_rate("", "sugar") == 1.0

    def test_cer_single_character_corruption(self):
        # 1 edit over 4 normalized chars = 0.25
        cer = character_error_rate("salt", "silt", normalize=False)
        assert pytest.approx(cer, 0.001) == 0.25

    def test_cer_complete_divergence(self):
        cer = character_error_rate("aaaa", "bbbb", normalize=False)
        assert pytest.approx(cer, 0.001) == 1.0

    def test_wer_exact_match(self):
        assert word_error_rate("Combine flour sugar salt", "Combine flour sugar salt") == 0.0
        assert word_error_rate("Add 1/2 cup milk", "Add ½ cup milk") == 0.0

    def test_wer_empty_strings(self):
        assert word_error_rate("", "") == 0.0
        assert word_error_rate(None, None) == 0.0
        assert word_error_rate("one two", "") == 1.0
        assert word_error_rate("", "one two") == 1.0

    def test_wer_single_word_substitution(self):
        # 1 word edit over 4 words = 0.25
        wer = word_error_rate("mix flour and sugar", "mix flour and butter")
        assert pytest.approx(wer, 0.001) == 0.25

    def test_wer_word_deletion_and_insertion(self):
        # Deletion: 1 deletion over 4 words = 0.25
        wer_del = word_error_rate("bake in hot oven", "bake in oven")
        assert pytest.approx(wer_del, 0.001) == 0.25

        # Insertion: 1 insertion over 3 words = 0.3333
        wer_ins = word_error_rate("bake in oven", "bake in hot oven")
        assert pytest.approx(wer_ins, 0.001) == 1.0 / 3.0


# ============================================================================
# 4. RECIPE TRANSCRIPTION METRIC SUITE TESTS
# ============================================================================

class TestRecipeTranscriptionMetrics:
    """Tests for recipe-level metric calculations."""

    @pytest.fixture
    def sample_recipe(self) -> RecipeSchema:
        return RecipeSchema(
            recipe_id="rec_apple_crisp",
            title="Grandma's Apple Crisp",
            description="Traditional 1948 family dessert recipe.",
            prep_time="20 mins",
            cook_time="35 mins",
            total_time="55 mins",
            yield_servings="6 servings",
            ingredients=[
                "5 medium apples, sliced",
                "1/2 cup granulated sugar",
                "3/4 cup flour",
                "1/3 cup butter, cold",
                "1/4 tsp salt",
            ],
            directions=[
                "1. Preheat oven to 375°F and grease dish.",
                "2. Toss apples with sugar.",
                "3. Combine flour, butter, and salt until crumbly.",
                "4. Spread topping over apples and bake 35 mins.",
            ],
            tags=["Dessert", "Baking"],
            source_notes="1948 Handwritten Card",
        )

    def test_identical_recipe_metrics(self, sample_recipe: RecipeSchema):
        res = calculate_transcription_metrics(sample_recipe, sample_recipe)
        assert res.overall_cer == 0.0
        assert res.overall_wer == 0.0
        assert res.ingredient_cer == 0.0
        assert res.direction_cer == 0.0
        assert res.fraction_accuracy == 1.0
        assert res.direction_sequence_score == 1.0

    def test_partially_mutated_recipe_metrics(self, sample_recipe: RecipeSchema):
        mutated = sample_recipe.model_copy(deep=True)
        # Mutate one ingredient
        mutated.ingredients = [
            "5 medium apples, sliced",
            "1/2 cup brown sugar",  # was granulated sugar
            "3/4 cup flour",
            "1/3 cup butter, cold",
            "1/4 tsp salt",
        ]
        res = calculate_transcription_metrics(sample_recipe, mutated)
        assert res.overall_cer > 0.0
        assert res.ingredient_cer > 0.0
        assert res.direction_cer == 0.0  # directions unchanged
        assert res.fraction_accuracy == 1.0  # fractions unchanged

    def test_fraction_accuracy_calculation(self):
        ref_fracs = ["1/2", "3/4", "1/8"]
        hyp_fracs = ["1/2", "3/4", "1/4"]  # 1 mismatch

        acc = calculate_fraction_accuracy(ref_fracs, hyp_fracs)
        # TP = 2 ("1/2", "3/4"), FP = 1 ("1/4"), FN = 1 ("1/8")
        # P = 2/3, R = 2/3, F1 = 2/3
        assert pytest.approx(acc, 0.001) == 2.0 / 3.0

        # Empty cases
        assert calculate_fraction_accuracy([], []) == 1.0
        assert calculate_fraction_accuracy(["1/2"], []) == 0.0
        assert calculate_fraction_accuracy([], ["1/2"]) == 0.0

    def test_fraction_accuracy_hyphenated_mixed_number_matching(self):
        ref_fracs = extract_fractions("1 1/2 cups flour and 3/4 tsp salt")
        hyp_fracs = extract_fractions("1-1/2 cups flour and 3/4 tsp salt")
        assert calculate_fraction_accuracy(ref_fracs, hyp_fracs) == 1.0

    def test_direction_sequence_score_monotonicity(self):
        ref_dirs = ["1. Step one", "2. Step two", "3. Step three"]
        hyp_dirs = ["1. Step one", "2. Step two", "3. Step three"]
        assert calculate_direction_sequence_score(ref_dirs, hyp_dirs) == 1.0

        # Non-sequential numbers
        broken_dirs = ["1. Step one", "4. Step two", "5. Step three"]
        score_broken = calculate_direction_sequence_score(ref_dirs, broken_dirs)
        assert score_broken < 1.0

    def test_direction_sequence_score_various_numbering_prefixes(self):
        ref_dirs = ["1. Step one", "2. Step two", "3. Step three"]
        hyp_paren = ["(1) Step one", "(2) Step two", "(3) Step three"]
        hyp_step_dash = ["Step 1 - Step one", "Step 2 - Step two", "Step 3 - Step three"]
        hyp_colon = ["1: Step one", "2: Step two", "3: Step three"]
        assert calculate_direction_sequence_score(ref_dirs, hyp_paren) == 1.0
        assert calculate_direction_sequence_score(ref_dirs, hyp_step_dash) == 1.0
        assert calculate_direction_sequence_score(ref_dirs, hyp_colon) == 1.0

    def test_direction_sequence_score_unnumbered_steps(self):
        ref_dirs = ["Mix flour and sugar.", "Pour into greased baking pan.", "Bake 20 mins."]
        hyp_dirs = ["Mix flour and sugar.", "Pour into greased baking pan.", "Bake 20 mins."]
        assert calculate_direction_sequence_score(ref_dirs, hyp_dirs) == 1.0

    def test_recipe_metrics_null_and_empty_elements_tolerance(self, sample_recipe: RecipeSchema):
        # Mutate to include empty or whitespace strings
        mutated = sample_recipe.model_copy(deep=True)
        mutated.ingredients = ["1 cup flour", "", "2 tbsp sugar"]
        mutated.directions = ["1. First step", "2. Second step"]
        res = calculate_transcription_metrics(sample_recipe, mutated)
        assert isinstance(res, TranscriptionMetricResult)
        assert res.overall_cer >= 0.0


# ============================================================================
# 5. PAIRING METRICS TESTS
# ============================================================================

class TestPairingMetrics:
    """Tests for correlation proposal Precision, Recall, and F1 calculations."""

    def test_perfect_pairs_and_standalones(self):
        gt = [("card1_front.jpg", "card1_back.jpg"), ("card2_front.jpg", "card2_back.jpg"), ("card3_single.jpg", None)]
        pred = [("card1_front.jpg", "card1_back.jpg"), ("card2_front.jpg", "card2_back.jpg"), ("card3_single.jpg", None)]

        res = calculate_pairing_metrics(pred, gt)
        assert res.precision == 1.0
        assert res.recall == 1.0
        assert res.f1_score == 1.0
        assert res.true_positives == 3
        assert res.false_positives == 0
        assert res.false_negatives == 0

    def test_empty_predictions_and_ground_truth(self):
        res = calculate_pairing_metrics([], [])
        assert res.precision == 1.0
        assert res.recall == 1.0
        assert res.f1_score == 1.0

    def test_partial_match_with_fp_and_fn(self):
        gt = [
            ("c1_f.jpg", "c1_b.jpg"),
            ("c2_f.jpg", "c2_b.jpg"),
            ("c3_f.jpg", "c3_b.jpg"),
        ]
        # Predicted: c1 matched correctly, c2 matched with c3 (FP), c3 missed (FN)
        pred = [
            ("c1_f.jpg", "c1_b.jpg"),  # TP
            ("c2_f.jpg", "c3_b.jpg"),  # FP
        ]
        res = calculate_pairing_metrics(pred, gt)
        assert res.true_positives == 1
        assert res.false_positives == 1
        assert res.false_negatives == 2
        # P = 1 / 2 = 0.5, R = 1 / 3 = 0.3333, F1 = 2 * 0.5 * (1/3) / (0.5 + 1/3) = 0.4
        assert pytest.approx(res.precision, 0.001) == 0.5
        assert pytest.approx(res.recall, 0.001) == 1.0 / 3.0
        assert pytest.approx(res.f1_score, 0.001) == 0.4

    def test_correlation_proposal_objects_input(self):
        gt = [("c1_front", "c1_back"), ("c2_solo", None)]
        pred = [
            CorrelationProposal(
                proposal_id="p1",
                front_card_id="c1_front",
                back_card_id="c1_back",
                confidence=0.95,
                recommended_action="PAIR",
            ),
            CorrelationProposal(
                proposal_id="p2",
                front_card_id="c2_solo",
                back_card_id=None,
                is_standalone=True,
                confidence=0.90,
                recommended_action="STANDALONE",
            ),
        ]
        res = calculate_pairing_metrics(pred, gt)
        assert res.f1_score == 1.0
        assert res.true_positives == 2

    def test_multi_card_clusters(self):
        # 3-card recipe grouping cluster
        gt = [{"c1", "c2", "c3"}]
        pred = [{"c1", "c2", "c3"}]
        res = calculate_pairing_metrics(pred, gt)
        assert res.true_positives >= 1
        assert res.f1_score == 1.0
        assert res.multi_card_accuracy == 1.0


# ============================================================================
# 6. SCHEMA CONFORMITY VALIDATOR TESTS
# ============================================================================

class TestSchemaConformityValidator:
    """Tests for Plan To Eat and Schema.org RFC conformity checks."""

    def test_valid_recipe_passes_all_checks(self):
        recipe = RecipeSchema(
            recipe_id="rec_valid_001",
            title="Old Fashioned Sugar Cookies",
            description="Crispy vintage rolled sugar cookies.",
            prep_time="20 mins",
            cook_time="10 mins",
            total_time="30 mins",
            yield_servings="24 cookies",
            ingredients=["2 cups flour", "1 cup sugar", "1/2 cup butter", "1 tsp baking powder"],
            directions=[
                "1. Preheat oven to 375°F.",
                "2. Cream butter and sugar.",
                "3. Roll dough and cut shapes.",
                "4. Bake 10 mins until lightly golden.",
            ],
            tags=["Cookies", "Vintage"],
            source_notes="Grandma's Box 1952",
        )
        res = validate_schema_conformity(recipe)
        assert res.is_valid is True
        assert res.conformity_score == 1.0
        assert res.plan_to_eat_compliant is True
        assert res.schema_org_compliant is True
        assert res.non_empty_title is True
        assert res.non_empty_ingredients is True
        assert res.non_empty_directions is True
        assert res.sequential_directions is True
        assert res.iso8601_duration_valid is True
        assert res.fractions_clean is True
        assert len(res.failed_checks) == 0

    def test_empty_ingredients_fails(self):
        recipe = RecipeSchema(
            recipe_id="rec_bad_001",
            title="Empty Recipe",
            ingredients=[],
            directions=["1. Do something."],
        )
        res = validate_schema_conformity(recipe)
        assert res.is_valid is False
        assert "non_empty_ingredients" in res.failed_checks
        assert res.non_empty_ingredients is False

    def test_non_sequential_directions_fails(self):
        # We bypass pydantic validator to test raw schema conformity detection
        recipe = RecipeSchema.model_construct(
            recipe_id="rec_bad_seq",
            title="Out of order steps",
            ingredients=["1 cup flour"],
            directions=["1. First step", "3. Third step (skipped 2)"],
        )
        res = validate_schema_conformity(recipe)
        assert "sequential_directions" in res.failed_checks
        assert res.sequential_directions is False

    def test_invalid_duration_fails(self):
        recipe = RecipeSchema(
            recipe_id="rec_bad_time",
            title="Invalid Time Recipe",
            prep_time="unparseable gibberish time string XYZ",
            ingredients=["1 cup milk"],
            directions=["1. Drink milk."],
        )
        res = validate_schema_conformity(recipe)
        assert "iso8601_duration_valid" in res.failed_checks
        assert res.iso8601_duration_valid is False

    def test_corrupt_fraction_character_fails(self):
        recipe = RecipeSchema(
            recipe_id="rec_corrupt_frac",
            title="Corrupted Glyph Recipe",
            ingredients=["1 \ufffd cup flour"],
            directions=["1. Mix."],
        )
        res = validate_schema_conformity(recipe)
        assert "fractions_clean" in res.failed_checks
        assert res.fractions_clean is False

    def test_zero_duration_strings_conformity_valid(self):
        # Compound zero duration strings e.g. "0 hours 0 minutes", "0 hrs 0 mins"
        recipe = RecipeSchema(
            recipe_id="rec_zero_time",
            title="No-Bake Refrigerator Recipe",
            prep_time="15 mins",
            cook_time="0 hours 0 minutes",
            total_time="15 mins",
            ingredients=["1 cup oats", "1/2 cup peanut butter"],
            directions=["1. Mix ingredients.", "2. Chill in refrigerator."],
        )
        res = validate_schema_conformity(recipe)
        assert "iso8601_duration_valid" in res.passed_checks
        assert res.iso8601_duration_valid is True
