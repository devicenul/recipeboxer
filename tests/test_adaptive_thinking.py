"""Unit and integration tests for Dynamic Thinking Budget Allocation.

Tests:
- compute_adaptive_thinking_budget across modalities and degradation levels
- GeminiRecipeExtractor integration with adaptive thinking mode
- Explicit thinking budget overrides taking precedence
- Backward compatibility with standard non-thinking inference
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from recipeboxer.config import Settings
from recipeboxer.extraction.extractor import (
    GeminiRecipeExtractor,
    compute_adaptive_thinking_budget,
)
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.vision.router import CardModality


class TestAdaptiveThinkingBudgetCalculation:
    """Tests for compute_adaptive_thinking_budget rule matrix."""

    def test_handwritten_cursive_budget(self):
        # Baseline cursive receives 2048 thinking tokens
        enabled, budget = compute_adaptive_thinking_budget(CardModality.HANDWRITTEN_CURSIVE)
        assert enabled is True
        assert budget == 2048

        # Degraded cursive receives 4096 thinking tokens
        enabled_sev, budget_sev = compute_adaptive_thinking_budget(
            CardModality.HANDWRITTEN_CURSIVE, degradation_level="SEVERE"
        )
        assert enabled_sev is True
        assert budget_sev == 4096

    def test_printed_clipping_budget(self):
        enabled, budget = compute_adaptive_thinking_budget(CardModality.PRINTED_CLIPPING_OR_LABEL)
        assert enabled is True
        assert budget == 1024

        enabled_sev, budget_sev = compute_adaptive_thinking_budget(
            CardModality.PRINTED_CLIPPING_OR_LABEL, degradation_level="MODERATE"
        )
        assert enabled_sev is True
        assert budget_sev == 2048

    def test_typewritten_budget(self):
        # Clean typewritten has 0 overhead (thinking disabled)
        enabled, budget = compute_adaptive_thinking_budget(CardModality.TYPEWRITTEN, degradation_level="NONE")
        assert enabled is False
        assert budget is None

        # Severe degraded typewritten allocates 1024 thinking tokens
        enabled_deg, budget_deg = compute_adaptive_thinking_budget(
            CardModality.TYPEWRITTEN, degradation_level="SEVERE"
        )
        assert enabled_deg is True
        assert budget_deg == 1024

    def test_mixed_media_budget(self):
        enabled, budget = compute_adaptive_thinking_budget(CardModality.MIXED_MEDIA)
        assert enabled is True
        assert budget >= 2048

    def test_explicit_base_budget_override(self):
        enabled, budget = compute_adaptive_thinking_budget(
            CardModality.TYPEWRITTEN, base_budget=3500
        )
        assert enabled is True
        assert budget == 3500


class TestExtractorAdaptiveThinkingIntegration:
    """Tests for GeminiRecipeExtractor handling of adaptive thinking configurations."""

    def test_extractor_initializes_adaptive_thinking_flag(self):
        # Default
        ext1 = GeminiRecipeExtractor()
        assert ext1.adaptive_thinking is False

        # Override via parameter
        ext2 = GeminiRecipeExtractor(adaptive_thinking=True)
        assert ext2.adaptive_thinking is True

        # Override via Settings
        custom_settings = Settings(adaptive_thinking=True)
        ext3 = GeminiRecipeExtractor(settings=custom_settings)
        assert ext3.adaptive_thinking is True

    @pytest.mark.asyncio
    async def test_extractor_api_payload_includes_adaptive_budget(self):
        ext = GeminiRecipeExtractor(
            api_key="valid-test-key-12345",
            adaptive_thinking=True,
        )

        mock_recipe = RecipeSchema(
            recipe_id="rec_test_123",
            title="Test Adaptive Recipe",
            ingredients=["1 cup sugar"],
            directions=["1. Stir and bake."],
        )

        fake_resp_json = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": mock_recipe.model_dump_json()}
                        ]
                    }
                }
            ]
        }

        mock_post_resp = MagicMock()
        mock_post_resp.json.return_value = fake_resp_json
        mock_post_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_post_resp)) as mock_post:
            # Call extraction on cursive sample
            sample_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 100 # Minimal jpeg header
            recipe = await ext._call_gemini_extraction_api(
                front_bytes=sample_bytes,
                front_id="rec_vhc_apple_crisp_1948_front.jpg",
            )

            assert recipe.title == "Test Adaptive Recipe"
            assert mock_post.called

            # Check that generationConfig received thinking_config
            call_kwargs = mock_post.call_args.kwargs
            payload = call_kwargs.get("json", {})
            gen_cfg = payload.get("generationConfig", {})
            assert "thinking_config" in gen_cfg
            assert "thinking_budget" in gen_cfg["thinking_config"]
            assert gen_cfg["thinking_config"]["thinking_budget"] > 0

    @pytest.mark.asyncio
    async def test_explicit_thinking_budget_takes_precedence(self):
        ext = GeminiRecipeExtractor(
            api_key="valid-test-key-12345",
            thinking_mode=True,
            thinking_budget_tokens=8192,
            adaptive_thinking=True,
        )

        mock_recipe = RecipeSchema(
            recipe_id="rec_test_456",
            title="Explicit Budget Recipe",
            ingredients=["2 cups flour"],
            directions=["1. Mix well."],
        )

        fake_resp_json = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": mock_recipe.model_dump_json()}
                        ]
                    }
                }
            ]
        }

        mock_post_resp = MagicMock()
        mock_post_resp.json.return_value = fake_resp_json
        mock_post_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_post_resp)) as mock_post:
            sample_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 100
            recipe = await ext._call_gemini_extraction_api(
                front_bytes=sample_bytes,
                front_id="rec_typ_beef_stroganoff_1956_front.jpg",
            )

            assert recipe.title == "Explicit Budget Recipe"
            call_kwargs = mock_post.call_args.kwargs
            payload = call_kwargs.get("json", {})
            gen_cfg = payload.get("generationConfig", {})
            assert gen_cfg["thinking_config"]["thinking_budget"] == 8192
