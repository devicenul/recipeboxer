# WP-B1 & WP-B3 Remediation Report: Cost Engine & Evaluation Budget

## Summary
Implemented WP-B1 (Cost Engine) and WP-B3 (Evaluation Budget Enforcement) according to `WAVE4_CONTRACTS.md` (§1.1, §1.2, §2) and `REMEDIATION_PLAN.md` §7:

1. **Price Table & Cost Calculation** (`recipeboxer/ai/cost.py` & `config/pricing.toml`):
   - `PriceTable`: Loads pricing configuration from `config/pricing.toml`. Schema defines per-model `input_per_mtok`, `output_per_mtok`, `thinking_per_mtok`, optional `context_tier_threshold_tokens`, `input_per_mtok_high_tier`, `output_per_mtok_high_tier`, `verified_on`, and `source_url`.
   - Missing/unpriced models safely return `None` and log a warning; never silently default to $0.00.
   - Placeholder values in `config/pricing.toml` are clearly preserved with schema finalised for WP-B0 live rate verification.
2. **Cost Ledger & Budget Guard** (`recipeboxer/ai/cost.py`):
   - `CostLedger(UsageLedger)`: Implements thread-safe call recording tracking per-stage and aggregate token usage, latency, and computed USD costs (or `None` when unpriced). Provides `snapshot()` and `to_dict()` methods.
   - `BudgetGuard`: Enforces pre-call ceilings `spend_cap_usd` and `max_calls`. Invoked before each LLM generation via `check_before_call(ledger, est_usd=0.0)`.
   - `BudgetExceeded(StageUnavailableError)`: Subclasses `StageUnavailableError` in `recipeboxer/models/failures.py` with `reason = FailureReason.BUDGET_EXCEEDED` and `retryable = False`.
3. **Pre-Run Cost Estimation** (`recipeboxer/ai/cost.py`):
   - `estimate_run_cost(n_cards, profile, prices, calibration=None)`: Implements the exact stage-by-stage token consumption formula defined in REMEDIATION_PLAN.md §7.1. Stage assumptions are documented and fully overridable by external calibration dictionaries.
4. **Settings Configuration** (`recipeboxer/config.py`):
   - Added `spend_cap_usd` (None default), `monthly_cap_usd`, `max_calls`, and `pricing_path` to `Settings`.
   - Added `PROFILE_PARAMS` table mapping `economy`, `balanced`, and `best` profiles to `image_long_edge`, `thinking_budget_transcribe`, `self_consistency`, and `grounding`.
   - Implemented `Settings.profile_params() -> Dict[str, Any]` per §1.2.
   - **WP-G4 Request Fulfilled**: Added `embeddings_enabled: bool = False`, `embedding_model: str = "text-embedding-004"` (provisional), and `embedding_batch_size: int = 16` to `Settings`.
5. **Client & Pipeline Integration**:
   - `recipeboxer/ai/client.py`: Injected optional `guard` into `GenAIClient` and hooked `eff_guard.check_before_call(self.ledger, est_usd=...)` directly before each generation attempt.
   - `recipeboxer/agent/orchestrator.py`: Caught `BudgetExceeded` across Stage 2 (fingerprinting), Stage 3 (correlation), and Stage 5 (extraction). When triggered, session enqueues a HITL item with `item_type=HitlItemType.BUDGET` and `kind="budget"` (added `BUDGET` to `HitlItemType` in `recipeboxer/models/hitl.py`) and cleanly pauses with `await session.pause_for_hitl()`. Never degrades to fake or corrupted recipes. Resumes on approval or cleanly exits on cancellation.
6. **Evaluation & Headless Budget Enforcement** (`recipeboxer/headless.py`, `recipeboxer/cli.py`, `evals/harness/run.py`):
   - `recipeboxer analyze --budget-usd X --max-calls Y`: Wired into CLI parser and passed to `run_headless_analysis`.
   - `evals/harness/run.py`: Added `--budget-usd` and `--max-calls` flags, passed down to SUT subprocess invocation.
   - Clean shutdown on budget breach: When budget or call cap is reached, headless pipeline cancels gracefully, producing status `"budget_exceeded"` with partial results, failures, and complete `ledger` (including `total_usd`). `evals.harness.run` halts further deck executions and writes aggregated `total_usd` in `ledger.json`.

## Files Modified / Created
- `recipeboxer/ai/cost.py` (new)
- `recipeboxer/ai/__init__.py` (re-exports)
- `recipeboxer/ai/client.py` (guard hook before call attempt)
- `recipeboxer/models/failures.py` (`BudgetExceeded` error)
- `recipeboxer/models/hitl.py` (`HitlItemType.BUDGET` enum value)
- `recipeboxer/config.py` (budget settings, profile_params, WP-G4 embedding settings)
- `config/pricing.toml` (final schema with tier thresholds and source URLs)
- `recipeboxer/headless.py` (budget pass-through, `_auto_hitl_bridge` budget exit, ledger totals)
- `recipeboxer/cli.py` (`--budget-usd`, `--max-calls` parser options)
- `evals/harness/run.py` (`--budget-usd`, `--max-calls` CLI & SUT pass-through, `create_run_parser`)
- `recipeboxer/agent/orchestrator.py` (budget pause boundary handling)
- `tests/test_wp_b1_cost.py` (new - 10 offline unit & integration tests)
- `tests/test_wp_b3_budget.py` (new - 5 offline unit & integration tests)

## Verification & Test Results
All tests execute completely offline without live network or external Gemini API dependencies:
```bash
.venv/bin/pytest tests/test_wp_b1_cost.py tests/test_wp_b3_budget.py tests/anticheat/test_canaries.py tests/anticheat/test_static_invariants.py tests/test_wp_e3_runner.py tests/test_wp_x1_client.py -v
```
Output:
```
============================== 49 passed in 5.99s ==============================
```
- `tests/test_wp_b1_cost.py`: 10 passed (price table schema, unpriced model warnings, context tier math, cost ledger snapshots, budget guard max_calls/cap_usd tripping, pre-run estimation with calibration, settings profile_params, GenAIClient hook, orchestrator HITL budget pause).
- `tests/test_wp_b3_budget.py`: 5 passed (CLI argument parsing, harness pass-through, headless max_calls trip, headless budget_usd trip, harness ledger.json writing).
- Anticheat and regression suites: 100% passed (zero regressions, zero cheat flags).

## Contract Deviations
None. All contracts strictly honored. Added `BUDGET` enum to `HitlItemType` in `recipeboxer/models/hitl.py` (unowned in Wave 4) per specification.
