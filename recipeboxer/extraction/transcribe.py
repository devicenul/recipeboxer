"""Verbatim recipe card transcription pass (WP-XA, X4).

Transcribes all prepared card faces in a single multimodal LLM call using
modality-routed prompt templates loaded from the prompt registry.
Adheres strictly to WAVE 5 Contracts §2 and REMEDIATION_PLAN.md §8.3:
- One call per recipe cluster with all prepared card faces
- Modality routing (handwritten, typed, clipping, mixed)
- Strict verbatim transcription per face (never correcting spelling or grammar)
- Preservation of [illegible: reason] and [crossed out: ...] markers
- Extraction of margin notes and physical damage spans
- Adaptive thinking budget for difficult handwriting and degraded/enhanced faces
- Typed failure handling (ExtractionUnavailable, BudgetExceeded propagation)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Union

from pydantic import BaseModel, ConfigDict, Field

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
from recipeboxer.models.failures import (
    BudgetExceeded,
    ExtractionUnavailable,
    FailureReason,
    StageUnavailableError,
)
from recipeboxer.models.recipe import IllegibleSpan, Legibility
from recipeboxer.prompts import PromptRecord, get_prompt

logger = logging.getLogger(__name__)

PROMPT_HANDWRITTEN = "transcribe_handwritten_v1"
PROMPT_TYPED = "transcribe_typed_v1"
PROMPT_CLIPPING = "transcribe_clipping_v1"
PROMPT_MIXED = "transcribe_mixed_v1"


class FaceTranscript(BaseModel):
    """Verbatim transcript of a single physical card face."""

    model_config = ConfigDict(extra="ignore")

    face_index: int = Field(..., ge=0, description="0-indexed face index (0 to N-1)")
    text: str = Field(..., description="Verbatim transcribed text of the card face")
    illegible_spans: List[IllegibleSpan] = Field(
        default_factory=list, description="Illegible text spans and damage reasons"
    )
    margin_notes: List[str] = Field(
        default_factory=list, description="Handwritten margin notes, dates, or stories"
    )
    crossed_out: List[str] = Field(
        default_factory=list, description="Crossed-out or struck-through words"
    )
    confidence: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="Transcription confidence score 0.0 to 1.0"
    )


class FaceTranscriptList(BaseModel):
    """Structured container for multi-face transcription response."""

    model_config = ConfigDict(extra="ignore")

    faces: List[FaceTranscript] = Field(
        default_factory=list, description="List of per-face transcripts in reading order"
    )


class Transcript(BaseModel):
    """Complete multi-face recipe transcript with provenance."""

    model_config = ConfigDict(extra="ignore")

    faces: List[FaceTranscript] = Field(
        ..., description="Ordered list of per-face transcripts"
    )
    model: str = Field(..., description="Model identifier used for transcription")
    prompt_id: str = Field(..., description="Prompt template identifier")
    prompt_version: str = Field(..., description="Deterministic prompt version hash")

    @property
    def full_text(self) -> str:
        """Concatenated transcript text across all faces."""
        return "\n\n".join(f.text for f in self.faces if f.text)


def resolve_transcribe_prompt_id(
    modality_hint: Optional[str] = None,
    faces: Optional[Sequence[PreparedFace]] = None,
) -> str:
    """Selects the prompt registry ID based on modality hint and face characteristics."""
    if modality_hint:
        raw = str(modality_hint).strip().lower()
        if any(k in raw for k in ("clipping", "newspaper", "newsprint", "magazine", "label", "package")):
            return PROMPT_CLIPPING
        if any(k in raw for k in ("type", "monospace", "ribbon")):
            return PROMPT_TYPED
        if any(k in raw for k in ("mixed", "stain", "damaged", "hybrid")):
            return PROMPT_MIXED
        if any(k in raw for k in ("hand", "cursive", "script", "pen", "pencil")):
            return PROMPT_HANDWRITTEN

    # If any prepared face has enhanced CLAHE variant (indicating faded/damaged card)
    if faces and any(getattr(f, "is_enhanced", False) for f in faces):
        return PROMPT_MIXED

    # Default to handwritten (primary archetype of family recipe cards)
    return PROMPT_HANDWRITTEN


def compute_transcribe_thinking_budget(
    settings: Settings,
    prompt_id: str,
    faces: Sequence[PreparedFace],
) -> Optional[int]:
    """Computes dynamic thinking budget based on quality profile and card properties."""
    # Check if explicit setting override is present
    if settings.thinking_budget_tokens is not None and settings.thinking_mode:
        return settings.thinking_budget_tokens

    params = settings.profile_params()
    configured_budget = params.get("thinking_budget_transcribe", 0)

    if configured_budget > 0:
        return int(configured_budget)

    if configured_budget == -1:
        # Dynamic / adaptive mode (e.g. Balanced profile)
        # Allocate thinking tokens for handwriting, mixed media, or contrast-enhanced faces
        is_difficult = (
            prompt_id in (PROMPT_HANDWRITTEN, PROMPT_MIXED)
            or any(getattr(f, "is_enhanced", False) for f in faces)
        )
        if is_difficult:
            return 1024
        return None

    return None


def _extract_markers_from_text(
    text: str,
    illegible_spans: List[IllegibleSpan],
    crossed_out: List[str],
) -> None:
    """Extracts [illegible: reason] and [crossed out: text] markers into structured lists if empty."""
    if not illegible_spans:
        for match in re.finditer(r"\[illegible:\s*([^\]]+)\]", text, re.IGNORECASE):
            reason_raw = match.group(1).strip().lower()
            reason = "ambiguous"
            for known in ("faded", "torn", "stain", "crossed_out", "ambiguous"):
                if known in reason_raw:
                    reason = known
                    break
            illegible_spans.append(IllegibleSpan(reason=reason))

    if not crossed_out:
        for match in re.finditer(r"\[crossed out:\s*([^\]]+)\]", text, re.IGNORECASE):
            crossed_out.append(match.group(1).strip())


async def transcribe(
    faces: list[PreparedFace],
    llm_client: LLMClient,
    settings: Settings,
    modality_hint: Optional[str] = None,
) -> Transcript:
    """Transcribes an ordered list of prepared card faces into a Transcript.

    Args:
        faces: Ordered list of PreparedFace instances for the recipe.
        llm_client: Production or test LLMClient instance.
        settings: Application Settings containing profile and model configurations.
        modality_hint: Optional modality classification hint (from fingerprint or router).

    Returns:
        Transcript containing per-face transcripts and prompt/model provenance.

    Raises:
        BudgetExceeded: If budget cap is reached (must propagate immediately).
        ExtractionUnavailable: If LLM call fails, input is invalid, or parsing fails.
    """
    if not faces:
        raise ExtractionUnavailable(
            "No prepared card faces provided for transcription.",
            reason=FailureReason.INVALID_RESPONSE,
            retryable=False,
        )

    # 1. Resolve modality prompt template from registry
    prompt_id = resolve_transcribe_prompt_id(modality_hint=modality_hint, faces=faces)
    try:
        prompt_record: PromptRecord = get_prompt(prompt_id)
    except KeyError:
        # Fallback to handwritten if specific prompt not found
        prompt_record = get_prompt(PROMPT_HANDWRITTEN)
        prompt_id = prompt_record.id

    # 2. Determine model and thinking budget
    model = settings.get_stage_model(LLMStage.TRANSCRIBE)
    thinking_budget = compute_transcribe_thinking_budget(settings, prompt_id, faces)
    timeout_s = settings.get_stage_timeout(LLMStage.TRANSCRIBE)
    # Dynamic scaling for thinking tokens and multiple faces (WP-SCALE Task 5)
    timeout_s = max(timeout_s, 90.0 + (thinking_budget or 0) * 0.05 + len(faces) * 20.0)
    max_output_tokens = max(8192, (thinking_budget or 0) + 4096)

    # 3. Build multimodal LLM parts with neutral labels
    parts: List[Union[TextPart, ImagePart]] = [
        TextPart(
            text=(
                f"Transcribe each of the {len(faces)} recipe card faces strictly verbatim in order. "
                "Preserve all original line breaks, archaic spellings, illegible markers [illegible: reason], "
                "and crossed out markers [crossed out: text]."
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

    # 4. Formulate request
    request = LLMRequest(
        stage=LLMStage.TRANSCRIBE,
        model=model,
        system_instruction=prompt_record.text,
        parts=parts,
        response_model=FaceTranscriptList,
        temperature=0.1,
        thinking_budget=thinking_budget,
        max_output_tokens=max_output_tokens,
        timeout_s=timeout_s,
        metadata={"n_faces": len(faces), "prompt_id": prompt_id},
    )

    # 5. Execute generation with error handling
    try:
        # WP-UP4: one extra stage-level retry (with backoff) on an empty/blank model response.
        from recipeboxer.extraction.repair import generate_with_empty_retry

        response = await generate_with_empty_retry(llm_client, request)
    except BudgetExceeded:
        # BudgetExceeded must ALWAYS propagate without swallowing
        raise
    except StageUnavailableError as exc:
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
            f"Transcription call failed: {exc}",
            reason=reason,
            details={"error": str(exc)},
        ) from exc

    # 6. Parse structured response
    raw_faces: List[FaceTranscript] = []
    embedded_recipe = None
    if response.parsed is not None:
        if isinstance(response.parsed, FaceTranscriptList):
            raw_faces = response.parsed.faces
        elif isinstance(response.parsed, Transcript):
            raw_faces = response.parsed.faces
        elif isinstance(response.parsed, list):
            raw_faces = [
                f if isinstance(f, FaceTranscript) else FaceTranscript.model_validate(f)
                for f in response.parsed
            ]
        elif isinstance(response.parsed, FaceTranscript):
            raw_faces = [response.parsed]
        elif hasattr(response.parsed, "title") and hasattr(response.parsed, "ingredients"):
            from recipeboxer.models.recipe import RecipeSchema
            embedded_recipe = (
                response.parsed
                if isinstance(response.parsed, RecipeSchema)
                else RecipeSchema.model_validate(
                    response.parsed.model_dump() if hasattr(response.parsed, "model_dump") else response.parsed
                )
            )
            text = f"{embedded_recipe.title}\n" + "\n".join(embedded_recipe.ingredients or []) + "\n" + "\n".join(embedded_recipe.directions or [])
            raw_faces = [FaceTranscript(face_index=0, text=text)]
    elif response.raw_text:
        try:
            json_text = _extract_json_text(response.raw_text)
            parsed_data = json.loads(json_text)
            if isinstance(parsed_data, dict) and "faces" in parsed_data:
                raw_faces = [FaceTranscript.model_validate(f) for f in parsed_data["faces"]]
            elif isinstance(parsed_data, list):
                raw_faces = [FaceTranscript.model_validate(f) for f in parsed_data]
            elif isinstance(parsed_data, dict):
                raw_faces = [FaceTranscript.model_validate(parsed_data)]
        except Exception as parse_err:
            raise ExtractionUnavailable(
                f"Failed to parse structured transcript response: {parse_err}",
                reason=FailureReason.SCHEMA_VALIDATION,
                details={"raw_text": response.raw_text[:500]},
            ) from parse_err

    if not raw_faces:
        raise ExtractionUnavailable(
            "Model returned empty face transcripts.",
            reason=FailureReason.INVALID_RESPONSE,
        )

    # 7. Normalize face indexes and extract fallback markers
    normalized_faces: List[FaceTranscript] = []
    for idx, face_item in enumerate(raw_faces):
        # Ensure face_index matches sequence position if unset or invalid
        effective_idx = face_item.face_index if face_item.face_index is not None and face_item.face_index >= 0 else idx
        _extract_markers_from_text(face_item.text, face_item.illegible_spans, face_item.crossed_out)
        normalized_faces.append(
            FaceTranscript(
                face_index=effective_idx,
                text=face_item.text,
                illegible_spans=face_item.illegible_spans,
                margin_notes=face_item.margin_notes,
                crossed_out=face_item.crossed_out,
                confidence=face_item.confidence,
            )
        )

    # Ensure sorted by face_index
    normalized_faces.sort(key=lambda f: f.face_index)

    transcript_obj = Transcript(
        faces=normalized_faces,
        model=response.model or model,
        prompt_id=prompt_record.id,
        prompt_version=prompt_record.version,
    )
    if embedded_recipe is not None:
        object.__setattr__(transcript_obj, "_embedded_recipe", embedded_recipe)
    return transcript_obj


__all__ = [
    "FaceTranscript",
    "FaceTranscriptList",
    "Transcript",
    "resolve_transcribe_prompt_id",
    "compute_transcribe_thinking_budget",
    "transcribe",
    "PROMPT_HANDWRITTEN",
    "PROMPT_TYPED",
    "PROMPT_CLIPPING",
    "PROMPT_MIXED",
]
