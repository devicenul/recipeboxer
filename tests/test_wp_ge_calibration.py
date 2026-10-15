"""Unit tests for LinkCalibrator, NumPy PAV isotonic regression, and calibration fitting (WP-GE, G8).

Enforces:
- Monotonicity in total_log_lr
- Probability clipping to [0.02, 0.98]
- Separate curves for set_verified in {True, False, None}
- Conservative sigmoid default when unfitted
- JSON save/load round-trip bit-identical behavior
- Strict path refusal on evals/corpora/t1..t4 and evals/vault (INV-AC1, §3.1)
- Coverage-vs-accuracy curve computation
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import numpy as np
import pytest

from recipeboxer.correlation.calibration import (
    DEFAULT_CALIBRATION_PATH,
    LinkCalibrator,
    MAX_PROB,
    MIN_PROB,
    pav,
)
from recipeboxer.models.correlation import FeatureEvidence, PairEvidence
from evals.calibration_fit import (
    assert_not_forbidden_path,
    compute_coverage_accuracy_curve,
    extract_pairs_from_deck,
    fit_and_save_calibration,
)


class TestPavIsotonicRegression:
    """Tests for the pure-NumPy Pool Adjacent Violators (PAV) algorithm."""

    def test_pav_empty_and_single_point(self) -> None:
        x_empty, y_empty = pav(np.array([]), np.array([]))
        assert len(x_empty) == 0
        assert len(y_empty) == 0

        x_single, y_single = pav(np.array([1.5]), np.array([0.7]))
        assert np.array_equal(x_single, [1.5])
        assert np.array_equal(y_single, [0.7])

    def test_pav_known_sequence(self) -> None:
        # Input y has violations: [0.2, 0.8, 0.1, 0.9]
        x = np.array([1.0, 2.0, 3.0, 4.0])
        y = np.array([0.2, 0.8, 0.1, 0.9])
        x_pts, y_pts = pav(x, y)

        # Monotonicity check
        assert np.all(np.diff(y_pts) >= 0.0)
        # Interpolated points must be non-decreasing
        grid = np.linspace(0.5, 4.5, 50)
        interp_y = np.interp(grid, x_pts, y_pts)
        assert np.all(np.diff(interp_y) >= -1e-12)

    def test_pav_random_noise_monotonicity(self) -> None:
        rng = np.random.default_rng(12345)
        for _ in range(10):
            n = rng.integers(20, 100)
            x = rng.normal(0.0, 3.0, size=n)
            y = rng.uniform(0.0, 1.0, size=n)
            x_pts, y_pts = pav(x, y)

            assert np.all(np.diff(y_pts) >= 0.0), "PAV output y_pts must be monotonically non-decreasing"

            # Dense interpolation check
            grid = np.linspace(np.min(x) - 1.0, np.max(x) + 1.0, 200)
            interp = np.interp(grid, x_pts, y_pts)
            assert np.all(np.diff(interp) >= -1e-12), "Interpolated curve must be monotonically non-decreasing"

    def test_pav_duplicate_x_aggregation(self) -> None:
        x = np.array([1.0, 1.0, 2.0, 2.0])
        y = np.array([0.0, 1.0, 0.2, 0.8])
        x_pts, y_pts = pav(x, y)
        assert np.all(np.diff(y_pts) >= 0.0)


class TestLinkCalibrator:
    """Tests for LinkCalibrator prediction, clipping, and curves."""

    def test_unfitted_conservative_sigmoid_default(self) -> None:
        cal = LinkCalibrator()

        ev_neutral = PairEvidence(a="c1", b="c2", total_log_lr=0.0)
        p_none = cal.predict(ev_neutral, set_verified=None)
        p_true = cal.predict(ev_neutral, set_verified=True)
        p_false = cal.predict(ev_neutral, set_verified=False)

        assert abs(p_none - 0.5) < 1e-4
        assert p_true > p_none
        assert p_false < p_none
        assert MIN_PROB <= p_false < p_none < p_true <= MAX_PROB

    def test_prediction_clipped_to_bounds(self) -> None:
        cal = LinkCalibrator()

        ev_extreme_high = PairEvidence(a="c1", b="c2", total_log_lr=100.0)
        ev_extreme_low = PairEvidence(a="c1", b="c2", total_log_lr=-100.0)

        assert cal.predict(ev_extreme_high, set_verified=None) == MAX_PROB
        assert cal.predict(ev_extreme_low, set_verified=None) == MIN_PROB

        assert cal.predict(ev_extreme_high, set_verified=True) == MAX_PROB
        assert cal.predict(ev_extreme_low, set_verified=False) == MIN_PROB

    def test_fit_and_predict_separate_curves(self) -> None:
        cal = LinkCalibrator()

        evidences = [
            PairEvidence(a="c1", b="c2", total_log_lr=-3.0),
            PairEvidence(a="c1", b="c3", total_log_lr=-1.0),
            PairEvidence(a="c1", b="c4", total_log_lr=1.0),
            PairEvidence(a="c1", b="c5", total_log_lr=3.0),
        ]
        labels = [False, False, True, True]
        set_verified = [False, None, None, True]

        cal.fit(evidences, labels, set_verified=set_verified)

        p_low = cal.predict(PairEvidence(a="a", b="b", total_log_lr=-2.0), set_verified=None)
        p_high = cal.predict(PairEvidence(a="a", b="b", total_log_lr=2.0), set_verified=None)

        assert p_low <= p_high
        assert MIN_PROB <= p_low <= MAX_PROB
        assert MIN_PROB <= p_high <= MAX_PROB

    def test_save_and_load_roundtrip(self) -> None:
        cal = LinkCalibrator()
        evs = [
            PairEvidence(a="c1", b="c2", total_log_lr=-2.0),
            PairEvidence(a="c1", b="c3", total_log_lr=0.0),
            PairEvidence(a="c1", b="c4", total_log_lr=2.0),
        ]
        cal.fit(evs, [False, True, True], set_verified=[False, None, True])

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            tf_path = Path(tf.name)

        try:
            cal.save(tf_path)
            loaded = LinkCalibrator.load(tf_path)

            for test_lr in [-4.0, -1.0, 0.0, 1.5, 4.0]:
                for sv in [True, False, None]:
                    orig_p = cal.predict(PairEvidence(a="x", b="y", total_log_lr=test_lr), set_verified=sv)
                    load_p = loaded.predict(PairEvidence(a="x", b="y", total_log_lr=test_lr), set_verified=sv)
                    assert abs(orig_p - load_p) < 1e-6
        finally:
            tf_path.unlink(missing_ok=True)

    def test_shipped_default_calibration_json(self) -> None:
        assert DEFAULT_CALIBRATION_PATH.exists(), f"Default calibration file missing: {DEFAULT_CALIBRATION_PATH}"
        cal = LinkCalibrator.load(DEFAULT_CALIBRATION_PATH)

        # Monotonicity across grid for each curve
        grid = np.linspace(-6.0, 6.0, 50)
        for sv in [True, False, None]:
            preds = [cal.predict(PairEvidence(a="a", b="b", total_log_lr=float(x)), set_verified=sv) for x in grid]
            assert np.all(np.diff(preds) >= -1e-6), f"Default curve for set_verified={sv} must be monotonic"
            assert all(MIN_PROB <= p <= MAX_PROB for p in preds)


class TestCalibrationFitAndSafety:
    """Tests for evals.calibration_fit safety invariants and helpers."""

    def test_refuses_forbidden_t1_t4_and_vault_paths(self) -> None:
        forbidden_cases = [
            "evals/corpora/t1/d01/card1.jpg",
            "/home/user/recipeboxer/evals/corpora/T2/d02",
            "evals/corpora/t3/d01",
            "evals/corpora/t4/deck_abc",
            "evals/vault/t1.gt.enc",
            "evals/vault/t2.gt.enc",
            "evals/vault/recipients.txt",
        ]
        for path in forbidden_cases:
            with pytest.raises(AssertionError, match="FORBIDDEN EVALUATION PATH"):
                assert_not_forbidden_path(path)

    def test_safe_paths_allowed(self) -> None:
        safe_cases = [
            "tests/fixtures/sample_cards/manifest.json",
            "recipeboxer/correlation/calibration_default.json",
            "/tmp/custom_calibration.json",
            "tests/fixtures/sample_cards/card_001.jpg",
        ]
        for path in safe_cases:
            assert_not_forbidden_path(path)

    def test_coverage_accuracy_curve_helper(self) -> None:
        probs = [0.95, 0.90, 0.85, 0.10, 0.05, 0.40]
        labels = [True, True, True, False, False, False]
        curve = compute_coverage_accuracy_curve(probs, labels, thresholds=[0.80, 0.90])

        assert len(curve) == 2
        rec80 = curve[0]
        assert rec80["threshold"] == 0.80
        assert rec80["covered_count"] == 5
        assert rec80["total_count"] == 6
        assert rec80["accuracy"] == 1.0

        rec90 = curve[1]
        assert rec90["threshold"] == 0.90
        assert rec90["covered_count"] == 3
        assert rec90["accuracy"] == 1.0

    def test_fit_and_save_calibration_synthetic(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            out_p = Path(tf.name)

        try:
            cal = fit_and_save_calibration(
                output_path=out_p,
                source="cardforge",
                seed=999,
                n_synthetic_cards=40,
            )
            assert out_p.exists()
            data = json.loads(out_p.read_text(encoding="utf-8"))
            assert data.get("schema") == "rb-cal/1"
            assert "curves" in data
        finally:
            out_p.unlink(missing_ok=True)
