"""Grouping and clustering evaluation metrics for RecipeBoxer.

Pure functions with no dependencies on `recipeboxer`.
Uses scipy.optimize.linear_sum_assignment for Hungarian bipartite matching.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import numpy as np
from scipy.optimize import linear_sum_assignment


def match_recipes(
    pred: List[List[str]],
    gold: List[List[str]],
) -> List[Tuple[Optional[int], Optional[int], float]]:
    """Matches predicted recipe clusters to gold clusters by maximizing card-overlap IoU.

    Uses Hungarian bipartite matching (scipy.optimize.linear_sum_assignment).
    Unmatched predicted clusters are returned as `(i, None, 0.0)`.
    Unmatched gold clusters are returned as `(None, j, 0.0)`.
    Pairs assigned with 0.0 IoU are treated as unmatched.

    Args:
        pred: List of predicted card clusters (each cluster is a list of card IDs).
        gold: List of ground-truth card clusters.

    Returns:
        List of tuples `(pred_idx, gold_idx, iou)` sorted deterministically.
    """
    n_pred = len(pred)
    n_gold = len(gold)

    if n_pred == 0 and n_gold == 0:
        return []
    if n_pred == 0:
        return [(None, j, 0.0) for j in range(n_gold)]
    if n_gold == 0:
        return [(i, None, 0.0) for i in range(n_pred)]

    # Compute IoU matrix
    iou_matrix = np.zeros((n_pred, n_gold), dtype=np.float64)
    pred_sets = [{Path(str(c)).name for c in p} for p in pred]
    gold_sets = [{Path(str(c)).name for c in g} for g in gold]

    for i, p_set in enumerate(pred_sets):
        for j, g_set in enumerate(gold_sets):
            intersection = len(p_set & g_set)
            union = len(p_set | g_set)
            if union > 0:
                iou_matrix[i, j] = float(intersection / union)

    # Cost matrix for Hungarian (minimize -iou)
    cost_matrix = -iou_matrix
    row_ind, col_ind = linear_sum_assignment(cost_matrix)

    matched: List[Tuple[Optional[int], Optional[int], float]] = []
    assigned_pred: Set[int] = set()
    assigned_gold: Set[int] = set()

    for r, c in zip(row_ind, col_ind):
        iou = float(iou_matrix[r, c])
        if iou > 0.0:
            matched.append((int(r), int(c), iou))
            assigned_pred.add(int(r))
            assigned_gold.add(int(c))

    # Any unassigned pred or gold with iou == 0 become unmatched
    unmatched_pred: List[Tuple[Optional[int], Optional[int], float]] = [
        (i, None, 0.0) for i in range(n_pred) if i not in assigned_pred
    ]
    unmatched_gold: List[Tuple[Optional[int], Optional[int], float]] = [
        (None, j, 0.0) for j in range(n_gold) if j not in assigned_gold
    ]

    all_matches = matched + unmatched_pred + unmatched_gold
    all_matches.sort(key=lambda m: (m[0] if m[0] is not None else float("inf"),
                                    m[1] if m[1] is not None else float("inf")))
    return all_matches


def bcubed_metrics(
    pred: List[List[str]],
    gold: List[List[str]],
) -> Tuple[float, float, float]:
    """Calculates B-cubed Precision, Recall, and F1 over the card universe.

    For each card item e:
        P(e) = |C(e) ∩ L(e)| / |C(e)|
        R(e) = |C(e) ∩ L(e)| / |L(e)|
    where C(e) is e's predicted cluster and L(e) is e's gold cluster.

    Args:
        pred: Predicted card clusters.
        gold: Ground-truth card clusters.

    Returns:
        (precision, recall, f1) tuple.
    """
    pred_sets = [set(p) for p in pred]
    gold_sets = [set(g) for g in gold]

    # Map each card to its cluster set
    pred_map: Dict[str, Set[str]] = {}
    for p_set in pred_sets:
        for c in p_set:
            pred_map[c] = p_set

    gold_map: Dict[str, Set[str]] = {}
    for g_set in gold_sets:
        for c in g_set:
            gold_map[c] = g_set

    all_cards = set(pred_map.keys()) | set(gold_map.keys())
    if not all_cards:
        return 1.0, 1.0, 1.0

    precisions: List[float] = []
    recalls: List[float] = []

    for c in all_cards:
        c_pred = pred_map.get(c, set())
        c_gold = gold_map.get(c, set())

        inter = len(c_pred & c_gold)
        p = inter / len(c_pred) if len(c_pred) > 0 else 0.0
        r = inter / len(c_gold) if len(c_gold) > 0 else 0.0

        precisions.append(p)
        recalls.append(r)

    p_avg = float(sum(precisions) / len(precisions))
    r_avg = float(sum(recalls) / len(recalls))

    if p_avg + r_avg > 0:
        f1 = float(2 * p_avg * r_avg / (p_avg + r_avg))
    else:
        f1 = 0.0

    return p_avg, r_avg, f1


def adjusted_rand_index(
    pred: List[List[str]],
    gold: List[List[str]],
) -> float:
    """Computes the Adjusted Rand Index (ARI) directly without sklearn.

    Supports arbitrary partitions, singletons, and empty boundaries.

    Args:
        pred: Predicted clusters.
        gold: Ground truth clusters.

    Returns:
        ARI float value <= 1.0 (1.0 = identical clustering).
    """
    pred_sets = [set(p) for p in pred if p]
    gold_sets = [set(g) for g in gold if g]

    pred_map: Dict[str, int] = {}
    for idx, p_set in enumerate(pred_sets):
        for c in p_set:
            pred_map[c] = idx

    gold_map: Dict[str, int] = {}
    for idx, g_set in enumerate(gold_sets):
        for c in g_set:
            gold_map[c] = idx

    all_cards = sorted(list(set(pred_map.keys()) | set(gold_map.keys())))
    n_items = len(all_cards)

    if n_items <= 1:
        return 1.0

    # Expand clusters for any items omitted as singletons
    next_pred_id = len(pred_sets)
    for c in all_cards:
        if c not in pred_map:
            pred_map[c] = next_pred_id
            pred_sets.append({c})
            next_pred_id += 1

    next_gold_id = len(gold_sets)
    for c in all_cards:
        if c not in gold_map:
            gold_map[c] = next_gold_id
            gold_sets.append({c})
            next_gold_id += 1

    # Contingency table n_ij
    n_pred_clusters = len(pred_sets)
    n_gold_clusters = len(gold_sets)
    contingency = np.zeros((n_pred_clusters, n_gold_clusters), dtype=np.int64)

    for c in all_cards:
        i = pred_map[c]
        j = gold_map[c]
        contingency[i, j] += 1

    # comb(n, 2) = n * (n - 1) / 2
    def comb2(n: int) -> int:
        return (n * (n - 1)) // 2 if n >= 2 else 0

    sum_comb_nij = sum(comb2(int(x)) for x in contingency.flat)
    a_row_sums = np.sum(contingency, axis=1)
    b_col_sums = np.sum(contingency, axis=0)

    sum_comb_a = sum(comb2(int(a)) for a in a_row_sums)
    sum_comb_b = sum(comb2(int(b)) for b in b_col_sums)

    comb_total = comb2(n_items)
    if comb_total == 0:
        return 1.0

    expected_index = (sum_comb_a * sum_comb_b) / comb_total
    max_index = 0.5 * (sum_comb_a + sum_comb_b)
    denominator = max_index - expected_index

    if denominator == 0.0:
        # If both are all singletons or both are 1 giant cluster and match
        return 1.0 if sum_comb_nij == expected_index else 0.0

    return float((sum_comb_nij - expected_index) / denominator)


def kendall_tau_order(
    pred: List[List[str]],
    gold: List[List[str]],
    matches: Optional[List[Tuple[Optional[int], Optional[int], float]]] = None,
) -> float:
    """Computes mean Kendall tau rank correlation of card order within matched multi-card clusters.

    For each matched pair of clusters with >= 2 common cards:
        tau = (concordant_pairs - discordant_pairs) / total_pairs

    Args:
        pred: Predicted card clusters in reading order.
        gold: Ground-truth card clusters in reading order.
        matches: Optional precomputed list from `match_recipes`.

    Returns:
        Mean tau in [-1.0, 1.0], or 1.0 if no multi-card matched clusters exist.
    """
    if matches is None:
        norm_pred = [[Path(str(c)).name for c in p] for p in pred]
        norm_gold = [[Path(str(c)).name for c in g] for g in gold]
        matches = match_recipes(norm_pred, norm_gold)

    tau_scores: List[float] = []

    for p_idx, g_idx, iou in matches:
        if p_idx is None or g_idx is None:
            continue

        p_cards = pred[p_idx]
        g_cards = gold[g_idx]

        g_order: Dict[str, int] = {}
        for idx, c in enumerate(g_cards):
            c_str = str(c)
            g_order[c_str] = idx
            g_order[Path(c_str).name] = idx

        common_pred: List[str] = []
        seen: Set[str] = set()
        for c in p_cards:
            c_str = str(c)
            c_name = Path(c_str).name
            matched_key = c_name if c_name in g_order else (c_str if c_str in g_order else None)
            if matched_key and matched_key not in seen:
                common_pred.append(matched_key)
                seen.add(matched_key)

        if len(common_pred) < 2:
            continue

        concordant = 0
        discordant = 0
        n = len(common_pred)

        for i in range(n):
            for j in range(i + 1, n):
                card_i = common_pred[i]
                card_j = common_pred[j]
                if g_order[card_i] < g_order[card_j]:
                    concordant += 1
                elif g_order[card_i] > g_order[card_j]:
                    discordant += 1

        total = concordant + discordant
        if total > 0:
            tau_scores.append((concordant - discordant) / total)

    if not tau_scores:
        has_gold_multicard = any(len(g) >= 2 for g in gold)
        if has_gold_multicard and len(pred) > 0:
            return 0.0
        return 1.0

    return float(sum(tau_scores) / len(tau_scores))


def false_merge_and_split_rates(
    pred: List[List[str]],
    gold: List[List[str]],
) -> Tuple[float, float]:
    """Calculates false-merge rate and false-split rate across pairs of cards.

    - False merge: pair of cards from DIFFERENT gold recipes merged into the same pred recipe.
      false_merge_rate = false_merges / total_different_gold_pairs
    - False split: pair of cards from the SAME gold recipe split into different pred recipes.
      false_split_rate = false_splits / total_same_gold_pairs

    Args:
        pred: Predicted clusters.
        gold: Ground truth clusters.

    Returns:
        (false_merge_rate, false_split_rate)
    """
    pred_map: Dict[str, int] = {}
    for idx, p in enumerate(pred):
        for c in p:
            pred_map[c] = idx

    gold_map: Dict[str, int] = {}
    for idx, g in enumerate(gold):
        for c in g:
            gold_map[c] = idx

    all_cards = sorted(list(set(pred_map.keys()) | set(gold_map.keys())))
    n_cards = len(all_cards)

    if n_cards < 2:
        return 0.0, 0.0

    same_gold_count = 0
    diff_gold_count = 0
    false_splits = 0
    false_merges = 0

    for i in range(n_cards):
        c1 = all_cards[i]
        g1 = gold_map.get(c1, -1 - i)
        p1 = pred_map.get(c1, -1 - i)

        for j in range(i + 1, n_cards):
            c2 = all_cards[j]
            g2 = gold_map.get(c2, -1 - j)
            p2 = pred_map.get(c2, -1 - j)

            # Gold relationship: both cards must be in gold to evaluate gold pairing
            if g1 >= 0 and g2 >= 0:
                if g1 == g2:
                    same_gold_count += 1
                    # Check if split in pred
                    if p1 < 0 or p2 < 0 or p1 != p2:
                        false_splits += 1
                else:
                    diff_gold_count += 1
                    # Check if merged in pred
                    if p1 >= 0 and p2 >= 0 and p1 == p2:
                        false_merges += 1

    fm_rate = (false_merges / diff_gold_count) if diff_gold_count > 0 else 0.0
    fs_rate = (false_splits / same_gold_count) if same_gold_count > 0 else 0.0

    return float(fm_rate), float(fs_rate)


def standalone_accuracy(
    pred: List[List[str]],
    gold: List[List[str]],
) -> float:
    """Calculates F1 accuracy of identifying standalone (single-card) recipes.

    Args:
        pred: Predicted clusters.
        gold: Ground-truth clusters.

    Returns:
        F1 score on standalone card identification (0.0 to 1.0).
    """
    pred_standalones = {c for p in pred if len(p) == 1 for c in p}
    gt_standalones = {c for g in gold if len(g) == 1 for c in g}

    if not gt_standalones and not pred_standalones:
        return 1.0
    if not gt_standalones or not pred_standalones:
        return 0.0

    tp = len(pred_standalones & gt_standalones)
    fp = len(pred_standalones - gt_standalones)
    fn = len(gt_standalones - pred_standalones)

    p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    r = tp / (tp + fn) if (tp + fn) > 0 else 0.0

    return float(2 * p * r / (p + r)) if (p + r) > 0 else 0.0


def score_grouping(pred: List[List[str]], gold: List[List[str]]) -> Dict[str, Any]:
    """Calculates full grouping evaluation metrics.

    Keys:
        bcubed_p: B-cubed Precision
        bcubed_r: B-cubed Recall
        bcubed_f1: B-cubed F1 score
        ari: Adjusted Rand Index
        exact_cluster_acc: Fraction of gold clusters matched with exact card sets
        order_kendall_tau: Mean Kendall tau of face order in matched multi-card clusters
        standalone_acc: Standalone card identification accuracy / F1
        false_merge_rate: Rate of incorrect cluster merges
        false_split_rate: Rate of incorrect cluster splits
        n_pred: Count of predicted clusters
        n_gold: Count of ground truth clusters

    Args:
        pred: Predicted card clusters.
        gold: Ground-truth card clusters.

    Returns:
        Dictionary conforming to contract §5.
    """
    n_pred = len(pred)
    n_gold = len(gold)

    # Hungarian bipartite match
    matches = match_recipes(pred, gold)

    # Exact cluster accuracy
    if n_gold == 0:
        exact_acc = 1.0 if n_pred == 0 else 0.0
    else:
        pred_sets = [set(p) for p in pred]
        gold_sets = [set(g) for g in gold]
        n_exact = sum(
            1 for p_idx, g_idx, _ in matches
            if p_idx is not None and g_idx is not None and pred_sets[p_idx] == gold_sets[g_idx]
        )
        exact_acc = n_exact / n_gold

    # B-cubed metrics
    bp, br, bf1 = bcubed_metrics(pred, gold)

    # ARI
    ari = adjusted_rand_index(pred, gold)

    # Kendall tau order
    tau = kendall_tau_order(pred, gold, matches)

    # Standalone accuracy
    sa_acc = standalone_accuracy(pred, gold)

    # False merge / split rates
    fm_rate, fs_rate = false_merge_and_split_rates(pred, gold)

    return {
        "bcubed_p": round(bp, 4),
        "bcubed_r": round(br, 4),
        "bcubed_f1": round(bf1, 4),
        "ari": round(ari, 4),
        "exact_cluster_acc": round(exact_acc, 4),
        "order_kendall_tau": round(tau, 4),
        "standalone_acc": round(sa_acc, 4),
        "false_merge_rate": round(fm_rate, 4),
        "false_split_rate": round(fs_rate, 4),
        "n_pred": n_pred,
        "n_gold": n_gold,
    }
