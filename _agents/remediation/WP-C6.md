# Work Package Completion Report: WP-C6 (Interim Harness Fixes & Anti-Cheating)

**Package Owner:** WP-C6  
**Branch:** `remediation/phase0`  
**Status:** Completed & Verified  

---

## 1. Summary of Remediation

In accordance with `_agents/remediation/REMEDIATION_PLAN.md` (§2, §3, §4.1, §6, §10), `_agents/INVARIANTS.md` (§10, INV-AC1..INV-AC6), and `_agents/TESTING_GUIDELINES.md` (§7), WP-C6 remediated the evaluation harness and obfuscation pipeline to close answer-key leakage backdoors, enforce black-box inputs to the System Under Test (SUT), eliminate dependency on generator modules, disable mock judging by default, and guarantee honest reporting under missing keys or pipeline stage failures.

### Key Changes Implemented:

1. **Obfuscation Integrity & De-Registration (`recipeboxer/evals/obfuscation.py`)**:
   - `ObfuscatedCard.sha256_hash` is now strictly computed as the SHA-256 digest of the sanitized target file (`actual_sha`), never `orig_sha`.
   - Card opaque IDs are randomly generated per run using `secrets.token_hex(4)` (never derived from original ID, filename, path, or SHA).
   - Added `ObfuscationStrategy.RANDOM` as the default obfuscation strategy.
   - Completely deleted `_ACTIVE_OBFUSCATION_REGISTRY`, `resolve_active_obfuscation_id`, and all global registration hooks. Mapping between original and obfuscated IDs is kept strictly private within the active `ObfuscatedCardCorpus` context manager and used solely by the harness to map correlation results back for ground-truth scoring.

2. **Fixtures Cleanup (`tests/fixtures/loader.py`)**:
   - Removed all de-obfuscation lookups and registry backdoor paths from `SampleCardCorpus.get_by_id`.
   - Removed deterministic `card_{sha}` dictionary population.
   - Replaced import of `CardModality` from `recipeboxer.tools.card_generator` with `recipeboxer.vision.router`.

3. **SUT Fingerprint Stripping & Stage-3 Hardening (`recipeboxer/evals/runner.py`)**:
   - `VisualFingerprint` objects supplied to the SUT optimizer carry ONLY `card_id` (opaque), `image_path` (sanitized file path), `sha256_hash` (actual file SHA), and `role=CardRole.UNKNOWN`.
   - Manifest ground-truth fields (`role`, `paper_tone`, `ruling_type`, `ink_type`, `modality`, `recipe_id`) are never passed to the SUT.
   - Stage-3 extraction iterates GT clusters labeled `'oracle_grouping'` with card face paths presented in random order (not GT `sequence_position`), with a `# TODO(WP-E4)` marker pointing to predicted-cluster grouping.
   - In correlation-only stage (`stage == "correlation"`), extraction metrics are skipped entirely (`overall_cer = None`, `recipe_results = []`), and `hyp_recipe = ref_recipe` substitution was completely eliminated.
   - Per-recipe `StageUnavailableError` exceptions are caught, counted, and indexed by `FailureReason` in `stage_failure_reasons`; extraction metrics report as `None` / `N/A` without any fallback substitution.

4. **Judge Defaults & Mock Quality Disclaimers (`recipeboxer/evals/judge.py`, `recipeboxer/evals/runner.py`)**:
   - `--mock-judge` defaults to `False` in CLI parser and `BenchmarkRunner`.
   - Without `--live-judge` and a valid Gemini API key, the judge is completely disabled and no judge scores are emitted.
   - Explicit mock judge runs are labeled `'mock (not a quality score)'` in evaluation results and benchmark report Markdown.

5. **Decoupling from Card Generator**:
   - Replaced all imports of `CardModality` from `recipeboxer.tools.card_generator` in `recipeboxer/evals/obfuscation.py` and `tests/fixtures/loader.py` with `recipeboxer.vision.router.CardModality`.

6. **Honest Accuracy Reporting Without Live Model**:
   - When no valid API key is present or when extractions fail, the report outputs `'N/A (no live model)'` for CER, WER, and accuracy metrics rather than fabricated numbers.

---

## 2. Files Changed

- `recipeboxer/evals/obfuscation.py` (actual SHA, random opaque IDs, registry deletion, decouple generator)
- `tests/fixtures/loader.py` (removed registry lookup backdoor, decouple generator)
- `recipeboxer/evals/judge.py` (mock labeling, no silent fallback)
- `recipeboxer/evals/runner.py` (stripped fingerprints, random face order, correlation skip, failure recording, N/A reporting, mock-judge default False)
- `tests/test_wp_c6_harness.py` (new focused unit test suite)
- `_agents/remediation/WP-C6.md` (remediation report)

---

## 3. Test Suite Verification

### Focused WP-C6 Test Suite (`tests/test_wp_c6_harness.py`)

Executed with unset Gemini keys:
```bash
PROJECT_CACHE_DIR="$HOME/.cache/projects/$(basename "$PWD")-$(pwd -P | sha1sum | cut -c1-8)"
unset GEMINI_API_KEY
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" "$PROJECT_CACHE_DIR/venv/bin/python" -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_c6_harness.py -v
```

**Results:**
```
tests/test_wp_c6_harness.py::TestObfuscationIntegrity::test_obfuscated_sha_equals_sanitized_and_differs_when_bytes_change PASSED [  9%]
tests/test_wp_c6_harness.py::TestObfuscationIntegrity::test_opaque_ids_are_random_per_run_not_derived PASSED [ 18%]
tests/test_wp_c6_harness.py::TestNoRegistryBackdoor::test_no_registry_symbols_exist PASSED [ 27%]
tests/test_wp_c6_harness.py::TestNoRegistryBackdoor::test_sample_card_corpus_has_no_registry_lookups PASSED [ 36%]
tests/test_wp_c6_harness.py::TestFingerprintSanitization::test_runner_fingerprints_carry_no_gt_attributes PASSED [ 45%]
tests/test_wp_c6_harness.py::TestCorrelationStageIsolation::test_correlation_only_does_not_produce_extraction_scores PASSED [ 54%]
tests/test_wp_c6_harness.py::TestJudgeConfiguration::test_mock_judge_off_by_default_in_runner_and_cli PASSED [ 63%]
tests/test_wp_c6_harness.py::TestJudgeConfiguration::test_explicit_mock_judge_labeled_not_quality_score PASSED [ 72%]
tests/test_wp_c6_harness.py::TestOfflineAndFailureHandling::test_no_api_key_reports_na_no_live_model PASSED [ 81%]
tests/test_wp_c6_harness.py::TestOfflineAndFailureHandling::test_stage_unavailable_error_recorded_not_substituted PASSED [ 90%]
tests/test_wp_c6_harness.py::TestDependencyIsolation::test_evals_does_not_import_card_generator PASSED [100%]

============================== 11 passed in 9.69s ==============================
```

### Static Invariants Verification (`tests/anticheat`)

Anticheat tests confirm that cheats previously cataloged for removal under WP-C1..WP-C6 have been excised.

---

## 4. Triage / Dependencies Documented for Other WPs

- **`tests/test_eval_runner.py`**: Contains 3 legacy tests (`test_run_benchmark_default_offline_extractor`, `test_run_benchmark_full_pipeline_all_modalities`, `test_run_benchmark_correlation_stage`) cataloged in `evals/triage.yaml` under WP-C8 for deletion / mock-rewriting. These tests asserted cheat-dependent F1 scores ($\ge 0.80$) and offline heuristic extractor outputs.
- WP-E4 will replace the interim Stage-3 `'oracle_grouping'` with predicted correlation clusters.
