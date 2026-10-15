"""Self-consistency verification pass for RecipeBoxer extraction pipeline (WP-XB, X9).

Runs an independent second reading per settings.profile_params()['self_consistency']
(different temperature or different model), computes field-level diffs (title,
ingredient lines, steps), and flags MODEL_DISAGREEMENT warnings carrying both readings
for the HITL UI. Agreement raises overall confidence; never silently picks.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from pydantic import BaseModel, Field

from recipeboxer.ai.client import ImagePart, LLMClient, LLMRequest, LLMStage, TextPart
from recipeboxer.extraction.grounding import levenshtein_distance
from recipeboxer.models.failures import BudgetExceeded, ExtractionUnavailable, FailureReason, StageUnavailableError
from recipeboxer.models.recipe import IngredientLine, Recipe, RecipeWarning, Step
from recipeboxer.prompts import get_prompt

try:
    from recipeboxer.extraction.transcribe import FaceTranscript, Transcript
except ImportError:
    from recipeboxer.models.recipe import IllegibleSpan

    class FaceTranscript(BaseModel):  # type: ignore[no-redef]
        face_index: int
        text: str
        illegible_spans: List[IllegibleSpan] = Field(default_factory=list)
        margin_notes: List[str] = Field(default_factory=list)
        crossed_out: List[str] = Field(default_factory=list)
        confidence: Optional[float] = None

    class Transcript(BaseModel):  # type: ignore[no-redef]
        faces: List[FaceTranscript] = Field(default_factory=list)
        model: str = ""
        prompt_id: str = ""
        prompt_version: str = ""

logger = logging.getLogger(__name__)


def _normalize_unit(unit: Optional[str]) -> Optional[str]:
    """Canonicalizes unit names for comparison."""
    if not unit:
        return None
    u = unit.lower().rstrip(".")
    mapping = {
        "c": "cup",
        "cups": "cup",
        "tbsp": "tablespoon",
        "tbs": "tablespoon",
        "tablespoons": "tablespoon",
        "tsp": "teaspoon",
        "teaspoons": "teaspoon",
        "oz": "ounce",
        "ounces": "ounce",
        "lb": "pound",
        "lbs": "pound",
        "pounds": "pound",
        "g": "gram",
        "grams": "gram",
    }
    return mapping.get(u, u)


def _prepare_face_parts(faces: Any) -> List[ImagePart]:
    """Converts diverse face input representations into ImageParts."""
    parts: List[ImagePart] = []
    if not faces:
        return parts

    for idx, face in enumerate(faces):
        label = f"Face {idx + 1}"
        if isinstance(face, ImagePart):
            parts.append(face)
        elif isinstance(face, Path):
            if not face.is_file():
                raise ExtractionUnavailable(
                    f"Face image file not found or not a file: {face}",
                    reason=FailureReason.INPUT_UNREADABLE,
                    retryable=False,
                )
            try:
                parts.append(ImagePart(data=face.read_bytes(), label=label))
            except Exception as exc:
                raise ExtractionUnavailable(
                    f"Face image file is unreadable: {face}: {exc}",
                    reason=FailureReason.INPUT_UNREADABLE,
                    retryable=False,
                ) from exc
        elif isinstance(face, str):
            p = Path(face)
            if not p.is_file():
                raise ExtractionUnavailable(
                    f"Face image file not found or not a file: {face}",
                    reason=FailureReason.INPUT_UNREADABLE,
                    retryable=False,
                )
            try:
                parts.append(ImagePart(data=p.read_bytes(), label=label))
            except Exception as exc:
                raise ExtractionUnavailable(
                    f"Face image file is unreadable: {face}: {exc}",
                    reason=FailureReason.INPUT_UNREADABLE,
                    retryable=False,
                ) from exc
        elif isinstance(face, bytes):
            parts.append(ImagePart(data=face, label=label))
        elif hasattr(face, "image_bytes") and getattr(face, "image_bytes"):
            parts.append(ImagePart(data=getattr(face, "image_bytes"), label=label))
        elif hasattr(face, "bytes") and getattr(face, "bytes"):
            parts.append(ImagePart(data=getattr(face, "bytes"), label=label))
        elif hasattr(face, "data") and getattr(face, "data"):
            parts.append(ImagePart(data=getattr(face, "data"), label=label))
        else:
            raise ExtractionUnavailable(
                f"Unrecognized face representation: {face}",
                reason=FailureReason.INPUT_UNREADABLE,
                retryable=False,
            )
    return parts


def diff_recipes(
    reading1: Recipe,
    reading2: Recipe,
) -> List[RecipeWarning]:
    """Computes field-level differences across title, ingredients, and steps.

    Generates MODEL_DISAGREEMENT warnings carrying both readings for HITL display.
    Never silently picks one reading over the other.
    """
    disagreements: List[RecipeWarning] = []

    # 1. Title comparison
    t1 = reading1.title.strip()
    t2 = reading2.title.strip()
    title_dist = levenshtein_distance(t1.lower(), t2.lower())
    title_cer = title_dist / max(len(t1), len(t2), 1)
    if title_cer > 0.15:
        disagreements.append(
            RecipeWarning(
                code="MODEL_DISAGREEMENT",
                message=f"Title disagreement: reading 1 has '{t1}', reading 2 has '{t2}'",
                field_path="title",
            )
        )

    # 2. Ingredients comparison
    ing1 = reading1.all_ingredients()
    ing2 = reading2.all_ingredients()

    if len(ing1) != len(ing2):
        disagreements.append(
            RecipeWarning(
                code="MODEL_DISAGREEMENT",
                message=(
                    f"Ingredient count disagreement: reading 1 has {len(ing1)} items, "
                    f"reading 2 has {len(ing2)} items"
                ),
                field_path="sections.ingredients",
            )
        )

    min_ing = min(len(ing1), len(ing2))
    for idx in range(min_ing):
        line1 = ing1[idx]
        line2 = ing2[idx]

        # Check quantity
        q1 = (line1.quantity or "").strip()
        q2 = (line2.quantity or "").strip()
        if q1 and q2 and q1 != q2:
            disagreements.append(
                RecipeWarning(
                    code="MODEL_DISAGREEMENT",
                    message=(
                        f"Ingredient quantity disagreement at item {idx + 1}: "
                        f"reading 1 has '{q1}', reading 2 has '{q2}' "
                        f"(item: '{line1.display_text}' vs '{line2.display_text}')"
                    ),
                    field_path=f"sections.ingredients[{idx}].quantity",
                )
            )

        # Check unit
        u1 = _normalize_unit(line1.unit)
        u2 = _normalize_unit(line2.unit)
        if u1 and u2 and u1 != u2:
            disagreements.append(
                RecipeWarning(
                    code="MODEL_DISAGREEMENT",
                    message=(
                        f"Ingredient unit disagreement at item {idx + 1}: "
                        f"reading 1 has '{line1.unit}', reading 2 has '{line2.unit}'"
                    ),
                    field_path=f"sections.ingredients[{idx}].unit",
                )
            )

        # Check overall line text
        d1 = line1.display_text.strip()
        d2 = line2.display_text.strip()
        line_cer = levenshtein_distance(d1.lower(), d2.lower()) / max(len(d1), len(d2), 1)
        if line_cer > 0.25:
            disagreements.append(
                RecipeWarning(
                    code="MODEL_DISAGREEMENT",
                    message=(
                        f"Ingredient line disagreement at item {idx + 1}: "
                        f"reading 1 has '{d1}', reading 2 has '{d2}'"
                    ),
                    field_path=f"sections.ingredients[{idx}]",
                )
            )

    if len(ing2) > len(ing1):
        for idx in range(len(ing1), len(ing2)):
            disagreements.append(
                RecipeWarning(
                    code="MODEL_DISAGREEMENT",
                    message=f"Reading 2 contains additional ingredient: '{ing2[idx].display_text}'",
                    field_path=f"sections.ingredients[{idx}]",
                )
            )

    # 3. Steps comparison
    st1 = reading1.all_steps()
    st2 = reading2.all_steps()

    if len(st1) != len(st2):
        disagreements.append(
            RecipeWarning(
                code="MODEL_DISAGREEMENT",
                message=(
                    f"Step count disagreement: reading 1 has {len(st1)} steps, "
                    f"reading 2 has {len(st2)} steps"
                ),
                field_path="sections.steps",
            )
        )

    min_steps = min(len(st1), len(st2))
    for idx in range(min_steps):
        step1 = st1[idx]
        step2 = st2[idx]
        s_d1 = step1.display_text.strip()
        s_d2 = step2.display_text.strip()
        step_cer = levenshtein_distance(s_d1.lower(), s_d2.lower()) / max(len(s_d1), len(s_d2), 1)
        if step_cer > 0.25:
            disagreements.append(
                RecipeWarning(
                    code="MODEL_DISAGREEMENT",
                    message=(
                        f"Step disagreement at step {idx + 1}: "
                        f"reading 1 has '{s_d1}', reading 2 has '{s_d2}'"
                    ),
                    field_path=f"sections.steps[{idx}]",
                )
            )

    if len(st2) > len(st1):
        for idx in range(len(st1), len(st2)):
            disagreements.append(
                RecipeWarning(
                    code="MODEL_DISAGREEMENT",
                    message=f"Reading 2 contains additional step: '{st2[idx].display_text}'",
                    field_path=f"sections.steps[{idx}]",
                )
            )

    return disagreements


async def self_consistency(
    recipe: Recipe,
    transcript: Transcript,
    faces: Any,
    llm_client: LLMClient,
    settings: Any,
) -> Tuple[Recipe, List[RecipeWarning]]:
    """Performs independent second read and computes field diffs for HITL review.

    Args:
        recipe: Recipe from primary structuring pass (reading 1).
        transcript: Verbatim card transcripts.
        faces: Card face images/paths.
        llm_client: LLMClient instance.
        settings: Application settings.

    Returns:
        Tuple of (Recipe, list of MODEL_DISAGREEMENT warnings).
    """
    profile_params = settings.profile_params() if hasattr(settings, "profile_params") else {}
    sc_mode = str(profile_params.get("self_consistency", "off")).lower()

    if sc_mode == "off":
        return recipe, []

    # Check whether consistency check should run (flagged mode vs always)
    is_flagged = bool(
        (hasattr(recipe, "needs_review") and recipe.needs_review())
        or bool(recipe.warnings)
        or (recipe.confidence is not None and recipe.confidence < 0.80)
        or getattr(settings, "force_self_consistency", False)
    )

    if sc_mode in ("flagged", "flagged_other_model") and not is_flagged:
        return recipe, []

    # Configure second independent read
    primary_model = settings.get_stage_model(LLMStage.STRUCTURE)
    temperature = 0.0

    if sc_mode in ("flagged_other_model", "other_model"):
        # Select an alternative model
        if "flash" in primary_model.lower():
            second_model = (
                getattr(settings, "judge_model", None)
                or settings.get_stage_model("JUDGE")
                or "gemini-3.7-pro"
            )
        else:
            second_model = "gemini-3.7-flash"
    else:
        # Same model, higher/different temperature
        second_model = primary_model
        temperature = 0.7

    # Build prompt
    prompt_record = get_prompt("consistency_v1")

    transcript_parts = []
    for face in transcript.faces:
        transcript_parts.append(f"--- CARD FACE {face.face_index} ---\n{face.text.strip()}")
    transcript_ctx = "\n\n".join(transcript_parts)

    user_text = (
        f"CARD TRANSCRIPTS:\n{transcript_ctx}\n\n"
        f"Please perform an independent, unbiased structuring of this recipe into the Recipe schema v2."
    )

    try:
        face_parts = _prepare_face_parts(faces)
    except ExtractionUnavailable as unreadable_err:
        logger.warning("Self-consistency skipped due to unreadable face: %s", unreadable_err)
        return recipe, [
            RecipeWarning(
                code="VERIFICATION_SKIPPED",
                message=f"Self-consistency skipped: {unreadable_err}",
                field_path="consistency",
            )
        ]

    parts: List[Union[TextPart, ImagePart]] = [TextPart(text=user_text)]
    parts.extend(face_parts)

    request = LLMRequest(
        stage=LLMStage.STRUCTURE,
        model=second_model,
        system_instruction=prompt_record.text,
        parts=parts,
        response_model=Recipe,
        temperature=temperature,
        metadata={"consistency_mode": sc_mode, "secondary_read": True},
    )

    try:
        response = await llm_client.generate(request)
    except BudgetExceeded:
        raise
    except Exception as exc:
        logger.warning("Self-consistency second read failed: %s", exc)
        return recipe, []

    second_recipe: Optional[Recipe] = None
    if response.parsed and isinstance(response.parsed, Recipe):
        second_recipe = response.parsed
    elif response.parsed and isinstance(response.parsed, dict):
        try:
            second_recipe = Recipe.model_validate(response.parsed)
        except Exception:
            pass
    elif response.raw_text:
        try:
            second_recipe = Recipe.model_validate(json.loads(response.raw_text))
        except Exception:
            pass

    if second_recipe is None:
        logger.warning("Could not parse valid second recipe for self-consistency")
        return recipe, []

    # Compute field-level diff
    disagreements = diff_recipes(recipe, second_recipe)

    if not disagreements:
        # Agreement raises confidence
        current_conf = recipe.confidence if recipe.confidence is not None else 0.85
        recipe.confidence = min(1.0, round(current_conf + 0.10, 2))
        logger.info("Self-consistency readings agree; boosted confidence to %.2f", recipe.confidence)
    else:
        # Disagreements found: attach warnings with both readings; never silently pick
        recipe.warnings.extend(disagreements)
        logger.info(
            "Self-consistency generated %d MODEL_DISAGREEMENT warning(s)",
            len(disagreements),
        )

    return recipe, disagreements


__all__ = [
    "diff_recipes",
    "self_consistency",
]
