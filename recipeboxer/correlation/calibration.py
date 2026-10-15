"""Isotonic calibration for directed pair evidence links (WP-GE, G8).

Provides LinkCalibrator using pure-NumPy Pool Adjacent Violators (PAV) isotonic
regression. Produces calibrated link probabilities monotone in total_log_lr with
separate curves for set_verified (True, False, None), and clips outputs to [0.02, 0.98].
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

from recipeboxer.models.correlation import PairEvidence

DEFAULT_CALIBRATION_PATH = Path(__file__).resolve().parent / "calibration_default.json"

MIN_PROB: float = 0.02
MAX_PROB: float = 0.98

# Conservative sigmoid offsets for set_verification states when unfitted
DEFAULT_SIGMOID_OFFSETS = {
    "true": 1.5,   # positive evidence from set verification
    "false": -2.0, # rejection from set verification
    "none": 0.0,   # neutral / unverified
}


def pav(
    x: np.ndarray,
    y: np.ndarray,
    sample_weight: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """Pool Adjacent Violators (PAV) algorithm for 1D isotonic regression.

    Given independent variable x and target binary/probability label y, computes
    a monotonically non-decreasing piecewise-linear calibration curve.

    Args:
        x: 1D array of predictor values (e.g. total_log_lr).
        y: 1D array of target labels (0 or 1, or probabilities).
        sample_weight: Optional 1D array of non-negative sample weights.

    Returns:
        (x_points, y_points): Monotonically non-decreasing curve breakpoints suitable
        for linear interpolation with np.interp.
    """
    x_arr = np.asarray(x, dtype=float)
    y_arr = np.asarray(y, dtype=float)

    if x_arr.ndim != 1 or y_arr.ndim != 1:
        raise ValueError("x and y must be 1-dimensional arrays")
    if len(x_arr) != len(y_arr):
        raise ValueError(f"Length mismatch: len(x)={len(x_arr)} != len(y)={len(y_arr)}")
    if len(x_arr) == 0:
        return np.array([], dtype=float), np.array([], dtype=float)
    if len(x_arr) == 1:
        return np.array([x_arr[0]], dtype=float), np.array([y_arr[0]], dtype=float)

    if sample_weight is None:
        w_arr = np.ones_like(y_arr, dtype=float)
    else:
        w_arr = np.asarray(sample_weight, dtype=float)
        if len(w_arr) != len(y_arr):
            raise ValueError("sample_weight length must match y")

    # 1. Sort by x ascending
    order = np.argsort(x_arr)
    x_sorted = x_arr[order]
    y_sorted = y_arr[order]
    w_sorted = w_arr[order]

    # 2. Combine duplicate x values into single weighted bins
    unique_x, inverse_indices = np.unique(x_sorted, return_inverse=True)
    bin_weights = np.bincount(inverse_indices, weights=w_sorted)
    bin_weighted_y = np.bincount(inverse_indices, weights=w_sorted * y_sorted)
    bin_mean_y = bin_weighted_y / np.maximum(bin_weights, 1e-12)

    # 3. Pool Adjacent Violators
    # Each block is [x_min, x_max, weight, mean_y]
    blocks: List[List[float]] = []
    for i in range(len(unique_x)):
        curr = [float(unique_x[i]), float(unique_x[i]), float(bin_weights[i]), float(bin_mean_y[i])]
        while blocks and blocks[-1][3] > curr[3]:
            prev = blocks.pop()
            w_sum = prev[2] + curr[2]
            y_mean = (prev[2] * prev[3] + curr[2] * curr[3]) / np.maximum(w_sum, 1e-12)
            curr = [prev[0], curr[1], w_sum, float(y_mean)]
        blocks.append(curr)

    # 4. Construct monotonically non-decreasing breakpoint curve
    x_out: List[float] = []
    y_out: List[float] = []
    for b in blocks:
        x_min, x_max, _, y_val = b
        if x_min == x_max:
            x_out.append(x_min)
            y_out.append(y_val)
        else:
            x_out.extend([x_min, x_max])
            y_out.extend([y_val, y_val])

    return np.asarray(x_out, dtype=float), np.asarray(y_out, dtype=float)


class LinkCalibrator:
    """Isotonic calibrator for directed pairwise correlation link hypotheses (G8).

    Maps pair total_log_lr and multimodal set-verification outcome into calibrated
    posterior probabilities P(A precedes B), clipped to [0.02, 0.98].
    """

    def __init__(
        self,
        curves: Optional[Dict[str, Dict[str, List[float]]]] = None,
        min_prob: float = MIN_PROB,
        max_prob: float = MAX_PROB,
        default_offsets: Optional[Dict[str, float]] = None,
    ):
        """Initializes the LinkCalibrator.

        Args:
            curves: Mapping of set_verified state key ('true', 'false', 'none') to
                dictionary with 'x' and 'y' breakpoint coordinate lists.
            min_prob: Lower probability clip bound (default 0.02).
            max_prob: Upper probability clip bound (default 0.98).
            default_offsets: Offsets added to total_log_lr in conservative sigmoid default.
        """
        self.curves: Dict[str, Dict[str, np.ndarray]] = {}
        if curves:
            for k, c in curves.items():
                x_pts = np.asarray(c.get("x", []), dtype=float)
                y_pts = np.asarray(c.get("y", []), dtype=float)
                if len(x_pts) > 0 and len(x_pts) == len(y_pts):
                    self.curves[k.lower()] = {"x": x_pts, "y": y_pts}

        self.min_prob = float(min_prob)
        self.max_prob = float(max_prob)
        self.default_offsets = dict(default_offsets or DEFAULT_SIGMOID_OFFSETS)

    @classmethod
    def _key_for_set_verified(cls, set_verified: Optional[bool]) -> str:
        """Normalizes set_verified value to string key ('true', 'false', 'none')."""
        if set_verified is True:
            return "true"
        elif set_verified is False:
            return "false"
        return "none"

    def _sigmoid_default(self, total_log_lr: float, set_verified: Optional[bool]) -> float:
        """Conservative sigmoid fallback on total_log_lr with set_verified offset."""
        key = self._key_for_set_verified(set_verified)
        offset = self.default_offsets.get(key, 0.0)
        z = total_log_lr + offset
        # Numerically stable logistic sigmoid
        if z >= 0:
            ez = math.exp(-z)
            p = 1.0 / (1.0 + ez)
        else:
            ez = math.exp(z)
            p = ez / (1.0 + ez)
        return p

    def fit(
        self,
        evidences: Sequence[Union[PairEvidence, float]],
        labels: Sequence[Union[bool, int, float]],
        set_verified: Optional[Sequence[Optional[bool]]] = None,
        sample_weight: Optional[Sequence[float]] = None,
    ) -> None:
        """Fits isotonic regression curve(s) via NumPy PAV.

        Args:
            evidences: List of PairEvidence objects or float total_log_lr values.
            labels: Ground truth binary labels (True = true continuation link, False = distractor/unlinked).
            set_verified: Optional sequence of set-verification outcomes (True/False/None) per pair.
            sample_weight: Optional sequence of non-negative sample weights.
        """
        if len(evidences) != len(labels):
            raise ValueError(f"Length mismatch: {len(evidences)} evidences != {len(labels)} labels")

        x_vals = np.array(
            [e.total_log_lr if hasattr(e, "total_log_lr") else float(e) for e in evidences],
            dtype=float,
        )
        y_vals = np.array([1.0 if bool(lbl) else 0.0 for lbl in labels], dtype=float)
        w_vals = np.asarray(sample_weight, dtype=float) if sample_weight is not None else None

        if set_verified is not None:
            if len(set_verified) != len(evidences):
                raise ValueError("set_verified length must match evidences")
            keys = [self._key_for_set_verified(sv) for sv in set_verified]

            for target_key in ("true", "false", "none"):
                indices = [i for i, k in enumerate(keys) if k == target_key]
                if len(indices) >= 2:
                    sub_x = x_vals[indices]
                    sub_y = y_vals[indices]
                    sub_w = w_vals[indices] if w_vals is not None else None
                    x_pts, y_pts = pav(sub_x, sub_y, sub_w)
                    self.curves[target_key] = {"x": x_pts, "y": y_pts}
                elif len(indices) == 1:
                    # Single point curve
                    idx = indices[0]
                    self.curves[target_key] = {
                        "x": np.array([x_vals[idx]], dtype=float),
                        "y": np.array([y_vals[idx]], dtype=float),
                    }
        else:
            # Fit single overarching curve on all available pairs
            if len(x_vals) > 0:
                x_pts, y_pts = pav(x_vals, y_vals, w_vals)
                self.curves["none"] = {"x": x_pts, "y": y_pts}

    def predict(
        self,
        ev: Union[PairEvidence, float],
        set_verified: Optional[bool] = None,
    ) -> float:
        """Predicts calibrated link probability P(A precedes B), clipped to [min_prob, max_prob].

        Args:
            ev: PairEvidence model or float total_log_lr.
            set_verified: Set-level verification outcome (True, False, None).

        Returns:
            Calibrated probability clipped to [0.02, 0.98].
        """
        total_log_lr = float(ev.total_log_lr if hasattr(ev, "total_log_lr") else ev)
        key = self._key_for_set_verified(set_verified)

        curve = self.curves.get(key)
        if curve is None and key != "none":
            # Fall back to "none" curve if specific verification state is unfitted
            curve = self.curves.get("none")

        if curve is not None and len(curve.get("x", [])) > 0:
            x_pts = curve["x"]
            y_pts = curve["y"]
            raw_p = float(np.interp(total_log_lr, x_pts, y_pts))
        else:
            raw_p = self._sigmoid_default(total_log_lr, set_verified)

        clipped_p = float(np.clip(raw_p, self.min_prob, self.max_prob))
        return clipped_p

    def save(self, path: Union[Path, str]) -> None:
        """Serializes calibrator curves and configuration to JSON file."""
        target_path = Path(path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        serializable_curves: Dict[str, Dict[str, List[float]]] = {}
        for k, c in self.curves.items():
            serializable_curves[k] = {
                "x": [round(float(v), 6) for v in c["x"]],
                "y": [round(float(v), 6) for v in c["y"]],
            }

        payload = {
            "schema": "rb-cal/1",
            "version": "1.0",
            "description": "RecipeBoxer link calibrator curves (WP-GE, G8)",
            "clip_bounds": [self.min_prob, self.max_prob],
            "default_offsets": self.default_offsets,
            "curves": serializable_curves,
        }
        # WP-UP3: keep the fitted model bundle stored alongside the legacy curves.
        if target_path.exists():
            try:
                prev = json.loads(target_path.read_text(encoding="utf-8"))
                if isinstance(prev, dict) and "up3" in prev:
                    payload["up3"] = prev["up3"]
            except Exception:
                pass
        target_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Union[Path, str]) -> "LinkCalibrator":
        """Loads calibrator curves and configuration from JSON file."""
        src_path = Path(path)
        if not src_path.exists():
            raise FileNotFoundError(f"Calibrator file not found: {src_path}")

        data = json.loads(src_path.read_text(encoding="utf-8"))
        clip_bounds = data.get("clip_bounds", [MIN_PROB, MAX_PROB])
        min_p = float(clip_bounds[0]) if len(clip_bounds) > 0 else MIN_PROB
        max_p = float(clip_bounds[1]) if len(clip_bounds) > 1 else MAX_PROB
        offsets = data.get("default_offsets", DEFAULT_SIGMOID_OFFSETS)
        curves_raw = data.get("curves", {})

        return cls(
            curves=curves_raw,
            min_prob=min_p,
            max_prob=max_p,
            default_offsets=offsets,
        )

    @classmethod
    def default(cls) -> "LinkCalibrator":
        """Returns the default shipped calibrator or conservative fallback."""
        if DEFAULT_CALIBRATION_PATH.exists():
            try:
                return cls.load(DEFAULT_CALIBRATION_PATH)
            except Exception:
                pass
        return cls()
