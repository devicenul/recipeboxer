"""Recipe validation and repair loop (WP-XB, X8).

Validates raw dictionary or JSON text against Recipe Schema v2.
If validation or parsing fails, initiates an interactive repair loop feeding
schema errors back to the model up to max_rounds=2 before failing fast.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional, Union

from pydantic import ValidationError

from recipeboxer.ai.client import LLMClient, LLMRequest, LLMStage, TextPart
from recipeboxer.models.failures import BudgetExceeded, ExtractionUnavailable, FailureReason
from recipeboxer.models.recipe import Recipe, RecipeWarning
from recipeboxer.prompts import get_prompt

logger = logging.getLogger(__name__)


def _clean_markdown_fences(raw_str: str) -> str:
    """Removes surrounding markdown code blocks (e.g. ```json ... ```)."""
    text = raw_str.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _format_validation_error(exc: Exception) -> str:
    """Extracts human- and model-actionable error messages from ValidationError or Exception."""
    if isinstance(exc, ValidationError):
        lines = []
        for err in exc.errors():
            loc = " -> ".join(str(elem) for elem in err.get("loc", []))
            msg = err.get("msg", "")
            err_type = err.get("type", "")
            lines.append(f"- Location '{loc}': {msg} (error_type: {err_type})")
        return "\n".join(lines)
    return str(exc)


async def validate_and_repair(
    raw: Union[Dict[str, Any], str],
    llm_client: LLMClient,
    settings: Any,
    max_rounds: int = 2,
) -> Recipe:
    """Validates raw dict or JSON string against Recipe Schema v2, repairing errors if needed.

    Args:
        raw: Dict or JSON string representing extracted recipe.
        llm_client: LLMClient instance for model interaction.
        settings: Application settings with stage model resolution.
        max_rounds: Maximum number of repair rounds (default 2).

    Returns:
        Validated Recipe object. If repaired, carries a REPAIRED_OUTPUT warning.

    Raises:
        ExtractionUnavailable: With reason SCHEMA_VALIDATION if invalid after max_rounds.
        BudgetExceeded: If spend or call budget is exceeded (propagated).
    """
    last_error: str = ""

    # Initial validation attempt (Round 0)
    try:
        if isinstance(raw, dict):
            return Recipe.model_validate(raw)
        elif isinstance(raw, str):
            cleaned = _clean_markdown_fences(raw)
            parsed_json = json.loads(cleaned)
            return Recipe.model_validate(parsed_json)
        else:
            raise ValueError(f"Expected dict or str for recipe validation, got {type(raw).__name__}")
    except BudgetExceeded:
        raise
    except Exception as exc:
        last_error = _format_validation_error(exc)
        logger.info("Initial recipe schema validation failed: %s; entering repair loop", last_error)

    # Prepare repair prompt template
    prompt_record = get_prompt("repair_v1")
    current_payload = raw

    # Repair loop: 1 .. max_rounds
    for round_idx in range(1, max_rounds + 1):
        payload_str = (
            json.dumps(current_payload, indent=2)
            if isinstance(current_payload, dict)
            else str(current_payload)
        )

        user_content = (
            f"The following extracted recipe payload failed schema validation in round {round_idx} of {max_rounds}.\n\n"
            f"SPECIFIC VALIDATION ERRORS TO FIX:\n{last_error}\n\n"
            f"INVALID PAYLOAD:\n{payload_str}\n\n"
            f"Please fix all errors and output a valid Recipe object matching the schema."
        )

        model_name = settings.get_stage_model(LLMStage.REPAIR)
        request = LLMRequest(
            stage=LLMStage.REPAIR,
            model=model_name,
            system_instruction=prompt_record.text,
            parts=[TextPart(text=user_content)],
            response_model=Recipe,
            temperature=0.0,
            metadata={"repair_round": round_idx},
        )

        try:
            response = await llm_client.generate(request)
        except BudgetExceeded:
            raise
        except Exception as gen_err:
            last_error = f"LLM generation failed in round {round_idx}: {gen_err}"
            logger.warning("Repair round %d failed during generation: %s", round_idx, gen_err)
            continue

        repaired_recipe: Optional[Recipe] = None

        if response.parsed and isinstance(response.parsed, Recipe):
            repaired_recipe = response.parsed
        elif response.parsed and isinstance(response.parsed, dict):
            try:
                repaired_recipe = Recipe.model_validate(response.parsed)
            except Exception as parse_exc:
                last_error = _format_validation_error(parse_exc)
                current_payload = response.parsed
        elif response.raw_text:
            try:
                cleaned_resp = _clean_markdown_fences(response.raw_text)
                resp_json = json.loads(cleaned_resp)
                repaired_recipe = Recipe.model_validate(resp_json)
            except Exception as parse_exc:
                last_error = _format_validation_error(parse_exc)
                current_payload = response.raw_text

        if repaired_recipe is not None:
            # Attach REPAIRED_OUTPUT warning
            repair_warning = RecipeWarning(
                code="REPAIRED_OUTPUT",
                message=f"Recipe repaired after schema validation error in round {round_idx}: {last_error}",
                field_path=None,
            )
            repaired_recipe.warnings.append(repair_warning)
            logger.info("Successfully repaired recipe in round %d", round_idx)
            return repaired_recipe

    # If exhausted all rounds without valid Recipe
    raise ExtractionUnavailable(
        f"Schema validation failed after {max_rounds} repair rounds: {last_error}",
        reason=FailureReason.SCHEMA_VALIDATION,
        retryable=False,
        details={"rounds_attempted": max_rounds, "last_error": last_error},
    )


__all__ = [
    "validate_and_repair",
]
