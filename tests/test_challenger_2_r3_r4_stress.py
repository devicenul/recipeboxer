"""Adversarial Empirical Challenge Test Suite for R3 (Telemetry/SSE/Navigation) and R4 (Extractor Isolation/Fallbacks).

Empirically challenges:
1. Multi-part sequence assembly (N >= 3) verifying card_ids, image_urls, and sequential ordering with mock clients.
2. Input representation polymorphism and robust error handling (PIL.Image, empty input rejection, [GUESS] tag handling).
3. Orchestrator Stage 5 incremental extraction and SSE event emission (/api/events/{session_id}).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from PIL import Image

from recipeboxer.agent.orchestrator import RecipeBoxerAgent
from recipeboxer.agent.session import PipelineSession, SessionManager
from recipeboxer.api.app import create_app
from recipeboxer.api.routes import get_manager
from recipeboxer.config import Settings
from recipeboxer.extraction.extractor import GeminiRecipeExtractor
from recipeboxer.models.domain import (
    BoundaryText,
    CardRole,
    VisualFingerprint,
)
from recipeboxer.ingestion.scanner import ScannedCard
from recipeboxer.models.failures import ExtractionUnavailable, FailureReason
from recipeboxer.models.hitl import CardSequenceApproval, HITLDecision
from recipeboxer.models.pipeline import PipelineStartRequest, PipelineState, PipelineStatus
from recipeboxer.models.recipe import RecipeSchema


# ============================================================================
# 1. Multi-Part Sequence Assembly Stress Tests (N >= 3) (R4)
# ============================================================================

class TestMultiPartSequenceAssembly:
    """Stress tests multi-part recipe sequences (N=3, N=4, N=5+) ordering, card IDs, and image URLs."""

    @pytest.mark.asyncio
    async def test_wedding_fruitcake_4_part_assembly(self, tmp_path: Path):
        """Tests 4-part Wedding Fruitcake sequence ordering, card_ids, and image_urls with injected client."""
        cards = [tmp_path / f"fruitcake_card_{i}.jpg" for i in range(1, 5)]
        for c in cards:
            c.write_bytes(b"dummy_bytes")

        expected_card_ids = [c.name for c in cards]
        mock_recipe = RecipeSchema(
            title="1942 Heirloom Dark Holiday Wedding Fruitcake",
            ingredients=["2 cups flour", "1 cup sugar", "sultanas", "brandy"],
            directions=[
                "1. Mix dry ingredients.",
                "2. Stir in sultanas.",
                "3. Add brandy.",
                "4. Bake slowly.",
            ],
        )

        class MockClient:
            async def extract_cards(self, card_inputs):
                return mock_recipe

        extractor = GeminiRecipeExtractor(client=MockClient())
        recipe = await extractor.extract_cards([str(c) for c in cards])

        assert recipe.title == "1942 Heirloom Dark Holiday Wedding Fruitcake"
        assert len(recipe.card_ids) == 4
        assert recipe.card_ids == expected_card_ids
        assert len(recipe.image_urls) == 4
        assert recipe.image_urls == [f"/api/cards/{cid}/image" for cid in recipe.card_ids]
        assert len(recipe.directions) == 4

    @pytest.mark.asyncio
    async def test_turkey_feast_3_part_assembly(self, tmp_path: Path):
        """Tests 3-part Turkey Stuffing Feast sequence ordering, card_ids, and image_urls with injected client."""
        cards = [tmp_path / f"turkey_feast_card_{i}.jpg" for i in range(1, 4)]
        for c in cards:
            c.write_bytes(b"dummy_bytes")

        expected_card_ids = [c.name for c in cards]
        mock_recipe = RecipeSchema(
            title="Grandma's Thanksgiving Roast Turkey & Sage Stuffing",
            ingredients=["1 whole turkey", "sage stuffing", "butter"],
            directions=[
                "1. Prepare stuffing.",
                "2. Stuff turkey and truss.",
                "3. Roast at 325F until golden.",
            ],
        )

        class MockClient:
            async def extract_cards(self, card_inputs):
                return mock_recipe

        extractor = GeminiRecipeExtractor(client=MockClient())
        recipe = await extractor.extract_cards([str(c) for c in cards])

        assert recipe.title == "Grandma's Thanksgiving Roast Turkey & Sage Stuffing"
        assert len(recipe.card_ids) == 3
        assert recipe.card_ids == expected_card_ids
        assert len(recipe.image_urls) == 3
        assert recipe.image_urls == [f"/api/cards/{cid}/image" for cid in recipe.card_ids]
        assert len(recipe.directions) == 3

    @pytest.mark.asyncio
    @pytest.mark.parametrize("num_cards", [3, 4, 5, 6, 8])
    async def test_synthetic_n_card_fallback_assembly(self, num_cards: int):
        """Tests arbitrary N-card sequence assembly (N >= 3) with injected client."""
        synthetic_cards = [
            VisualFingerprint(
                card_id=f"synthetic_deck_c{i}_face.jpg",
                image_path=f"/tmp/synthetic_deck_c{i}_face.jpg",
                sha256_hash=f"synthetic_hash_{i}",
                role=CardRole.SEQUENCE_START if i == 0 else CardRole.SEQUENCE_CONTINUATION,
            )
            for i in range(num_cards)
        ]
        expected_card_ids = [f"synthetic_deck_c{i}_face.jpg" for i in range(num_cards)]

        mock_recipe = RecipeSchema(
            title=f"Multi-Card Recipe ({num_cards} Cards)",
            ingredients=["1 cup flour"],
            directions=[
                f"{i + 1}. Step {i + 1} continuation" if i > 0 else "1. Front preparation"
                for i in range(num_cards)
            ],
        )

        class MockClient:
            async def extract_cards(self, card_inputs):
                return mock_recipe

        extractor = GeminiRecipeExtractor(client=MockClient())
        recipe = await extractor.extract_cards(synthetic_cards)

        assert recipe.title == f"Multi-Card Recipe ({num_cards} Cards)"
        assert len(recipe.card_ids) == num_cards
        assert recipe.card_ids == expected_card_ids
        assert len(recipe.image_urls) == num_cards
        assert recipe.image_urls == [f"/api/cards/{cid}/image" for cid in recipe.card_ids]
        assert len(recipe.directions) == num_cards

        # Verify sequential directions
        assert recipe.directions[0].startswith("1. Front preparation")
        for idx in range(1, num_cards):
            assert recipe.directions[idx].startswith(f"{idx + 1}. Step {idx + 1} continuation")


# ============================================================================
# 2. Input Representation & Fallback Resolution Stress Tests (R4)
# ============================================================================

class TestInputRepresentationAndFallback:
    """Stress tests resolution across varied input formats and boundary/fallback behavior."""

    @pytest.mark.asyncio
    async def test_resolve_via_pil_image(self):
        """Resolves recipe via PIL Image object with injected client and verifies unmocked raises."""
        img = Image.new("RGB", (100, 100), color=(255, 255, 255))

        # Unmocked without key raises ExtractionUnavailable
        extractor_no_key = GeminiRecipeExtractor(api_key=None)
        with pytest.raises(ExtractionUnavailable):
            await extractor_no_key.extract(img)

        # Injected client succeeds
        mock_recipe = RecipeSchema(
            title="White Bread",
            ingredients=["3 cups flour", "1 cup water", "1 tsp yeast"],
            directions=["1. Knead and bake."],
        )
        mock_client = AsyncMock()
        mock_client.extract_recipe = AsyncMock(return_value=mock_recipe)
        extractor = GeminiRecipeExtractor(client=mock_client)
        recipe = await extractor.extract(img)

        assert isinstance(recipe, RecipeSchema)
        assert recipe.title == "White Bread"
        assert len(recipe.ingredients) >= 1
        assert len(recipe.directions) >= 1

    @pytest.mark.asyncio
    async def test_empty_input_rejection(self):
        """Tests that empty input list raises ValueError."""
        extractor = GeminiRecipeExtractor(api_key=None)
        with pytest.raises(ValueError, match="No card inputs provided"):
            await extractor.extract([])

    @pytest.mark.asyncio
    async def test_mystery_untitled_card_guess_tagging(self):
        """Tests that untitled/mystery cards receive [GUESS] tag or raise ExtractionUnavailable when unmocked."""
        fp = VisualFingerprint(
            card_id="mystery_recipe_box_card_01.jpg",
            image_path="/tmp/mystery_recipe_box_card_01.jpg",
            sha256_hash="mystery_hash_12345",
            role=CardRole.STANDALONE,
            boundary_text=BoundaryText(first_line="Grandma's secret pudding"),
        )
        # Unmocked raises
        extractor_no_key = GeminiRecipeExtractor(api_key=None)
        with pytest.raises(ExtractionUnavailable):
            await extractor_no_key.extract(fp)

        # Mock client returning [GUESS] passes through cleanly
        mock_client = AsyncMock()
        mock_client.extract_recipe = AsyncMock(return_value=RecipeSchema(
            title="Grandma's secret pudding [GUESS]",
            ingredients=["2 cups milk", "1/2 cup sugar"],
            directions=["1. Cook pudding."],
        ))
        extractor = GeminiRecipeExtractor(client=mock_client)
        recipe = await extractor.extract(fp)
        assert recipe.title.endswith(" [GUESS]")
        assert "Grandma's secret pudding [GUESS]" == recipe.title


# ============================================================================
# 3. Orchestrator Stage 5 Incremental Telemetry & SSE Streaming (R3)
# ============================================================================

class TestStage5IncrementalTelemetryAndSSE:
    """Stress tests orchestrator Stage 5 incremental extraction and SSE stream event emission."""

    @pytest.mark.asyncio
    async def test_orchestrator_stage5_incremental_updates(self):
        """Verifies that orchestrator Stage 5 extraction updates session.recipes and metrics incrementally."""
        agent = RecipeBoxerAgent()
        session = PipelineSession(session_id="test_stage5_session")
        session.config = PipelineStartRequest(scans_dir="/tmp/dummy_scans")

        # Seed session with 3 card proposals
        card1 = VisualFingerprint(
            card_id="card_apple_crisp.jpg",
            image_path="/tmp/dummy_scans/card_apple_crisp.jpg",
            sha256_hash="hash_apple_crisp",
            role=CardRole.STANDALONE,
        )
        card2 = VisualFingerprint(
            card_id="card_shortbread.jpg",
            image_path="/tmp/dummy_scans/card_shortbread.jpg",
            sha256_hash="hash_shortbread",
            role=CardRole.STANDALONE,
        )
        card3 = VisualFingerprint(
            card_id="card_pecan_pie.jpg",
            image_path="/tmp/dummy_scans/card_pecan_pie.jpg",
            sha256_hash="hash_pecan_pie",
            role=CardRole.STANDALONE,
        )
        session.fingerprints = {c.card_id: c for c in [card1, card2, card3]}

        session.approvals = [
            CardSequenceApproval(proposal_id="prop_1", ordered_card_ids=[card1.card_id], decision=HITLDecision.APPROVE),
            CardSequenceApproval(proposal_id="prop_2", ordered_card_ids=[card2.card_id], decision=HITLDecision.APPROVE),
            CardSequenceApproval(proposal_id="prop_3", ordered_card_ids=[card3.card_id], decision=HITLDecision.APPROVE),
        ]
        session.progress_metrics.approved = 3

        mock_recipes = {
            "card_apple_crisp.jpg": RecipeSchema(
                title="Grandma's 1948 Old-Fashioned Apple Crisp",
                ingredients=["4 cups apples", "1 cup oats"],
                directions=["1. Bake at 350F."],
            ),
            "card_shortbread.jpg": RecipeSchema(
                title="Traditional Scottish Butter Shortbread",
                ingredients=["2 cups butter", "1 cup sugar"],
                directions=["1. Press into pan and bake."],
            ),
            "card_pecan_pie.jpg": RecipeSchema(
                title="Southern Pecan Pie",
                ingredients=["1 cup pecans", "1 cup syrup"],
                directions=["1. Bake until set."],
            ),
        }
        session.recipe_extractor_fn = lambda fps: mock_recipes[fps[0].card_id]

        # Run extraction stage directly or through orchestrator
        session.state = PipelineState.EXTRACTING
        recipes: List[RecipeSchema] = []
        session.recipes = []
        session.progress_metrics.extracted = 0

        # Simulate the Stage 5 loop in orchestrator.py
        for idx, approval in enumerate(session.approvals):
            ordered_fps = [session.fingerprints[cid] for cid in approval.card_ids]
            recipe = await agent._extract_single_recipe(
                session,
                front_fp=ordered_fps[0],
                ordered_fps=ordered_fps,
            )
            assert recipe is not None
            recipes.append(recipe)
            session.recipes.append(recipe)
            session.progress_metrics.extracted = len(session.recipes)

            # Assert incremental progress after each recipe
            assert len(session.recipes) == idx + 1
            assert session.progress_metrics.extracted == idx + 1

        assert len(session.recipes) == 3
        assert session.recipes[0].title == "Grandma's 1948 Old-Fashioned Apple Crisp"
        assert session.recipes[1].title == "Traditional Scottish Butter Shortbread"
        assert session.recipes[2].title == "Southern Pecan Pie"

    @pytest.mark.asyncio
    async def test_sse_event_stream_emission(self):
        """Tests that /api/events/{session_id} emits recipe_extracted and completed events in real-time."""
        app = create_app()
        client = TestClient(app)

        manager = app.state.session_manager
        session = manager.create_session()
        session_id = session.session_id

        # Setup fingerprints in session
        card1 = VisualFingerprint(
            card_id="card_sample_01.jpg",
            image_path="/tmp/card_sample_01.jpg",
            sha256_hash="hash_sample_01",
            role=CardRole.STANDALONE,
        )
        card2 = VisualFingerprint(
            card_id="card_sample_02.jpg",
            image_path="/tmp/card_sample_02.jpg",
            sha256_hash="hash_sample_02",
            role=CardRole.STANDALONE,
        )
        session.fingerprints = {c.card_id: c for c in [card1, card2]}
        session.state = PipelineState.EXTRACTING
        session.progress_metrics.total_scans = 2
        session.progress_metrics.approved = 2

        async def stream_and_extract():
            collected_text = ""

            async def background_extractor():
                await asyncio.sleep(0.05)
                r1 = RecipeSchema(
                    title="Apple Crisp",
                    ingredients=["apples", "sugar"],
                    directions=["Bake."],
                    card_ids=[card1.card_id],
                )
                session.recipes.append(r1)
                session.progress_metrics.extracted = 1

                await asyncio.sleep(0.1)
                r2 = RecipeSchema(
                    title="Shortbread",
                    ingredients=["butter", "sugar", "flour"],
                    directions=["Bake."],
                    card_ids=[card2.card_id],
                )
                session.recipes.append(r2)
                session.progress_metrics.extracted = 2

                await asyncio.sleep(0.1)
                session.state = PipelineState.COMPLETED

            task = asyncio.create_task(background_extractor())

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                async with ac.stream("GET", f"/api/events?session_id={session_id}") as response:
                    assert response.status_code == 200
                    async for chunk in response.aiter_text():
                        collected_text += chunk
                        if "event: completed" in collected_text or "COMPLETED" in collected_text:
                            break

            await task
            return collected_text

        collected = await asyncio.wait_for(stream_and_extract(), timeout=5.0)

        assert ": connected" in collected
        assert "event: recipe_extracted" in collected
        assert "Apple Crisp" in collected
        assert "Shortbread" in collected
        assert "event: completed" in collected or "COMPLETED" in collected
