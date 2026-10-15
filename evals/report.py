"""Evaluation reporting and baseline comparison engine for RecipeBoxer (WP-E7).

Provides:
- render(report, feedback): Renders JSON and Markdown report representations while
  enforcing the feedback policy (§3.2, WAVE3_CONTRACTS.md §4, §6).
  * Tier != T0 strictly permits 'aggregate' feedback. Caller requesting 'per_item' on
    T1-T4 raises ValueError.
  * For Tier != T0, all per-item and per-image fields are stripped from the output.
  * When status is 'no_live_model', renders metrics as 'N/A'.
- compare(report, baseline, tolerances): Compares an evaluation report against a baseline
  with per-metric tolerances and directionality (higher-better vs lower-better).
  Returns regression messages (empty list = pass).
- CLI: `python -m evals.report compare REPORT BASELINE` exiting non-zero on regressions.
- load_pricing(path): Loads user-editable pricing configuration from config/pricing.toml.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

# ============================================================================
# 1. METRIC DIRECTIONS AND DEFAULT TOLERANCES
# ============================================================================

# "higher" indicates higher value is better (regression if current < baseline - tol).
# "lower" indicates lower value is better (regression if current > baseline + tol).
METRIC_DIRECTIONS: Dict[str, str] = {
    # Grouping metrics
    "bcubed_p": "higher",
    "bcubed_r": "higher",
    "bcubed_f1": "higher",
    "ari": "higher",
    "exact_cluster_acc": "higher",
    "order_kendall_tau": "higher",
    "standalone_acc": "higher",
    "false_merge_rate": "lower",
    "false_split_rate": "lower",
    "pairing_precision": "higher",
    "pairing_recall": "higher",
    "pairing_f1": "higher",
    "f1_score": "higher",
    "precision": "higher",
    "recall": "higher",
    "multi_card_accuracy": "higher",
    # Extraction metrics
    "title_cer": "lower",
    "overall_cer": "lower",
    "overall_wer": "lower",
    "ingredient_cer": "lower",
    "ingredient_wer": "lower",
    "direction_cer": "lower",
    "direction_wer": "lower",
    "ingredient_p": "higher",
    "ingredient_r": "higher",
    "ingredient_f1": "higher",
    "qty_unit_exact": "higher",
    "direction_f1": "higher",
    "direction_tau": "higher",
    "hallucination_rate": "lower",
    "omission_rate": "lower",
    "illegible_precision": "higher",
    "fraction_accuracy": "higher",
    "schema_conformity_rate": "higher",
    # Calibration metrics
    "ece": "lower",
    "ece_all": "lower",
    "ece_matched": "lower",
    "confidence_coverage": "higher",
    # Judge metrics
    "score": "higher",
    "judge_score": "higher",
    "aggregate_score": "higher",
    "pass_rate": "higher",
    # Ops / Resource metrics
    "total_cost_usd": "lower",
    "cost_usd": "lower",
    "latency_ms": "lower",
    "failure_rate": "lower",
}

DEFAULT_TOLERANCES: Dict[str, float] = {
    "default": 0.01,
    "bcubed_f1": 0.01,
    "bcubed_p": 0.01,
    "bcubed_r": 0.01,
    "ari": 0.02,
    "exact_cluster_acc": 0.01,
    "title_cer": 0.02,
    "overall_cer": 0.02,
    "overall_wer": 0.02,
    "ingredient_f1": 0.01,
    "direction_f1": 0.01,
    "hallucination_rate": 0.01,
    "omission_rate": 0.01,
    "judge_score": 0.20,
    "aggregate_score": 0.20,
    "score": 0.20,
}

PER_ITEM_FIELD_NAMES = {
    "per_item",
    "items",
    "item_details",
    "item_scores",
    "per_image",
    "image_diffs",
    "diffs",
    "recipes",
    "unassigned_image_ids",
    "verbatim",
    "face_diffs",
}


# ============================================================================
# 2. PRICING LOADER
# ============================================================================

def load_pricing(path: Optional[Union[Path, str]] = None) -> Dict[str, Any]:
    """Loads model pricing table from recipeboxer/data/pricing.toml or custom path.

    Args:
        path: Optional path to pricing.toml. Defaults to package data recipeboxer/data/pricing.toml.

    Returns:
        Dictionary mapping model IDs to pricing dictionaries and metadata.
    """
    if path is not None:
        target_path = Path(path)
        if target_path.exists():
            with open(target_path, "rb") as f:
                return tomllib.load(f)
        if not str(path).endswith("pricing.toml"):
            raise FileNotFoundError(f"Pricing configuration file not found at {target_path}")

    import importlib.resources
    traversable = importlib.resources.files("recipeboxer.data").joinpath("pricing.toml")
    return tomllib.loads(traversable.read_text(encoding="utf-8"))


# ============================================================================
# 3. FEEDBACK SANITIZATION
# ============================================================================

def _sanitize_for_feedback_policy(data: Any, tier: str, feedback: str) -> Any:
    """Recursively removes per-item and per-image details unless tier == 'T0' and feedback == 'per_item'."""
    is_t0 = str(tier).strip().upper() == "T0"
    allow_per_item = is_t0 and (feedback.lower() == "per_item")

    if allow_per_item:
        return data

    if isinstance(data, dict):
        cleaned: Dict[str, Any] = {}
        for k, v in data.items():
            k_lower = k.lower()
            if k_lower in PER_ITEM_FIELD_NAMES:
                # If key is 'recipes', replace with recipe count if it's a list
                if k_lower == "recipes" and isinstance(v, list):
                    cleaned["recipe_count"] = len(v)
                continue
            if isinstance(v, (dict, list)):
                cleaned[k] = _sanitize_for_feedback_policy(v, tier, feedback)
            else:
                cleaned[k] = v
        return cleaned

    if isinstance(data, list):
        return [_sanitize_for_feedback_policy(item, tier, feedback) for item in data]

    return data


# ============================================================================
# 4. REPORT RENDERING
# ============================================================================

def render(report: dict, feedback: str = "aggregate") -> tuple[str, str]:
    """Renders evaluation report into JSON text and GitHub Flavored Markdown.

    Enforces the feedback policy:
    - Tier != T0 permits aggregate feedback only. If caller requests 'per_item' on
      T1-T4, raises ValueError.
    - Tier != T0 strips all per-item and per-image fields from JSON and Markdown.
    - If status == 'no_live_model', metrics render as 'N/A'.

    Args:
        report: Raw evaluation report dictionary.
        feedback: 'aggregate' or 'per_item'.

    Returns:
        Tuple of (json_text, markdown_text).

    Raises:
        ValueError: If feedback == 'per_item' is requested for tier != T0.
    """
    raw_tier = report.get("tier") or report.get("metadata", {}).get("tier", "T1")
    tier = str(raw_tier).strip().upper()
    feedback_mode = str(feedback).strip().lower()

    if tier != "T0" and feedback_mode == "per_item":
        raise ValueError(
            f"Feedback policy violation: per_item feedback is strictly prohibited for tier '{tier}'. "
            "Only aggregate feedback is permitted for T1–T4 to prevent benchmark overfitting and leaks."
        )

    # 1. Clean data according to feedback policy
    report_copy = copy.deepcopy(report)
    sanitized_report = _sanitize_for_feedback_policy(report_copy, tier=tier, feedback=feedback_mode)

    # 2. Render JSON
    json_text = json.dumps(sanitized_report, indent=2, ensure_ascii=False)

    # 3. Render Markdown
    md_text = _render_markdown(sanitized_report, tier=tier, feedback=feedback_mode)

    return json_text, md_text


def _get_metric_val(container: Any, key: str) -> Any:
    """Extracts a scalar metric value from flat dict, nested dict, or {'mean': ...} container."""
    if not isinstance(container, dict):
        return None
    val = container.get(key)
    if val is None and "overall" in container and isinstance(container["overall"], dict):
        val = container["overall"].get(key)
    if isinstance(val, dict):
        if "mean" in val:
            return val["mean"]
        if "score" in val:
            return val["score"]
        if "value" in val:
            return val["value"]
    return val


def _render_markdown(report: dict, tier: str, feedback: str) -> str:
    """Builds formatted Markdown text for the evaluation report."""
    run_id = report.get("run_id", "N/A")
    git_commit = report.get("git_commit", "N/A")
    status = report.get("status", "ok")
    created = report.get("created") or report.get("date") or "N/A"
    track = report.get("track", "end_to_end")
    variant = report.get("variant", "opaque")
    model_profile = report.get("model_profile", "balanced")

    lines: List[str] = [
        f"# RecipeBoxer Evaluation Report — {tier}",
        "",
        f"- **Run ID:** `{run_id}`",
        f"- **Tier:** `{tier}`",
        f"- **Status:** `{status}`",
        f"- **Track:** `{track}`",
        f"- **Variant:** `{variant}`",
        f"- **Model Profile:** `{model_profile}`",
        f"- **Git Commit:** `{git_commit}`",
        f"- **Timestamp:** {created}",
        "",
    ]

    # Handle special statuses
    if status == "no_live_model":
        lines.extend([
            "> [!NOTE]",
            "> **NO LIVE MODEL EVALUATION**",
            ">",
            "> No live Gemini API credentials were configured for this evaluation run.",
            "> In accordance with INV-AC6, no synthetic or mock scoring was executed.",
            "> All quality, transcription, and grouping metrics are recorded as **N/A**.",
            "",
            "## Summary Metrics",
            "",
            "| Metric | Value | Status |",
            "|:---|:---:|:---|",
            "| **Grouping B-Cubed F1** | `N/A` | Offline |",
            "| **Grouping ARI** | `N/A` | Offline |",
            "| **Title CER** | `N/A` | Offline |",
            "| **Ingredient F1** | `N/A` | Offline |",
            "| **Direction F1** | `N/A` | Offline |",
            "| **Hallucination Rate** | `N/A` | Offline |",
            "| **LLM Judge Score** | `N/A` | Offline |",
            "| **Estimated Cost** | `N/A` | Offline |",
            "",
        ])
        return "\n".join(lines)

    if status == "cheat_detected":
        lines.extend([
            "> [!CAUTION]",
            "> **CHEAT DETECTED — AUDIT HOOK TRIPPED**",
            ">",
            "> The evaluation runner detected unauthorized access to ground-truth vaults,",
            "> answer keys, or fixture paths during pipeline execution.",
            "",
        ])
    elif status == "budget_exceeded":
        lines.extend([
            "> [!WARNING]",
            "> **BUDGET EXCEEDED**",
            ">",
            "> The evaluation run reached the configured spend cap.",
            "> Pipeline execution halted; partial outputs are reported below.",
            "",
        ])
    elif status == "failed":
        lines.extend([
            "> [!CAUTION]",
            "> **EVALUATION FAILED**",
            ">",
            "> Zero valid recipes were produced across the evaluation run.",
            "> All cards are counted as extraction omissions.",
            "",
        ])
    elif status == "partial":
        lines.extend([
            "> [!WARNING]",
            "> **PARTIAL EVALUATION**",
            ">",
            "> One or more recipe cards or stages encountered failures during execution.",
            "> Unresolved cards are reported as omissions.",
            "",
        ])

    # Extract metrics for standard rendering
    metrics = report.get("metrics", {})
    grouping = metrics.get("grouping", {})
    extraction = metrics.get("extraction", {})
    calibration = metrics.get("calibration", {})
    judge = metrics.get("judge", {})
    ops = metrics.get("ops", report.get("ledger", {}).get("totals", {}))

    # Fallback to top-level if metrics dict is flat
    if not grouping and any(k in report for k in ("bcubed_f1", "ari", "exact_cluster_acc")):
        grouping = report
    if not extraction and any(k in report for k in ("ingredient_f1", "title_cer", "direction_f1")):
        extraction = report

    lines.extend([
        "## Summary Metrics",
        "",
        "### Grouping & Card Pairing",
        "",
        "| Metric | Score | Direction | Description |",
        "|:---|:---:|:---:|:---|",
        f"| **B-Cubed F1** | `{_fmt_metric(_get_metric_val(grouping, 'bcubed_f1'))}` | Higher | Precision-recall harmonic mean across image clusters |",
        f"| **B-Cubed Precision** | `{_fmt_metric(_get_metric_val(grouping, 'bcubed_p'))}` | Higher | Proportion of correctly clustered image pairs |",
        f"| **B-Cubed Recall** | `{_fmt_metric(_get_metric_val(grouping, 'bcubed_r'))}` | Higher | Completeness of clustered recipe card sets |",
        f"| **Adjusted Rand Index (ARI)** | `{_fmt_metric(_get_metric_val(grouping, 'ari'))}` | Higher | Chance-adjusted cluster partition agreement |",
        f"| **Exact Cluster Accuracy** | `{_fmt_metric(_get_metric_val(grouping, 'exact_cluster_acc'))}` | Higher | Proportion of clusters matching GT exactly |",
        f"| **Card Order (Kendall Tau)** | `{_fmt_metric(_get_metric_val(grouping, 'order_kendall_tau'))}` | Higher | Face order correlation within multi-card recipes |",
        f"| **False Merge Rate** | `{_fmt_metric(_get_metric_val(grouping, 'false_merge_rate'))}` | Lower | Rate of distinct recipes merged into one |",
        f"| **False Split Rate** | `{_fmt_metric(_get_metric_val(grouping, 'false_split_rate'))}` | Lower | Rate of single recipes split into fragments |",
        "",
        "### Recipe Extraction Quality",
        "",
        "| Metric | Score | Direction | Description |",
        "|:---|:---:|:---:|:---|",
        f"| **Title CER** | `{_fmt_metric(_get_metric_val(extraction, 'title_cer'))}` | Lower | Character error rate for recipe titles |",
        f"| **Ingredient F1** | `{_fmt_metric(_get_metric_val(extraction, 'ingredient_f1'))}` | Higher | Hungarian line-level ingredient F1 (CER ≤ 0.1) |",
        f"| **Quantity / Unit Match** | `{_fmt_metric(_get_metric_val(extraction, 'qty_unit_exact'))}` | Higher | Exact matches on ingredient quantity and unit |",
        f"| **Direction F1** | `{_fmt_metric(_get_metric_val(extraction, 'direction_f1'))}` | Higher | Instruction step sequence F1 score |",
        f"| **Hallucination Rate** | `{_fmt_metric(_get_metric_val(extraction, 'hallucination_rate'))}` | Lower | Rate of fabricated ingredients or steps |",
        f"| **Omission Rate** | `{_fmt_metric(_get_metric_val(extraction, 'omission_rate'))}` | Lower | Rate of GT items completely missing |",
    ])

    verb_cer = _get_metric_val(extraction, "verbatim_cer")
    if verb_cer is not None:
        lines.append(f"| **Verbatim CER** | `{_fmt_metric(verb_cer)}` | Lower | Character error rate against raw card transcription |")

    unscored_no_gt = _get_metric_val(extraction, "n_unscored_no_structured_gt")
    if unscored_no_gt is not None and unscored_no_gt > 0:
        lines.append(f"| **Unscored (No Structured GT)** | `{unscored_no_gt}` | N/A | Recipes skipped due to pending structured GT |")

    n_failures = _get_metric_val(extraction, "n_stage_failures")
    if n_failures is not None and n_failures > 0:
        lines.append(f"| **Stage Failures** | `{n_failures}` | Lower | Pipeline stage failures reported during run |")

    lines.append("")

    # Calibration Section (RT3-02): all-prediction ECE is the headline; matched-only is diagnostic
    if isinstance(calibration, dict) and calibration:
        cov = calibration.get("confidence_coverage")
        lines.extend([
            "### Confidence Calibration",
            "",
            "| Metric | Score | Direction | Description |",
            "|:---|:---:|:---:|:---|",
            f"| **ECE (all predictions)** | `{_fmt_metric(calibration.get('ece_all', calibration.get('ece')))}` | Lower | 10-bin ECE over every predicted recipe with a confidence; unmatched (hallucinated/fragment) predictions count as incorrect |",
            f"| **ECE (matched only)** | `{_fmt_metric(calibration.get('ece_matched'))}` | Lower | Diagnostic: Hungarian-matched predictions only (excludes false-positive recipes; flatters calibration) |",
            f"| **Confidence Coverage** | `{_fmt_metric(cov)}` | Higher | Share of predicted recipes carrying a confidence (None = abstention, not scored in ECE) |",
            f"| **Predictions with Confidence** | `{calibration.get('n_with_confidence', 'N/A')} / {calibration.get('n_pred', 'N/A')}` | N/A | Scored population for ECE (all) |",
            "",
        ])

    # LLM Judge Section
    judge_score = judge.get("score") or judge.get("aggregate_score") or report.get("judge_score")
    judge_rationale = judge.get("rationale") or report.get("judge_rationale", "")
    lines.extend([
        "### LLM-as-a-Judge Evaluation (Informational)",
        "",
        f"- **Score:** `{_fmt_metric(judge_score)} / 5.0`",
    ])
    if judge_rationale:
        lines.append(f"- **Rationale:** {judge_rationale}")
    lines.append("")

    # Ops & Cost Summary
    total_cost = ops.get("total_cost_usd")
    if total_cost is None:
        total_cost = ops.get("total_usd")
    if total_cost is None:
        total_cost = ops.get("cost_usd")

    cost_str = f"${_fmt_metric(total_cost, is_currency=True)}" if total_cost is not None else "unknown"

    lines.extend([
        "### Operations & Budget",
        "",
        f"- **Total Spend (USD):** `{cost_str}`",
        f"- **Total Calls:** `{ops.get('calls', ops.get('total_calls', 'N/A'))}`",
        f"- **Input Tokens:** `{ops.get('input_tokens', 'N/A')}`",
        f"- **Output Tokens:** `{ops.get('output_tokens', 'N/A')}`",
        f"- **Latency (ms):** `{ops.get('latency_ms', 'N/A')}`",
        "",
    ])

    # Category breakdown if present
    categories = report.get("categories") or report.get("by_category")
    if categories and isinstance(categories, dict):
        lines.extend([
            "## Category Breakdown",
            "",
            "| Category | Grouping F1 | Title CER | Ingredient F1 | Directions F1 |",
            "|:---|:---:|:---:|:---:|:---:|",
        ])
        for cat_name, cat_data in categories.items():
            g_f1 = cat_data.get("bcubed_f1")
            g_f1_str = f"`{_fmt_metric(g_f1)}`" if g_f1 is not None else "`N/A*`"
            lines.append(
                f"| `{cat_name}` | {g_f1_str} | "
                f"`{_fmt_metric(_get_metric_val(cat_data, 'title_cer'))}` | "
                f"`{_fmt_metric(_get_metric_val(cat_data, 'ingredient_f1'))}` | "
                f"`{_fmt_metric(_get_metric_val(cat_data, 'direction_f1'))}` |"
            )
        lines.append("")
        lines.append(
            "> [!NOTE]\n"
            "> `*` Grouping F1 (B-Cubed) is an aggregate deck-partition metric; "
            "card-count categories break down recipe extraction quality."
        )
        lines.append("")

    # If T0 with per-item feedback, render details
    if tier == "T0" and feedback == "per_item":
        items = report.get("items") or report.get("per_item")
        if items and isinstance(items, list):
            lines.extend([
                "## Per-Item Diagnostic Details (T0 Only)",
                "",
                "```json",
                json.dumps(items, indent=2),
                "```",
                "",
            ])

    return "\n".join(lines)


def _fmt_metric(val: Any, is_currency: bool = False) -> str:
    """Formats numeric metric values cleanly for tables."""
    if val is None or val == "N/A":
        return "N/A"
    try:
        f = float(val)
        if is_currency:
            return f"{f:.4f}"
        if 0.0 <= f <= 1.0:
            return f"{f:.4f}"
        return f"{f:.2f}"
    except (ValueError, TypeError):
        return str(val)


# ============================================================================
# 5. BASELINE COMPARISON & REGRESSION TESTING
# ============================================================================

def _extract_metric_values(d: Any, prefix: str = "") -> Dict[str, float]:
    """Recursively flattens nested dictionary to extract numerical metrics.

    Produces canonical dot-separated keys (e.g. 'grouping.bcubed_f1').
    """
    results: Dict[str, float] = {}
    if not isinstance(d, dict):
        return results

    for k, v in d.items():
        key_str = f"{prefix}.{k}" if prefix else k
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            results[key_str] = float(v)
            if k == "mean" and prefix:
                results[prefix] = float(v)
                if ".overall." in prefix:
                    results[prefix.replace(".overall.", ".")] = float(v)
                if prefix.startswith("overall."):
                    results[prefix[len("overall."):]] = float(v)
        elif isinstance(v, dict):
            results.update(_extract_metric_values(v, prefix=key_str))
    return results


def compare(
    report: dict,
    baseline: dict,
    tolerances: Optional[Dict[str, float]] = None,
) -> List[str]:
    """Compares an evaluation report against a baseline and returns regression messages.

    Args:
        report: Current evaluation report dict.
        baseline: Baseline evaluation report dict.
        tolerances: Optional mapping of metric names to allowable regression thresholds.
            Falls back to DEFAULT_TOLERANCES or 'default' (0.01).

    Returns:
        List of regression error messages. Empty list indicates clean pass.
    """
    regressions: List[str] = []
    tol_map = dict(DEFAULT_TOLERANCES)
    if tolerances:
        tol_map.update(tolerances)

    # 1. Status checks
    report_status = report.get("status", "ok")
    baseline_status = baseline.get("status", "ok")

    if report_status == "cheat_detected":
        regressions.append(
            "Evaluation failed: status is 'cheat_detected' (anti-cheat audit hook tripped)."
        )
        return regressions

    if report_status == "no_live_model":
        if baseline_status != "no_live_model":
            regressions.append(
                "Evaluation regression: current status is 'no_live_model' (metrics N/A), "
                "failing comparison against evaluated baseline."
            )
        return regressions

    # 2. Extract metrics from report and baseline
    curr_metrics = _extract_metric_values(report.get("metrics", report))
    base_metrics = _extract_metric_values(baseline.get("metrics", baseline))

    # If baseline is empty or has benchmark.obfuscated, extract from whole dict
    if not base_metrics and "benchmark" in baseline:
        base_metrics = _extract_metric_values(baseline["benchmark"])
    if not curr_metrics and "benchmark" in report:
        curr_metrics = _extract_metric_values(report["benchmark"])

    # 3. Compare each metric found in baseline
    default_tol = tol_map.get("default", 0.01)

    for metric_name, base_val in base_metrics.items():
        leaf_k = metric_name.split(".")[-1].lower()

        # Resolve current metric value
        curr_val = curr_metrics.get(metric_name)
        if curr_val is None:
            curr_val = curr_metrics.get(leaf_k)
        if curr_val is None:
            for ck, cv in curr_metrics.items():
                if ck.split(".")[-1].lower() == leaf_k:
                    curr_val = cv
                    break

        if curr_val is None:
            continue

        direction = METRIC_DIRECTIONS.get(
            metric_name,
            METRIC_DIRECTIONS.get(leaf_k, "higher"),
        )
        tol = tol_map.get(
            metric_name,
            tol_map.get(leaf_k, default_tol),
        )

        if direction == "higher":
            # Higher is better: regression if current < baseline - tol
            if curr_val < (base_val - tol):
                delta = base_val - curr_val
                regressions.append(
                    f"Regression in '{metric_name}' (higher is better): "
                    f"current {curr_val:.4f} < baseline {base_val:.4f} - tol {tol:.4f} "
                    f"(dropped by {delta:.4f})"
                )
        elif direction == "lower":
            # Lower is better: regression if current > baseline + tol
            if curr_val > (base_val + tol):
                delta = curr_val - base_val
                regressions.append(
                    f"Regression in '{metric_name}' (lower is better): "
                    f"current {curr_val:.4f} > baseline {base_val:.4f} + tol {tol:.4f} "
                    f"(increased by {delta:.4f})"
                )

    return regressions


# ============================================================================
# 6. CLI ENTRY POINT
# ============================================================================

def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI for evals.report.

    Usage:
        python -m evals.report compare REPORT BASELINE [--tolerances JSON]
        python -m evals.report render REPORT [--feedback aggregate|per_item] [--out DIR]
    """
    parser = argparse.ArgumentParser(
        prog="python -m evals.report",
        description="RecipeBoxer evaluation report renderer and baseline regression comparator.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: compare
    compare_parser = subparsers.add_parser("compare", help="Compare an eval report against a baseline.")
    compare_parser.add_argument("report", type=Path, help="Path to current report JSON.")
    compare_parser.add_argument("baseline", type=Path, help="Path to baseline JSON.")
    compare_parser.add_argument(
        "--tolerances",
        type=str,
        default=None,
        help="Optional JSON string or path to JSON file specifying metric tolerances.",
    )

    # Subcommand: render
    render_parser = subparsers.add_parser("render", help="Render report JSON and Markdown files.")
    render_parser.add_argument("report", type=Path, help="Path to report JSON.")
    render_parser.add_argument(
        "--feedback",
        choices=["aggregate", "per_item"],
        default="aggregate",
        help="Feedback mode (per_item only allowed for tier T0).",
    )
    render_parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output directory to write report.json and report.md.",
    )

    args = parser.parse_args(argv)

    if args.command == "compare":
        if not args.report.exists():
            sys.stderr.write(f"Error: Report file not found: {args.report}\n")
            return 2
        if not args.baseline.exists():
            sys.stderr.write(f"Error: Baseline file not found: {args.baseline}\n")
            return 2

        with open(args.report, "r", encoding="utf-8") as f:
            report_data = json.load(f)
        with open(args.baseline, "r", encoding="utf-8") as f:
            baseline_data = json.load(f)

        tolerances = None
        if args.tolerances:
            tol_path = Path(args.tolerances)
            if tol_path.exists():
                with open(tol_path, "r", encoding="utf-8") as f:
                    tolerances = json.load(f)
            else:
                tolerances = json.loads(args.tolerances)

        regressions = compare(report_data, baseline_data, tolerances=tolerances)
        if regressions:
            sys.stderr.write("=" * 60 + "\n")
            sys.stderr.write(f"BASELINE REGRESSION DETECTED ({len(regressions)} failures):\n")
            sys.stderr.write("=" * 60 + "\n")
            for reg in regressions:
                sys.stderr.write(f"  FAILED: {reg}\n")
            sys.stderr.write("=" * 60 + "\n")
            return 1

        sys.stdout.write("All metrics within baseline tolerances. Comparison PASSED.\n")
        return 0

    if args.command == "render":
        if not args.report.exists():
            sys.stderr.write(f"Error: Report file not found: {args.report}\n")
            return 2

        with open(args.report, "r", encoding="utf-8") as f:
            report_data = json.load(f)

        try:
            json_text, md_text = render(report_data, feedback=args.feedback)
        except ValueError as exc:
            sys.stderr.write(f"Error: {exc}\n")
            return 1

        if args.out:
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / "report.json").write_text(json_text, encoding="utf-8")
            (args.out / "report.md").write_text(md_text, encoding="utf-8")
            sys.stdout.write(f"Rendered report.json and report.md in {args.out}\n")
        else:
            sys.stdout.write(md_text + "\n")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
