# Work Package Completion Report: WP-C7 (Pipeline Failure Routing, Anti-Drop & HITL Integration)

**Package Owner:** WP-C7 (flash)  
**Branch:** `remediation/phase0`  
**Status:** Completed & Verified  

---

## 1. Summary of Remediation

In accordance with `_agents/remediation/REMEDIATION_PLAN.md` (§2, §4.1, §8.3, §10), `_agents/INVARIANTS.md` (§10, INV-AC4, INV-AC6), and `_agents/TESTING_GUIDELINES.md` (§7), WP-C7 addressed all pipeline failure surfacing, elimination of silent recipe drops, no-key onboarding fail-fast behavior, degraded/unverified signal tracking, and additive REST API schema extensions.

### Key Changes:

1. **Explicit `StageFailure` Conversion & HITL Routing (INV-AC4)**:
   - When fingerprinting, correlation, or recipe extraction encounters a `StageUnavailableError` (or unexpected exception), it is caught per card/proposal and converted to a typed `StageFailure`.
   - The failure is appended to `failures: List[StageFailure]` on the session and pipeline status models.
   - Enqueued into Human-in-the-Loop review as a typed HITL item (`HitlItemType.STAGE_FAILURE`) so operators can inspect failed cards/proposals directly.

2. **Elimination of Silent Recipe Drops**:
   - Fixed the silent drop in `recipeboxer/agent/orchestrator.py`: recipes failing extraction are wrapped in `FailedRecipe(title=..., status="FAILED", failure=...)` and appended to `session.extracted_recipes`.
   - Failed recipes remain visible in status models, telemetry, and HITL queues with failure details—never silently dropped or discarded.
   - Removed the hard `RuntimeError("Recipe extraction failed for all approved cards.")` that previously aborted the pipeline without recording output.

3. **Degraded Fingerprint & Unverified Proposal Surfacing**:
   - Surfaced cards with `degraded=True` (e.g. offline pixel-only analysis or visual degradation) in `session.degraded_fingerprints_count`.
   - Marked proposals with `semantic_verified=False` or containing degraded cards as `needs_review=True` with `recommended_action="NEEDS_REVIEW"` and review reasons.
   - Surfaced degraded and unverified metrics in `ProgressMetrics` and `PipelineStatus`.

4. **No-Key / No-Client Fail-Fast Behavior (INV-AC6, D5)**:
   - Implemented `_has_usable_model_or_client()` in `RecipeBoxerAgent` to inspect session mocks, component overrides, settings, and session configs.
   - Stage 1 directory scanning and hashing run normally.
   - If no API key and no injected client exist when analysis begins, the pipeline immediately enters `PipelineState.FAILED` with `StageFailure(reason=FailureReason.NO_API_KEY, retryable=False, details={"blocked_by": "INV-AC6"})`, records status, enqueues a HITL failure item, and exits before Stage 2 / Stage 4 without hanging at the HITL event boundary.

5. **Additive REST API Extensions (`recipeboxer/api/routes.py`)**:
   - Extended `/api/status`, `/api/pipeline/status`, and `/api/sessions/{session_id}` responses to include additive fields: `failures`, `failures_count`, `degraded_fingerprints_count`, `unverified_proposals_count`, `failed_extractions_count`, and `hitl_queue`.
   - Updated `/api/hitl/pending` to return `failures`, `failure_items`, and `hitl_queue`.
   - Added dedicated `/api/hitl/queue` and `/api/sessions/{session_id}/hitl/queue` endpoints.
   - All schema additions are strictly additive and backward-compatible.

---

## 2. Files Changed

- `recipeboxer/models/hitl.py`:
  - Added `HitlItemType` enum (`PROPOSAL`, `STAGE_FAILURE`, `UNASSIGNED_CARD`, `MANUAL_REVIEW`).
  - Added `HitlItem` model and `HitlQueueItem` alias with fields for failures and review reasons.
- `recipeboxer/models/pipeline.py`:
  - Added `FailedRecipe` schema subclass with `status="FAILED"`.
  - Added failure and degraded counters to `ProgressMetrics` and `PipelineStatus`.
- `recipeboxer/models/session.py`:
  - Added additive failure counters and `hitl_queue` fields to `SessionSummary` and `SessionDetail`.
- `recipeboxer/ingestion/runner.py`:
  - Added `failure` field to `CardTaskResult` and `failures` list to `IngestionResult`.
  - Converted `StageUnavailableError` and unexpected exceptions during fingerprinting to `StageFailure`.
- `recipeboxer/agent/session.py`:
  - Added failure lists, `hitl_queue`, and counters to `PipelineSession`.
  - Implemented `add_failure()`, `enqueue_hitl_item()`, and `enqueue_hitl_failure()`.
  - Updated `get_status()` to populate and forward failures and telemetry.
- `recipeboxer/agent/orchestrator.py`:
  - Implemented `_has_usable_model_or_client()` for INV-AC6 compliance.
  - Implemented no-key fail-fast path before Stage 2.
  - Caught failures in fingerprinting, correlation, and extraction.
  - Replaced silent recipe drops with `FailedRecipe` preservation.
  - Added auto-approve and failed-state guards to prevent hanging at the HITL event boundary.
- `recipeboxer/api/routes.py`:
  - Forwarded failure and telemetry fields in `get_pipeline_status` and `get_session_detail`.
  - Added failure items to `get_hitl_pending`.
  - Added `/api/hitl/queue` and `/api/sessions/{session_id}/hitl/queue` endpoints.
- `tests/test_wp_c7_failures.py`:
  - Unit and integration test suite covering all WP-C7 requirements (5 tests).
- `_agents/remediation/WP-C7.md`:
  - This completion report.

---

## 3. Tests Run & Results

### 1. Targeted Failure Test Suite (`tests/test_wp_c7_failures.py`)

```bash
PROJECT_CACHE_DIR="$HOME/.cache/projects/$(basename "$PWD")-$(pwd -P | sha1sum | cut -c1-8)"
env -u GEMINI_API_KEY -u RECIPEBOXER_GEMINI_API_KEY PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" \
    "$PROJECT_CACHE_DIR/venv/bin/python" -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" \
    tests/test_wp_c7_failures.py -v
```

**Results:**
```
tests/test_wp_c7_failures.py::test_injected_extractor_unavailable_yields_failed_recipe_and_hitl_item PASSED [ 20%]
tests/test_wp_c7_failures.py::test_mixed_batch_one_succeeds_one_fails_preserves_both PASSED                 [ 40%]
tests/test_wp_c7_failures.py::test_no_key_fail_fast_without_injected_client PASSED                          [ 60%]
tests/test_wp_c7_failures.py::test_degraded_fingerprints_and_unverified_proposals_surfaced PASSED         [ 80%]
tests/test_wp_c7_failures.py::test_api_status_and_hitl_queue_endpoints_expose_failures PASSED            [100%]

============================== 5 passed in 0.50s ===============================
```

### 2. Static Anti-Cheat Invariants Suite (`tests/anticheat`)

```bash
env -u GEMINI_API_KEY -u RECIPEBOXER_GEMINI_API_KEY PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" \
    "$PROJECT_CACHE_DIR/venv/bin/python" -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" \
    tests/test_wp_c7_failures.py tests/anticheat --runxfail -v
```

**Results:**
- All 5 WP-C7 failure tests: `PASSED`
- `tests/anticheat/test_static_invariants.py`: 4 `PASSED` (0 offending hits in owned files for INV-AC1..INV-AC4)
- Suite summary: `9 passed in 1.95s`

### 3. Incremental Ingestion Suite (`tests/test_incremental_ingestion.py`)

```bash
env -u GEMINI_API_KEY -u RECIPEBOXER_GEMINI_API_KEY PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" \
    "$PROJECT_CACHE_DIR/venv/bin/python" -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" \
    tests/test_incremental_ingestion.py -q
```

**Results:** `11 passed in 0.91s`.

---

## 4. Triage Notes for Related Work Packages

- **WP-C8 (Test Triage)**:
  - In `tests/test_fastapi_routes.py::TestFastAPIRoutes::test_full_pipeline_api_lifecycle`, the test fixture `test_session_manager` initializes `SessionManager()` without injecting `mock_gemini_client` into sessions created via POST `/api/pipeline/start`.
  - Under INV-AC6 / WP-C7 no-key fail-fast, this test triggers `Analysis blocked: No Gemini API key or injected LLM client available` and fails at HITL polling as expected.
  - As cataloged in `_agents/remediation/REMEDIATION_PLAN.md` §10 (Test Triage Inventory, line 431), this test is owned by WP-C8 (`action: rewrite_mock`) to properly inject mock clients into sessions created by the API.

---

## 5. Risks & Mitigations

- **Risk**: API consumers relying on strict schema validation might choke on additive response fields.
  - **Mitigation**: All new fields (`failures`, `hitl_queue`, `degraded_fingerprints_count`, etc.) are optional with default values (empty lists or zeros) and do not alter existing property types or names.
- **Risk**: Automated tests running the orchestrator without setting the approval event could hang.
  - **Mitigation**: Added support for `auto_approve=True` in `session.config` and a fast bail-out guard if `session.state == PipelineState.FAILED` so pipelines never block at the HITL event boundary when stages fail.
