"""Unit tests for evaluation reporting, feedback policy, and baseline comparison (WP-E7).

Tests verify:
- render(): JSON and Markdown generation.
- Feedback policy enforcement:
  * Tier != T0 strictly forbids per_item feedback (raises ValueError).
  * Tier != T0 strips per-item and per-image details from outputs.
  * Tier == T0 permits per_item feedback.
- Status 'no_live_model' renders all accuracy metrics as 'N/A'.
- compare():
  * Higher-is-better metrics detect regression when current < baseline - tolerance.
  * Lower-is-better metrics detect regression when current > baseline + tolerance.
  * Tolerances parameter overrides default tolerances.
  * Clean passes return an empty list.
  * Cheat detected or offline status triggers regression error.
- CLI: python -m evals.report compare exits non-zero on regression, zero on pass.
- config/pricing.toml loading and unverified placeholders.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
import pytest

from evals.report import compare, load_pricing, render


# ============================================================================
# 1. FEEDBACK POLICY TESTS
# ============================================================================

def test_render_t1_per_item_raises_value_error():
    """Requesting per_item feedback on T1 raises ValueError per feedback policy."""
    report = {
        "tier": "T1",
        "status": "ok",
        "metrics": {"grouping": {"bcubed_f1": 0.85}},
    }
    with pytest.raises(ValueError, match="per_item feedback is strictly prohibited for tier 'T1'"):
        render(report, feedback="per_item")


def test_render_t2_t3_t4_per_item_raises_value_error():
    """T2, T3, T4 all prohibit per_item feedback."""
    for tier in ("T2", "T3", "T4"):
        report = {
            "tier": tier,
            "status": "ok",
            "metrics": {"grouping": {"bcubed_f1": 0.90}},
        }
        with pytest.raises(ValueError, match=f"per_item feedback is strictly prohibited for tier '{tier}'"):
            render(report, feedback="per_item")


def test_render_t0_per_item_allowed():
    """Tier T0 permits per_item feedback with diagnostics preserved."""
    report = {
        "tier": "T0",
        "status": "ok",
        "metrics": {"grouping": {"bcubed_f1": 0.95}},
        "items": [
            {"recipe_id": "r1", "pred_title": "Pecan Pie", "gold_title": "Pecan Pie"}
        ],
    }
    json_text, md_text = render(report, feedback="per_item")
    data = json.loads(json_text)

    assert "items" in data
    assert len(data["items"]) == 1
    assert "Per-Item Diagnostic Details" in md_text


def test_render_t1_aggregate_strips_per_item_fields():
    """Tier T1 with aggregate feedback strips all per-item and per-image details."""
    report = {
        "tier": "T1",
        "status": "ok",
        "metrics": {
            "grouping": {"bcubed_f1": 0.88, "ari": 0.82},
            "extraction": {"title_cer": 0.04, "ingredient_f1": 0.92},
        },
        "per_item": [{"card_id": "c1.jpg", "error": "split"}],
        "image_diffs": {"c1.jpg": "diff details"},
        "decks": [
            {
                "deck_id": "d01",
                "recipes": [{"recipe_id": "r1", "image_ids": ["c1.jpg"]}],
                "metrics": {"bcubed_f1": 0.90},
            }
        ],
    }
    json_text, md_text = render(report, feedback="aggregate")
    data = json.loads(json_text)

    # Invariants:
    # 1. per_item and image_diffs stripped
    assert "per_item" not in data
    assert "image_diffs" not in data
    assert "recipes" not in data["decks"][0]
    assert data["decks"][0]["recipe_count"] == 1

    # 2. Markdown has no per-item leaks
    assert "c1.jpg" not in md_text
    assert "image_diffs" not in md_text


# ============================================================================
# 2. STATUS & N/A RENDERING TESTS
# ============================================================================

def test_render_no_live_model_status():
    """Status 'no_live_model' renders N/A for all quality metrics in Markdown."""
    report = {
        "tier": "T1",
        "status": "no_live_model",
        "run_id": "run-offline-123",
    }
    json_text, md_text = render(report, feedback="aggregate")
    data = json.loads(json_text)

    assert data["status"] == "no_live_model"
    assert "NO LIVE MODEL EVALUATION" in md_text
    assert "| `N/A` | Offline |" in md_text
    assert "All quality, transcription, and grouping metrics are recorded as **N/A**" in md_text


# ============================================================================
# 3. BASELINE COMPARISON TESTS
# ============================================================================

def test_compare_clean_pass():
    """Comparing identical or improved metrics yields an empty regression list."""
    baseline = {
        "metrics": {
            "grouping": {"bcubed_f1": 0.85, "ari": 0.80},
            "extraction": {"title_cer": 0.05, "ingredient_f1": 0.90},
        }
    }
    report = {
        "metrics": {
            "grouping": {"bcubed_f1": 0.86, "ari": 0.82},  # Higher is better: improved
            "extraction": {"title_cer": 0.04, "ingredient_f1": 0.91},  # Lower CER: improved
        }
    }
    regressions = compare(report, baseline)
    assert regressions == []


def test_compare_higher_better_regression():
    """Detects regression when a higher-is-better metric drops beyond tolerance."""
    baseline = {"metrics": {"grouping": {"bcubed_f1": 0.90}}}
    # Dropped to 0.85 (default tol = 0.01; 0.85 < 0.90 - 0.01)
    report = {"metrics": {"grouping": {"bcubed_f1": 0.85}}}

    regressions = compare(report, baseline)
    assert len(regressions) == 1
    assert "Regression in 'grouping.bcubed_f1' (higher is better)" in regressions[0]
    assert "dropped by 0.0500" in regressions[0]


def test_compare_lower_better_regression():
    """Detects regression when a lower-is-better metric rises beyond tolerance."""
    baseline = {"metrics": {"extraction": {"title_cer": 0.03}}}
    # Rose to 0.10 (default tol = 0.02; 0.10 > 0.03 + 0.02)
    report = {"metrics": {"extraction": {"title_cer": 0.10}}}

    regressions = compare(report, baseline)
    assert len(regressions) == 1
    assert "Regression in 'extraction.title_cer' (lower is better)" in regressions[0]
    assert "increased by 0.0700" in regressions[0]


def test_compare_within_tolerance_passes():
    """Changes within the allowable tolerance do not trigger regressions."""
    baseline = {"metrics": {"grouping": {"bcubed_f1": 0.90}, "extraction": {"title_cer": 0.05}}}
    # bcubed_f1 dropped by 0.005 (within 0.01 tolerance)
    # title_cer increased by 0.010 (within 0.02 tolerance)
    report = {"metrics": {"grouping": {"bcubed_f1": 0.895}, "extraction": {"title_cer": 0.060}}}

    regressions = compare(report, baseline)
    assert regressions == []


def test_compare_custom_tolerances():
    """Custom tolerances dictionary overrides defaults."""
    baseline = {"metrics": {"grouping": {"bcubed_f1": 0.90}}}
    report = {"metrics": {"grouping": {"bcubed_f1": 0.85}}}

    # With large tolerance (0.10), drop of 0.05 passes
    regressions = compare(report, baseline, tolerances={"bcubed_f1": 0.10})
    assert regressions == []

    # With strict tolerance (0.01), it fails
    regressions_strict = compare(report, baseline, tolerances={"bcubed_f1": 0.01})
    assert len(regressions_strict) == 1


def test_compare_status_regressions():
    """Status regressions (cheat_detected or offline vs online) are flagged."""
    baseline = {"status": "ok", "metrics": {"grouping": {"bcubed_f1": 0.90}}}

    # Cheat detected
    cheat_report = {"status": "cheat_detected", "metrics": {"grouping": {"bcubed_f1": 0.99}}}
    cheat_regs = compare(cheat_report, baseline)
    assert len(cheat_regs) == 1
    assert "cheat_detected" in cheat_regs[0]

    # Offline report compared against online baseline
    offline_report = {"status": "no_live_model"}
    offline_regs = compare(offline_report, baseline)
    assert len(offline_regs) == 1
    assert "no_live_model" in offline_regs[0]


# ============================================================================
# 4. CLI TESTS
# ============================================================================

def test_cli_compare_exit_codes(tmp_path: Path):
    """CLI exits 0 on clean pass and 1 on regression."""
    baseline_path = tmp_path / "baseline.json"
    pass_report_path = tmp_path / "pass_report.json"
    fail_report_path = tmp_path / "fail_report.json"

    baseline_data = {"metrics": {"bcubed_f1": 0.90, "title_cer": 0.04}}
    pass_data = {"metrics": {"bcubed_f1": 0.91, "title_cer": 0.03}}
    fail_data = {"metrics": {"bcubed_f1": 0.70, "title_cer": 0.25}}

    baseline_path.write_text(json.dumps(baseline_data), encoding="utf-8")
    pass_report_path.write_text(json.dumps(pass_data), encoding="utf-8")
    fail_report_path.write_text(json.dumps(fail_data), encoding="utf-8")

    # 1. Clean pass -> Exit 0
    res_pass = subprocess.run(
        [sys.executable, "-m", "evals.report", "compare", str(pass_report_path), str(baseline_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert res_pass.returncode == 0
    assert "Comparison PASSED" in res_pass.stdout

    # 2. Regression -> Exit 1
    res_fail = subprocess.run(
        [sys.executable, "-m", "evals.report", "compare", str(fail_report_path), str(baseline_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert res_fail.returncode == 1
    assert "BASELINE REGRESSION DETECTED" in res_fail.stderr


def test_cli_render_outputs(tmp_path: Path):
    """CLI render writes report.json and report.md to output directory."""
    report_path = tmp_path / "report.json"
    out_dir = tmp_path / "out"
    report_data = {
        "tier": "T1",
        "status": "ok",
        "metrics": {"grouping": {"bcubed_f1": 0.88}},
    }
    report_path.write_text(json.dumps(report_data), encoding="utf-8")

    res = subprocess.run(
        [sys.executable, "-m", "evals.report", "render", str(report_path), "--out", str(out_dir)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert res.returncode == 0
    assert (out_dir / "report.json").exists()
    assert (out_dir / "report.md").exists()


# ============================================================================
# 5. PRICING TOML TESTS
# ============================================================================

def test_pricing_toml_loading():
    """config/pricing.toml loads cleanly with verified, non-zero model prices."""
    pricing = load_pricing()
    assert "meta" in pricing
    assert pricing["meta"]["verified_on"] not in ("", "UNVERIFIED")

    for model in ("gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-pro-preview"):
        assert model in pricing
        m_data = pricing[model]
        assert m_data["input_per_mtok"] > 0.0
        assert m_data["output_per_mtok"] > 0.0
        assert "thinking_per_mtok" in m_data
        assert m_data["verified_on"] not in ("", "UNVERIFIED")
