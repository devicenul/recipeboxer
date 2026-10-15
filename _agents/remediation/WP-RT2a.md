# Work Package Completion Note: WP-RT2a (Post-Wave-5 Defect Remediation)

**Remediation Date:** 2026-10-15  
**Target Work Package:** `WP-RT2a`  
**Addressed Findings:** 
- Fix 1: FIND-01 (fingerprint.py mock sniffing, duck typing, and URL key leakage)
- Fix 2: FIND-02 (httpx and google-genai secret key logging suppression)
- Fix 3: FIND-03 (FailedRecipe isolation to failures list, status determination, and omission accounting)
- Fix 4: Session persistence datetime JSON serialization defect
- Fix 5: Evals report rendering and breakdown fixes (Kendall tau normalization & 0.0 unmerged penalty, pending/missing structured GT robustness with verbatim CER, category breakdown mean extraction & bcubed_f1 footnote, ops spend 'unknown' fallback)
**Specification References:** `_agents/remediation/REDTEAM-2.md`, `_agents/remediation/WAVE3_CONTRACTS.md` §0, `_agents/remediation/WAVE5_CONTRACTS.md`, `_agents/INVARIANTS.md` §10 (INV-AC1..INV-AC6)

---

## 1. Summary of Changes

### Fix 1 / FIND-01: Visual Extractor Mock Sniffing Removal & Single LLMClient Path
- **Files Modified:** `recipeboxer/vision/fingerprint.py`, `tests/test_wp_c45_fingerprint_heuristics.py`, `tests/test_correlation.py`, `tests/test_challenger_m2.py`.
- **Problem:** `GeminiVisualExtractor` inspected runtime class names (`type(client).__name__.endswith("Mock")`, `hasattr(client, "mock")`), dispatched through legacy duck-typed branches, and fell back to `_call_gemini_api` which constructed raw httpx requests passing Gemini API keys in URL query parameters (`?key=...`), exposing credentials in proxy logs and debuggers.
- **Remediation:**
  - Completely excised all mock sniffing, duck-typing, and legacy client dispatch branches from `GeminiVisualExtractor`.
  - Unified runtime execution into a single clean dependency path: `self.llm_client: LLMClient` (or `create_llm_client(self.settings)`).
  - Explicitly re-raises `BudgetExceeded` so spend limits propagate uninterrupted to the orchestrator.
  - On genuine provider/network failure (or offline execution without live keys), falls back to offline degraded pixel-level analysis (`degraded=True`, role=UNKNOWN, zero invented text).
  - Completely deleted `_call_gemini_api` and URL query key transmission.
  - Migrated existing unit tests relying on Duck-typed or MagicMock clients to `ScriptedLLMClient`.

### Fix 2 / FIND-02: Secret Key Logging Prevention
- **Files Modified:** `recipeboxer/headless.py`, `evals/harness/run.py`.
- **New Test:** `tests/test_wp_rt2a_secret_logging.py`.
- **Problem:** When `DEBUG` level logging is active, `httpx` and `google_genai` log HTTP request URLs and headers, potentially exposing API keys.
- **Remediation:**
  - Configured `logging.getLogger("httpx").setLevel(logging.WARNING)` and `logging.getLogger("google_genai").setLevel(logging.WARNING)` in entry points (`headless.py`, `run.py`).
  - Added unit test asserting `GenAIClient` calls with an injected secret key never emit the secret into Python logging records across any logger.

### Fix 3 / FIND-03: FailedRecipe Isolation & SUT/Harness Status Handling
- **Files Modified:** `recipeboxer/headless.py`, `evals/harness/run.py`, `evals/scoring/evaluator.py`.
- **Problem:** When recipe extraction failed, `orchestrator.py` created `FailedRecipe(title="Extraction Failed (...)")`. `headless.py` included these placeholder recipes in `recipes_out`, causing the evaluation scorer to score stub recipes (resulting in CER 1.0 and F1 0) rather than counting the associated cards as omissions. Additionally, SUT status and harness overall status did not differentiate partial runs, failures, or budget exhaustion.
- **Remediation:**
  - In `recipeboxer/headless.py`, filtered out `FailedRecipe` objects from `recipes_out` and redirected them into `failures_out` (`card_ids`, `stage="EXTRACTION"`, `reason`, `message`).
  - SUT deck status set to:
    - `"budget_exceeded"` if spend cap reached.
    - `"no_live_model"` if offline without API credentials.
    - `"failed"` if 0 valid recipes were extracted.
    - `"partial"` if any failures exist in `failures_out`.
    - `"ok"` only if all extracted recipes succeeded with 0 failures.
  - In `evals/harness/run.py`, aggregated `overall_status` across decks (`budget_exceeded`, `no_live_model`, `failed`, `partial`, `ok`) and recorded deck failure summaries (`failure_count`, `by_stage`, `reasons`).
  - In `evals/scoring/evaluator.py`, unassigned and failed cards are matched as omissions, and `n_stage_failures` is tracked and reported.

### Fix 4: Session Persistence Datetime JSON Serialization Bug
- **Files Modified:** `recipeboxer/storage/session_store.py`, `recipeboxer/storage/cache.py`, `recipeboxer/headless.py`.
- **New Test:** `tests/test_wp_rt2a_session_persistence.py`.
- **Problem:** `json.dumps()` in `session_store.py` (`save_session`, `save_card`, `save_cards`, `save_proposals`), `cache.py`, and `headless.py` crashed with `TypeError: Object of type datetime is not JSON serializable` when saving objects containing `CaptureMeta.taken_at` or timestamps.
- **Remediation:**
  - Added `default=str` to `json.dumps()` across all session store serialization points, cache metadata serialization, and headless output serialization.
  - Added dataclass handling (`dataclasses.asdict`) when persisting lists of cards and proposals.
  - Verified with regression test persisting cards with `datetime` fields and session metadata.

### Fix 5: Evals Scoring Robustness, Report Rendering, and Category Breakdown
- **Files Modified:** `evals/metrics/grouping.py`, `evals/metrics/extraction.py`, `evals/scoring/evaluator.py`, `evals/report.py`.
- **New Test:** `tests/test_wp_rt2a_eval_robustness.py`.
- **Problem:**
  - `kendall_tau_order` did not normalize card paths/basenames and returned `1.0` when gold contained multi-card recipes but the SUT produced zero multi-card clusters (inverting cheat surfaces).
  - Tiers with pending or missing structured ground truth (e.g. T3 `structured_status: pending`) caused `score_recipe` to crash with `AttributeError: 'NoneType' object has no attribute 'get'`.
  - Category breakdown tables rendered `None` / `N/A` for extraction metrics because `evaluator.py` queried top-level keys rather than the `agg["overall"][metric]["mean"]` structure, and copied the whole-deck `bcubed_f1` across card categories.
  - Ops spend was missing key fallback when `total_usd` was stored under `total_cost_usd` or absent, displaying raw `N/A` or throwing formatting exceptions.
- **Remediation:**
  - In `evals/metrics/grouping.py`, normalized card IDs using `Path(str(c)).name`, deduplicated card IDs, and explicitly returned `0.0` when gold has multi-card recipes and the model matched zero multi-card clusters.
  - In `evals/metrics/extraction.py`, `score_recipe` safely handles `gold_structured is None`: skips structured extraction metrics (`title_cer: None`, `ingredient_f1: None`, etc.) and computes `verbatim_cer` when verbatim GT is provided.
  - In `evals/scoring/evaluator.py`, attached metadata (`deck_type`, `faces`, `faces_count`, and `modality` from `deck_gt`) to recipe rows, tracked `n_unscored_no_structured_gt`, aggregated category metrics from `cat_overall[metric]["mean"]`, and set `bcubed_f1: None` for category slices.
  - In `evals/report.py`:
    - Added `_get_metric_val(container, key)` supporting flat dicts, nested dicts, and `{"mean": ...}` structures.
    - Updated `_extract_metric_values` to alias `{"mean": ...}` leaves to their parent metric names so regression comparisons work seamlessly across both flat and hierarchical baselines.
    - Added prominent status banners for `budget_exceeded`, `partial`, and `failed`.
    - Handled missing ops spend gracefully with `'unknown'`.
    - Rendered `N/A*` for category grouping F1 with an explanatory footnote.

---

## 2. Test Verification

- **Targeted Unit Tests:**
  - `tests/test_wp_rt2a_secret_logging.py`: 1 passed.
  - `tests/test_wp_rt2a_session_persistence.py`: 2 passed.
  - `tests/test_wp_rt2a_eval_robustness.py`: 6 passed.
  - `tests/test_wp_g1_fingerprint.py`: 12 passed.
  - `tests/test_wp_c45_fingerprint_heuristics.py`: 9 passed.
  - `tests/test_correlation.py`: 14 passed.
  - `tests/test_challenger_m2.py`: 61 passed.
  - `tests/test_wp_e7_report.py`: 10 passed.
  - `tests/test_wp_e4_scorer.py`: 7 passed.
  - `tests/test_wp_e3_runner.py`: 5 passed.
- **Anti-Cheat Verification:**
  - `tests/anticheat/test_static_invariants.py`: passed.
  - `tests/anticheat/test_canaries.py`: passed (10 passed in 31.16s).
