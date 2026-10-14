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
- If the recipe has NO explicitly stated title, or if the title is missing, torn, illegible, or inferred/guessed from ingredients or context: infer the most descriptive title (e.g. 'Rustic Peach Cobbler') and ALWAYS append the suffix ' [GUESS]' (e.g. 'Rustic Peach Cobbler [GUESS]') to clearly flag it for user verification.

ANECDOTE, STORY & METADATA PRESERVATION:
- Extract family stories, handwritten marginalia (e.g., 'Cousin Maeve\\'s birthday gathering 1931', 'Great for parish picnics'), vintage dates, occasion notes, and personal memories into the 'description' field.
- Extract author attribution, vintage dates, handwritten signatures, and cookbook or card provenance into 'source_notes'.
- STRICT ISOLATION: NEVER place family stories, personal anecdotes, handwritten marginalia, or attribution notes into the 'ingredients' list or 'directions' steps — keep ingredients and directions strictly confined to culinary measurements, ingredients, and sequential preparation/cooking steps.

Carefully extract and format the following fields:
1. Title: The clean, primary recipe title. Strip decorative quotation marks, card numbering, or trailing punctuation. Append ' [GUESS]' if inferred or untitled.
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


class GeminiRecipeExtractor:
    """Multimodal structured recipe extractor using Gemini vision models."""

    def __init__(
        self,
        api_key: Optional[str] = None,
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
        self.api_key = api_key or self.settings.gemini_api_key
        self.model_name = model_name or self.settings.get_extraction_model()
        self.client = client
        self.llm_client = llm_client
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

        # 1. Real LLMClient if injected (FIND-03)
        if self.llm_client is not None:
            return await self._call_llm_client_extraction(
                resolved_cards=resolved_cards,
                ordered_card_ids=ordered_card_ids,
            )

        # 2. Custom or injected client (e.g. MockGeminiClient in test suite)
        if self.client is not None:
            res = None
            try:
                # Check for extract_cards if multi-card
                if len(cards_list) > 2 and hasattr(self.client, "extract_cards"):
                    try:
                        potential_res = self.client.extract_cards(cards_list)
                        if inspect.isawaitable(potential_res):
                            potential_res = await potential_res
                        if potential_res is not None and not type(potential_res).__name__.endswith("Mock"):
                            res = potential_res
                    except (TypeError, AttributeError):
                        pass

                if res is None:
                    for method_name in ("extract_recipe", "extract", "transcribe_recipe"):
                        if hasattr(self.client, method_name):
                            method = getattr(self.client, method_name)
                            # Try calling with original inputs first
                            try:
                                if len(cards_list) > 2:
                                    try:
                                        potential_res = method(cards_list)
                                    except TypeError:
                                        potential_res = method(primary_front, primary_back)
                                else:
                                    potential_res = method(primary_front, primary_back)

                                if inspect.isawaitable(potential_res):
                                    potential_res = await potential_res
                                if potential_res is not None and not type(potential_res).__name__.endswith("Mock"):
                                    res = potential_res
                                    break
                                elif potential_res is not None and res is None:
                                    res = potential_res
                            except (TypeError, AttributeError):
                                pass

                            # If not resolved and front_input isn't VisualFingerprint, adapt to mock fingerprint
                            if res is None and not isinstance(primary_front, VisualFingerprint):
                                try:
                                    adapted_front = VisualFingerprint(
                                        card_id=front_id or "card_front",
                                        image_path=front_path or "/memory/card_front.jpg",
                                        sha256_hash=hashlib.sha256(front_bytes or b"").hexdigest(),
                                        role=CardRole.SEQUENCE_START if len(cards_list) > 1 else CardRole.STANDALONE,
                                    )
                                    adapted_back = (
                                        VisualFingerprint(
                                            card_id=back_id or "card_back",
                                            image_path=back_path or "/memory/card_back.jpg",
                                            sha256_hash=hashlib.sha256(back_bytes or b"").hexdigest(),
                                            role=CardRole.SEQUENCE_CONTINUATION,
                                        )
                                        if len(cards_list) > 1
                                        else None
                                    )
                                    potential_res = method(adapted_front, adapted_back)
                                    if inspect.isawaitable(potential_res):
                                        potential_res = await potential_res
                                    if potential_res is not None and not type(potential_res).__name__.endswith("Mock"):
                                        res = potential_res
                                        break
                                    elif potential_res is not None and res is None:
                                        res = potential_res
                                except (TypeError, AttributeError):
                                    pass

                if (res is None or type(res).__name__.endswith("Mock")) and callable(self.client):
                    try:
                        potential_res = (
                            self.client(cards_list)
                            if len(cards_list) > 2
                            else self.client(primary_front, primary_back)
                        )
                        if inspect.isawaitable(potential_res):
                            potential_res = await potential_res
                        if potential_res is not None and not type(potential_res).__name__.endswith("Mock"):
                            res = potential_res
                    except TypeError:
                        try:
                            potential_res = self.client(primary_front, primary_back)
                            if inspect.isawaitable(potential_res):
                                potential_res = await potential_res
                            if potential_res is not None and not type(potential_res).__name__.endswith("Mock"):
                                res = potential_res
                        except TypeError:
                            try:
                                potential_res = self.client(primary_front)
                                if inspect.isawaitable(potential_res):
                                    potential_res = await potential_res
                                if potential_res is not None and not type(potential_res).__name__.endswith("Mock"):
                                    res = potential_res
                            except TypeError:
                                pass
            except ExtractionUnavailable:
                raise
            except Exception as exc:
                raise ExtractionUnavailable(
                    f"Injected client call failed: {exc}",
                    reason=FailureReason.INVALID_RESPONSE,
                    card_ids=ordered_card_ids,
                ) from exc

            if res is not None and not type(res).__name__.endswith("Mock"):
                final_recipe: Optional[RecipeSchema] = None
                if isinstance(res, RecipeSchema):
                    final_recipe = res
                elif hasattr(res, "model_dump"):
                    try:
                        final_recipe = RecipeSchema.model_validate(res.model_dump())
                    except Exception as exc:
                        raise ExtractionUnavailable(
                            f"Injected client model validation failed: {exc}",
                            reason=FailureReason.SCHEMA_VALIDATION,
                            card_ids=ordered_card_ids,
                        ) from exc
                elif isinstance(res, dict):
                    try:
                        final_recipe = RecipeSchema.model_validate(res)
                    except Exception as exc:
                        raise ExtractionUnavailable(
                            f"Injected client dict validation failed: {exc}",
                            reason=FailureReason.SCHEMA_VALIDATION,
                            card_ids=ordered_card_ids,
                        ) from exc

                if final_recipe is not None:
                    if ordered_card_ids and not final_recipe.card_ids:
                        final_recipe.card_ids = ordered_card_ids
                    if final_recipe.card_ids and not final_recipe.image_urls:
                        final_recipe.image_urls = [f"/api/cards/{cid}/image" for cid in final_recipe.card_ids]
                    return final_recipe

            # Injected client yielded nothing usable
            raise ExtractionUnavailable(
                "Injected client yielded no usable recipe",
                reason=FailureReason.INVALID_RESPONSE,
                card_ids=ordered_card_ids,
            )

        # 2. Real Gemini API call if valid API key is present
        is_real_key = bool(self.api_key and not self.api_key.startswith("mock"))
        if not is_real_key:
            raise ExtractionUnavailable(
                "No Gemini API key configured for recipe extraction",
                reason=FailureReason.NO_API_KEY,
                card_ids=ordered_card_ids,
                retryable=False,
            )

        try:
            recipe = await self._call_gemini_extraction_api(
                front_bytes=front_bytes,
                front_id=front_id or "card_front",
                back_bytes=back_bytes,
                back_id=back_id,
                resolved_cards=resolved_cards,
            )
            if ordered_card_ids and not recipe.card_ids:
                recipe.card_ids = ordered_card_ids
            if recipe.card_ids and not recipe.image_urls:
                recipe.image_urls = [f"/api/cards/{cid}/image" for cid in recipe.card_ids]
            return recipe
        except ExtractionUnavailable:
            raise
        except httpx.TimeoutException as exc:
            raise ExtractionUnavailable(
                f"Gemini API recipe extraction timed out: {exc}",
                reason=FailureReason.TIMEOUT,
                card_ids=ordered_card_ids,
                retryable=True,
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 429:
                raise ExtractionUnavailable(
                    f"Gemini API rate limit exceeded (HTTP 429): {exc}",
                    reason=FailureReason.RATE_LIMITED,
                    card_ids=ordered_card_ids,
                    retryable=True,
                ) from exc
            else:
                raise ExtractionUnavailable(
                    f"Gemini API call failed with HTTP status {status}: {exc}",
                    reason=FailureReason.MODEL_UNAVAILABLE,
                    card_ids=ordered_card_ids,
                    retryable=True,
                ) from exc
        except (json.JSONDecodeError, KeyError, IndexError) as exc:
            raise ExtractionUnavailable(
                f"Failed to parse Gemini API response into JSON: {exc}",
                reason=FailureReason.INVALID_RESPONSE,
                card_ids=ordered_card_ids,
                retryable=True,
            ) from exc
        except (pydantic.ValidationError, ValueError) as exc:
            reason = (
                FailureReason.SCHEMA_VALIDATION
                if isinstance(exc, pydantic.ValidationError)
                else FailureReason.INVALID_RESPONSE
            )
            raise ExtractionUnavailable(
                f"Invalid recipe schema from Gemini API: {exc}",
                reason=reason,
                card_ids=ordered_card_ids,
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise ExtractionUnavailable(
                f"Gemini API request failed: {exc}",
                reason=FailureReason.MODEL_UNAVAILABLE,
                card_ids=ordered_card_ids,
                retryable=True,
            ) from exc
        except Exception as exc:
            raise ExtractionUnavailable(
                f"Gemini API recipe extraction failed: {exc}",
                reason=FailureReason.MODEL_UNAVAILABLE,
                card_ids=ordered_card_ids,
                retryable=True,
            ) from exc

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

    async def _call_llm_client_extraction(
        self,
        resolved_cards: List[Tuple[Optional[bytes], Optional[str], Optional[str]]],
        ordered_card_ids: List[str],
    ) -> RecipeSchema:
        """Executes multimodal JSON extraction call using injected LLMClient."""
        try:
            from recipeboxer.ai.gemini import sanitize_prompt_card_id
        except Exception:
            def sanitize_prompt_card_id(cid: Optional[str], default_role: str = "card") -> str:
                if not cid:
                    return default_role
                clean = re.sub(r"[^a-zA-Z0-9_-]", "_", cid)
                if not any(k in clean.lower() for k in ["front", "back", "card", "part", "side", "continuation"]):
                    return f"{default_role}_{clean}"
                return clean

        system_instruction = RECIPE_EXTRACTION_SYSTEM_INSTRUCTION
        effective_thinking = self.thinking_mode
        effective_budget = self.thinking_budget_tokens

        first_bytes = resolved_cards[0][0] if resolved_cards else None
        first_path = resolved_cards[0][1] if resolved_cards else None
        first_id = resolved_cards[0][2] if resolved_cards else None

        if self.adaptive_thinking:
            try:
                mod_input: Any = first_bytes or first_path or first_id
                mod_res = classify_card_modality(mod_input)
                routed = route_prompt_for_modality(mod_res.modality)
                system_instruction = routed.system_instruction
                adaptive_mode, adaptive_budget = compute_adaptive_thinking_budget(mod_res.modality)
                if self.thinking_budget_tokens is None:
                    effective_thinking = adaptive_mode
                    effective_budget = adaptive_budget
            except Exception:
                pass

        parts: List[Union[TextPart, ImagePart]] = [
            TextPart(text="Transcribe and structure the recipe from the provided card images into standard RecipeSchema JSON."),
        ]

        if len(resolved_cards) > 2:
            parts.append(
                TextPart(
                    text=(
                        f"You are transcribing a physical multi-card recipe spanning {len(resolved_cards)} card faces "
                        f"in exact sequential order (Image 1 = Front, Image 2 = Continuation 1, Image 3 = Continuation 2...). "
                        f"Seamlessly merge all ingredient items and numbered preparation steps in order across all card faces without duplication."
                    )
                )
            )

            for idx, (c_bytes, c_path, c_id) in enumerate(resolved_cards):
                role_label = "Front (Page 1)" if idx == 0 else f"Continuation {idx} (Page {idx + 1})"
                if c_bytes:
                    parts.append(ImagePart(data=c_bytes))
                sanitized_cid = sanitize_prompt_card_id(c_id, f"card_{idx + 1}")
                parts.append(TextPart(text=f"Image {idx + 1}: {role_label}: {sanitized_cid}"))
        elif len(resolved_cards) == 2:
            # Front card
            c1_bytes, _, c1_id = resolved_cards[0]
            if c1_bytes:
                parts.append(ImagePart(data=c1_bytes))
            sanitized_c1 = sanitize_prompt_card_id(c1_id, "card_front")
            parts.append(TextPart(text=f"Image A (Front Card): {sanitized_c1}"))

            # Back card
            c2_bytes, _, c2_id = resolved_cards[1]
            if c2_bytes:
                parts.append(ImagePart(data=c2_bytes))
            sanitized_c2 = sanitize_prompt_card_id(c2_id, "card_back")
            parts.append(TextPart(text=f"Image B (Back Card): {sanitized_c2}"))
        elif len(resolved_cards) == 1:
            c1_bytes, _, c1_id = resolved_cards[0]
            if c1_bytes:
                parts.append(ImagePart(data=c1_bytes))
            sanitized_c1 = sanitize_prompt_card_id(c1_id, "card_front")
            parts.append(TextPart(text=f"Image 1 (Front Card): {sanitized_c1}"))

        req = LLMRequest(
            stage=LLMStage.STRUCTURE,
            model=self.model_name,
            system_instruction=system_instruction,
            parts=parts,
            response_model=RecipeSchema,
            temperature=0.1,
            thinking_budget=effective_budget,
        )

        try:
            resp = await self.llm_client.generate(req)
        except StageUnavailableError:
            raise
        except Exception as exc:
            raise ExtractionUnavailable(
                f"Gemini LLMClient recipe extraction failed: {exc}",
                reason=FailureReason.MODEL_UNAVAILABLE,
                card_ids=ordered_card_ids,
            ) from exc

        recipe: Optional[RecipeSchema] = None
        if resp.parsed is not None:
            if isinstance(resp.parsed, RecipeSchema):
                recipe = resp.parsed.model_copy()
            elif isinstance(resp.parsed, dict):
                recipe = RecipeSchema.model_validate(resp.parsed)
            elif hasattr(resp.parsed, "model_dump"):
                recipe = RecipeSchema.model_validate(resp.parsed.model_dump())
        elif resp.raw_text:
            try:
                from recipeboxer.ai.client import _extract_json_text
                cleaned = _extract_json_text(resp.raw_text)
                recipe = RecipeSchema.model_validate(json.loads(cleaned))
            except Exception as exc:
                raise ExtractionUnavailable(
                    f"Failed to parse recipe JSON from LLMClient: {exc}",
                    reason=FailureReason.INVALID_RESPONSE,
                    card_ids=ordered_card_ids,
                ) from exc

        if recipe is None:
            raise ExtractionUnavailable(
                "Model returned empty recipe response",
                reason=FailureReason.INVALID_RESPONSE,
                card_ids=ordered_card_ids,
            )

        if ordered_card_ids and not recipe.card_ids:
            recipe.card_ids = ordered_card_ids
        if recipe.card_ids and not recipe.image_urls:
            recipe.image_urls = [f"/api/cards/{cid}/image" for cid in recipe.card_ids]

        return recipe

    async def _call_gemini_extraction_api(
        self,
        front_bytes: Optional[bytes] = None,
        front_id: Optional[str] = None,
        back_bytes: Optional[bytes] = None,
        back_id: Optional[str] = None,
        resolved_cards: Optional[List[Tuple[Optional[bytes], Optional[str], Optional[str]]]] = None,
    ) -> RecipeSchema:
        """Executes multimodal JSON extraction call to Gemini API."""
        if resolved_cards is None:
            resolved_cards = []
            if front_bytes or front_id:
                resolved_cards.append((front_bytes, None, front_id or "card_front"))
            if back_bytes or back_id:
                resolved_cards.append((back_bytes, None, back_id or "card_back"))

        try:
            from recipeboxer.ai.gemini import sanitize_prompt_card_id
        except Exception:
            def sanitize_prompt_card_id(cid: Optional[str], default_role: str = "card") -> str:
                return default_role if not cid else Path(str(cid).strip()).name

        system_instruction = RECIPE_EXTRACTION_SYSTEM_INSTRUCTION
        effective_thinking = self.thinking_mode
        effective_budget = self.thinking_budget_tokens

        first_id = resolved_cards[0][2] if resolved_cards else front_id
        first_bytes = resolved_cards[0][0] if resolved_cards else front_bytes
        first_path = resolved_cards[0][1] if resolved_cards else None

        if self.adaptive_thinking:
            try:
                mod_input: Any = first_bytes or first_path or first_id
                mod_res = classify_card_modality(mod_input)
                routed = route_prompt_for_modality(mod_res.modality)
                system_instruction = routed.system_instruction
                adaptive_mode, adaptive_budget = compute_adaptive_thinking_budget(mod_res.modality)
                if self.thinking_budget_tokens is None:
                    effective_thinking = adaptive_mode
                    effective_budget = adaptive_budget
            except Exception:
                pass

        parts: List[Dict[str, Any]] = [
            {"text": system_instruction}
        ]

        if len(resolved_cards) > 2:
            parts.append({
                "text": (
                    f"You are transcribing a physical multi-card recipe spanning {len(resolved_cards)} card faces "
                    f"in exact sequential order (Image 1 = Front, Image 2 = Continuation 1, Image 3 = Continuation 2...). "
                    f"Seamlessly merge all ingredient items and numbered preparation steps in order across all card faces without duplication."
                )
            })

            for idx, (c_bytes, c_path, c_id) in enumerate(resolved_cards):
                role_label = "Front (Page 1)" if idx == 0 else f"Continuation {idx} (Page {idx + 1})"
                if c_bytes:
                    b64_c = base64.b64encode(c_bytes).decode("utf-8")
                    parts.append({
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": b64_c,
                        }
                    })
                sanitized_cid = sanitize_prompt_card_id(c_id, f"card_{idx + 1}")
                parts.append({"text": f"Image {idx + 1}: {role_label}: {sanitized_cid}"})
        elif len(resolved_cards) == 2:
            # Front card
            c1_bytes, _, c1_id = resolved_cards[0]
            if c1_bytes:
                b64_front = base64.b64encode(c1_bytes).decode("utf-8")
                parts.append({
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": b64_front,
                    }
                })
            sanitized_c1 = sanitize_prompt_card_id(c1_id, "card_front")
            parts.append({"text": f"Image A (Front Card): {sanitized_c1}"})

            # Back card
            c2_bytes, _, c2_id = resolved_cards[1]
            if c2_bytes:
                b64_back = base64.b64encode(c2_bytes).decode("utf-8")
                parts.append({
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": b64_back,
                    }
                })
            sanitized_c2 = sanitize_prompt_card_id(c2_id, "card_back")
            parts.append({"text": f"Image B (Back Card): {sanitized_c2}"})
        elif len(resolved_cards) == 1:
            c1_bytes, _, c1_id = resolved_cards[0]
            if c1_bytes:
                b64_front = base64.b64encode(c1_bytes).decode("utf-8")
                parts.append({
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": b64_front,
                    }
                })
            sanitized_c1 = sanitize_prompt_card_id(c1_id, "card_front")
            parts.append({"text": f"Image 1 (Front Card): {sanitized_c1}"})

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"

        gen_config: Dict[str, Any] = {
            "response_mime_type": "application/json",
            "temperature": 0.1,
        }

        # Handle thinking mode configuration
        if effective_thinking:
            thinking_cfg: Dict[str, Any] = {}
            if effective_budget is not None:
                thinking_cfg["thinking_budget"] = effective_budget
            if thinking_cfg:
                gen_config["thinking_config"] = thinking_cfg

        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": gen_config,
        }

        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]

            # Strip markdown code blocks if present
            cleaned_text = re.sub(r"^```(?:json)?\s*", "", raw_text.strip(), flags=re.IGNORECASE)
            cleaned_text = re.sub(r"\s*```$", "", cleaned_text.strip()).strip()

            parsed_json = json.loads(cleaned_text)
            recipe = RecipeSchema.model_validate(parsed_json)
            recipe.card_ids = [cid for _, _, cid in resolved_cards if cid]
            recipe.image_urls = [f"/api/cards/{cid}/image" for cid in recipe.card_ids]
            return recipe


# Alias for backward compatibility
RecipeExtractor = GeminiRecipeExtractor
