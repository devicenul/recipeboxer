"""Scorer CLI and evaluation report generator for RecipeBoxer (WP-E4).

Supports:
- Evaluation scoring against encrypted ground truth (or T0 manifest):
  `python -m evals.score --tier T1 --outputs DIR --report DIR [--feedback aggregate|per_item] [--compare BASELINE.json]`
- Oracle plan extraction generation:
  `python -m evals.score oracle-plan --tier T1 --out plan.json [--decks d01,d02]`
- Strict enforcement of feedback policy (per_item only allowed on T0).
- Automatic N/A reporting when status is 'no_live_model'.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from evals.report import compare as compare_reports, render as render_report
from evals.scoring.evaluator import score_evaluation
from evals.scoring.gt import create_oracle_plan, load_ground_truth

logger = logging.getLogger("evals.score")


def create_parser() -> argparse.ArgumentParser:
    """Builds the main argument parser for evals.score."""
    parser = argparse.ArgumentParser(
        prog="python -m evals.score",
        description="Ground-truth scorer and evaluation report generator.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Optional subcommand")

    # Oracle plan generator subcommand
    oracle_p = subparsers.add_parser(
        "oracle-plan",
        help="Generate extraction-only oracle grouping plan from ground truth",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    oracle_p.add_argument("--tier", type=str, required=True, help="Evaluation benchmark tier (e.g. T0, T1)")
    oracle_p.add_argument("--out", type=str, required=True, help="Output destination JSON path for plan")
    oracle_p.add_argument("--decks", type=str, default=None, help="Comma-separated deck IDs")
    oracle_p.add_argument("--seed", type=int, default=42, help="RNG seed for face and set shuffling")
    oracle_p.add_argument("--identity", type=str, default=None, help="Path to age identity file")

    # Main scorer options
    parser.add_argument("--tier", type=str, help="Evaluation benchmark tier (e.g. T0, T1)")
    parser.add_argument("--outputs", type=str, help="Directory containing SUT outputs.json and ledger.json")
    parser.add_argument("--report", type=str, help="Destination directory for report.json and report.md")
    parser.add_argument(
        "--feedback",
        type=str,
        default="aggregate",
        choices=["aggregate", "per_item"],
        help="Feedback granularity (per_item allowed only for T0)",
    )
    parser.add_argument("--compare", type=str, default=None, help="Path to baseline report JSON to check for regressions")
    parser.add_argument("--identity", type=str, default=None, help="Path to age identity file for decrypting T1-T4")

    return parser


def run_oracle_plan_subcommand(args: argparse.Namespace) -> int:
    """Executes the oracle-plan subcommand."""
    tier = str(args.tier).strip().upper()
    deck_filter = (
        {d.strip() for d in args.decks.split(",") if d.strip()} if args.decks else None
    )

    logger.info(f"Loading ground truth for tier {tier}...")
    gt_data = load_ground_truth(tier=tier, identity_path=args.identity)

    logger.info(f"Constructing oracle plan (seed={args.seed})...")
    plan = create_oracle_plan(gt_data=gt_data, deck_filter=deck_filter, seed=args.seed)

    out_p = Path(args.out).resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(f"Oracle plan written to {out_p} ({len(plan.get('decks', []))} deck(s))")
    return 0


def run_scoring(args: argparse.Namespace) -> int:
    """Executes evaluation scoring and report generation."""
    if not args.tier or not args.outputs or not args.report:
        print("Error: --tier, --outputs, and --report are required for scoring.", file=sys.stderr)
        return 2

    tier = str(args.tier).strip().upper()
    feedback = str(args.feedback).strip().lower()

    # Enforce feedback policy (contracts §4)
    if tier != "T0" and feedback == "per_item":
        raise ValueError(
            f"Feedback policy violation: per_item feedback is strictly prohibited for tier '{tier}'. "
            "Only aggregate feedback is permitted for T1–T4."
        )

    outputs_dir = Path(args.outputs).resolve()
    outputs_file = outputs_dir / "outputs.json"
    if not outputs_file.exists():
        raise FileNotFoundError(f"SUT outputs file not found: {outputs_file}")

    outputs_data = json.loads(outputs_file.read_text(encoding="utf-8"))

    ledger_file = outputs_dir / "ledger.json"
    ledger_data: Optional[Dict[str, Any]] = None
    if ledger_file.exists():
        try:
            ledger_data = json.loads(ledger_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Could not parse ledger.json: {e}")

    sut_status = outputs_data.get("status", "ok")

    # Load GT unless status is cheat_detected or no_live_model (which don't need GT)
    gt_data: Dict[str, Any] = {}
    if sut_status not in ("cheat_detected", "no_live_model"):
        logger.info(f"Loading ground truth for tier {tier}...")
        gt_data = load_ground_truth(tier=tier, identity_path=args.identity)

    # Compute scores
    logger.info("Computing evaluation scores and metrics...")
    raw_report = score_evaluation(
        outputs_data=outputs_data,
        gt_data=gt_data,
        ledger_data=ledger_data,
        feedback=feedback,
    )

    # Render report JSON and Markdown using evals.report
    json_text, md_text = render_report(raw_report, feedback=feedback)

    report_dir = Path(args.report).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)

    report_json_path = report_dir / "report.json"
    report_md_path = report_dir / "report.md"

    report_json_path.write_text(json_text, encoding="utf-8")
    report_md_path.write_text(md_text, encoding="utf-8")

    print(f"Scoring complete. Report written to {report_dir}")
    print(f"- JSON: {report_json_path}")
    print(f"- Markdown: {report_md_path}")

    # Baseline comparison if requested
    if args.compare:
        baseline_p = Path(args.compare).resolve()
        if not baseline_p.exists():
            raise FileNotFoundError(f"Baseline report file not found: {baseline_p}")
        baseline_data = json.loads(baseline_p.read_text(encoding="utf-8"))
        regressions = compare_reports(raw_report, baseline_data)
        if regressions:
            print("\nREGRESSIONS DETECTED:", file=sys.stderr)
            for r in regressions:
                print(f"  - {r}", file=sys.stderr)
            return 1
        else:
            print("\nBaseline comparison passed (zero regressions).")

    return 0


def score_tier(
    tier: str,
    outputs_dir: Union[str, Path],
    report_dir: Union[str, Path],
    feedback: str = "aggregate",
    compare: Optional[Union[str, Path]] = None,
    identity: Optional[Union[str, Path]] = None,
) -> Tuple[str, str]:
    """Programmatic API to score a tier run and write report artifacts."""
    args = argparse.Namespace(
        subcommand=None,
        tier=tier,
        outputs=str(outputs_dir),
        report=str(report_dir),
        feedback=feedback,
        compare=str(compare) if compare else None,
        identity=str(identity) if identity else None,
    )
    ret = run_scoring(args)
    if ret != 0:
        raise RuntimeError(f"Scoring failed with exit code {ret}")
    rdir = Path(report_dir)
    return (
        (rdir / "report.json").read_text(encoding="utf-8"),
        (rdir / "report.md").read_text(encoding="utf-8"),
    )


def main(argv: Optional[List[str]] = None) -> int:
    """Main execution entry point for evals.score."""
    parser = create_parser()
    args = parser.parse_args(args=argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if args.subcommand == "oracle-plan":
        return run_oracle_plan_subcommand(args)
    else:
        return run_scoring(args)


if __name__ == "__main__":
    sys.exit(main())
