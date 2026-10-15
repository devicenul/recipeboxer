"""Build per-deck pairwise feature caches for UP3 model fitting/eval (offline, no live calls).

For every deck: fingerprints (from LLM cache), DeckBackground, per-card descriptors and, for every
ordered pair (a, b), the rank feature vector computed WITH physical image features. Results are cached
to $PCD/up3/feats/<deck>.pkl so model fitting iterations are instant.
"""
from __future__ import annotations

import asyncio
import os
import pickle
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO = Path(os.environ.get("UP3_SRC") or Path(__file__).resolve().parents[3])
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import DECKS, UP3, DiskCacheLLMClient, get_fingerprints, gold_clusters, load_manifest  # noqa: E402

FEATS = UP3 / "feats"
_G = {}


def _init(deck: str):
    from recipeboxer.correlation.background import DeckBackground
    from recipeboxer.correlation.engine import FileImageProvider
    from recipeboxer.correlation.evidence import compute_raw_features
    from recipeboxer.correlation.linkmodel import card_descriptor

    man = load_manifest(deck)
    fps = asyncio.run(get_fingerprints(deck, DiskCacheLLMClient(None), man))
    images = FileImageProvider({f.card_id: DECKS / deck / f.card_id for f in fps})
    bg = DeckBackground.fit(fps, raw_pair_fn=lambda a, b: compute_raw_features(a, b, images=images))
    _G.update(deck=deck, fps=fps, images=images, bg=bg, desc=[card_descriptor(f) for f in fps])


def _rows(i: int):
    from recipeboxer.correlation.evidence import pair_evidence
    from recipeboxer.correlation.linkmodel import rank_feature_vector

    fps, bg, images, desc = _G["fps"], _G["bg"], _G["images"], _G["desc"]
    out = []
    for j in range(len(fps)):
        if i == j:
            continue
        ev = pair_evidence(fps[i], fps[j], bg, images=images)
        evt = pair_evidence(fps[i], fps[j], bg, images=None)
        out.append((i, j, rank_feature_vector(ev, desc[i], desc[j], fps[i], fps[j]), ev.total_log_lr,
                    rank_feature_vector(evt, desc[i], desc[j], fps[i], fps[j])))
    return out


def build(deck: str, procs: int = 6):
    FEATS.mkdir(parents=True, exist_ok=True)
    outp = FEATS / f"{deck}.pkl"
    if outp.exists():
        return
    t0 = time.time()
    _init(deck)
    n = len(_G["fps"])
    with ProcessPoolExecutor(max_workers=procs, initializer=_init, initargs=(deck,)) as ex:
        chunks = list(ex.map(_rows, range(n)))
    rows = [r for ch in chunks for r in ch]
    man = load_manifest(deck)
    gold = gold_clusters(man)
    ids = [f.card_id for f in _G["fps"]]
    pickle.dump({"deck": deck, "ids": ids, "desc": _G["desc"], "rows": rows, "gold": gold,
                 "deck_type": man["deck_type"]}, open(outp, "wb"))
    print(f"{deck}: {n} cards, {len(rows)} pairs in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    for d in sys.argv[1].split(","):
        build(d)
