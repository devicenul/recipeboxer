"""Model calibration metrics for evals.

Pure functions with no dependencies outside the standard library.
"""

from __future__ import annotations

from typing import List, Sequence


def ece(
    confidences: Sequence[float],
    correct: Sequence[bool],
    n_bins: int = 10,
) -> float:
    """Calculates Expected Calibration Error (ECE) across confidence bins.

    Partition [0, 1] into `n_bins` equal-width intervals:
        ECE = sum_{b=1}^{B} (|B_b| / N) * |acc(B_b) - conf(B_b)|

    Args:
        confidences: Predicted probability/confidence values in [0.0, 1.0].
        correct: Boolean ground-truth correctness indicators.
        n_bins: Number of equal-width probability bins (default 10).

    Returns:
        ECE float in [0.0, 1.0]. Returns 0.0 if inputs are empty.
    """
    n = len(confidences)
    if n == 0:
        return 0.0

    if len(correct) != n:
        raise ValueError(
            f"confidences and correct must have equal length: {n} != {len(correct)}"
        )

    if n_bins <= 0:
        raise ValueError(f"n_bins must be positive, got {n_bins}")

    # Initialize bins
    bin_counts = [0] * n_bins
    bin_correct = [0] * n_bins
    bin_conf_sums = [0.0] * n_bins

    for conf, is_corr in zip(confidences, correct):
        # Clamp confidence to [0.0, 1.0]
        c = max(0.0, min(1.0, float(conf)))
        # Determine bin index: interval [b/B, (b+1)/B), with 1.0 mapped to last bin
        bin_idx = min(int(c * n_bins), n_bins - 1)

        bin_counts[bin_idx] += 1
        bin_conf_sums[bin_idx] += c
        if bool(is_corr):
            bin_correct[bin_idx] += 1

    total_ece = 0.0
    for b in range(n_bins):
        count = bin_counts[b]
        if count == 0:
            continue

        acc_b = bin_correct[b] / count
        conf_b = bin_conf_sums[b] / count
        weight = count / n
        total_ece += weight * abs(acc_b - conf_b)

    return float(round(total_ece, 6))
