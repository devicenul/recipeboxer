# Work Package Completion Note: WP-GE (G8 Calibration, G9 Ablations, G13 Scale Stress)

## 1. Summary
WP-GE implements correlation probability calibration (G8), feature toggle ablations (G9), and scale & homogeneity stress testing (G13) per `WAVE5_CONTRACTS.md` and `REMEDIATION_PLAN.md` (§9, §9.1 items 5-6, §3.1).

1. **`recipeboxer/correlation/calibration.py` (`LinkCalibrator`)**:
   - Pure-NumPy Pool Adjacent Violators (`pav`) 1D isotonic regression without `sklearn`.
   - Guaranteed monotonicity in `total_log_lr`.
   - Separate calibration curves for `set_verified` states: `True`, `False`, and `None`.
   - Conservative sigmoid default fallback on `total_log_lr` with set-verification offsets (`+1.5` for `True`, `-2.0` for `False`, `0.0` for `None`).
   - Predicted probabilities strictly clipped to `[0.02, 0.98]`.
   - Full JSON serialization (`save` / `load`) adhering to schema `rb-cal/1`.
   - Default shipped calibrator: `recipeboxer/correlation/calibration_default.json`.

2. **`evals/calibration_fit.py`**:
   - Generates fresh synthetic CardForge decks (homogeneous and mixed) and/or ingests T0 sample cards.
   - Computes directed `PairEvidence` using production modules (WP-GA) or realistic synthetic doubles.
   - Fits `LinkCalibrator` and writes `calibration_default.json`.
   - Strict anti-cheat path assertion (`assert_not_forbidden_path`): refuses to read `evals/corpora/t1..t4` or `evals/vault` (raises `AssertionError`).
   - Implements `compute_coverage_accuracy_curve` helper for HITL routing analysis across confidence thresholds.

3. **`evals/ablations.py`**:
   - Drives `evals.harness.run` and `evals.score` across 8 `RB_FEATURE_TOGGLES` variants: `all_on`, `-pixels`, `-content`, `-physical`, `-capture`, `-set_verify`, `+embeddings`, `+ocr`.
   - Reports grouping metrics broken out by `deck_type` (`homogeneous` vs `mixed`), enforcing aggregate-only metrics for T1+ per feedback policy.
   - Supports `--budget-usd` spending cap per variant run.
   - Offline mode prints planned matrix table with `N/A` markers without asserting accuracy.

4. **`evals/stress.py`**:
   - Scales up to 300–500-card homogeneous decks in an isolated temporary directory (>= 70% cards on identical dominant stationery stock, ruling, and ink).
   - Runs `GroupingEngine` (production or contract double `GroupingEngineDouble`) with `ScriptedLLMClient` serving structured `SetVerification` responses.
   - Measures LLM call counts and execution latency vs $N$ to empirically verify linear $O(N \cdot k)$ scaling rather than $O(N^2)$ pairwise explosion.
   - Strictly offline with zero accuracy claims.

5. **Test Suites (`tests/test_wp_ge_calibration.py`, `tests/test_wp_ge_ablations_stress.py`)**:
   - 22 comprehensive unit tests verifying PAV correctness, monotonicity, clipping bounds, separate verification curves, save/load round-trips, path refusal, coverage curve math, ablation toggle schemas, offline formatting, and stress call-count scaling.

---

## 2. Files Changed & Owned

### Owned & Created:
- `recipeboxer/correlation/calibration.py`: `LinkCalibrator` and NumPy `pav` implementation.
- `recipeboxer/correlation/calibration_default.json`: Conservative default sigmoid calibrator.
- `evals/calibration_fit.py`: Calibration curve fitting on T0/CardForge with anti-cheat path refusal.
- `evals/ablations.py`: Ablation matrix runner with homogeneous vs mixed breakout.
- `evals/stress.py`: Scale stress runner (300-500 cards) measuring $O(N \cdot k)$ call counts.
- `tests/test_wp_ge_calibration.py`: Unit tests for PAV, calibration, and safety invariants.
- `tests/test_wp_ge_ablations_stress.py`: Unit tests for ablations matrix and stress scaling.
- `_agents/remediation/WP-GE.md`: This completion report.

### Touched Outside Ownership:
- `evals/harness/run.py`: Added `"RB_FEATURE_TOGGLES"` to `ENV_ALLOWLIST` so that SUT subprocesses receive feature toggles for ablations as specified in `WAVE5_CONTRACTS.md` line 94.

---

## 3. Commands & Verification Results

### 1. Static Anti-Cheat Invariants (`tests/anticheat/test_static_invariants.py`)
```bash
$ timeout 60 $py -m pytest -o cache_dir=$PROJECT_CACHE_DIR/pytest tests/anticheat/test_static_invariants.py -v
tests/anticheat/test_static_invariants.py::test_no_answer_key_paths PASSED [ 25%]
tests/anticheat/test_static_invariants.py::test_no_filename_semantics PASSED [ 50%]
tests/anticheat/test_static_invariants.py::test_no_fixture_content PASSED [ 75%]
tests/anticheat/test_static_invariants.py::test_no_cardforge_or_evals_import PASSED [100%]
4 passed in 1.62s
```

### 2. WP-GE Targeted Test Suite (`tests/test_wp_ge_*.py`)
```bash
$ timeout 60 $py -m pytest -o cache_dir=$PROJECT_CACHE_DIR/pytest tests/test_wp_ge_calibration.py tests/test_wp_ge_ablations_stress.py -v
tests/test_wp_ge_calibration.py::TestPavIsotonicRegression::test_pav_empty_and_single_point PASSED [  4%]
tests/test_wp_ge_calibration.py::TestPavIsotonicRegression::test_pav_known_sequence PASSED [  9%]
tests/test_wp_ge_calibration.py::TestPavIsotonicRegression::test_pav_random_noise_monotonicity PASSED [ 13%]
tests/test_wp_ge_calibration.py::TestPavIsotonicRegression::test_pav_duplicate_x_aggregation PASSED [ 18%]
tests/test_wp_ge_calibration.py::TestLinkCalibrator::test_unfitted_conservative_sigmoid_default PASSED [ 22%]
tests/test_wp_ge_calibration.py::TestLinkCalibrator::test_prediction_clipped_to_bounds PASSED [ 27%]
tests/test_wp_ge_calibration.py::TestLinkCalibrator::test_fit_and_predict_separate_curves PASSED [ 31%]
tests/test_wp_ge_calibration.py::TestLinkCalibrator::test_save_and_load_roundtrip PASSED [ 36%]
tests/test_wp_ge_calibration.py::TestLinkCalibrator::test_shipped_default_calibration_json PASSED [ 40%]
tests/test_wp_ge_calibration.py::TestCalibrationFitAndSafety::test_refuses_forbidden_t1_t4_and_vault_paths PASSED [ 45%]
tests/test_wp_ge_calibration.py::TestCalibrationFitAndSafety::test_safe_paths_allowed PASSED [ 50%]
tests/test_wp_ge_calibration.py::TestCalibrationFitAndSafety::test_coverage_accuracy_curve_helper PASSED [ 54%]
tests/test_wp_ge_calibration.py::TestCalibrationFitAndSafety::test_fit_and_save_calibration_synthetic PASSED [ 59%]
tests/test_wp_ge_ablations_stress.py::TestAblationsMatrix::test_ablation_variants_schema_and_count PASSED [ 63%]
tests/test_wp_ge_ablations_stress.py::TestAblationsMatrix::test_all_on_baseline_and_toggles PASSED [ 68%]
tests/test_wp_ge_ablations_stress.py::TestAblationsMatrix::test_offline_ablation_table_formatting_no_accuracy_assert PASSED [ 72%]
tests/test_wp_ge_ablations_stress.py::TestAblationsMatrix::test_ablation_table_with_mock_results PASSED [ 77%]
tests/test_wp_ge_ablations_stress.py::TestAblationsMatrix::test_run_ablations_offline_returns_cleanly PASSED [ 81%]
tests/test_wp_ge_ablations_stress.py::TestScaleStressBenchmark::test_homogeneous_deck_stationery_consistency PASSED [ 86%]
tests/test_wp_ge_ablations_stress.py::TestScaleStressBenchmark::test_stress_call_count_scaling_with_doubles PASSED [ 90%]
tests/test_wp_ge_ablations_stress.py::TestScaleStressBenchmark::test_stress_single_run_300_cards PASSED [ 95%]
tests/test_wp_ge_ablations_stress.py::TestScaleStressBenchmark::test_format_stress_report PASSED [100%]
22 passed in 0.28s
```

### 3. CLI Smoke Tests

**Ablations (offline mode)**:
```bash
$ python -m evals.ablations --offline
=== RecipeBoxer Planned Feature Ablation Matrix ===
| Variant | Pixels | Capture | Physical | Content | SetVerify | Embeddings | OCR | Homogeneous B-Cubed F1 | Mixed B-Cubed F1 | Status |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|---|
| `all_on` | ON | ON | ON | ON | ON | off | off | N/A | N/A | N/A (offline) |
| `-pixels` | off | ON | ON | ON | ON | off | off | N/A | N/A | N/A (offline) |
| `-content` | ON | ON | ON | off | ON | off | off | N/A | N/A | N/A (offline) |
| `-physical` | ON | ON | off | ON | ON | off | off | N/A | N/A | N/A (offline) |
| `-capture` | ON | off | ON | ON | ON | off | off | N/A | N/A | N/A (offline) |
| `-set_verify` | ON | ON | ON | ON | off | off | off | N/A | N/A | N/A (offline) |
| `+embeddings` | ON | ON | ON | ON | ON | ON | off | N/A | N/A | N/A (offline) |
| `+ocr` | ON | ON | ON | ON | ON | off | ON | N/A | N/A | N/A (offline) |
```

**Scale Stress Sweep ($N \in [50, 100, 200, 300]$)**:
```bash
$ python -m evals.stress --sweep
=== Scale & Homogeneity Stress Benchmark (WP-GE, G13) ===
| Cards (N) | Screening (k) | LLM Calls | Calls / N | Latency (s) | Scaling Verified |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 50 | 5 | 50 | 1.00 | 0.001s | PASS (~O(N*k)) |
| 100 | 5 | 100 | 1.00 | 0.001s | PASS (~O(N*k)) |
| 200 | 5 | 200 | 1.00 | 0.004s | PASS (~O(N*k)) |
| 300 | 5 | 300 | 1.00 | 0.004s | PASS (~O(N*k)) |

*Note: Tests measure call counts and latency scaling only. No accuracy claims are asserted offline.*
```

---

## 4. Contract Deviations
None. All implementations conform to `WAVE5_CONTRACTS.md` §1.2, §3, and `REMEDIATION_PLAN.md` §9, §9.1.

## 5. Requests for Other WPs
- **WP-GD (`recipeboxer/correlation/engine.py`)**: When initializing `GroupingEngine`, load `DEFAULT_CALIBRATION_PATH = recipeboxer/correlation/calibration_default.json` via `LinkCalibrator.load()` or `LinkCalibrator.default()` to calibrate top-k link probabilities and route links below threshold to HITL.

## 6. Risks & Mitigations
- **Low calibration sample count on custom runs**: If empirical pair data lacks examples for `set_verified=False` or `True`, `LinkCalibrator` gracefully falls back to the conservative sigmoid default with documented prior shifts (`+1.5` / `-2.0`), guaranteeing valid monotonic predictions clipped to `[0.02, 0.98]`.
