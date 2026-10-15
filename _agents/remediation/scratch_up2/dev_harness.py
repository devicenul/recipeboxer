"""Dev harness for WP-UP2 grouping diagnosis, tuning, and evaluation.

Generates fresh cardforge decks (homogeneous, mixed), runs GroupingEngine live
(fingerprints + set verify) once, and caches all LLM outputs to disk.
Enables instant offline iteration on candidate screening, evidence weighting,
probability calibration, and path-cover optimization.
Computes B-cubed P/R/F1, false split/merge, exact cluster accuracy, and ECE.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SCRATCH_DIR = Path(__file__).resolve().parent
if str(SCRATCH_DIR) not in sys.path:
    sys.path.insert(0, str(SCRATCH_DIR))

# Set project cache environment
CACHE_DIR = Path(os.environ.get("HOME", "/home/droid")) / ".cache" / "projects" / "recipeboxer-ba754a91" / "up2"
DECKS_DIR = CACHE_DIR / "decks"
CACHE_LLM_DIR = CACHE_DIR / "cache"

from gen_decks import generate_deck

from evals.metrics.calibration import ece
from evals.metrics.grouping import score_grouping
from recipeboxer.ai.client import GenAIClient, LLMClient, create_llm_client
from recipeboxer.ai.cost import BudgetGuard, CostLedger, PriceTable
from recipeboxer.config import get_settings
from recipeboxer.correlation.background import DeckBackground
from recipeboxer.correlation.calibration import LinkCalibrator
from recipeboxer.correlation.engine import FileImageProvider, GroupingEngine, GroupingResult
from recipeboxer.correlation.evidence import compute_raw_features, pair_evidence
from recipeboxer.correlation.optimizer import path_cover
from recipeboxer.correlation.screening import top_k_candidates
from recipeboxer.correlation.semantic import SetVerifier, compute_set_verify_cache_key
from recipeboxer.models.correlation import FeatureEvidence, PairEvidence, SetVerification
from recipeboxer.models.domain import VisualFingerprint
from recipeboxer.vision.fingerprint import GeminiVisualExtractor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("up2.harness")


class DiskSetVerifyCache:
    """Persistent filesystem cache for SetVerification responses."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir / "set_verify"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._by_anchor: Dict[str, SetVerification] = {}
        for p in self.cache_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                ver = SetVerification.model_validate(data)
                if ver.anchor:
                    self._by_anchor[ver.anchor] = ver
            except Exception:
                pass

    def get(self, key: str) -> Optional[SetVerification]:
        p = self.cache_dir / f"{key}.json"
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return SetVerification.model_validate(data)
            except Exception as e:
                logger.warning(f"Failed to read cached SetVerification {key}: {e}")
        return None

    def get_by_anchor(self, anchor_id: str) -> Optional[SetVerification]:
        return self._by_anchor.get(anchor_id)

    def set(self, key: str, value: SetVerification) -> None:
        p = self.cache_dir / f"{key}.json"
        try:
            p.write_text(value.model_dump_json(indent=2), encoding="utf-8")
            if value.anchor:
                self._by_anchor[value.anchor] = value
        except Exception as e:
            logger.warning(f"Failed to write cached SetVerification {key}: {e}")


def get_image_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def get_or_extract_fingerprints(
    deck_dir: Path,
    extractor: Optional[GeminiVisualExtractor] = None,
    cache_dir: Path = CACHE_LLM_DIR,
) -> List[VisualFingerprint]:
    fp_cache_dir = cache_dir / "fingerprints"
    fp_cache_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = deck_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cards = manifest.get("cards", [])

    fps: List[VisualFingerprint] = []

    for c in cards:
        fname = c["filename"]
        img_path = deck_dir / fname
        sha = c.get("sha256_hash") or get_image_sha256(img_path)

        cached_file = fp_cache_dir / f"{sha}.json"
        if cached_file.exists():
            try:
                fp_data = json.loads(cached_file.read_text(encoding="utf-8"))
                fp = VisualFingerprint.model_validate(fp_data)
                fps.append(fp)
                continue
            except Exception as e:
                logger.warning(f"Failed to load cached fingerprint {sha}: {e}")

        if extractor is None:
            raise RuntimeError(f"Fingerprint not cached for {fname} ({sha}) and no extractor provided.")

        logger.info(f"Extracting live fingerprint for {fname}...")
        fp = await extractor.extract(str(img_path))
        fp.card_id = fname
        fp.image_path = str(img_path)
        fp.sha256_hash = sha
        cached_file.write_text(fp.model_dump_json(indent=2), encoding="utf-8")
        fps.append(fp)

    return fps


# Deck definitions
DECK_CONFIGS = {
    "homo_train": {
        "recipe_ids": [
            "rec_vhc_apple_crisp_1948",
            "rec_vhc_rhubarb_pie_1965",
            "rec_mcs_turkey_stuffing_feast",
            "rec_sta_quick_chocolate_fudge",
            "rec_sta_southern_pecan_pie",
            "rec_vhc_amish_cinnamon_bread_1973",
        ],
        "seed": 101,
        "homogeneous_substrate": "RULED_INDEX",
        "homogeneous_modality": "HANDWRITTEN_CURSIVE",
    },
    "mixed_train": {
        "recipe_ids": [
            "rec_typ_beef_stroganoff_1956",
            "rec_clp_crisco_fried_chicken",
            "rec_mcs_wedding_fruitcake_1942",
            "rec_dmg_butter_shortbread",
            "rec_typ_tomato_soup_cake_1952",
        ],
        "seed": 102,
        "homogeneous_substrate": None,
        "homogeneous_modality": None,
    },
    "homo_test": {
        "recipe_ids": [
            "rec_vhc_strawberry_rhubarb_jam_1954",
            "rec_mcs_sunday_pot_roast_gravy",
            "rec_sta_classic_banana_bread",
            "rec_typ_swedish_meatballs_1961",
            "rec_dmg_moms_lasagna_1971",
        ],
        "seed": 201,
        "homogeneous_substrate": "RULED_INDEX",
        "homogeneous_modality": "HANDWRITTEN_CURSIVE",
    },
    "mixed_test": {
        "recipe_ids": [
            "rec_mcs_traditional_panettone_1955",
            "rec_clp_green_bean_casserole_1968",
            "rec_sta_old_fashioned_lemonade",
            "rec_typ_lemon_chiffon_pie_1958",
            "rec_dmg_buttermilk_pancakes_1969",
        ],
        "seed": 202,
        "homogeneous_substrate": None,
        "homogeneous_modality": None,
    },
}


def ensure_decks_generated() -> Dict[str, Path]:
    from cardforge.models import CardModality, PaperSubstrate

    deck_paths: Dict[str, Path] = {}
    for dname, cfg in DECK_CONFIGS.items():
        dpath = DECKS_DIR / dname
        deck_paths[dname] = dpath
        if not (dpath / "manifest.json").exists():
            logger.info(f"Generating deck {dname}...")
            sub = PaperSubstrate(cfg["homogeneous_substrate"]) if cfg["homogeneous_substrate"] else None
            mod = CardModality(cfg["homogeneous_modality"]) if cfg["homogeneous_modality"] else None
            generate_deck(
                deck_name=dname,
                output_dir=dpath,
                recipe_ids=cfg["recipe_ids"],
                seed=cfg["seed"],
                homogeneous_substrate=sub,
                homogeneous_modality=mod,
            )
    return deck_paths


def get_ground_truth(deck_dir: Path) -> Tuple[List[List[str]], List[Tuple[str, str]]]:
    manifest = json.loads((deck_dir / "manifest.json").read_text(encoding="utf-8"))
    recipes: Dict[str, List[str]] = {}
    true_pairs: List[Tuple[str, str]] = []

    for c in manifest["cards"]:
        rid = c["recipe_cluster"]["recipe_id"]
        recipes.setdefault(rid, []).append(c["card_id"])

    gold_clusters: List[List[str]] = []
    for rid, cids in recipes.items():
        gold_clusters.append(cids)
        for i in range(len(cids) - 1):
            true_pairs.append((cids[i], cids[i + 1]))

    return gold_clusters, true_pairs


async def run_pipeline_on_deck(
    deck_name: str,
    deck_dir: Path,
    llm_client: Optional[LLMClient],
    calibrator: Optional[LinkCalibrator] = None,
    diagnose: bool = False,
) -> Dict[str, Any]:
    gold_clusters, true_pairs = get_ground_truth(deck_dir)

    # 1. Visual Fingerprints (cached)
    extractor = GeminiVisualExtractor(llm_client=llm_client) if llm_client else None
    fps = await get_or_extract_fingerprints(deck_dir, extractor=extractor, cache_dir=CACHE_LLM_DIR)

    # 2. ImageProvider
    image_map = {fp.card_id: deck_dir / fp.card_id for fp in fps}
    image_provider = FileImageProvider(image_map)

    # 3. SetVerifier with persistent disk cache
    sv_cache = DiskSetVerifyCache(CACHE_LLM_DIR)
    set_verifier = SetVerifier(llm_client=llm_client, cache=sv_cache)

    engine = GroupingEngine(calibrator=calibrator, set_verifier=set_verifier)
    result = await engine.group(
        fps=fps,
        images=image_provider,
        llm_client=llm_client,
    )

    pred_chains = result.chains
    scores = score_grouping(pred_chains, gold_clusters)

    # Calculate Calibration ECE on proposals
    confidences: List[float] = []
    correct_list: List[bool] = []

    gold_cluster_map = {}
    for g_idx, cluster in enumerate(gold_clusters):
        for c in cluster:
            gold_cluster_map[c] = g_idx

    for prop in result.proposals:
        cids = prop.ordered_card_ids
        conf = prop.confidence
        # Is this cluster correct? Pure & complete
        g_indices = {gold_cluster_map.get(cid) for cid in cids}
        is_pure = (len(g_indices) == 1 and None not in g_indices)
        is_exact = is_pure and (len(cids) == len(gold_clusters[list(g_indices)[0]]))

        if conf is not None:
            confidences.append(float(conf))
            correct_list.append(bool(is_exact))

    calib_ece = ece(confidences, correct_list) if confidences else 0.0

    # Diagnostics if requested
    diag_info: Dict[str, Any] = {}
    if diagnose:
        # Per-stage recall of true links
        n_true = len(true_pairs)
        in_top_k = 0
        confirmed_by_sv = 0
        p_links: List[float] = []
        in_path_cover = 0

        # Build candidate lookup
        # Extract pairs from result.evidence
        evidence_pairs = {(ev.a, ev.b): ev for ev in result.evidence}
        # Build set verifier continuations
        sv_pairs = set()
        for ver in result.verifications:
            seq = [ver.anchor] + list(ver.continuations or [])
            for idx in range(len(seq) - 1):
                sv_pairs.add((seq[idx], seq[idx + 1]))

        # Path cover edges
        pred_edges = set()
        for ch in pred_chains:
            for idx in range(len(ch) - 1):
                pred_edges.add((ch[idx], ch[idx + 1]))

        for a, b in true_pairs:
            # 1. Was in top_k?
            is_cand = (a, b) in evidence_pairs
            if is_cand:
                in_top_k += 1
            # 2. Was confirmed by SV?
            is_sv = (a, b) in sv_pairs
            if is_sv:
                confirmed_by_sv += 1
            # 3. p_link
            ev = evidence_pairs.get((a, b))
            p_val = ev.p_link if ev and ev.p_link is not None else 0.0
            p_links.append(p_val)
            # 4. In path cover?
            if (a, b) in pred_edges:
                in_path_cover += 1

        diag_info = {
            "n_true_links": n_true,
            "top_k_recall": in_top_k / n_true if n_true else 1.0,
            "sv_recall": confirmed_by_sv / n_true if n_true else 1.0,
            "mean_true_p_link": float(np.mean(p_links)) if p_links else 0.0,
            "path_cover_recall": in_path_cover / n_true if n_true else 1.0,
            "true_links_detail": [
                {
                    "pair": f"{a} -> {b}",
                    "in_top_k": (a, b) in evidence_pairs,
                    "in_sv": (a, b) in sv_pairs,
                    "p_link": evidence_pairs.get((a, b)).p_link if (a, b) in evidence_pairs else None,
                    "in_path_cover": (a, b) in pred_edges,
                }
                for a, b in true_pairs
            ],
        }

    return {
        "deck_name": deck_name,
        "n_cards": len(fps),
        "n_gold_recipes": len(gold_clusters),
        "n_pred_chains": len(pred_chains),
        "bcubed_p": scores["bcubed_p"],
        "bcubed_r": scores["bcubed_r"],
        "bcubed_f1": scores["bcubed_f1"],
        "false_split": scores["false_split_rate"],
        "false_merge": scores["false_merge_rate"],
        "exact_cluster_acc": scores["exact_cluster_acc"],
        "ece": calib_ece,
        "diagnostics": diag_info,
    }


async def main():
    parser = argparse.ArgumentParser(description="UP2 Grouping Dev Harness")
    parser.add_argument("--live", action="store_true", help="Run live LLM calls and cache outputs")
    parser.add_argument("--budget-usd", type=float, default=2.0, help="Max USD budget for live calls")
    parser.add_argument("--diagnose", action="store_true", help="Print per-stage recall diagnostics")
    parser.add_argument("--deck", type=str, default=None, help="Specific deck name to run")
    args = parser.parse_args()

    # Generate decks if needed
    deck_paths = ensure_decks_generated()

    # Client setup
    llm_client: Optional[LLMClient] = None
    ledger: Optional[CostLedger] = None
    if args.live:
        prices = PriceTable.load()
        guard = BudgetGuard(cap_usd=args.budget_usd)
        ledger = CostLedger(prices=prices)
        settings = get_settings()
        llm_client = GenAIClient(api_key=settings.gemini_api_key, settings=settings, guard=guard, ledger=ledger)
        logger.info(f"Live GenAIClient initialized with budget ceiling ${args.budget_usd:.2f}")

    target_decks = [args.deck] if args.deck else list(deck_paths.keys())

    results = []
    for dname in target_decks:
        dpath = deck_paths[dname]
        logger.info(f"Evaluating {dname}...")
        res = await run_pipeline_on_deck(dname, dpath, llm_client=llm_client, diagnose=args.diagnose)
        results.append(res)

    print("\n" + "=" * 95)
    print(f"{'DECK':<15} {'CARDS':<6} {'B3-P':<8} {'B3-R':<8} {'B3-F1':<8} {'F-SPLIT':<8} {'F-MERGE':<8} {'EXACT':<8} {'ECE':<8}")
    print("-" * 95)
    for r in results:
        print(
            f"{r['deck_name']:<15} {r['n_cards']:<6} {r['bcubed_p']:<8.3f} {r['bcubed_r']:<8.3f} "
            f"{r['bcubed_f1']:<8.3f} {r['false_split']:<8.3f} {r['false_merge']:<8.3f} "
            f"{r['exact_cluster_acc']:<8.3f} {r['ece']:<8.3f}"
        )
    print("=" * 95)

    if args.diagnose:
        print("\nDIAGNOSTICS (Per-Stage Link Survival):")
        for r in results:
            d = r.get("diagnostics", {})
            if d:
                print(f"\nDeck: {r['deck_name']} (Total True Links: {d['n_true_links']})")
                print(f"  Top-k Screening Recall:  {d['top_k_recall']:.3f}")
                print(f"  SetVerifier Recall:      {d['sv_recall']:.3f}")
                print(f"  Mean True Link p_link:   {d['mean_true_p_link']:.3f}")
                print(f"  Path Cover Link Recall:  {d['path_cover_recall']:.3f}")
                for item in d["true_links_detail"]:
                    print(f"    {item['pair']:<50} top_k={item['in_top_k']} sv={item['in_sv']} p={item['p_link']} path={item['in_path_cover']}")

    if ledger:
        print(f"\nTotal Live Spend: ${ledger.total_usd:.4f} USD ({ledger.calls} calls)")


if __name__ == "__main__":
    asyncio.run(main())
