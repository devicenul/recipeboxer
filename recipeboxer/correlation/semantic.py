"""Stage 2 Multimodal Set-Level Verification for RecipeBoxer (G6, WP-GC).

Evaluates whether candidate card faces continue the recipe begun on an anchor card,
using structured LLM verification with neutral labels, deterministic randomized candidate
ordering (to eliminate position bias), verbatim visible quote validation, and content-keyed caching.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import uuid
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence, Set, Tuple, Union, runtime_checkable

from pydantic import BaseModel, Field

from recipeboxer.ai.client import (
    ImagePart,
    LLMClient,
    LLMRequest,
    LLMStage,
    TextPart,
    _extract_json_text,
    create_llm_client,
)
from recipeboxer.config import Settings, get_settings
from recipeboxer.models.correlation import CorrelationProposal, SetVerification
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.failures import (
    BudgetExceeded,
    FailureReason,
    SemanticVerificationUnavailable,
    StageUnavailableError,
)
from recipeboxer.extraction.grounding import levenshtein_distance
from recipeboxer.prompts import PromptRecord, get_prompt
from recipeboxer.vision.heuristics import calculate_heuristic_distance

logger = logging.getLogger(__name__)

SET_VERIFY_PROMPT_ID = "set_verify_v1"
# WP-UP4: prompt for anchors that are themselves a back side / continuation card.
SET_VERIFY_EXT_PROMPT_ID = "set_verify_v2"


def _is_continuation_anchor(fp: VisualFingerprint) -> bool:
    """True when the anchor face is a back side or a continuation card (not a recipe start)."""
    if getattr(fp, "looks_like_back", False) is True:
        return True
    role = str(getattr(getattr(fp, "role", None), "value", getattr(fp, "role", "")) or "").upper()
    return "CONTINUATION" in role or "BACK" in role


@runtime_checkable
class ImageProvider(Protocol):
    """Protocol for accessing raw image bytes given a card_id."""

    def get(self, card_id: str) -> bytes:
        ...


class DefaultImageProvider:
    """Default filesystem/mapping image provider."""

    def __init__(self, card_paths: Optional[Dict[str, Union[str, Path]]] = None) -> None:
        self.card_paths: Dict[str, Union[str, Path]] = dict(card_paths or {})

    def get(self, card_id: str) -> Optional[bytes]:
        if card_id in self.card_paths:
            path = Path(self.card_paths[card_id])
            if path.exists() and path.is_file():
                try:
                    return path.read_bytes()
                except Exception as exc:
                    logger.warning("Failed to read image bytes for card %s: %s", card_id, exc)
                    return None
        return None



def compute_set_verify_cache_key(
    anchor_hash: str,
    candidate_hashes: Sequence[str],
    model: str,
    prompt_version: str,
) -> str:
    """Computes a deterministic cache key for set verification.

    Keyed by (anchor hash, sorted candidate hashes, model, prompt version).
    """
    sorted_hashes = ",".join(sorted(candidate_hashes))
    raw = f"{anchor_hash}:{sorted_hashes}:{model}:{prompt_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class SetVerificationResponse(BaseModel):
    """Structured Pydantic response schema from the LLM for set verification using neutral labels."""

    continuations: List[str] = Field(
        default_factory=list,
        description="Ordered list of candidate labels (e.g. ['Candidate 2', 'Candidate 1']) that continue the anchor recipe, in sequence order. Empty if none.",
    )
    rejected: List[str] = Field(
        default_factory=list,
        description="List of candidate labels that do NOT continue the anchor recipe.",
    )
    evidence_quotes: List[str] = Field(
        default_factory=list,
        description="Verbatim text quotes visible on the cards supporting continuity or rejection.",
    )
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Confidence score between 0.0 and 1.0 in this set verification decision.",
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Brief justification explaining narrative continuity, culinary logic, and handwriting/ink matching.",
    )


def _format_fingerprint_text(label: str, fp: VisualFingerprint) -> str:
    """Formats textual and structural fingerprint fields using only opaque neutral labels."""
    lines = [f"{label}:"]
    if fp.visible_title:
        lines.append(f"  - Visible title: {fp.visible_title}")
    if fp.first_lines:
        lines.append(f"  - First lines: {' / '.join(fp.first_lines)}")
    elif fp.boundary_text and fp.boundary_text.first_line:
        lines.append(f"  - First line: {fp.boundary_text.first_line}")
    if fp.last_lines:
        lines.append(f"  - Last lines: {' / '.join(fp.last_lines)}")
    elif fp.boundary_text and fp.boundary_text.last_line:
        lines.append(f"  - Last line: {fp.boundary_text.last_line}")
    if fp.printed_markers:
        lines.append(f"  - Printed markers: {', '.join(fp.printed_markers)}")
    if fp.sections_detected:
        lines.append(f"  - Sections detected: {', '.join(fp.sections_detected)}")
    if fp.text_cut_off_start is not None:
        lines.append(f"  - Text cut off at start: {fp.text_cut_off_start}")
    if fp.text_cut_off_end is not None:
        lines.append(f"  - Text cut off at end: {fp.text_cut_off_end}")
    if fp.looks_like_back is not None:
        lines.append(f"  - Looks like back of card: {fp.looks_like_back}")
    if fp.has_ingredients is not None:
        lines.append(f"  - Has ingredients: {fp.has_ingredients}")
    if fp.has_directions is not None:
        lines.append(f"  - Has directions: {fp.has_directions}")
    if fp.script_descriptor:
        lines.append(f"  - Script / typography: {fp.script_descriptor}")
    return "\n".join(lines)


def _extract_all_fingerprint_text(fps: Sequence[VisualFingerprint]) -> Tuple[str, bool]:
    """Extracts all known visible text from a sequence of fingerprints for quote verification.

    Returns:
        (combined_text, has_full_text)
    """
    texts: List[str] = []
    has_full_text = True
    for fp in fps:
        fp_has_full = False
        if fp.visible_title:
            texts.append(fp.visible_title)
        if fp.first_lines:
            texts.extend(fp.first_lines)
        if fp.last_lines:
            texts.extend(fp.last_lines)
        if fp.printed_markers:
            texts.extend(fp.printed_markers)
        if fp.boundary_text:
            if fp.boundary_text.first_line:
                texts.append(fp.boundary_text.first_line)
            if fp.boundary_text.last_line:
                texts.append(fp.boundary_text.last_line)
        ocr_lines = getattr(fp, "ocr_lines", None)
        if ocr_lines and isinstance(ocr_lines, list):
            texts.extend(str(l) for l in ocr_lines)
            fp_has_full = True
        body_text = getattr(fp, "body_text", None) or getattr(fp, "text", None)
        if body_text:
            texts.append(str(body_text))
            fp_has_full = True
        body_lines = getattr(fp, "body_lines", None)
        if body_lines and isinstance(body_lines, list):
            texts.extend(str(l) for l in body_lines)
            fp_has_full = True
        if not fp_has_full:
            has_full_text = False
    return " ".join(texts), has_full_text


def _is_phrase_match(clean_q: str, clean_corpus: str) -> bool:
    """Checks if a normalized clean quote appears as a contiguous or near-contiguous phrase in clean corpus."""
    if not clean_q or not clean_corpus:
        return False
    # Check 1: direct substring in clean corpus
    if clean_q in clean_corpus:
        return True

    # Check 2: direct substring without whitespace (handles line wrapping, hyphenation, compound words)
    q_nospace = clean_q.replace(" ", "")
    c_nospace = clean_corpus.replace(" ", "")
    if len(q_nospace) >= 5 and q_nospace in c_nospace:
        return True

    # Check 3: contiguous sliding window phrase match with CER <= 0.20
    q_words = clean_q.split()
    if len(q_words) >= 2:
        c_words = clean_corpus.split()
        k = len(q_words)
        for i in range(max(0, len(c_words) - k + 1)):
            window = c_words[i : i + k]
            window_str = " ".join(window)
            cer = levenshtein_distance(clean_q, window_str) / max(len(clean_q), 1)
            if cer <= 0.20:
                return True

    return False


def _validate_evidence_quotes(
    quotes: Sequence[str],
    all_fps: Sequence[VisualFingerprint],
) -> Tuple[List[str], List[str], List[str]]:
    """Validates cited quotes against provided fingerprint text/OCR.

    Returns:
        (verified_quotes, unverified_quotes, unchecked_quotes)
    """
    corpus, has_full_text = _extract_all_fingerprint_text(all_fps)
    normalized_corpus = re.sub(r"\s+", " ", corpus.lower().strip())
    clean_corpus = re.sub(r"[^\w\s]", "", normalized_corpus)

    verified: List[str] = []
    unverified: List[str] = []
    unchecked: List[str] = []

    for raw_quote in quotes:
        quote = raw_quote.strip()
        if not quote:
            continue
        norm_q = re.sub(r"\s+", " ", quote.lower().strip())
        clean_q = re.sub(r"[^\w\s]", "", norm_q)

        if not clean_q:
            continue

        if norm_q in normalized_corpus or _is_phrase_match(clean_q, clean_corpus):
            verified.append(quote)
            continue

        # Quote not matched in available text.
        # If full text (OCR / body) was available for all cards, mark UNVERIFIED (hallucination).
        # If full text was not available, mark UNCHECKED (neutral, no penalty).
        if has_full_text:
            unverified.append(quote)
        else:
            unchecked.append(quote)

    return verified, unverified, unchecked



def _fetch_image_bytes(images: Any, card: VisualFingerprint) -> bytes:
    """Helper to safely fetch image bytes from ImageProvider or filesystem."""
    if images is not None:
        if hasattr(images, "get"):
            data = images.get(card.card_id)
            if data:
                return data
        elif callable(images):
            try:
                data = images(card.card_id)
                if data:
                    return data
            except Exception:
                pass
    if card.image_path:
        p = Path(card.image_path)
        if p.exists() and p.is_file():
            try:
                return p.read_bytes()
            except Exception:
                pass
    return b""


class SetVerifier:
    """Stage 2 set-level verification using multimodal LLM (G6).

    Executes ONE call per anchor with the anchor image + its k candidate images
    under neutral labels in deterministically randomized order to eliminate position bias.
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        settings: Optional[Settings] = None,
        cache: Optional[Any] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm_client = llm_client
        self.model_name = self.settings.get_stage_model(LLMStage.SET_VERIFY)
        self.prompt_record: PromptRecord = get_prompt(SET_VERIFY_PROMPT_ID)
        self.ext_prompt_record: PromptRecord = get_prompt(SET_VERIFY_EXT_PROMPT_ID)
        self._cache = cache if cache is not None else {}

    def _cache_get(self, key: str) -> Optional[SetVerification]:
        if isinstance(self._cache, dict):
            return self._cache.get(key)
        if hasattr(self._cache, "get"):
            return self._cache.get(key)
        return None

    def _cache_set(self, key: str, value: SetVerification) -> None:
        if isinstance(self._cache, dict):
            self._cache[key] = value
        elif hasattr(self._cache, "set"):
            self._cache.set(key, value)
        elif hasattr(self._cache, "__setitem__"):
            self._cache[key] = value

    async def verify(
        self,
        anchor: VisualFingerprint,
        candidates: List[VisualFingerprint],
        images: Optional[ImageProvider] = None,
    ) -> SetVerification:
        """Verifies candidate continuation cards against an anchor card in a single LLM call.

        Args:
            anchor: VisualFingerprint of the anchor card (front of recipe).
            candidates: List of k candidate VisualFingerprints.
            images: ImageProvider protocol providing raw image bytes by card_id.

        Returns:
            SetVerification domain model with verified continuations, rejections, evidence quotes, and confidence.

        Raises:
            BudgetExceeded: If budget cap is exceeded (propagated unmodified).
            SemanticVerificationUnavailable: If model unavailable, rate-limited, timeout, or no client.
        """
        # WP-UP4: back / continuation anchors use set_verify_v2, which explicitly allows a back ->
        # next-card-front continuation (v1 asks for the recipe that *begins* on the Anchor and tended to
        # reject those). Start anchors keep v1 (and its cache).
        is_ext_anchor = _is_continuation_anchor(anchor)
        prec = self.ext_prompt_record if is_ext_anchor else self.prompt_record

        # Base case: 0 candidates
        if not candidates:
            return SetVerification(
                anchor=anchor.card_id,
                continuations=[],
                rejected=[],
                evidence_quotes=[],
                confidence=None,
                flags=[],
                model=self.model_name,
                prompt_id=prec.id,
                prompt_version=prec.version,
            )

        # Cache check
        cache_key = compute_set_verify_cache_key(
            anchor.sha256_hash or "",
            [c.sha256_hash or "" for c in candidates],
            self.model_name,
            prec.version,
        )
        cached = self._cache_get(cache_key)
        if cached is None and hasattr(self._cache, "get_by_anchor"):
            cached = self._cache.get_by_anchor(anchor.card_id)
        if cached is not None:
            return cached

        if self.llm_client is None:
            raise SemanticVerificationUnavailable(
                "No LLM client configured for set-level verification",
                reason=FailureReason.NO_API_KEY,
                card_ids=[anchor.card_id] + [c.card_id for c in candidates],
            )

        # Deterministic randomized candidate order seeded from content hashes
        base_candidates = sorted(candidates, key=lambda c: (c.sha256_hash or "", c.card_id))
        cand_hashes_str = ",".join(sorted(c.sha256_hash or "" for c in candidates))
        seed_material = f"{anchor.sha256_hash or ''}:{cand_hashes_str}"
        seed_int = int(hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:16], 16)
        rng = random.Random(seed_int)

        shuffled_candidates = list(base_candidates)
        rng.shuffle(shuffled_candidates)

        label_to_card_id: Dict[str, str] = {}
        card_id_to_label: Dict[str, str] = {}
        for i, cand in enumerate(shuffled_candidates):
            lbl = f"Candidate {i + 1}"
            label_to_card_id[lbl] = cand.card_id
            card_id_to_label[cand.card_id] = lbl

        # Assemble multimodal request parts strictly using neutral labels
        parts: List[Union[TextPart, ImagePart]] = []

        anchor_intro = (
            (
                "Evaluate whether any candidate cards continue the recipe text on the Anchor face (the Anchor "
                "may be a back side or a later card; its continuation may be the front of the next card).\n\n"
                if is_ext_anchor
                else "Evaluate whether any candidate cards continue the recipe started on the Anchor card.\n\n"
            )
            + _format_fingerprint_text("Anchor", anchor)
        )
        parts.append(TextPart(text=anchor_intro))

        anchor_bytes = _fetch_image_bytes(images, anchor)
        if anchor_bytes:
            parts.append(ImagePart(data=anchor_bytes, label="Anchor"))

        for i, cand in enumerate(shuffled_candidates):
            cand_lbl = f"Candidate {i + 1}"
            cand_text = _format_fingerprint_text(cand_lbl, cand)
            parts.append(TextPart(text=cand_text))
            cand_bytes = _fetch_image_bytes(images, cand)
            if cand_bytes:
                parts.append(ImagePart(data=cand_bytes, label=cand_lbl))

        req = LLMRequest(
            stage=LLMStage.SET_VERIFY,
            model=self.model_name,
            system_instruction=prec.text,
            parts=parts,
            response_model=SetVerificationResponse,
            temperature=0.1,
            metadata={"anchor_card_id": anchor.card_id, "k": len(candidates)},
        )

        try:
            resp = await self.llm_client.generate(req)
        except BudgetExceeded:
            raise
        except SemanticVerificationUnavailable:
            raise
        except StageUnavailableError as exc:
            raise SemanticVerificationUnavailable(
                f"Semantic verification stage unavailable: {exc}",
                reason=exc.reason,
                card_ids=[anchor.card_id] + [c.card_id for c in candidates],
                retryable=exc.retryable,
                details=exc.details,
            ) from exc
        except Exception as exc:
            raise SemanticVerificationUnavailable(
                f"LLMClient generate failed during set verification: {exc}",
                reason=FailureReason.MODEL_UNAVAILABLE,
                card_ids=[anchor.card_id] + [c.card_id for c in candidates],
            ) from exc

        # Parse structured output
        parsed: Optional[SetVerificationResponse] = None
        if resp.parsed is not None:
            if isinstance(resp.parsed, SetVerificationResponse):
                parsed = resp.parsed
            elif isinstance(resp.parsed, SetVerification):
                self._cache_set(cache_key, resp.parsed)
                return resp.parsed
            elif isinstance(resp.parsed, CorrelationProposal):
                prop = resp.parsed
                cand_ids = {c.card_id for c in candidates}
                c_list = (
                    [cid for cid in prop.ordered_card_ids if cid != anchor.card_id and cid in cand_ids]
                    if prop.recommended_action in ("PAIR", "SEQUENCE")
                    else []
                )
                r_list = [c.card_id for c in candidates if c.card_id not in c_list]
                res = SetVerification(
                    anchor=anchor.card_id,
                    continuations=c_list,
                    rejected=r_list,
                    evidence_quotes=[prop.continuity_explanation] if prop.continuity_explanation else [],
                    confidence=prop.confidence,
                    flags=[],
                    model=self.model_name,
                    prompt_id=prec.id,
                    prompt_version=prec.version,
                )
                self._cache_set(cache_key, res)
                return res
            elif hasattr(resp.parsed, "model_dump"):
                parsed = SetVerificationResponse.model_validate(resp.parsed.model_dump())
            elif isinstance(resp.parsed, dict):
                parsed = SetVerificationResponse.model_validate(resp.parsed)

        if parsed is None and resp.raw_text:
            try:
                cleaned_json = _extract_json_text(resp.raw_text)
                data = json.loads(cleaned_json)
                parsed = SetVerificationResponse.model_validate(data)
            except Exception as exc:
                raise SemanticVerificationUnavailable(
                    f"Failed to parse LLM response into SetVerificationResponse: {exc}",
                    reason=FailureReason.SCHEMA_VALIDATION,
                    card_ids=[anchor.card_id] + [c.card_id for c in candidates],
                ) from exc

        if parsed is None:
            raise SemanticVerificationUnavailable(
                "LLM returned empty or null response for set verification",
                reason=FailureReason.INVALID_RESPONSE,
                card_ids=[anchor.card_id] + [c.card_id for c in candidates],
            )

        # Map candidate labels back to original card_ids
        continuations_out: List[str] = []
        for candidate_lbl in parsed.continuations:
            cl = str(candidate_lbl).strip()
            if not cl or cl.lower() in ("none", "null", "no", "n/a", "[]"):
                continue

            matched_id = label_to_card_id.get(cl)
            if not matched_id:
                # Normalization check
                norm_cl = re.sub(r"\s+", "", cl.lower())
                for l_key, c_id in label_to_card_id.items():
                    if norm_cl == re.sub(r"\s+", "", l_key.lower()):
                        matched_id = c_id
                        break
            if matched_id and matched_id not in continuations_out:
                continuations_out.append(matched_id)

        # All candidate cards not continuing the anchor are rejected
        rejected_out: List[str] = [c.card_id for c in candidates if c.card_id not in continuations_out]

        # Quote validation against provided fingerprint text / OCR
        all_fps = [anchor] + candidates
        quotes_in = list(parsed.evidence_quotes)
        verified_quotes, unverified_quotes, unchecked_quotes = _validate_evidence_quotes(quotes_in, all_fps)

        flags: List[str] = []
        eff_confidence = parsed.confidence

        if unverified_quotes:
            for uq in unverified_quotes:
                flags.append(f"UNVERIFIED_QUOTE: {uq}")
                logger.warning("SetVerifier: cited quote %r conflicts with available card text/OCR", uq)
            # Lower confidence by 50% when hallucinated quotes conflict with available text
            if eff_confidence is not None:
                eff_confidence = round(max(0.0, eff_confidence * 0.5), 4)
            else:
                eff_confidence = 0.35

        if unchecked_quotes:
            for uq in unchecked_quotes:
                flags.append(f"UNCHECKED_QUOTE: {uq}")
                logger.info("SetVerifier: cited quote %r is unchecked due to partial fingerprint text", uq)
            # UNCHECKED quotes are recorded neutrally without confidence penalty

        result = SetVerification(
            anchor=anchor.card_id,
            continuations=continuations_out,
            rejected=rejected_out,
            evidence_quotes=quotes_in,
            confidence=eff_confidence,
            flags=flags,
            verified_quotes=verified_quotes,
            unverified_quotes=unverified_quotes,
            unchecked_quotes=unchecked_quotes,
            model=self.model_name,
            prompt_id=prec.id,
            prompt_version=prec.version,
        )

        self._cache_set(cache_key, result)
        return result


_DEFAULT_SENTINEL = object()


class GeminiSemanticVerifier:
    """Deprecated compatibility shim for existing callers of the pairwise API.

    Delegates to SetVerifier with k=1. Single path: LLMClient.
    """

    def __init__(
        self,
        api_key: Optional[str] = _DEFAULT_SENTINEL,  # type: ignore[assignment]
        model_name: Optional[str] = None,
        client: Optional[Any] = None,
        heuristic_weight: Optional[float] = None,
        semantic_weight: Optional[float] = None,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        warnings.warn(
            "GeminiSemanticVerifier is deprecated; use SetVerifier instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.settings = get_settings()
        if api_key is _DEFAULT_SENTINEL:
            self.api_key = self.settings.gemini_api_key
        else:
            self.api_key = api_key

        self.model_name = model_name or self.settings.get_correlation_model()
        self.client = client
        if llm_client is not None:
            self.llm_client = llm_client
        elif client is not None and isinstance(client, LLMClient):
            self.llm_client = client
        else:
            self.llm_client = None

        self.heuristic_weight = (
            heuristic_weight if heuristic_weight is not None else self.settings.heuristic_weight
        )
        self.semantic_weight = (
            semantic_weight if semantic_weight is not None else self.settings.semantic_weight
        )

        if self.llm_client is None and self.api_key:
            from recipeboxer.ai.client import GenAIClient
            from recipeboxer.config import is_usable_gemini_key

            if is_usable_gemini_key(self.api_key):
                self.llm_client = GenAIClient(api_key=self.api_key, settings=self.settings)

        self._set_verifier = SetVerifier(
            llm_client=self.llm_client,
            settings=self.settings,
        )

    async def verify_pair(
        self,
        front_fp: VisualFingerprint,
        back_fp: VisualFingerprint,
        heuristic_score: Optional[float] = None,
    ) -> CorrelationProposal:
        """Verifies semantic continuity between candidate front and back card fingerprints.

        Delegates to SetVerifier with k=1. Single path: LLMClient.
        """
        # Without an LLM client, raise SemanticVerificationUnavailable(NO_API_KEY) (INV-AC4/AC6)
        if self.llm_client is None:
            raise SemanticVerificationUnavailable(
                "No LLM client or usable API key configured for semantic verification",
                reason=FailureReason.NO_API_KEY,
                card_ids=[front_fp.card_id, back_fp.card_id],
            )


        if heuristic_score is None:
            heuristic_score = calculate_heuristic_distance(front_fp, back_fp)

        # Delegate to SetVerifier with k=1
        images = DefaultImageProvider({
            front_fp.card_id: front_fp.image_path,
            back_fp.card_id: back_fp.image_path,
        })
        verification = await self._set_verifier.verify(front_fp, [back_fp], images=images)

        is_match = back_fp.card_id in verification.continuations
        s_score = (verification.confidence if verification.confidence is not None else 1.0) if is_match else 0.0
        action = "PAIR" if is_match else "REJECT"

        composite = round(
            max(0.0, min(1.0, self.heuristic_weight * heuristic_score + self.semantic_weight * s_score)),
            4,
        )

        explanation = (
            " ".join(verification.evidence_quotes)
            if verification.evidence_quotes
            else ("Set verification confirmed continuation." if is_match else "Set verification rejected candidate.")
        )

        return CorrelationProposal(
            proposal_id=f"prop_{uuid.uuid4().hex[:8]}",
            ordered_card_ids=[front_fp.card_id, back_fp.card_id],
            part_count=2,
            transition_scores=[composite],
            heuristic_score=heuristic_score,
            semantic_score=s_score,
            confidence=composite,
            is_standalone=False,
            continuity_explanation=explanation,
            recommended_action=action,
            semantic_verified=True,
            degraded_reason=None,
        )

    async def verify_candidate_proposals(
        self,
        candidates: List[Tuple[VisualFingerprint, VisualFingerprint, float]],
    ) -> List[CorrelationProposal]:
        """Verifies a batch of candidate pairs passing Stage 1 screening."""
        proposals: List[CorrelationProposal] = []
        for front_fp, back_fp, h_score in candidates:
            proposal = await self.verify_pair(front_fp, back_fp, heuristic_score=h_score)
            proposals.append(proposal)
        return proposals


__all__ = [
    "ImageProvider",
    "DefaultImageProvider",
    "compute_set_verify_cache_key",
    "SetVerificationResponse",
    "SetVerifier",
    "GeminiSemanticVerifier",
]
