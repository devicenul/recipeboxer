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
from pathlib import Path
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

    deck_type = deck_gt.get("deck_type", "mixed")
    image_modality_map: Dict[str, str] = {}
    for img in deck_gt.get("images", []):
        iid = img.get("image_id", "")
        m = img.get("modality", "unknown")
        if iid:
            image_modality_map[iid] = m
            image_modality_map[Path(iid).name] = m
            image_modality_map[Path(iid).stem] = m

    n_unscored_no_structured_gt = 0

    for pred_idx, gold_idx, iou in matches:
        gold_rec = gold_recipes[gold_idx] if gold_idx is not None else None
        pred_rec_obj = pred_recipes[pred_idx] if pred_idx is not None else None

        gold_struct = gold_rec.get("structured") if gold_rec else None
        gold_verb = gold_rec.get("verbatim") if gold_rec else None

        has_structured = (
            gold_rec is not None
            and gold_struct is not None
            and isinstance(gold_struct, dict)
            and len(gold_struct) > 0
            and gold_rec.get("structured_status") not in ("pending", "pending_text_only_draft")
        )

        if gold_rec is not None and not has_structured:
            n_unscored_no_structured_gt += 1
            target_gold_struct = None
        elif gold_rec is None:
            target_gold_struct = {}
        else:
            target_gold_struct = gold_struct

        pred_struct = pred_rec_obj.get("recipe", {}) if pred_rec_obj else None
        schema_invalid = False
        if pred_struct is not None:
            if hasattr(pred_struct, "to_scoring_dict"):
                pred_struct = pred_struct.to_scoring_dict()
            elif isinstance(pred_struct, dict) and "sections" in pred_struct:
                try:
                    from recipeboxer.models.recipe import Recipe
                    pred_struct = Recipe.model_validate(pred_struct).to_scoring_dict()
                except Exception:
                    # Malformed SUT output counts as an omission, and is flagged.
                    pred_struct = None
                    schema_invalid = True

        row = score_recipe(
            pred=pred_struct,
            gold_structured=target_gold_struct,
            gold_verbatim=gold_verb,
        )
        row["schema_invalid"] = schema_invalid
        row["deck_type"] = deck_type

        # Attach metadata for category aggregation
        if gold_rec:
            row["recipe_id"] = gold_rec.get("recipe_id", "")
            faces = gold_rec.get("faces", [])
            row["faces"] = str(len(faces))
            row["faces_count"] = str(len(faces))
            row["difficulty"] = gold_rec.get("difficulty", {}).get("legibility", "good")
            mods = [
                image_modality_map.get(f) or image_modality_map.get(Path(f).name) or image_modality_map.get(Path(f).stem)
                for f in faces
            ]
            mods = [m for m in mods if m is not None]
            if mods:
                row["modality"] = mods[0] if all(m == mods[0] for m in mods) else "mixed"
            else:
                row["modality"] = "unknown"
        else:
            row["faces"] = "unknown"
            row["faces_count"] = "unknown"
            row["modality"] = "unknown"
            row["difficulty"] = "unknown"

        if pred_rec_obj:
            raw_conf = pred_rec_obj.get("confidence")
            if raw_conf is not None:
                try:
                    conf = float(raw_conf)
                    confidences.append(conf)
                    is_correct = (iou >= 0.5) and (row.get("ingredient_f1") is not None and row.get("ingredient_f1", 0.0) >= 0.7)
                    correct_list.append(is_correct)
                except (ValueError, TypeError):
                    pass

        recipe_rows.append(row)

    # Aggregate extraction scores
    extraction_summary = aggregate_extraction(recipe_rows)
    extraction_summary["n_unscored_no_structured_gt"] = n_unscored_no_structured_gt
    extraction_summary["n_stage_failures"] = len(deck_output.get("failures", []))

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
        "confidences": confidences,
        "correct_list": correct_list,
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
        all_confidences.extend(res.get("confidences", []))
        all_correct.extend(res.get("correct_list", []))

    # Overall grouping aggregate
    overall_grouping: Dict[str, Any] = {}
    if all_grouping_scores:
        grouping_keys = all_grouping_scores[0].keys()
        for k in grouping_keys:
            vals = [s[k] for s in all_grouping_scores if s.get(k) is not None]
            overall_grouping[k] = sum(vals) / len(vals) if vals else 0.0

    # Overall extraction aggregate
    overall_extraction = aggregate_extraction(all_recipe_rows)
    total_unscored_no_gt = sum(
        res.get("extraction", {}).get("n_unscored_no_structured_gt", 0)
        for res in deck_results
    )
    total_stage_failures = sum(
        res.get("extraction", {}).get("n_stage_failures", 0)
        for res in deck_results
    )
    overall_extraction["n_unscored_no_structured_gt"] = total_unscored_no_gt
    overall_extraction["n_stage_failures"] = total_stage_failures

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
            cat_overall = agg.get("overall", {})
            category_metrics[cat_label] = {
                "ingredient_f1": cat_overall.get("ingredient_f1", {}).get("mean"),
                "title_cer": cat_overall.get("title_cer", {}).get("mean"),
                "direction_f1": cat_overall.get("direction_f1", {}).get("mean"),
                "bcubed_f1": None,
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
