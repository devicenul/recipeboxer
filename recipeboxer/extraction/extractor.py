"""Multimodal Gemini recipe transcription into RecipeSchema for RecipeBoxer.

Transcribes approved card scans into validated RecipeSchema models using the Gemini Multimodal Vision API.
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import hashlib
import inspect
import io
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx
from PIL import Image
import pydantic

from recipeboxer.ai.client import ImagePart, LLMClient, LLMRequest, LLMStage, TextPart
from recipeboxer.config import Settings, get_settings
from recipeboxer.ingestion.scanner import ScannedCard
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.failures import (
    ExtractionUnavailable,
    FailureReason,
    PipelineStage,
    StageFailure,
    StageUnavailableError,
)
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.vision.router import (
    CardModality,
    classify_card_modality,
    route_prompt_for_modality,
)


logger = logging.getLogger(__name__)

RECIPE_EXTRACTION_SYSTEM_INSTRUCTION = """You are an expert culinary archivist and document transcription specialist.
You are transcribing physical recipe cards (which may include front side, reverse back side, and multi-card continuation cards) into a structured recipe JSON.

TITLE INFERENCE & GUESSING:
- If the card has a clearly stated title, extract it cleanly without modification.
- If the recipe has NO explicitly stated title, or if the title is missing, torn, illegible, or inferred/guessed from ingredients or context: infer the most descriptive title (e.g. '<title as printed or inferred>') and set title_is_inferred to true. Do NOT append '[GUESS]' to the title text.

ANECDOTE, STORY & METADATA PRESERVATION:
- Extract family stories, handwritten marginalia (e.g., 'Cousin Maeve\\'s birthday gathering 1931', 'Great for parish picnics'), vintage dates, occasion notes, and personal memories into the 'description' field.
- Extract author attribution, vintage dates, handwritten signatures, and cookbook or card provenance into 'source_notes'.
- STRICT ISOLATION: NEVER place family stories, personal anecdotes, handwritten marginalia, or attribution notes into the 'ingredients' list or 'directions' steps — keep ingredients and directions strictly confined to culinary measurements, ingredients, and sequential preparation/cooking steps.

Carefully extract and format the following fields:
1. Title: The clean, primary recipe title. Strip decorative quotation marks, card numbering, or trailing punctuation. Do NOT append '[GUESS]'.

2. Description: Headnotes, background story, family anecdotes, marginalia, or general preparation notes (e.g. pan sizing, preheating).
3. Yield/Servings: Quantity or batch size (e.g. '24 cookies', '6 servings', '1 9-inch pie').
4. Prep Time: Active prep duration string (e.g. '15 mins', '30 minutes').
5. Cook Time: Active cook/bake duration string (e.g. '25 mins', '1 hr 15 mins').
6. Total Time: Overall time including resting/chilling (e.g. '2 hrs 15 mins').
7. Ingredients: An array of ingredient strings. Each string must be a single ingredient with quantity, unit, ingredient name, and prep notes (e.g. '2 cups all-purpose flour', '1/3 cup unsalted butter, softened'). If sub-recipe headers exist, include them formatted cleanly (e.g. 'For the Frosting:'). Strip leading bullets, asterisks, or dashes. STRICTLY EXCLUDE personal stories, marginalia, and notes.
8. Directions: An array of sequential preparation and cooking steps. Each step must be clearly articulated. Numbers like '1. ', '2. ' will be automatically standardized, but preserve the natural sequential ordering. STRICTLY EXCLUDE personal anecdotes and marginalia.
9. Tags: Categorical tags (e.g. ['Dessert', 'Baking', 'Cookies', 'Vintage']).
10. Source Notes: Attribution, card author, vintage dates, handwritten provenance, or cookbook reference (e.g. 'Great-Uncle Arthur (1931) Recipe Box').

When multiple images are provided (Front, Back, Continuation cards):
- Merge all sides seamlessly into a single complete recipe in sequential order without duplication.

Respond ONLY with valid JSON conforming to the RecipeSchema structure.
"""

RECIPE_EXTRACTION_SYSTEM_PROMPT = RECIPE_EXTRACTION_SYSTEM_INSTRUCTION


def compute_adaptive_thinking_budget(
    modality: Union[CardModality, str],
    degradation_level: Optional[str] = None,
    base_budget: Optional[int] = None,
) -> Tuple[bool, Optional[int]]:
    """Calculates adaptive thinking mode and token budget based on card modality and degradation.

    High reasoning budget for faded/cursive script or multi-layer clippings;
    fast 0-token inference for clean typewritten cards.

    Args:
        modality: CardModality or modality string.
        degradation_level: Optional physical damage level (NONE, MILD, MODERATE, SEVERE).
        base_budget: Optional explicit baseline budget to scale.

    Returns:
        Tuple of (thinking_mode_enabled: bool, thinking_budget_tokens: Optional[int]).
    """
    if base_budget is not None and base_budget > 0:
        return True, base_budget

    norm_mod = CardModality.normalize(modality)
    deg = (degradation_level or "NONE").upper()

    if norm_mod == CardModality.HANDWRITTEN_CURSIVE:
        budget = 4096 if deg in ("MODERATE", "SEVERE") else 2048
        return True, budget

    if norm_mod in (CardModality.PRINTED_CLIPPING_OR_LABEL, CardModality.PRINTED_CLIPPING):
        budget = 2048 if deg in ("MODERATE", "SEVERE") else 1024
        return True, budget

    if norm_mod == CardModality.MIXED_MEDIA:
        budget = 4096 if deg in ("MODERATE", "SEVERE") else 2048
        return True, budget

    if norm_mod == CardModality.HANDWRITTEN_PRINT:
        budget = 2048 if deg in ("MODERATE", "SEVERE") else 1024
        return True, budget

    # TYPEWRITTEN: Fast inference without thinking overhead unless severely degraded
    if deg in ("MODERATE", "SEVERE"):
        return True, 1024

    return False, None


_UNSET = object()


class GeminiRecipeExtractor:
    """Multimodal structured recipe extractor using Gemini vision models."""

    def __init__(
        self,
        api_key: Optional[str] = _UNSET,  # type: ignore[assignment]
        model_name: Optional[str] = None,
        client: Optional[Any] = None,
        thinking_mode: Optional[bool] = None,
        thinking_budget_tokens: Optional[int] = None,
        adaptive_thinking: Optional[bool] = None,
        settings: Optional[Settings] = None,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        """Initialize GeminiRecipeExtractor.

        Args:
            api_key: Optional Gemini API key override. Defaults to settings.gemini_api_key.
            model_name: Optional model override. Defaults to settings.get_extraction_model().
            client: Optional mock or custom Gemini client for testing.
            thinking_mode: Optional override for thinking mode.
            thinking_budget_tokens: Optional cap for thinking tokens.
            adaptive_thinking: Optional override for dynamic thinking budget allocation.
            settings: Optional custom Settings instance.
            llm_client: Optional production-hardened LLMClient instance.
        """
        self.settings: Settings = settings or get_settings()
        self.api_key = self.settings.gemini_api_key if api_key is _UNSET else api_key
        self.model_name = model_name or self.settings.get_extraction_model()
        self.client = client
        if llm_client is not None:
            self.llm_client = llm_client
        elif client is not None and isinstance(client, LLMClient):
            self.llm_client = client
        else:
            self.llm_client = None
        self.thinking_mode = (
            thinking_mode if thinking_mode is not None else self.settings.thinking_mode
        )
        self.thinking_budget_tokens = (
            thinking_budget_tokens
            if thinking_budget_tokens is not None
            else self.settings.thinking_budget_tokens
        )
        self.adaptive_thinking = (
            adaptive_thinking
            if adaptive_thinking is not None
            else getattr(self.settings, "adaptive_thinking", False)
        )

    async def extract_cards(
        self,
        cards: List[Union[VisualFingerprint, ScannedCard, Path, str, bytes, Image.Image]],
    ) -> RecipeSchema:
        """Extracts structured RecipeSchema from an ordered list of card scans (3+ cards).

        Args:
            cards: Ordered list of card fingerprints, ScannedCards, paths, bytes, or Images.

        Returns:
            Validated RecipeSchema instance merging content in sequential order.
        """
        return await self.extract(front_input=cards)

    async def extract(
        self,
        front_input: Union[
            VisualFingerprint,
            ScannedCard,
            Path,
            str,
            bytes,
            Image.Image,
            List[Union[VisualFingerprint, ScannedCard, Path, str, bytes, Image.Image]],
            Tuple[Any, ...],
        ],
        back_input: Optional[Union[VisualFingerprint, ScannedCard, Path, str, bytes, Image.Image]] = None,
    ) -> RecipeSchema:
        """Extracts structured RecipeSchema from front and optional back card scans or an ordered sequence.

        Args:
            front_input: Front card or sequence of cards.
            back_input: Optional back card scan.

        Returns:
            Validated RecipeSchema instance.

        Raises:
            ExtractionUnavailable: If no client/API key is present, the client yields
                invalid output, or the API call fails.
        """
        # Normalize input to list of items
        if isinstance(front_input, (list, tuple)):
            cards_list = list(front_input)
            if back_input is not None:
                cards_list.append(back_input)
        else:
            cards_list = [front_input]
            if back_input is not None:
                cards_list.append(back_input)

        if not cards_list:
            raise ValueError("No card inputs provided for extraction.")

        resolved_cards = [self._resolve_input(item) for item in cards_list]
        ordered_card_ids = [c[2] for c in resolved_cards if c[2]]

        primary_front = cards_list[0]
        primary_back = cards_list[1] if len(cards_list) > 1 else None

        front_bytes, front_path, front_id = resolved_cards[0]
        back_bytes, back_path, back_id = (
            resolved_cards[1] if len(resolved_cards) > 1 else (None, None, None)
        )

        effective_client = self.llm_client
        if effective_client is None:
            from recipeboxer.config import is_usable_gemini_key
            if not is_usable_gemini_key(self.api_key):
                raise ExtractionUnavailable(
                    "No Gemini API key configured for recipe extraction",
                    reason=FailureReason.NO_API_KEY,
                    card_ids=ordered_card_ids,
                    retryable=False,
                )
            from recipeboxer.ai.client import create_llm_client
            effective_client = create_llm_client(self.settings, api_key=self.api_key)
            if effective_client is None:
                raise ExtractionUnavailable(
                    "No Gemini API key configured for recipe extraction",
                    reason=FailureReason.NO_API_KEY,
                    card_ids=ordered_card_ids,
                    retryable=False,
                )

        return await self._delegate_to_pipeline(
            cards_list=cards_list,
            resolved_cards=resolved_cards,
            ordered_card_ids=ordered_card_ids,
            effective_client=effective_client,
        )


    # Alias for method name compatibility
    extract_recipe = extract

    def extract_sync(
        self,
        front_input: Union[
            VisualFingerprint,
            ScannedCard,
            Path,
            str,
            bytes,
            Image.Image,
            List[Union[VisualFingerprint, ScannedCard, Path, str, bytes, Image.Image]],
            Tuple[Any, ...],
        ],
        back_input: Optional[Union[VisualFingerprint, ScannedCard, Path, str, bytes, Image.Image]] = None,
    ) -> RecipeSchema:
        """Synchronously extract recipe by running the async extraction path."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, self.extract(front_input, back_input))
                return future.result()
        else:
            return asyncio.run(self.extract(front_input, back_input))

    async def extract_batch(
        self,
        pairs: List[Tuple[Any, Optional[Any]]],
        concurrency: Optional[int] = None,
    ) -> List[Union[RecipeSchema, StageFailure]]:
        """Extracts a batch of recipe card pairs concurrently with semaphore throttling.

        Per-item failures are collected and returned as StageFailure records.

        Args:
            pairs: List of (front_input, optional_back_input) tuples.
            concurrency: Max concurrent requests (defaults to settings.max_concurrency).

        Returns:
            List of extracted RecipeSchema objects or StageFailure records preserving input ordering.
        """
        max_conc = concurrency or self.settings.max_concurrency
        sem = asyncio.Semaphore(max_conc)

        async def _worker(front: Any, back: Optional[Any]) -> Union[RecipeSchema, StageFailure]:
            async with sem:
                try:
                    return await self.extract(front, back)
                except StageUnavailableError as err:
                    return err.to_failure()
                except Exception as exc:
                    cids = []
                    for item in (front, back):
                        if item is not None:
                            try:
                                _, _, cid = self._resolve_input(item)
                                if cid:
                                    cids.append(cid)
                            except Exception:
                                pass
                    return StageFailure(
                        stage=PipelineStage.EXTRACTION,
                        reason=FailureReason.MODEL_UNAVAILABLE,
                        message=str(exc),
                        card_ids=cids,
                    )

        tasks = [_worker(f, b) for f, b in pairs]
        return await asyncio.gather(*tasks)

    def _resolve_input(
        self,
        item: Optional[Union[VisualFingerprint, ScannedCard, Path, str, bytes, Image.Image]],
    ) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
        """Resolves various card input representations into (raw_bytes, path_str, id_str)."""
        if item is None:
            return None, None, None

        if isinstance(item, VisualFingerprint):
            p = Path(item.image_path)
            raw_b = p.read_bytes() if p.exists() and p.is_file() else b""
            return raw_b, item.image_path, item.card_id

        if isinstance(item, ScannedCard) or hasattr(item, "file_path"):
            p = Path(item.file_path)
            raw_b = p.read_bytes() if p.exists() and p.is_file() else b""
            return raw_b, str(p), getattr(item, "card_id", p.name)

        if isinstance(item, (Path, str)):
            p = Path(item)
            raw_b = p.read_bytes() if p.exists() and p.is_file() else b""
            return raw_b, str(p), p.name

        if isinstance(item, bytes):
            h = hashlib.sha256(item).hexdigest()[:8]
            return item, f"/memory/card_{h}.jpg", f"card_{h}.jpg"

        if isinstance(item, Image.Image):
            buf = io.BytesIO()
            item.save(buf, format="JPEG")
            raw_b = buf.getvalue()
            h = hashlib.sha256(raw_b).hexdigest()[:8]
            return raw_b, f"/memory/card_{h}.jpg", f"card_{h}.jpg"

        raise TypeError(f"Unsupported card input type: {type(item)}")

    async def _delegate_to_pipeline(
        self,
        cards_list: List[Any],
        resolved_cards: List[Tuple[Optional[bytes], Optional[str], Optional[str]]],
        ordered_card_ids: List[str],
        effective_client: LLMClient,
    ) -> RecipeSchema:
        """Delegates extraction to the modular ExtractionPipeline (WP-XA)."""
        import tempfile
        from recipeboxer.extraction.pipeline import ExtractionPipeline

        face_paths: List[Path] = []
        temp_files: List[Path] = []
        modality_hint: Optional[str] = None

        # Look for script_descriptor in any VisualFingerprint input
        for item in cards_list:
            if isinstance(item, VisualFingerprint):
                if getattr(item, "script_descriptor", None) and not modality_hint:
                    modality_hint = item.script_descriptor

        try:
            for (c_bytes, c_path, c_id) in resolved_cards:
                cid_list = [c_id] if c_id else ordered_card_ids
                if c_path and Path(c_path).exists() and Path(c_path).is_file():
                    try:
                        with Image.open(Path(c_path)) as img:
                            img.verify()
                    except Exception as verify_err:
                        raise ExtractionUnavailable(
                            f"Input card image file is corrupted or unreadable: {c_path}",
                            reason=FailureReason.INPUT_UNREADABLE,
                            card_ids=cid_list,
                            retryable=False,
                        ) from verify_err
                    face_paths.append(Path(c_path))
                elif c_bytes:
                    try:
                        with Image.open(io.BytesIO(c_bytes)) as img:
                            img.verify()
                    except Exception as verify_err:
                        raise ExtractionUnavailable(
                            f"Input card image bytes are corrupted or unreadable: {c_id}",
                            reason=FailureReason.INPUT_UNREADABLE,
                            card_ids=cid_list,
                            retryable=False,
                        ) from verify_err
                    tf = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
                    tf.write(c_bytes)
                    tf.close()
                    p = Path(tf.name)
                    temp_files.append(p)
                    face_paths.append(p)
                else:
                    raise ExtractionUnavailable(
                        f"Card has no readable image file or bytes: {c_id or c_path}",
                        reason=FailureReason.INPUT_UNREADABLE,
                        card_ids=cid_list,
                        retryable=False,
                    )

            pipeline = ExtractionPipeline(
                llm_client=effective_client,
                settings=self.settings,
            )
            try:
                result = await pipeline.run(face_paths, modality_hint=modality_hint)
            except StageUnavailableError as exc:
                if not exc.card_ids and ordered_card_ids:
                    exc.card_ids = ordered_card_ids
                raise
            recipe = result.recipe

            if ordered_card_ids and not recipe.card_ids:
                recipe.card_ids = ordered_card_ids
            if recipe.card_ids and not recipe.image_urls:
                recipe.image_urls = [f"/api/cards/{cid}/image" for cid in recipe.card_ids]

            return recipe
        finally:
            for tf in temp_files:
                try:
                    tf.unlink(missing_ok=True)
                except Exception:
                    pass


# Alias for backward compatibility
RecipeExtractor = GeminiRecipeExtractor
