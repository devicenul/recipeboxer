"""Offline evaluation script for WP-O1 spike on Tier T0 sample cards.

This script evaluates RapidOCR on the 48 visible T0 synthetic cards:
1. Modality breakdown:
   - Handwritten Cursive
   - Typewritten
   - Printed Clipping
   - Mixed Media / Damaged
2. CER (Character Error Rate) vs ground truth manifest text.
3. Latency (ms) per card.
4. Boundary cue detection accuracy vs ground-truth continuation markers.

Saved under _agents/remediation/scratch_o1/ (scratch only, not in package).
"""

from __future__ import annotations

import json
import logging
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

from recipeboxer.vision.ocr import LocalOCR, boundary_cues

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("eval_t0_ocr")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "sample_cards"
MANIFEST_PATH = FIXTURES_DIR / "manifest.json"


def levenshtein_distance(s1: str, s2: str) -> int:
    """Computes Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        s1, s2 = s2, s1
    if len(s2) == 0:
        return len(s1)

    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1] * (len(s2) + 1)
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            curr_row[j + 1] = min(curr_row[j] + 1, prev_row[j + 1] + 1, prev_row[j] + cost)
        prev_row = curr_row
    return prev_row[len(s2)]


def compute_cer(reference: str, hypothesis: str) -> float:
    """Computes Character Error Rate: edit_distance(ref, hyp) / len(ref)."""
    ref_norm = " ".join(reference.lower().split())
    hyp_norm = " ".join(hypothesis.lower().split())
    if not ref_norm:
        return 0.0 if not hyp_norm else 1.0
    dist = levenshtein_distance(ref_norm, hyp_norm)
    return dist / float(len(ref_norm))


def main() -> None:
    assert MANIFEST_PATH.exists(), f"Manifest missing at {MANIFEST_PATH}"

    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)

    cards = manifest_data.get("cards", [])
    logger.info("Loaded %d cards from T0 manifest", len(cards))

    ocr = LocalOCR()
    assert ocr.available(), "LocalOCR is not available!"

    # Warmup
    warmup_path = FIXTURES_DIR / cards[0]["filename"]
    ocr.read(warmup_path)

    card_results: List[Dict[str, Any]] = []
    recipes_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    total_start = time.perf_counter()

    for idx, card in enumerate(cards, 1):
        card_id = card.get("card_id", card.get("filename"))
        filename = card["filename"]
        img_path = FIXTURES_DIR / filename
        modality = card.get("modality", "UNKNOWN")
        role = card.get("role", "UNKNOWN")
        cluster_info = card.get("recipe_cluster", {})
        recipe_id = cluster_info.get("recipe_id", "unknown_recipe")
        gt_cue = cluster_info.get("continuation_cue")
        has_gt_forward_cue = bool(gt_cue)

        # Run OCR and measure latency
        t0 = time.perf_counter()
        lines = ocr.read(img_path)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        ocr_text = "\n".join(line.text for line in lines)

        # Extract boundary cues
        cues = boundary_cues(lines)
        detected_forward_cue = cues["has_continuation_cue"]
        detected_start_cue = cues["has_continuation_start"]

        # GT recipe text elements
        gt_recipe = card.get("ground_truth_recipe", {})
        title = gt_recipe.get("title", "")
        ingredients = gt_recipe.get("ingredients", [])
        directions = gt_recipe.get("directions", [])

        card_eval = {
            "card_id": card_id,
            "filename": filename,
            "recipe_id": recipe_id,
            "modality": modality,
            "role": role,
            "latency_ms": latency_ms,
            "num_ocr_lines": len(lines),
            "ocr_text": ocr_text,
            "gt_cue": gt_cue,
            "has_gt_forward_cue": has_gt_forward_cue,
            "detected_forward_cue": detected_forward_cue,
            "detected_start_cue": detected_start_cue,
            "cues": cues,
            "gt_title": title,
            "gt_ingredients": ingredients,
            "gt_directions": directions,
        }
        card_results.append(card_eval)
        recipes_map[recipe_id].append(card_eval)
        logger.info("[%2d/%2d] %s (%s) - %.1f ms - lines: %d - cue: %s",
                    idx, len(cards), filename, modality, latency_ms, len(lines), detected_forward_cue)

    total_duration = time.perf_counter() - total_start

    # Compute Recipe-level CER by assembling all faces of each recipe
    recipe_cer_list: List[Dict[str, Any]] = []
    for recipe_id, r_cards in recipes_map.items():
        # Sort cards by sequence position
        r_cards_sorted = sorted(r_cards, key=lambda c: c["filename"])
        combined_ocr = "\n".join(c["ocr_text"] for c in r_cards_sorted)

        ref_card = r_cards[0]
        gt_all_lines = [ref_card["gt_title"]] + ref_card["gt_ingredients"] + ref_card["gt_directions"]
        combined_ref = "\n".join(gt_all_lines)

        cer = compute_cer(combined_ref, combined_ocr)
        recipe_modality = ref_card["modality"]

        recipe_cer_list.append({
            "recipe_id": recipe_id,
            "modality": recipe_modality,
            "num_cards": len(r_cards),
            "cer": cer,
            "ref_len": len(combined_ref),
            "ocr_len": len(combined_ocr),
        })

    # Aggregations by modality
    modality_stats: Dict[str, Dict[str, Any]] = {}
    modalities = sorted(set(c["modality"] for c in card_results))

    for mod in modalities:
        mod_cards = [c for c in card_results if c["modality"] == mod]
        mod_recipes = [r for r in recipe_cer_list if r["modality"] == mod]

        latencies = [c["latency_ms"] for c in mod_cards]
        lines_count = [c["num_ocr_lines"] for c in mod_cards]

        # Cue detection evaluation:
        # True Positive: has_gt_forward_cue and detected_forward_cue
        # False Positive: not has_gt_forward_cue and detected_forward_cue
        # False Negative: has_gt_forward_cue and not detected_forward_cue
        # True Negative: not has_gt_forward_cue and not detected_forward_cue
        tp = sum(1 for c in mod_cards if c["has_gt_forward_cue"] and c["detected_forward_cue"])
        fp = sum(1 for c in mod_cards if not c["has_gt_forward_cue"] and c["detected_forward_cue"])
        fn = sum(1 for c in mod_cards if c["has_gt_forward_cue"] and not c["detected_forward_cue"])
        tn = sum(1 for c in mod_cards if not c["has_gt_forward_cue"] and not c["detected_forward_cue"])

        prec = tp / float(tp + fp) if (tp + fp) > 0 else 1.0
        rec = tp / float(tp + fn) if (tp + fn) > 0 else 1.0
        f1 = (2 * prec * rec) / float(prec + rec) if (prec + rec) > 0 else 0.0
        cue_acc = (tp + tn) / float(len(mod_cards)) if mod_cards else 0.0

        cers = [r["cer"] for r in mod_recipes] if mod_recipes else [0.0]

        modality_stats[mod] = {
            "num_cards": len(mod_cards),
            "num_recipes": len(mod_recipes),
            "mean_latency_ms": round(statistics.mean(latencies), 1),
            "median_latency_ms": round(statistics.median(latencies), 1),
            "mean_lines": round(statistics.mean(lines_count), 1),
            "mean_cer": round(statistics.mean(cers), 4),
            "median_cer": round(statistics.median(cers), 4),
            "cue_tp": tp,
            "cue_fp": fp,
            "cue_fn": fn,
            "cue_tn": tn,
            "cue_precision": round(prec, 3),
            "cue_recall": round(rec, 3),
            "cue_f1": round(f1, 3),
            "cue_accuracy": round(cue_acc, 3),
        }

    # High-level group comparisons: Typed/Clipping vs Handwritten
    typed_clipping_cards = [c for c in card_results if c["modality"] in ("TYPEWRITTEN", "PRINTED_CLIPPING")]
    typed_clipping_recipes = [r for r in recipe_cer_list if r["modality"] in ("TYPEWRITTEN", "PRINTED_CLIPPING")]
    handwritten_cards = [c for c in card_results if c["modality"] == "HANDWRITTEN_CURSIVE"]
    handwritten_recipes = [r for r in recipe_cer_list if r["modality"] == "HANDWRITTEN_CURSIVE"]

    def group_summary(name: str, g_cards: List[Dict[str, Any]], g_recipes: List[Dict[str, Any]]) -> Dict[str, Any]:
        lats = [c["latency_ms"] for c in g_cards]
        cers = [r["cer"] for r in g_recipes]
        tp = sum(1 for c in g_cards if c["has_gt_forward_cue"] and c["detected_forward_cue"])
        fp = sum(1 for c in g_cards if not c["has_gt_forward_cue"] and c["detected_forward_cue"])
        fn = sum(1 for c in g_cards if c["has_gt_forward_cue"] and not c["detected_forward_cue"])
        tn = sum(1 for c in g_cards if not c["has_gt_forward_cue"] and not c["detected_forward_cue"])
        prec = tp / float(tp + fp) if (tp + fp) > 0 else 1.0
        rec = tp / float(tp + fn) if (tp + fn) > 0 else 1.0
        f1 = (2 * prec * rec) / float(prec + rec) if (prec + rec) > 0 else 0.0
        return {
            "group": name,
            "num_cards": len(g_cards),
            "num_recipes": len(g_recipes),
            "mean_latency_ms": round(statistics.mean(lats), 1),
            "mean_cer": round(statistics.mean(cers), 4),
            "median_cer": round(statistics.median(cers), 4),
            "cue_tp": tp,
            "cue_fp": fp,
            "cue_fn": fn,
            "cue_tn": tn,
            "cue_precision": round(prec, 3),
            "cue_recall": round(rec, 3),
            "cue_f1": round(f1, 3),
            "cue_accuracy": round((tp + tn) / float(len(g_cards)), 3),
        }

    high_level_groups = {
        "typed_and_clipping": group_summary("Typed / Clipping", typed_clipping_cards, typed_clipping_recipes),
        "handwritten": group_summary("Handwritten Cursive", handwritten_cards, handwritten_recipes),
    }

    # Print Report
    print("\n" + "=" * 80)
    print("WP-O1 RAPIDOCR T0 EVALUATION RESULTS")
    print("=" * 80)
    print(f"Total Cards Evaluated: {len(cards)} across {len(recipes_map)} recipes")
    print(f"Total Wall Time: {total_duration:.2f}s (Average: {total_duration / len(cards) * 1000:.1f} ms/card)\n")

    print(f"{'Modality':<24} {'Cards':<6} {'CER (Mean)':<12} {'Latency':<12} {'Cue Prec':<10} {'Cue Rec':<10} {'Cue F1':<8}")
    print("-" * 80)
    for mod, s in modality_stats.items():
        print(f"{mod:<24} {s['num_cards']:<6} {s['mean_cer']:<12.4f} {s['mean_latency_ms']:<7.1f}ms   {s['cue_precision']:<10.3f} {s['cue_recall']:<10.3f} {s['cue_f1']:<8.3f}")

    print("\n" + "-" * 80)
    print("HIGH-LEVEL SUMMARY (TYPED/CLIPPING vs HANDWRITTEN):")
    print("-" * 80)
    for gname, gs in high_level_groups.items():
        print(f"Group: {gs['group']}")
        print(f"  Cards: {gs['num_cards']} | Mean Latency: {gs['mean_latency_ms']} ms")
        print(f"  CER: Mean={gs['mean_cer']:.4f}, Median={gs['median_cer']:.4f}")
        print(f"  Boundary Cues: Precision={gs['cue_precision']:.3f}, Recall={gs['cue_recall']:.3f}, F1={gs['cue_f1']:.3f}, Acc={gs['cue_accuracy']:.3f}")
        print(f"  Cue Matrix: TP={gs['cue_tp']}, FP={gs['cue_fp']}, FN={gs['cue_fn']}, TN={gs['cue_tn']}")

    # Save output json
    out_dir = REPO_ROOT / "_agents" / "remediation" / "scratch_o1"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "eval_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "modalities": modality_stats,
                "high_level": high_level_groups,
                "cards": card_results,
                "recipes": recipe_cer_list,
            },
            f,
            indent=2,
        )
    print(f"\nDetailed evaluation written to: {out_file}")


if __name__ == "__main__":
    main()
