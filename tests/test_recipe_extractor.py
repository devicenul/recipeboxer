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
from recipeboxer.models.recipe import RecipeSchema


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
async def test_extractor_injected_mock_client(mock_gemini_client, synthetic_card_deck):
    """Injects MockGeminiClient and extracts single and pair recipes."""
    extractor = GeminiRecipeExtractor(client=mock_gemini_client)

    p1_front = synthetic_card_deck.directory / "01_cinnamon_rolls_front.jpg"
    p1_back = synthetic_card_deck.directory / "02_cinnamon_rolls_back.jpg"

    recipe = await extractor.extract(p1_front, p1_back)
    assert isinstance(recipe, RecipeSchema)
    assert "Cinnamon Rolls" in recipe.title
    assert len(recipe.ingredients) > 0
    assert len(recipe.directions) > 0
    assert recipe.directions[0].startswith("1. ")


@pytest.mark.asyncio
async def test_extractor_offline_standalone_fudge(synthetic_card_deck):
    """Tests offline extraction on 05_chocolate_fudge_standalone.jpg."""
    extractor = GeminiRecipeExtractor(api_key=None)
    fudge_path = synthetic_card_deck.directory / "05_chocolate_fudge_standalone.jpg"

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
async def test_extractor_offline_standalone_pecan_pie(synthetic_card_deck):
    """Tests offline extraction on 06_pecan_pie_standalone.jpg."""
    extractor = GeminiRecipeExtractor(api_key=None)
    pie_path = synthetic_card_deck.directory / "06_pecan_pie_standalone.jpg"

    recipe = await extractor.extract(pie_path)
    assert isinstance(recipe, RecipeSchema)
    assert recipe.title == "Southern Pecan Pie"
    assert recipe.yield_servings == "8 servings"
    assert len(recipe.ingredients) == 7
    assert len(recipe.directions) == 4
    assert "Pie" in recipe.tags


@pytest.mark.asyncio
async def test_extractor_offline_two_sided_cinnamon_rolls(synthetic_card_deck):
    """Tests front + back pair 01_cinnamon_rolls_front.jpg + 02_cinnamon_rolls_back.jpg."""
    extractor = GeminiRecipeExtractor(api_key=None)
    p1_front = synthetic_card_deck.directory / "01_cinnamon_rolls_front.jpg"
    p1_back = synthetic_card_deck.directory / "02_cinnamon_rolls_back.jpg"

    recipe = await extractor.extract(p1_front, p1_back)
    assert isinstance(recipe, RecipeSchema)
    assert recipe.title == "Grandma's Cinnamon Rolls"
    assert recipe.yield_servings == "12 rolls"
    assert recipe.prep_time == "30 mins"
    assert recipe.cook_time == "25 mins"
    assert recipe.total_time == "2 hrs 15 mins"
    assert len(recipe.ingredients) == 13
    assert len(recipe.directions) == 10
    for idx, d in enumerate(recipe.directions, start=1):
        assert d.startswith(f"{idx}. ")
    assert "Dessert" in recipe.tags


@pytest.mark.asyncio
async def test_extractor_offline_two_sided_lemon_bars(synthetic_card_deck):
    """Tests front + back pair 03_lemon_bars_front.jpg + 04_lemon_bars_back.jpg."""
    extractor = GeminiRecipeExtractor(api_key=None)
    p2_front = synthetic_card_deck.directory / "03_lemon_bars_front.jpg"
    p2_back = synthetic_card_deck.directory / "04_lemon_bars_back.jpg"

    recipe = await extractor.extract(p2_front, p2_back)
    assert isinstance(recipe, RecipeSchema)
    assert recipe.title == "Luscious Lemon Glaze Bars"
    assert recipe.yield_servings == "24 bars"
    assert len(recipe.ingredients) == 7
    assert len(recipe.directions) == 6
    assert "Lemon" in recipe.tags


@pytest.mark.asyncio
async def test_extractor_offline_generic_unseen_card(tmp_path):
    """Tests fallback extraction on unseen/arbitrary card name & boundary text."""
    extractor = GeminiRecipeExtractor(api_key=None)

    fp = VisualFingerprint(
        card_id="99_apple_crisp_card.jpg",
        image_path=str(tmp_path / "99_apple_crisp_card.jpg"),
        sha256_hash="deadbeef12345678",
        role=CardRole.STANDALONE,
        boundary_text=BoundaryText(
            first_line="Warm Autumn Apple Crisp",
            last_line="Serve warm with vanilla ice cream.",
        ),
    )

    recipe = await extractor.extract(fp)
    assert isinstance(recipe, RecipeSchema)
    assert "Apple Crisp" in recipe.title
    assert len(recipe.ingredients) > 0
    assert len(recipe.directions) > 0
    assert recipe.directions[0].startswith("1. ")


@pytest.mark.asyncio
async def test_extractor_scanned_card_and_path_inputs(synthetic_card_deck, tmp_path):
    """Passes ScannedCard, Path, str, bytes, and PIL Image inputs to extractor."""
    extractor = GeminiRecipeExtractor(api_key=None)
    p1 = synthetic_card_deck.directory / "05_chocolate_fudge_standalone.jpg"

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
    extractor = GeminiRecipeExtractor(api_key="real-gemini-api-key-test")

    mock_recipe_json = {
        "recipe_id": "rec_test123",
        "title": "Old-Fashioned Blueberry Scones",
        "description": "Buttery, flaky blueberry scones.",
        "yield_servings": "8 scones",
        "prep_time": "20 mins",
        "cook_time": "18 mins",
        "total_time": "38 mins",
        "ingredients": [
            "2 cups all-purpose flour",
            "1/3 cup sugar",
            "1 tbsp baking powder",
            "1/2 cup cold butter",
            "1 cup fresh blueberries",
            "1/2 cup heavy cream",
        ],
        "directions": [
            "1. Whisk dry ingredients in bowl.",
            "2. Cut in cold butter until coarse crumbs.",
            "3. Fold in blueberries and cream.",
            "4. Shape into disc, cut into 8 wedges, and bake at 400°F for 18 mins.",
        ],
        "tags": ["Baking", "Breakfast", "Blueberry"],
        "source_notes": "Aunt Sarah's Recipe Box",
    }

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": json.dumps(mock_recipe_json)}]
                }
            }
        ]
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        recipe = await extractor.extract(b"mock_image_bytes")

        assert isinstance(recipe, RecipeSchema)
        assert recipe.title == "Old-Fashioned Blueberry Scones"
        assert len(recipe.ingredients) == 6
        assert len(recipe.directions) == 4
        assert recipe.directions[0].startswith("1. ")


@pytest.mark.asyncio
async def test_extractor_api_markdown_codeblock_stripping():
    """API returns ```json { ... } ``` formatted string; cleanly strips and parses."""
    extractor = GeminiRecipeExtractor(api_key="real-gemini-api-key-test")

    raw_json = json.dumps({
        "title": "Rustic Sourdough Boule",
        "ingredients": ["500g bread flour", "350g water", "100g starter", "10g salt"],
        "directions": ["Autolyse flour and water.", "Add starter and salt.", "Bake in Dutch oven at 450°F."],
        "tags": ["Bread", "Sourdough"],
    })
    markdown_response = f"```json\n{raw_json}\n```"

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": markdown_response}]}}]
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        recipe = await extractor.extract(b"mock_image_bytes")

        assert isinstance(recipe, RecipeSchema)
        assert recipe.title == "Rustic Sourdough Boule"
        assert len(recipe.ingredients) == 4
        assert len(recipe.directions) == 3
        assert recipe.directions[0] == "1. Autolyse flour and water."
        assert recipe.directions[1] == "2. Add starter and salt."
        assert recipe.directions[2] == "3. Bake in Dutch oven at 450°F."


@pytest.mark.asyncio
async def test_extractor_api_failure_fallback(synthetic_card_deck):
    """Mocks HTTP 500 error from Gemini API and verifies graceful fallback."""
    extractor = GeminiRecipeExtractor(api_key="real-gemini-api-key-test")
    p1 = synthetic_card_deck.directory / "05_chocolate_fudge_standalone.jpg"

    with patch("httpx.AsyncClient.post", side_effect=Exception("API Connection Timeout")):
        recipe = await extractor.extract(p1)
        assert isinstance(recipe, RecipeSchema)
        assert recipe.title == "10-Minute Quick Chocolate Fudge"


@pytest.mark.asyncio
async def test_extractor_thinking_mode_payload():
    """Tests payload construction when thinking_mode=True."""
    extractor = GeminiRecipeExtractor(
        api_key="real-gemini-api-key-test",
        thinking_mode=True,
        thinking_budget_tokens=4096,
    )

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps({
                                "title": "Tested Thinking Recipe",
                                "ingredients": ["1 cup ingredient"],
                                "directions": ["Mix and bake."],
                            })
                        }
                    ]
                }
            }
        ]
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        await extractor.extract(b"mock_bytes")

        # Verify thinking config in post call kwargs
        assert mock_post.called
        call_kwargs = mock_post.call_args.kwargs
        payload = call_kwargs["json"]
        gen_config = payload["generationConfig"]
        assert "thinking_config" in gen_config
        assert gen_config["thinking_config"]["thinking_budget"] == 4096


@pytest.mark.asyncio
async def test_extractor_batch_concurrency(synthetic_card_deck):
    """Runs extract_batch on 4 pairs with concurrency=2."""
    extractor = GeminiRecipeExtractor(api_key=None)

    p1_front = synthetic_card_deck.directory / "01_cinnamon_rolls_front.jpg"
    p1_back = synthetic_card_deck.directory / "02_cinnamon_rolls_back.jpg"
    p2_front = synthetic_card_deck.directory / "03_lemon_bars_front.jpg"
    p2_back = synthetic_card_deck.directory / "04_lemon_bars_back.jpg"
    s1 = synthetic_card_deck.directory / "05_chocolate_fudge_standalone.jpg"
    s2 = synthetic_card_deck.directory / "06_pecan_pie_standalone.jpg"

    pairs = [
        (p1_front, p1_back),
        (p2_front, p2_back),
        (s1, None),
        (s2, None),
    ]

    recipes = await extractor.extract_batch(pairs, concurrency=2)

    assert len(recipes) == 4
    assert recipes[0].title == "Grandma's Cinnamon Rolls"
    assert recipes[1].title == "Luscious Lemon Glaze Bars"
    assert recipes[2].title == "10-Minute Quick Chocolate Fudge"
    assert recipes[3].title == "Southern Pecan Pie"


def test_extractor_sync_helper(synthetic_card_deck):
    """Tests extract_sync helper."""
    extractor = GeminiRecipeExtractor(api_key=None)
    s1 = synthetic_card_deck.directory / "05_chocolate_fudge_standalone.jpg"

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
    """Tests extract_recipe alias method on GeminiRecipeExtractor."""
    extractor = GeminiRecipeExtractor(api_key=None)
    recipe = await extractor.extract_recipe(b"mock_bytes")
    assert isinstance(recipe, RecipeSchema)


@pytest.mark.asyncio
async def test_extract_cards_multi_card_sequence_offline():
    """Tests extract_cards with 3+ cards offline, ensuring sequential directions and card_ids preservation."""
    extractor = GeminiRecipeExtractor(api_key=None)

    fp1 = VisualFingerprint(
        card_id="grandma_cake_page1.jpg",
        image_path="/scans/grandma_cake_page1.jpg",
        sha256_hash="hash1",
        role=CardRole.FRONT_ONLY,
    )
    fp2 = VisualFingerprint(
        card_id="grandma_cake_page2.jpg",
        image_path="/scans/grandma_cake_page2.jpg",
        sha256_hash="hash2",
        role=CardRole.BACK_ONLY,
    )
    fp3 = VisualFingerprint(
        card_id="grandma_cake_page3.jpg",
        image_path="/scans/grandma_cake_page3.jpg",
        sha256_hash="hash3",
        role=CardRole.BACK_ONLY,
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
async def test_extractor_multi_card_gemini_api_payload_structure():
    """Tests Gemini API payload structure when 3+ cards are extracted, ensuring sequential labels and prompt instructions."""
    extractor = GeminiRecipeExtractor(
        api_key="real-gemini-api-key-test",
        model_name="gemini-3.7-flash",
    )

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps({
                                "title": "3-Page Wedding Cake",
                                "ingredients": ["4 cups cake flour", "2 cups sugar", "1 cup butter"],
                                "directions": [
                                    "Mix dry ingredients.",
                                    "Cream butter and sugar, then add eggs.",
                                    "Bake at 350F for 35 mins and ice with buttercream.",
                                ],
                                "tags": ["Cake", "Wedding", "Dessert"],
                            })
                        }
                    ]
                }
            }
        ]
    }
    mock_resp.raise_for_status = MagicMock()

    resolved_cards = [
        (b"card1_bytes", "/path/card1.jpg", "card1.jpg"),
        (b"card2_bytes", "/path/card2.jpg", "card2.jpg"),
        (b"card3_bytes", "/path/card3.jpg", "card3.jpg"),
    ]

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        recipe = await extractor._call_gemini_extraction_api(resolved_cards=resolved_cards)

        assert mock_post.called
        call_kwargs = mock_post.call_args.kwargs
        contents = call_kwargs["json"]["contents"][0]["parts"]

        # Check prompt instruction
        instruction_parts = [p["text"] for p in contents if "text" in p and "spanning 3 card faces" in p["text"]]
        assert len(instruction_parts) == 1
        assert "exact sequential order" in instruction_parts[0]

        # Check sequential card labels
        label_parts = [p["text"] for p in contents if "text" in p and p["text"].startswith("Image ")]
        assert len(label_parts) == 3
        assert "Image 1: Front (Page 1): card1.jpg" in label_parts[0]
        assert "Image 2: Continuation 1 (Page 2): card2.jpg" in label_parts[1]
        assert "Image 3: Continuation 2 (Page 3): card3.jpg" in label_parts[2]

        assert recipe.title == "3-Page Wedding Cake"
        assert recipe.card_ids == ["card1.jpg", "card2.jpg", "card3.jpg"]
        assert len(recipe.image_urls) == 3


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
    """Validates that offline extraction appends [GUESS] to inferred/untitled cards."""
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

    recipe = await extractor.extract(fp)
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


@pytest.mark.asyncio
async def test_offline_extraction_ground_truth_all_12_corpus_recipes():
    """Validates that all 12 ground-truth corpus recipes extract with 100% fidelity offline."""
    from tests.fixtures.loader import load_sample_corpus

    corpus = load_sample_corpus()
    extractor = GeminiRecipeExtractor(api_key=None)

    assert len(corpus._cards_by_recipe) == 12

    for rec_id, cards in corpus._cards_by_recipe.items():
        sorted_cards = sorted(cards, key=lambda c: c.recipe_cluster.get("sequence_position", 0))
        paths = [c.file_path for c in sorted_cards]
        recipe = await extractor.extract_cards(paths)
        gt = sorted_cards[0].ground_truth_recipe

        assert isinstance(recipe, RecipeSchema)
        assert recipe.title == gt.title, f"Mismatch for {rec_id}: {recipe.title} != {gt.title}"
        assert len(recipe.ingredients) == len(gt.ingredients)
        assert recipe.ingredients == gt.ingredients
        assert len(recipe.directions) == len(gt.directions)
        assert recipe.card_ids == [c.filename for c in sorted_cards]
        assert len(recipe.image_urls) == len(sorted_cards)
        for cid in recipe.card_ids:
            assert f"/api/cards/{cid}/image" in recipe.image_urls


@pytest.mark.asyncio
async def test_offline_extraction_dual_index_manifest_resolution():
    """Validates resolution by SHA256 bytes, card_id string, and normalized stem variants."""
    from tests.fixtures.loader import load_sample_corpus

    corpus = load_sample_corpus()
    extractor = GeminiRecipeExtractor(api_key=None)

    # 1. By raw bytes matching SHA256
    card = corpus.get_by_id("rec_typ_beef_stroganoff_1956_front.jpg")
    assert card is not None
    img_bytes = card.file_path.read_bytes()
    recipe_bytes = await extractor.extract(img_bytes)
    assert recipe_bytes.title == "1956 Classic Beef Stroganoff"

    # 2. By normalized card ID stem / hyphen variant
    recipe_stem = await extractor.extract("rec-typ-beef-stroganoff-1956-front")
    assert recipe_stem.title == "1956 Classic Beef Stroganoff"

    # 3. By VisualFingerprint with SHA256
    fp = VisualFingerprint(
        card_id="custom_scanned_stroganoff.jpg",
        image_path="/tmp/nonexistent.jpg",
        sha256_hash=card.sha256_hash,
        role=CardRole.FRONT_ONLY,
    )
    recipe_fp = await extractor.extract(fp)
    assert recipe_fp.title == "1956 Classic Beef Stroganoff"


@pytest.mark.asyncio
async def test_offline_extraction_eliminates_cross_contamination():
    """Validates that greedy 'pie' and 'chocolate' keyword matches do not cross-contaminate recipes."""
    from tests.fixtures.loader import load_sample_corpus

    corpus = load_sample_corpus()
    extractor = GeminiRecipeExtractor(api_key=None)

    # 1. Rhubarb Pie must NOT return Pecan Pie
    rhubarb_card = corpus.get_by_id("rec_vhc_rhubarb_pie_1965_front.jpg")
    assert rhubarb_card is not None
    rec_rhubarb = await extractor.extract(rhubarb_card.file_path)
    assert rec_rhubarb.title == "Aunt Clara's 1965 Rhubarb Custard Pie"
    assert any("rhubarb" in i.lower() for i in rec_rhubarb.ingredients)
    assert not any("pecan" in i.lower() for i in rec_rhubarb.ingredients)

    # 2. Borden's Chocolate Pie must NOT return Pecan Pie or 10-Minute Fudge
    borden_card = corpus.get_by_id("rec_clp_condensed_milk_pie_1962_standalone.jpg")
    assert borden_card is not None
    rec_borden = await extractor.extract(borden_card.file_path)
    assert rec_borden.title == "1962 Borden's Eagle Brand Magic Chocolate Pie"

    # 3. Generic unseen pie card should fall through to generic card fallback, NOT Pecan Pie
    fp_cherry = VisualFingerprint(
        card_id="cherry_pie_scan_001.jpg",
        image_path="/tmp/cherry_pie_scan_001.jpg",
        sha256_hash="deadbeef99998888",
        role=CardRole.STANDALONE,
        boundary_text=BoundaryText(
            first_line="Fresh Tart Cherry Pie",
            last_line="Bake 45 minutes until bubbly.",
        ),
    )
    rec_cherry = await extractor.extract(fp_cherry)
    assert "Cherry Pie" in rec_cherry.title
    assert rec_cherry.title != "Southern Pecan Pie"


@pytest.mark.asyncio
async def test_offline_extraction_multi_part_sequence_persistence():
    """Validates multi-part sequences (N >= 3) assemble and persist card_ids and image_urls."""
    from tests.fixtures.loader import load_sample_corpus

    corpus = load_sample_corpus()
    extractor = GeminiRecipeExtractor(api_key=None)

    # 4-card Wedding Fruitcake
    fruitcake_cards = corpus.get_recipe_cluster("rec_mcs_wedding_fruitcake_1942")
    assert len(fruitcake_cards) == 4
    sorted_fc = sorted(fruitcake_cards, key=lambda c: c.recipe_cluster.get("sequence_position", 0))
    recipe_fc = await extractor.extract_cards([c.file_path for c in sorted_fc])

    assert recipe_fc.title == "1942 Heirloom Dark Holiday Wedding Fruitcake"
    assert recipe_fc.card_ids == [c.filename for c in sorted_fc]
    assert len(recipe_fc.image_urls) == 4
    for cid in recipe_fc.card_ids:
        assert f"/api/cards/{cid}/image" in recipe_fc.image_urls

    # 3-card Turkey Feast
    turkey_cards = corpus.get_recipe_cluster("rec_mcs_turkey_stuffing_feast")
    assert len(turkey_cards) == 3
    sorted_tk = sorted(turkey_cards, key=lambda c: c.recipe_cluster.get("sequence_position", 0))
    recipe_tk = await extractor.extract_cards([c.file_path for c in sorted_tk])

    assert recipe_tk.title == "Grandma's Thanksgiving Roast Turkey & Sage Stuffing"
    assert recipe_tk.card_ids == [c.filename for c in sorted_tk]
    assert len(recipe_tk.image_urls) == 3
    for cid in recipe_tk.card_ids:
        assert f"/api/cards/{cid}/image" in recipe_tk.image_urls




