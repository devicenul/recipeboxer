# WP-00b: Contaminated Baseline Report

- **Work Package**: WP-00b (Contaminated Baseline)
- **Phase**: Phase 0 (Guardrails, Baseline, Cost Calibration)
- **Status**: Completed
- **Date**: 2026-10-14
- **Git Commit**: `1272a55f0be19a82c509ccfbbec9811fdd072f85`
- **Git Branch**: `remediation/phase0`
- **Artifacts Created**:
  - [`evals/baselines/pre_remediation.json`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/evals/baselines/pre_remediation.json)
  - [`evals/baselines/README.md`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/evals/baselines/README.md)
  - [`_agents/remediation/WP-00b.md`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/_agents/remediation/WP-00b.md)

---

## 1. Commands Executed

All commands executed in the Linux VM shell with `Cwd=/storage/emulated/10/AntiGravity/Workspaces/recipeboxer`. In compliance with project rules:
- Caches and virtual environments were isolated to `$HOME/.cache/projects/recipeboxer-ba754a91/`.
- No modifications were made to production code, test code, or `setup.sh`.
- `GEMINI_API_KEY` and `RECIPEBOXER_GEMINI_API_KEY` were unset.
- Intermediate logs, JUnit XML, and benchmark reports were written under `$PROJECT_CACHE_DIR/wp00b/`.

### 1.1 Full Offline Test Suite Execution
```bash
PROJECT_CACHE_DIR="$HOME/.cache/projects/$(basename "$PWD")-$(pwd -P | sha1sum | cut -c1-8)"
mkdir -p "$PROJECT_CACHE_DIR/wp00b/reports"
mkdir -p "$PROJECT_CACHE_DIR/pycache"
mkdir -p "$PROJECT_CACHE_DIR/pytest"
unset GEMINI_API_KEY RECIPEBOXER_GEMINI_API_KEY

PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" "$PROJECT_CACHE_DIR/venv/bin/python" -m pytest \
    -o cache_dir="$PROJECT_CACHE_DIR/pytest" \
    --ignore=tests/anticheat \
    -q \
    -p no:randomly \
    --junitxml="$PROJECT_CACHE_DIR/wp00b/junit.xml" \
    tests/ \
    > "$PROJECT_CACHE_DIR/wp00b/test_run.log" 2>&1
```

### 1.2 Benchmark Run (a): Default (Obfuscation Active, Offline Mock Judge)
```bash
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" "$PROJECT_CACHE_DIR/venv/bin/python" -m recipeboxer.evals.runner \
    -o "$PROJECT_CACHE_DIR/wp00b/reports/obfuscated" \
    > "$PROJECT_CACHE_DIR/wp00b/bench_obfuscated.log" 2>&1
```

### 1.3 Benchmark Run (b): Unobfuscated (--no-obfuscate)
```bash
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" "$PROJECT_CACHE_DIR/venv/bin/python" -m recipeboxer.evals.runner \
    --no-obfuscate \
    -o "$PROJECT_CACHE_DIR/wp00b/reports/unobfuscated" \
    > "$PROJECT_CACHE_DIR/wp00b/bench_unobfuscated.log" 2>&1
```

---

## 2. Test Suite Execution Summary

- **Total Collected**: 1,929 tests
- **Passed**: 1,908 (98.9%)
- **Failed**: 15 (1.1%)
- **Skipped**: 6 (Windows `.ps1` shell scripts & packaging binaries)
- **Errored**: 0
- **Xfailed**: 0
- **Duration**: 174.15 seconds (2m 54s)

### Pre-Existing Test Failures (15 Total)

| # | Test Node ID | Failure Category | Root Cause Analysis |
|---|---|---|---|
| 1 | `tests/test_archive_empirical_challenger.py::TestZipSlipAdversarialVectors::test_zipslip_escaping_paths_rejected[..\\..\\evil.txt]` | ZipSlip Path Traversal | On Linux, backslash `\` is treated as a regular filename character rather than path separator; archive unpacker did not normalize backslashes before validation. |
| 2 | `tests/test_archive_empirical_challenger.py::TestZipSlipAdversarialVectors::test_zipslip_escaping_paths_rejected[..\\..\\..\\evil.txt]` | ZipSlip Path Traversal | Same backslash path traversal bypass as above. |
| 3 | `tests/test_archive_empirical_challenger.py::TestZipSlipAdversarialVectors::test_zipslip_escaping_paths_rejected[assets/scans/..\\..\\..\\evil.txt]` | ZipSlip Path Traversal | Same backslash path traversal bypass as above. |
| 4 | `tests/test_archive_empirical_challenger.py::TestZipSlipAdversarialVectors::test_zipslip_escaping_paths_rejected[C:/evil.txt]` | ZipSlip Path Traversal | Windows drive letter `C:/` not recognized as absolute/escaping path on POSIX filesystem. |
| 5 | `tests/test_archive_empirical_challenger.py::TestZipSlipAdversarialVectors::test_zipslip_escaping_paths_rejected[C:\\evil.txt]` | ZipSlip Path Traversal | Windows drive letter with backslash `C:\` not recognized as escaping path on POSIX filesystem. |
| 6 | `tests/test_archive_empirical_challenger.py::TestZipSlipAdversarialVectors::test_zipslip_escaping_paths_rejected[D:/assets/scans/evil.txt]` | ZipSlip Path Traversal | Windows drive letter `D:/` not recognized as escaping path on POSIX filesystem. |
| 7 | `tests/test_browse_directory.py::TestBrowseDirectoryEndpoints::test_browse_directory_success_with_selected_path` | Missing Dependency | `ModuleNotFoundError: No module named 'tkinter'`. Python `tkinter` is not packaged in the headless Debian Linux VM. |
| 8 | `tests/test_browse_directory.py::TestBrowseDirectoryEndpoints::test_browse_directory_canceled_returns_null_path` | Missing Dependency | Same `ModuleNotFoundError: No module named 'tkinter'`. |
| 9 | `tests/test_browse_directory.py::TestBrowseDirectoryEndpoints::test_browse_directory_alias_endpoint` | Missing Dependency | Same `ModuleNotFoundError: No module named 'tkinter'`. |
| 10 | `tests/test_browse_directory.py::TestBrowseDirectoryEndpoints::test_browse_directory_get_endpoint` | Missing Dependency | Same `ModuleNotFoundError: No module named 'tkinter'`. |
| 11 | `tests/test_browse_directory.py::TestBrowseDirectoryEndpoints::test_browse_directory_get_alias_with_title` | Missing Dependency | Same `ModuleNotFoundError: No module named 'tkinter'`. |
| 12 | `tests/test_browse_directory.py::TestBrowseDirectoryEndpoints::test_browse_directory_dialog_exception_gracefully_handled` | Missing Dependency | Same `ModuleNotFoundError: No module named 'tkinter'`. |
| 13 | `tests/test_browse_directory.py::TestBrowseDirectoryEndpoints::test_browse_directory_whitespace_inputs_and_nonexistent_dir` | Missing Dependency | Same `ModuleNotFoundError: No module named 'tkinter'`. |
| 14 | `tests/test_challenger_m1_adversarial_deep.py::TestActiveProvidersIntegrity::test_pipeline_start_local_success` | Assertion Error | `assert 'HITL_PENDING' in ('PROCESSING', 'IDLE', 'INDEXING', 'EXTRACTING', 'COMPLETED')`. The pipeline immediately flagged the run as `HITL_PENDING`, which was valid behavior omitted from the test's expected status tuple. |
| 15 | `tests/test_challenger_m2_empirical_rigor.py::test_generator_deterministic_reproducibility` | Visual Rendering Drift | `AssertionError: Deterministic generation mismatches with seed 42`. Slight font rendering/rasterization differences in Pillow between platforms caused 20 out of 48 generated image SHA-256 hashes to diverge from the pre-baked fixtures. |

---

## 3. Contaminated Benchmark Results Summary

Evaluation dataset: 48 cards, 24 recipes across 6 historical modalities (`tests/fixtures/sample_cards/`).

| Metric Dimension | Obfuscated (Default Runner) | Unobfuscated (`--no-obfuscate`) | Target Benchmark |
|:---|:---:|:---:|:---:|
| **Pairing Precision** | 100.0% (`1.0`) | 100.0% (`1.0`) | $\ge 85.0\%$ |
| **Pairing Recall** | 100.0% (`1.0`) | 100.0% (`1.0`) | $\ge 85.0\%$ |
| **Pairing F1 Score** | **100.0%** (`1.0`) | **100.0%** (`1.0`) | $\ge 85.0\%$ |
| **Multi-Card Accuracy** | 100.0% (`1.0`) | 100.0% (`1.0`) | $\ge 85.0\%$ |
| **Standalone Accuracy** | 100.0% (`1.0`) | 100.0% (`1.0`) | $\ge 90.0\%$ |
| **Overall CER** | **78.67%** (`0.7867`) | **0.00%** (`0.0`) | $\le 15.0\%$ |
| **Overall WER** | **90.49%** (`0.9049`) | **0.00%** (`0.0`) | $\le 25.0\%$ |
| **Ingredient CER** | **80.28%** (`0.8028`) | **0.00%** (`0.0`) | $\le 15.0\%$ |
| **Ingredient WER** | **90.36%** (`0.9036`) | **0.00%** (`0.0`) | $\le 20.0\%$ |
| **Direction CER** | **84.56%** (`0.8456`) | **0.00%** (`0.0`) | $\le 15.0\%$ |
| **Direction WER** | **93.44%** (`0.9344`) | **0.00%** (`0.0`) | $\le 25.0\%$ |
| **Fraction Accuracy** | **39.23%** (`0.3923`) | **100.0%** (`1.0`) | $\ge 90.0\%$ |
| **Schema Conformity Rate** | 100.0% (`1.0`) | 100.0% (`1.0`) | $\ge 95.0\%$ |
| **LLM-as-a-Judge Aggregate** | **3.50 / 5.0** | **4.96 / 5.0** | $\ge 3.5 / 5.0$ |
| **Execution Duration** | 3.34 seconds | 0.10 seconds | N/A |

### Qualitative Rubric Dimension Scores (LLM-as-a-Judge)

| Rubric Dimension | Obfuscated Score | Unobfuscated Score | Target |
|:---|:---:|:---:|:---:|
| **Semantic Culinary Fidelity** | 2.00 / 5.0 | 5.00 / 5.0 | $\ge 3.5$ |
| **Vintage Headnote Preservation** | 5.00 / 5.0 | 5.00 / 5.0 | $\ge 3.0$ |
| **Ingredient-Direction Cross-Consistency** | 2.00 / 5.0 | 4.83 / 5.0 | $\ge 4.0$ |
| **Step Ordering Logic** | 5.00 / 5.0 | 5.00 / 5.0 | $\ge 4.0$ |

---

## 4. Anomalies & Cheat Mechanics Observed

1. **Unobfuscated Execution Duration (0.10s)**:
   Extracting 24 recipes from 48 images in 0.10 seconds with 0.00% CER is physical proof of answer-key retrieval rather than visual processing. The extractor checked image hashes against `manifest.json` and retrieved pre-rendered ground-truth recipes directly.
2. **Obfuscated Degradation Collapse**:
   When obfuscation was enabled, filename lookups failed. Instead of visual OCR, the system fell back to canned placeholders (`Recipe from card_<obf_hash>.jpg`), causing CER to spike to 78.67% and WER to 90.49%.
3. **Persistent Correlation Leaks**:
   Pairing F1 remained 100.0% even under obfuscation because original SHA-256 hashes and pre-computed ground-truth visual characteristics (`paper_tone`, `ruling_type`, `ink_type`) were passed directly to `PairingOptimizer` (`runner.py:467-478`).
4. **Mock Judge Artifacts**:
   The offline mock judge assigned default scores (3.50 for degraded recipes, 4.96 for ground-truth text) without interacting with any multimodal LLM.

---

## 5. Verification Checklist

- [x] Full Python test suite executed offline (1,929 tests collected, 1,908 passed, 15 failed, 6 skipped).
- [x] All 15 pre-existing failing node IDs recorded with root-cause categorization.
- [x] Benchmark executed twice (default obfuscated vs. `--no-obfuscate`).
- [x] JSON reports saved to `$PROJECT_CACHE_DIR/wp00b/reports/`.
- [x] `evals/baselines/pre_remediation.json` written with exact metric names and contaminated warning label.
- [x] `evals/baselines/README.md` created explaining contaminated status and upcoming Gate A `honest_v0.json`.
- [x] `_agents/remediation/WP-00b.md` completed.
- [x] No modifications to production/test code, `setup.sh`, or git index.
