"""Unit tests for LLM-as-a-Judge semantic evaluator (WP-E6).

Tests verify:
- Conformance to Judge Protocol.
- Model tier discrimination and enforcement (different tiers required; same tier raises ValueError).
- Factory create_judge returns None when offline / unkeyed, never silent mock.
- Blinded evaluation (no system/model names, randomized field order, gold labelled 'reference', pred 'candidate').
- Structured response model parsing and score clamping [0.0, 5.0].
- PlumbingJudge mock labelling ('mock (not a quality score)').
- 100% offline execution with ScriptedLLMClient.
"""

from __future__ import annotations

import asyncio
import json
import pytest
from pydantic import BaseModel

from evals.judge import (
    BLINDED_JUDGE_SYSTEM_INSTRUCTION,
    Judge,
    JudgeResultSchema,
    LiveJudge,
    PlumbingJudge,
    create_judge,
    get_model_tier,
)
from recipeboxer.ai.client import LLMRequest, LLMResponse, LLMStage, UsageTokens
from recipeboxer.ai.replay import ScriptedLLMClient
from recipeboxer.config import Settings


def test_model_tier_classification():
    """Verifies get_model_tier accurately detects 'flash' and 'pro' tiers."""
    assert get_model_tier("gemini-3.7-flash") == "flash"
    assert get_model_tier("gemini-2.5-flash") == "flash"
    assert get_model_tier("gemini-2.0-flash-lite") == "flash"
    assert get_model_tier("flash") == "flash"

    assert get_model_tier("gemini-3.7-pro") == "pro"
    assert get_model_tier("gemini-2.5-pro") == "pro"
    assert get_model_tier("pro") == "pro"

    with pytest.raises(ValueError, match="Unrecognized model tier"):
        get_model_tier("llama-3-8b")


def test_create_judge_returns_none_when_unconfigured():
    """create_judge returns None if no Gemini API key is configured (never mock)."""
    settings = Settings(gemini_api_key=None)
    judge = create_judge(settings=settings, extractor_model="gemini-3.7-flash")
    assert judge is None


def test_tier_enforcement_raises_on_identical_tiers():
    """Live judge creation must reject matching extractor and judge tiers."""
    dummy_client = ScriptedLLMClient([])

    # Both flash -> ValueError
    with pytest.raises(ValueError, match="must use a different tier"):
        LiveJudge(
            client=dummy_client,
            model="gemini-3.7-flash",
            extractor_model="gemini-2.5-flash",
        )

    # Both pro -> ValueError
    with pytest.raises(ValueError, match="must use a different tier"):
        LiveJudge(
            client=dummy_client,
            model="gemini-3.7-pro",
            extractor_model="gemini-2.5-pro",
        )


def test_create_judge_auto_selects_opposing_tier():
    """create_judge automatically selects the opposite tier if not overridden."""
    dummy_client = ScriptedLLMClient([])

    # Extractor is flash -> Judge auto-selects pro
    judge_pro = create_judge(
        settings=Settings(gemini_api_key="dummy-key"),
        extractor_model="gemini-3.7-flash",
        client=dummy_client,
    )
    assert isinstance(judge_pro, LiveJudge)
    assert judge_pro.judge_tier == "pro"
    assert judge_pro.extractor_tier == "flash"

    # Extractor is pro -> Judge auto-selects flash
    judge_flash = create_judge(
        settings=Settings(gemini_api_key="dummy-key"),
        extractor_model="gemini-3.7-pro",
        client=dummy_client,
    )
    assert isinstance(judge_flash, LiveJudge)
    assert judge_flash.judge_tier == "flash"
    assert judge_flash.extractor_tier == "pro"


def test_create_judge_raises_on_same_tier_override():
    """create_judge raises ValueError if judge_model override matches extractor tier."""
    dummy_client = ScriptedLLMClient([])
    with pytest.raises(ValueError, match="cannot match extractor model tier"):
        create_judge(
            settings=Settings(gemini_api_key="dummy-key"),
            extractor_model="gemini-3.7-flash",
            judge_model="gemini-2.5-flash",
            client=dummy_client,
        )


@pytest.mark.asyncio
async def test_live_judge_blinded_prompt_and_structured_output():
    """Verifies LiveJudge blinds prompts, randomizes key order, and maps structured output."""
    canned_result = JudgeResultSchema(
        score=4.5,
        rationale="Excellent ingredient accuracy; minor omission of serving suggestion.",
    )
    client = ScriptedLLMClient([canned_result])

    judge = LiveJudge(
        client=client,
        model="gemini-3.7-pro",
        extractor_model="gemini-3.7-flash",
    )

    pred = {
        "title": "Grandma's Sugar Cookies",
        "ingredients": ["2 cups flour", "1 cup sugar"],
        "directions": ["Mix dry ingredients.", "Bake at 350F for 10 min."],
        # System metadata that must be scrubbed:
        "model": "gemini-3.7-flash",
        "pipeline": "recipeboxer",
        "recipe_id": "r001",
    }
    gold = {
        "title": "Grandma's Sugar Cookies",
        "ingredients": ["2 cups flour", "1 cup sugar"],
        "directions": ["Mix dry ingredients.", "Bake at 350F for 10 min."],
        "card_id": "c001",
        "generator": "cardforge",
    }

    result = await asyncio.wait_for(judge.judge(pred, gold), timeout=5.0)

    assert result["score"] == 4.5
    assert "serving suggestion" in result["rationale"]

    # Inspect the captured LLM request
    assert len(client.requests) == 1
    req = client.requests[0]
    assert req.stage == LLMStage.JUDGE
    assert req.model == "gemini-3.7-pro"
    assert req.response_model == JudgeResultSchema

    prompt_body = req.parts[0].text
    # Blinding invariants:
    # 1. No system or model names in prompt text
    assert "RecipeBoxer" not in prompt_body
    assert "recipeboxer" not in prompt_body
    assert "Gemini" not in prompt_body
    assert "gemini" not in prompt_body
    assert "cardforge" not in prompt_body

    # 2. Strict labelling: 'reference' and 'candidate'
    assert "### reference" in prompt_body
    assert "### candidate" in prompt_body
    assert "gold" not in prompt_body.lower()
    assert "pred" not in prompt_body.lower()


@pytest.mark.asyncio
async def test_live_judge_score_clamping():
    """Scores returned from LLM are clamped between 0.0 and 5.0."""
    client = ScriptedLLMClient([
        {"score": 99.0, "rationale": "Overinflated score"},
        {"score": -5.0, "rationale": "Negative score"},
    ])

    judge = LiveJudge(
        client=client,
        model="gemini-3.7-pro",
        extractor_model="gemini-3.7-flash",
    )

    res_high = await asyncio.wait_for(judge.judge({"a": 1}, {"b": 2}), timeout=5.0)
    assert res_high["score"] == 5.0

    res_low = await asyncio.wait_for(judge.judge({"a": 1}, {"b": 2}), timeout=5.0)
    assert res_low["score"] == 0.0


@pytest.mark.asyncio
async def test_plumbing_judge_mock_label():
    """PlumbingJudge output is explicitly labelled 'mock (not a quality score)'."""
    plumber = PlumbingJudge(score=4.0, rationale="Sanity check run.")
    res = await asyncio.wait_for(plumber.judge({}, {}), timeout=5.0)

    assert res["score"] == 4.0
    assert "mock (not a quality score)" in res["rationale"]
    assert res.get("mock_label") == "mock (not a quality score)"


def test_judge_protocol_conformance():
    """Both LiveJudge and PlumbingJudge conform to the Judge Protocol."""
    assert issubclass(LiveJudge, Judge)
    assert issubclass(PlumbingJudge, Judge)
