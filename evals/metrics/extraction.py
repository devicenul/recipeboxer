"""Recipe extraction scoring and aggregation metrics for RecipeBoxer evals.

Pure functions with no dependencies on `recipeboxer`.
Uses scipy.optimize.linear_sum_assignment for line-level Hungarian matching.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
import numpy as np
from scipy.optimize import linear_sum_assignment

from evals.metrics.text import (
    cer,
    normalize_text,
    normalize_unit,
    quantities_match,
    UNIT_SYNONYMS,
)

# Regex matching [illegible] or [illegible: reason]
ILLEGIBLE_REGEX = re.compile(r"\[illegible(?::[^\]]*)?\]", re.IGNORECASE)


def _extract_ingredient_line_info(item: Any) -> Tuple[str, Optional[str], Optional[str]]:
    """Extracts (raw_text, quantity, unit) from an ingredient representation.

    Supports both dictionary schema inputs and raw text strings.
    """
    if item is None:
        return "", None, None

    if isinstance(item, dict):
        raw = str(item.get("raw") or item.get("item") or "").strip()
        qty = item.get("quantity")
        unit = item.get("unit")
        qty_str = str(qty).strip() if qty is not None else None
        unit_str = str(unit).strip() if unit is not None else None
        if not raw and (qty_str or unit_str):
            raw = f"{qty_str or ''} {unit_str or ''} {item.get('item', '')}".strip()
        return raw, qty_str, unit_str

    s = str(item).strip()
    if not s:
        return "", None, None

    # Parse leading quantity and unit from string
    m = re.match(
        r"^((?:\d+[\s\-]+)?\d+\s*\/\s*\d+|\d+(?:\.\d+)?)\s*([a-zA-Z\.]+)?\b(.*)$",
        s,
    )
    if m:
        qty_str = m.group(1).strip()
        potential_unit = m.group(2).strip() if m.group(2) else None
        if potential_unit:
            norm_u = normalize_unit(potential_unit)
            if norm_u in UNIT_SYNONYMS.values() or potential_unit.lower() in UNIT_SYNONYMS:
                unit_str = potential_unit
            else:
                unit_str = None
        else:
            unit_str = None
        return s, qty_str, unit_str

    return s, None, None


def _evaluate_illegible_precision(
    pred_texts: List[str],
    gold_texts: List[str],
    gold_verbatim: Optional[List[str]] = None,
) -> Optional[float]:
    """Evaluates precision of `[illegible]` markers in prediction.

    Approximation documentation:
    An `[illegible]` marker in the prediction counts as correct if:
    1. The corresponding aligned span in ground-truth text or verbatim contains
       an explicit `[illegible]`, `?`, or unreadable marker.
    2. Or ground-truth verbatim has `[illegible]` anywhere in the corresponding card/section.
    3. Or the marker aligns with a span in gold that is marked illegible or deviates
       from standard legible recipe vocabulary.
    If no `[illegible]` markers were predicted, returns None.

    Args:
        pred_texts: List of predicted text lines across all fields.
        gold_texts: List of ground-truth text lines across all fields.
        gold_verbatim: Optional verbatim transcription lines from card faces.

    Returns:
        Precision float in [0.0, 1.0], or None if no markers were predicted.
    """
    all_pred_markers: List[Tuple[int, str]] = []  # (line_idx, marker)
    for idx, line in enumerate(pred_texts):
        matches = ILLEGIBLE_REGEX.findall(line)
        for m in matches:
            all_pred_markers.append((idx, m))

    if not all_pred_markers:
        return None

    # Check for gold illegibility evidence
    verbatim_has_illegible = False
    if gold_verbatim:
        for v in gold_verbatim:
            if v and (ILLEGIBLE_REGEX.search(v) or "[unclear" in v.lower()):
                verbatim_has_illegible = True
                break

    gold_illegible_lines = set()
    for idx, line in enumerate(gold_texts):
        if line and (ILLEGIBLE_REGEX.search(line) or "[unclear" in line.lower()):
            gold_illegible_lines.add(idx)

    correct_count = 0
    for pred_idx, _ in all_pred_markers:
        # If verbatim card has illegibility evidence
        if verbatim_has_illegible:
            correct_count += 1
            continue

        # If aligned or proximate gold line has illegibility marker
        if pred_idx in gold_illegible_lines or any(
            g_idx in gold_illegible_lines
            for g_idx in range(max(0, pred_idx - 1), min(len(gold_texts), pred_idx + 2))
        ):
            correct_count += 1
            continue

        # If gold line is missing or empty while pred produced [illegible]
        if pred_idx >= len(gold_texts) or not gold_texts[pred_idx].strip():
            correct_count += 1
            continue

    return float(round(correct_count / len(all_pred_markers), 4))


def score_recipe(
    pred: Optional[Dict[str, Any]],
    gold_structured: Dict[str, Any],
    gold_verbatim: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Calculates comprehensive extraction accuracy metrics for a recipe.

    Metrics:
    - title_cer: Character Error Rate on recipe title
    - ingredient_p / ingredient_r / ingredient_f1: Line-level ingredient matching
      via Hungarian algorithm with CER <= 0.1 match threshold
    - qty_unit_exact: Exact quantity and normalized unit match rate on matched lines
    - direction_f1: Direction step F1 score (Hungarian CER <= 0.25)
    - direction_tau: Kendall tau rank correlation of matched direction step order
    - field_cer: Dictionary of per-field CER scores
    - hallucination_rate: Fraction of predicted lines with no gold match
    - omission_rate: Fraction of gold lines with no predicted match
    - illegible_precision: Precision of `[illegible]` markers (None if no markers)

    Args:
        pred: Predicted recipe dictionary, or None if omitted / failed.
        gold_structured: Ground-truth structured recipe dictionary.
        gold_verbatim: Optional list of verbatim text strings per card face.

    Returns:
        Structured dictionary conforming to contract §5.
    """
    # Conversion of schema-v2 recipes to the scoring dict happens in evals/scoring/evaluator.py;
    # this module stays pure (contract §5). Objects exposing to_scoring_dict() are still accepted.
    if pred is not None and hasattr(pred, "to_scoring_dict"):
        pred = pred.to_scoring_dict()

    is_omitted = pred is None or not isinstance(pred, dict)
    pred_dict: Dict[str, Any] = pred if not is_omitted else {}

    # If structured ground truth is missing (e.g. pending T3 GT), compute verbatim-only CER if available
    if gold_structured is None:
        verb_cer = None
        if gold_verbatim:
            gold_text = "\n".join([str(v).strip() for v in gold_verbatim if str(v).strip()])
            pred_lines = []
            if pred_dict:
                if pred_dict.get("title"):
                    pred_lines.append(str(pred_dict.get("title")).strip())
                for ing in pred_dict.get("ingredients", []):
                    if isinstance(ing, dict):
                        pred_lines.append(str(ing.get("raw") or ing.get("item") or "").strip())
                    else:
                        pred_lines.append(str(ing).strip())
                for d in pred_dict.get("directions", []):
                    pred_lines.append(str(d).strip())
            pred_text = "\n".join([p for p in pred_lines if p])
            if gold_text:
                verb_cer = round(cer(gold_text, pred_text, normalize=True, case_sensitive=False), 4)

        return {
            "title_cer": None,
            "ingredient_p": None,
            "ingredient_r": None,
            "ingredient_f1": None,
            "qty_unit_exact": None,
            "direction_f1": None,
            "direction_tau": None,
            "field_cer": {},
            "hallucination_rate": None,
            "omission_rate": None,
            "illegible_precision": None,
            "verbatim_cer": verb_cer,
        }

    # 1. Title CER
    gold_title = str(gold_structured.get("title", "") or "")
    pred_title = str(pred_dict.get("title", "") or "") if not is_omitted else ""
    title_cer_score = cer(gold_title, pred_title, normalize=True, case_sensitive=False)

    # 2. Ingredient Hungarian matching (CER <= 0.1)
    gold_ing_raw = gold_structured.get("ingredients", []) or []
    pred_ing_raw = pred_dict.get("ingredients", []) or []

    gold_ings = [_extract_ingredient_line_info(g) for g in gold_ing_raw]
    pred_ings = [_extract_ingredient_line_info(p) for p in pred_ing_raw]

    # Filter out empty items
    gold_ings = [g for g in gold_ings if g[0].strip()]
    pred_ings = [p for p in pred_ings if p[0].strip()]

    n_pred_ing = len(pred_ings)
    n_gold_ing = len(gold_ings)

    ing_tp = 0
    ing_qty_unit_matched = 0

    if n_pred_ing > 0 and n_gold_ing > 0:
        cost_matrix = np.zeros((n_pred_ing, n_gold_ing), dtype=np.float64)
        for i, p_info in enumerate(pred_ings):
            for j, g_info in enumerate(gold_ings):
                cost_matrix[i, j] = cer(g_info[0], p_info[0], normalize=True, case_sensitive=False)

        row_ind, col_ind = linear_sum_assignment(cost_matrix)
        for r, c in zip(row_ind, col_ind):
            err = cost_matrix[r, c]
            if err <= 0.1:  # Contract: CER <= 0.1 match
                ing_tp += 1
                p_info = pred_ings[r]
                g_info = gold_ings[c]

                # Quantity match
                qty_ok = quantities_match(p_info[1], g_info[1])
                # Unit match
                unit_ok = (normalize_unit(p_info[2]) == normalize_unit(g_info[2]))

                if qty_ok and unit_ok:
                    ing_qty_unit_matched += 1

    if n_gold_ing == 0 and n_pred_ing == 0:
        ing_p, ing_r, ing_f1 = 1.0, 1.0, 1.0
        qty_unit_exact_score = 1.0
    elif n_gold_ing == 0:
        ing_p, ing_r, ing_f1 = 0.0, 1.0, 0.0
        qty_unit_exact_score = 0.0
    elif n_pred_ing == 0:
        ing_p, ing_r, ing_f1 = 1.0 if n_gold_ing == 0 else 0.0, 0.0, 0.0
        qty_unit_exact_score = 0.0
    else:
        ing_p = ing_tp / n_pred_ing
        ing_r = ing_tp / n_gold_ing
        ing_f1 = (2 * ing_p * ing_r / (ing_p + ing_r)) if (ing_p + ing_r) > 0 else 0.0
        qty_unit_exact_score = (ing_qty_unit_matched / ing_tp) if ing_tp > 0 else 0.0

    # 3. Direction matching (CER <= 0.25) & Kendall tau
    gold_dir_raw = [str(d).strip() for d in (gold_structured.get("directions", []) or []) if str(d).strip()]
    pred_dir_raw = [str(d).strip() for d in (pred_dict.get("directions", []) or []) if str(d).strip()]

    n_pred_dir = len(pred_dir_raw)
    n_gold_dir = len(gold_dir_raw)

    dir_tp = 0
    matched_dir_pairs: List[Tuple[int, int]] = []  # (pred_idx, gold_idx)

    if n_pred_dir > 0 and n_gold_dir > 0:
        dir_cost = np.zeros((n_pred_dir, n_gold_dir), dtype=np.float64)
        for i, p_step in enumerate(pred_dir_raw):
            for j, g_step in enumerate(gold_dir_raw):
                dir_cost[i, j] = cer(g_step, p_step, normalize=True, case_sensitive=False)

        r_ind, c_ind = linear_sum_assignment(dir_cost)
        for r, c in zip(r_ind, c_ind):
            if dir_cost[r, c] <= 0.25:
                dir_tp += 1
                matched_dir_pairs.append((int(r), int(c)))

    if n_gold_dir == 0 and n_pred_dir == 0:
        dir_f1 = 1.0
        dir_tau: Optional[float] = 1.0
    elif n_gold_dir == 0:
        dir_f1 = 0.0
        dir_tau = None
    elif n_pred_dir == 0:
        dir_f1 = 0.0
        dir_tau = None
    else:
        d_p = dir_tp / n_pred_dir
        d_r = dir_tp / n_gold_dir
        dir_f1 = (2 * d_p * d_r / (d_p + d_r)) if (d_p + d_r) > 0 else 0.0

        # Kendall tau on matched direction steps
        matched_dir_pairs.sort(key=lambda x: x[0])
        gold_order = [p[1] for p in matched_dir_pairs]

        if len(gold_order) >= 2:
            concordant = 0
            discordant = 0
            for i in range(len(gold_order)):
                for j in range(i + 1, len(gold_order)):
                    if gold_order[i] < gold_order[j]:
                        concordant += 1
                    else:
                        discordant += 1
            tot = concordant + discordant
            dir_tau = (concordant - discordant) / tot if tot > 0 else 1.0
        elif len(gold_dir_raw) <= 1:
            dir_tau = 1.0
        else:
            dir_tau = None

    # 4. Per-field CER
    field_cer_dict: Dict[str, float] = {
        "title": round(title_cer_score, 4),
    }

    # Ingredients field CER
    gold_ing_text = "\n".join(g[0] for g in gold_ings)
    pred_ing_text = "\n".join(p[0] for p in pred_ings)
    field_cer_dict["ingredients"] = round(
        cer(gold_ing_text, pred_ing_text, normalize=True, case_sensitive=False), 4
    )

    # Directions field CER
    gold_dir_text = "\n".join(gold_dir_raw)
    pred_dir_text = "\n".join(pred_dir_raw)
    field_cer_dict["directions"] = round(
        cer(gold_dir_text, pred_dir_text, normalize=True, case_sensitive=False), 4
    )

    # Yield / servings
    if "yield" in gold_structured or "yield_servings" in gold_structured:
        g_yd = str(gold_structured.get("yield") or gold_structured.get("yield_servings") or "")
        p_yd = str(pred_dict.get("yield") or pred_dict.get("yield_servings") or "")
        field_cer_dict["yield"] = round(cer(g_yd, p_yd, normalize=True, case_sensitive=False), 4)

    # Source note
    if "source_note" in gold_structured or "source_notes" in gold_structured:
        g_sn = str(gold_structured.get("source_note") or gold_structured.get("source_notes") or "")
        p_sn = str(pred_dict.get("source_note") or pred_dict.get("source_notes") or "")
        field_cer_dict["source_note"] = round(cer(g_sn, p_sn, normalize=True, case_sensitive=False), 4)

    # 5. Hallucination and Omission rates
    # Total lines = ingredients + directions
    total_pred_lines = n_pred_ing + n_pred_dir
    total_gold_lines = n_gold_ing + n_gold_dir

    unmatched_pred_lines = (n_pred_ing - ing_tp) + (n_pred_dir - dir_tp)
    unmatched_gold_lines = (n_gold_ing - ing_tp) + (n_gold_dir - dir_tp)

    if total_pred_lines > 0:
        hallucination_rate = unmatched_pred_lines / total_pred_lines
    else:
        hallucination_rate = 0.0

    if total_gold_lines > 0:
        omission_rate = unmatched_gold_lines / total_gold_lines
    else:
        omission_rate = 1.0 if is_omitted else 0.0

    # 6. Illegible precision
    all_pred_lines: List[str] = [pred_title] + [p[0] for p in pred_ings] + pred_dir_raw
    all_gold_lines: List[str] = [gold_title] + [g[0] for g in gold_ings] + gold_dir_raw
    illegible_prec = _evaluate_illegible_precision(
        all_pred_lines, all_gold_lines, gold_verbatim=gold_verbatim
    )

    verb_cer = None
    if gold_verbatim:
        gold_text = "\n".join([str(v).strip() for v in gold_verbatim if str(v).strip()])
        pred_lines = [pred_title] + [p[0] for p in pred_ings] + pred_dir_raw
        pred_text = "\n".join([p for p in pred_lines if p.strip()])
        if gold_text:
            verb_cer = round(cer(gold_text, pred_text, normalize=True, case_sensitive=False), 4)

    return {
        "title_cer": round(title_cer_score, 4),
        "ingredient_p": round(ing_p, 4),
        "ingredient_r": round(ing_r, 4),
        "ingredient_f1": round(ing_f1, 4),
        "qty_unit_exact": round(qty_unit_exact_score, 4),
        "direction_f1": round(dir_f1, 4),
        "direction_tau": round(dir_tau, 4) if dir_tau is not None else None,
        "field_cer": field_cer_dict,
        "hallucination_rate": round(hallucination_rate, 4),
        "omission_rate": round(omission_rate, 4),
        "illegible_precision": illegible_prec,
        "verbatim_cer": verb_cer,
    }


def aggregate(
    rows: List[Dict[str, Any]],
    by: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Computes overall metric means and breakdowns grouped by categories.

    None / 'N/A' values are excluded from averages with an explicit count `n`.

    Args:
        rows: List of metric row dictionaries (each row can contain scores and metadata).
        by: List of grouping keys (defaults to `["deck_type", "modality", "faces"]`).

    Returns:
        Structured dictionary containing:
        - `"overall"`: Dict mapping metric names to `{"mean": float | None, "n": int}`
        - `"breakdowns"`: Dict mapping grouping keys to per-group metric dictionaries
        - `"count"`: Total number of rows evaluated
    """
    if by is None:
        by = ["deck_type", "modality", "faces"]

    def _aggregate_subset(subset: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Averages metrics across a subset of rows."""
        accumulators: Dict[str, List[float]] = {}
        nested_accumulators: Dict[str, Dict[str, List[float]]] = {}

        for row in subset:
            for k, v in row.items():
                if k in by or k in ("deck_id", "recipe_id", "pred_id", "run_id"):
                    continue

                if isinstance(v, dict):
                    # Nested metrics like field_cer
                    if k not in nested_accumulators:
                        nested_accumulators[k] = {}
                    for sub_k, sub_v in v.items():
                        if sub_k not in nested_accumulators[k]:
                            nested_accumulators[k][sub_k] = []
                        if sub_v is not None and not (isinstance(sub_v, str) and sub_v.upper() in ("N/A", "NONE")):
                            try:
                                f_val = float(sub_v)
                                if not math.isnan(f_val):
                                    nested_accumulators[k][sub_k].append(f_val)
                            except (ValueError, TypeError):
                                pass
                else:
                    if k not in accumulators:
                        accumulators[k] = []
                    if v is not None and not (isinstance(v, str) and v.upper() in ("N/A", "NONE")):
                        try:
                            f_val = float(v)
                            if not math.isnan(f_val):
                                accumulators[k].append(f_val)
                        except (ValueError, TypeError):
                            pass

        summary: Dict[str, Any] = {}
        for k, values in accumulators.items():
            n = len(values)
            mean_val = (sum(values) / n) if n > 0 else None
            summary[k] = {
                "mean": round(mean_val, 4) if mean_val is not None else None,
                "n": n,
            }

        for k, sub_dict in nested_accumulators.items():
            summary[k] = {}
            for sub_k, values in sub_dict.items():
                n = len(values)
                mean_val = (sum(values) / n) if n > 0 else None
                summary[k][sub_k] = {
                    "mean": round(mean_val, 4) if mean_val is not None else None,
                    "n": n,
                }

        return summary

    overall = _aggregate_subset(rows)

    breakdowns: Dict[str, Dict[str, Any]] = {}
    for group_key in by:
        # Collect distinct group values
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            g_val = str(row.get(group_key, "unknown"))
            if g_val not in groups:
                groups[g_val] = []
            groups[g_val].append(row)

        breakdowns[group_key] = {}
        for g_val, sub_rows in groups.items():
            breakdowns[group_key][g_val] = {
                "metrics": _aggregate_subset(sub_rows),
                "count": len(sub_rows),
            }

    return {
        "overall": overall,
        "breakdowns": breakdowns,
        "count": len(rows),
    }
