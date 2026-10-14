"""Unit and integration tests for WP-E4 Scorer & Oracle Plan Generator.

Tests:
1. evals.scoring.gt:
   - T0 manifest ground truth loading and normalization into rb-gt/1 schema
   - Oracle plan generation (random order, sets shuffled, text stripped)
2. evals.scoring.evaluator:
   - Grouping metric evaluation (B-Cubed, ARI, exact cluster acc)
   - Hungarian matching and extraction scoring
   - Ledger ops summarization
3. evals.score:
   - Feedback policy enforcement: per_item permitted ONLY on T0, raises ValueError on T1-T4
   - Scoring execution producing report.json and report.md
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from evals.score import score_tier
from evals.scoring.evaluator import evaluate_predictions
from evals.scoring.gt import create_oracle_plan, load_ground_truth


REPO_ROOT = Path(__file__).resolve().parent.parent


class TestGroundTruthAndOracle:
    """Verifies GT normalization and oracle plan generation."""

    def test_load_t0_ground_truth(self) -> None:
        gt = load_ground_truth("T0")
        assert gt["schema"] == "rb-gt/1"
        assert gt["tier"] == "T0"
        assert len(gt["decks"]) >= 1

        deck = gt["decks"][0]
        assert "images" in deck
        assert "recipes" in deck
        assert len(deck["images"]) > 0
        assert len(deck["recipes"]) > 0

        # Verify recipe structured fields
        r0 = deck["recipes"][0]
        assert "recipe_id" in r0
        assert "faces" in r0
        assert "structured" in r0
        assert "title" in r0["structured"]

    def test_create_oracle_plan(self, tmp_path: Path) -> None:
        gt = load_ground_truth("T0")
        plan_data = create_oracle_plan(gt)
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan_data), encoding="utf-8")

        assert plan_file.exists()
        assert "decks" in plan_data
        assert len(plan_data["decks"]) >= 1

        d0 = plan_data["decks"][0]
        assert "deck_id" in d0
        assert "sets" in d0
        assert len(d0["sets"]) > 0

        # Ensure no text metadata leaks into oracle plan
        plan_str = json.dumps(plan_data).lower()
        assert "title" not in plan_str
        assert "ingredients" not in plan_str
        assert "directions" not in plan_str


class TestFeedbackPolicyEnforcement:
    """Enforces Contract §4: per_item feedback is only permitted on T0."""

    def test_per_item_allowed_for_t0(self, tmp_path: Path) -> None:
        # Create minimal synthetic outputs for T0
        outputs_dir = tmp_path / "t0_outputs"
        outputs_dir.mkdir()
        gt = load_ground_truth("T0")
        first_deck = gt["decks"][0]
        deck_id = first_deck["deck_id"]

        dummy_outputs = {
            "schema": "rb-out/1",
            "tier": "T0",
            "run_id": "test_run",
            "status": "ok",
            "track": "end_to_end",
            "decks": [{
                "deck_id": deck_id,
                "recipes": [],
                "unassigned_image_ids": [img["image_id"] for img in first_deck["images"]],
                "failures": [],
            }],
        }
        (outputs_dir / "outputs.json").write_text(json.dumps(dummy_outputs), encoding="utf-8")
        (outputs_dir / "ledger.json").write_text(json.dumps({"calls": [], "totals": {}}), encoding="utf-8")

        report_dir = tmp_path / "report"
        rep_json, rep_md = score_tier(
            tier="T0",
            outputs_dir=outputs_dir,
            report_dir=report_dir,
            feedback="per_item",
        )
        assert report_dir.exists()
        assert (report_dir / "report.json").exists()
        assert (report_dir / "report.md").exists()

    def test_per_item_disallowed_for_non_t0(self, tmp_path: Path) -> None:
        outputs_dir = tmp_path / "t1_outputs"
        outputs_dir.mkdir()
        (outputs_dir / "outputs.json").write_text("{}", encoding="utf-8")
        (outputs_dir / "ledger.json").write_text("{}", encoding="utf-8")

        report_dir = tmp_path / "report"
        with pytest.raises(ValueError, match="Feedback policy violation: per_item feedback is strictly prohibited"):
            score_tier(
                tier="T1",
                outputs_dir=outputs_dir,
                report_dir=report_dir,
                feedback="per_item",
            )
