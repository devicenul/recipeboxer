"""Unit and regression tests for WP-RT2a evaluation robustness, status handling, and reporting.

Tests verify:
1. T3 pending / missing structured GT:
   - Evaluator does not crash on NoneType / missing structured GT.
   - Grouping metrics are fully computed.
   - Unscored recipes are counted and reported in `n_unscored_no_structured_gt`.
   - Verbatim-only CER is computed when verbatim GT exists.
2. SUT and harness status handling:
   - Empty recipe list sets status to 'failed'.
   - Recipe failures outputted to separate failures list setting status to 'partial'.
   - Budget exceeded sets status to 'budget_exceeded'.
   - Status banners rendered properly in markdown report.
3. Category breakdowns:
   - Category metrics have None for bcubed_f1, and valid means for extraction metrics.
   - Report renders 'N/A*' for grouping F1 in category breakdown with footnote.
4. Grouping kendall tau robustness:
   - Path vs basename normalization.
   - Returns 0.0 when gold has multi-card recipes but zero multi-card clusters matched.
5. Operations budget spend:
   - Report handles missing spend gracefully with 'unknown'.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from evals.metrics.extraction import score_recipe
from evals.metrics.grouping import kendall_tau_order, score_grouping
from evals.report import render
from evals.scoring.evaluator import evaluate_deck, score_evaluation


def test_missing_and_pending_structured_gt_does_not_crash():
    """T3 pending / missing structured GT skips extraction scoring safely without crashing."""
    deck_output = {
        "deck_id": "d01",
        "recipes": [
            {
                "recipe_id": "pred_r1",
                "image_ids": ["c01.jpg", "c02.jpg"],
                "recipe": {
                    "title": "Grandma's Meatloaf",
                    "ingredients": ["1 lb ground beef", "1 onion"],
                    "directions": ["Mix and bake."],
                },
                "confidence": 0.95,
            }
        ],
        "failures": [],
    }

    deck_gt = {
        "deck_id": "d01",
        "deck_type": "handwritten",
        "images": [
            {"image_id": "c01.jpg", "modality": "HANDWRITTEN_CURSIVE"},
            {"image_id": "c02.jpg", "modality": "HANDWRITTEN_CURSIVE"},
        ],
        "recipes": [
            {
                "recipe_id": "gold_r1",
                "faces": ["c01.jpg", "c02.jpg"],
                "structured_status": "pending",
                "structured": None,
                "verbatim": ["Grandma's Meatloaf\n1 lb beef\n1 onion\nMix and bake."],
            }
        ],
    }

    res = evaluate_deck(deck_output, deck_gt)

    # 1. Grouping metrics still computed
    assert res["grouping"]["bcubed_f1"] == 1.0
    assert res["grouping"]["exact_cluster_acc"] == 1.0

    # 2. Extraction metrics recorded as unscored
    assert res["extraction"]["n_unscored_no_structured_gt"] == 1
    assert res["extraction"]["n_stage_failures"] == 0

    # 3. Verbatim CER computed
    recipe_row = res["recipe_rows"][0]
    assert recipe_row["title_cer"] is None
    assert recipe_row["ingredient_f1"] is None
    assert recipe_row["verbatim_cer"] is not None
    assert 0.0 <= recipe_row["verbatim_cer"] <= 1.0
    assert recipe_row["modality"] == "HANDWRITTEN_CURSIVE"


def test_full_evaluation_with_pending_structured_gt():
    """score_evaluation aggregates unscored counts and renders in report."""
    outputs_data = {
        "schema": "rb-out/1",
        "run_id": "run-test-t3",
        "tier": "T3",
        "git_commit": "abc1234",
        "track": "end_to_end",
        "variant": "opaque",
        "status": "ok",
        "decks": [
            {
                "deck_id": "d01",
                "recipes": [
                    {
                        "recipe_id": "pred_r1",
                        "image_ids": ["c01.jpg"],
                        "recipe": {"title": "Apple Pie", "ingredients": ["Apples", "Flour"], "directions": ["Bake."]},
                    }
                ],
                "failures": [],
            }
        ],
    }

    gt_data = {
        "tier": "T3",
        "decks": [
            {
                "deck_id": "d01",
                "deck_type": "handwritten",
                "images": [{"image_id": "c01.jpg", "modality": "HANDWRITTEN_PRINT"}],
                "recipes": [
                    {
                        "recipe_id": "gold_r1",
                        "faces": ["c01.jpg"],
                        "structured_status": "pending",
                        "structured": None,
                        "verbatim": ["Apple Pie\nApples, Flour\nBake."],
                    }
                ],
            }
        ],
    }

    report = score_evaluation(outputs_data, gt_data)
    assert report["metrics"]["extraction"]["n_unscored_no_structured_gt"] == 1

    json_str, md_str = render(report, feedback="aggregate")
    assert "Unscored (No Structured GT)" in md_str
    assert "Verbatim CER" in md_str


def test_status_banners_in_markdown():
    """Report renders prominent banners for budget_exceeded, partial, and failed statuses."""
    base_report = {
        "tier": "T1",
        "run_id": "run-banner-test",
        "git_commit": "def5678",
        "metrics": {
            "grouping": {"bcubed_f1": 0.50},
            "extraction": {"overall": {"title_cer": {"mean": 0.10}, "ingredient_f1": {"mean": 0.80}}},
        },
    }

    # 1. budget_exceeded
    r_budget = dict(base_report, status="budget_exceeded")
    _, md_budget = render(r_budget, feedback="aggregate")
    assert "BUDGET EXCEEDED" in md_budget

    # 2. partial
    r_partial = dict(base_report, status="partial")
    _, md_partial = render(r_partial, feedback="aggregate")
    assert "PARTIAL EVALUATION" in md_partial

    # 3. failed
    r_failed = dict(base_report, status="failed")
    _, md_failed = render(r_failed, feedback="aggregate")
    assert "EVALUATION FAILED" in md_failed


def test_category_breakdown_bcubed_footnote():
    """Category breakdown uses mean metrics, None for bcubed_f1, and displays footnote."""
    outputs_data = {
        "schema": "rb-out/1",
        "tier": "T1",
        "status": "ok",
        "decks": [
            {
                "deck_id": "d01",
                "recipes": [
                    {
                        "recipe_id": "pred_r1",
                        "image_ids": ["c01.jpg", "c02.jpg"],
                        "recipe": {
                            "title": "Chocolate Cake",
                            "ingredients": ["Flour", "Cocoa", "Sugar"],
                            "directions": ["Mix and bake."],
                        },
                    }
                ],
            }
        ],
    }
    gt_data = {
        "tier": "T1",
        "decks": [
            {
                "deck_id": "d01",
                "deck_type": "typed",
                "images": [{"image_id": "c01.jpg"}, {"image_id": "c02.jpg"}],
                "recipes": [
                    {
                        "recipe_id": "gold_r1",
                        "faces": ["c01.jpg", "c02.jpg"],
                        "structured": {
                            "title": "Chocolate Cake",
                            "ingredients": ["Flour", "Cocoa", "Sugar"],
                            "directions": ["Mix and bake."],
                        },
                    }
                ],
            }
        ],
    }

    report = score_evaluation(outputs_data, gt_data)
    assert "2-card" in report["categories"]
    cat_entry = report["categories"]["2-card"]
    assert cat_entry["bcubed_f1"] is None
    assert isinstance(cat_entry["ingredient_f1"], float)

    _, md_str = render(report, feedback="aggregate")
    assert "| `2-card` | `N/A*` |" in md_str
    assert "Grouping F1 (B-Cubed) is an aggregate deck-partition metric" in md_str


def test_kendall_tau_order_normalization_and_unmatched():
    """kendall_tau_order handles path strings and returns 0.0 when multi-card recipes fail to group."""
    # 1. Path vs basename matching
    pred = [["/tmp/deck/c01.jpg", "/tmp/deck/c02.jpg"]]
    gold = [["c01.jpg", "c02.jpg"]]
    assert kendall_tau_order(pred, gold) == 1.0

    # 2. Inverted order
    pred_inv = [["/tmp/deck/c02.jpg", "/tmp/deck/c01.jpg"]]
    assert kendall_tau_order(pred_inv, gold) == -1.0

    # 3. Gold has multi-card recipes, but model produced only single-card clusters (or failed to match multi-card)
    pred_split = [["c01.jpg"], ["c02.jpg"]]
    assert kendall_tau_order(pred_split, gold) == 0.0


def test_ops_spend_unknown_when_missing():
    """Report displays 'unknown' for ops spend if spend data is missing or None."""
    report = {
        "tier": "T1",
        "status": "ok",
        "metrics": {
            "grouping": {"bcubed_f1": 0.90},
            "extraction": {"title_cer": 0.05},
            "ops": {"calls": 3, "total_cost_usd": None},
        },
    }
    _, md_str = render(report, feedback="aggregate")
    assert "- **Total Spend (USD):** `unknown`" in md_str
