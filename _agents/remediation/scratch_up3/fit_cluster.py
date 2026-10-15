"""Fit UP3 link / single / multi models from labelled engine rows ($PCD/up3/rows/<tag>/<deck>.pkl).

usage: fit_cluster.py <rows_tag> <fit_decks> [<ho_rows_tag> <ho_decks>]  (OUT=models.json, BASE=pre/rank json)
Reports in-sample and (optional) held-out ECE/Brier with bootstrap 95% CIs.
"""
from __future__ import annotations

import json
import os
import pickle
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from evals.metrics.calibration import ece  # noqa: E402
from recipeboxer.correlation.linkmodel import LINK_FEATURES, MULTI_FEATURES, SINGLE_FEATURES, LogisticModel  # noqa: E402

PCD = Path(os.environ["HOME"]) / ".cache/projects/recipeboxer-ba754a91"
ROWS = PCD / "up3/rows"
NAMES = {"link": LINK_FEATURES, "single": SINGLE_FEATURES, "multi": MULTI_FEATURES}
L2 = {"link": 3.0, "single": 5.0, "multi": 5.0}


def load(tag, decks):
    out = {k: ([], []) for k in NAMES}
    per = {}
    for d in decks:
        lab = pickle.load(open(ROWS / tag / f"{d}.pkl", "rb"))
        per[d] = lab
        for k in NAMES:
            for _, v, y in lab[k]:
                out[k][0].append(v)
                out[k][1].append(1.0 if y else 0.0)
    return {k: (np.array(X), np.array(y)) for k, (X, y) in out.items()}, per


def boot_ci(p, y, fn, n=500, seed=0):
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        vals.append(fn(p[i], y[i]))
    return np.percentile(vals, [2.5, 97.5])


def report(name, p, y):
    if len(y) == 0:
        return f"{name}: n=0"
    e = ece(list(p), list(y.astype(bool)))
    lo, hi = boot_ci(p, y, lambda a, b: ece(list(a), list(b.astype(bool))))
    br = float(np.mean((p - y) ** 2))
    return f"{name}: n={len(y)} pos={int(y.sum())} mean_p={p.mean():.3f} acc={y.mean():.3f} ECE={e:.3f} [{lo:.3f},{hi:.3f}] Brier={br:.3f}"


def main():
    fit_tag, fit_decks = sys.argv[1], sys.argv[2].split(",")
    data, _ = load(fit_tag, fit_decks)
    models = {}
    for k, (X, y) in data.items():
        if len(y) < 10 or y.min() == y.max():
            print(f"skip {k}: n={len(y)}")
            continue
        m = LogisticModel(NAMES[k]).fit(X, y, l2=L2[k])
        m.meta = {"n_fit": int(len(y)), "pos_fit": int(y.sum()), "fit_decks": fit_decks}
        models[k] = m
        print(report(f"FIT {k}", m.predict_proba(X), y))
        print("   ", ", ".join(f"{nm}={c:+.2f}" for nm, c in sorted(zip(NAMES[k], m.coef), key=lambda t: -abs(t[1]))[:8]))
    if len(sys.argv) > 4:
        ho, per = load(sys.argv[3], sys.argv[4].split(","))
        for k, (X, y) in ho.items():
            if k in models and len(y):
                print(report(f"HO  {k}", models[k].predict_proba(X), y))
    out = os.environ.get("OUT")
    if out:
        base = json.loads(Path(os.environ["BASE"]).read_text()) if os.environ.get("BASE") else {}
        base.update({k: m.to_dict() for k, m in models.items()})
        Path(out).write_text(json.dumps(base))
        print("wrote", out, sorted(base))


if __name__ == "__main__":
    main()
