"""Held-out recipe-level calibration: shipped recipe model vs raw group confidence (scratch)."""
import pickle, sys
import numpy as np
sys.path.insert(0, ".")
from recipeboxer.correlation.linkmodel import default_models


def ece(p, y, bins=10):
    p, y = np.asarray(p, float), np.asarray(y, float)
    idx = np.minimum((p * bins).astype(int), bins - 1)
    return float(sum(abs(p[idx == b].mean() - y[idx == b].mean()) * (idx == b).sum() for b in range(bins) if (idx == b).any()) / len(p))


def boot(p, y, n=2000, seed=0):
    rng = np.random.default_rng(seed)
    p, y = np.asarray(p), np.asarray(y)
    v = [ece(p[s], y[s]) for s in (rng.integers(0, len(p), len(p)) for _ in range(n))]
    return np.percentile(v, [2.5, 97.5])


rows = pickle.load(open(sys.argv[1], "rb"))
m = default_models().get("recipe")
rows = [r for r in rows if r["group_conf"] is not None]
y = np.array([r["correct"] for r in rows], float)
raw = np.array([r["group_conf"] for r in rows])
fit = np.array([float(m.predict_proba(r["vec"])[0]) for r in rows])
print(f"n={len(rows)} acc={y.mean():.3f} exact={np.mean([r['group_exact'] for r in rows]):.3f}")
for name, p in (("raw_group_conf", raw), ("recipe_model", fit)):
    lo, hi = boot(p, y)
    print(f"{name:15s} ECE={ece(p, y):.3f} CI=[{lo:.3f},{hi:.3f}] meanp={p.mean():.3f}")
for d in sorted({r["deck"] for r in rows}):
    s = np.array([r["deck"] == d for r in rows])
    print(f"  {d:15s} n={s.sum():3d} acc={y[s].mean():.3f} raw={ece(raw[s], y[s]):.3f} model={ece(fit[s], y[s]):.3f} meanp_model={fit[s].mean():.3f}")
