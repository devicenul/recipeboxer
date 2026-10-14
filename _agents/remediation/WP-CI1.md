# Work Package Completion Report: WP-CI1 (CI Workflows & Security Infrastructure)

**Agent**: WP-CI1  
**Branch**: `remediation/phase0`  
**Plan Reference**: `_agents/remediation/REMEDIATION_PLAN.md` §3.4, §4.1, §6, §7.3; `_agents/remediation/WAVE3_CONTRACTS.md` §0, §3, §4  
**Date**: 2026-10-14  

---

## 1. Summary

WP-CI1 implements the RecipeBoxer continuous integration infrastructure and the two-workflow evaluation security architecture for honest model evaluation against encrypted ground truth.

Key components delivered:
1. **`.github/workflows/ci.yml`**: Triggers on `push` and `pull_request`. Executes offline test suite on Python 3.13 (matching local virtualenv) with pip caching, dependency installation including editable `cardforge`, a separate required `tests/anticheat` invariant check step, backend/cardforge test suite execution with timeout, and a separate frontend test job running `npm test` (`vitest run`). Configured with minimal permissions (`contents: read`) and no secrets.
2. **`.github/workflows/eval-run.yml`**: Untrusted evaluation runner executing on pull requests labeled `run-eval`, pushes to `main`, or manual `workflow_dispatch`. Gates execution behind the `eval-live` GitHub environment (requiring maintainer approval) for injection of `CI_GEMINI_API_KEY` (dedicated low-quota key). Contains **NO vault secrets or age identities**. Enforces per-run budget ceiling (`RB_EVAL_MAX_USD`). Executes `python -m evals.harness.run --tier T1 --out out/`, writes `run-metadata.json` (capturing PR number and head SHA), and publishes `out/` as artifact `sut-outputs`.
3. **`.github/workflows/eval-score.yml`**: Trusted evaluation scorer triggered via `workflow_run` upon successful completion of `eval-run`. Checks out trusted code exclusively from the default branch (`main`). Injects `CI_AGE_IDENTITY` into a temporary file outside the workspace with `umask 077` and sets `RB_EVAL_AGE_KEY_FILE`. Downloads `sut-outputs` and performs strict schema and size validation without shell interpolation or `eval()`. Executes `python -m evals.score --tier T1 --outputs sut_artifact --report report/ --feedback aggregate`. Enforces rate limiting (max 10 scored runs per PR per 24 hours) by counting previous bot comments via `actions/github-script` before posting aggregate evaluation reports. Configured with minimal permissions (`contents: read`, `actions: read`, `pull-requests: write`). Strictly avoids `pull_request_target`.
4. **`docs/CI.md`**: Comprehensive operations and security architecture guide detailing the two-workflow split, maintainer setup steps (identity generation via `evals.vault keygen`, recipient registration, rekeying, secret configuration, environment gating, budget caps, label setup), fork PR policies, rate limiting, oracle grouping tracks, future Tier 2 held-out benchmarks (`eval-heldout.yml`), and threat model analysis table.
5. **Action SHA Pinning**: All GitHub Actions are pinned to full 40-character immutable commit SHAs with version comments (`actions/checkout@11bd719... # v4.2.2`, `actions/setup-python@4237552... # v5.4.0`, `actions/setup-node@1d0ff46... # v4.2.0`, `actions/upload-artifact@4cec3d8... # v4.6.1`, `actions/download-artifact@cc20338... # v4.1.9`, `actions/github-script@60a0d83... # v7.0.1`).

---

## 2. Files Changed & Created

| Path | Status | Description |
|---|---|---|
| `.github/workflows/ci.yml` | Created | Offline backend (Python 3.13) + anticheat + frontend (vitest) test workflow. |
| `.github/workflows/eval-run.yml` | Created | Untrusted SUT evaluation runner in `eval-live` environment with `CI_GEMINI_API_KEY`. |
| `.github/workflows/eval-score.yml` | Created | Trusted scorer workflow on `workflow_run`, `CI_AGE_IDENTITY` mounting, schema validation, rate-limited PR comment. |
| `docs/CI.md` | Created | CI maintenance and evaluation security architecture documentation. |
| `_agents/remediation/WP-CI1.md` | Created | Work package completion report. |

---

## 3. Commands Executed & Verification Results

1. **Python Environment & Version Verification**:
   - Python: `3.13.5` (matches dev venv).
   - PyYAML: `6.0.3` installed and verified.
2. **Action Commit SHA Resolution**:
   - Queried GitHub API via curl for latest immutable 40-character SHAs:
     - `actions/checkout@v4.2.2`: `11bd71901bbe5b1630ceea73d27597364c9af683`
     - `actions/setup-python@v5.4.0`: `42375524e23c412d93fb67b49958b491fce71c38`
     - `actions/setup-node@v4.2.0`: `1d0ff469b7ec7b3cb9d8673fde0c81c44821de2a`
     - `actions/upload-artifact@v4.6.1`: `4cec3d8aa04e39d1a68397de0c4cd6fb9dce8ec1`
     - `actions/download-artifact@v4.1.9`: `cc203385981b70ca67e1cc392babf9cc229d5806`
     - `actions/github-script@v7.0.1`: `60a0d83039c74a4aee543508d2ffcb1c3799cdea`
3. **YAML Syntax & Structural Linting**:
   - Validated all `.github/workflows/*.yml` with `yaml.safe_load`: 0 errors.
   - Verified structural validity and SHA pinning (all actions pinned to 40-hex SHAs): Passed.
   - Note on `actionlint`: `actionlint` binary was not present in the Linux VM environment; full AST structural parsing and SHA regex verification were executed via Python.
4. **Anticheat Test Execution**:
   - Command: `pytest tests/anticheat -v -q -p no:randomly`
   - Result: 4 passed in 1.46s (all static invariants maintained).
5. **Cardforge Test Suite**:
   - Command: `pytest cardforge/tests -q -p no:randomly`
   - Result: 42 passed in 11.23s.

---

## 4. Contract Deviations

**None**. All workflows strictly follow the interface contracts in `_agents/remediation/WAVE3_CONTRACTS.md` §0, §3, and §4, and the security rules in `REMEDIATION_PLAN.md` §3.4.

---

## 5. Requests for Other Work Packages & Maintainers

1. **Maintainer / Orchestrator**:
   - Run `python -m evals.vault keygen --out /tmp/recipeboxer_ci_age.key` to create the CI age identity.
   - Add the public key to `evals/vault/recipients.txt` via `python -m evals.vault add-recipient <pubkey>` and rekey the vault (`python -m evals.vault rekey evals/vault`).
   - Add repository secret `CI_AGE_IDENTITY` with the secret key content.
   - Create GitHub Environment `eval-live` with required maintainer reviewers.
   - Add environment secret `CI_GEMINI_API_KEY` with provider-side spend limits ($5–$10/mo).
   - Create the PR label `run-eval`.
2. **WP-E3E4**:
   - Provide the implementation of `evals.harness.run` and `evals.score` matching the CLI signatures specified in `WAVE3_CONTRACTS.md` §3/§4 and called by `eval-run.yml` and `eval-score.yml`.

---

## 6. Risks & Mitigations

1. **Risk**: Malicious fork PR consumes Gemini API quota.
   - **Mitigation**: PRs cannot run `eval-run` until a maintainer applies `run-eval` AND approves the `eval-live` environment deployment. Provider-side budget caps enforce hard limits.
2. **Risk**: Untrusted PR attempts to exfiltrate evaluation ground truth.
   - **Mitigation**: Untrusted runner runs with no vault identity. Scoring runs in `eval-score.yml` exclusively on trusted `main` branch code.
3. **Risk**: Untrusted PR poisons artifact payload to execute arbitrary code during scoring.
   - **Mitigation**: `eval-score.yml` validates artifact file sizes, JSON schemas, and identifiers without `eval()` or shell interpolation before passing paths to `evals.score`.
