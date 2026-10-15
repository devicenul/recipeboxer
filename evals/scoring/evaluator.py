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
from evals.metrics.extraction import (
    aggregate as aggregate_extraction,
    match_recipes_by_content,
    recipe_content_similarity,
    score_recipe,
)
from evals.metrics.grouping import match_recipes, score_grouping
from evals.metrics.ops import summarize_ledger

logger = logging.getLogger("evals.scoring.evaluator")


def _parse_confidence(raw: Any) -> Optional[float]:
    """Parses a SUT recipe confidence; None (abstention) for missing / non-numeric values."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return v


def calibration_summary(
    conf_all: List[float],
    correct_all: List[bool],
    conf_matched: List[float],
    correct_matched: List[bool],
    n_pred: int,
) -> Dict[str, Any]:
    """Calibration block of the report (RT3-02).

    - ``ece_all`` (headline; also exposed as ``ece``): 10-bin ECE over **every predicted recipe** with a
      non-None confidence. Matched predictions are correct iff content_sim >= 0.5 AND ingredient_f1 >= 0.7;
      unmatched predictions (hallucinated recipes / grouping fragments) are incorrect by definition.
    - ``ece_matched`` (diagnostic, flatters the SUT): the same ECE restricted to Hungarian-matched predictions.
    - ``confidence_coverage`` = n_with_confidence / n_pred. A None confidence is an abstention: it is not
      scored in ECE but lowers coverage, so a SUT cannot improve ECE by withholding doubtful confidences
      without that being visible.
    """
    def _safe_ece(c: List[float], y: List[bool]) -> Optional[float]:
        if not c or not y:
            return None
        try:
            return ece(c, y)
        except Exception:
            return None

    e_all = _safe_ece(conf_all, correct_all)
    return {
        "ece": e_all,
        "ece_all": e_all,
        "ece_matched": _safe_ece(conf_matched, correct_matched),
        "confidence_coverage": round(len(conf_all) / n_pred, 4) if n_pred > 0 else None,
        "n_pred": int(n_pred),
        "n_with_confidence": len(conf_all),
        "n_matched_with_confidence": len(conf_matched),
        "accuracy_all": round(sum(1 for y in correct_all if y) / len(correct_all), 4) if correct_all else None,
        "mean_confidence_all": round(sum(conf_all) / len(conf_all), 4) if conf_all else None,
        "definition": "ece/ece_all: all predicted recipes with confidence (unmatched = incorrect); "
                      "ece_matched: matched predictions only; None confidence = abstention (see confidence_coverage)",
    }


def evaluate_deck(
    deck_output: Dict[str, Any],
    deck_gt: Dict[str, Any],
    *,
    min_overlap: float = 0.10,
) -> Dict[str, Any]:
    """Scores a single deck's predicted recipes against ground truth.

    Protocol (WP-SV):
    - Grouping metrics (B-Cubed, ARI, exact cluster, tau) evaluated on card clusters.
    - Extraction matching performed on recipe content similarity (word-token Jaccard)
      with Hungarian bipartite matching and minimum-overlap threshold (`min_overlap`).
    - Matched pairs scored for extraction accuracy ('matched-only').
    - Unmatched gold recipes scored as omissions (pred=None, metrics=0, CER=1.0)
      and included in 'end-to-end' aggregates.
    - Unmatched predicted recipes (fragments, hallucinations) counted as
      `n_unmatched_pred` / `hallucinated_recipe_rate`, NOT averaged into per-field metrics.
    """
    pred_recipes = deck_output.get("recipes", [])
    gold_recipes = deck_gt.get("recipes", [])

    pred_clusters = [r.get("image_ids", []) for r in pred_recipes]
    gold_clusters = [r.get("faces", []) for r in gold_recipes]

    # 1. Grouping metrics on card clusters
    grouping_scores = score_grouping(pred_clusters, gold_clusters)

    # 2. Principled Hungarian recipe matching on content similarity
    matched_pairs, unmatched_preds, unmatched_golds = match_recipes_by_content(
        pred_recipes, gold_recipes, min_overlap=min_overlap
    )

    deck_type = deck_gt.get("deck_type", "mixed")
    image_modality_map: Dict[str, str] = {}
    for img in deck_gt.get("images", []):
        iid = img.get("image_id", "")
        m = img.get("modality", "unknown")
        if iid:
            image_modality_map[iid] = m
            image_modality_map[Path(iid).name] = m
            image_modality_map[Path(iid).stem] = m

    matched_rows: List[Dict[str, Any]] = []
    omitted_rows: List[Dict[str, Any]] = []
    # Calibration populations (RT3-02):
    # - matched-only: matched predictions with a non-None confidence (``ece_matched``);
    # - all predictions: every predicted recipe with a non-None confidence, where unmatched (hallucinated /
    #   fragment) predictions count as incorrect (``ece_all``). None confidences are abstentions and are
    #   reported via ``confidence_coverage = n_with_confidence / n_pred``.
    confidences: List[float] = []
    correct_list: List[bool] = []
    confidences_all: List[float] = []
    correct_all: List[bool] = []
    n_unscored_no_structured_gt = 0

    # 3. Score matched recipe pairs
    for pred_idx, gold_idx, sim in matched_pairs:
        gold_rec = gold_recipes[gold_idx]
        pred_rec_obj = pred_recipes[pred_idx]

        gold_struct = gold_rec.get("structured")
        gold_verb = gold_rec.get("verbatim")

        has_structured = (
            gold_struct is not None
            and isinstance(gold_struct, dict)
            and len(gold_struct) > 0
            and gold_rec.get("structured_status") not in ("pending", "pending_text_only_draft")
        )

        if not has_structured:
            n_unscored_no_structured_gt += 1
            target_gold_struct = None
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
                    pred_struct = None
                    schema_invalid = True

        row = score_recipe(
            pred=pred_struct,
            gold_structured=target_gold_struct,
            gold_verbatim=gold_verb,
        )
        row["schema_invalid"] = schema_invalid
        row["deck_type"] = deck_type
        row["recipe_id"] = gold_rec.get("recipe_id", "")
        row["content_similarity"] = sim

        # Attach metadata from GT (never SUT output)
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

        # Calibration tracking
        if pred_rec_obj:
            conf = _parse_confidence(pred_rec_obj.get("confidence"))
            if conf is not None:
                is_correct = (sim >= 0.5) and (
                    row.get("ingredient_f1") is not None and row.get("ingredient_f1", 0.0) >= 0.7
                )
                confidences.append(conf)
                correct_list.append(is_correct)
                confidences_all.append(conf)
                correct_all.append(is_correct)

        matched_rows.append(row)

    # 3b. Unmatched (hallucinated / fragment) predictions enter the all-prediction calibration as incorrect
    for pred_idx in unmatched_preds:
        pred_rec_obj = pred_recipes[pred_idx] if 0 <= pred_idx < len(pred_recipes) else None
        if pred_rec_obj:
            conf = _parse_confidence(pred_rec_obj.get("confidence"))
            if conf is not None:
                confidences_all.append(conf)
                correct_all.append(False)

    # 4. Score unmatched gold recipes (recipe-level omissions)
    for gold_idx in unmatched_golds:
        gold_rec = gold_recipes[gold_idx]
        gold_struct = gold_rec.get("structured")
        gold_verb = gold_rec.get("verbatim")

        has_structured = (
            gold_struct is not None
            and isinstance(gold_struct, dict)
            and len(gold_struct) > 0
            and gold_rec.get("structured_status") not in ("pending", "pending_text_only_draft")
        )

        if not has_structured:
            n_unscored_no_structured_gt += 1
            target_gold_struct = None
        else:
            target_gold_struct = gold_struct

        omitted_row = score_recipe(
            pred=None,
            gold_structured=target_gold_struct,
            gold_verbatim=gold_verb,
        )
        omitted_row["schema_invalid"] = False
        omitted_row["deck_type"] = deck_type
        omitted_row["recipe_id"] = gold_rec.get("recipe_id", "")
        omitted_row["content_similarity"] = 0.0

        faces = gold_rec.get("faces", [])
        omitted_row["faces"] = str(len(faces))
        omitted_row["faces_count"] = str(len(faces))
        omitted_row["difficulty"] = gold_rec.get("difficulty", {}).get("legibility", "good")
        mods = [
            image_modality_map.get(f) or image_modality_map.get(Path(f).name) or image_modality_map.get(Path(f).stem)
            for f in faces
        ]
        mods = [m for m in mods if m is not None]
        if mods:
            omitted_row["modality"] = mods[0] if all(m == mods[0] for m in mods) else "mixed"
        else:
            omitted_row["modality"] = "unknown"

        omitted_rows.append(omitted_row)

    # 5. Extraction aggregates: matched-only vs end-to-end
    matched_only_summary = aggregate_extraction(matched_rows)
    end_to_end_summary = aggregate_extraction(matched_rows + omitted_rows)

    n_pred_total = len(pred_recipes)
    n_gold_total = len(gold_recipes)
    n_unmatched_pred = len(unmatched_preds)
    n_omitted_gold = len(unmatched_golds)

    extraction_summary = {
        "matched_only": matched_only_summary,
        "end_to_end": end_to_end_summary,
        "overall": matched_only_summary.get("overall", {}),
        "breakdowns": matched_only_summary.get("breakdowns", {}),
        "count": len(matched_rows),
        "n_matched": len(matched_pairs),
        "n_unmatched_pred": n_unmatched_pred,
        "n_omitted_gold": n_omitted_gold,
        "hallucinated_recipe_rate": round(n_unmatched_pred / n_pred_total, 4) if n_pred_total > 0 else 0.0,
        "omitted_recipe_rate": round(n_omitted_gold / n_gold_total, 4) if n_gold_total > 0 else 0.0,
        "n_unscored_no_structured_gt": n_unscored_no_structured_gt,
        "n_stage_failures": len(deck_output.get("failures", [])),
    }

    # Calibration ECE (RT3-02): all predictions (headline) + matched-only (diagnostic) + coverage
    calibration = calibration_summary(confidences_all, correct_all, confidences, correct_list, n_pred_total)

    return {
        "deck_id": deck_output.get("deck_id", deck_gt.get("deck_id", "d01")),
        "grouping": grouping_scores,
        "extraction": extraction_summary,
        "calibration_ece": calibration["ece_all"],
        "calibration": calibration,
        "recipe_rows": matched_rows,
        "omitted_rows": omitted_rows,
        "matched_pairs": matched_pairs,
        "unmatched_preds": unmatched_preds,
        "unmatched_golds": unmatched_golds,
        "matches": [(p, g, s) for p, g, s in matched_pairs]
        + [(p, None, 0.0) for p in unmatched_preds]
        + [(None, g, 0.0) for g in unmatched_golds],
        "confidences": confidences,
        "correct_list": correct_list,
        "confidences_all": confidences_all,
        "correct_all": correct_all,
        "n_pred": n_pred_total,
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
    all_matched_rows: List[Dict[str, Any]] = []
    all_omitted_rows: List[Dict[str, Any]] = []
    all_confidences: List[float] = []
    all_correct: List[bool] = []
    all_confidences_all: List[float] = []
    all_correct_all: List[bool] = []
    all_n_pred = 0
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
        all_matched_rows.extend(res.get("recipe_rows", []))
        all_omitted_rows.extend(res.get("omitted_rows", []))
        all_grouping_scores.append(res.get("grouping", {}))
        all_confidences.extend(res.get("confidences", []))
        all_correct.extend(res.get("correct_list", []))
        all_confidences_all.extend(res.get("confidences_all", []))
        all_correct_all.extend(res.get("correct_all", []))
        all_n_pred += int(res.get("n_pred", 0) or 0)

    # Overall grouping aggregate
    overall_grouping: Dict[str, Any] = {}
    if all_grouping_scores:
        grouping_keys = list(all_grouping_scores[0].keys())
        for k in grouping_keys:
            vals = [s[k] for s in all_grouping_scores if s.get(k) is not None]
            if k == "order_kendall_tau":
                overall_grouping[k] = round(sum(vals) / len(vals), 4) if vals else None
                overall_grouping["order_kendall_tau_n"] = len(vals)
            else:
                overall_grouping[k] = round(sum(vals) / len(vals), 4) if vals else 0.0

    # Overall extraction aggregates: matched-only vs end-to-end
    overall_matched = aggregate_extraction(all_matched_rows)
    overall_e2e = aggregate_extraction(all_matched_rows + all_omitted_rows)

    total_unscored_no_gt = sum(
        res.get("extraction", {}).get("n_unscored_no_structured_gt", 0)
        for res in deck_results
    )
    total_stage_failures = sum(
        res.get("extraction", {}).get("n_stage_failures", 0)
        for res in deck_results
    )
    total_unmatched_pred = sum(
        res.get("extraction", {}).get("n_unmatched_pred", 0)
        for res in deck_results
    )
    total_omitted_gold = sum(
        res.get("extraction", {}).get("n_omitted_gold", 0)
        for res in deck_results
    )
    total_matched = sum(
        res.get("extraction", {}).get("n_matched", 0)
        for res in deck_results
    )
    total_pred = sum(len(d.get("recipes", [])) for d in out_decks)
    total_gold = sum(
        len(gt_decks.get(d.get("deck_id", ""), {}).get("recipes", []))
        for d in out_decks
    )

    overall_extraction = {
        "matched_only": overall_matched,
        "end_to_end": overall_e2e,
        "overall": overall_matched["overall"],
        "breakdowns": overall_matched["breakdowns"],
        "count": len(all_matched_rows),
        "n_matched": total_matched,
        "n_unmatched_pred": total_unmatched_pred,
        "n_omitted_gold": total_omitted_gold,
        "hallucinated_recipe_rate": round(total_unmatched_pred / total_pred, 4) if total_pred > 0 else 0.0,
        "omitted_recipe_rate": round(total_omitted_gold / total_gold, 4) if total_gold > 0 else 0.0,
        "n_unscored_no_structured_gt": total_unscored_no_gt,
        "n_stage_failures": total_stage_failures,
    }

    # Overall calibration (RT3-02)
    overall_calibration = calibration_summary(
        all_confidences_all, all_correct_all, all_confidences, all_correct, all_n_pred
    )

    # Category aggregations strictly grounded in GT attributes
    category_metrics: Dict[str, Any] = {}
    if all_matched_rows:
        # Group by faces_count from GT
        by_faces: Dict[str, List[Dict[str, Any]]] = {}
        for row in all_matched_rows:
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

        # Group by deck_type from GT
        by_dt: Dict[str, List[Dict[str, Any]]] = {}
        for row in all_matched_rows:
            dt = row.get("deck_type", "unknown")
            by_dt.setdefault(dt, []).append(row)

        for dt_label, rows in by_dt.items():
            agg = aggregate_extraction(rows)
            cat_overall = agg.get("overall", {})
            category_metrics[dt_label] = {
                "ingredient_f1": cat_overall.get("ingredient_f1", {}).get("mean"),
                "title_cer": cat_overall.get("title_cer", {}).get("mean"),
                "direction_f1": cat_overall.get("direction_f1", {}).get("mean"),
                "bcubed_f1": None,
            }

        # Group by modality from GT
        by_mod: Dict[str, List[Dict[str, Any]]] = {}
        for row in all_matched_rows:
            m = row.get("modality", "unknown")
            by_mod.setdefault(m, []).append(row)

        for mod_label, rows in by_mod.items():
            agg = aggregate_extraction(rows)
            cat_overall = agg.get("overall", {})
            category_metrics[mod_label] = {
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
            "calibration": overall_calibration,
            "judge": {},
            "ops": ops_summary,
        },
        "categories": category_metrics,
        "decks": deck_results,
    }

    return report


evaluate_predictions = score_evaluation
