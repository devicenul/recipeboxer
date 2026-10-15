"""WP-UP3: fitted link / cluster / recipe confidence models, ranked screening and consistency timeouts.

Offline and hermetic; no accuracy numbers are asserted. Models here are tiny hand-built stubs so the
tests pin behaviour/semantics, not the shipped coefficients.
"""

from __future__ import annotations

import asyncio
from typing import Any, List

import numpy as np
import pytest

from recipeboxer.ai.client import LLMClient, LLMRequest
from recipeboxer.config import Settings
from recipeboxer.correlation.engine import GroupingEngine
from recipeboxer.correlation.linkmodel import (
    LINK_FEATURES,
    MULTI_FEATURES,
    RANK_FEATURES,
    RECIPE_FEATURES,
    SINGLE_FEATURES,
    LogisticModel,
    Up3Models,
    link_feature_vector,
    recipe_confidence,
)
from recipeboxer.models.correlation import SetVerification
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.recipe import Recipe, RecipeWarning
from tests.test_wp_xb_grounding_repair_consistency import make_fictitious_recipe, make_fictitious_transcript


class InMemoryImageProvider:
    def __init__(self, data: dict) -> None:
        self.data = data

    def get(self, card_id: str) -> bytes:
        return self.data.get(card_id, b"")


def _const_model(names, intercept: float) -> LogisticModel:
    m = LogisticModel(names, coef=np.zeros(len(names)), intercept=intercept)
    return m


def _stub_models(single_p: float = 0.8, multi_p: float = 0.7) -> Up3Models:
    rank = LogisticModel(RANK_FEATURES, coef=np.zeros(len(RANK_FEATURES)), intercept=-1.0)
    rank.coef[RANK_FEATURES.index("a_expects_cont")] = 2.0
    link = LogisticModel(LINK_FEATURES, coef=np.zeros(len(LINK_FEATURES)), intercept=-3.0)
    link.coef[LINK_FEATURES.index("sv_pos_direct")] = 6.0
    return Up3Models({
        "pre": rank, "rank": rank, "link": link,
        "single": _const_model(SINGLE_FEATURES, float(np.log(single_p / (1 - single_p)))),
        "multi": _const_model(MULTI_FEATURES, float(np.log(multi_p / (1 - multi_p)))),
    })


def _fp(cid: str, role: CardRole, title: str | None = None, **kw: Any) -> VisualFingerprint:
    return VisualFingerprint(card_id=cid, image_path=f"/x/{cid}", sha256_hash=cid.ljust(64, "0"), role=role,
                             visible_title=title, **kw)


# ---------------------------------------------------------------------------------------------
def test_logistic_model_fit_roundtrip() -> None:
    rng = np.random.default_rng(0)
    X = rng.normal(size=(400, 2))
    y = (X[:, 0] - 0.5 * X[:, 1] + rng.normal(scale=0.5, size=400) > 0).astype(float)
    m = LogisticModel(["a", "b"]).fit(X, y, l2=1.0)
    assert m.coef[0] > 0 > m.coef[1]
    m2 = LogisticModel.from_dict(m.to_dict())
    assert np.allclose(m.predict_proba(X[:5]), m2.predict_proba(X[:5]), atol=1e-4)


def test_unfitted_models_are_ignored() -> None:
    assert Up3Models({"link": LogisticModel(LINK_FEATURES)}).get("link") is None


def test_link_feature_vector_shapes_and_flags() -> None:
    v = link_feature_vector(1.5, pos_direct=True, pos_chain=True, neg=True, seen=True, conf=0.9, rescue=False,
                            n_into_b=2, n_out_a=1, n_cards=40)
    assert v.shape == (len(LINK_FEATURES),)
    d = dict(zip(LINK_FEATURES, v))
    assert d["sv_pos_direct"] == 1.0 and d["sv_pos_chain"] == 0.0 and d["sv_neg"] == 0.0  # positive wins
    assert d["sv_conf_pos"] == 0.9 and d["n_pos_into_b"] == 1.0


# ---------------------------------------------------------------------------------------------
class _Verifier:
    def __init__(self, cont: dict) -> None:
        self.cont = cont
        self.calls: List[tuple] = []

    async def verify(self, anchor, candidates, images=None):
        self.calls.append((anchor.card_id, [c.card_id for c in candidates]))
        cids = [c.card_id for c in candidates]
        conts = [c for c in self.cont.get(anchor.card_id, []) if c in cids]
        return SetVerification(anchor=anchor.card_id, continuations=conts,
                               rejected=[c for c in cids if c not in conts], confidence=0.9,
                               model="stub", prompt_id="set_verify_v1", prompt_version="v1")


def _deck():
    return [
        _fp("a1", CardRole.SEQUENCE_START, "Plum Cake", has_ingredients=True, has_directions=False,
            text_cut_off_end=True),
        _fp("a2", CardRole.SEQUENCE_CONTINUATION, None, has_ingredients=False, has_directions=True),
        _fp("s1", CardRole.STANDALONE, "Corn Relish", has_ingredients=True, has_directions=True),
        _fp("s2", CardRole.STANDALONE, "Lemon Curd", has_ingredients=True, has_directions=True),
    ]


@pytest.mark.asyncio
async def test_up3_engine_fitted_confidences_and_verified_chain() -> None:
    models = _stub_models(single_p=0.8, multi_p=0.7)
    ver = _Verifier({"a1": ["a2"]})
    eng = GroupingEngine(set_verifier=ver, models=models)
    eng.MIN_CARDS_FITTED = 0  # tiny synthetic deck: exercise the fitted path explicitly
    res = await eng.group(_deck(), InMemoryImageProvider({}), settings=Settings(gemini_api_key=None))
    assert res.stats["screening"] == "up3_ranked"
    assert res.stats["link_model"] == "up3_fitted"
    assert ["a1", "a2"] in res.chains
    by = {tuple(p.ordered_card_ids): p for p in res.proposals}
    assert by[("a1", "a2")].confidence == pytest.approx(0.7, abs=1e-3)  # fitted multi model output
    for sid in ("s1", "s2"):
        # fitted standalone probability (not None, not a hard-coded prior)
        assert by[(sid,)].confidence == pytest.approx(0.8, abs=1e-3)
    assert set(eng.last_rows) == {"link", "single", "multi"}
    assert len(eng.last_rows["single"]) == 2 and len(eng.last_rows["multi"]) == 1


@pytest.mark.asyncio
async def test_up3_engine_without_verifier_keeps_none_standalone_confidence() -> None:
    eng = GroupingEngine(models=_stub_models())
    res = await eng.group(_deck(), InMemoryImageProvider({}), settings=Settings(gemini_api_key=None))
    for p in res.proposals:
        if p.is_standalone:
            assert p.confidence is None  # no SetVerifier evidence -> no fitted probability (INV-AC4)
        assert p.recommended_action in ("UNCERTAIN", "NEEDS_REVIEW")


@pytest.mark.asyncio
async def test_ranked_screening_ignores_capture_order() -> None:
    from recipeboxer.correlation.background import DeckBackground
    from recipeboxer.correlation.ranking import ranked_candidates

    fps = _deck()
    m = _stub_models().get("rank")
    bg = DeckBackground.fit(fps)
    r1 = ranked_candidates(fps, bg, m, m, images=None, pool_m=3)
    rev = list(reversed(fps))
    r2 = ranked_candidates(rev, DeckBackground.fit(rev), m, m, images=None, pool_m=3)
    common = set(r1.rank_logit) & set(r2.rank_logit)
    assert common
    for e in common:
        assert r1.rank_logit[e] == pytest.approx(r2.rank_logit[e], abs=1e-6)


@pytest.mark.asyncio
async def test_rescue_offers_unclaimed_titleless_fragment() -> None:
    models = _stub_models()
    ver = _Verifier({})  # first pass claims nothing
    eng = GroupingEngine(set_verifier=ver, models=models)
    eng.MIN_CARDS_FITTED = 0  # tiny synthetic deck: exercise the fitted path explicitly
    await eng.group(_deck(), InMemoryImageProvider({}), settings=Settings(gemini_api_key=None))
    # a2 is title-less and unclaimed -> some rescue call must include it as a candidate
    assert any("a2" in cands for _, cands in ver.calls)


# ---------------------------------------------------------------------------------------------
def test_recipe_confidence_semantics() -> None:
    r = Recipe(title="Plum Cake")
    assert recipe_confidence(None, r, 1, models=Up3Models({})) is None
    assert recipe_confidence(0.6, r, 1, models=Up3Models({})) == 0.6  # no recipe model -> grouping factor
    m = LogisticModel(RECIPE_FEATURES, coef=np.zeros(len(RECIPE_FEATURES)), intercept=0.0)
    m.coef[RECIPE_FEATURES.index("n_warn")] = -5.0
    m.intercept = 1.0
    mods = Up3Models({"recipe": m})
    clean = recipe_confidence(0.9, r, 1, models=mods)
    r.warnings.append(RecipeWarning(code="GROUNDING_UNSUPPORTED", message="x"))
    warned = recipe_confidence(0.9, r, 1, models=mods)
    assert 0.0 <= warned < clean <= 1.0
    assert recipe_confidence(None, r, 1, models=mods) is None


# ---------------------------------------------------------------------------------------------
class _HangingClient(LLMClient):
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc
        self.requests: List[LLMRequest] = []

    async def generate(self, request: LLMRequest):
        self.requests.append(request)
        raise self.exc


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [asyncio.TimeoutError(), RuntimeError("")])
async def test_consistency_second_read_failure_is_bounded_and_described(exc: BaseException) -> None:
    from recipeboxer.extraction.consistency import self_consistency

    settings = Settings(gemini_api_key=None, default_model="gemini-3.7-flash", quality_profile="balanced")
    recipe = make_fictitious_recipe()
    recipe.warnings.append(RecipeWarning(code="LOW_CONFIDENCE", message="Faded card face"))
    client = _HangingClient(exc)
    out, warns = await self_consistency(recipe, make_fictitious_transcript(), [], client, settings)
    assert len(client.requests) == 1
    req = client.requests[0]
    assert req.metadata.get("max_attempts") == 2
    assert req.timeout_s and req.timeout_s >= 60
    assert len(warns) == 1 and warns[0].code == "VERIFICATION_SKIPPED"
    msg = warns[0].message.split("failed:", 1)[-1].strip()
    assert msg  # never an empty reason
    assert out.confidence == recipe.confidence
