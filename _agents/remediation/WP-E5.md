# Work Package Completion Note: WP-E5 (Metrics API)

## 1. Summary

Implemented WP-E5 providing the authoritative evaluation metric suite under `evals/metrics/**` in strict compliance with `_agents/remediation/WAVE3_CONTRACTS.md` (§5) and `REMEDIATION_PLAN.md` (§6, §9.1):
- **Grouping (`evals/metrics/grouping.py`)**: Hungarian recipe matching by card-overlap IoU (`match_recipes`), B-cubed Precision, Recall, and F1, Adjusted Rand Index (`adjusted_rand_index`, implemented directly without sklearn), exact-cluster accuracy, Kendall's tau face order correlation within matched multi-card clusters, standalone card identification accuracy (F1), and false-merge / false-split rates.
- **Extraction (`evals/metrics/extraction.py`)**: `score_recipe` measuring title CER, line-level ingredient P/R/F1 via Hungarian matching with CER <= 0.1 threshold, quantity/unit exact matching with unit synonyms, direction F1 + Kendall tau step order, per-field CER, hallucination rate, omission rate, and `[illegible]` marker precision. Plus `aggregate(rows, by=[...])` for computing overall means and per-group breakdowns (deck_type, modality, faces) with explicit sample counts (`n`) and exclusion of None / N-A values.
- **Calibration (`evals/metrics/calibration.py`)**: Expected Calibration Error (`ece`) across probability bins.
- **Ops (`evals/metrics/ops.py`)**: Execution ledger summarization (`summarize_ledger`) aggregating input, output, and thinking tokens, latency, retry attempts, failures per stage, and USD cost using an optional pricing table.
- **Text & Unit Helpers (`evals/metrics/text.py`)**: Exact Levenshtein distance, normalized CER and WER with unicode NFKC normalization, vulgar fraction standardization, whitespace collapse, case-sensitivity toggle, and unit synonym canonicalization.
- **Zero dependencies on `recipeboxer`**: All functions in `evals/metrics/**` are pure, deterministic, and free of any imports from `recipeboxer`.

---

## 2. Files Changed

- `evals/metrics/__init__.py`: Package init exposing contract §5 API functions.
- `evals/metrics/text.py`: Levenshtein distance, CER/WER, text normalization, fractions, unit synonyms.
- `evals/metrics/grouping.py`: `score_grouping`, `match_recipes`, B-cubed, direct ARI, exact cluster accuracy, face Kendall tau, standalone accuracy, false merge/split rates.
- `evals/metrics/extraction.py`: `score_recipe`, `aggregate`, line-level Hungarian matching, unit/quantity matching, illegible precision.
- `evals/metrics/calibration.py`: `ece` Expected Calibration Error.
- `evals/metrics/ops.py`: `summarize_ledger` with token, latency, retry, failure, and USD cost tracking.
- `tests/test_eval_metrics.py`: Rewritten to test the new `evals.metrics` package.
- `tests/test_wp_e5_edge_cases.py`: Thorough edge-case test suite covering empty inputs, extreme clustering, permutation invariance, fractions, and calibration boundaries.
- `_agents/remediation/WP-E5.md`: This completion note.

---

## 3. Per-Test Outcomes for Rewritten `tests/test_eval_metrics.py`

| Test / Class | Status | Description / Outcome |
|---|---|---|
| `TestLevenshteinDistance::test_identical_sequences` | PASSED | Retained & adapted; tests zero distance on identical strings/lists. |
| `TestLevenshteinDistance::test_empty_sequence_boundaries` | PASSED | Retained & adapted; tests boundary distance against empty inputs. |
| `TestLevenshteinDistance::test_single_edits` | PASSED | Retained & adapted; tests single insertion, deletion, and substitution. |
| `TestLevenshteinDistance::test_transpositions_and_longer_edits` | PASSED | Retained & adapted; tests multi-character edits. |
| `TestLevenshteinDistance::test_asymmetric_sequence_lengths` | PASSED | Retained & adapted; tests sequence order asymmetry with O(min(N, M)) space. |
| `TestTextNormalization::test_unicode_vulgar_fractions_conversion` | PASSED | Retained & adapted; converts unicode fractions ('½', '¼', '¾', '⅛') to ASCII. |
| `TestTextNormalization::test_all_standard_vulgar_fractions` | PASSED | Retained & adapted; tests all 14 standard unicode vulgar fractions. |
| `TestTextNormalization::test_smart_quotes_dashes_degrees` | PASSED | Retained & adapted; tests quotes, dashes, and degree symbols. |
| `TestTextNormalization::test_whitespace_collapsing` | PASSED | Retained & adapted; tests multiple whitespace and newline collapse. |
| `TestTextNormalization::test_case_and_punctuation_preservation_options` | PASSED | Retained & adapted; tests case_sensitive and preserve_punctuation parameters. |
| `TestTextNormalization::test_unicode_nfkc_normalization` | PASSED | New; tests NFKC normalization of ligatures ('ﬁ' -> 'fi'). |
| `TestTextNormalization::test_extract_fractions` | PASSED | Retained & adapted; extracts fractions and mixed numbers. |
| `TestTextNormalization::test_extract_fractions_hyphenated_and_slashes` | PASSED | Retained & adapted; tests U+2044 and U+2215 fraction slashes. |
| `TestErrorRates::test_cer_exact_match` | PASSED | Retained & adapted; tests CER exact match and unicode fraction equivalence. |
| `TestErrorRates::test_cer_empty_strings` | PASSED | Retained & adapted; tests empty string edge cases for CER. |
| `TestErrorRates::test_cer_single_character_corruption` | PASSED | Retained & adapted; tests single char error rate calculation. |
| `TestErrorRates::test_cer_case_sensitivity_option` | PASSED | New; tests case-sensitive CER toggle. |
| `TestErrorRates::test_wer_exact_match` | PASSED | Retained & adapted; tests WER exact match. |
| `TestErrorRates::test_wer_empty_strings` | PASSED | Retained & adapted; tests empty string edge cases for WER. |
| `TestUnitAndQuantityNormalization::test_unit_synonyms` | PASSED | New; tests canonicalization of c/cup/cups, tsp/t/teaspoon, tbsp/T/tablespoon, lb/pound, oz. |
| `TestUnitAndQuantityNormalization::test_quantities_match` | PASSED | New; tests semantic quantity equivalence ("1 1/2" == "1.5" == "3/2" == "1-1/2"). |
| `TestGroupingMetrics::test_match_recipes_perfect_overlap` | PASSED | New; tests Hungarian bipartite matching with 1.0 IoU overlap. |
| `TestGroupingMetrics::test_match_recipes_unmatched_pred_and_gold` | PASSED | New; tests Hungarian unmatched outputs `(i, None, 0.0)` and `(None, j, 0.0)`. |
| `TestGroupingMetrics::test_score_grouping_identical` | PASSED | New; tests B-cubed, ARI, exact cluster, tau, standalone, false merge/split on identical partitions. |
| `TestGroupingMetrics::test_score_grouping_face_order_kendall_tau` | PASSED | New; tests face order Kendall tau on reversed card reading order. |
| `TestGroupingMetrics::test_score_grouping_false_merge_and_split` | PASSED | New; tests false-merge rate on over-clustering and false-split rate on under-clustering. |
| `TestExtractionMetrics::test_identical_recipe_scores` | PASSED | New; tests `score_recipe` on identical pred and gold structured recipes. |
| `TestExtractionMetrics::test_unit_synonym_matching_in_extraction` | PASSED | New; tests line Hungarian matching (CER <= 0.1) and unit synonym exact matching. |
| `TestExtractionMetrics::test_omitted_recipe_scores` | PASSED | New; tests `score_recipe(None, gold)` handling omitted recipes. |
| `TestExtractionMetrics::test_hallucination_rate` | PASSED | New; tests hallucination rate when extra lines are predicted. |
| `TestExtractionMetrics::test_illegible_marker_precision` | PASSED | New; tests precision of `[illegible]` markers against gold. |
| `TestCalibrationAndOps::test_ece_perfect_calibration` | PASSED | New; tests ECE on well-calibrated predictions. |
| `TestCalibrationAndOps::test_ece_complete_miscalibration` | PASSED | New; tests ECE on completely miscalibrated predictions (1.0 conf, 0.0 acc). |
| `TestCalibrationAndOps::test_summarize_ledger_with_pricing` | PASSED | New; tests token summarization, retry counting, and USD cost via pricing table. |
| `TestAggregation::test_aggregate_means_and_breakdowns` | PASSED | New; tests `aggregate` across overall means and group breakdowns with explicit n. |

### Deleted Tests from Previous `test_eval_metrics.py`:
- `TestRecipeTranscriptionMetrics` (5 tests): Deleted; tested deprecated `RecipeSchema`-coupled `calculate_transcription_metrics`. Replaced by `score_recipe`.
- `TestPairingMetrics` (5 tests): Deleted; tested deprecated `CorrelationProposal`-coupled `calculate_pairing_metrics`. Replaced by `score_grouping`.
- `TestSchemaConformityValidator` (6 tests): Deleted; tested PlanToEat and Schema.org RFC compliance validators which belong in exporter tests rather than the eval metrics suite.

---

## 4. Edge-Case Coverage (`tests/test_wp_e5_edge_cases.py`)

- `test_both_empty`: Completely empty pred and gold groupings return baseline 1.0/0.0 values.
- `test_empty_pred_non_empty_gold`: Omitted predictions yield 0.0 accuracy, 1.0 false split rate, and unmatched gold tuples.
- `test_non_empty_pred_empty_gold`: Hallucinated predictions yield 0.0 accuracy and unmatched pred tuples.
- `test_all_singletons_predicted`: Extreme under-clustering yields B-cubed Precision=1.0, Recall=0.5, false split=1.0, false merge=0.0.
- `test_one_giant_cluster_predicted`: Extreme over-clustering yields B-cubed Precision=0.5, Recall=1.0, false merge=1.0, false split=0.0.
- `test_permutation_invariance_of_cluster_order`: Invariance under cluster reordering.
- `test_permutation_invariance_of_cluster_order_and_items`: Invariance under transposed cluster and item representations.
- `test_kendall_tau_face_order_subsets`: Tau scores for complete reversal (-1.0), partial reversal (1/3), and singletons (1.0).
- `test_standalone_accuracy_boundary_cases`: Decks with 0 standalones, false positive standalones, and missed standalones.
- `test_unicode_fractions_and_mixed_numbers`: Fraction extraction across vulgar glyphs, hyphens, and U+2044 / U+2215 slashes.
- `test_quantities_match_edge_cases`: None, empty, decimal vs vulgar fractions, and unparseable strings.
- `test_normalize_unit_synonyms_exhaustive`: Complete coverage of all volume, weight, and count unit synonyms.
- `test_cer_and_wer_extremes`: Case insensitivity, unicode NFKC ligatures, and empty string bounds.
- `test_score_recipe_both_empty`: Empty recipe dicts return clean baseline scores.
- `test_score_recipe_ingredients_reversed`: Line Hungarian matching pairs lines regardless of list ordering.
- `test_direction_reversed_order_tau`: Reversed direction steps produce tau = -1.0.
- `test_100_percent_hallucination_and_omission`: Disjoint recipe content produces 1.0 hallucination and 1.0 omission rates.
- `test_illegible_marker_precision_variations`: Correct markers vs false markers on legible text.
- `test_ece_edge_cases`: Empty lists, boundary confidences, and validation exceptions.
- `test_summarize_ledger_empty`: Empty ledger dict handled safely.
- `test_summarize_ledger_failures_only`: Ledgers containing only failures tracked by stage.
- `test_aggregate_empty_rows`: Empty dataset returns empty summary structure.
- `test_aggregate_all_none_and_na`: All None and "N/A" values excluded with `n=0` and `mean=None`.
- `test_aggregate_nested_fields`: Nested `field_cer` dictionaries aggregated with per-field n.

---

## 5. Commands and Results

1. **Target test suite execution:**
   ```bash
   PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 60 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_eval_metrics.py tests/test_wp_e5_edge_cases.py -q -p no:randomly
   ```
   **Output:** `59 passed in 0.55s`

2. **Anti-cheat static invariants check:**
   ```bash
   PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 60 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/anticheat/test_static_invariants.py -q -p no:randomly
   ```
   **Output:** `4 passed in 1.51s`

3. **Dependency check:**
   Verified that `evals/metrics/**` contains zero imports of `recipeboxer`.

---

## 6. Contract Deviations

None. All function signatures, parameters, and return dictionary structures strictly conform to `WAVE3_CONTRACTS.md` §5.

---

## 7. Requests for Other WPs / Risks

- **Requests**: None.
- **Risks**: None. All metric calculations are pure, deterministic, and execute in sub-second time.
