"""Ablation matrix runner for RecipeBoxer correlation feature toggles (WP-GE, G9).

Executes evals.harness.run across RB_FEATURE_TOGGLES variants:
- all_on (baseline: pixels=True, capture=True, physical=True, content=True, set_verify=True, embeddings=False, ocr=False)
- -pixels (pixels=False)
- -content (content=False)
- -physical (physical=False)
- -capture (capture=False)
- -set_verify (set_verify=False)
- +embeddings (embeddings=True)
- +ocr (ocr=True)

Scores outputs with evals.score, breaks out grouping metrics (B-Cubed F1, exact cluster acc)
by deck_type (homogeneous vs mixed; aggregate only on T1+ per feedback policy).
Supports --budget-usd spend cap per variant run.
When running offline (no GEMINI_API_KEY or --offline), prints planned matrix + N/A without assertions.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("evals.ablations")

# Canonical ablation variants per REMEDIATION_PLAN.md §9 (G9) and WAVE5_CONTRACTS.md §1.2
ABLATION_VARIANTS: List[Tuple[str, Dict[str, bool]]] = [
    (
        "all_on",
        {
            "pixels": True,
            "capture": True,
            "physical": True,
            "content": True,
            "set_verify": True,
            "embeddings": False,
            "ocr": False,
        },
    ),
    (
        "-pixels",
        {
            "pixels": False,
            "capture": True,
            "physical": True,
            "content": True,
            "set_verify": True,
            "embeddings": False,
            "ocr": False,
        },
    ),
    (
        "-content",
        {
            "pixels": True,
            "capture": True,
            "physical": True,
            "content": False,
            "set_verify": True,
            "embeddings": False,
            "ocr": False,
        },
    ),
    (
        "-physical",
        {
            "pixels": True,
            "capture": True,
            "physical": False,
            "content": True,
            "set_verify": True,
            "embeddings": False,
            "ocr": False,
        },
    ),
    (
        "-capture",
        {
            "pixels": True,
            "capture": False,
            "physical": True,
            "content": True,
            "set_verify": True,
            "embeddings": False,
            "ocr": False,
        },
    ),
    (
        "-set_verify",
        {
            "pixels": True,
            "capture": True,
            "physical": True,
            "content": True,
            "set_verify": False,
            "embeddings": False,
            "ocr": False,
        },
    ),
    (
        "+embeddings",
        {
            "pixels": True,
            "capture": True,
            "physical": True,
            "content": True,
            "set_verify": True,
            "embeddings": True,
            "ocr": False,
        },
    ),
    (
        "+ocr",
        {
            "pixels": True,
            "capture": True,
            "physical": True,
            "content": True,
            "set_verify": True,
            "embeddings": False,
            "ocr": True,
        },
    ),
]


def format_bool_icon(val: bool) -> str:
    """Formats boolean flag as ON/OFF string."""
    return "ON" if val else "off"


def build_ablation_matrix_table(
    results: Optional[Dict[str, Dict[str, Any]]] = None,
    offline: bool = False,
) -> str:
    """Constructs a Markdown table representing the ablation matrix.

    When offline or results is None, score columns are marked 'N/A (offline)'.
    When results are provided, reports Homogeneous vs Mixed B-Cubed F1 and Exact Cluster Accuracy.

    Args:
        results: Mapping from variant name to scored metrics dictionary.
        offline: If True, forces N/A offline output.

    Returns:
        GitHub-flavored Markdown table string.
    """
    lines: List[str] = []
    lines.append("| Variant | Pixels | Capture | Physical | Content | SetVerify | Embeddings | OCR | Homogeneous B-Cubed F1 | Mixed B-Cubed F1 | Status |")
    lines.append("|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|---|")

    for name, toggles in ABLATION_VARIANTS:
        pix = format_bool_icon(toggles["pixels"])
        cap = format_bool_icon(toggles["capture"])
        phy = format_bool_icon(toggles["physical"])
        con = format_bool_icon(toggles["content"])
        sv = format_bool_icon(toggles["set_verify"])
        emb = format_bool_icon(toggles["embeddings"])
        ocr = format_bool_icon(toggles["ocr"])

        if offline or results is None or name not in results:
            homo_f1 = "N/A"
            mixed_f1 = "N/A"
            status = "N/A (offline)" if (offline or results is None) else "Pending"
        else:
            v_res = results[name]
            h_val = v_res.get("homogeneous", {}).get("bcubed_f1")
            m_val = v_res.get("mixed", {}).get("bcubed_f1")
            homo_f1 = f"{h_val:.3f}" if (h_val is not None) else "N/A"
            mixed_f1 = f"{m_val:.3f}" if (m_val is not None) else "N/A"
            status = v_res.get("status", "Completed")

        lines.append(
            f"| `{name}` | {pix} | {cap} | {phy} | {con} | {sv} | {emb} | {ocr} | {homo_f1} | {mixed_f1} | {status} |"
        )

    return "\n".join(lines)


def run_ablations(
    tier: str = "T0",
    budget_usd_per_run: Optional[float] = None,
    max_calls_per_run: Optional[int] = None,
    profile: str = "balanced",
    out_dir: Optional[Union[str, Path]] = None,
    offline: bool = False,
    identity_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Runs ablation matrix across all 8 feature toggle configurations.

    Args:
        tier: Evaluation tier (T0, T1, etc.).
        budget_usd_per_run: Spending cap in USD per ablation variant run.
        max_calls_per_run: Maximum LLM generation calls per ablation variant run.
        profile: Model quality profile ('economy', 'balanced', 'best').
        out_dir: Target output directory for evaluation reports and matrix dump.
        offline: If True, forces offline mode (prints planned matrix without running SUT).
        identity_path: Optional age identity key path for scoring encrypted tiers (T1+).

    Returns:
        Structured ablation report dictionary.
    """
    has_live_key = bool(
        os.environ.get("GEMINI_API_KEY", "").strip()
        or os.environ.get("RECIPEBOXER_GEMINI_API_KEY", "").strip()
    )

    is_offline = offline or not has_live_key

    if is_offline:
        logger.info("Executing ablations in OFFLINE mode (no live Gemini key). Printing planned matrix.")
        md_table = build_ablation_matrix_table(results=None, offline=True)
        print("\n=== RecipeBoxer Planned Feature Ablation Matrix ===")
        print(md_table)
        print("\nNote: Offline mode checks toggle schemas and matrix structure without asserting accuracy numbers.\n")

        return {
            "mode": "offline",
            "tier": tier,
            "variants": [v[0] for v in ABLATION_VARIANTS],
            "markdown_table": md_table,
            "results": {},
        }

    # Live execution: run each variant
    from evals.harness.run import run_benchmark
    from evals.scoring.evaluator import score_evaluation
    from evals.scoring.gt import load_ground_truth

    base_out = Path(out_dir or tempfile.mkdtemp(prefix="rb_ablations_")).resolve()
    base_out.mkdir(parents=True, exist_ok=True)

    variant_results: Dict[str, Dict[str, Any]] = {}

    for name, toggles in ABLATION_VARIANTS:
        logger.info(f"\n=======================================================")
        logger.info(f"Running Ablation Variant: {name}")
        logger.info(f"Toggles: {toggles}")
        logger.info(f"=======================================================\n")

        v_out_dir = base_out / name
        v_out_dir.mkdir(parents=True, exist_ok=True)

        # Set environment variable RB_FEATURE_TOGGLES
        original_toggles = os.environ.get("RB_FEATURE_TOGGLES")
        try:
            os.environ["RB_FEATURE_TOGGLES"] = json.dumps(toggles)

            # 1. Execute SUT via harness
            bench_res = run_benchmark(
                tier=tier,
                out_dir=v_out_dir,
                profile=profile,
                budget_usd=budget_usd_per_run,
                max_calls=max_calls_per_run,
            )

            # 2. Score predictions
            gt_data = load_ground_truth(tier=tier, identity_path=identity_path)
            outputs_path = v_out_dir / "outputs.json"
            ledger_path = v_out_dir / "ledger.json"

            outputs_data = json.loads(outputs_path.read_text(encoding="utf-8")) if outputs_path.exists() else {}
            ledger_data = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {}

            score_report = score_evaluation(
                outputs_data=outputs_data,
                gt_data=gt_data,
                ledger_data=ledger_data,
                feedback="aggregate" if tier.upper() != "T0" else "per_item",
            )

            # Group scores by deck_type (homogeneous vs mixed)
            deck_scores_by_type: Dict[str, List[Dict[str, Any]]] = {}
            for d in score_report.get("decks", []):
                dtype = d.get("deck_type", "mixed")
                grp = d.get("grouping", {})
                deck_scores_by_type.setdefault(dtype, []).append(grp)

            # Aggregate per deck_type
            type_aggregates: Dict[str, Dict[str, float]] = {}
            for dtype, scores_list in deck_scores_by_type.items():
                if scores_list:
                    avg_f1 = sum(s.get("bcubed_f1", 0.0) for s in scores_list) / len(scores_list)
                    avg_acc = sum(s.get("exact_cluster_accuracy", 0.0) for s in scores_list) / len(scores_list)
                    type_aggregates[dtype] = {
                        "bcubed_f1": round(avg_f1, 4),
                        "exact_cluster_accuracy": round(avg_acc, 4),
                    }

            variant_results[name] = {
                "status": "ok",
                "homogeneous": type_aggregates.get("homogeneous", {}),
                "mixed": type_aggregates.get("mixed", {}),
                "overall_grouping": score_report.get("metrics", {}).get("grouping", {}),
            }

        finally:
            if original_toggles is not None:
                os.environ["RB_FEATURE_TOGGLES"] = original_toggles
            else:
                os.environ.pop("RB_FEATURE_TOGGLES", None)

    md_table = build_ablation_matrix_table(results=variant_results, offline=False)
    print("\n=== RecipeBoxer Feature Ablation Results ===")
    print(md_table)

    summary_file = base_out / "ablation_summary.json"
    summary_file.write_text(
        json.dumps({"tier": tier, "results": variant_results}, indent=2),
        encoding="utf-8",
    )

    return {
        "mode": "live",
        "tier": tier,
        "results": variant_results,
        "markdown_table": md_table,
    }


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for evals.ablations."""
    parser = argparse.ArgumentParser(
        prog="python -m evals.ablations",
        description="Runs correlation feature toggle ablations matrix broken out by deck_type.",
    )
    parser.add_argument("--tier", type=str, default="T0", help="Evaluation benchmark tier (T0, T1, ...)")
    parser.add_argument("--budget-usd", type=float, default=None, help="Budget cap in USD per ablation run")
    parser.add_argument("--max-calls", type=int, default=None, help="Max LLM calls per ablation run")
    parser.add_argument("--profile", type=str, default="balanced", choices=["economy", "balanced", "best"])
    parser.add_argument("--out", type=str, default=None, help="Directory for ablation reports and logs")
    parser.add_argument("--offline", action="store_true", help="Force offline mode (prints planned matrix)")
    parser.add_argument("--identity", type=str, default=None, help="Path to age identity file for scoring T1+")
    args = parser.parse_args(args=argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    try:
        run_ablations(
            tier=args.tier,
            budget_usd_per_run=args.budget_usd,
            max_calls_per_run=args.max_calls,
            profile=args.profile,
            out_dir=args.out,
            offline=args.offline,
            identity_path=args.identity,
        )
        return 0
    except Exception as exc:
        logger.error(f"Ablation execution failed: {exc}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
