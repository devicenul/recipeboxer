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
        from PIL import Image
        from recipeboxer.ai.client import LLMResponse, UsageTokens
        from recipeboxer.extraction.image_prep import PreparedFace
        from recipeboxer.extraction.transcribe import (
            FaceTranscript,
            FaceTranscriptList,
            transcribe,
        )

        settings = Settings(model_profile="balanced")
        fake_transcript = FaceTranscriptList(
            faces=[
                FaceTranscript(
                    face_index=0,
                    text="1 cup sugar\n1. Stir and bake.",
                )
            ]
        )
        fake_resp = LLMResponse(
            model="gemini-test",
            text="{}",
            parsed=fake_transcript,
            usage=UsageTokens(input_tokens=100, output_tokens=50),
        )

        class RecordingClient:
            def __init__(self, response):
                self.response = response
                self.requests = []

            async def generate(self, request):
                self.requests.append(request)
                return self.response

        client = RecordingClient(fake_resp)
        face = PreparedFace(
            face_index=0,
            n_faces=1,
            data=b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9",
            mime_type="image/jpeg",
            variant="standard",
            long_edge_px=100,
            label="Face 1 of 1",
        )

        await transcribe(
            faces=[face],
            llm_client=client,
            settings=settings,
            modality_hint="HANDWRITTEN_CURSIVE",
        )
        assert len(client.requests) == 1
        req = client.requests[0]
        assert req.thinking_budget is not None
        assert req.thinking_budget > 0

    @pytest.mark.asyncio
    async def test_explicit_thinking_budget_takes_precedence(self):
        from PIL import Image
        from recipeboxer.ai.client import LLMResponse, UsageTokens
        from recipeboxer.extraction.image_prep import PreparedFace
        from recipeboxer.extraction.transcribe import (
            FaceTranscript,
            FaceTranscriptList,
            transcribe,
        )

        settings = Settings(
            thinking_mode=True,
            thinking_budget_tokens=8192,
        )
        fake_transcript = FaceTranscriptList(
            faces=[
                FaceTranscript(
                    face_index=0,
                    text="2 cups flour\n1. Mix well.",
                )
            ]
        )
        fake_resp = LLMResponse(
            model="gemini-test",
            text="{}",
            parsed=fake_transcript,
            usage=UsageTokens(input_tokens=100, output_tokens=50),
        )

        class RecordingClient:
            def __init__(self, response):
                self.response = response
                self.requests = []

            async def generate(self, request):
                self.requests.append(request)
                return self.response

        client = RecordingClient(fake_resp)
        face = PreparedFace(
            face_index=0,
            n_faces=1,
            data=b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9",
            mime_type="image/jpeg",
            variant="standard",
            long_edge_px=100,
            label="Face 1 of 1",
        )

        await transcribe(
            faces=[face],
            llm_client=client,
            settings=settings,
            modality_hint="TYPEWRITTEN",
        )
        assert len(client.requests) == 1
        req = client.requests[0]
        assert req.thinking_budget == 8192
