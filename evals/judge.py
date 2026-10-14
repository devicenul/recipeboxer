"""LLM-as-a-Judge semantic evaluation engine for RecipeBoxer (WP-E6).

Performs qualitative assessment of candidate recipe transcriptions against reference
recipes using an independent LLM tier.

Authoritative contracts and design invariants:
1. INV-AC1..INV-AC6: Zero answer-key or fixture leaks.
2. Tier separation: The judge MUST use a different model tier from the extractor
   (e.g., if extractor is pro, judge uses flash; if extractor is flash, judge uses pro).
   Raises ValueError if extractor and judge share the same model tier.
3. Blinded evaluation: No model names, no system names, randomized field order.
   Gold recipe is strictly labelled 'reference', and candidate recipe is strictly
   labelled 'candidate'.
4. Structured outputs: Pydantic response models enforced through LLMRequest with
   stage LLMStage.JUDGE.
5. No mock judge in scoring paths. Only PlumbingJudge is allowed for tests/plumbing
   validation, and its output is labelled 'mock (not a quality score)'.
6. Non-gating: Judge scores are reported for qualitative insight and never gate CI.
"""

from __future__ import annotations

import json
import logging
import random
from typing import Any, Dict, Optional, Protocol, Union, runtime_checkable
from pydantic import BaseModel, Field, field_validator

from recipeboxer.ai.client import (
    LLMClient,
    LLMRequest,
    LLMStage,
    TextPart,
    create_llm_client,
)
from recipeboxer.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Scrub keys that could reveal identity, origin, or system internals
METADATA_KEYS_TO_SCRUB = {
    "model",
    "system",
    "run_id",
    "pipeline",
    "generator",
    "card_id",
    "recipe_id",
    "pred_id",
    "confidence",
    "flags",
    "image_ids",
    "stock_id",
    "sha256",
    "deck_id",
    "deck_type",
}

BLINDED_JUDGE_SYSTEM_INSTRUCTION = (
    "You are an impartial culinary and document transcription judge.\n"
    "Your objective is to evaluate the fidelity, completeness, and qualitative accuracy "
    "of a candidate recipe transcription against an authoritative reference recipe.\n\n"
    "Scoring criteria (0.0 to 5.0):\n"
    "- 5.0: Flawless culinary transcription. Ingredient quantities, units, and items match perfectly; "
    "directions retain complete logical sequence and cooking techniques; headnotes/notes preserved.\n"
    "- 4.0: Highly accurate. Negligible wording differences or formatting variations that do not alter the dish.\n"
    "- 3.0: Acceptable but with minor omissions (e.g., optional garnish missing from directions, slight unit ambiguity).\n"
    "- 2.0: Noticeable culinary errors, missing ingredients, or truncated preparation steps.\n"
    "- 1.0: Severe discrepancies, hallucinated ingredients, or scrambled step order.\n"
    "- 0.0: Unusable, completely inaccurate, or empty transcription.\n\n"
    "Provide an objective score between 0.0 and 5.0 and a concise rationale detailing any omissions, "
    "discrepancies, or variations."
)


def get_model_tier(model_name: str) -> str:
    """Determines model tier ('flash' or 'pro') from model identifier string.

    Args:
        model_name: Model identifier (e.g. 'gemini-3.7-flash', 'gemini-2.5-pro', 'flash', 'pro').

    Returns:
        'flash' or 'pro'.

    Raises:
        ValueError: If model name does not indicate 'flash' or 'pro'.
    """
    m = model_name.lower().strip()
    if "pro" in m:
        return "pro"
    if "flash" in m:
        return "flash"
    raise ValueError(
        f"Unrecognized model tier for '{model_name}'. Expected model identifier containing 'flash' or 'pro'."
    )


def _scrub_metadata(d: Any) -> Any:
    """Recursively removes internal identifying metadata keys from dictionaries."""
    if isinstance(d, dict):
        return {
            k: _scrub_metadata(v)
            for k, v in d.items()
            if k.lower() not in METADATA_KEYS_TO_SCRUB
        }
    if isinstance(d, list):
        return [_scrub_metadata(x) for x in d]
    return d


def _randomize_dict_fields(d: Any, seed: Optional[int] = None) -> Any:
    """Recursively randomizes the insertion order of dictionary keys to prevent order bias.

    Preserves list element order (important for ingredients and recipe steps).
    """
    rng = random.Random(seed)
    if isinstance(d, dict):
        items = list(d.items())
        rng.shuffle(items)
        return {k: _randomize_dict_fields(v, seed=rng.randint(0, 1000000)) for k, v in items}
    if isinstance(d, list):
        return [_randomize_dict_fields(x, seed=rng.randint(0, 1000000)) for x in d]
    return d


class JudgeResultSchema(BaseModel):
    """Structured Pydantic model for LLM judge responses."""

    score: float = Field(
        ...,
        description="Overall recipe quality and transcription fidelity score from 0.0 to 5.0",
    )
    rationale: str = Field(
        ...,
        description="Impartial rationale and justification citing specific discrepancies or confirming fidelity",
    )

    @field_validator("score", mode="before")
    @classmethod
    def _clamp_score(cls, v: Any) -> float:
        """Clamps score between 0.0 and 5.0."""
        try:
            val = float(v)
            return max(0.0, min(5.0, val))
        except (ValueError, TypeError):
            return 0.0


@runtime_checkable
class Judge(Protocol):
    """Protocol for semantic recipe evaluation judges."""

    async def judge(self, pred: dict, gold: dict) -> dict:
        """Evaluates a candidate prediction against a reference ground truth.

        Args:
            pred: Candidate recipe dictionary.
            gold: Reference recipe dictionary.

        Returns:
            Dict conforming to {"score": float, "rationale": str}.
        """
        ...


class LiveJudge:
    """Blinded live LLM judge using LLMClient with stage JUDGE."""

    def __init__(
        self,
        client: LLMClient,
        model: str,
        extractor_model: str,
    ) -> None:
        """Initializes LiveJudge with explicit client and tier validation.

        Args:
            client: Connected LLMClient instance.
            model: Judge model identifier.
            extractor_model: Extractor model identifier (must be a different tier).

        Raises:
            ValueError: If judge model and extractor model belong to the same tier.
        """
        self.client = client
        self.model = model
        self.extractor_model = extractor_model

        judge_tier = get_model_tier(model)
        extractor_tier = get_model_tier(extractor_model)
        if judge_tier == extractor_tier:
            raise ValueError(
                f"Judge model '{model}' (tier: {judge_tier}) must use a different tier "
                f"from extractor model '{extractor_model}' (tier: {extractor_tier})"
            )
        self.judge_tier = judge_tier
        self.extractor_tier = extractor_tier

    async def judge(self, pred: dict, gold: dict) -> dict:
        """Evaluates candidate recipe against reference recipe in a blinded fashion."""
        # 1. Scrub internal identifying metadata
        clean_pred = _scrub_metadata(pred)
        clean_gold = _scrub_metadata(gold)

        # 2. Randomize field order to avoid position bias
        blinded_candidate = _randomize_dict_fields(clean_pred)
        blinded_reference = _randomize_dict_fields(clean_gold)

        # 3. Format blinded prompt strictly labelling reference and candidate
        cand_json = json.dumps(blinded_candidate, indent=2, sort_keys=False)
        ref_json = json.dumps(blinded_reference, indent=2, sort_keys=False)

        prompt_text = (
            "Please evaluate the candidate recipe transcription against the reference recipe.\n\n"
            "### reference\n"
            f"```json\n{ref_json}\n```\n\n"
            "### candidate\n"
            f"```json\n{cand_json}\n```\n\n"
            "Assess semantic fidelity, ingredient quantities/units, direction completeness, "
            "and note preservation. Return your score (0.0 to 5.0) and rationale."
        )

        request = LLMRequest(
            stage=LLMStage.JUDGE,
            model=self.model,
            system_instruction=BLINDED_JUDGE_SYSTEM_INSTRUCTION,
            parts=[TextPart(text=prompt_text)],
            response_model=JudgeResultSchema,
            temperature=0.0,
        )

        response = await self.client.generate(request)

        if response.parsed and isinstance(response.parsed, JudgeResultSchema):
            score = max(0.0, min(5.0, float(response.parsed.score)))
            return {
                "score": score,
                "rationale": response.parsed.rationale,
            }

        # Fallback if raw text returned in test environments
        raw = response.raw_text.strip()
        score = 0.0
        rationale = raw
        try:
            parsed_raw = json.loads(raw)
            if isinstance(parsed_raw, dict):
                score = float(parsed_raw.get("score", 0.0))
                rationale = str(parsed_raw.get("rationale", raw))
        except Exception:
            pass

        return {
            "score": max(0.0, min(5.0, score)),
            "rationale": rationale,
        }


class PlumbingJudge:
    """Mock judge strictly for test fixtures and plumbing verification.

    NEVER used in official scoring paths. Always labels its outputs with
    'mock (not a quality score)'.
    """

    def __init__(
        self,
        score: float = 5.0,
        rationale: str = "Deterministic test fixture evaluation.",
    ) -> None:
        self.score = float(score)
        self.rationale = rationale

    async def judge(self, pred: dict, gold: dict) -> dict:
        """Returns mock evaluation result with explicit mock disclaimer."""
        return {
            "score": max(0.0, min(5.0, self.score)),
            "rationale": f"mock (not a quality score): {self.rationale}",
            "mock_label": "mock (not a quality score)",
        }


def create_judge(
    settings: Optional[Settings] = None,
    extractor_model: str = "gemini-3.7-flash",
    judge_model: Optional[str] = None,
    client: Optional[LLMClient] = None,
) -> Optional[Judge]:
    """Factory creating an independent live Judge conforming to Wave 3 contracts.

    Rules:
    - Returns None if no Gemini API key is configured.
    - Selects the opposing model tier from extractor_model (flash <-> pro).
    - Raises ValueError if the configured or requested judge model belongs to the same tier.
    - Never instantiates a mock judge; official scoring paths return None when offline.

    Args:
        settings: Application Settings instance.
        extractor_model: Model identifier used by the extraction stage.
        judge_model: Optional explicit judge model override.
        client: Optional pre-configured LLMClient instance (useful for testing).

    Returns:
        LiveJudge instance if API credentials exist, else None.

    Raises:
        ValueError: If judge model and extractor model share the same tier.
    """
    s = settings or get_settings()

    # 1. Determine extractor tier
    extractor_tier = get_model_tier(extractor_model)

    # 2. Determine judge model and tier
    if judge_model:
        effective_judge = judge_model
    elif getattr(s, "judge_model", None):
        effective_judge = s.judge_model
    else:
        # Default opposing tier
        if extractor_tier == "flash":
            effective_judge = "gemini-3.7-pro"
        else:
            effective_judge = "gemini-3.7-flash"

    judge_tier = get_model_tier(effective_judge)
    if judge_tier == extractor_tier:
        raise ValueError(
            f"Judge model tier '{judge_tier}' ({effective_judge}) cannot match "
            f"extractor model tier '{extractor_tier}' ({extractor_model}). "
            f"LLM-as-a-Judge requires an independent model tier to prevent self-preference bias."
        )

    # 3. Resolve client
    if client is not None:
        effective_client = client
    else:
        effective_client = create_llm_client(s)
        if effective_client is None:
            return None

    return LiveJudge(
        client=effective_client,
        model=effective_judge,
        extractor_model=extractor_model,
    )


__all__ = [
    "Judge",
    "LiveJudge",
    "PlumbingJudge",
    "JudgeResultSchema",
    "create_judge",
    "get_model_tier",
    "BLINDED_JUDGE_SYSTEM_INSTRUCTION",
]
