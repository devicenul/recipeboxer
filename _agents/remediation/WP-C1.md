# Work Package Completion Report: WP-C1 (Recipe Extractor Remediation)

**Package Owner:** WP-C1 (flash)  
**Branch:** `remediation/phase0`  
**Status:** Completed & Verified  

---

## 1. Summary of Remediation

In accordance with `_agents/remediation/REMEDIATION_PLAN.md` (§2, §4.1, §10), `_agents/INVARIANTS.md` (§10, INV-AC1..INV-AC6), and `_agents/TESTING_GUIDELINES.md` (§7), WP-C1 eliminated all manifest lookups, answer-key caches, and canned-recipe fallback paths from `recipeboxer/extraction/extractor.py`.

### Key Changes:

1. **Answer Key & Offline Heuristic Elimination (INV-AC1, INV-AC3)**:
   - Completely deleted `_MANIFEST_CACHE`, `_MANIFEST_BY_CARD_ID`, `_MANIFEST_BY_SHA256`, and `_get_manifest_lookup_tables()`.
   - Completely deleted `_extract_offline_recipe()`, including all canned recipes (cinnamon rolls, lemon bars, fudge, pecan pie), multi-card generic recipes, and fallback placeholder recipes.
   - Updated module and package docstrings in `recipeboxer/extraction/extractor.py` and `recipeboxer/extraction/__init__.py` to remove references to deterministic offline heuristics.

2. **Honest `extract()` Execution and Failure Signaling (INV-AC4, INV-AC6)**:
   - Kept injected-client path for tests; if the client yields nothing usable (None, unhandled response, or invalid model), raises `ExtractionUnavailable(reason=FailureReason.INVALID_RESPONSE)` or `FailureReason.SCHEMA_VALIDATION` instead of falling back.
   - Requires real API key (not None, not starting with `'mock'`) when no client is injected; if missing, raises `ExtractionUnavailable(reason=FailureReason.NO_API_KEY, retryable=False, card_ids=ordered_card_ids)`.
   - Mapped API exceptions from `_call_gemini_extraction_api`:
     - `httpx.TimeoutException` $\rightarrow$ `FailureReason.TIMEOUT`
     - HTTP 429 $\rightarrow$ `FailureReason.RATE_LIMITED`
     - Other HTTP status / network errors $\rightarrow$ `FailureReason.MODEL_UNAVAILABLE`
     - JSON decode / response structure errors $\rightarrow$ `FailureReason.INVALID_RESPONSE`
     - Pydantic validation errors $\rightarrow$ `FailureReason.SCHEMA_VALIDATION`
     - All exceptions populate `card_ids`.

3. **Honest `extract_sync()` Execution**:
   - Executes the async extraction path via `asyncio` (using thread pool executor when inside a running loop) without offline heuristics.

4. **Batch Extraction Helper (`extract_batch()`)**:
   - Per-item failures are collected and returned as `StageFailure` records (never placeholder recipes) preserving input ordering.

---

## 2. Files Changed

- `recipeboxer/extraction/extractor.py` (Remediated: removed manifest tables, canned recipes, offline heuristics; added failure mapping)
- `recipeboxer/extraction/__init__.py` (Updated package docstring)
- `tests/test_wp_c1_extractor.py` (New targeted unit test suite)
- `_agents/remediation/WP-C1.md` (Remediation report)

---

## 3. Tests Run & Results

### Targeted Unit Test Suite (`tests/test_wp_c1_extractor.py`)

Ran with unconfigured API key and explicit injected mocks:
```bash
PROJECT_CACHE_DIR="$HOME/.cache/projects/$(basename "$PWD")-$(pwd -P | sha1sum | cut -c1-8)"
env -u GEMINI_API_KEY -u RECIPEBOXER_GEMINI_API_KEY PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" "$PROJECT_CACHE_DIR/venv/bin/python" -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_c1_extractor.py -v
```

**Results:**
```
tests/test_wp_c1_extractor.py::test_no_key_raises_no_api_key PASSED      [  6%]
tests/test_wp_c1_extractor.py::test_extract_sync_no_key_raises PASSED    [ 13%]
tests/test_wp_c1_extractor.py::test_injected_client_result_passes_through PASSED [ 20%]
tests/test_wp_c1_extractor.py::test_injected_client_dict_passes_through PASSED [ 26%]
tests/test_wp_c1_extractor.py::test_injected_client_yielding_nothing_raises_invalid_response PASSED [ 33%]
tests/test_wp_c1_extractor.py::test_injected_client_invalid_schema_raises_schema_validation PASSED [ 40%]
tests/test_wp_c1_extractor.py::test_extract_sync_with_injected_client PASSED [ 46%]
tests/test_wp_c1_extractor.py::test_api_exception_mapping_timeout PASSED [ 53%]
tests/test_wp_c1_extractor.py::test_api_exception_mapping_rate_limited PASSED [ 60%]
tests/test_wp_c1_extractor.py::test_api_exception_mapping_model_unavailable PASSED [ 66%]
tests/test_wp_c1_extractor.py::test_api_exception_mapping_json_error PASSED [ 73%]
tests/test_wp_c1_extractor.py::test_api_exception_mapping_validation_error PASSED [ 80%]
tests/test_wp_c1_extractor.py::test_module_has_no_manifest_or_offline_cheats PASSED [ 86%]
tests/test_wp_c1_extractor.py::test_corpus_image_path_with_no_client_raises PASSED [ 93%]
tests/test_wp_c1_extractor.py::test_extract_batch_returns_stage_failures PASSED [100%]

============================== 15 passed in 0.11s ==============================
```

### Static Invariants Anti-Cheat Suite (`tests/anticheat`)

```bash
env -u GEMINI_API_KEY -u RECIPEBOXER_GEMINI_API_KEY PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" "$PROJECT_CACHE_DIR/venv/bin/python" -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/anticheat --runxfail -v
```

**Results:**
- **Owned files (`recipeboxer/extraction/extractor.py`, `recipeboxer/extraction/__init__.py`, `tests/test_wp_c1_extractor.py`)**: **0 offending hits** for INV-AC1, INV-AC2, INV-AC3, INV-AC4.
- Overall suite: `4 passed in 1.32s`.

### Related Adaptive Thinking Suite (`tests/test_adaptive_thinking.py`)

```bash
env -u GEMINI_API_KEY -u RECIPEBOXER_GEMINI_API_KEY PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" "$PROJECT_CACHE_DIR/venv/bin/python" -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_adaptive_thinking.py -q
```
**Results:** `8 passed in 0.13s`.

---

## 4. Triage-Listed Failures Observed

The following 16 tests in `tests/test_recipe_extractor.py` failed as expected due to removal of offline cheats, matching entries cataloged in `evals/triage.yaml` for deletion or rewriting under WP-C8:

1. `test_offline_extraction_ground_truth_all_12_corpus_recipes` (triage line 1: delete)
2. `test_offline_extraction_dual_index_manifest_resolution` (triage line 8: delete)
3. `test_offline_extraction_eliminates_cross_contamination` (triage line 15: delete)
4. `test_offline_extraction_multi_part_sequence_persistence` (triage line 22: delete)
5. `test_extractor_offline_standalone_fudge` (triage line 85: rewrite_mock)
6. `test_extractor_offline_standalone_pecan_pie` (triage line 92: rewrite_mock)
7. `test_extractor_offline_two_sided_cinnamon_rolls` (triage line 99: rewrite_mock)
8. `test_extractor_offline_two_sided_lemon_bars` (triage line 106: rewrite_mock)
9. `test_extractor_scanned_card_and_path_inputs` (triage line 113: rewrite_mock)
10. `test_extractor_batch_concurrency` (triage line 120: rewrite_mock)
11. `test_extractor_sync_helper` (triage line 127: rewrite_mock)
12. `test_extractor_offline_generic_unseen_card` (triage line 718: delete)
13. `test_extractor_api_failure_fallback` (triage line 725: delete)
14. `test_extractor_alias_method` (triage line 732: rewrite_mock)
15. `test_extract_cards_multi_card_sequence_offline` (triage line 739: rewrite_mock)
16. `test_offline_extraction_inferred_untitled_card_appends_guess` (triage line 746: delete)

Additionally, `test_extractor_initialization_defaults` failed asserting `model_name == "gemini-3.7-flash"` because WP-X1's concurrent edit to `recipeboxer/config.py` configured quality profile defaults to `gemini-2.5-flash`.

---

## 5. Requests for Other WPs

- **WP-X1**: Define `DEFAULT_STAGE_TIMEOUTS` in `recipeboxer/config.py`. In `Settings`, `stage_timeouts: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_STAGE_TIMEOUTS))` currently triggers `NameError` if `DEFAULT_STAGE_TIMEOUTS` is missing.
- **WP-C8**: Rewrite/delete the 16 legacy tests in `tests/test_recipe_extractor.py` per `evals/triage.yaml`.

---

## 6. Risks & Mitigations

- **Risk**: Downstream caller expects `extract_batch()` to always return `RecipeSchema` instances and might fail if an item returns `StageFailure`.
  - **Mitigation**: `extract_batch()` returns `StageFailure` only on item failure. Orchestrator stage review processes `StageFailure` directly into HITL queue as required by INV-AC4.
