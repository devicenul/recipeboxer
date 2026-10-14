# WP-00d: Test Triage Checklist & Discovery Inventory

- **Work Package**: WP-00d
- **Plan Reference**: `_agents/remediation/REMEDIATION_PLAN.md` (§10 Test Triage Inventory, §4.1 Architectural Invariants)
- **Status**: Complete
- **Date**: 2026-10-14
- **Branch**: `remediation/phase0`
- **Artifacts Produced**: `evals/triage.yaml`, `_agents/remediation/WP-00d.md`

---

## 1. Executive Summary

Work Package **WP-00d** operationalizes Section 10 of the RecipeBoxer Remediation Plan into a structured, machine-readable triage manifest (`evals/triage.yaml`) and conducts an exhaustive independent audit of the test suite to discover all additional tests dependent on legacy evaluation cheats.

### Key Outcomes
1. **Section 10 Inventory Resolution**: All abbreviated test specifications from the plan were mapped to verified, exact `pytest` node IDs. Every single inventory entry was resolved against the 1,929 collected tests in the test suite (56 test node IDs + 2 fixture pseudo-nodes = 58 inventory entries).
2. **Independent Discovery of Additional Cheat Dependencies**: 123 additional tests were identified across the codebase that rely on legacy cheats (canned recipes, offline continuity heuristics, ObfuscatedCardCorpus harness leaks, mock judges, or `MockGeminiClient`/`synthetic_card_deck` fixtures).
3. **Unified Triage Manifest**: Produced `evals/triage.yaml` containing 181 validated entries with standardized schema `{nodeid, category, action, reason, owner_wp, status, source}` owned by `WP-C8`.

---

## 2. Triage Counts Summary

### 2.1 Counts by Source, Category, and Action

| Source | Category | Delete | Rewrite (Mock) | Move Live | Total |
|---|---|---|---|---|---|
| **Inventory** | Category A (Manifest / canned recipes / modality) | 12 | 15 | 1 | 28 |
| **Inventory** | Category B (Filename heuristics / fingerprint / continuity) | 4 | 13 | 2 | 19 |
| **Inventory** | Category C (Harness leaks / mock judge / thresholds) | 5 | 2 | 2 | 9 |
| **Inventory** | Fixtures (`MockGeminiClient`, `synthetic_card_deck`) | 0 | 2 | 0 | 2 |
| **Inventory Total** | *All Inventory* | **21** | **32** | **5** | **58** |
|---|---|---|---|---|---|
| **Discovered** | Category A (Manifest / canned recipes / modality) | 3 | 21 | 0 | 24 |
| **Discovered** | Category B (Filename heuristics / fingerprint / continuity) | 0 | 18 | 0 | 18 |
| **Discovered** | Category C (Harness leaks / mock judge / thresholds) | 21 | 2 | 0 | 23 |
| **Discovered** | Fixtures (Tests relying on conftest mock fixtures) | 0 | 58 | 0 | 58 |
| **Discovered Total** | *All Discovered* | **24** | **99** | **0** | **123** |
|---|---|---|---|---|---|
| **Combined Total** | Category A | 15 | 36 | 1 | 52 |
| **Combined Total** | Category B | 4 | 31 | 2 | 37 |
| **Combined Total** | Category C | 26 | 4 | 2 | 32 |
| **Combined Total** | Fixtures & Fixture-dependent | 0 | 60 | 0 | 60 |
| **Grand Total** | **All Categories** | **45** | **131** | **5** | **181** |

---

## 3. Inventory Resolution & Discrepancies

All entries listed in Section 10 of `REMEDIATION_PLAN.md` were successfully resolved to concrete pytest node IDs. No entries could not be resolved. The following minor discrepancies and expansions were clarified:

1. **`TestKeywordExclusivityAndCrossContamination::*` (2)** in `tests/test_challenger_2_r3_r4_stress.py`:
   - In the plan, this is noted as `test_challenger_2_r3_r4_stress.py::TestKeywordExclusivityAndCrossContamination::* (2)`.
   - In code, the class is named `TestCorpusStrictIsolation`, containing:
     - `tests/test_challenger_2_r3_r4_stress.py::TestCorpusStrictIsolation::test_rhubarb_pie_isolation_against_pecan_pie`
     - `tests/test_challenger_2_r3_r4_stress.py::TestCorpusStrictIsolation::test_magic_chocolate_pie_isolation_against_pecan_pie`
   - Both tests assert token exclusivity relying on canned recipe extraction.

2. **`TestInputRepresentationAndFallback::* (4)`** in `tests/test_challenger_2_r3_r4_stress.py`:
   - In the plan, 4 tests are allocated for deletion. The class in code contains 6 test methods.
   - The 4 tests that directly test manifest and byte cheats are mapped to the inventory deletions:
     - `test_resolve_via_visual_fingerprint_with_hash`
     - `test_resolve_via_scanned_card`
     - `test_resolve_via_raw_bytes`
     - `test_synthetic_keyword_exactness`
   - The remaining 2 methods (`test_resolve_via_pil_image` and `test_mystery_untitled_card_guess_tagging`) were triaged into the discovered list under Category A rewrite.

3. **`test_pair_order_invariance[...]` (6)** in `tests/test_challenger_r1_r2.py`:
   - In the plan, noted as `TestChallenger2CardPairsCorrelationR2::test_pair_order_invariance[...] (6)`.
   - Expanded to all 6 exact parametrized pytest node IDs.

4. **Fixture Pseudo-Nodes**:
   - `tests/conftest.py::MockGeminiClient` and `tests/conftest.py::synthetic_card_deck` were preserved as explicit `fixture` entries in `evals/triage.yaml` to track the WP-C8 fixture refactoring.

---

## 4. List of Discovered Extras with Justifications

A total of 123 additional tests were discovered through AST and source analysis across five cheat criteria:

### 4.1 Category A: Manifest & Extraction Fallback Cheats (24 tests)

| Action | Pytest Node ID | One-line Justification |
|---|---|---|
| `delete` | `tests/test_challenger_2_r3_r4_stress.py::TestCorpusStrictIsolation::test_all_12_corpus_recipes_fidelity` | Extracts all 12 corpus recipes offline asserting ground-truth fidelity |
| `delete` | `tests/test_challenger_2_r3_r4_stress.py::TestCorpusStrictIsolation::test_all_pairs_cross_contamination_matrix` | Extracts all 12 corpus recipes offline asserting token exclusivity matrix |
| `delete` | `tests/test_challenger_2_r3_r4_stress.py::TestParallelBatchConcurrency::test_concurrent_all_12_recipes_extraction` | Concurrently extracts all 12 corpus recipes offline asserting ground-truth fidelity |
| `rewrite_mock` | `tests/test_challenger_2_r3_r4_stress.py::TestInputRepresentationAndFallback::test_resolve_via_pil_image` | Calls extractor without client on PIL Image relying on generic offline fallback |
| `rewrite_mock` | `tests/test_challenger_2_r3_r4_stress.py::TestInputRepresentationAndFallback::test_mystery_untitled_card_guess_tagging` | Calls extractor without client relying on offline title guessing fallback |
| `rewrite_mock` | `tests/test_challenger_2_r3_r4_stress.py::TestMultiPartSequenceAssembly::test_synthetic_n_card_fallback_assembly[3]` | Calls extractor without client relying on offline N-card fallback assembly |
| `rewrite_mock` | `tests/test_challenger_2_r3_r4_stress.py::TestMultiPartSequenceAssembly::test_synthetic_n_card_fallback_assembly[4]` | Calls extractor without client relying on offline N-card fallback assembly |
| `rewrite_mock` | `tests/test_challenger_2_r3_r4_stress.py::TestMultiPartSequenceAssembly::test_synthetic_n_card_fallback_assembly[5]` | Calls extractor without client relying on offline N-card fallback assembly |
| `rewrite_mock` | `tests/test_challenger_2_r3_r4_stress.py::TestMultiPartSequenceAssembly::test_synthetic_n_card_fallback_assembly[6]` | Calls extractor without client relying on offline N-card fallback assembly |
| `rewrite_mock` | `tests/test_challenger_2_r3_r4_stress.py::TestMultiPartSequenceAssembly::test_synthetic_n_card_fallback_assembly[8]` | Calls extractor without client relying on offline N-card fallback assembly |
| `rewrite_mock` | `tests/test_challenger_2_sequence_exporters.py::test_multicard_transcription_sequence_preservation` | Calls extractor without client relying on offline multi-card sequence fallback |
| `rewrite_mock` | `tests/test_challenger_m3_extraction.py::TestOfflineExtractionTitleGuessing::test_untitled_or_unknown_card_appends_guess_suffix` | Calls extractor without client relying on offline title guessing fallback |
| `rewrite_mock` | `tests/test_challenger_m3_extraction.py::TestOfflineExtractionTitleGuessing::test_known_and_standard_cards_do_not_append_guess` | Calls extractor without client relying on offline standard card title fallback |
| `rewrite_mock` | `tests/test_challenger_m3_extraction.py::TestOfflineExtractionTitleGuessing::test_title_candidate_sanitization_strips_leading_numbers_and_punctuation` | Calls extractor without client relying on offline title sanitization fallback |
| `rewrite_mock` | `tests/test_challenger_m3_m4.py::TestRecipeExtractionEdgeCases::test_extractor_offline_boundary_text_fallback_handling` | Calls extractor without client relying on offline boundary text fallback |
| `rewrite_mock` | `tests/test_challenger_m3_m4.py::TestRecipeExtractionEdgeCases::test_extractor_batch_concurrency_stress` | Uses synthetic_card_deck and calls extractor without client under batch concurrency |
| `rewrite_mock` | `tests/test_challenger_m3_m4.py::TestMilestone3Milestone4Integration::test_fastapi_export_csv_endpoint_incomplete_pipeline_returns_400` | Uses synthetic_card_deck fixture in session export test |
| `rewrite_mock` | `tests/test_challenger_m3_m4.py::TestMilestone3Milestone4Integration::test_fastapi_export_csv_endpoint_invalid_session_returns_404` | Uses synthetic_card_deck fixture in session export test |
| `rewrite_mock` | `tests/test_recipe_extractor.py::test_extractor_injected_mock_client` | Relies on MockGeminiClient and synthetic_card_deck fixtures with semantic filenames |
| `rewrite_mock` | `tests/test_recipe_extractor.py::test_extractor_offline_generic_unseen_card` | Calls extractor without client on unseen card relying on offline title extraction |
| `rewrite_mock` | `tests/test_recipe_extractor.py::test_extractor_api_failure_fallback` | Relies on synthetic_card_deck and asserts fallback to canned chocolate fudge recipe |
| `rewrite_mock` | `tests/test_recipe_extractor.py::test_extractor_alias_method` | Calls extractor without client relying on offline canned recipe extraction |
| `rewrite_mock` | `tests/test_recipe_extractor.py::test_extract_cards_multi_card_sequence_offline` | Calls extractor without client on fruitcake corpus cards asserting canned recipe |
| `rewrite_mock` | `tests/test_recipe_extractor.py::test_offline_extraction_inferred_untitled_card_appends_guess` | Calls extractor without client relying on offline title guessing fallback |

### 4.2 Category B: Filename Heuristics & Continuity Fallbacks (18 tests)

| Action | Pytest Node ID | One-line Justification |
|---|---|---|
| `rewrite_mock` | `tests/test_challenger_m2.py::TestSemanticVerifierMockAndFallback::test_offline_fallback_strong_continuity_cues` | Calls GeminiSemanticVerifier without client relying on offline continuity cue fallback |
| `rewrite_mock` | `tests/test_challenger_m2.py::TestSemanticVerifierMockAndFallback::test_offline_fallback_completely_unrelated_cards` | Calls GeminiSemanticVerifier without client relying on offline rejection fallback |
| `rewrite_mock` | `tests/test_challenger_m2.py::TestSemanticVerifierMockAndFallback::test_offline_fallback_moderate_heuristic_uncertain_action` | Calls GeminiSemanticVerifier without client relying on offline heuristic threshold fallback |
| `rewrite_mock` | `tests/test_challenger_m2.py::TestSemanticVerifierMockAndFallback::test_none_or_empty_boundary_text_graceful_handling` | Calls GeminiSemanticVerifier without client relying on offline boundary fallback |
| `rewrite_mock` | `tests/test_correlation.py::TestGeminiVisualExtractor::test_extractor_with_mock_client` | Relies on MockGeminiClient filename-based role classification ('test_front.jpg') |
| `rewrite_mock` | `tests/test_correlation.py::TestGeminiVisualExtractor::test_extractor_corrupted_image_raises_error` | Calls GeminiVisualExtractor without client relying on offline byte check |
| `rewrite_mock` | `tests/test_correlation.py::TestGeminiVisualExtractor::test_extractor_scanned_card_input` | Calls GeminiVisualExtractor without client relying on offline pixel analysis |
| `rewrite_mock` | `tests/test_correlation.py::TestGeminiVisualExtractor::test_extractor_sync_interface` | Calls GeminiVisualExtractor sync interface without client |
| `rewrite_mock` | `tests/test_correlation.py::TestGeminiSemanticVerifier::test_semantic_verifier_with_mock_client` | Relies on MockGeminiClient filename keyword matching ('01_cinnamon_rolls_front.jpg') |
| `rewrite_mock` | `tests/test_correlation.py::TestGeminiSemanticVerifier::test_semantic_verifier_custom_weights` | Calls GeminiSemanticVerifier without client on lemon bars filenames asserting confidence |
| `rewrite_mock` | `tests/test_correlation.py::TestGeminiSemanticVerifier::test_semantic_verifier_offline_continuity_cues` | Calls GeminiSemanticVerifier without client relying on offline continuity cue fallback |
| `rewrite_mock` | `tests/test_correlation.py::TestGeminiSemanticVerifier::test_semantic_verifier_mismatched_pair_rejected` | Calls GeminiSemanticVerifier without client relying on offline pair rejection |
| `rewrite_mock` | `tests/test_correlation.py::TestPairingOptimizer::test_end_to_end_synthetic_deck_correlation` | Relies on MockGeminiClient and synthetic_card_deck with filename-pattern matching |
| `rewrite_mock` | `tests/test_challenger_1_full_empirical.py::TestDirectedSequenceCorrelationEngine::test_corpus_thanksgiving_turkey_and_fruitcake_sets` | Uses sample_card_corpus multi-card sets with ground-truth title in optimizer |
| `rewrite_mock` | `tests/test_challenger_m2_empirical_verification.py::TestM2MultiCardSequenceChainingPermutations::test_pot_roast_3card_all_permutations_reconstruct_natural_order` | Uses _evaluate_offline_continuity on sample_cards pot roast set |
| `rewrite_mock` | `tests/test_challenger_m2_empirical_verification.py::TestM2MultiCardSequenceChainingPermutations::test_panettone_4card_all_permutations_reconstruct_natural_order` | Uses _evaluate_offline_continuity on sample_cards panettone set |
| `rewrite_mock` | `tests/test_challenger_m2_empirical_rigor.py::test_3card_sunday_pot_roast_sequence_chain` | Verifies sample_cards cluster roles and continuation cues against corpus |
| `rewrite_mock` | `tests/test_challenger_m2_empirical_rigor.py::test_4card_traditional_panettone_sequence_chain` | Verifies sample_cards cluster roles and continuation cues against corpus |

### 4.3 Category C: Harness Leaks, Obfuscation Registry & Mock Judge (23 tests)

| Action | Pytest Node ID | One-line Justification |
|---|---|---|
| `rewrite_mock` | `tests/test_challenger_m2_prompt_leak.py::TestCorpusManifestPromptLeaks::test_all_48_cards_extraction_payload_leak_free` | Uses sample_cards corpus with ObfuscatedCardCorpus |
| `rewrite_mock` | `tests/test_challenger_m2_prompt_leak.py::TestCorpusManifestPromptLeaks::test_multi_card_pairs_extraction_payload_leak_free` | Uses sample_cards corpus with ObfuscatedCardCorpus |
| `delete` | `tests/test_challenger_e2e_cross_feature.py::TestClippingFixturesAndMultiCardSequences::test_multicard_sets_3_and_4_parts_sequence_pairing` | Relies on ObfuscatedCardCorpus.map_proposals_to_ground_truth removed in WP-C6 |
| `delete` | `tests/test_eval_obfuscation.py::TestObfuscatedCardCorpus::test_map_proposals_to_ground_truth` | Tests map_proposals_to_ground_truth harness leak removed in WP-C6 |
| `delete` | `tests/test_eval_obfuscation.py::TestObfuscatedCardCorpus::test_map_correlation_result` | Tests map_correlation_result harness leak removed in WP-C6 |
| `delete` | `tests/test_challenger_m2_obfuscation_deep.py::TestReverseMappingAndProposals::test_proposal_translation_preserves_all_metadata` | Tests map_proposals_to_ground_truth preserving GT metadata removed in WP-C6 |
| `delete` | `tests/test_challenger_m2_obfuscation_deep.py::TestReverseMappingAndProposals::test_dict_and_tuple_proposal_translation` | Tests map_proposals_to_ground_truth proposal translation removed in WP-C6 |
| `delete` | `tests/test_challenger_m2_obfuscation_deep.py::TestReverseMappingAndProposals::test_unregistered_id_fallback` | Tests resolve_active_obfuscation_id fallback removed in WP-C6 |
| `delete` | `tests/test_challenger_m2_obfuscation_deep.py::TestDiverseCardCategories::test_single_standalone_cards` | Tests ObfuscatedCardCorpus passing GT metadata for single_standalone_cards removed in WP-C6 |
| `delete` | `tests/test_challenger_m2_obfuscation_deep.py::TestDiverseCardCategories::test_multi_card_sets` | Tests ObfuscatedCardCorpus passing GT metadata for multi_card_sets removed in WP-C6 |
| `delete` | `tests/test_challenger_m2_obfuscation_deep.py::TestDiverseCardCategories::test_damaged_cards_category_by_mixed_media_modality` | Tests ObfuscatedCardCorpus passing GT metadata for damaged_cards_category_by_mixed_media_modality removed in WP-C6 |
| `delete` | `tests/test_challenger_m2_obfuscation_deep.py::TestDiverseCardCategories::test_historical_clippings_category` | Tests ObfuscatedCardCorpus passing GT metadata for historical_clippings_category removed in WP-C6 |
| `delete` | `tests/test_challenger_m2_obfuscation_deep.py::TestDiverseCardCategories::test_typewritten_category` | Tests ObfuscatedCardCorpus passing GT metadata for typewritten_category removed in WP-C6 |
| `delete` | `tests/test_challenger_m2_obfuscation_deep.py::TestDiverseCardCategories::test_handwritten_cursive_category` | Tests ObfuscatedCardCorpus passing GT metadata for handwritten_cursive_category removed in WP-C6 |
| `delete` | `tests/test_challenger_m2_obfuscation_deep.py::TestDiverseCardCategories::test_full_corpus_roundtrip` | Tests ObfuscatedCardCorpus passing GT metadata for full_corpus_roundtrip removed in WP-C6 |
| `delete` | `tests/test_eval_runner.py::TestRecipeJudgeDeterministicMock::test_mock_judge_high_fidelity` | Tests RecipeJudge deterministic mock removed per WP-C6 and WP-E6 |
| `delete` | `tests/test_eval_runner.py::TestRecipeJudgeDeterministicMock::test_mock_judge_catches_missing_ingredients_in_directions` | Tests RecipeJudge deterministic mock removed per WP-C6 and WP-E6 |
| `delete` | `tests/test_eval_runner.py::TestRecipeJudgeDeterministicMock::test_mock_judge_catches_missing_headnotes` | Tests RecipeJudge deterministic mock removed per WP-C6 and WP-E6 |
| `delete` | `tests/test_eval_runner.py::TestRecipeJudgeDeterministicMock::test_mock_judge_step_numbering_prefixes` | Tests RecipeJudge deterministic mock removed per WP-C6 and WP-E6 |
| `delete` | `tests/test_eval_runner.py::TestRecipeJudgeDeterministicMock::test_mock_judge_bracketed_and_prefixed_steps` | Tests RecipeJudge deterministic mock removed per WP-C6 and WP-E6 |
| `delete` | `tests/test_eval_runner.py::TestRecipeJudgeDeterministicMock::test_mock_judge_singular_plural_morphology` | Tests RecipeJudge deterministic mock removed per WP-C6 and WP-E6 |
| `delete` | `tests/test_eval_runner.py::TestRecipeJudgeDeterministicMock::test_judge_async_and_batch_evaluation` | Tests RecipeJudge deterministic mock removed per WP-C6 and WP-E6 |
| `delete` | `tests/test_eval_runner.py::TestRecipeJudgeLiveApiAndRetries::test_judge_fallback_to_mock_on_network_failure` | Tests fallback to mock judge removed per WP-C6 and WP-E6 |

### 4.4 Fixture: Tests Dependent on Conftest Mock Fixtures (58 tests)

| Action | Pytest Node ID | One-line Justification |
|---|---|---|
| `rewrite_mock` | `tests/e2e/test_e2e_pipeline.py::TestTier1FeatureCoverage::test_f3_parallel_ingestion_semaphore` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/e2e/test_e2e_pipeline.py::TestTier1FeatureCoverage::test_f4_slow_batch_ingestion_intervals` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/e2e/test_e2e_pipeline.py::TestTier1FeatureCoverage::test_f6_visual_fingerprinting_attributes` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/e2e/test_e2e_pipeline.py::TestTier1FeatureCoverage::test_f8_semantic_continuity_verification` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/e2e/test_e2e_pipeline.py::TestTier1FeatureCoverage::test_f10_hitl_async_event_pause_resume` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/e2e/test_e2e_pipeline.py::TestTier1FeatureCoverage::test_f11_fastapi_pipeline_start_and_status` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/e2e/test_e2e_pipeline.py::TestTier1FeatureCoverage::test_f12_fastapi_hitl_pending_and_approve` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/e2e/test_e2e_pipeline.py::TestTier1FeatureCoverage::test_f13_recipe_extraction_schema_adherence` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/e2e/test_e2e_pipeline.py::TestTier1FeatureCoverage::test_f15_fastapi_export_csv_download` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/e2e/test_e2e_pipeline.py::TestTier2BoundaryAndCornerCases::test_t2_corrupted_image_handling_graceful` | Relies on MockGeminiClient fixture |
| `rewrite_mock` | `tests/e2e/test_e2e_pipeline.py::TestTier2BoundaryAndCornerCases::test_t2_missing_card_back_unassigned` | Relies on MockGeminiClient fixture |
| `rewrite_mock` | `tests/e2e/test_e2e_pipeline.py::TestTier3CrossFeatureInteractions::test_t3_parallel_ingestion_with_hitl_override_and_csv_export` | Relies on synthetic_card_deck fixture |
| `rewrite_mock` | `tests/e2e/test_e2e_pipeline.py::TestTier3CrossFeatureInteractions::test_t3_slow_batch_with_sqlite_cache_hit_and_hitl_rejection` | Relies on synthetic_card_deck fixture |
| `rewrite_mock` | `tests/e2e/test_e2e_pipeline.py::TestTier4RealWorldScenarios::test_t4_scenario2_mixed_batch_ingestion` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/e2e/test_e2e_pipeline.py::TestTier4RealWorldScenarios::test_t4_scenario3_rate_limited_slow_batch_resumption` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/e2e/test_e2e_pipeline.py::TestTier1And2DeepVerification::test_f3_parallel_high_concurrency_stress` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/e2e/test_e2e_pipeline.py::TestTier1And2DeepVerification::test_f4_slow_batch_zero_interval_fast_path` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/e2e/test_e2e_pipeline.py::TestTier1And2DeepVerification::test_f8_semantic_correlation_unrelated_recipes_low_confidence` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/e2e/test_e2e_pipeline.py::TestTier1And2DeepVerification::test_f10_hitl_bridge_cancellation_unblocks_cleanly` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/e2e/test_e2e_pipeline.py::TestTier1And2DeepVerification::test_f11_fastapi_status_elapsed_time_and_token_accounting` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/e2e/test_e2e_pipeline.py::TestTier1And2DeepVerification::test_f15_fastapi_export_before_completed_returns_400` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/test_e2e_4tier_suite.py::TestTier1FeatureCoverage::test_t1_f03_03_sse_state_change_event_broadcast` | Relies on synthetic_card_deck fixture |
| `rewrite_mock` | `tests/test_e2e_4tier_suite.py::TestTier1FeatureCoverage::test_t1_f05_01_start_pipeline_parallel_mode_configuration` | Relies on synthetic_card_deck fixture |
| `rewrite_mock` | `tests/test_e2e_4tier_suite.py::TestTier1FeatureCoverage::test_t1_f05_04_slow_batch_mode_configuration_parameters` | Relies on synthetic_card_deck fixture |
| `rewrite_mock` | `tests/test_e2e_4tier_suite.py::TestTier1FeatureCoverage::test_t1_f06_01_card_image_streaming_mime_type_and_caching` | Relies on synthetic_card_deck fixture |
| `rewrite_mock` | `tests/test_e2e_4tier_suite.py::TestTier1FeatureCoverage::test_t1_f06_02_card_image_streaming_cache_headers_present` | Relies on synthetic_card_deck fixture |
| `rewrite_mock` | `tests/test_e2e_4tier_suite.py::TestTier1FeatureCoverage::test_t1_f06_05_card_image_streaming_with_session_id_query` | Relies on synthetic_card_deck fixture |
| `rewrite_mock` | `tests/test_e2e_4tier_suite.py::TestTier1FeatureCoverage::test_t1_f08_01_get_candidates_returns_ranked_alternatives` | Relies on synthetic_card_deck fixture |
| `rewrite_mock` | `tests/test_e2e_4tier_suite.py::TestTier1FeatureCoverage::test_t1_f08_02_get_candidates_includes_score_breakdown` | Relies on synthetic_card_deck fixture |
| `rewrite_mock` | `tests/test_e2e_4tier_suite.py::TestTier1FeatureCoverage::test_t1_f15_01_pipeline_start_and_status_tracking` | Relies on synthetic_card_deck fixture |
| `rewrite_mock` | `tests/test_e2e_4tier_suite.py::TestTier2BoundaryAndCornerCases::test_t2_f05_01_start_pipeline_zero_or_negative_concurrency_rejected` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/test_e2e_4tier_suite.py::TestTier2BoundaryAndCornerCases::test_t2_f05_02_start_pipeline_extreme_concurrency_limit` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/test_e2e_4tier_suite.py::TestTier2BoundaryAndCornerCases::test_t2_f05_03_start_pipeline_slow_batch_zero_interval_sequential` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/test_e2e_4tier_suite.py::TestTier2BoundaryAndCornerCases::test_t2_f15_01_pipeline_corrupted_image_skip_and_continue` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/test_e2e_4tier_suite.py::TestTier2BoundaryAndCornerCases::test_t2_f15_02_pipeline_all_standalone_cards_deck` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/test_e2e_4tier_suite.py::TestTier2BoundaryAndCornerCases::test_t2_f15_03_pipeline_unmatched_odd_card_in_unassigned` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/test_e2e_4tier_suite.py::TestTier4RealWorldApplicationScenarios::test_t4_s1_vintage_heirloom_index_cards_with_faded_cursive` | Relies on MockGeminiClient fixture |
| `rewrite_mock` | `tests/test_empirical_challenger.py::TestEmpiricalChallengerM3M4::test_concurrent_pipeline_start_requests` | Relies on synthetic_card_deck fixture |
| `rewrite_mock` | `tests/test_empirical_challenger.py::TestEmpiricalChallengerM3M4::test_concurrent_pipeline_start_with_invalid_mix` | Relies on synthetic_card_deck fixture |
| `rewrite_mock` | `tests/test_empirical_challenger.py::TestEmpiricalChallengerM3M4::test_hitl_unblock_and_complete_downstream_extraction` | Relies on synthetic_card_deck fixture |
| `rewrite_mock` | `tests/test_empirical_challenger.py::TestEmpiricalChallengerM3M4::test_hitl_rejection_and_override_handling` | Relies on synthetic_card_deck fixture |
| `rewrite_mock` | `tests/test_empirical_challenger.py::TestEmpiricalChallengerM3M4::test_rapid_double_approval_race_condition` | Relies on synthetic_card_deck fixture |
| `rewrite_mock` | `tests/test_empirical_challenger.py::TestEmpiricalChallengerM3M4::test_session_cancellation_during_hitl_pending` | Relies on synthetic_card_deck fixture |
| `rewrite_mock` | `tests/test_empirical_challenger.py::TestEmpiricalChallengerM3M4::test_full_pipeline_slow_batch_mode_via_api` | Relies on synthetic_card_deck fixture |
| `rewrite_mock` | `tests/test_empirical_challenger.py::TestEmpiricalChallengerM3M4::test_empty_approvals_with_rejected_ids_clean_completion` | Relies on synthetic_card_deck fixture |
| `rewrite_mock` | `tests/test_empirical_challenger_m2_multicard.py::TestPipelineSessionMultiCardOrchestration::test_session_multi_card_4_cards_approval_with_batch_tags` | Relies on MockGeminiClient and synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/test_empirical_challenger_m2_multicard.py::TestPipelineSessionMultiCardOrchestration::test_session_multi_card_reversal_fallback_when_card_ids_empty` | Relies on MockGeminiClient and synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/test_empirical_challenger_m2_multicard.py::TestPipelineSessionMultiCardOrchestration::test_session_mixed_multi_card_standalone_and_rejection_batch` | Relies on MockGeminiClient and synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/test_fastapi_routes.py::TestFastAPIRoutes::test_start_pipeline_directory_path_alias` | Relies on synthetic_card_deck fixture |
| `rewrite_mock` | `tests/test_fastapi_routes.py::TestFastAPIRoutes::test_status_endpoint_with_explicit_session_id` | Relies on synthetic_card_deck fixture |
| `rewrite_mock` | `tests/test_fastapi_routes.py::TestFastAPIRoutes::test_full_pipeline_api_lifecycle` | Relies on synthetic_card_deck fixture |
| `rewrite_mock` | `tests/test_hitl_bridge.py::test_multi_card_hitl_approval_sequence_preservation` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/test_hitl_bridge.py::test_incremental_recipe_extraction_progress` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/test_hitl_bridge.py::TestHITLBridge::test_session_state_transitions_pause_and_resume` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/test_hitl_bridge.py::TestHITLBridge::test_session_cancellation_at_hitl_pending` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/test_hitl_bridge.py::TestHITLBridge::test_session_cancellation_during_ingestion` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/test_hitl_bridge.py::TestHITLBridge::test_hitl_rejections_and_overrides` | Relies on MockGeminiClient or synthetic_card_deck fixtures |
| `rewrite_mock` | `tests/test_plan_to_eat_csv.py::test_csv_synthetic_deck_export` | Relies on synthetic_card_deck fixture |

---

## 5. Verification & Anti-Cheat Invariant Compliance

1. **Pytest Node ID Validation**:
   - Every single test node ID in `evals/triage.yaml` was collected and verified against `pytest --collect-only -q` using the project virtualenv.
   - Verified 1,929 total collected test node IDs; exactly 179 test node IDs + 2 fixture pseudo-nodes are cataloged in `evals/triage.yaml`.
2. **Read-Only Codebase Invariant**:
   - Zero production files under `recipeboxer/` were modified.
   - Zero existing test files under `tests/` were modified.
   - No git commands (`git add`, `git commit`, `git checkout`, `git stash`) were executed.
   - `setup.sh` was unmodified.
3. **Next Steps for WP-C8**:
   - In Phase 2, WP-C8 will consume `evals/triage.yaml` to execute the planned actions (45 deletions, 131 mock rewrites, and 5 moves to live eval).
