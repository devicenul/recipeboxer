"""Quantitative scoring engine evaluating SUT predictions against ground truth (WP-E4).

Computes:
- Grouping metrics (B-Cubed, ARI, exact cluster acc, order tau) via evals.metrics.grouping.
- Hungarian recipe matching on card-overlap IoU via match_recipes.
- Extraction quality metrics (title CER, ingredient F1, direction F1, omission/hallucination)
  via evals.metrics.extraction.
- Calibration error (ECE) via evals.metrics.calibration.
- Ops and token expenditure metrics via evals.metrics.ops.
- Category breakdowns (deck_type, modality, faces count).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from evals.metrics.calibration import ece
from evals.metrics.extraction import aggregate as aggregate_extraction, score_recipe
from evals.metrics.grouping import match_recipes, score_grouping
from evals.metrics.ops import summarize_ledger

logger = logging.getLogger("evals.scoring.evaluator")


def evaluate_deck(
    deck_output: Dict[str, Any],
    deck_gt: Dict[str, Any],
) -> Dict[str, Any]:
    """Scores a single deck's predicted recipes against ground truth."""
    pred_recipes = deck_output.get("recipes", [])
    gold_recipes = deck_gt.get("recipes", [])

    pred_clusters = [r.get("image_ids", []) for r in pred_recipes]
    gold_clusters = [r.get("faces", []) for r in gold_recipes]

    # 1. Grouping metrics
    grouping_scores = score_grouping(pred_clusters, gold_clusters)

    # 2. Hungarian matching on card-overlap IoU
    matches = match_recipes(pred_clusters, gold_clusters)

    # 3. Extraction metrics per matched recipe
    recipe_rows: List[Dict[str, Any]] = []
    confidences: List[float] = []
    correct_list: List[bool] = []

    for pred_idx, gold_idx, iou in matches:
        gold_rec = gold_recipes[gold_idx] if gold_idx is not None else None
        pred_rec_obj = pred_recipes[pred_idx] if pred_idx is not None else None

        gold_struct = gold_rec.get("structured", {}) if gold_rec else {}
        gold_verb = gold_rec.get("verbatim", []) if gold_rec else None
        pred_struct = pred_rec_obj.get("recipe", {}) if pred_rec_obj else None

        row = score_recipe(
            pred=pred_struct,
            gold_structured=gold_struct,
            gold_verbatim=gold_verb,
        )

        # Attach metadata for category aggregation
        if gold_rec:
            row["recipe_id"] = gold_rec.get("recipe_id", "")
            faces = gold_rec.get("faces", [])
            row["faces_count"] = str(len(faces))
            row["difficulty"] = gold_rec.get("difficulty", {}).get("legibility", "good")

        if pred_rec_obj:
            conf = float(pred_rec_obj.get("confidence", 1.0))
            confidences.append(conf)
            is_correct = (iou >= 0.5) and (row.get("ingredient_f1", 0.0) >= 0.7)
            correct_list.append(is_correct)

        recipe_rows.append(row)

    # Aggregate extraction scores
    extraction_summary = aggregate_extraction(recipe_rows)

    # Calibration ECE
    calibration_ece = None
    if confidences and correct_list:
        try:
            calibration_ece = ece(confidences, correct_list)
        except Exception:
            calibration_ece = None

    return {
        "deck_id": deck_output.get("deck_id", deck_gt.get("deck_id", "d01")),
        "grouping": grouping_scores,
        "extraction": extraction_summary,
        "calibration_ece": calibration_ece,
        "recipe_rows": recipe_rows,
        "matches": matches,
    }


def score_evaluation(
    outputs_data: Dict[str, Any],
    gt_data: Dict[str, Any],
    ledger_data: Optional[Dict[str, Any]] = None,
    feedback: str = "aggregate",
) -> Dict[str, Any]:
    """Computes comprehensive evaluation metrics and constructs report representation."""
    tier = outputs_data.get("tier", gt_data.get("tier", "T1"))
    status = outputs_data.get("status", "ok")
    run_id = outputs_data.get("run_id", "unknown_run")
    git_commit = outputs_data.get("git_commit", "unknown")
    track = outputs_data.get("track", "end_to_end")
    variant = outputs_data.get("variant", "opaque")
    model_profile = outputs_data.get("model_profile", "balanced")

    # If status is no_live_model or cheat_detected, bypass quantitative metrics
    if status in ("no_live_model", "cheat_detected"):
        ops_summary = summarize_ledger(ledger_data or {}) if ledger_data else {}
        return {
            "run_id": run_id,
            "tier": tier,
            "status": status,
            "git_commit": git_commit,
            "track": track,
            "variant": variant,
            "model_profile": model_profile,
            "metrics": {
                "grouping": {},
                "extraction": {},
                "calibration": {},
                "judge": {},
                "ops": ops_summary,
            },
            "decks": [],
            "status_message": f"Pipeline terminated with status: {status}",
        }

    # Index GT decks by deck_id
    gt_decks = {d.get("deck_id", "d01"): d for d in gt_data.get("decks", [])}
    out_decks = outputs_data.get("decks", [])

    deck_results: List[Dict[str, Any]] = []
    all_recipe_rows: List[Dict[str, Any]] = []
    all_confidences: List[float] = []
    all_correct: List[bool] = []
    all_grouping_scores: List[Dict[str, Any]] = []

    for out_d in out_decks:
        did = out_d.get("deck_id", "d01")
        if did in gt_decks:
            gt_d = gt_decks[did]
        elif len(gt_decks) == 1:
            gt_d = next(iter(gt_decks.values()))
        else:
            logger.warning(f"No matching GT deck found for '{did}'; skipping.")
            continue

        res = evaluate_deck(out_d, gt_d)
        deck_results.append(res)
        all_recipe_rows.extend(res.get("recipe_rows", []))
        all_grouping_scores.append(res.get("grouping", {}))

    # Overall grouping aggregate
    overall_grouping: Dict[str, Any] = {}
    if all_grouping_scores:
        grouping_keys = all_grouping_scores[0].keys()
        for k in grouping_keys:
            vals = [s[k] for s in all_grouping_scores if s.get(k) is not None]
            overall_grouping[k] = sum(vals) / len(vals) if vals else 0.0

    # Overall extraction aggregate
    overall_extraction = aggregate_extraction(all_recipe_rows)

    # Overall calibration ECE
    overall_ece = None
    if all_confidences and all_correct:
        try:
            overall_ece = ece(all_confidences, all_correct)
        except Exception:
            pass

    # Category aggregations
    category_metrics: Dict[str, Any] = {}
    if all_recipe_rows:
        # Group by faces_count
        by_faces: Dict[str, List[Dict[str, Any]]] = {}
        for row in all_recipe_rows:
            fc = row.get("faces_count", "1")
            by_faces.setdefault(f"{fc}-card", []).append(row)

        for cat_label, rows in by_faces.items():
            agg = aggregate_extraction(rows)
            category_metrics[cat_label] = {
                "ingredient_f1": agg.get("ingredient_f1"),
                "title_cer": agg.get("title_cer"),
                "direction_f1": agg.get("direction_f1"),
                "bcubed_f1": overall_grouping.get("bcubed_f1"),
            }

    # Ops ledger summary
    effective_ledger = ledger_data or outputs_data.get("ledger") or {}
    ops_summary = summarize_ledger(effective_ledger)

    report = {
        "schema": "rb-report/1",
        "run_id": run_id,
        "tier": tier,
        "status": status,
        "git_commit": git_commit,
        "track": track,
        "variant": variant,
        "model_profile": model_profile,
        "metrics": {
            "grouping": overall_grouping,
            "extraction": overall_extraction,
            "calibration": {"ece": overall_ece},
            "judge": {},
            "ops": ops_summary,
        },
        "categories": category_metrics,
        "decks": deck_results,
    }

    return report


evaluate_predictions = score_evaluation
