"""WP-UP4: out-of-distribution guard + hard link constraints + set_verify_v2 for back anchors.

Offline and hermetic; stub verifiers keyed by explicit maps, tiny hand-built models. No accuracy numbers.
"""

from __future__ import annotations

from typing import Any, List

import numpy as np
import pytest

from recipeboxer.config import Settings
from recipeboxer.correlation import guards as G
from recipeboxer.correlation.engine import GroupingEngine
from recipeboxer.correlation.linkmodel import (
    LINK_FEATURES,
    MULTI_FEATURES,
    RANK_FEATURES,
    SINGLE_FEATURES,
    LogisticModel,
    Up3Models,
    card_descriptor,
)
from recipeboxer.models.correlation import FeatureEvidence, PairEvidence, SetVerification
from recipeboxer.models.domain import CardRole, VisualFingerprint


class _Images:
    def get(self, card_id: str) -> bytes:
        return b""


def _fp(cid: str, role: CardRole = CardRole.STANDALONE, title: str | None = None, **kw: Any) -> VisualFingerprint:
    return VisualFingerprint(card_id=cid, image_path=f"/x/{cid}", sha256_hash=cid.ljust(64, "0"), role=role,
                             visible_title=title, **kw)


def _models(support: dict | None = None) -> Up3Models:
    rank = LogisticModel(RANK_FEATURES, coef=np.zeros(len(RANK_FEATURES)), intercept=-1.0)
    rank.coef[RANK_FEATURES.index("a_expects_cont")] = 2.0
    link = LogisticModel(LINK_FEATURES, coef=np.zeros(len(LINK_FEATURES)), intercept=-3.0)
    link.coef[LINK_FEATURES.index("sv_pos_direct")] = 6.0
    const = lambda names: LogisticModel(names, coef=np.zeros(len(names)), intercept=1.0)  # noqa: E731
    meta = {"support": support} if support else {}
    return Up3Models({"pre": rank, "rank": rank, "link": link, "single": const(SINGLE_FEATURES),
                      "multi": const(MULTI_FEATURES)}, meta=meta)


class _Verifier:
    """Explicit map anchor -> continuations; optional validated quotes per anchor."""

    def __init__(self, cont: dict, quotes: dict | None = None, conf: float = 0.9) -> None:
        self.cont, self.quotes, self.conf = cont, quotes or {}, conf
        self.calls: List[tuple] = []

    async def verify(self, anchor, candidates, images=None):
        cids = [c.card_id for c in candidates]
        self.calls.append((anchor.card_id, cids))
        conts = [c for c in self.cont.get(anchor.card_id, []) if c in cids]
        q = self.quotes.get(anchor.card_id, [])
        return SetVerification(anchor=anchor.card_id, continuations=conts, rejected=[c for c in cids if c not in conts],
                               confidence=self.conf, evidence_quotes=q, verified_quotes=q, model="stub",
                               prompt_id="set_verify_v1", prompt_version="v1")


def _card_deck():
    return [
        _fp("a1", CardRole.SEQUENCE_START, "Plum Cake", has_ingredients=True, has_directions=False,
            text_cut_off_end=True, first_lines=["Plum Cake"], last_lines=["2 cups flour"]),
        _fp("a2", CardRole.SEQUENCE_CONTINUATION, None, has_ingredients=False, has_directions=True,
            first_lines=["Cream butter"], last_lines=["Bake 1 hour"]),
        _fp("s1", CardRole.STANDALONE, "Corn Relish", has_ingredients=True, has_directions=True,
            first_lines=["Corn Relish"], last_lines=["Seal jars"]),
        _fp("s2", CardRole.STANDALONE, "Lemon Curd", has_ingredients=True, has_directions=True,
            first_lines=["Lemon Curd"], last_lines=["Chill well"]),
    ]


def _support_from(fps) -> dict:
    return G.DomainSupport.fit(fps, [card_descriptor(f) for f in fps]).to_dict()


def _dense(cid: str, **kw) -> VisualFingerprint:
    long = "take a pound of fine flower and a pound of sugar beaten and searced then put in"
    return _fp(cid, first_lines=[long], last_lines=[long], **kw)


# ---------------------------------------------------------------------------------------------
# DomainSupport
# ---------------------------------------------------------------------------------------------
def test_domain_support_in_support_deck_is_not_ood():
    fps = _card_deck()
    sup = G.DomainSupport.from_meta({"support": _support_from(fps * 3)})
    rep = sup.assess(fps, {f.card_id: card_descriptor(f) for f in fps})
    assert rep["available"] and rep["ood"] is False and rep["frac_out"] == 0.0


def test_domain_support_flags_dense_page_deck():
    sup = G.DomainSupport.from_meta({"support": _support_from(_card_deck() * 3)})
    pages = [_dense(f"p{i}", title=f"To make Dish {i}", has_ingredients=False, has_directions=True) for i in range(6)]
    rep = sup.assess(pages, {f.card_id: card_descriptor(f) for f in pages})
    assert rep["ood"] is True and rep["frac_dense"] == 1.0


def test_domain_support_unavailable_without_meta():
    rep = G.DomainSupport.from_meta({}).assess(_card_deck(), {})
    assert rep == {"available": False, "ood": False, "frac_out": 0.0}


def test_domain_support_roundtrip_dict():
    d = _support_from(_card_deck())
    s2 = G.DomainSupport.from_meta({"support": d})
    assert s2.wpl_q99 == d["wpl_q99"] and s2.patterns == d["patterns"]


# ---------------------------------------------------------------------------------------------
# Hard constraints
# ---------------------------------------------------------------------------------------------
def test_nonrecipe_page_detection():
    dist = _fp("d", CardRole.UNKNOWN, "Linen sent to wash", has_ingredients=False, has_directions=False)
    frag = _fp("f", CardRole.UNKNOWN, None, has_ingredients=False, has_directions=False, text_cut_off_start=True)
    rec = _fp("r", CardRole.STANDALONE, "Corn Relish", has_ingredients=True, has_directions=True)
    assert G.is_nonrecipe(card_descriptor(dist), dist)
    assert not G.is_nonrecipe(card_descriptor(frag), frag)
    assert not G.is_nonrecipe(card_descriptor(rec), rec)


@pytest.mark.parametrize("title,first,cut_start,expected", [
    ("To make a Tansey", ["To make a Tansey"], False, True),          # new heading at the top
    ("To make a Tansey", ["slices then bake it an hour"], False, False),  # heading lower on the page
    ("To make a Tansey", ["To make a Tansey"], True, False),          # text cut off at the start
    ("Plum Cake (cont.)", ["Plum Cake (cont.)"], False, False),       # continuation header
    ("Plum Cake", ["Plum Cake"], False, False),                       # repeats A's title
])
def test_starts_new_recipe(title, first, cut_start, expected):
    a = _fp("a", CardRole.SEQUENCE_START, "Plum Cake")
    b = _fp("b", CardRole.STANDALONE, title, first_lines=first, text_cut_off_start=cut_start)
    assert G.starts_new_recipe(a, b, card_descriptor(b)) is expected


def test_physical_conflict_threshold():
    mk = lambda llr: PairEvidence(a="x", b="y", features=[FeatureEvidence(name="paper_lab", raw_value=0.1, log_lr=llr)])  # noqa: E731
    assert G.physical_conflict(mk(-4.0))
    assert not G.physical_conflict(mk(-1.7))


def test_sv_positive_ok_requires_validated_quote_and_confidence():
    assert G.sv_positive_ok((1, 0, 0.9), 0.6)
    assert not G.sv_positive_ok((0, 0, 0.9), 0.6)   # no validated quote
    assert not G.sv_positive_ok((1, 1, 0.9), 0.6)   # a contradicted quote
    assert not G.sv_positive_ok((1, 0, 0.5), 0.6)
    assert not G.sv_positive_ok(None, 0.0)


# ---------------------------------------------------------------------------------------------
# Engine integration
# ---------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_engine_in_support_links_verified_pair_and_reports_guard():
    deck = _card_deck()
    eng = GroupingEngine(set_verifier=_Verifier({"a1": ["a2"]}), models=_models(_support_from(deck * 3)))
    eng.MIN_CARDS_FITTED = 0
    res = await eng.group(deck, _Images(), settings=Settings(gemini_api_key=None))
    assert ["a1", "a2"] in res.chains
    assert res.stats["domain_guard"]["strict_mode"] is False


@pytest.mark.asyncio
async def test_engine_strict_mode_blocks_unquoted_sv_positive():
    sup = _support_from(_card_deck() * 3)
    pages = [
        _dense("p1", role=CardRole.SEQUENCE_START, title="To make a Cake", has_ingredients=False, has_directions=True,
               text_cut_off_end=True),
        _dense("p2", role=CardRole.SEQUENCE_CONTINUATION, has_ingredients=False, has_directions=True,
               text_cut_off_start=True),
        _dense("p3", title="To make Wafers", has_ingredients=False, has_directions=True),
        _dense("p4", title="To make Mead", has_ingredients=False, has_directions=True),
    ]
    # Verifier says p1 -> p2 but cites no validated quote: strict mode must not link it.
    eng = GroupingEngine(set_verifier=_Verifier({"p1": ["p2"]}), models=_models(sup))
    eng.MIN_CARDS_FITTED = 0
    res = await eng.group(pages, _Images(), settings=Settings(gemini_api_key=None))
    assert res.stats["domain_guard"]["strict_mode"] is True
    assert all(len(c) == 1 for c in res.chains)
    assert res.stats["link_gates"].get("strict_unconfirmed", 0) >= 1
    # Same deck, verifier cites a validated quote: the link is allowed.
    eng2 = GroupingEngine(set_verifier=_Verifier({"p1": ["p2"]}, quotes={"p1": ["put in"]}), models=_models(sup))
    eng2.MIN_CARDS_FITTED = 0
    res2 = await eng2.group(pages, _Images(), settings=Settings(gemini_api_key=None))
    assert ["p1", "p2"] in res2.chains


@pytest.mark.asyncio
async def test_engine_strict_mode_disables_orphan_rescue():
    sup = _support_from(_card_deck() * 3)
    pages = [_dense(f"p{i}", has_ingredients=False, has_directions=True,
                    title=None if i == 2 else f"To make Dish {i}") for i in range(1, 5)]
    ver = _Verifier({})
    eng = GroupingEngine(set_verifier=ver, models=_models(sup))
    eng.MIN_CARDS_FITTED = 0
    res = await eng.group(pages, _Images(), settings=Settings(gemini_api_key=None))
    assert res.stats["domain_guard"]["strict_mode"] is True
    assert res.stats["rescue_calls"] == 0


@pytest.mark.asyncio
async def test_engine_never_links_distractor_even_if_verifier_says_so():
    deck = _card_deck() + [_fp("d1", CardRole.UNKNOWN, "Phone numbers", has_ingredients=False, has_directions=False,
                                first_lines=["Phone numbers"], last_lines=["Hardware 4-0093"])]
    eng = GroupingEngine(set_verifier=_Verifier({"s1": ["d1"]}, quotes={"s1": ["Seal jars"]}),
                         models=_models(_support_from(deck * 3)))
    eng.MIN_CARDS_FITTED = 0
    res = await eng.group(deck, _Images(), settings=Settings(gemini_api_key=None))
    assert ["d1"] in res.chains
    assert not any("d1" in c and len(c) > 1 for c in res.chains)


@pytest.mark.asyncio
async def test_engine_new_heading_needs_strong_quoted_evidence():
    deck = _card_deck()
    # s1 visibly starts a new recipe at its top; an unquoted SV positive a2 -> s1 must not link.
    eng = GroupingEngine(set_verifier=_Verifier({"a1": ["a2", "s1"]}), models=_models(_support_from(deck * 3)))
    eng.MIN_CARDS_FITTED = 0
    res = await eng.group(deck, _Images(), settings=Settings(gemini_api_key=None))
    assert not any("s1" in c and len(c) > 1 for c in res.chains)


def test_guards_can_be_disabled_for_ablation():
    assert GroupingEngine.GUARDS_ENABLED is True


# ---------------------------------------------------------------------------------------------
# set_verify_v2 for back / continuation anchors
# ---------------------------------------------------------------------------------------------
def test_set_verifier_uses_v2_prompt_for_back_anchor():
    import asyncio

    from recipeboxer.ai.replay import ScriptedLLMClient
    from recipeboxer.correlation.semantic import SetVerificationResponse, SetVerifier

    resp = SetVerificationResponse(continuations=["Candidate 1"], rejected=[], evidence_quotes=[], confidence=0.8,
                                   reasoning="r")
    sc = ScriptedLLMClient([resp, resp])
    sv = SetVerifier(llm_client=sc, settings=Settings(gemini_api_key=None))
    back = _fp("b1", CardRole.SEQUENCE_CONTINUATION, None, looks_like_back=True)
    front = _fp("f1", CardRole.SEQUENCE_START, "Plum Cake")
    nxt = _fp("n1", CardRole.SEQUENCE_CONTINUATION, None)
    v_back = asyncio.run(asyncio.wait_for(sv.verify(back, [nxt]), timeout=10))
    v_front = asyncio.run(asyncio.wait_for(sv.verify(front, [nxt]), timeout=10))
    assert v_back.prompt_id == "set_verify_v2" and v_front.prompt_id == "set_verify_v1"
    assert "back" in sc.requests[0].system_instruction.lower() and "NEXT physical card" in sc.requests[0].system_instruction
    assert sc.requests[1].system_instruction != sc.requests[0].system_instruction

