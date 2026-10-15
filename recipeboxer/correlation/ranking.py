"""WP-UP3 candidate ranking: fitted, capture-order-free, scalable screening.

Pipeline (all generic; no filenames / hashes / corpus strings):
1. Cheap prefilter on ALL ordered pairs: text/structure-only ``pair_evidence`` (images=None, ~1 ms per
   pair) -> fitted ``pre`` logistic model. This replaces HEAD's TF-IDF/capture/header pool, which on
   homogeneous shuffled decks missed ~35% of true links before the SetVerifier ever saw them.
2. Pool = top-M successors of every card U top-M predecessors of every card U capture neighbours (+-1).
3. Full ``pair_evidence`` (with physical image features) on the pool only -> fitted ``rank`` model.
4. SetVerifier candidates per anchor = rank-ordered pool, adaptive k (k_min..k_max): candidates are kept
   while their fitted probability is >= ``p_keep`` (relative cut), so dense same-stock decks get more
   candidates and sparse/distinct decks fewer.

Capture order is NOT a model feature: it is only used to add two neighbours to the pool, so a shuffled
capture order cannot penalise true links (HEAD's ``sort_adjacency`` gave every non-adjacent pair -2.3).
"""

from __future__ import annotations

import math
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from recipeboxer.correlation.background import DeckBackground
from recipeboxer.correlation.evidence import pair_evidence
from recipeboxer.correlation.linkmodel import LogisticModel, card_descriptor, rank_feature_vector
from recipeboxer.models.correlation import PairEvidence
from recipeboxer.models.domain import VisualFingerprint

Edge = Tuple[str, str]


def _apply_filter(ev: PairEvidence, feature_filter: Optional[Callable[[str], bool]]) -> PairEvidence:
    if feature_filter is not None:
        ev.features = [f for f in ev.features if feature_filter(f.name)]
        ev.total_log_lr = round(sum(f.weight * f.log_lr for f in ev.features if f.log_lr is not None), 4)
    return ev


class RankedScreening:
    """Result container for :func:`ranked_candidates`."""

    def __init__(self) -> None:
        self.candidates: Dict[str, List[PairEvidence]] = {}
        self.evidence: Dict[Edge, PairEvidence] = {}
        self.rank_logit: Dict[Edge, float] = {}
        self.pre_logit: Optional[np.ndarray] = None
        self.desc: Dict[str, Dict[str, float]] = {}
        self.stats: Dict[str, float] = {}

    def rank_p(self, edge: Edge) -> Optional[float]:
        z = self.rank_logit.get(edge)
        return None if z is None else 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, z))))


def ranked_candidates(
    fps: List[VisualFingerprint],
    background: DeckBackground,
    pre_model: LogisticModel,
    rank_model: LogisticModel,
    *,
    images=None,
    feature_filter: Optional[Callable[[str], bool]] = None,
    pool_m: int = 10,
    k_min: int = 3,
    k_max: int = 8,
    p_keep: float = 0.02,
    max_workers: int = 6,
) -> RankedScreening:
    out = RankedScreening()
    n = len(fps)
    ids = [fp.card_id for fp in fps]
    desc = [card_descriptor(fp) for fp in fps]
    out.desc = {cid: d for cid, d in zip(ids, desc)}
    if n < 2:
        out.candidates = {cid: [] for cid in ids}
        return out

    # 1. All-pairs cheap prefilter (text/structure only).
    rows: List[Tuple[int, int]] = []
    X: List[np.ndarray] = []
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            evt = _apply_filter(pair_evidence(fps[i], fps[j], background, images=None), feature_filter)
            X.append(rank_feature_vector(evt, desc[i], desc[j], fps[i], fps[j]))
            rows.append((i, j))
    S = np.full((n, n), -np.inf)
    z = pre_model.logit(np.vstack(X))
    for (i, j), zz in zip(rows, z):
        S[i, j] = zz
    out.pre_logit = S

    # 2. Pool: top-M out U top-M in U capture neighbours.
    m = min(pool_m, n - 1)
    pool: set = set()
    for i in range(n):
        for j in np.argsort(-S[i], kind="stable")[:m]:
            pool.add((i, int(j)))
    for j in range(n):
        for i in np.argsort(-S[:, j], kind="stable")[:m]:
            pool.add((int(i), j))
    def _cap(t: int) -> int:
        v = getattr(getattr(fps[t], "capture", None), "natural_sort_index", None)
        return int(v) if v is not None else t

    order = sorted(range(n), key=lambda t: (_cap(t), ids[t]))
    for r in range(n - 1):
        a, b = order[r], order[r + 1]
        pool.add((a, b))
        pool.add((b, a))

    # 3. Full evidence on the pool -> rank model.
    pool_sorted = sorted(pool, key=lambda t: (ids[t[0]], ids[t[1]]))
    def _full(ij: Tuple[int, int]):
        i, j = ij
        ev = _apply_filter(pair_evidence(fps[i], fps[j], background, images=images), feature_filter)
        return ev, rank_feature_vector(ev, desc[i], desc[j], fps[i], fps[j])

    # Physical (image) evidence dominates cost (FFT-based, releases the GIL) -> bounded thread pool.
    workers = max(1, min(max_workers, (os.cpu_count() or 2)))
    if workers > 1 and len(pool_sorted) > 64:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            full = list(ex.map(_full, pool_sorted))
    else:
        full = [_full(ij) for ij in pool_sorted]
    evs = [ev for ev, _ in full]
    Xf = [v for _, v in full]
    zf = rank_model.logit(np.vstack(Xf)) if Xf else np.zeros(0)
    by_anchor: Dict[str, List[Tuple[float, PairEvidence]]] = {cid: [] for cid in ids}
    for ev, zz in zip(evs, zf):
        edge = (ev.a, ev.b)
        out.evidence[edge] = ev
        out.rank_logit[edge] = float(zz)
        by_anchor[ev.a].append((float(zz), ev))

    # 4. Adaptive-k candidate lists (relative cut on fitted probability).
    for cid, lst in by_anchor.items():
        lst.sort(key=lambda t: (-t[0], t[1].b))
        chosen: List[PairEvidence] = []
        for rank_i, (zz, ev) in enumerate(lst[:k_max]):
            p = 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, zz))))
            if rank_i >= k_min and p < p_keep:
                break
            chosen.append(ev)
        out.candidates[cid] = chosen
    out.stats = {
        "prefilter_pairs": float(len(rows)),
        "pool_pairs": float(len(pool_sorted)),
        "mean_k": float(np.mean([len(v) for v in out.candidates.values()])) if out.candidates else 0.0,
    }
    return out
