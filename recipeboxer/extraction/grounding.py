"""Grounding verification pass for RecipeBoxer extraction pipeline (WP-XB, X6).

Verifies that structured recipe components (ingredients and instruction steps)
are factually grounded in the physical card transcripts and local OCR evidence.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from pydantic import BaseModel, Field

from recipeboxer.ai.client import LLMClient, LLMRequest, LLMStage, TextPart
from recipeboxer.models.failures import BudgetExceeded, ExtractionUnavailable, FailureReason, StageUnavailableError
from recipeboxer.models.recipe import IngredientLine, Recipe, RecipeSection, RecipeWarning, Step
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

# Grounding threshold specified by contract: normalized CER <= 0.25
MAX_GROUNDING_CER = 0.25


def levenshtein_distance(s1: str, s2: str) -> int:
    """Computes exact Levenshtein edit distance between two strings."""
    if s1 == s2:
        return 0
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)
    if len(s1) < len(s2):
        s1, s2 = s2, s1
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1] * (len(s2) + 1)
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            curr[j + 1] = min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost)
        prev = curr
    return prev[len(s2)]


def semi_global_cer(query: str, target: str) -> float:
    """Computes minimum normalized CER of query against any substring of target.

    Uses semi-global dynamic programming (free start anywhere in target).
    Normalized by len(query).
    """
    q = " ".join(query.strip().split()).lower()
    t = " ".join(target.strip().split()).lower()
    if not q:
        return 0.0
    if not t:
        return 1.0
    if q in t:
        return 0.0

    len_q = len(q)
    len_t = len(t)
    prev = [0] * (len_t + 1)
    for i, c_q in enumerate(q):
        curr = [i + 1] * (len_t + 1)
        for j, c_t in enumerate(t):
            cost = 0 if c_q == c_t else 1
            curr[j + 1] = min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost)
        prev = curr
    return min(prev) / max(len_q, 1)


def local_window_cer(query: str, face_text: str) -> float:
    """Finds best alignment of query within face text using line and substring windows."""
    q_norm = " ".join(query.strip().split()).lower()
    if not q_norm:
        return 0.0

    # 1. Exact substring check
    f_norm = " ".join(face_text.strip().split()).lower()
    if q_norm in f_norm:
        return 0.0

    # 2. Line-by-line and 2-line window checks
    best_cer = 1.0
    lines = [ln.strip() for ln in face_text.splitlines() if ln.strip()]
    for i, line in enumerate(lines):
        ln_norm = " ".join(line.split()).lower()
        if not ln_norm:
            continue
        if q_norm in ln_norm or ln_norm in q_norm:
            # Check length-normalized ratio
            dist = abs(len(q_norm) - len(ln_norm))
            cer = dist / max(len(q_norm), len(ln_norm))
            if cer < best_cer:
                best_cer = cer
        else:
            dist = levenshtein_distance(q_norm, ln_norm)
            cer = dist / max(len(q_norm), 1)
            if cer < best_cer:
                best_cer = cer

        # Pair with next line for wrapped lines
        if i + 1 < len(lines):
            pair_norm = f"{ln_norm} {' '.join(lines[i+1].split()).lower()}"
            if q_norm in pair_norm:
                best_cer = min(best_cer, 0.0)
            else:
                dist = levenshtein_distance(q_norm, pair_norm)
                cer = dist / max(len(q_norm), 1)
                if cer < best_cer:
                    best_cer = cer

        if best_cer <= MAX_GROUNDING_CER:
            return best_cer

    # 3. Semi-global alignment over full face text
    dp_cer = semi_global_cer(q_norm, f_norm)
    return min(best_cer, dp_cer)


class LineGroundingCheck(BaseModel):
    """Grounding check result for a single candidate line."""

    line_id: str
    supported: bool
    quote: Optional[str] = None
    reason: Optional[str] = None


class GroundingBatchResponse(BaseModel):
    """Batched grounding verification response from LLM."""

    checks: List[LineGroundingCheck] = Field(default_factory=list)


def _extract_number_and_unit(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Helper to extract quantity and unit tokens from a line of text."""
    # Match numbers like 1, 1 1/2, 1/2, 2.5
    num_match = re.search(r"\b(\d+(?:\s+\d+/\d+|\.\d+|/\d+)?)\b", text)
    num = num_match.group(1).strip() if num_match else None

    # Match common cooking units
    unit_match = re.search(
        r"\b(cups?|c\.|tbsp\.?|tablespoons?|tsp\.?|teaspoons?|oz\.?|ounces?|lbs?\.?|pounds?|grams?|g|ml|pinch|dash|cloves?|slices?|cans?)\b",
        text,
        re.IGNORECASE,
    )
    unit = unit_match.group(1).lower().rstrip(".") if unit_match else None
    return num, unit


def _normalize_unit(unit: Optional[str]) -> Optional[str]:
    """Normalizes abbreviations to canonical unit names."""
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


def _normalize_number(num: Optional[str]) -> Optional[float]:
    """Converts fraction or decimal string to float for comparison."""
    if not num:
        return None
    try:
        parts = num.strip().split()
        total = 0.0
        for p in parts:
            if "/" in p:
                n, d = p.split("/")
                total += float(n) / float(d)
            else:
                total += float(p)
        return total
    except Exception:
        return None


def _flatten_ocr_lines(ocr_lines: Optional[Sequence[Any]]) -> List[Any]:
    """Recursively flattens potential nested lists of OCR lines (e.g. per-face lists)."""
    if not ocr_lines:
        return []
    flat: List[Any] = []
    for item in ocr_lines:
        if isinstance(item, (list, tuple)):
            flat.extend(_flatten_ocr_lines(item))
        else:
            flat.append(item)
    return flat


async def ground(
    recipe: Recipe,
    transcript: Transcript,
    llm_client: LLMClient,
    settings: Any,
    ocr_lines: Optional[Sequence[Any]] = None,
    *,
    remove_unsupported: Optional[bool] = None,
) -> Tuple[Recipe, List[RecipeWarning]]:
    """Grounds recipe ingredients and steps against transcript and optional local OCR.

    Performs:
    1. Fuzzy-alignment (CER <= 0.25) of every line's original_text to card face transcripts.
    2. One batched LLM support check for any unmatched lines (prompts/grounding_v1.md).
    3. Flags unsupported lines with GROUNDING_UNSUPPORTED warning, or removes them if
       model says unsupported AND line is absent from OCR (when remove_unsupported=True).
    4. Numbers and units cross-checked against OCR lines (disagreement -> warning, never overwrite).

    Args:
        recipe: Structured Recipe Schema v2 to ground.
        transcript: Verbatim card face transcript from Pass 1.
        llm_client: LLMClient for model calls.
        settings: Global application settings.
        ocr_lines: Optional local OCR lines for independent cross-check.
        remove_unsupported: Whether to remove unsupported lines absent from OCR (configurable).

    Returns:
        Tuple of (Recipe, list of generated RecipeWarning objects).
    """
    warnings: List[RecipeWarning] = []
    flat_ocr_lines = _flatten_ocr_lines(ocr_lines)

    # Check configurable removal
    should_remove = (
        remove_unsupported
        if remove_unsupported is not None
        else getattr(settings, "grounding_remove_unsupported", False)
    )

    # 1. Fuzzy-align every ingredient line and step against transcript faces
    unmatched_items: List[Dict[str, Any]] = []

    for s_idx, section in enumerate(recipe.sections):
        # Ingredients
        for i_idx, ing in enumerate(section.ingredients):
            orig = ing.original_text or ing.display_text
            if not orig.strip():
                continue
            best_cer = min(
                (local_window_cer(orig, face.text) for face in transcript.faces),
                default=1.0,
            )
            if best_cer > MAX_GROUNDING_CER:
                unmatched_items.append({
                    "line_id": f"ing_{s_idx}_{i_idx}",
                    "type": "ingredient",
                    "section_idx": s_idx,
                    "item_idx": i_idx,
                    "item": ing,
                    "text": orig,
                    "field_path": f"sections[{s_idx}].ingredients[{i_idx}]",
                })

        # Steps
        for st_idx, step in enumerate(section.steps):
            orig = step.original_text or step.display_text
            if not orig.strip():
                continue
            best_cer = min(
                (local_window_cer(orig, face.text) for face in transcript.faces),
                default=1.0,
            )
            if best_cer > MAX_GROUNDING_CER:
                unmatched_items.append({
                    "line_id": f"step_{s_idx}_{st_idx}",
                    "type": "step",
                    "section_idx": s_idx,
                    "item_idx": st_idx,
                    "item": step,
                    "text": orig,
                    "field_path": f"sections[{s_idx}].steps[{st_idx}]",
                })

    # 2. One batched LLM support check for all unmatched lines
    items_to_remove: List[Tuple[int, str, int]] = []

    if unmatched_items:
        prompt_record = get_prompt("grounding_v1")

        # Build transcript context
        transcript_parts = []
        for face in transcript.faces:
            transcript_parts.append(f"--- CARD FACE {face.face_index} ---\n{face.text.strip()}")
        transcript_ctx = "\n\n".join(transcript_parts)

        # Build lines to verify list
        lines_list = "\n".join(
            f"{i+1}. [ID: {item['line_id']}] \"{item['text']}\""
            for i, item in enumerate(unmatched_items)
        )

        user_content = (
            f"CARD TRANSCRIPT:\n{transcript_ctx}\n\n"
            f"UNGROUNDED CANDIDATE LINES TO VERIFY:\n{lines_list}\n\n"
            f"Please verify each line and output a GroundingBatchResponse with checks for all candidate line IDs."
        )

        model_name = settings.get_stage_model(LLMStage.GROUNDING)
        request = LLMRequest(
            stage=LLMStage.GROUNDING,
            model=model_name,
            system_instruction=prompt_record.text,
            parts=[TextPart(text=user_content)],
            response_model=GroundingBatchResponse,
            temperature=0.0,
        )

        try:
            response = await llm_client.generate(request)
        except BudgetExceeded:
            raise
        except Exception as exc:
            logger.warning("LLM grounding check failed: %s; keeping candidate lines with warnings", exc)
            response = None

        check_map: Dict[str, LineGroundingCheck] = {}
        if response and response.parsed:
            for chk in response.parsed.checks:
                check_map[chk.line_id] = chk

        # Prepare normalized OCR text for fallback presence check
        ocr_text_joined = ""
        if flat_ocr_lines:
            ocr_text_joined = " ".join(
                line.text if hasattr(line, "text") else str(line)
                for line in flat_ocr_lines
            ).lower()

        for item in unmatched_items:
            chk = check_map.get(item["line_id"])
            is_supported = chk.supported if chk else False

            if not is_supported:
                # Check whether line text or item is present in local OCR
                item_obj = item["item"]
                query_tokens = []
                if hasattr(item_obj, "item") and item_obj.item:
                    query_tokens.append(item_obj.item.lower())
                query_tokens.append(item["text"].lower())

                present_in_ocr = bool(
                    ocr_text_joined
                    and any(tok in ocr_text_joined for tok in query_tokens if len(tok) >= 3)
                )

                if should_remove and not present_in_ocr:
                    items_to_remove.append((item["section_idx"], item["type"], item["item_idx"]))
                    warn = RecipeWarning(
                        code="GROUNDING_UNSUPPORTED",
                        message=f"Removed unsupported {item['type']} line '{item['text']}' (not found in transcript or OCR)",
                        field_path=item["field_path"],
                    )
                else:
                    quote_str = f" (quote: '{chk.quote}')" if (chk and chk.quote) else ""
                    warn = RecipeWarning(
                        code="GROUNDING_UNSUPPORTED",
                        message=f"Line '{item['text']}' is unsupported by card transcript{quote_str}",
                        field_path=item["field_path"],
                    )
                warnings.append(warn)

    # Apply removals if configured (in reverse index order)
    if items_to_remove:
        # Group removals by section and type
        for s_idx in range(len(recipe.sections)):
            ing_removals = sorted(
                [idx for (s, t, idx) in items_to_remove if s == s_idx and t == "ingredient"],
                reverse=True,
            )
            step_removals = sorted(
                [idx for (s, t, idx) in items_to_remove if s == s_idx and t == "step"],
                reverse=True,
            )
            for idx in ing_removals:
                if idx < len(recipe.sections[s_idx].ingredients):
                    recipe.sections[s_idx].ingredients.pop(idx)
            for idx in step_removals:
                if idx < len(recipe.sections[s_idx].steps):
                    recipe.sections[s_idx].steps.pop(idx)

    # 3. Cross-check numbers and units against OCR lines
    if flat_ocr_lines:
        ocr_texts = [
            line.text.strip() if hasattr(line, "text") else str(line).strip()
            for line in flat_ocr_lines
            if (line.text.strip() if hasattr(line, "text") else str(line).strip())
        ]

        for s_idx, section in enumerate(recipe.sections):
            for i_idx, ing in enumerate(section.ingredients):
                if not ing.quantity and not ing.unit:
                    continue

                ing_item_norm = ing.item.strip().lower()
                if not ing_item_norm or len(ing_item_norm) < 3:
                    continue

                # Find OCR lines that match this ingredient item
                matching_ocr_lines = [
                    ot for ot in ocr_texts if ing_item_norm in ot.lower()
                ]
                if not matching_ocr_lines:
                    continue

                best_ocr_line = matching_ocr_lines[0]
                ocr_num_raw, ocr_unit_raw = _extract_number_and_unit(best_ocr_line)

                # Cross-check quantity
                if ing.quantity and ocr_num_raw:
                    rec_qty_val = _normalize_number(ing.quantity)
                    ocr_qty_val = _normalize_number(ocr_num_raw)
                    if (
                        rec_qty_val is not None
                        and ocr_qty_val is not None
                        and abs(rec_qty_val - ocr_qty_val) > 0.05
                    ):
                        warn = RecipeWarning(
                            code="MODEL_DISAGREEMENT",
                            message=(
                                f"OCR quantity disagreement for '{ing.original_text}': "
                                f"recipe has '{ing.quantity}', OCR has '{ocr_num_raw}' in '{best_ocr_line}'"
                            ),
                            field_path=f"sections[{s_idx}].ingredients[{i_idx}].quantity",
                        )
                        warnings.append(warn)

                # Cross-check unit
                if ing.unit and ocr_unit_raw:
                    rec_unit_norm = _normalize_unit(ing.unit)
                    ocr_unit_norm = _normalize_unit(ocr_unit_raw)
                    if (
                        rec_unit_norm
                        and ocr_unit_norm
                        and rec_unit_norm != ocr_unit_norm
                    ):
                        warn = RecipeWarning(
                            code="MODEL_DISAGREEMENT",
                            message=(
                                f"OCR unit disagreement for '{ing.original_text}': "
                                f"recipe has '{ing.unit}', OCR has '{ocr_unit_raw}' in '{best_ocr_line}'"
                            ),
                            field_path=f"sections[{s_idx}].ingredients[{i_idx}].unit",
                        )
                        warnings.append(warn)

    # Attach warnings to recipe object
    recipe.warnings.extend(warnings)
    return recipe, warnings


__all__ = [
    "MAX_GROUNDING_CER",
    "LineGroundingCheck",
    "GroundingBatchResponse",
    "levenshtein_distance",
    "semi_global_cer",
    "local_window_cer",
    "ground",
]
