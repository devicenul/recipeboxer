"""Offline screening diagnostics: rank of the true successor among ALL cards under the current evidence."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO = Path(os.environ.get("UP3_SRC") or Path(__file__).resolve().parents[3])
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import DECKS, DiskCacheLLMClient, get_fingerprints, gold_clusters, load_manifest  # noqa: E402


async def main(deck: str, show: int = 12):
    from recipeboxer.correlation.background import DeckBackground
    from recipeboxer.correlation.engine import FileImageProvider
    from recipeboxer.correlation.evidence import compute_raw_features, pair_evidence
    from recipeboxer.correlation.screening import top_k_candidates

    man = load_manifest(deck)
    gold = gold_clusters(man)
    client = DiskCacheLLMClient(None)
    fps = await get_fingerprints(deck, client, man)
    fpm = {f.card_id: f for f in fps}
    images = FileImageProvider({f.card_id: DECKS / deck / f.card_id for f in fps})
    bg = DeckBackground.fit(fps, raw_pair_fn=lambda a, b: compute_raw_features(a, b, images=images))
    k = int(os.environ.get("K", "5"))
    cands = top_k_candidates(fps, bg, k=k, images=images)
    true = [(g[i], g[i + 1], i, len(g)) for g in gold for i in range(len(g) - 1)]
    in_k = 0
    rows = []
    for a, b, pos, L in true:
        ids = [e.b for e in cands[a]]
        ok = b in ids
        in_k += ok
        ev = pair_evidence(fpm[a], fpm[b], bg, images=images)
        top = cands[a][0] if cands[a] else None
        rows.append((ok, a, b, pos, L, ev, top))
    print(f"{deck}: true links {len(true)}  in top-{k}: {in_k} ({in_k/max(1,len(true)):.2f})")
    for ok, a, b, pos, L, ev, top in rows[:show] if os.environ.get("ALL") else [r for r in rows if not r[0]][:show]:
        fa, fb = fpm[a], fpm[b]
        feats = {f.name: (round(f.raw, 2) if isinstance(f.raw, (int, float)) else f.raw, round(f.log_lr, 2) if f.log_lr is not None else None) for f in ev.features}
        print(f"--- {'OK ' if ok else 'MISS'} pos {pos}/{L}  A role={fa.role} back={fa.looks_like_back} title={fa.visible_title!r} last={fa.last_lines[-1:]!r} cutE={fa.text_cut_off_end}")
        print(f"    B role={fb.role} back={fb.looks_like_back} title={fb.visible_title!r} first={fb.first_lines[:1]!r} markers={fb.printed_markers} cutS={fb.text_cut_off_start}")
        print(f"    true total={ev.total_log_lr:.2f}  top cand total={top.total_log_lr if top else None}")
        print(f"    feats={feats}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 12))
