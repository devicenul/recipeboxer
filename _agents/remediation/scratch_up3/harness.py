"""WP-UP3 dev harness: live-once / offline-forever grouping (and extraction) evaluation on fresh decks.

- DiskCacheLLMClient caches every LLM response keyed by LLMRequest.compute_fingerprint() under
  $PCD/up3/llm_cache, so re-runs are free; cache misses in --offline mode raise.
- Runs GroupingEngine exactly as production does (GroupingEngine.group).
- Metrics: B-cubed P/R/F1, pairwise false split/merge (evals.metrics.grouping), link precision/recall,
  ECE of proposal confidence vs exact-cluster correctness (incl. standalones), per-stage diagnostics.
- --dump writes per-deck JSON of evidence/links/proposals for offline analysis + calibration fitting.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(os.environ.get("UP3_SRC") or Path(__file__).resolve().parents[3])
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from recipeboxer.ai.client import LLMClient, LLMRequest, LLMResponse, UsageTokens  # noqa: E402
from recipeboxer.models.failures import StageUnavailableError, FailureReason  # noqa: E402

PCD = Path(os.environ.get("HOME", "/home/droid")) / ".cache" / "projects" / "recipeboxer-ba754a91"
UP3 = PCD / "up3"
DECKS = UP3 / "decks"
LLM_CACHE = UP3 / "llm_cache"

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("up3")


class CacheMiss(StageUnavailableError):
    pass


class DiskCacheLLMClient(LLMClient):
    def __init__(self, inner: Optional[LLMClient], cache_dir: Path = LLM_CACHE) -> None:
        self.inner = inner
        self.dir = cache_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0
        self.live_calls = 0

    async def generate(self, request: LLMRequest) -> LLMResponse[Any]:
        key = request.compute_fingerprint()
        p = self.dir / f"{key}.json"
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            self.hits += 1
            parsed = None
            if d.get("parsed") is not None and request.response_model is not None:
                parsed = request.response_model.model_validate(d["parsed"])
            return LLMResponse(parsed=parsed, raw_text=d.get("raw_text", ""), usage=UsageTokens(**d.get("usage", {})),
                               model=request.model, latency_ms=0.0, attempts=1, finish_reason=d.get("finish_reason"),
                               request_fingerprint=key)
        self.misses += 1
        if self.inner is None:
            raise CacheMiss(f"cache miss {request.stage} {key[:10]}", reason=FailureReason.MODEL_UNAVAILABLE)
        resp = await self.inner.generate(request)
        self.live_calls += 1
        parsed = resp.parsed.model_dump(mode="json") if resp.parsed is not None and hasattr(resp.parsed, "model_dump") else None
        p.write_text(json.dumps({"stage": str(getattr(request.stage, "value", request.stage)), "parsed": parsed,
                                 "raw_text": resp.raw_text, "usage": resp.usage.model_dump(),
                                 "finish_reason": resp.finish_reason}), encoding="utf-8")
        return resp


def load_manifest(deck: str) -> Dict[str, Any]:
    return json.loads((DECKS / deck / "manifest.json").read_text(encoding="utf-8"))


def gold_clusters(man: Dict[str, Any]) -> List[List[str]]:
    return [list(r["faces"]) for r in man["recipes"]]


async def get_fingerprints(deck: str, client: LLMClient, man: Dict[str, Any]):
    from recipeboxer.models.features import CaptureMeta
    from recipeboxer.vision.fingerprint import GeminiVisualExtractor

    ext = GeminiVisualExtractor(llm_client=client)
    ids = sorted(c["card_id"] for c in man["cards"])  # natural sort of opaque ids == random order
    sem = asyncio.Semaphore(8)

    async def one(i: int, cid: str):
        async with sem:
            path = DECKS / deck / cid
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            fp = await ext.extract(str(path), sha256_hash=sha, card_id=cid)
            fp.card_id = cid
            fp.image_path = str(path)
            try:
                fp.capture = CaptureMeta(natural_sort_index=i, source="none")
            except Exception:
                try:
                    fp.capture = CaptureMeta(natural_sort_index=i)
                except Exception:
                    pass
            return fp

    return list(await asyncio.gather(*[one(i, c) for i, c in enumerate(ids)]))


def exact_correct(chain: List[str], gold_of: Dict[str, int], gold: List[List[str]]) -> bool:
    gs = {gold_of.get(c) for c in chain}
    if len(gs) != 1 or None in gs:
        return False
    g = gold[next(iter(gs))]
    return set(g) == set(chain)


async def run_deck(deck: str, client: LLMClient, *, dump: bool = False, tag: str = "") -> Dict[str, Any]:
    from evals.metrics.calibration import ece
    from evals.metrics.grouping import score_grouping
    from recipeboxer.config import get_settings
    from recipeboxer.correlation.engine import FileImageProvider, GroupingEngine

    man = load_manifest(deck)
    gold = gold_clusters(man)
    gold_of = {c: i for i, g in enumerate(gold) for c in g}
    fps = await get_fingerprints(deck, client, man)
    images = FileImageProvider({fp.card_id: DECKS / deck / fp.card_id for fp in fps})
    if os.environ.get("UP3_ENGINE", "concurrent") == "concurrent":
        from recipeboxer.agent.orchestrator import ConcurrentGroupingEngine as _Eng
    else:
        _Eng = GroupingEngine
    kw = {}
    if os.environ.get("UP3_MODELS"):
        from recipeboxer.correlation.linkmodel import LogisticModel, Up3Models
        md = json.loads(Path(os.environ["UP3_MODELS"]).read_text())
        md = md.get("models", md)
        kw["models"] = Up3Models({k: LogisticModel.from_dict(v) for k, v in md.items()})
    engine = _Eng(**kw)
    t0 = time.time()
    res = await engine.group(fps, images, llm_client=client, settings=get_settings())
    dt = time.time() - t0
    rows = getattr(engine, "last_rows", None)
    if rows and os.environ.get("UP3_ROWS"):
        import pickle
        true_l = {(g[i], g[i + 1]) for g in gold for i in range(len(g) - 1)}
        lab = {
            "link": [(e, v, e in true_l) for e, v in rows.get("link", [])],
            "single": [(c, v, exact_correct(c, gold_of, gold)) for c, v in rows.get("single", [])],
            "multi": [(c, v, exact_correct(c, gold_of, gold)) for c, v in rows.get("multi", [])],
        }
        rd = UP3 / "rows" / os.environ["UP3_ROWS"]
        rd.mkdir(parents=True, exist_ok=True)
        pickle.dump(lab, open(rd / f"{deck}.pkl", "wb"))
    chains = res.chains
    sc = score_grouping(chains, gold)

    true_links = {(g[i], g[i + 1]) for g in gold for i in range(len(g) - 1)}
    pred_links = {(c[i], c[i + 1]) for c in chains for i in range(len(c) - 1)}
    ev_pairs = {(e.a, e.b): e for e in res.evidence}
    sv_pairs = set()
    for v in res.verifications:
        seq = [v.anchor] + list(v.continuations or [])
        sv_pairs |= {(seq[i], seq[i + 1]) for i in range(len(seq) - 1)}

    confs, corr, kinds = [], [], []
    for p in res.proposals:
        if p.confidence is None:
            continue
        confs.append(float(p.confidence))
        corr.append(exact_correct(p.ordered_card_ids, gold_of, gold))
        kinds.append("single" if len(p.ordered_card_ids) == 1 else "multi")
    e_all = ece(confs, corr) if confs else None
    e_single = ece([c for c, k in zip(confs, kinds) if k == "single"], [x for x, k in zip(corr, kinds) if k == "single"]) if "single" in kinds else None
    e_multi = ece([c for c, k in zip(confs, kinds) if k == "multi"], [x for x, k in zip(corr, kinds) if k == "multi"]) if "multi" in kinds else None
    n_none = sum(1 for p in res.proposals if p.confidence is None)

    out = {
        "deck": deck, "type": man["deck_type"], "n": len(fps), "n_gold": len(gold),
        "P": sc["bcubed_p"], "R": sc["bcubed_r"], "F1": sc["bcubed_f1"],
        "fsplit": sc["false_split_rate"], "fmerge": sc["false_merge_rate"], "exact": sc["exact_cluster_acc"],
        "ece": e_all, "ece_single": e_single, "ece_multi": e_multi, "n_conf_none": n_none,
        "mean_conf": (sum(confs) / len(confs)) if confs else None, "acc": (sum(corr) / len(corr)) if corr else None,
        "true_links": len(true_links),
        "topk_recall": len(true_links & set(ev_pairs)) / max(1, len(true_links)),
        "sv_recall": len(true_links & sv_pairs) / max(1, len(true_links)),
        "link_recall": len(true_links & pred_links) / max(1, len(true_links)),
        "link_prec": len(true_links & pred_links) / max(1, len(pred_links)),
        "sv_prec": len(true_links & sv_pairs) / max(1, len(sv_pairs)),
        "n_sv": len(res.verifications), "secs": round(dt, 1), "stats": res.stats,
    }
    if dump:
        dd = UP3 / "dumps" / (tag or "cur")
        dd.mkdir(parents=True, exist_ok=True)
        payload = {
            "deck": deck, "summary": out,
            "gold": gold,
            "chains": chains,
            "evidence": [e.model_dump(mode="json") for e in res.evidence],
            "verifications": [v.model_dump(mode="json") for v in res.verifications],
            "proposals": [p.model_dump(mode="json") for p in res.proposals],
            "fps": [fp.model_dump(mode="json", exclude={"pixel"}) for fp in fps],
        }
        (dd / f"{deck}.json").write_text(json.dumps(payload, default=str), encoding="utf-8")
    return out


def make_client(live: bool, budget: float):
    inner = None
    ledger = None
    if live:
        from recipeboxer.ai.client import GenAIClient
        from recipeboxer.ai.cost import BudgetGuard, CostLedger, PriceTable
        from recipeboxer.config import get_settings

        s = get_settings()
        ledger = CostLedger(prices=PriceTable.load())
        guard = BudgetGuard(cap_usd=budget)
        inner = GenAIClient(api_key=s.gemini_api_key, settings=s, guard=guard, ledger=ledger)
    return DiskCacheLLMClient(inner), ledger


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--decks", required=True)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--budget-usd", type=float, default=0.0)
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--tag", default="")
    ap.add_argument("--fp-only", action="store_true")
    ap.add_argument("--json-out", default="")
    a = ap.parse_args()
    if a.live and a.budget_usd <= 0:
        raise SystemExit("--budget-usd required for live runs")
    client, ledger = make_client(a.live, a.budget_usd)
    rows = []
    try:
        for d in a.decks.split(","):
            if a.fp_only:
                man = load_manifest(d)
                fps = await get_fingerprints(d, client, man)
                print(f"{d}: {len(fps)} fingerprints")
                continue
            rows.append(await run_deck(d, client, dump=a.dump, tag=a.tag))
    finally:
        if ledger is not None:
            print(f"LIVE SPEND ${ledger.total_usd:.4f} calls={client.live_calls}")
        print(f"cache hits={client.hits} misses={client.misses}")
    if rows:
        hdr = f"{'deck':<16}{'n':>5}{'P':>7}{'R':>7}{'F1':>7}{'fspl':>7}{'fmrg':>7}{'ECE':>7}{'eS':>7}{'eM':>7}{'acc':>6}{'mconf':>6}{'topk':>6}{'sv':>6}{'lnkR':>6}{'lnkP':>6}{'nSV':>5}"
        print(hdr)
        f = lambda v: f"{v:7.3f}" if isinstance(v, (int, float)) else f"{'-':>7}"
        for r in rows:
            print(f"{r['deck']:<16}{r['n']:>5}{f(r['P'])}{f(r['R'])}{f(r['F1'])}{f(r['fsplit'])}{f(r['fmerge'])}{f(r['ece'])}{f(r['ece_single'])}{f(r['ece_multi'])}"
                  f"{r['acc'] or 0:6.2f}{r['mean_conf'] or 0:6.2f}{r['topk_recall']:6.2f}{r['sv_recall']:6.2f}{r['link_recall']:6.2f}{r['link_prec']:6.2f}{r['n_sv']:>5}")
        if a.json_out:
            Path(a.json_out).write_text(json.dumps(rows, indent=1, default=str))


if __name__ == "__main__":
    asyncio.run(main())
