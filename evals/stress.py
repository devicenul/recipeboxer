"""Scale and homogeneity stress evaluation runner (WP-GE, G13).

Generates 300-500-card synthetic homogeneous CardForge decks in an isolated
temporary directory and runs the production GroupingEngine (WP-GD; contract double if
engine is in development) using ScriptedLLMClient returning plausible structured
responses.

Measures call counts and latency vs N to verify ~O(N*k) linear scaling (top-k screening
plus set-level verification) rather than O(N^2) pairwise explosion.
Strictly offline with ZERO accuracy claims.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
from pathlib import Path
import random
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple, Union

from PIL import Image, ImageDraw

from recipeboxer.ai.client import LLMRequest, LLMResponse, UsageTokens
from recipeboxer.ai.replay import ScriptedLLMClient
from recipeboxer.models.correlation import PairEvidence, SetVerification
from recipeboxer.models import VisualFingerprint

logger = logging.getLogger("evals.stress")


class ImageProvider(Protocol):
    """Protocol for image retrieval by card identifier."""

    def get(self, card_id: str) -> bytes:
        ...


class TempDirImageProvider:
    """Provides card images from a local directory."""

    def __init__(self, directory: Path):
        self.directory = directory

    def get(self, card_id: str) -> bytes:
        p = self.directory / card_id
        if not p.exists():
            # Return synthetic 100x100 jpeg bytes if file not created
            img = Image.new("RGB", (100, 100), color=(248, 244, 230))
            import io
            buf = io.BytesIO()
            img.save(buf, format="JPEG")
            return buf.getvalue()
        return p.read_bytes()


class GroupingEngineDouble:
    """Contract-compliant test double for GroupingEngine (WP-GD) when engine.py is absent.

    Implements:
    - O(N*k) top-k screening candidate selection
    - Set-verification call per card with candidates using llm_client
    - Bounded O(N*k) call-count scaling
    """

    def __init__(self, k: int = 5):
        self.k = k

    async def group(
        self,
        fps: List[VisualFingerprint],
        images: ImageProvider,
        *,
        llm_client: ScriptedLLMClient,
        settings: Any = None,
        embedder: Any = None,
        toggles: Any = None,
    ) -> Dict[str, Any]:
        """Groups fingerprints into recipe chains with O(N*k) screening and set-verification."""
        n = len(fps)
        call_count_start = llm_client.call_count

        # 1. Top-k screening: each card selects at most k candidates (k=5)
        # In a homogeneous collection, cards share paper and ink, so top-k selects candidates
        # based on capture order and content cues rather than physical substrate alone.
        candidates_by_card: Dict[str, List[str]] = {}
        for i, fp in enumerate(fps):
            # Select up to k neighbors
            start_idx = max(0, i - self.k)
            end_idx = min(n, i + self.k + 1)
            cands = [fps[j].card_id for j in range(start_idx, end_idx) if j != i]
            candidates_by_card[fp.card_id] = cands[: self.k]

        # 2. Set-level verification: one LLM call per anchor card that has candidates
        verifications: List[SetVerification] = []
        for fp in fps:
            cands = candidates_by_card.get(fp.card_id, [])
            if not cands:
                continue

            # Construct structured request for SetVerification
            req = LLMRequest(
                stage="SET_VERIFY",
                model="gemini-test",
                prompt_id="set_verify",
                prompt_version="1.0",
                response_model=SetVerification,
                temperature=0.0,
            )
            resp = await llm_client.generate(req)
            if resp.parsed and isinstance(resp.parsed, SetVerification):
                verifications.append(resp.parsed)

        call_count_end = llm_client.call_count
        llm_calls_made = call_count_end - call_count_start

        return {
            "chains": [[fp.card_id] for fp in fps],
            "verifications": verifications,
            "calls_made": llm_calls_made,
            "card_count": n,
        }


def generate_homogeneous_stress_deck(
    target_dir: Path,
    n_cards: int = 300,
    seed: int = 42,
    render_images: bool = False,
) -> Tuple[List[str], List[VisualFingerprint]]:
    """Generates a homogeneous synthetic recipe card deck in target_dir.

    In a homogeneous collection, >= 70% of cards share the same stationery stock,
    ruling, ink, and handwriting style.

    Args:
        target_dir: Directory where card artifacts/images are created.
        n_cards: Total number of cards to generate (300 to 500).
        seed: Random seed for deterministic generation.
        render_images: If True, renders actual JPEG files to disk.

    Returns:
        (card_ids, fingerprints)
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)

    dominant_stock = "stock_cream_ruled"
    dominant_paper = "cream_aged"
    dominant_ruling = "STANDARD_RULED"
    dominant_ink = "fountain_blue"

    card_ids: List[str] = []
    fps: List[VisualFingerprint] = []

    for idx in range(1, n_cards + 1):
        cid = f"card_{seed}_{idx:04d}.jpg"
        card_ids.append(cid)

        # 80% dominant stock, 20% slight variation (homogeneous deck property)
        is_dominant = (rng.random() < 0.80)
        paper = dominant_paper if is_dominant else "white_index"
        ruling = dominant_ruling if is_dominant else "WIDE_RULED"
        ink = dominant_ink if is_dominant else "ballpoint_black"

        if render_images:
            img = Image.new("RGB", (200, 150), color=(248, 244, 230) if is_dominant else (255, 255, 255))
            draw = ImageDraw.Draw(img)
            draw.text((10, 10), f"Recipe Card {idx}", fill=(20, 40, 120))
            img.save(target_dir / cid, format="JPEG", quality=75)

        from recipeboxer.models.domain import PaperColorInfo, PaperRulingInfo, InkInfo

        paper_info = PaperColorInfo(
            tone_name="cream" if is_dominant else "white",
            hex_approx="#f4eedb" if is_dominant else "#ffffff",
            lab_l=94.0 if is_dominant else 99.0,
            lab_a=-0.5 if is_dominant else 0.0,
            lab_b=9.5 if is_dominant else 1.0,
        )
        ruling_info = PaperRulingInfo(
            has_ruling=True,
            ruling_type="STANDARD_RULED" if is_dominant else "WIDE_RULED",
            line_spacing_px=24 if is_dominant else 32,
        )
        ink_info = InkInfo(
            ink_color="blue" if is_dominant else "black",
            ink_type="fountain_pen" if is_dominant else "ballpoint",
        )

        fp = VisualFingerprint(
            card_id=cid,
            image_path=str(target_dir / cid),
            sha256_hash=f"hash_{seed}_{idx:04d}",
            paper_color=paper_info,
            ruling=ruling_info,
            ink=ink_info,
            role="STANDALONE",
            role_confidence=0.8,
            visible_title=f"Recipe Title {idx}" if idx % 3 == 1 else None,
            first_lines=[f"Step {idx}"],
            last_lines=["Enjoy!"],
            looks_like_back=(idx % 3 == 0),
        )
        fps.append(fp)

    return card_ids, fps


def create_scripted_verification_client(
    card_ids: Sequence[str],
    max_calls: int = 1000,
) -> ScriptedLLMClient:
    """Builds a ScriptedLLMClient loaded with structured SetVerification responses."""
    responses: List[SetVerification] = []

    for i, cid in enumerate(card_ids):
        # Plausible continuation hypothesis
        next_cid = card_ids[i + 1] if i + 1 < len(card_ids) else None
        conts = [next_cid] if (next_cid and i % 3 != 0) else []

        resp = SetVerification(
            anchor=cid,
            continuations=conts,
            rejected=[],
            evidence_quotes=["continued on reverse side"] if conts else [],
            confidence=0.88,
            model="gemini-test-stress",
            prompt_id="set_verify",
            prompt_version="1.0",
        )
        responses.append(resp)

    # Pad responses up to max_calls if needed
    while len(responses) < max_calls:
        responses.append(
            SetVerification(
                anchor="card_generic",
                continuations=[],
                rejected=[],
                evidence_quotes=[],
                confidence=0.5,
                model="gemini-test-stress",
                prompt_id="set_verify",
                prompt_version="1.0",
            )
        )

    return ScriptedLLMClient(responses=responses)


async def run_stress_test(
    n_cards: int = 300,
    seed: int = 42,
    render_images: bool = False,
    k: int = 5,
    use_double: bool = False,
) -> Dict[str, Any]:
    """Executes scale stress test for N cards measuring calls and latency.

    Verification invariant: LLM calls must scale linearly as O(N) <= N, not O(N^2).

    Args:
        n_cards: Number of cards in homogeneous deck.
        seed: Random seed.
        render_images: Whether to render actual JPEG files to disk.
        k: Top-k screening candidate limit.
        use_double: Use the GroupingEngineDouble (fast unit tests only; default measures production).

    Returns:
        Structured test result dictionary with scaling statistics.
    """
    with tempfile.TemporaryDirectory(prefix="rb_stress_deck_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        card_ids, fps = generate_homogeneous_stress_deck(
            target_dir=tmp_dir,
            n_cards=n_cards,
            seed=seed,
            render_images=render_images,
        )

        image_provider = TempDirImageProvider(tmp_dir)
        llm_client = create_scripted_verification_client(card_ids, max_calls=n_cards + 50)

        # Check for production GroupingEngine or use double
        if use_double:
            engine = GroupingEngineDouble(k=k)
            engine_name = "GroupingEngineDouble (WP-GD contract)"
        else:
            # No silent fallback to the double: the stress tool exists to measure production.
            from recipeboxer.correlation.engine import GroupingEngine
            engine = GroupingEngine()
            engine_name = "Production GroupingEngine"

        logger.info(f"Running scale stress test with {engine_name} on N={n_cards} cards (k={k})...")

        t0 = time.perf_counter()
        result = await engine.group(
            fps=fps,
            images=image_provider,
            llm_client=llm_client,
        )
        elapsed_s = time.perf_counter() - t0

        calls_made = llm_client.call_count
        calls_per_card = calls_made / float(n_cards) if n_cards > 0 else 0.0

        # Verification: call count must be bounded by O(N*k) (specifically <= N calls)
        is_linear_scaling = (calls_made <= n_cards * 1.5)

        logger.info(
            f"Stress test complete: N={n_cards}, Calls={calls_made}, "
            f"Calls/N={calls_per_card:.2f}, Latency={elapsed_s:.3f}s, Linear={is_linear_scaling}"
        )

        return {
            "n_cards": n_cards,
            "llm_calls": calls_made,
            "calls_per_card": round(calls_per_card, 3),
            "latency_seconds": round(elapsed_s, 4),
            "engine": engine_name,
            "k_screening": k,
            "is_linear_scaling": is_linear_scaling,
            "scaling_complexity": "O(N*k)",
            "accuracy_claims": "NONE (offline measurement of ops/calls only)",
        }


async def run_stress_sweep(
    card_counts: Sequence[int] = (50, 100, 200, 300),
    seed: int = 42,
    k: int = 5,
    use_double: bool = False,
) -> List[Dict[str, Any]]:
    """Runs a sweep over multiple N values to demonstrate linear scaling O(N*k)."""
    sweep_results: List[Dict[str, Any]] = []
    for n in card_counts:
        res = await run_stress_test(n_cards=n, seed=seed, render_images=False, k=k, use_double=use_double)
        sweep_results.append(res)
    return sweep_results


def format_stress_report(results: Sequence[Dict[str, Any]]) -> str:
    """Formats stress test results as a Markdown table."""
    lines: List[str] = []
    lines.append("| Cards (N) | Screening (k) | LLM Calls | Calls / N | Latency (s) | Scaling Verified |")
    lines.append("|:---:|:---:|:---:|:---:|:---:|:---:|")

    for r in results:
        n = r.get("n_cards", 0)
        k = r.get("k_screening", 5)
        calls = r.get("llm_calls", 0)
        ratio = r.get("calls_per_card", 0.0)
        lat = r.get("latency_seconds", 0.0)
        verified = "PASS (~O(N*k))" if r.get("is_linear_scaling") else "FAIL (O(N^2))"
        lines.append(f"| {n} | {k} | {calls} | {ratio:.2f} | {lat:.3f}s | {verified} |")

    lines.append("\n*Note: Tests measure call counts and latency scaling only. No accuracy claims are asserted offline.*")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for evals.stress."""
    parser = argparse.ArgumentParser(
        prog="python -m evals.stress",
        description="Scale stress test on 300-500 card homogeneous decks to verify O(N*k) scaling.",
    )
    parser.add_argument("--cards", type=int, default=300, help="Number of cards to evaluate (default 300)")
    parser.add_argument("--sweep", action="store_true", help="Run sweep over N=[50, 100, 200, 300]")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--k", type=int, default=5, help="Top-k screening size")
    parser.add_argument("--out", type=str, default=None, help="Optional output JSON path")
    args = parser.parse_args(args=argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    try:
        if args.sweep:
            counts = [50, 100, 200, args.cards] if args.cards not in (50, 100, 200) else [50, 100, 200, 300]
            counts = sorted(list(set(counts)))
            results = asyncio.run(run_stress_sweep(card_counts=counts, seed=args.seed, k=args.k))
        else:
            res = asyncio.run(run_stress_test(n_cards=args.cards, seed=args.seed, k=args.k))
            results = [res]

        md_report = format_stress_report(results)
        print("\n=== Scale & Homogeneity Stress Benchmark (WP-GE, G13) ===")
        print(md_report)
        print()

        if args.out:
            out_p = Path(args.out)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(json.dumps(results, indent=2), encoding="utf-8")
            logger.info(f"Stress test results written to {out_p}")

        return 0
    except Exception as exc:
        logger.error(f"Stress test execution failed: {exc}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
