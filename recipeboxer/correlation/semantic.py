"""Stage 2 Gemini Multimodal Semantic Continuity Verifier for RecipeBoxer.

Evaluates culinary, syntactic, and visual continuity across candidate front/back card pairs,
computing a composite confidence score to drive Human-in-the-Loop proposals.
"""

from __future__ import annotations

import base64
import inspect
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from PIL import Image

from recipeboxer.config import get_settings
from recipeboxer.models.correlation import CorrelationProposal
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.vision.heuristics import _get_enum_str, calculate_heuristic_distance

logger = logging.getLogger(__name__)

SEMANTIC_VERIFICATION_PROMPT = """You are an expert culinary archivist and forensic document analyst.
You are evaluating two scanned recipe card sides: Card A (Candidate Front) and Card B (Candidate Back).

Determine whether Card A and Card B form a continuous, matching two-sided recipe.

Evaluate:
1. Syntactic & Narrative Continuity: Does text on Card A end with an incomplete thought/step that Card B continues?
2. Recipe Identity: Are the directions on Card B preparing the exact ingredients listed on Card A?
3. Culinary Logic: Do cooking temperatures, equipment, and steps match the ingredients?
4. Penmanship & Medium: Does the handwriting, ink, and card stock match?

Respond with strict JSON with these fields:
- "is_pair_match": boolean (true if matching pair)
- "semantic_score": float (0.0 to 1.0 confidence)
- "explanation": string (narrative justification)
- "recommended_action": string ("PAIR", "UNCERTAIN", or "REJECT")
"""

class GeminiSemanticVerifier:
    """Stage 2 semantic continuity verifier using Gemini Multimodal models."""


    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        client: Optional[Any] = None,
        heuristic_weight: Optional[float] = None,
        semantic_weight: Optional[float] = None,
    ) -> None:
        """Initialize GeminiSemanticVerifier.

        Args:
            api_key: Optional Gemini API key. Defaults to settings.
            model_name: Optional model override. Defaults to settings.get_correlation_model().
            client: Optional mock Gemini client for testing.
            heuristic_weight: Weight of heuristic score in composite confidence (default 0.30).
            semantic_weight: Weight of semantic score in composite confidence (default 0.70).
        """
        self.settings = get_settings()
        self.api_key = api_key or self.settings.gemini_api_key
        self.model_name = model_name or self.settings.get_correlation_model()
        self.client = client
        self.heuristic_weight = (
            heuristic_weight if heuristic_weight is not None else self.settings.heuristic_weight
        )
        self.semantic_weight = (
            semantic_weight if semantic_weight is not None else self.settings.semantic_weight
        )

    async def verify_pair(
        self,
        front_fp: VisualFingerprint,
        back_fp: VisualFingerprint,
        heuristic_score: Optional[float] = None,
    ) -> CorrelationProposal:
        """Verifies semantic continuity between candidate front and back card fingerprints.

        Args:
            front_fp: VisualFingerprint of front card.
            back_fp: VisualFingerprint of back card.
            heuristic_score: Pre-calculated Stage 1 heuristic score (calculated if None).

        Returns:
            CorrelationProposal item.
        """
        if heuristic_score is None:
            heuristic_score = calculate_heuristic_distance(front_fp, back_fp)

        # 1. Custom or injected client (e.g. MockGeminiClient in tests)
        if self.client is not None:
            res = None
            for method_name in ("verify_pair", "verify_correlation"):
                if hasattr(self.client, method_name):
                    method = getattr(self.client, method_name)
                    try:
                        potential_res = method(front_fp, back_fp, heuristic_score)
                        if inspect.isawaitable(potential_res):
                            potential_res = await potential_res
                        if potential_res is not None and not type(potential_res).__name__.endswith("Mock"):
                            res = potential_res
                            break
                        elif potential_res is not None and res is None:
                            res = potential_res
                    except TypeError:
                        pass

            if (res is None or type(res).__name__.endswith("Mock")) and callable(self.client):
                try:
                    potential_res = self.client(front_fp, back_fp, heuristic_score)
                    if inspect.isawaitable(potential_res):
                        potential_res = await potential_res
                    if potential_res is not None and not type(potential_res).__name__.endswith("Mock"):
                        res = potential_res
                except TypeError:
                    pass

            if res is not None:
                if isinstance(res, CorrelationProposal):
                    return res
                if hasattr(res, "model_dump") and not type(res).__name__.endswith("Mock"):
                    return CorrelationProposal.model_validate(res.model_dump())
                if isinstance(res, dict):
                    return CorrelationProposal.model_validate(res)
                return res

        # 2. Real Gemini API call if API key configured
        if self.api_key and not self.api_key.startswith("mock"):
            try:
                return await self._call_gemini_semantic_api(front_fp, back_fp, heuristic_score)
            except Exception as exc:
                logger.warning(
                    "Gemini API semantic verification failed: %s. Returning degraded proposal.",
                    exc,
                )
                return self._evaluate_offline_continuity(
                    front_fp,
                    back_fp,
                    heuristic_score,
                    degraded_reason=f"API_ERROR: {type(exc).__name__}",
                )

        # 3. Offline semantic continuity evaluation (no model available)
        return self._evaluate_offline_continuity(
            front_fp,
            back_fp,
            heuristic_score,
            degraded_reason="NO_MODEL",
        )

    async def verify_candidate_proposals(
        self,
        candidates: List[Tuple[VisualFingerprint, VisualFingerprint, float]],
    ) -> List[CorrelationProposal]:
        """Verifies a batch of candidate pairs passing Stage 1 screening.

        Args:
            candidates: List of (front_fp, back_fp, heuristic_score) tuples.

        Returns:
            List of CorrelationProposal items.
        """
        proposals: List[CorrelationProposal] = []
        for front_fp, back_fp, h_score in candidates:
            proposal = await self.verify_pair(front_fp, back_fp, heuristic_score=h_score)
            proposals.append(proposal)
        return proposals

    async def _call_gemini_semantic_api(
        self,
        front_fp: VisualFingerprint,
        back_fp: VisualFingerprint,
        heuristic_score: float,
    ) -> CorrelationProposal:
        """Invokes Gemini Multimodal API with dual image attachments."""
        import httpx

        # Read images if available on filesystem - strictly opaque labels enforce INV-AC2
        parts: List[Dict[str, Any]] = [
            {
                "text": f"{SEMANTIC_VERIFICATION_PROMPT}\n\nFront Card: Card A\nBack Card: Card B"
            }
        ]

        p1 = Path(front_fp.image_path)
        if p1.exists() and p1.is_file():
            b64_1 = base64.b64encode(p1.read_bytes()).decode("utf-8")
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64_1}})

        p2 = Path(back_fp.image_path)
        if p2.exists() and p2.is_file():
            b64_2 = base64.b64encode(p2.read_bytes()).decode("utf-8")
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64_2}})

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.1,
            },
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed_json = json.loads(raw_text)

            semantic_score = float(parsed_json.get("semantic_score", 0.0))
            explanation = str(parsed_json.get("explanation", ""))
            action = str(parsed_json.get("recommended_action", "PAIR"))

            composite = self.heuristic_weight * heuristic_score + self.semantic_weight * semantic_score
            composite = round(max(0.0, min(1.0, composite)), 4)

            return CorrelationProposal(
                proposal_id=f"prop_{uuid.uuid4().hex[:8]}",
                ordered_card_ids=[front_fp.card_id, back_fp.card_id],
                part_count=2,
                transition_scores=[composite],
                heuristic_score=heuristic_score,
                semantic_score=semantic_score,
                confidence=composite,
                is_standalone=False,
                continuity_explanation=explanation,
                recommended_action=action,
                semantic_verified=True,
                degraded_reason=None,
            )

    def _evaluate_offline_continuity(
        self,
        front_fp: VisualFingerprint,
        back_fp: VisualFingerprint,
        heuristic_score: float,
        degraded_reason: str = "NO_MODEL",
    ) -> CorrelationProposal:
        """Evaluates semantic continuity offline using honest boundary text cues and heuristics."""
        f_last = (
            front_fp.boundary_text.last_line.lower()
            if front_fp.boundary_text and front_fp.boundary_text.last_line
            else ""
        )
        b_first = (
            back_fp.boundary_text.first_line.lower()
            if back_fp.boundary_text and back_fp.boundary_text.first_line
            else ""
        )

        has_cue = bool(
            front_fp.boundary_text and front_fp.boundary_text.has_continuation_symbol
        )
        is_cutoff = bool(
            front_fp.boundary_text and front_fp.boundary_text.is_cut_off_mid_sentence
        )
        has_cont_header = any(
            kw in b_first for kw in ("cont", "direct", "step", "bake")
        )
        has_explicit_cont_in_header = "continued" in b_first or "cont." in b_first or "cont)" in b_first
        has_cont_symbol_in_text = any(
            sym in f_last for sym in ("(over", "->", "turn over", "pt.", "part")
        )

        front_has_continuation = has_cue or has_cont_symbol_in_text
        back_has_continuation = has_explicit_cont_in_header or (is_cutoff and has_cont_header)

        # Check real evidence in boundary text
        if front_has_continuation and (is_cutoff or back_has_continuation):
            # Strong boundary text evidence (front cue + back continuation)
            semantic_score = 0.85
            raw_comp = self.heuristic_weight * heuristic_score + self.semantic_weight * semantic_score
            composite = round(min(0.80, max(0.0, raw_comp)), 4)
            action = "PAIR" if composite >= 0.75 else "UNCERTAIN"
            explanation = (
                f"Strong boundary text continuity detected ({degraded_reason}): front card ends with continuation cue/cutoff, "
                "back card begins with continuation header."
            )
        elif front_has_continuation or back_has_continuation:
            # Moderate / partial boundary text cues
            semantic_score = 0.70
            raw_comp = self.heuristic_weight * heuristic_score + self.semantic_weight * semantic_score
            composite = round(min(0.65, max(0.0, raw_comp)), 4)
            action = "UNCERTAIN"
            explanation = (
                f"Moderate boundary text cues detected ({degraded_reason}): continuation cues present in boundary OCR text. "
                "Recommended for human review."
            )
        else:
            # No real boundary evidence: neutral score, confidence = heuristic score only
            semantic_score = 0.5
            composite = round(max(0.0, min(1.0, heuristic_score)), 4)
            action = "UNCERTAIN"
            explanation = (
                f"Unverified correlation ({degraded_reason}): no multimodal LLM available; "
                "confidence reflects Stage 1 heuristic score only."
            )

        return CorrelationProposal(
            proposal_id=f"prop_{uuid.uuid4().hex[:8]}",
            ordered_card_ids=[front_fp.card_id, back_fp.card_id],
            part_count=2,
            transition_scores=[composite],
            heuristic_score=heuristic_score,
            semantic_score=semantic_score,
            confidence=composite,
            is_standalone=False,
            continuity_explanation=explanation,
            recommended_action=action,
            semantic_verified=False,
            degraded_reason=degraded_reason,
        )
