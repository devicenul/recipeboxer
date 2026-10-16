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


# --- WP-T3I: real-world GT features (multi-recipe images, excluded fragments, pending text GT, distractors) ---

# Structured/verbatim GT not drafted yet: the recipe takes part in grouping metrics only.
PENDING_TEXT_STATUSES: Tuple[str, ...] = ("pending_draft",)
EXTRACTION_PENDING_REASON = "structured GT pending"


def _norm_id(x: Any) -> str:
    return Path(str(x)).name


def is_excluded_recipe(rec: Dict[str, Any]) -> bool:
    """A gold recipe excluded from recipe-level/extraction scoring (partial fragment, memorization flag, ...)."""
    return str(rec.get("score_policy") or "").strip().lower() == "exclude" or bool(rec.get("excluded_reason"))


def has_text_gt(rec: Dict[str, Any]) -> bool:
    """False only for recipes whose verbatim/structured GT is explicitly pending (``pending_draft``) and empty.

    Every other recipe keeps the legacy behaviour (content matching against structured and/or verbatim GT).
    """
    verb = rec.get("verbatim") or []
    if isinstance(verb, str):
        verb = [verb]
    if any(str(v).strip() for v in verb):
        return True
    return rec.get("structured_status") not in PENDING_TEXT_STATUSES


def image_components(clusters: Sequence[Sequence[str]]) -> List[List[str]]:
    """Merges clusters that share an image (union-find) into image-level groups, preserving first-seen order.

    Used when one image may carry several recipes (``multi_recipe_image``) or a recipe crosses from one
    multi-recipe page to the next: the grouping question then becomes "which images belong together".
    """
    parent: Dict[str, str] = {}
    order: List[str] = []

    def find(a: str) -> str:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for cl in clusters:
        ids = [_norm_id(c) for c in cl]
        for c in ids:
            if c not in parent:
                parent[c] = c
                order.append(c)
        for c in ids[1:]:
            ra, rb = find(ids[0]), find(c)
            if ra != rb:
                parent[rb] = ra
    comps: Dict[str, List[str]] = {}
    for c in order:
        comps.setdefault(find(c), []).append(c)
    return list(comps.values())


def _has_shared_images(clusters: Sequence[Sequence[str]]) -> bool:
    seen: set = set()
    for cl in clusters:
        for c in {_norm_id(x) for x in cl}:
            if c in seen:
                return True
            seen.add(c)
    return False


def non_recipe_image_ids(deck_gt: Dict[str, Any]) -> List[str]:
    """Distractor / non-recipe images: deck ``non_recipe_images`` plus images with role distractor/non_recipe."""
    ids = {_norm_id(x) for x in (deck_gt.get("non_recipe_images") or [])}
    for img in deck_gt.get("images", []) or []:
        if str(img.get("role") or "").lower() in ("distractor", "non_recipe"):
            ids.add(_norm_id(img.get("image_id", "")))
    ids.discard("")
    return sorted(ids)


def _pending_extraction_summary(n_pending: int, n_excluded: int, n_stage_failures: int,
                                n_pred: int, n_gold_text: int = 0) -> Dict[str, Any]:
    """Extraction block when no gold recipe has text GT yet: everything N/A (None), never zero."""
    empty = aggregate_extraction([])
    return {
        "status": "not_available",
        "reason": EXTRACTION_PENDING_REASON if n_pending else "all gold recipes excluded from scoring",
        "matched_only": empty,
        "end_to_end": empty,
        "overall": {},
        "breakdowns": {},
        "count": 0,
        "n_matched": None,
        "n_unmatched_pred": None,
        "n_omitted_gold": None,
        "hallucinated_recipe_rate": None,
        "omitted_recipe_rate": None,
        "n_unscored_no_structured_gt": n_pending,
        "n_pending_structured_gt": n_pending,
        "n_excluded_gold": n_excluded,
        "n_stage_failures": n_stage_failures,
        "n_pred_total": n_pred,
        "n_gold_text": n_gold_text,
    }


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

    Real-world GT extensions (WP-T3I):
    - Multi-recipe images: when one gold image belongs to several recipes (or the deck sets
      ``multi_recipe_images``), grouping metrics are computed on image-level groups (connected components
      of the recipe/image relation, for both pred and gold). Recipe-level set agreement is reported in
      ``grouping_detail`` (``recipe_set_exact_acc``, recipe counts).
    - Excluded gold recipes (``score_policy: exclude`` or a non-empty ``excluded_reason``) are dropped from
      extraction matching and recipe-level counts; their images still count for image-level grouping.
    - Pending text GT (``structured_status: pending_draft`` with no verbatim): such recipes are not content-
      matched. If no gold recipe of the deck has text GT, extraction and calibration are N/A
      (``status: not_available``, ``reason: structured GT pending``) - never zero.
    - Distractor (non-recipe) images: ``grouping_detail.distractor_rejection_rate`` = share of non-recipe
      images not placed in any predicted recipe.
    """
    pred_recipes = deck_output.get("recipes", [])
    gold_all = deck_gt.get("recipes", [])

    pred_clusters = [r.get("image_ids", []) for r in pred_recipes]
    gold_clusters = [r.get("faces", []) for r in gold_all]

    excluded_idx = {i for i, r in enumerate(gold_all) if is_excluded_recipe(r)}
    scored_idx = [i for i in range(len(gold_all)) if i not in excluded_idx]
    text_idx = [i for i in scored_idx if has_text_gt(gold_all[i])]
    pending_idx = [i for i in scored_idx if not has_text_gt(gold_all[i])]

    # 1. Grouping metrics on card clusters (image-level components when images carry several recipes)
    multi_recipe = bool(deck_gt.get("multi_recipe_images")) or _has_shared_images(gold_clusters)
    grouping_detail: Dict[str, Any] = {
        "mode": "image_components" if multi_recipe else "recipes",
        "n_gold_recipes": len(gold_all),
        "n_gold_recipes_scored": len(scored_idx),
        "n_excluded_gold": len(excluded_idx),
        "n_pred_recipes": len(pred_recipes),
    }
    if multi_recipe:
        grouping_scores = score_grouping(image_components(pred_clusters), image_components(gold_clusters))
        scored_gold_sets = [[_norm_id(c) for c in gold_clusters[i]] for i in scored_idx]
        pred_sets_norm = [[_norm_id(c) for c in p] for p in pred_clusters]
        n_exact = 0
        for p_i, g_i, _ in match_recipes(pred_sets_norm, scored_gold_sets):
            if p_i is not None and g_i is not None and set(pred_sets_norm[p_i]) == set(scored_gold_sets[g_i]):
                n_exact += 1
        grouping_detail["recipe_set_exact"] = n_exact
        grouping_detail["recipe_set_exact_acc"] = (
            round(n_exact / len(scored_gold_sets), 4) if scored_gold_sets else None
        )
    else:
        grouping_scores = score_grouping(pred_clusters, gold_clusters)

    distractors = set(non_recipe_image_ids(deck_gt))
    pred_images = {_norm_id(c) for p in pred_clusters for c in p}
    n_dis_included = len(distractors & pred_images)
    grouping_detail["n_distractor_images"] = len(distractors)
    grouping_detail["n_distractor_in_pred_recipes"] = n_dis_included
    grouping_detail["distractor_rejection_rate"] = (
        round(1.0 - n_dis_included / len(distractors), 4) if distractors else None
    )

    n_stage_failures = len(deck_output.get("failures", []))
    if not text_idx and (pending_idx or excluded_idx):
        # No text GT at all for this deck: grouping only; extraction + calibration N/A (not zero).
        extraction_summary = _pending_extraction_summary(
            n_pending=len(pending_idx), n_excluded=len(excluded_idx),
            n_stage_failures=n_stage_failures, n_pred=len(pred_recipes),
        )
        calibration = calibration_summary([], [], [], [], len(pred_recipes))
        return {
            "deck_id": deck_output.get("deck_id", deck_gt.get("deck_id", "d01")),
            "grouping": grouping_scores,
            "grouping_detail": grouping_detail,
            "extraction": extraction_summary,
            "calibration_ece": None,
            "calibration": calibration,
            "recipe_rows": [],
            "omitted_rows": [],
            "matched_pairs": [],
            "unmatched_preds": [],
            "unmatched_golds": [],
            "matches": [],
            "confidences": [],
            "correct_list": [],
            "confidences_all": [],
            "correct_all": [],
            "n_pred": len(pred_recipes),
            "extraction_available": False,
        }

    # Extraction is scored only against recipes that have text GT and are not excluded
    # (identical to the legacy behaviour when nothing is excluded or pending).
    gold_recipes = [gold_all[i] for i in text_idx]

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
            and gold_rec.get("structured_status") not in ("pending", "pending_text_only_draft", "pending_draft")
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
            and gold_rec.get("structured_status") not in ("pending", "pending_text_only_draft", "pending_draft")
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
        # With pending (text-less) gold recipes, predictions of those recipes cannot be matched, so the
        # hallucinated-recipe rate would be inflated: report N/A instead (WP-T3I).
        "hallucinated_recipe_rate": (
            None if pending_idx
            else (round(n_unmatched_pred / n_pred_total, 4) if n_pred_total > 0 else 0.0)
        ),
        "omitted_recipe_rate": round(n_omitted_gold / n_gold_total, 4) if n_gold_total > 0 else 0.0,
        "n_unscored_no_structured_gt": n_unscored_no_structured_gt + len(pending_idx),
        "n_pending_structured_gt": len(pending_idx),
        "n_excluded_gold": len(excluded_idx),
        "n_stage_failures": len(deck_output.get("failures", [])),
        "n_pred_total": n_pred_total,
        "n_gold_text": n_gold_total,
    }

    # Calibration ECE (RT3-02): all predictions (headline) + matched-only (diagnostic) + coverage
    calibration = calibration_summary(confidences_all, correct_all, confidences, correct_list, n_pred_total)

    return {
        "deck_id": deck_output.get("deck_id", deck_gt.get("deck_id", "d01")),
        "grouping": grouping_scores,
        "grouping_detail": grouping_detail,
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
        "extraction_available": True,
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

    def _ext_sum(results: List[Dict[str, Any]], key: str) -> int:
        return sum(int(r.get("extraction", {}).get(key, 0) or 0) for r in results)

    # WP-T3I: decks whose gold has no text GT yet (pending_draft) contribute grouping metrics only.
    avail = [r for r in deck_results if r.get("extraction_available", True)]
    total_unscored_no_gt = _ext_sum(deck_results, "n_unscored_no_structured_gt")
    total_stage_failures = _ext_sum(deck_results, "n_stage_failures")
    total_pending = _ext_sum(deck_results, "n_pending_structured_gt")
    total_excluded = _ext_sum(deck_results, "n_excluded_gold")

    if deck_results and not avail:
        overall_extraction = _pending_extraction_summary(
            n_pending=total_pending, n_excluded=total_excluded,
            n_stage_failures=total_stage_failures, n_pred=all_n_pred,
        )
        overall_extraction["n_unscored_no_structured_gt"] = total_unscored_no_gt
    else:
        total_unmatched_pred = _ext_sum(avail, "n_unmatched_pred")
        total_omitted_gold = _ext_sum(avail, "n_omitted_gold")
        total_matched = _ext_sum(avail, "n_matched")
        special = len(avail) != len(deck_results) or any(
            r.get("extraction", {}).get("n_pending_structured_gt") or r.get("extraction", {}).get("n_excluded_gold")
            for r in avail
        )
        if special:
            total_pred = _ext_sum(avail, "n_pred_total")
            total_gold = _ext_sum(avail, "n_gold_text")
        else:  # legacy totals (unchanged behaviour for ordinary GT)
            total_pred = sum(len(d.get("recipes", [])) for d in out_decks)
            total_gold = sum(
                len(gt_decks.get(d.get("deck_id", ""), {}).get("recipes", []))
                for d in out_decks
            )
        any_pending_in_avail = any(r.get("extraction", {}).get("n_pending_structured_gt") for r in avail)

        overall_extraction = {
            "matched_only": overall_matched,
            "end_to_end": overall_e2e,
            "overall": overall_matched["overall"],
            "breakdowns": overall_matched["breakdowns"],
            "count": len(all_matched_rows),
            "n_matched": total_matched,
            "n_unmatched_pred": total_unmatched_pred,
            "n_omitted_gold": total_omitted_gold,
            "hallucinated_recipe_rate": (
                None if any_pending_in_avail
                else (round(total_unmatched_pred / total_pred, 4) if total_pred > 0 else 0.0)
            ),
            "omitted_recipe_rate": round(total_omitted_gold / total_gold, 4) if total_gold > 0 else 0.0,
            "n_unscored_no_structured_gt": total_unscored_no_gt,
            "n_stage_failures": total_stage_failures,
        }
        if special:
            overall_extraction["n_pending_structured_gt"] = total_pending
            overall_extraction["n_excluded_gold"] = total_excluded
            overall_extraction["n_decks_without_text_gt"] = len(deck_results) - len(avail)

    # Grouping detail (WP-T3I): recipe-level agreement on multi-recipe images + distractor rejection
    details = [r.get("grouping_detail") or {} for r in deck_results]
    grouping_detail: Dict[str, Any] = {}
    if details:
        grouping_detail["modes"] = sorted({d.get("mode", "recipes") for d in details})
        for k in ("n_gold_recipes", "n_gold_recipes_scored", "n_excluded_gold", "n_pred_recipes",
                  "n_distractor_images", "n_distractor_in_pred_recipes"):
            grouping_detail[k] = sum(int(d.get(k, 0) or 0) for d in details)
        n_dis = grouping_detail["n_distractor_images"]
        grouping_detail["distractor_rejection_rate"] = (
            round(1.0 - grouping_detail["n_distractor_in_pred_recipes"] / n_dis, 4) if n_dis else None
        )
        multi = [d for d in details if d.get("mode") == "image_components"]
        if multi:
            exact = sum(int(d.get("recipe_set_exact", 0) or 0) for d in multi)
            denom = sum(int(d.get("n_gold_recipes_scored", 0) or 0) for d in multi)
            grouping_detail["recipe_set_exact"] = exact
            grouping_detail["recipe_set_exact_acc"] = round(exact / denom, 4) if denom else None

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
    if grouping_detail and (
        "image_components" in grouping_detail.get("modes", [])
        or grouping_detail.get("n_distractor_images")
        or grouping_detail.get("n_excluded_gold")
    ):
        report["metrics"]["grouping_detail"] = grouping_detail

    return report


evaluate_predictions = score_evaluation
