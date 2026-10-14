# Work Package Completion Note: WP-E3 + WP-E4 (Production Headless Entry, Black-Box Harness & Scorer)

## 1. Summary

Implemented WP-E3 and WP-E4 in full compliance with `_agents/remediation/WAVE3_CONTRACTS.md` (§3, §4, §7, §8), `REMEDIATION_PLAN.md` (§3, §3.4, §6, §6.1), and `_agents/INVARIANTS.md` (§10, INV-AC1 through INV-AC6):

1. **Production Headless Entry (`recipeboxer/headless.py` & `recipeboxer/cli.py` `analyze`)**:
   - Implemented `run_headless_analysis` driving the real production pipeline (`RecipeBoxerAgent`, `PipelineSession`) with automated Human-In-The-Loop (HITL) proposal approvals.
   - Enforced fail-fast behavior without a live key (INV-AC6): sets overall status to `no_live_model` and records `StageFailure(reason=NO_API_KEY)`.
   - Built `HeadlessClientAdapter` allowing test fixtures to supply `ScriptedLLMClient` via Python API while populating `InMemoryUsageLedger`.
   - Utilized isolated `FingerprintCache(":memory:")` for every headless execution to ensure zero cross-run cache pollution.
   - Preserved deterministic output by fixing session IDs and normalizing timestamps in headless dumps.
   - Extended `recipeboxer/cli.py` with the `analyze` subcommand accepting `--input`, `--out`, `--auto-approve`, `--profile`, and `--image-sets`.

2. **Black-Box SUT Execution Harness (`evals/harness/run.py`, `evals/harness/sut_boot.py`, `evals/harness/obfuscation.py`)**:
   - `evals/harness/obfuscation.py`: Randomized deck shuffling with opaque filenames (e.g. `card_<token_hex>.jpg`), EXIF metadata stripping (`sanitize_image_exif`), and bidirectional mapping (`DeckMapping`).
   - `evals/harness/sut_boot.py`: SUT process bootstrap installing `sys.addaudithook` to intercept forbidden file read events (`evals/`, `tests/fixtures`, `*.gt.enc`, external `manifest.json`, age keys). Violations immediately terminate with exit code `86` and `CHEAT_DETECTED` on stderr.
   - `evals/harness/run.py`: Black-box subprocess runner executing the SUT with scrubbed environment allow-list in an isolated temp directory. Automatically maps obfuscated names back to corpus image IDs and writes Contract §3 artifacts: `outputs.json` (`schema: "rb-out/1"`), `ledger.json`, and `run.log`. Supports `--oracle-plan` and `--variant`.

3. **Ground Truth, Scorer, and Oracle Plan (`evals/score.py` & `evals/scoring/`)**:
   - `evals/scoring/gt.py`: Loads T0 ground truth from `tests/fixtures/sample_cards/manifest.json` and normalizes into `rb-gt/1` format; decrypts T1–T4 from `evals/vault/*.gt.enc` via `evals.vault.decrypt_bytes`. Generates oracle grouping plans (`create_oracle_plan`) containing shuffled card sets with zero textual leaks.
   - `evals/scoring/evaluator.py`: Integrates grouping metrics (`evals.metrics.grouping`), Hungarian recipe matching, extraction scoring (`evals.metrics.extraction`), ECE calibration, and ops/token costs.
   - `evals/score.py`: Main CLI and programmatic API (`score_tier`) generating `report.json` and `report.md`. Strictly enforces feedback policy: raises `ValueError` if `--feedback per_item` is requested for any tier other than `T0`.
   - `evals/run_benchmarks.py`: Replaced legacy harness wrapper with delegation to `evals.harness.run`.

4. **Elimination of `recipeboxer/evals/` (INV-AC1)**:
   - Verified existence of `evals/metrics/` and `evals/judge.py`, then completely deleted `recipeboxer/evals/`.
   - Updated `tests/anticheat/test_static_invariants.py` to remove `recipeboxer/evals` from exclusions and disallow it.

5. **Canary Tests (`tests/anticheat/test_canaries.py`)**:
   - Implemented all 5 non-negotiable canary tests from REMEDIATION_PLAN.md §6.1:
     1. Rename Invariance (Canary 1)
     2. Poisoned Vault (Canary 2)
     3. Audit-Hook Trip (Canary 3)
     4. Prompt-Leak Scan (Canary 4)
     5. Import Graph Check (Canary 5 - Static and Runtime)

---

## 2. Files Changed

### Created
- `recipeboxer/headless.py`: Production headless runner, client adapter, oracle optimizer, and auto-approve bridge.
- `evals/harness/__init__.py`: Package init.
- `evals/harness/obfuscation.py`: Deck obfuscation, EXIF sanitization, and bidirectional `DeckMapping`.
- `evals/harness/sut_boot.py`: SUT bootstrap with `sys.addaudithook` exit code 86 enforcement.
- `evals/harness/run.py`: Black-box evaluation runner generating `rb-out/1` artifacts.
- `evals/scoring/__init__.py`: Package init.
- `evals/scoring/gt.py`: Ground truth normalization, vault decryption, and oracle plan generator.
- `evals/scoring/evaluator.py`: Evaluation metrics engine combining grouping, extraction, calibration, and ops.
- `evals/score.py`: Scorer CLI and `oracle-plan` generator with feedback policy enforcement.
- `tests/anticheat/test_canaries.py`: Five canary test suites.
- `tests/test_wp_e3_runner.py`: Unit and integration tests for headless analysis and harness runner.
- `tests/test_wp_e4_scorer.py`: Unit tests for scorer, ground truth, oracle plan, and feedback policy.
- `_agents/remediation/WP-E3E4.md`: This completion note.

### Modified
- `recipeboxer/cli.py`: Added `analyze` subcommand with full argument parsing.
- `evals/run_benchmarks.py`: Updated to forward directly to `evals.harness.run`.
- `tests/anticheat/test_static_invariants.py`: Removed `recipeboxer/evals` from `TEMPORARY_EXCLUSIONS`.
- `tests/test_eval_obfuscation.py`: Rewritten to test `evals.harness.obfuscation`.

### Deleted
- `recipeboxer/evals/` (entire directory deleted: `__init__.py`, `judge.py`, `metrics.py`, `obfuscation.py`, `runner.py`).
- `tests/test_wp_c6_harness.py`: Deleted (superseded by `tests/test_wp_e3_runner.py`).
- `tests/test_eval_runner.py`: Deleted (superseded by `tests/test_wp_e3_runner.py`).
- `tests/test_challenger_m2_obfuscation_deep.py`: Deleted (superseded by `tests/anticheat/test_canaries.py::test_canary_rename_invariance`).
- `tests/test_challenger_m2_prompt_leak.py`: Deleted (superseded by `tests/anticheat/test_canaries.py::test_canary_prompt_leak_scan`).

---

## 3. Legacy Test Triage Table

The following table records the triage decisions for the 5 legacy test files owned in Contracts §8:

| Legacy Test File | Action | Rationale | New Test Outcome |
|---|---|---|---|
| `tests/test_eval_obfuscation.py` | **REWRITTEN** | Rewritten to target `evals.harness.obfuscation`. Tests EXIF stripping, randomized opaque deck preparation, and bidirectional mapping. | `3 passed` |
| `tests/test_eval_runner.py` | **DELETED** | Fully superseded by `tests/test_wp_e3_runner.py` which validates SUT execution, fail-fast without API key, and `rb-out/1` output generation. | N/A (deleted) |
| `tests/test_challenger_m2_obfuscation_deep.py` | **DELETED** | Fully superseded by `tests/anticheat/test_canaries.py::test_canary_rename_invariance` (Canary 1). | N/A (deleted) |
| `tests/test_challenger_m2_prompt_leak.py` | **DELETED** | Fully superseded by `tests/anticheat/test_canaries.py::test_canary_prompt_leak_scan` (Canary 4). | N/A (deleted) |
| `tests/test_wp_c6_harness.py` | **DELETED** | Wave 2 legacy in-process harness superseded by black-box subprocess runner in `tests/test_wp_e3_runner.py` and `tests/test_wp_e4_scorer.py`. | N/A (deleted) |

---

## 4. Verification & Canary Results

All test suites pass 100%:

```bash
$ pytest tests/anticheat/test_canaries.py tests/anticheat/test_static_invariants.py tests/test_eval_obfuscation.py tests/test_wp_e3_runner.py tests/test_wp_e4_scorer.py -v
============================== 21 passed in 3.96s ==============================
```

Detailed test summary:
- `tests/anticheat/test_canaries.py`:
  - `test_canary_rename_invariance`: PASSED
  - `test_canary_poisoned_vault`: PASSED
  - `test_canary_audit_hook_trip`: PASSED
  - `test_canary_prompt_leak_scan`: PASSED
  - `test_canary_import_graph_static`: PASSED
  - `test_canary_import_graph_runtime`: PASSED
- `tests/anticheat/test_static_invariants.py`:
  - `test_no_answer_key_paths`: PASSED
  - `test_no_filename_semantics`: PASSED
  - `test_no_fixture_content`: PASSED
  - `test_no_cardforge_or_evals_import`: PASSED
- `tests/test_eval_obfuscation.py`:
  - `TestExifSanitization::test_sanitize_image_exif_strips_metadata`: PASSED
  - `TestDeckObfuscation::test_prepare_obfuscated_deck_opaque`: PASSED
  - `TestDeckObfuscation::test_deck_mapping_stem_lookup`: PASSED
- `tests/test_wp_e3_runner.py`:
  - `TestHeadlessExecution::test_headless_no_key_fail_fast`: PASSED
  - `TestHeadlessExecution::test_headless_scripted_client_flow`: PASSED
  - `TestSutBootAuditHook::test_audit_hook_blocks_vault_access`: PASSED
  - `TestHarnessRun::test_run_t0_offline_produces_contract_artifacts`: PASSED
- `tests/test_wp_e4_scorer.py`:
  - `TestGroundTruthAndOracle::test_load_t0_ground_truth`: PASSED
  - `TestGroundTruthAndOracle::test_create_oracle_plan`: PASSED
  - `TestFeedbackPolicyEnforcement::test_per_item_allowed_for_t0`: PASSED
  - `TestFeedbackPolicyEnforcement::test_per_item_disallowed_for_non_t0`: PASSED

---

## 5. Usage Section

### 5.1 Headless SUT Execution

Run the production digitization pipeline headlessly from the command line:

```bash
# Basic run with auto-approval
python -m recipeboxer analyze --input /path/to/card_scans --out /tmp/predictions.json --auto-approve

# Run with custom model tier and oracle image grouping plan
python -m recipeboxer analyze \
    --input /path/to/card_scans \
    --out /tmp/predictions.json \
    --auto-approve \
    --profile best \
    --image-sets /path/to/image_sets.json
```

Python API (for offline testing with scripted or mock LLM clients):

```python
from pathlib import Path
from recipeboxer.ai import ScriptedLLMClient
from recipeboxer.headless import run_headless_analysis

client = ScriptedLLMClient([...])
result = await run_headless_analysis(
    input_dir=Path("tests/fixtures/sample_cards"),
    out_file=Path("/tmp/out.json"),
    auto_approve=True,
    injected_client=client,
)
print("Status:", result["status"])
```

### 5.2 Black-Box Benchmark Runner

Execute black-box evaluation against an evaluation tier (runs SUT in isolated subprocess with scrubbed environment and audit hooks):

```bash
# Run T0 (sample cards benchmark)
python -m evals.harness.run --tier T0 --out /tmp/eval_t0

# Run T1 with oracle pairing plan
python -m evals.harness.run \
    --tier T1 \
    --out /tmp/eval_t1_oracle \
    --oracle-plan /tmp/t1_oracle_plan.json \
    --variant opaque

# Run specific decks with variant transformation
python -m evals.harness.run \
    --tier T1 \
    --decks d01,d02 \
    --out /tmp/eval_t1_variant \
    --variant misleading
```

Output directory contains:
- `outputs.json`: SUT predictions adhering to schema `rb-out/1`.
- `ledger.json`: Token consumption and latency ledger.
- `run.log`: Full runner execution log including subprocess stdout/stderr.

### 5.3 Oracle Plan Generation

Generate an oracle grouping plan for a benchmark tier (sets in randomized order, faces shuffled, zero text metadata):

```bash
# Generate oracle plan for T0
python -m evals.score oracle-plan --tier T0 --out /tmp/t0_oracle_plan.json

# Generate oracle plan for T1 (decrypting GT using default age identity or custom path)
python -m evals.score oracle-plan \
    --tier T1 \
    --identity ~/.config/recipeboxer/eval.key \
    --out /tmp/t1_oracle_plan.json
```

### 5.4 Evaluation Scorer

Score SUT outputs against ground truth and produce Markdown and JSON reports:

```bash
# Score T0 run with per-item feedback (permitted on T0)
python -m evals.score \
    --tier T0 \
    --outputs /tmp/eval_t0 \
    --report /tmp/report_t0 \
    --feedback per_item

# Score T1 run with aggregate feedback (per-item strictly forbidden on T1-T4)
python -m evals.score \
    --tier T1 \
    --outputs /tmp/eval_t1 \
    --report /tmp/report_t1 \
    --feedback aggregate \
    --identity ~/.config/recipeboxer/eval.key

# Score with baseline regression comparison
python -m evals.score \
    --tier T1 \
    --outputs /tmp/eval_t1 \
    --report /tmp/report_t1 \
    --compare evals/baselines/T1_balanced.json
```

Report directory contains:
- `report.json`: Structured evaluation results, grouping metrics, extraction scores, calibration, and ops summary.
- `report.md`: GitHub-flavored Markdown report.
