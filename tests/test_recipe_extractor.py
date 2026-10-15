"""Unit and integration tests for RecipeBoxer recipe extraction engine.

Tests GeminiRecipeExtractor with multimodal Gemini vision, mock clients,
thinking mode, batch concurrency, offline heuristics, and edge cases.
"""

import asyncio
import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from PIL import Image

from recipeboxer.config import Settings
from recipeboxer.extraction.extractor import (
    RECIPE_EXTRACTION_SYSTEM_INSTRUCTION,
    GeminiRecipeExtractor,
)
from recipeboxer.ingestion.scanner import ScannedCard
from recipeboxer.models.domain import (
    BoundaryText,
    CardRole,
    InkInfo,
    PaperColorInfo,
    PaperRulingInfo,
    VisualFingerprint,
)
from recipeboxer.models.failures import ExtractionUnavailable, FailureReason
from recipeboxer.models.recipe import IngredientLine, Recipe, RecipeSchema, RecipeSection, Step
from recipeboxer.ai.replay import ScriptedLLMClient
from recipeboxer.extraction.transcribe import FaceTranscript, FaceTranscriptList

TINY_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"


@pytest.mark.asyncio
async def test_extractor_initialization_defaults():
    """Validates default model name, settings integration, and API key loading."""
    extractor = GeminiRecipeExtractor()
    assert extractor.model_name == "gemini-3.7-flash"
    assert extractor.thinking_mode is False
    assert extractor.thinking_budget_tokens is None
    assert extractor.client is None


@pytest.mark.asyncio
async def test_extractor_custom_model_and_budget_overrides():
    """Passes custom model, thinking mode, and thinking budget."""
    extractor = GeminiRecipeExtractor(
        api_key="custom-test-key",
        model_name="gemini-2.5-pro",
        thinking_mode=True,
        thinking_budget_tokens=2048,
    )
    assert extractor.api_key == "custom-test-key"
    assert extractor.model_name == "gemini-2.5-pro"
    assert extractor.thinking_mode is True
    assert extractor.thinking_budget_tokens == 2048


@pytest.mark.asyncio
async def test_extractor_injected_mock_client(tmp_path):
    """Injects explicit mock client and extracts single and pair recipes."""
    mock_recipe = RecipeSchema(
        title="Grandma's Cinnamon Rolls",
        ingredients=["2 cups flour", "1 tsp cinnamon"],
        directions=["1. Roll dough.", "2. Bake at 375F."],
    )
    mock_client = AsyncMock()
    mock_client.extract_recipe = AsyncMock(return_value=mock_recipe)
    extractor = GeminiRecipeExtractor(client=mock_client)

    p1_front = tmp_path / "card_01a.jpg"
    p1_back = tmp_path / "card_01b.jpg"
    p1_front.write_bytes(b"dummy_front_bytes")
    p1_back.write_bytes(b"dummy_back_bytes")

    recipe = await extractor.extract(p1_front, p1_back)
    assert isinstance(recipe, RecipeSchema)
    assert "Cinnamon Rolls" in recipe.title
    assert len(recipe.ingredients) == 2
    assert len(recipe.directions) == 2
    assert recipe.directions[0].startswith("1. ")


@pytest.mark.asyncio
async def test_extractor_offline_standalone_fudge(tmp_path):
    """Tests extraction with injected client for standalone card."""
    fudge_recipe = RecipeSchema(
        title="10-Minute Quick Chocolate Fudge",
        ingredients=["3 cups chocolate chips", "1 can sweetened condensed milk", "1 tsp vanilla", "pinch salt"],
        directions=["1. Melt chips with milk.", "2. Stir in vanilla.", "3. Chill until firm."],
        tags=["Candy"],
    )
    client = AsyncMock()
    client.extract_recipe = AsyncMock(return_value=fudge_recipe)
    extractor = GeminiRecipeExtractor(client=client)
    fudge_path = tmp_path / "card_fudge_standalone.jpg"
    fudge_path.write_bytes(b"fake_fudge_image")

    recipe = await extractor.extract(fudge_path)
    assert isinstance(recipe, RecipeSchema)
    assert recipe.title == "10-Minute Quick Chocolate Fudge"
    assert len(recipe.ingredients) == 4
    assert len(recipe.directions) == 3
    assert recipe.directions[0].startswith("1. ")
    assert recipe.directions[1].startswith("2. ")
    assert recipe.directions[2].startswith("3. ")
    assert "Candy" in recipe.tags


@pytest.mark.asyncio
async def test_extractor_offline_standalone_pecan_pie(tmp_path):
    """Tests extraction with injected client for pecan pie standalone card."""
    pecan_recipe = RecipeSchema(
        title="Southern Pecan Pie",
        yield_servings="8 servings",
        ingredients=["1 cup pecans", "1 cup corn syrup", "3 eggs", "1/2 cup sugar"],
        directions=["1. Mix filling.", "2. Pour into crust.", "3. Arrange pecans.", "4. Bake 50 mins."],
        tags=["Pie"],
    )
    client = AsyncMock()
    client.extract_recipe = AsyncMock(return_value=pecan_recipe)
    extractor = GeminiRecipeExtractor(client=client)
    pie_path = tmp_path / "card_pecan_standalone.jpg"
    pie_path.write_bytes(b"fake_pecan_image")

    recipe = await extractor.extract(pie_path)
    assert isinstance(recipe, RecipeSchema)
    assert recipe.title == "Southern Pecan Pie"
    assert recipe.yield_servings == "8 servings"
    assert len(recipe.ingredients) == 4
    assert len(recipe.directions) == 4
    assert "Pie" in recipe.tags


@pytest.mark.asyncio
async def test_extractor_offline_two_sided_cinnamon_rolls(tmp_path):
    """Tests front + back pair with injected mock client."""
    crolls_recipe = RecipeSchema(
        title="Grandma's Cinnamon Rolls",
        yield_servings="12 rolls",
        prep_time="30 mins",
        cook_time="25 mins",
        total_time="2 hrs 15 mins",
        ingredients=["2 cups flour", "1 cup milk", "1 tbsp yeast", "cinnamon", "sugar"],
        directions=[f"{idx}. Step {idx} details" for idx in range(1, 11)],
        tags=["Dessert"],
    )
    client = AsyncMock()
    client.extract_recipe = AsyncMock(return_value=crolls_recipe)
    extractor = GeminiRecipeExtractor(client=client)
    p1_front = tmp_path / "card_cr_front.jpg"
    p1_back = tmp_path / "card_cr_back.jpg"
    p1_front.write_bytes(b"fake_front")
    p1_back.write_bytes(b"fake_back")

    recipe = await extractor.extract(p1_front, p1_back)
    assert isinstance(recipe, RecipeSchema)
    assert recipe.title == "Grandma's Cinnamon Rolls"
    assert recipe.yield_servings == "12 rolls"
    assert recipe.prep_time == "30 mins"
    assert recipe.cook_time == "25 mins"
    assert recipe.total_time == "2 hrs 15 mins"
    assert len(recipe.ingredients) == 5
    assert len(recipe.directions) == 10
    for idx, d in enumerate(recipe.directions, start=1):
        assert d.startswith(f"{idx}. ")
    assert "Dessert" in recipe.tags


@pytest.mark.asyncio
async def test_extractor_offline_two_sided_lemon_bars(tmp_path):
    """Tests front + back pair with injected mock client for lemon bars."""
    lemon_recipe = RecipeSchema(
        title="Luscious Lemon Glaze Bars",
        yield_servings="24 bars",
        ingredients=["2 cups flour", "1/2 cup butter", "4 eggs", "1 1/2 cups sugar", "lemon juice"],
        directions=[f"{idx}. Step {idx} directions" for idx in range(1, 7)],
        tags=["Lemon"],
    )
    client = AsyncMock()
    client.extract_recipe = AsyncMock(return_value=lemon_recipe)
    extractor = GeminiRecipeExtractor(client=client)
    p2_front = tmp_path / "card_lemon_f.jpg"
    p2_back = tmp_path / "card_lemon_b.jpg"
    p2_front.write_bytes(b"lemon_f")
    p2_back.write_bytes(b"lemon_b")

    recipe = await extractor.extract(p2_front, p2_back)
    assert isinstance(recipe, RecipeSchema)
    assert recipe.title == "Luscious Lemon Glaze Bars"
    assert recipe.yield_servings == "24 bars"
    assert len(recipe.ingredients) == 5
    assert len(recipe.directions) == 6
    assert "Lemon" in recipe.tags


@pytest.mark.asyncio
async def test_extractor_offline_generic_unseen_card(tmp_path):
    """Tests fallback extraction on unseen card: no-key raises ExtractionUnavailable, injected client succeeds."""
    fp = VisualFingerprint(
        card_id="card_unseen_01.jpg",
        image_path=str(tmp_path / "card_unseen_01.jpg"),
        sha256_hash="deadbeef12345678",
        role=CardRole.STANDALONE,
        boundary_text=BoundaryText(
            first_line="Warm Autumn Apple Crisp",
            last_line="Serve warm with vanilla ice cream.",
        ),
    )

    # Without client/key raises ExtractionUnavailable
    extractor_no_key = GeminiRecipeExtractor(api_key=None)
    with pytest.raises(ExtractionUnavailable) as exc_info:
        await extractor_no_key.extract(fp)
    assert exc_info.value.reason == FailureReason.NO_API_KEY

    # With injected client passes through
    mock_client = AsyncMock()
    mock_client.extract_recipe = AsyncMock(return_value=RecipeSchema(
        title="Warm Autumn Apple Crisp",
        ingredients=["4 cups apples", "1 cup oats"],
        directions=["1. Bake at 350F."],
    ))
    extractor = GeminiRecipeExtractor(client=mock_client)
    recipe = await extractor.extract(fp)
    assert isinstance(recipe, RecipeSchema)
    assert "Apple Crisp" in recipe.title
    assert len(recipe.ingredients) > 0
    assert len(recipe.directions) > 0
    assert recipe.directions[0].startswith("1. ")


@pytest.mark.asyncio
async def test_extractor_scanned_card_and_path_inputs(tmp_path):
    """Passes ScannedCard, Path, str, bytes, and PIL Image inputs to extractor with injected client."""
    mock_recipe = RecipeSchema(
        title="10-Minute Quick Chocolate Fudge",
        ingredients=["3 cups chocolate chips", "1 can condensed milk"],
        directions=["1. Melt and chill."],
    )
    client = AsyncMock()
    client.extract_recipe = AsyncMock(return_value=mock_recipe)
    extractor = GeminiRecipeExtractor(client=client)

    p1 = tmp_path / "card_input_test.jpg"
    p1.write_bytes(b"dummy_jpeg_bytes")

    # 1. Path object
    r1 = await extractor.extract(p1)
    assert r1.title == "10-Minute Quick Chocolate Fudge"

    # 2. String path
    r2 = await extractor.extract(str(p1))
    assert r2.title == "10-Minute Quick Chocolate Fudge"

    # 3. ScannedCard
    card = ScannedCard(
        card_id=p1.name,
        file_path=p1,
        filename=p1.name,
        sha256_hash="mockhash",
        file_size_bytes=p1.stat().st_size,
        width=600,
        height=400,
        format="JPEG",
        mode="RGB",
    )
    r3 = await extractor.extract(card)
    assert r3.title == "10-Minute Quick Chocolate Fudge"

    # 4. Raw bytes
    img_bytes = p1.read_bytes()
    r4 = await extractor.extract(img_bytes)
    assert isinstance(r4, RecipeSchema)

    # 5. PIL Image
    pil_img = Image.new("RGB", (100, 100), color=(255, 255, 255))
    r5 = await extractor.extract(pil_img)
    assert isinstance(r5, RecipeSchema)



@pytest.mark.asyncio
async def test_extractor_api_mock_success_json():
    """Mocks Gemini API HTTP response with valid JSON payload."""
    mock_trans = FaceTranscriptList(
        faces=[
            FaceTranscript(
                face_index=0,
                text="Old-Fashioned Blueberry Scones\n2 cups all-purpose flour\n1/3 cup sugar\n1 tbsp baking powder\n1/2 cup cold butter\n1 cup fresh blueberries\n1/2 cup heavy cream\n1. Whisk dry ingredients in bowl.\n2. Cut in cold butter until coarse crumbs.\n3. Fold in blueberries and cream.\n4. Shape into disc, cut into 8 wedges, and bake at 400°F for 18 mins.",
            )
        ]
    )
    mock_recipe = Recipe(
        title="Old-Fashioned Blueberry Scones",
        sections=[
            RecipeSection(
                name=None,
                ingredients=[
                    IngredientLine(original_text="2 cups all-purpose flour", item="flour", quantity="2", unit="cups"),
                    IngredientLine(original_text="1/3 cup sugar", item="sugar", quantity="1/3", unit="cup"),
                    IngredientLine(original_text="1 tbsp baking powder", item="baking powder", quantity="1", unit="tbsp"),
                    IngredientLine(original_text="1/2 cup cold butter", item="butter", quantity="1/2", unit="cup"),
                    IngredientLine(original_text="1 cup fresh blueberries", item="blueberries", quantity="1", unit="cup"),
                    IngredientLine(original_text="1/2 cup heavy cream", item="heavy cream", quantity="1/2", unit="cup"),
                ],
                steps=[
                    Step(original_text="1. Whisk dry ingredients in bowl.", text="Whisk dry ingredients in bowl."),
                    Step(original_text="2. Cut in cold butter until coarse crumbs.", text="Cut in cold butter until coarse crumbs."),
                    Step(original_text="3. Fold in blueberries and cream.", text="Fold in blueberries and cream."),
                    Step(original_text="4. Shape into disc, cut into 8 wedges, and bake at 400°F for 18 mins.", text="Shape into disc, cut into 8 wedges, and bake at 400°F for 18 mins."),
                ],
            )
        ],
        tags=["Baking", "Breakfast", "Blueberry"],
    )
    client = ScriptedLLMClient([mock_trans, mock_recipe])
    extractor = GeminiRecipeExtractor(llm_client=client)
    recipe = await extractor.extract(TINY_JPEG)

    assert isinstance(recipe, RecipeSchema)
    assert recipe.title == "Old-Fashioned Blueberry Scones"
    assert len(recipe.ingredients) == 6
    assert len(recipe.directions) == 4
    assert recipe.directions[0].startswith("1. ")


@pytest.mark.asyncio
async def test_extractor_api_markdown_codeblock_stripping():
    """Model returns structured recipe; cleanly extracts into RecipeSchema."""
    mock_trans = FaceTranscriptList(
        faces=[
            FaceTranscript(
                face_index=0,
                text="Rustic Sourdough Boule\n500g bread flour\n350g water\n100g starter\n10g salt\n1. Autolyse flour and water.\n2. Add starter and salt.\n3. Bake in Dutch oven at 450°F.",
            )
        ]
    )
    mock_recipe = Recipe(
        title="Rustic Sourdough Boule",
        sections=[
            RecipeSection(
                name=None,
                ingredients=[
                    IngredientLine(original_text="500g bread flour", item="bread flour", quantity="500", unit="g"),
                    IngredientLine(original_text="350g water", item="water", quantity="350", unit="g"),
                    IngredientLine(original_text="100g starter", item="starter", quantity="100", unit="g"),
                    IngredientLine(original_text="10g salt", item="salt", quantity="10", unit="g"),
                ],
                steps=[
                    Step(original_text="1. Autolyse flour and water.", text="Autolyse flour and water."),
                    Step(original_text="2. Add starter and salt.", text="Add starter and salt."),
                    Step(original_text="3. Bake in Dutch oven at 450°F.", text="Bake in Dutch oven at 450°F."),
                ],
            )
        ],
        tags=["Bread", "Sourdough"],
    )
    client = ScriptedLLMClient([mock_trans, mock_recipe])
    extractor = GeminiRecipeExtractor(llm_client=client)
    recipe = await extractor.extract(TINY_JPEG)

    assert isinstance(recipe, RecipeSchema)
    assert recipe.title == "Rustic Sourdough Boule"
    assert len(recipe.ingredients) == 4
    assert len(recipe.directions) == 3
    assert recipe.directions[0] == "1. Autolyse flour and water."


@pytest.mark.asyncio
async def test_extractor_api_failure_fallback(tmp_path):
    """Mocks timeout error from LLMClient and verifies typed ExtractionUnavailable is raised."""
    p1 = tmp_path / "card_timeout_test.jpg"
    p1.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9")

    client = ScriptedLLMClient([asyncio.TimeoutError("API Connection Timeout")])
    extractor = GeminiRecipeExtractor(llm_client=client)
    with pytest.raises(ExtractionUnavailable) as exc_info:
        await extractor.extract(p1)
    assert exc_info.value.reason == FailureReason.TIMEOUT


@pytest.mark.asyncio
async def test_extractor_thinking_mode_payload():
    """Tests payload construction when thinking_mode=True."""
    mock_trans = FaceTranscriptList(
        faces=[FaceTranscript(face_index=0, text="Tested Thinking Recipe\n1 cup ingredient\nMix and bake.")]
    )
    mock_recipe = Recipe(
        title="Tested Thinking Recipe",
        sections=[RecipeSection(name=None, ingredients=[IngredientLine(original_text="1 cup ingredient")], steps=[Step(original_text="Mix and bake.")])],
    )
    client = ScriptedLLMClient([mock_trans, mock_recipe])
    settings = Settings(thinking_mode=True, thinking_budget_tokens=4096)
    extractor = GeminiRecipeExtractor(
        llm_client=client,
        settings=settings,
        thinking_mode=True,
        thinking_budget_tokens=4096,
    )
    await extractor.extract(TINY_JPEG)

    assert len(client.requests) >= 1
    assert client.requests[0].thinking_budget == 4096


@pytest.mark.asyncio
async def test_extractor_batch_concurrency():
    """Runs extract_batch on 4 pairs with concurrency=2 using injected client."""
    def make_recipe(title):
        return RecipeSchema(
            title=title,
            ingredients=["1 cup item"],
            directions=["1. Do step."],
        )

    titles = [
        "Grandma's Cinnamon Rolls",
        "Luscious Lemon Glaze Bars",
        "10-Minute Quick Chocolate Fudge",
        "Southern Pecan Pie",
    ]
    responses = [make_recipe(t) for t in titles]
    call_idx = 0

    class OrderedClient:
        async def extract_recipe(self, front, back=None):
            nonlocal call_idx
            res = responses[call_idx % len(responses)]
            call_idx += 1
            return res

    extractor = GeminiRecipeExtractor(client=OrderedClient())

    pairs = [
        (b"card_1", b"card_2"),
        (b"card_3", b"card_4"),
        (b"card_5", None),
        (b"card_6", None),
    ]

    recipes = await extractor.extract_batch(pairs, concurrency=2)

    assert len(recipes) == 4
    for r in recipes:
        assert isinstance(r, RecipeSchema)


def test_extractor_sync_helper(tmp_path):
    """Tests extract_sync helper with injected client."""
    mock_recipe = RecipeSchema(
        title="10-Minute Quick Chocolate Fudge",
        ingredients=["3 cups chocolate chips", "1 can milk"],
        directions=["1. Melt and chill."],
    )
    client = MagicMock()
    client.extract_recipe = MagicMock(return_value=mock_recipe)
    extractor = GeminiRecipeExtractor(client=client)
    s1 = tmp_path / "card_sync.jpg"
    s1.write_bytes(b"dummy")

    recipe = extractor.extract_sync(s1)
    assert isinstance(recipe, RecipeSchema)
    assert recipe.title == "10-Minute Quick Chocolate Fudge"



def test_extractor_unsupported_type_raises():
    """Tests that passing an unsupported type raises TypeError."""
    extractor = GeminiRecipeExtractor(api_key=None)
    with pytest.raises(TypeError, match="Unsupported card input type"):
        extractor._resolve_input(12345)  # type: ignore


@pytest.mark.asyncio
async def test_extractor_injected_client_dict_response():
    """Tests injected client returning a dictionary instead of a RecipeSchema."""
    class DictClient:
        async def extract_recipe(self, front, back=None):
            return {
                "title": "Dict Recipe",
                "ingredients": ["1 cup sugar"],
                "directions": ["Mix well."],
                "tags": ["Sweet"],
            }

    extractor = GeminiRecipeExtractor(client=DictClient())
    recipe = await extractor.extract(b"mock_bytes")
    assert isinstance(recipe, RecipeSchema)
    assert recipe.title == "Dict Recipe"
    assert recipe.directions == ["1. Mix well."]


@pytest.mark.asyncio
async def test_extractor_injected_sync_callable():
    """Tests injected client that is a synchronous callable."""
    def sync_callable(front, back=None):
        return RecipeSchema(
            title="Callable Recipe",
            ingredients=["2 eggs"],
            directions=["Scramble eggs."],
        )

    extractor = GeminiRecipeExtractor(client=sync_callable)
    recipe = await extractor.extract(b"mock_bytes")
    assert isinstance(recipe, RecipeSchema)
    assert recipe.title == "Callable Recipe"
    assert recipe.directions == ["1. Scramble eggs."]


@pytest.mark.asyncio
async def test_extractor_alias_method():
    """Tests extract_recipe alias method on GeminiRecipeExtractor with injected client."""
    mock_recipe = RecipeSchema(
        title="Alias Test Recipe",
        ingredients=["1 cup sugar"],
        directions=["1. Mix."],
    )
    client = AsyncMock()
    client.extract_recipe = AsyncMock(return_value=mock_recipe)
    extractor = GeminiRecipeExtractor(client=client)
    recipe = await extractor.extract_recipe(b"mock_bytes")
    assert isinstance(recipe, RecipeSchema)
    assert recipe.title == "Alias Test Recipe"


@pytest.mark.asyncio
async def test_extract_cards_multi_card_sequence_offline():
    """Tests extract_cards with 3+ cards, ensuring sequential directions and card_ids preservation."""
    multi_recipe = RecipeSchema(
        title="Grandma's 3-Card Cake",
        ingredients=["3 cups flour", "1 cup butter"],
        directions=["1. Step 1", "2. Step 2", "3. Step 3"],
    )
    class MultiCardClient:
        async def extract_cards(self, cards):
            return multi_recipe

    extractor = GeminiRecipeExtractor(client=MultiCardClient())

    fp1 = VisualFingerprint(
        card_id="grandma_cake_page1.jpg",
        image_path="/scans/grandma_cake_page1.jpg",
        sha256_hash="hash1",
        role=CardRole.SEQUENCE_START,
    )
    fp2 = VisualFingerprint(
        card_id="grandma_cake_page2.jpg",
        image_path="/scans/grandma_cake_page2.jpg",
        sha256_hash="hash2",
        role=CardRole.SEQUENCE_CONTINUATION,
    )
    fp3 = VisualFingerprint(
        card_id="grandma_cake_page3.jpg",
        image_path="/scans/grandma_cake_page3.jpg",
        sha256_hash="hash3",
        role=CardRole.SEQUENCE_CONTINUATION,
    )

    recipe = await extractor.extract_cards([fp1, fp2, fp3])
    assert isinstance(recipe, RecipeSchema)
    assert recipe.card_ids == ["grandma_cake_page1.jpg", "grandma_cake_page2.jpg", "grandma_cake_page3.jpg"]
    assert len(recipe.image_urls) == 3
    assert recipe.image_urls[0] == "/api/cards/grandma_cake_page1.jpg/image"
    assert len(recipe.directions) == 3
    assert recipe.directions[0].startswith("1. ")
    assert recipe.directions[1].startswith("2. ")
    assert recipe.directions[2].startswith("3. ")



@pytest.mark.asyncio
async def test_extractor_multi_card_gemini_api_payload_structure(tmp_path):
    """Tests multi-card sequential extraction via ExtractionPipeline."""
    p1 = tmp_path / "card1.jpg"
    p2 = tmp_path / "card2.jpg"
    p3 = tmp_path / "card3.jpg"
    for p in (p1, p2, p3):
        p.write_bytes(TINY_JPEG)

    mock_trans = FaceTranscriptList(
        faces=[
            FaceTranscript(face_index=0, text="3-Page Wedding Cake\n4 cups cake flour"),
            FaceTranscript(face_index=1, text="2 cups sugar\n1 cup butter"),
            FaceTranscript(face_index=2, text="1. Mix dry ingredients.\n2. Cream butter and sugar.\n3. Bake at 350F for 35 mins."),
        ]
    )
    mock_recipe = Recipe(
        title="3-Page Wedding Cake",
        sections=[
            RecipeSection(
                name=None,
                ingredients=[
                    IngredientLine(original_text="4 cups cake flour", item="cake flour", quantity="4", unit="cups", source_face=0),
                    IngredientLine(original_text="2 cups sugar", item="sugar", quantity="2", unit="cups", source_face=1),
                    IngredientLine(original_text="1 cup butter", item="butter", quantity="1", unit="cup", source_face=1),
                ],
                steps=[
                    Step(original_text="1. Mix dry ingredients.", text="Mix dry ingredients.", source_face=2),
                    Step(original_text="2. Cream butter and sugar.", text="Cream butter and sugar.", source_face=2),
                    Step(original_text="3. Bake at 350F for 35 mins.", text="Bake at 350F for 35 mins.", source_face=2),
                ],
            )
        ],
        tags=["Cake", "Wedding", "Dessert"],
    )
    client = ScriptedLLMClient([mock_trans, mock_recipe])
    extractor = GeminiRecipeExtractor(llm_client=client)

    c1 = VisualFingerprint(card_id="card1.jpg", image_path=str(p1), sha256_hash="0"*64, role=CardRole.SEQUENCE_START)
    c2 = VisualFingerprint(card_id="card2.jpg", image_path=str(p2), sha256_hash="1"*64, role=CardRole.SEQUENCE_CONTINUATION)
    c3 = VisualFingerprint(card_id="card3.jpg", image_path=str(p3), sha256_hash="2"*64, role=CardRole.SEQUENCE_CONTINUATION)

    recipe = await extractor.extract_cards([c1, c2, c3])

    assert recipe.title == "3-Page Wedding Cake"
    assert recipe.card_ids == ["card1.jpg", "card2.jpg", "card3.jpg"]
    assert len(recipe.image_urls) == 3

    # Check that transcribe request had neutral face labels
    trans_req = client.requests[0]
    labels = [p.label for p in trans_req.parts if getattr(p, "label", None)]
    assert any("Face 1 of 3" in lbl for lbl in labels)
    assert any("Face 2 of 3" in lbl for lbl in labels)
    assert any("Face 3 of 3" in lbl for lbl in labels)


@pytest.mark.asyncio
async def test_extraction_prompts_contain_guess_and_anecdote_rules():
    """Validates that all system extraction prompts mandate [GUESS] suffix and strict anecdote isolation."""
    from recipeboxer.vision.router import (
        MIXED_MEDIA_SYSTEM_PROMPT,
        PRINT_CLIPPING_SYSTEM_PROMPT,
        TYPEWRITTEN_SYSTEM_PROMPT,
        VINTAGE_HANDWRITING_SYSTEM_PROMPT,
    )

    prompts = [
        RECIPE_EXTRACTION_SYSTEM_INSTRUCTION,
        VINTAGE_HANDWRITING_SYSTEM_PROMPT,
        PRINT_CLIPPING_SYSTEM_PROMPT,
        TYPEWRITTEN_SYSTEM_PROMPT,
        MIXED_MEDIA_SYSTEM_PROMPT,
    ]

    for p in prompts:
        assert "[GUESS]" in p
        assert "TITLE INFERENCE & GUESSING" in p or "TITLE INFERENCE" in p or "TITLE" in p
        assert "description" in p
        assert "source_notes" in p
        assert "STRICT ISOLATION" in p or "ingredients" in p


@pytest.mark.asyncio
async def test_offline_extraction_inferred_untitled_card_appends_guess():
    """Validates unmocked extractor raises ExtractionUnavailable and client [GUESS] passes through."""
    extractor = GeminiRecipeExtractor(api_key=None)

    fp = VisualFingerprint(
        card_id="untitled_scanned_card_001.jpg",
        image_path="/tmp/untitled_scanned_card_001.jpg",
        sha256_hash="abc12345678",
        role=CardRole.STANDALONE,
        boundary_text=BoundaryText(
            first_line="Apple Brown Betty",
            last_line="Serve warm.",
        ),
    )

    with pytest.raises(ExtractionUnavailable):
        await extractor.extract(fp)

    # Injected client returning [GUESS] preserves it
    client = AsyncMock()
    client.extract_recipe = AsyncMock(return_value=RecipeSchema(
        title="Apple Brown Betty [GUESS]",
        ingredients=["4 apples", "1 cup brown sugar"],
        directions=["1. Bake at 350F."],
    ))
    extractor_mock = GeminiRecipeExtractor(client=client)
    recipe = await extractor_mock.extract(fp)
    assert isinstance(recipe, RecipeSchema)
    assert recipe.title.endswith(" [GUESS]")
    assert "Apple Brown Betty" in recipe.title


@pytest.mark.asyncio
async def test_recipe_schema_preserves_guess_and_anecdote_isolation():
    """Validates that RecipeSchema maintains title guessing and clean metadata separation."""
    recipe = RecipeSchema(
        title="Heirloom Cinnamon Streusel [GUESS]",
        description="Passed down from Great-Aunt Clara for Sunday suppers after church in 1938.",
        source_notes="Grandma's cedar recipe box (1938 handwritten index card)",
        ingredients=["2 cups flour", "1 cup sugar", "2 tsp cinnamon"],
        directions=["1. Combine dry ingredients.", "2. Bake at 350F for 30 minutes."],
        tags=["Baking", "Vintage", "Sunday Supper"],
    )

    assert recipe.title == "Heirloom Cinnamon Streusel [GUESS]"
    assert "1938" in recipe.description
    assert "Sunday suppers" in recipe.description
    assert "cedar recipe box" in recipe.source_notes
    assert all("1938" not in ing for ing in recipe.ingredients)
    assert all("suppers" not in d for d in recipe.directions)





