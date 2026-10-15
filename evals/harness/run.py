"""Black-box evaluation harness runner for RecipeBoxer (WP-E3).

Executes the System Under Test (SUT) against evaluation tiers (T0-T4) adhering strictly to:
- Contracts §3: rb-out/1 outputs.json, ledger.json, run.log.
- INV-AC1, INV-AC5: Subprocess execution with scrubbed environment allow-list, cwd=temp dir,
  audit hook injection (sut_boot.py) aborting with exit code 86 and CHEAT_DETECTED.
- Opaque image copying with random filenames and reverse mapping post-run.
- Support for --oracle-plan (skipping correlation grouping via --image-sets) and --variant.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from evals.harness.obfuscation import DeckMapping, prepare_obfuscated_deck

logger = logging.getLogger("evals.harness.run")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)

ENV_ALLOWLIST = {
    "PATH",
    "PYTHONPATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LD_LIBRARY_PATH",
    "PROJECT_CACHE_DIR",
    "PYTHONPYCACHEPREFIX",
    "RECIPEBOXER_API_HOST",
    "RECIPEBOXER_API_PORT",
    "RECIPEBOXER_MAX_CONCURRENCY",
    "RB_FEATURE_TOGGLES",
    "RB_DEBUG_GROUPING",
}

FORBIDDEN_ENV_SUBSTRINGS = ["AGE", "VAULT", "SECRET", "RB_EVAL"]


def get_repo_root() -> Path:
    """Returns absolute path to the repository root."""
    return Path(__file__).resolve().parent.parent.parent


def get_git_commit() -> str:
    """Retrieves current git commit hash, or 'unknown' if not in git repo."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(get_repo_root()),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return "unknown"


def build_scrubbed_env(allow_live_key: bool = False) -> Dict[str, str]:
    """Builds a strictly scrubbed environment dictionary for SUT subprocess execution."""
    scrubbed: Dict[str, str] = {}
    current_env = os.environ

    for key, value in current_env.items():
        # Check forbidden substrings
        if any(bad in key for bad in FORBIDDEN_ENV_SUBSTRINGS):
            continue

        if key in ENV_ALLOWLIST:
            scrubbed[key] = value

    # Always ensure repo root is in PYTHONPATH
    repo_root = str(get_repo_root())
    existing_pythonpath = scrubbed.get("PYTHONPATH", "")
    if existing_pythonpath:
        scrubbed["PYTHONPATH"] = f"{repo_root}:{existing_pythonpath}"
    else:
        scrubbed["PYTHONPATH"] = repo_root

    # Live key propagation if requested and allowed
    if allow_live_key:
        for k in ("GEMINI_API_KEY", "RECIPEBOXER_GEMINI_API_KEY"):
            if k in current_env and current_env[k].strip():
                scrubbed[k] = current_env[k].strip()

    return scrubbed


def resolve_deck_images(
    tier: str,
    images_dir: Optional[Union[str, Path]] = None,
    deck_filter: Optional[Set[str]] = None,
) -> Dict[str, List[Path]]:
    """Resolves deck IDs to list of image paths for the specified evaluation tier."""
    repo_root = get_repo_root()
    norm_tier = tier.upper()
    decks: Dict[str, List[Path]] = {}

    if images_dir:
        base_path = Path(images_dir).resolve()
        if not base_path.exists():
            raise FileNotFoundError(f"Specified images-dir does not exist: {base_path}")

        # Check if base_path has subdirectories representing decks
        subdirs = [d for d in sorted(base_path.iterdir()) if d.is_dir() and not d.name.startswith(".")]
        if subdirs:
            for d in subdirs:
                if deck_filter and d.name not in deck_filter:
                    continue
                imgs = [
                    f for f in sorted(d.iterdir())
                    if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
                ]
                if imgs:
                    decks[d.name] = imgs
        else:
            deck_name = deck_filter.pop() if (deck_filter and len(deck_filter) == 1) else "d01"
            imgs = [
                f for f in sorted(base_path.iterdir())
                if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
            ]
            decks[deck_name] = imgs
        return decks

    if norm_tier == "T0":
        sample_cards_dir = repo_root / "tests" / "fixtures" / "sample_cards"
        if not sample_cards_dir.exists():
            raise FileNotFoundError(f"T0 sample cards directory not found at {sample_cards_dir}")
        imgs = [
            f for f in sorted(sample_cards_dir.iterdir())
            if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
        ]
        deck_name = "d01"
        if deck_filter and len(deck_filter) == 1:
            deck_name = next(iter(deck_filter))
        decks[deck_name] = imgs
        return decks

    # T1..T4 in evals/corpora/<tier>/
    corpus_dir = repo_root / "evals" / "corpora" / tier.lower()
    if not corpus_dir.exists():
        corpus_dir = repo_root / "evals" / "corpora" / tier.upper()

    if not corpus_dir.exists():
        raise FileNotFoundError(f"Corpus directory not found for tier {tier} at {corpus_dir}")

    decks_json_path = corpus_dir / "DECKS.json"
    if decks_json_path.exists():
        try:
            deck_list = json.loads(decks_json_path.read_text(encoding="utf-8"))
            for entry in deck_list:
                did = entry.get("deck_id")
                if did:
                    if deck_filter and did not in deck_filter:
                        continue
                    deck_path = corpus_dir / did
                    if deck_path.exists() and deck_path.is_dir():
                        imgs = [
                            f for f in sorted(deck_path.iterdir())
                            if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
                        ]
                        decks[did] = imgs
        except Exception as e:
            logger.warning(f"Could not parse DECKS.json in {corpus_dir}: {e}")

    if not decks:
        # Check subdirectories
        for d in sorted(corpus_dir.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                if deck_filter and d.name not in deck_filter:
                    continue
                imgs = [
                    f for f in sorted(d.iterdir())
                    if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
                ]
                if imgs:
                    decks[d.name] = imgs

    if not decks:
        # Check flat images in corpus_dir
        imgs = [
            f for f in sorted(corpus_dir.iterdir())
            if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
        ]
        if imgs:
            decks["d01"] = imgs

    return decks


def run_benchmark(
    tier: str,
    out_dir: Union[str, Path],
    decks: Optional[Sequence[str]] = None,
    images_dir: Optional[Union[str, Path]] = None,
    oracle_plan: Optional[Union[str, Path]] = None,
    variant: str = "opaque",
    profile: str = "balanced",
    model_profile: Optional[str] = None,
    timeout_s: int = 600,
    seed: int = 42,
    budget_usd: Optional[float] = None,
    max_calls: Optional[int] = None,
) -> Dict[str, Any]:
    """Runs black-box SUT evaluation across deck corpora and outputs rb-out/1 artifacts."""
    eff_profile = model_profile or profile
    out_path = Path(out_dir).resolve()
    out_path.mkdir(parents=True, exist_ok=True)
    run_log_path = out_path / "run.log"

    log_fp = open(run_log_path, "w", encoding="utf-8")

    def _log(msg: str) -> None:
        log_fp.write(f"{msg}\n")
        log_fp.flush()
        logger.info(msg)

    _log(f"--- Starting RecipeBoxer SUT Evaluation Run [tier={tier}, variant={variant}] ---")

    repo_root = get_repo_root()
    sut_boot_path = repo_root / "evals" / "harness" / "sut_boot.py"
    if not sut_boot_path.exists():
        raise FileNotFoundError(f"SUT boot script not found at {sut_boot_path}")

    # Detect live Gemini key in host environment
    has_live_key = bool(
        os.environ.get("GEMINI_API_KEY", "").strip()
        or os.environ.get("RECIPEBOXER_GEMINI_API_KEY", "").strip()
    )

    deck_filter = set(decks) if decks else None
    resolved_decks = resolve_deck_images(tier, images_dir=images_dir, deck_filter=deck_filter)
    _log(f"Resolved {len(resolved_decks)} deck(s) for tier {tier}: {list(resolved_decks.keys())}")

    # Parse oracle plan if provided
    oracle_data: Optional[Dict[str, Any]] = None
    if oracle_plan:
        oracle_p = Path(oracle_plan).resolve()
        if not oracle_p.exists():
            raise FileNotFoundError(f"Oracle plan file not found: {oracle_p}")
        oracle_data = json.loads(oracle_p.read_text(encoding="utf-8"))

    track = "oracle_grouping" if oracle_plan else "end_to_end"
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    git_commit = get_git_commit()

    deck_outputs: List[Dict[str, Any]] = []
    all_ledger_calls: List[Dict[str, Any]] = []
    overall_status = "ok"

    for deck_id, img_paths in resolved_decks.items():
        _log(f"\nProcessing Deck '{deck_id}' ({len(img_paths)} images)...")

        # Create isolated temp directory for SUT execution
        with tempfile.TemporaryDirectory(prefix=f"rb_deck_{deck_id}_") as temp_dir_str:
            temp_dir = Path(temp_dir_str)

            # Apply variant if requested and module exists
            effective_img_paths = list(img_paths)
            if variant != "opaque":
                try:
                    import evals.variants as ev_variants
                    if hasattr(ev_variants, "apply_variant"):
                        effective_img_paths = ev_variants.apply_variant(
                            effective_img_paths, variant=variant, seed=seed
                        )
                except ImportError:
                    _log(f"Variant '{variant}' requested but evals.variants not yet available; using opaque copy.")

            # Obfuscate deck images with random names in random order
            deck_map = prepare_obfuscated_deck(
                image_paths=effective_img_paths,
                target_dir=temp_dir,
                strip_exif=(variant == "reencode"),
                seed=seed,
            )

            # Prepare oracle image sets if oracle plan active
            oracle_file_path: Optional[Path] = None
            if oracle_data:
                # Find deck in oracle plan
                deck_oracle_sets: List[List[str]] = []
                for d in oracle_data.get("decks", []):
                    if d.get("deck_id") == deck_id or len(resolved_decks) == 1:
                        for s in d.get("sets", []):
                            translated = [deck_map.map_orig_to_temp(img_id) for img_id in s]
                            deck_oracle_sets.append(translated)
                        break

                if deck_oracle_sets:
                    oracle_file_path = temp_dir / "oracle_sets.json"
                    oracle_file_path.write_text(json.dumps(deck_oracle_sets), encoding="utf-8")

            # Prepare SUT invocation command
            temp_out = temp_dir / "sut_output.json"
            cmd = [
                sys.executable,
                str(sut_boot_path),
                "analyze",
                "--input",
                str(temp_dir),
                "--out",
                str(temp_out),
                "--auto-approve",
                "--profile",
                eff_profile,
            ]
            if budget_usd is not None:
                cmd.extend(["--budget-usd", str(budget_usd)])
            if max_calls is not None:
                cmd.extend(["--max-calls", str(max_calls)])
            if oracle_file_path:
                cmd.extend(["--image-sets", str(oracle_file_path)])

            # Scrubbed execution environment
            sut_env = build_scrubbed_env(allow_live_key=has_live_key)

            _log(f"Executing SUT subprocess for deck '{deck_id}' (cwd={temp_dir})...")
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(temp_dir),
                    env=sut_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=timeout_s,
                )
                _log(f"SUT Exit Code: {proc.returncode}")
                if proc.stdout:
                    _log(f"--- SUT STDOUT ---\n{proc.stdout.strip()}")
                if proc.stderr:
                    _log(f"--- SUT STDERR ---\n{proc.stderr.strip()}")

                if proc.returncode == 86 or "CHEAT_DETECTED" in proc.stderr or "CHEAT_DETECTED" in proc.stdout:
                    _log("CHEAT_DETECTED in subprocess! Tripping cheat detection invariant.")
                    overall_status = "cheat_detected"
                    deck_outputs.append({
                        "deck_id": deck_id,
                        "recipes": [],
                        "unassigned_image_ids": [p.name for p in img_paths],
                        "failures": [{
                            "stage": "EXECUTION",
                            "reason": "CHEAT_DETECTED",
                            "image_ids": [p.name for p in img_paths],
                            "message": "Subprocess tripped audit hook (exit code 86).",
                        }],
                    })
                    break

                # Parse SUT output file
                if temp_out.exists():
                    sut_res = json.loads(temp_out.read_text(encoding="utf-8"))
                    sut_status = sut_res.get("status", "ok")

                    if sut_status == "budget_exceeded":
                        _log(f"Budget cap exceeded in SUT execution for deck '{deck_id}'. Halting further deck runs.")
                        overall_status = "budget_exceeded"
                    elif sut_status == "no_live_model" and overall_status not in ("cheat_detected", "budget_exceeded"):
                        overall_status = "no_live_model"
                    elif sut_status == "error" and overall_status == "ok":
                        overall_status = "error"


                    # Map temp image names back to original corpus image IDs
                    mapped_recipes: List[Dict[str, Any]] = []
                    for r in sut_res.get("recipes", []):
                        raw_cids = r.get("image_ids", [])
                        orig_cids = [deck_map.map_temp_to_orig(cid) for cid in raw_cids]
                        r_copy = dict(r)
                        r_copy["image_ids"] = orig_cids
                        if "recipe" in r_copy and isinstance(r_copy["recipe"], dict):
                            r_copy["recipe"] = dict(r_copy["recipe"])
                            r_copy["recipe"]["card_ids"] = orig_cids
                        mapped_recipes.append(r_copy)

                    mapped_unassigned = [
                        deck_map.map_temp_to_orig(cid)
                        for cid in sut_res.get("unassigned_image_ids", [])
                    ]

                    mapped_failures: List[Dict[str, Any]] = []
                    seen_mapped_f: set[tuple[str, tuple[str, ...]]] = set()
                    for f in sut_res.get("failures", []):
                        f_copy = dict(f)
                        raw_f_cids = f.get("image_ids", [])
                        f_orig_cids = [deck_map.map_temp_to_orig(cid) for cid in raw_f_cids]
                        f_copy["image_ids"] = f_orig_cids
                        f_key = (f_copy.get("stage", ""), tuple(sorted(str(cid) for cid in f_orig_cids)))
                        if f_key not in seen_mapped_f:
                            seen_mapped_f.add(f_key)
                            mapped_failures.append(f_copy)

                    deck_outputs.append({
                        "deck_id": deck_id,
                        "status": sut_status,
                        "recipes": mapped_recipes,
                        "unassigned_image_ids": mapped_unassigned,
                        "failures": mapped_failures,
                    })

                    # Ledger calls
                    deck_ledger = sut_res.get("ledger", {})
                    all_ledger_calls.extend(deck_ledger.get("calls", []))

                    if sut_status == "budget_exceeded":
                        break
                else:
                    _log(f"ERROR: SUT failed to produce output file {temp_out}")
                    if overall_status == "ok":
                        overall_status = "error"
                    deck_outputs.append({
                        "deck_id": deck_id,
                        "status": "failed",
                        "recipes": [],
                        "unassigned_image_ids": [p.name for p in img_paths],
                        "failures": [{
                            "stage": "EXECUTION",
                            "reason": "OUTPUT_MISSING",
                            "image_ids": [p.name for p in img_paths],
                            "message": f"SUT exited with code {proc.returncode} but produced no output file.",
                        }],
                    })

            except subprocess.TimeoutExpired:
                _log(f"ERROR: SUT subprocess timed out after {timeout_s}s on deck '{deck_id}'")
                if overall_status == "ok":
                    overall_status = "error"
                deck_outputs.append({
                    "deck_id": deck_id,
                    "status": "failed",
                    "recipes": [],
                    "unassigned_image_ids": [p.name for p in img_paths],
                    "failures": [{
                        "stage": "EXECUTION",
                        "reason": "TIMEOUT",
                        "image_ids": [p.name for p in img_paths],
                        "message": f"SUT subprocess exceeded {timeout_s}s timeout limit.",
                    }],
                })

    log_fp.close()

    # Aggregate ledger totals
    total_in = sum(c.get("input_tokens", 0) for c in all_ledger_calls)
    total_out = sum(c.get("output_tokens", 0) for c in all_ledger_calls)
    total_think = sum(c.get("thinking_tokens", 0) for c in all_ledger_calls)
    total_lat = sum(c.get("latency_ms", 0.0) for c in all_ledger_calls)
    total_usd = 0.0
    has_usd = False
    for c in all_ledger_calls:
        cost = c.get("cost_usd")
        if cost is not None:
            total_usd += float(cost)
            has_usd = True

    totals_payload: Dict[str, Any] = {
        "calls": len(all_ledger_calls),
        "input_tokens": total_in,
        "output_tokens": total_out,
        "thinking_tokens": total_think,
        "total_tokens": total_in + total_out,
        "latency_ms": total_lat,
    }
    if has_usd:
        totals_payload["total_usd"] = round(total_usd, 6)

    ledger_payload = {
        "calls": all_ledger_calls,
        "totals": totals_payload,
    }
    (out_path / "ledger.json").write_text(json.dumps(ledger_payload, indent=2), encoding="utf-8")

    if overall_status != "cheat_detected":
        if any(d.get("status") == "budget_exceeded" for d in deck_outputs) or overall_status == "budget_exceeded":
            overall_status = "budget_exceeded"
        elif any(d.get("status") == "no_live_model" for d in deck_outputs) or overall_status == "no_live_model":
            overall_status = "no_live_model"
        else:
            total_recipes = sum(len(d.get("recipes", [])) for d in deck_outputs)
            total_failures = sum(len(d.get("failures", [])) for d in deck_outputs)
            has_deck_partial = any(
                d.get("status") in ("partial", "error", "failed") or len(d.get("failures", [])) > 0
                for d in deck_outputs
            )
            if total_recipes == 0:
                overall_status = "failed"
            elif has_deck_partial or total_failures > 0:
                overall_status = "partial"
            else:
                overall_status = "ok"

    deck_failure_summaries: Dict[str, Any] = {}
    for d in deck_outputs:
        did = d.get("deck_id", "d01")
        f_list = d.get("failures", [])
        if f_list:
            by_stage: Dict[str, int] = {}
            reasons = set()
            for f in f_list:
                st = f.get("stage", "UNKNOWN")
                by_stage[st] = by_stage.get(st, 0) + 1
                r = f.get("reason")
                if r:
                    reasons.add(str(r))
            deck_failure_summaries[did] = {
                "failure_count": len(f_list),
                "by_stage": by_stage,
                "reasons": sorted(list(reasons)),
            }

    outputs_payload = {
        "schema": "rb-out/1",
        "run_id": run_id,
        "tier": tier,
        "git_commit": git_commit,
        "track": track,
        "variant": variant,
        "model_profile": eff_profile,
        "live": has_live_key,
        "decks": deck_outputs,
        "status": overall_status,
    }
    if deck_failure_summaries:
        outputs_payload["failure_summary"] = deck_failure_summaries
    (out_path / "outputs.json").write_text(json.dumps(outputs_payload, indent=2), encoding="utf-8")

    return outputs_payload


execute_run = run_benchmark


def create_run_parser() -> argparse.ArgumentParser:
    """Creates the CLI argument parser for evals.harness.run."""
    parser = argparse.ArgumentParser(
        prog="python -m evals.harness.run",
        description="Black-box evaluation harness for RecipeBoxer.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--tier", type=str, default="T0", help="Evaluation benchmark tier (e.g. T0, T1, T2)")
    parser.add_argument("--out", type=str, default="rb-out", help="Output directory for outputs.json and ledger.json")
    parser.add_argument("--decks", type=str, default=None, help="Comma-separated deck IDs to evaluate (e.g. d01,d02)")
    parser.add_argument("--images-dir", type=str, default=None, help="Explicit path to images directory")
    parser.add_argument("--oracle-plan", type=str, default=None, help="Path to oracle plan JSON file")
    parser.add_argument(
        "--variant",
        type=str,
        default="opaque",
        choices=["opaque", "misleading", "shuffle", "reencode"],
        help="Naming and formatting variant",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default="balanced",
        choices=["economy", "balanced", "best"],
        help="Model quality profile",
    )
    parser.add_argument("--budget-usd", type=float, default=None, help="Evaluation run spending cap in USD (WP-B3)")
    parser.add_argument("--max-calls", type=int, default=None, help="Maximum number of LLM generation calls (WP-B3)")
    parser.add_argument("--timeout", type=int, default=600, help="Per-deck timeout in seconds")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI execution entry point for evals.harness.run."""
    parser = create_run_parser()
    args = parser.parse_args(args=argv)
    deck_list = [d.strip() for d in args.decks.split(",") if d.strip()] if args.decks else None

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    try:
        res = run_benchmark(
            tier=args.tier,
            out_dir=args.out,
            decks=deck_list,
            images_dir=args.images_dir,
            oracle_plan=args.oracle_plan,
            variant=args.variant,
            profile=args.profile,
            timeout_s=args.timeout,
            budget_usd=args.budget_usd,
            max_calls=args.max_calls,
        )

        print(f"Benchmark run complete. Status: {res.get('status')}. Artifacts in {args.out}")
        return 0
    except Exception as exc:
        logger.error(f"Fatal harness execution error: {exc}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
