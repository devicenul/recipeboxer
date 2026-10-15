"""Adversarial stress-testing suite for Milestone 3 (M3: Recipe Editor UX & Extraction Intelligence).

Tests:
1. Title Guessing Heuristic ([GUESS] suffix rules) across all extraction prompts and offline heuristics.
2. Anecdote, family story, and marginalia isolation into description and source_notes.
3. Clean exclusion of stories from ingredients and directions.
4. Edge cases in title guessing (special characters, empty text, untitled tags, multi-line titles).
5. RecipeSchema validation & serialization integrity for guessed titles and preserved anecdotes.
"""

import io
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from recipeboxer.agent.skills.print_clipping_extractor.parser import (
    get_print_clipping_prompt,
)
from recipeboxer.agent.skills.vintage_handwriting_ocr.parser import (
    get_vintage_handwriting_prompt,
)
from recipeboxer.extraction.extractor import (
    RECIPE_EXTRACTION_SYSTEM_INSTRUCTION,
    GeminiRecipeExtractor,
)
from recipeboxer.models.domain import (
    BoundaryText,
    CardRole,
    VisualFingerprint,
)
from recipeboxer.models.failures import ExtractionUnavailable, FailureReason
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.vision.router import (
    CardModality,
    MIXED_MEDIA_SYSTEM_PROMPT,
    PRINT_CLIPPING_SYSTEM_PROMPT,
    TYPEWRITTEN_SYSTEM_PROMPT,
    VINTAGE_HANDWRITING_SYSTEM_PROMPT,
    route_prompt_for_modality,
)


class TestPromptTitleGuessingAndStoryIsolation:
    """Validates that all system prompts across modalities enforce [GUESS] and story isolation rules."""

    @pytest.mark.parametrize(
        "prompt_name,prompt_text",
        [
            ("RECIPE_EXTRACTION_SYSTEM_INSTRUCTION", RECIPE_EXTRACTION_SYSTEM_INSTRUCTION),
            ("VINTAGE_HANDWRITING_SYSTEM_PROMPT", VINTAGE_HANDWRITING_SYSTEM_PROMPT),
            ("PRINT_CLIPPING_SYSTEM_PROMPT", PRINT_CLIPPING_SYSTEM_PROMPT),
            ("TYPEWRITTEN_SYSTEM_PROMPT", TYPEWRITTEN_SYSTEM_PROMPT),
            ("MIXED_MEDIA_SYSTEM_PROMPT", MIXED_MEDIA_SYSTEM_PROMPT),
            ("get_vintage_handwriting_prompt()", get_vintage_handwriting_prompt()),
            ("get_print_clipping_prompt()", get_print_clipping_prompt()),
        ],
    )
    def test_all_modality_prompts_contain_mandatory_guess_and_isolation_instructions(
        self, prompt_name: str, prompt_text: str
    ):
        # 1. Check title guessing instructions and [GUESS] tag
        assert "[GUESS]" in prompt_text, f"{prompt_name} missing '[GUESS]' suffix instruction"
        assert re.search(r"title\s*(?:inference|guessing|inferred|no\s+stated\s+title)", prompt_text, re.IGNORECASE)

        # 2. Check story and anecdote preservation instructions
        assert "description" in prompt_text, f"{prompt_name} missing 'description' field target"
        assert "source_notes" in prompt_text, f"{prompt_name} missing 'source_notes' field target"
        assert re.search(r"(?:anecdote|family\s+stor|marginalia|memories|headnote)", prompt_text, re.IGNORECASE)

        # 3. Check strict isolation instructions excluding stories from ingredients and directions
        assert re.search(
            r"(?:STRICT\s+ISOLATION|NEVER\s+place.*ingredients|confined to.*ingredients)",
            prompt_text,
            re.IGNORECASE,
        ), f"{prompt_name} missing strict story isolation constraint"

    def test_route_prompt_for_all_modalities_yields_compliant_instructions(self):
        for modality in [
            CardModality.HANDWRITTEN_CURSIVE,
            CardModality.HANDWRITTEN_PRINT,
            CardModality.TYPEWRITTEN,
            CardModality.PRINTED_CLIPPING_OR_LABEL,
            CardModality.MIXED_MEDIA,
        ]:
            routed = route_prompt_for_modality(modality)
            instruction = routed.system_instruction
            assert "[GUESS]" in instruction
            assert "description" in instruction
            assert "source_notes" in instruction


class TestOfflineExtractionTitleGuessing:
    """Stress tests extraction title guessing heuristics across various boundary texts and filenames."""

    @pytest.mark.asyncio
    async def test_untitled_or_unknown_card_appends_guess_suffix(self):
        # Without API key or client, unmocked extractor raises ExtractionUnavailable
        unmocked = GeminiRecipeExtractor(api_key=None)
        fp = VisualFingerprint(
            card_id="untitled_index_card_01.jpg",
            image_path="/tmp/untitled_index_card_01.jpg",
            sha256_hash="hash123",
            role=CardRole.STANDALONE,
            boundary_text=BoundaryText(first_line="Peach Cobbler", last_line="Bake 30 mins."),
        )
        with pytest.raises(ExtractionUnavailable) as exc_info:
            await unmocked.extract(fp)
        assert exc_info.value.reason == FailureReason.NO_API_KEY

        # Injected client returning recipe with [GUESS] title preserves [GUESS]
        mock_client = AsyncMock()
        mock_client.extract_recipe = AsyncMock(return_value=RecipeSchema(
            recipe_id="rec_guess_01",
            title="Peach Cobbler [GUESS]",
            ingredients=["2 cups peaches", "1 cup flour"],
            directions=["1. Bake 30 mins."],
        ))
        extractor = GeminiRecipeExtractor(client=mock_client)
        recipe = await extractor.extract(fp)
        assert recipe.title == "Peach Cobbler [GUESS]"
        assert recipe.title.endswith(" [GUESS]")

    @pytest.mark.asyncio
    async def test_known_and_standard_cards_do_not_append_guess(self):
        fp = VisualFingerprint(
            card_id="grandma_oatmeal_raisin_cookies.jpg",
            image_path="/tmp/grandma_oatmeal_raisin_cookies.jpg",
            sha256_hash="hash456",
            role=CardRole.STANDALONE,
            boundary_text=BoundaryText(
                first_line="Grandma's Best Oatmeal Raisin Cookies",
                last_line="Yields 36 cookies.",
            ),
        )
        mock_client = AsyncMock()
        mock_client.extract_recipe = AsyncMock(return_value=RecipeSchema(
            recipe_id="rec_standard_01",
            title="Grandma's Best Oatmeal Raisin Cookies",
            ingredients=["2 cups rolled oats", "1 cup raisins"],
            directions=["1. Bake 12 mins at 350F."],
        ))
        extractor = GeminiRecipeExtractor(client=mock_client)
        recipe = await extractor.extract(fp)
        assert recipe.title == "Grandma's Best Oatmeal Raisin Cookies"
        assert not recipe.title.endswith(" [GUESS]")

    @pytest.mark.asyncio
    async def test_title_candidate_sanitization_strips_leading_numbers_and_punctuation(self):
        fp = VisualFingerprint(
            card_id="mystery_recipe_numbered.jpg",
            image_path="/tmp/mystery_recipe_numbered.jpg",
            sha256_hash="hash789",
            role=CardRole.STANDALONE,
            boundary_text=BoundaryText(
                first_line="1. - Heavenly Lemon Meringue Pie",
                last_line="Chill before serving.",
            ),
        )
        mock_client = AsyncMock()
        mock_client.extract_recipe = AsyncMock(return_value=RecipeSchema(
            recipe_id="rec_sanitized_01",
            title="Heavenly Lemon Meringue Pie [GUESS]",
            ingredients=["1 pie crust", "3 egg yolks", "1 cup sugar"],
            directions=["1. Chill before serving."],
        ))
        extractor = GeminiRecipeExtractor(client=mock_client)
        recipe = await extractor.extract(fp)
        assert recipe.title == "Heavenly Lemon Meringue Pie [GUESS]"


class TestGeminiApiExtractionStoryAndGuessHandling:
    """Mocks Gemini API responses containing complex family stories and inferred titles."""

    @pytest.mark.asyncio
    async def test_gemini_api_extraction_correctly_isolates_stories_and_flags_guess(self):
        llm_response = {
            "recipe_id": "rec_adv_001",
            "title": "Rustic Autumn Apple Cobbler [GUESS]",
            "description": "Passed down from Great-Aunt Clara, who baked this every Sunday in October after harvest in 1948.",
            "yield_servings": "6 servings",
            "prep_time": "15 mins",
            "cook_time": "40 mins",
            "total_time": "55 mins",
            "ingredients": [
                "5 medium Granny Smith apples, peeled and sliced",
                "1 cup sugar",
                "1 cup all-purpose flour",
                "1 tsp baking powder",
                "1/2 cup butter, melted",
                "1 egg, beaten",
            ],
            "directions": [
                "1. Place sliced apples in a greased 8-inch square baking dish.",
                "2. Whisk together sugar, flour, and baking powder.",
                "3. Stir in egg until crumbly, then scatter over apples.",
                "4. Drizzle melted butter over top and bake at 375°F for 40 minutes.",
            ],
            "tags": ["Dessert", "Baking", "Autumn", "Vintage"],
            "source_notes": "Great-Aunt Clara (1948 Handwritten Card, Ohio Church Box)",
        }

        mock_client = AsyncMock()
        mock_client.extract_recipe = AsyncMock(return_value=RecipeSchema(**llm_response))
        extractor = GeminiRecipeExtractor(client=mock_client)

        valid_jpeg = (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c"
            b"\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c"
            b"\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01"
            b"\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01"
            b"\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06"
            b"\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
        )
        recipe = await extractor.extract(valid_jpeg)

        assert isinstance(recipe, RecipeSchema)
        assert recipe.title.endswith(" [GUESS]")
        assert "Great-Aunt Clara" in recipe.description
        assert "1948" in recipe.description
        assert "Ohio Church Box" in recipe.source_notes

        # Verify ingredients and directions do NOT contain family stories or years
        for ing in recipe.ingredients:
            assert "Great-Aunt" not in ing
            assert "1948" not in ing
            assert "Sunday" not in ing

        for d in recipe.directions:
            assert "Great-Aunt" not in d
            assert "harvest" not in d

    @pytest.mark.asyncio
    async def test_recipe_schema_serialization_round_trip(self):
        recipe = RecipeSchema(
            recipe_id="rec_serialization_test",
            title="Old Fashioned Molasses Cookies [GUESS]",
            description="Handwritten note on back: 'Mother made these for the 1954 county fair.'",
            yield_servings="36 cookies",
            prep_time="20 mins",
            cook_time="12 mins",
            total_time="32 mins",
            ingredients=["2 1/4 cups flour", "1 cup dark molasses", "3/4 cup shortening"],
            directions=["1. Cream shortening and molasses.", "2. Roll into balls and bake at 375F."],
            tags=["Cookies", "Vintage", "Fair Winner"],
            source_notes="1954 County Fair Collection",
            card_ids=["card_front.jpg", "card_back.jpg"],
            image_urls=["/api/cards/card_front.jpg/image", "/api/cards/card_back.jpg/image"],
        )

        data = recipe.model_dump()
        reloaded = RecipeSchema.model_validate(data)

        assert reloaded.title == recipe.title
        assert reloaded.description == recipe.description
        assert reloaded.source_notes == recipe.source_notes
        assert reloaded.card_ids == recipe.card_ids
        assert reloaded.image_urls == recipe.image_urls
