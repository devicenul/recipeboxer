"""Fit UP3 'pre' (text-only, all pairs) and 'rank' (full evidence) models on fit decks; simulate the
screening pipeline (pool -> rank -> adaptive k) and report true-link coverage on held-out decks vs the
HEAD total_log_lr signal. Offline only (feature caches from build_feats.py).
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
from recipeboxer.correlation.linkmodel import RANK_FEATURES, LogisticModel  # noqa: E402

PCD = Path(os.environ["HOME"]) / ".cache/projects/recipeboxer-ba754a91"
FEATS = PCD / "up3/feats"


def load(deck):
    d = pickle.load(open(FEATS / f"{deck}.pkl", "rb"))
    ids = d["ids"]
    idx = {c: i for i, c in enumerate(ids)}
    gold_idx = [[idx[c] for c in g if c in idx] for g in d["gold"]]
    true = {(g[i], g[i + 1]) for g in gold_idx for i in range(len(g) - 1)}
    X = np.array([r[2] for r in d["rows"]])
    Xt = np.array([r[4] for r in d["rows"]])
    ij = np.array([(r[0], r[1]) for r in d["rows"]])
    tl = np.array([r[3] for r in d["rows"]])
    y = np.array([1.0 if (int(a), int(b)) in true else 0.0 for a, b in ij])
    anchor_ok = np.array([not (ds["back"] or ds["role_cont"]) for ds in d["desc"]])
    return dict(d=d, X=X, Xt=Xt, ij=ij, tl=tl, y=y, n=len(ids), true=true, gold=gold_idx, anchor_ok=anchor_ok)


def mat(D, score):
    S = np.full((D["n"], D["n"]), -np.inf)
    S[D["ij"][:, 0], D["ij"][:, 1]] = score
    return S


def simulate(D, pre, rank, M=10, kmin=3, kmax=8, pkeep=0.05):
    n = D["n"]
    S = mat(D, pre.logit(D["Xt"]))
    R = mat(D, rank.logit(D["X"]))
    pool = np.zeros((n, n), bool)
    m = min(M, n - 1)
    for i in range(n):
        pool[i, np.argsort(-S[i])[:m]] = True
    for j in range(n):
        pool[np.argsort(-S[:, j])[:m], j] = True
    np.fill_diagonal(pool, False)
    C = {}
    for i in range(n):
        cand = [j for j in np.argsort(-R[i]) if pool[i, j]][:kmax]
        keep = []
        for r, j in enumerate(cand):
            p = 1 / (1 + np.exp(-R[i, j]))
            if r >= kmin and p < pkeep:
                break
            keep.append(j)
        C[i] = keep
    tau = float(os.environ.get("TAU", "2"))
    D = dict(D)
    ext = np.array([D["anchor_ok"][i] or (len(C[i]) and 1 / (1 + np.exp(-R[i, C[i][0]])) >= tau) for i in range(n)])
    D["anchor_ok"] = ext
    pos_in = {}
    for gi, g in enumerate(D["gold"]):
        for p, c in enumerate(g):
            pos_in[c] = (gi, p)
    t = max(1, len(D["true"]))
    in_pool = sum(pool[a, b] for a, b in D["true"]) / t
    direct = sum((b in C[a]) for a, b in D["true"]) / t
    direct_anchor = sum((b in C[a]) and D["anchor_ok"][a] for a, b in D["true"]) / t
    via = 0
    for a, b in D["true"]:
        gi, pa = pos_in[a]
        g = D["gold"][gi]
        ok = D["anchor_ok"][a] and b in C[a]
        for x in g[:pa]:
            if D["anchor_ok"][x] and a in C[x] and b in C[x]:
                ok = True
        via += ok
    meank = np.mean([len(v) for v in C.values()])
    return dict(pool=round(in_pool, 3), direct=round(direct, 3), anchorOK=round(direct_anchor, 3),
                covered=round(via / t, 3), meank=round(float(meank), 2), pool_per=round(pool.sum() / n, 1),
                n_anchor=int(D["anchor_ok"].sum()))


def head_sim(D, k=5):
    R = mat(D, D["tl"])
    t = max(1, len(D["true"]))
    return round(sum(b in set(np.argsort(-R[a])[:k]) for a, b in D["true"]) / t, 3)


def fit_model(Ds, fit, key, l2):
    X = np.vstack([Ds[d][key] for d in fit])
    y = np.concatenate([Ds[d]["y"] for d in fit])
    w = np.concatenate([np.full(len(Ds[d]["y"]), 1.0 / len(Ds[d]["y"])) for d in fit])
    return LogisticModel(RANK_FEATURES).fit(X, y, l2=l2, w=w / w.mean())


def main():
    fit = sys.argv[1].split(",")
    ho = sys.argv[2].split(",")
    l2 = float(os.environ.get("L2", "3.0"))
    Ds = {d: load(d) for d in fit + ho}
    pre = fit_model(Ds, fit, "Xt", l2)
    rank = fit_model(Ds, fit, "X", l2)
    print("rank coefs:", ", ".join(f"{nm}={c:+.2f}" for nm, c in sorted(zip(RANK_FEATURES, rank.coef), key=lambda t: -abs(t[1]))[:14]))
    M = int(os.environ.get("M", "10")); kmax = int(os.environ.get("KMAX", "8")); pk = float(os.environ.get("PKEEP", "0.05"))
    for d in fit + ho:
        D = Ds[d]
        s = simulate(D, pre, rank, M=M, kmax=kmax, pkeep=pk)
        tag = "FIT" if d in fit else "HO "
        print(f"{tag} {d:<15} n={D['n']:<4} links={len(D['true']):<4} {s}  head_tl@5={head_sim(D)}")
    out = os.environ.get("OUT")
    if out:
        Path(out).write_text(json.dumps({"pre": pre.to_dict(), "rank": rank.to_dict()}))


if __name__ == "__main__":
    main()
