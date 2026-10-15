"""Live (cached) extraction of PREDICTED recipes from a grouping dump, labelled exactly like the scorer.

usage: extract_cal.py --dump-tag new --decks a,b [--max-recipes N] [--live --budget-usd X] --out rows.pkl
For each predicted chain: RecipeExtractor.extract(ordered fps) (production extractor, cached by request
fingerprint), then evals.metrics matching (match_recipes_by_content) + score_recipe; correct =
content_sim >= 0.5 AND ingredient_f1 >= 0.7 (evaluator.py L139-151). Records the recipe feature vector
with the grouping confidence the headless path would use (exact-chain proposal confidence).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pickle
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import DECKS, UP3, get_fingerprints, load_manifest, make_client  # noqa: E402


async def run(deck, dump_tag, client, max_recipes, seed):
    from evals.metrics.extraction import match_recipes_by_content, score_recipe
    from recipeboxer.config import get_settings
    from recipeboxer.ai.client import LLMStage
    from recipeboxer.correlation.linkmodel import recipe_feature_vector
    from recipeboxer.extraction.extractor import RecipeExtractor
    from recipeboxer.models.recipe import Recipe

    man = load_manifest(deck)
    dump = json.loads((UP3 / "dumps" / dump_tag / f"{deck}.json").read_text())
    fps = {fp.card_id: fp for fp in await get_fingerprints(deck, client, man)}
    s = get_settings()
    ext = RecipeExtractor(llm_client=client, model_name=s.get_stage_model(LLMStage.STRUCTURE), settings=s)
    props = {tuple(p["ordered_card_ids"]): p.get("confidence") for p in dump["proposals"]}
    chains = [c for c in dump["chains"]]
    rng = random.Random(seed)
    if max_recipes and len(chains) > max_recipes:
        chains = rng.sample(chains, max_recipes)
    sem = asyncio.Semaphore(4)

    async def one(ch):
        async with sem:
            try:
                r = await ext.extract([fps[c] for c in ch])
                return ch, r
            except Exception as exc:  # budget or failure
                if "Budget" in type(exc).__name__:
                    raise
                print("extract failed", deck, len(ch), type(exc).__name__, str(exc)[:120], flush=True)
                return ch, None

    res = await asyncio.gather(*(one(c) for c in chains))
    preds, keep = [], []
    for ch, r in res:
        if r is None:
            continue
        rr = r if isinstance(r, Recipe) else Recipe.model_validate(r.model_dump())
        preds.append({"image_ids": list(ch), "recipe": rr.model_dump(mode="json")})
        keep.append((ch, rr))
    golds = [{"faces": g["faces"], "structured": {"title": g["title"], "ingredients": g["ingredients"],
              "directions": g["directions"], "yield": g.get("yield")}, "verbatim": g.get("verbatim")} for g in man["recipes"]]
    matched, _, _ = match_recipes_by_content(preds, golds, min_overlap=0.10)
    out = []
    for pi, gi, sim in matched:
        ch, rr = keep[pi]
        row = score_recipe(pred=rr.to_scoring_dict(), gold_structured=golds[gi]["structured"], gold_verbatim=golds[gi]["verbatim"])
        f1 = row.get("ingredient_f1")
        correct = (sim >= 0.5) and (f1 is not None and f1 >= 0.7)
        gconf = props.get(tuple(ch))
        out.append({"deck": deck, "chain": list(ch), "group_conf": gconf, "sim": sim, "ing_f1": f1, "correct": bool(correct),
                    "group_exact": set(ch) == set(golds[gi]["faces"]),
                    "vec": recipe_feature_vector(gconf, rr, len(ch))})
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-tag", required=True)
    ap.add_argument("--decks", required=True)
    ap.add_argument("--max-recipes", type=int, default=0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--budget-usd", type=float, default=0.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.live and a.budget_usd <= 0:
        raise SystemExit("--budget-usd required")
    client, ledger = make_client(a.live, a.budget_usd)
    rows = []
    try:
        for d in a.decks.split(","):
            r = await run(d, a.dump_tag, client, a.max_recipes, a.seed)
            print(f"{d}: matched={len(r)} correct={sum(x['correct'] for x in r)} group_exact={sum(x['group_exact'] for x in r)}", flush=True)
            rows += r
    finally:
        if ledger is not None:
            print(f"LIVE SPEND ${ledger.total_usd:.4f} calls={client.live_calls}")
        print(f"cache hits={client.hits} misses={client.misses}")
        pickle.dump(rows, open(a.out, "wb"))


if __name__ == "__main__":
    asyncio.run(main())
