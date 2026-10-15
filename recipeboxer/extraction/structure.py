"""Recipe structuring pass (WP-XA, X5).

Transforms verbatim multi-face transcripts and card images into validated Recipe Schema v2.
Adheres strictly to WAVE 5 Contracts §2 and REMEDIATION_PLAN.md §8.3:
- Transcript + images -> schema v2 Recipe via structured LLM generation
- Never invent quantities (missing amounts remain null)
- original_text verbatim from transcript
- Accurate source_face attribution per line and step
- Legibility classification and illegible_spans populated from transcript markers
- Multi-face section and instruction continuity
- Advertisement, clipping, and extraneous noise routed to notes
- title_is_inferred accurately set when no visible title exists on the card
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Union

from recipeboxer.ai.client import (
    ImagePart,
    LLMClient,
    LLMRequest,
    LLMStage,
    TextPart,
    _extract_json_text,
)
from recipeboxer.config import Settings, get_settings
from recipeboxer.extraction.image_prep import PreparedFace
from recipeboxer.extraction.transcribe import Transcript
from recipeboxer.models.failures import (
    BudgetExceeded,
    ExtractionUnavailable,
    FailureReason,
    StageUnavailableError,
)
from recipeboxer.models.recipe import (
    IllegibleSpan,
    IngredientLine,
    Legibility,
    Recipe,
    RecipeSection,
    RecipeStructureResponse,
    RecipeWarning,
    Step,
    TextSpan,
)
from recipeboxer.prompts import PromptRecord, get_prompt

logger = logging.getLogger(__name__)

PROMPT_STRUCTURE = "structure_v1"


def _format_transcript_text(transcript: Transcript) -> str:
    """Formats multi-face transcript into structured text for structuring prompt."""
    blocks: List[str] = []
    for face in transcript.faces:
        face_num = face.face_index + 1
        lines = [f"=== Face {face_num} (index {face.face_index}) ==="]
        lines.append("--- Verbatim Card Text ---")
        lines.append(face.text or "(no text)")

        if face.margin_notes:
            lines.append("--- Margin Notes & Annotations ---")
            for note in face.margin_notes:
                lines.append(f"- {note}")

        if face.illegible_spans:
            lines.append("--- Illegible / Damaged Spans ---")
            for span in face.illegible_spans:
                guess = f" (guess: '{span.text_guess}')" if span.text_guess else ""
                lines.append(f"- reason: {span.reason}{guess}")

        if face.crossed_out:
            lines.append("--- Crossed-Out Text ---")
            for co in face.crossed_out:
                lines.append(f"- {co}")

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def _postprocess_recipe(
    recipe: Recipe,
    transcript: Transcript,
    n_faces: int,
) -> Recipe:
    """Applies strict schema invariant checks and post-processing."""
    max_face_idx = max(0, n_faces - 1)

    # 1. Title inference inspection
    title_str = recipe.title.strip()
    if "[GUESS]" in title_str or "[inferred]" in title_str.lower():
        title_str = re.sub(r"\[(GUESS|inferred)\]", "", title_str, flags=re.IGNORECASE).strip()
        recipe.title = title_str
        recipe.title_is_inferred = True

    if recipe.title_is_inferred:
        if not any(w.code == "INFERRED_TITLE" for w in recipe.warnings):
            recipe.warnings.append(
                RecipeWarning(
                    code="INFERRED_TITLE",
                    message="Recipe title was inferred from context as no explicit title was visible on the card.",
                    field_path="title",
                )
            )

    # 2. Never invent quantities & source face bounds & legibility markers
    has_illegible = False

    for section in recipe.sections:
        for ing in section.ingredients:
            # Nullify fabricated string placeholders for quantity
            if ing.quantity is not None:
                q_clean = str(ing.quantity).strip().lower()
                if q_clean in ("", "null", "none", "n/a", "to taste", "pinch", "dash"):
                    ing.quantity = None

            # Bound source_face
            if ing.source_face is None or ing.source_face < 0 or ing.source_face > max_face_idx:
                ing.source_face = 0

            # Marker inspection on original_text
            orig_lower = ing.original_text.lower()
            if "[illegible" in orig_lower:
                has_illegible = True
                if ing.legibility == Legibility.CLEAR:
                    ing.legibility = Legibility.UNCERTAIN
                if not ing.illegible_spans:
                    for m in re.finditer(r"\[illegible:\s*([^\]]+)\]", ing.original_text, re.IGNORECASE):
                        reason_raw = m.group(1).strip().lower()
                        reason = "ambiguous"
                        for known in ("faded", "torn", "stain", "crossed_out", "ambiguous"):
                            if known in reason_raw:
                                reason = known
                                break
                        ing.illegible_spans.append(IllegibleSpan(reason=reason))

            if ing.legibility in (Legibility.UNCERTAIN, Legibility.ILLEGIBLE):
                has_illegible = True

        for step in section.steps:
            # Bound source_face
            if step.source_face is None or step.source_face < 0 or step.source_face > max_face_idx:
                step.source_face = 0

            # Marker inspection on step original_text
            orig_lower = step.original_text.lower()
            if "[illegible" in orig_lower:
                has_illegible = True
                if step.legibility == Legibility.CLEAR:
                    step.legibility = Legibility.UNCERTAIN
                if not step.illegible_spans:
                    for m in re.finditer(r"\[illegible:\s*([^\]]+)\]", step.original_text, re.IGNORECASE):
                        reason_raw = m.group(1).strip().lower()
                        reason = "ambiguous"
                        for known in ("faded", "torn", "stain", "crossed_out", "ambiguous"):
                            if known in reason_raw:
                                reason = known
                                break
                        step.illegible_spans.append(IllegibleSpan(reason=reason))

            if step.legibility in (Legibility.UNCERTAIN, Legibility.ILLEGIBLE):
                has_illegible = True

    # 3. Add ILLEGIBLE_CONTENT warning if illegibility detected
    if has_illegible and not any(w.code == "ILLEGIBLE_CONTENT" for w in recipe.warnings):
        recipe.warnings.append(
            RecipeWarning(
                code="ILLEGIBLE_CONTENT",
                message="Recipe text contains illegible, faded, or torn segments flagged during transcription.",
                field_path="sections",
            )
        )

    # 4. Integrate transcript margin notes into notes if omitted
    existing_note_texts = {n.text.strip() for n in recipe.notes if n.text}
    for face in transcript.faces:
        for mn in face.margin_notes:
            mn_clean = mn.strip()
            if mn_clean and mn_clean not in existing_note_texts:
                recipe.notes.append(
                    TextSpan(
                        text=mn_clean,
                        kind="margin_note",
                        source_face=face.face_index,
                        confidence=0.9,
                        legibility=Legibility.CLEAR,
                    )
                )
                existing_note_texts.add(mn_clean)

    return recipe


async def structure(
    transcript: Transcript,
    faces: list[PreparedFace],
    llm_client: LLMClient,
    settings: Settings,
) -> Recipe:
    """Structures recipe transcript and card faces into Recipe Schema v2.

    Args:
        transcript: Verbatim multi-face transcript produced by transcribe().
        faces: Ordered list of PreparedFace instances.
        llm_client: Production or test LLMClient instance.
        settings: Application Settings.

    Returns:
        Structured Recipe v2 model with section continuity, verbatim original_text,
        and proper legibility and title inference flags.

    Raises:
        BudgetExceeded: If budget cap is reached.
        ExtractionUnavailable: If LLM call or schema validation fails.
    """
    if not transcript or not transcript.faces:
        raise ExtractionUnavailable(
            "No transcript faces provided for recipe structuring.",
            reason=FailureReason.INVALID_RESPONSE,
            retryable=False,
        )

    # 1. Load structure prompt template
    try:
        prompt_record: PromptRecord = get_prompt(PROMPT_STRUCTURE)
    except KeyError:
        raise ExtractionUnavailable(
            f"Structure prompt template '{PROMPT_STRUCTURE}' not found in registry.",
            reason=FailureReason.INVALID_RESPONSE,
            retryable=False,
        )

    model = settings.get_stage_model(LLMStage.STRUCTURE)
    timeout_s = settings.get_stage_timeout(LLMStage.STRUCTURE)

    # 2. Build parts: formatted transcripts + card face images with neutral labels
    transcript_text = _format_transcript_text(transcript)

    # Scale timeout and max_output_tokens dynamically for long transcripts (WP-SCALE Task 5)
    timeout_s = max(timeout_s, 60.0 + (len(transcript_text) // 500) * 15.0 + len(faces) * 15.0)
    profile_params = settings.profile_params()
    max_output_tokens = profile_params.get("max_output_tokens", 8192)

    parts: List[Union[TextPart, ImagePart]] = [
        TextPart(
            text=(
                "Structure the following recipe card transcript into Recipe Schema v2. "
                "Adhere to all schema constraints: never invent quantities (missing amounts must be null), "
                "preserve verbatim original_text, set accurate source_face indices, "
                "isolate clipping and margin noise into notes, and set title_is_inferred accurately.\n\n"
                f"{transcript_text}"
            )
        )
    ]

    for face in faces:
        parts.append(
            ImagePart(
                data=face.data,
                mime_type=face.mime_type,
                label=face.label,
            )
        )

    # 3. Formulate request
    request = LLMRequest(
        stage=LLMStage.STRUCTURE,
        model=model,
        system_instruction=prompt_record.text,
        parts=parts,
        response_model=RecipeStructureResponse,
        temperature=0.0,
        max_output_tokens=max_output_tokens,
        timeout_s=timeout_s,
        metadata={
            "n_faces": len(faces),
            "transcript_model": transcript.model,
            "transcript_prompt_id": transcript.prompt_id,
        },
    )

    # 4. Execute generation with error handling and repair loop on parse errors
    response: Optional[LLMResponse] = None
    try:
        # WP-UP4: one extra stage-level retry (with backoff) on an empty/blank model response.
        from recipeboxer.extraction.repair import generate_with_empty_retry

        response = await generate_with_empty_retry(llm_client, request)
    except BudgetExceeded:
        # BudgetExceeded must ALWAYS propagate without swallowing
        raise
    except StageUnavailableError as exc:
        # Check if error contains raw text that can be repaired (WP-SCALE Task 5)
        raw_text_payload = exc.details.get("raw_text") if exc.details else None
        if raw_text_payload and exc.reason in (FailureReason.INVALID_RESPONSE, FailureReason.SCHEMA_VALIDATION):
            try:
                from recipeboxer.extraction.repair import validate_and_repair
                logger.info("Attempting repair loop on invalid structure response: %s", exc)
                repaired = await validate_and_repair(raw_text_payload, llm_client, settings)
                return _postprocess_recipe(repaired, transcript, len(faces))
            except Exception as repair_exc:
                logger.warning("Repair loop failed after parse error: %s", repair_exc)
        if isinstance(exc, ExtractionUnavailable):
            raise
        raise ExtractionUnavailable(
            str(exc),
            reason=exc.reason,
            card_ids=exc.card_ids,
            retryable=exc.retryable,
            details=exc.details,
        ) from exc
    except Exception as exc:
        reason = (
            FailureReason.TIMEOUT
            if isinstance(exc, (asyncio.TimeoutError, TimeoutError))
            or "timeout" in str(exc).lower()
            else FailureReason.MODEL_UNAVAILABLE
        )
        raise ExtractionUnavailable(
            f"Structuring call failed: {exc}",
            reason=reason,
            details={"error": str(exc)},
        ) from exc

    # 5. Extract and validate Recipe instance
    recipe: Optional[Recipe] = None
    if response is not None and response.parsed is not None:
        if isinstance(response.parsed, RecipeStructureResponse):
            recipe = response.parsed.to_recipe()
        elif isinstance(response.parsed, Recipe):
            recipe = response.parsed
        elif isinstance(response.parsed, dict):
            try:
                recipe = RecipeStructureResponse.model_validate(response.parsed).to_recipe()
            except Exception:
                recipe = Recipe.model_validate(response.parsed)
    elif response is not None and response.raw_text:
        try:
            from recipeboxer.extraction.repair import validate_and_repair
            repaired = await validate_and_repair(response.raw_text, llm_client, settings)
            recipe = repaired
        except Exception as parse_err:
            raise ExtractionUnavailable(
                f"Failed to parse structured recipe schema: {parse_err}",
                reason=FailureReason.SCHEMA_VALIDATION,
                details={"raw_text": response.raw_text[:500]},
            ) from parse_err

    if recipe is None:
        raise ExtractionUnavailable(
            "Structuring pass did not yield a valid Recipe model.",
            reason=FailureReason.SCHEMA_VALIDATION,
        )

    # 6. Postprocess and enforce schema invariants
    return _postprocess_recipe(recipe, transcript, len(faces))


__all__ = [
    "structure",
    "PROMPT_STRUCTURE",
]
