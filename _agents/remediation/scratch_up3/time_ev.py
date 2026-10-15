import asyncio, sys, time, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3])); sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import DECKS, DiskCacheLLMClient, get_fingerprints, load_manifest
from recipeboxer.correlation.background import DeckBackground
from recipeboxer.correlation.engine import FileImageProvider
from recipeboxer.correlation.evidence import compute_raw_features, pair_evidence
deck = sys.argv[1]
man = load_manifest(deck)
fps = asyncio.run(get_fingerprints(deck, DiskCacheLLMClient(None), man))
images = FileImageProvider({f.card_id: DECKS / deck / f.card_id for f in fps})
t = time.time(); bg = DeckBackground.fit(fps, raw_pair_fn=lambda a, b: compute_raw_features(a, b, images=images)); print("bg", time.time() - t)
t = time.time(); n = 0
for i in range(10):
    for j in range(len(fps)):
        if i != j:
            ev = pair_evidence(fps[i], fps[j], bg, images=None); n += 1
print("text-only per pair ms", 1000 * (time.time() - t) / n)
print([(f.name, f.log_lr) for f in ev.features])
t = time.time(); n = 0
for j in range(1, 30):
    ev = pair_evidence(fps[0], fps[j], bg, images=images); n += 1
print("full per pair ms", 1000 * (time.time() - t) / n)
import cProfile, pstats
cProfile.run("for j in range(1,15): pair_evidence(fps[1], fps[j], bg, images=images)", "/tmp/prof")
pstats.Stats("/tmp/prof").sort_stats("cumtime").print_stats(12)
