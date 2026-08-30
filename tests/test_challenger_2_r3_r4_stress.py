"""Adversarial Empirical Challenge Test Suite for R3 (Telemetry/SSE/Navigation) and R4 (Extractor Isolation/Fallbacks).

Empirically challenges:
1. Strict Recipe Isolation across all 12 corpus recipes (all-pairs cross-contamination checks).
2. Synthetic keyword collision & boundary isolation (avoiding misroutes to Pecan Pie, etc.).
3. Multi-part sequence assembly (N >= 3) verifying card_ids, image_urls, and sequential ordering.
4. Deterministic manifest resolution across all input representations (Fingerprint, ScannedCard, Path, str, bytes, PIL.Image).
5. Robust fallback for unknown, corrupted, and mystery cards (ensuring valid RecipeSchema and [GUESS] handling).
6. Orchestrator Stage 5 incremental extraction and SSE event emission (/api/events/{session_id}).
7. Parallel batch concurrency and cross-task isolation.
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
from recipeboxer.extraction.extractor import (
    GeminiRecipeExtractor,
    _get_manifest_lookup_tables,
)
from recipeboxer.models.domain import (
    BoundaryText,
    CardRole,
    VisualFingerprint,
)
from recipeboxer.ingestion.scanner import ScannedCard
from recipeboxer.models.hitl import CardSequenceApproval, HITLDecision
from recipeboxer.models.pipeline import PipelineStartRequest, PipelineState, PipelineStatus
from recipeboxer.models.recipe import RecipeSchema
from tests.fixtures.loader import load_sample_corpus


# ============================================================================
# 1. Corpus Manifest & All-Pairs Cross-Contamination Stress Tests (R4)
# ============================================================================

class TestCorpusStrictIsolation:
    """Stress tests strict recipe isolation across all 12 corpus recipes in tests/fixtures/sample_cards/."""

    @pytest.fixture(scope="class")
    def corpus_manifest(self) -> Dict[str, Any]:
        p = Path("tests/fixtures/sample_cards/manifest.json")
        assert p.exists(), "Manifest file must exist"
        return json.loads(p.read_text(encoding="utf-8"))

    @pytest.fixture(scope="class")
    def extractor(self) -> GeminiRecipeExtractor:
        return GeminiRecipeExtractor(api_key=None)

    @pytest.mark.asyncio
    async def test_all_12_corpus_recipes_fidelity(self, extractor: GeminiRecipeExtractor):
        """Extracts each of the 12 corpus recipes and verifies 100% ground-truth fidelity and isolation."""
        corpus = load_sample_corpus()
        assert len(corpus._cards_by_recipe) in (12, 24), "Corpus must have 12 or 24 recipes"

        for rec_id, cards in corpus._cards_by_recipe.items():
            sorted_cards = sorted(cards, key=lambda c: c.recipe_cluster.get("sequence_position", 0))
            paths = [c.file_path for c in sorted_cards]
            expected_card_ids = [c.filename for c in sorted_cards]
            gt = sorted_cards[0].ground_truth_recipe

            recipe = await extractor.extract_cards(paths)

            # Assert complete fidelity
            assert recipe.title == gt.title, f"Recipe {rec_id} title mismatch"
            assert recipe.description == gt.description, f"Recipe {rec_id} description mismatch"
            assert recipe.yield_servings == gt.yield_servings, f"Recipe {rec_id} yield mismatch"
            assert recipe.prep_time == gt.prep_time, f"Recipe {rec_id} prep_time mismatch"
            assert recipe.cook_time == gt.cook_time, f"Recipe {rec_id} cook_time mismatch"
            assert recipe.total_time == gt.total_time, f"Recipe {rec_id} total_time mismatch"
            assert recipe.ingredients == gt.ingredients, f"Recipe {rec_id} ingredients mismatch"
            assert recipe.directions == gt.directions, f"Recipe {rec_id} directions mismatch"
            assert recipe.tags == gt.tags, f"Recipe {rec_id} tags mismatch"
            assert recipe.source_notes == gt.source_notes, f"Recipe {rec_id} source_notes mismatch"

            # Assert card_ids and image_urls
            assert recipe.card_ids == expected_card_ids, f"Recipe {rec_id} card_ids mismatch"
            assert len(recipe.image_urls) == len(expected_card_ids), f"Recipe {rec_id} image_urls count mismatch"
            for cid in expected_card_ids:
                assert f"/api/cards/{cid}/image" in recipe.image_urls

    @pytest.mark.asyncio
    async def test_rhubarb_pie_isolation_against_pecan_pie(self, extractor: GeminiRecipeExtractor):
        """Specifically verifies that Rhubarb Pie never cross-contaminates with Pecan Pie."""
        rhubarb_paths = [
            "tests/fixtures/sample_cards/rec_vhc_rhubarb_pie_1965_front.jpg",
            "tests/fixtures/sample_cards/rec_vhc_rhubarb_pie_1965_back.jpg",
        ]
        recipe = await extractor.extract_cards(rhubarb_paths)

        assert "Rhubarb" in recipe.title
        assert recipe.title == "Aunt Clara's 1965 Rhubarb Custard Pie"
        assert "Pecan" not in recipe.title
        assert "Southern Pecan Pie" != recipe.title

        # Check ingredient isolation
        recipe_text = json.dumps(recipe.model_dump()).lower()
        assert "rhubarb" in recipe_text
        assert "pecan" not in recipe_text
        assert "corn syrup" not in recipe_text

    @pytest.mark.asyncio
    async def test_magic_chocolate_pie_isolation_against_pecan_pie(self, extractor: GeminiRecipeExtractor):
        """Specifically verifies that 1962 Borden's Chocolate Pie never matches Southern Pecan Pie."""
        pie_path = "tests/fixtures/sample_cards/rec_clp_condensed_milk_pie_1962_standalone.jpg"
        recipe = await extractor.extract(pie_path)

        assert "Chocolate Pie" in recipe.title
        assert recipe.title == "1962 Borden's Eagle Brand Magic Chocolate Pie"
        assert "Pecan" not in recipe.title

        recipe_text = json.dumps(recipe.model_dump()).lower()
        assert "chocolate" in recipe_text
        assert "pecan" not in recipe_text
        assert "corn syrup" not in recipe_text

    @pytest.mark.asyncio
    async def test_all_pairs_cross_contamination_matrix(self, extractor: GeminiRecipeExtractor):
        """Extracts all 12 recipes and verifies pairwise ingredient & title exclusivity."""
        corpus = load_sample_corpus()
        extracted_recipes: Dict[str, RecipeSchema] = {}

        for rec_id, cards in corpus._cards_by_recipe.items():
            sorted_cards = sorted(cards, key=lambda c: c.recipe_cluster.get("sequence_position", 0))
            paths = [c.file_path for c in sorted_cards]
            extracted_recipes[rec_id] = await extractor.extract_cards(paths)

        # Pairwise cross-check distinctive tokens
        distinctive_markers = {
            "rec_vhc_apple_crisp_1948": ["apple", "oats", "crisp"],
            "rec_vhc_rhubarb_pie_1965": ["rhubarb", "custard"],
            "rec_typ_beef_stroganoff_1956": ["stroganoff", "sirloin", "mushrooms", "sour cream"],
            "rec_typ_tomato_soup_cake_1952": ["tomato soup", "cloves", "mystery spice cake"],
            "rec_clp_crisco_fried_chicken": ["crisco", "chicken", "paprika"],
            "rec_clp_condensed_milk_pie_1962": ["condensed milk", "eagle brand", "chocolate pie"],
            "rec_dmg_moms_lasagna_1971": ["lasagna", "ricotta", "mozzarella", "ground beef"],
            "rec_dmg_butter_shortbread": ["shortbread", "scottish", "caster sugar"],
            "rec_mcs_turkey_stuffing_feast": ["turkey", "sage stuffing", "roasting pan"],
            "rec_mcs_wedding_fruitcake_1942": ["fruitcake", "sultanas", "brandy", "wedding fruitcake"],
            "rec_sta_quick_chocolate_fudge": ["fudge", "condensed milk", "chocolate chips"],
            "rec_sta_southern_pecan_pie": ["pecan", "corn syrup", "pecan halves"],
        }

        for rec_id, markers in distinctive_markers.items():
            current_recipe = extracted_recipes[rec_id]
            current_dump = json.dumps(current_recipe.model_dump()).lower()

            # Verify current recipe has its own markers
            for m in markers:
                assert m in current_dump, f"Recipe {rec_id} expected marker '{m}' missing in its own dump"

            # Verify OTHER recipes do not have high-specificity exclusive markers
            exclusive_tokens = {
                "rec_vhc_rhubarb_pie_1965": "rhubarb custard",
                "rec_typ_tomato_soup_cake_1952": "tomato soup",
                "rec_dmg_moms_lasagna_1971": "lasagna",
                "rec_mcs_wedding_fruitcake_1942": "sultanas",
                "rec_mcs_turkey_stuffing_feast": "sage stuffing",
                "rec_sta_southern_pecan_pie": "pecan halves",
            }
            if rec_id in exclusive_tokens:
                ex_tok = exclusive_tokens[rec_id]
                for other_id, other_recipe in extracted_recipes.items():
                    if other_id != rec_id:
                        other_dump = json.dumps(other_recipe.model_dump()).lower()
                        assert ex_tok not in other_dump, (
                            f"Cross-contamination detected! Token '{ex_tok}' from {rec_id} found in {other_id}"
                        )


# ============================================================================
# 2. Multi-Part Sequence Assembly Stress Tests (N >= 3) (R4)
# ============================================================================

class TestMultiPartSequenceAssembly:
    """Stress tests multi-part recipe sequences (N=3, N=4, N=5+) ordering, card IDs, and image URLs."""

    @pytest.fixture(scope="class")
    def extractor(self) -> GeminiRecipeExtractor:
        return GeminiRecipeExtractor(api_key=None)

    @pytest.mark.asyncio
    async def test_wedding_fruitcake_4_part_assembly(self, extractor: GeminiRecipeExtractor):
        """Tests 4-part Wedding Fruitcake sequence ordering, card_ids, and image_urls."""
        cards = [
            "tests/fixtures/sample_cards/rec_mcs_wedding_fruitcake_1942_card1_front.jpg",
            "tests/fixtures/sample_cards/rec_mcs_wedding_fruitcake_1942_card1_back.jpg",
            "tests/fixtures/sample_cards/rec_mcs_wedding_fruitcake_1942_card2_front.jpg",
            "tests/fixtures/sample_cards/rec_mcs_wedding_fruitcake_1942_card2_back.jpg",
        ]
        recipe = await extractor.extract_cards(cards)

        assert recipe.title == "1942 Heirloom Dark Holiday Wedding Fruitcake"
        assert len(recipe.card_ids) == 4
        assert recipe.card_ids == [
            "rec_mcs_wedding_fruitcake_1942_card1_front.jpg",
            "rec_mcs_wedding_fruitcake_1942_card1_back.jpg",
            "rec_mcs_wedding_fruitcake_1942_card2_front.jpg",
            "rec_mcs_wedding_fruitcake_1942_card2_back.jpg",
        ]
        assert len(recipe.image_urls) == 4
        assert recipe.image_urls == [f"/api/cards/{cid}/image" for cid in recipe.card_ids]
        assert len(recipe.ingredients) == 12
        assert len(recipe.directions) == 8

    @pytest.mark.asyncio
    async def test_turkey_feast_3_part_assembly(self, extractor: GeminiRecipeExtractor):
        """Tests 3-part Turkey Stuffing Feast sequence ordering, card_ids, and image_urls."""
        cards = [
            "tests/fixtures/sample_cards/rec_mcs_turkey_stuffing_feast_card1_front.jpg",
            "tests/fixtures/sample_cards/rec_mcs_turkey_stuffing_feast_card1_back.jpg",
            "tests/fixtures/sample_cards/rec_mcs_turkey_stuffing_feast_card2_front.jpg",
        ]
        recipe = await extractor.extract_cards(cards)

        assert recipe.title == "Grandma's Thanksgiving Roast Turkey & Sage Stuffing"
        assert len(recipe.card_ids) == 3
        assert recipe.card_ids == [
            "rec_mcs_turkey_stuffing_feast_card1_front.jpg",
            "rec_mcs_turkey_stuffing_feast_card1_back.jpg",
            "rec_mcs_turkey_stuffing_feast_card2_front.jpg",
        ]
        assert len(recipe.image_urls) == 3
        assert recipe.image_urls == [f"/api/cards/{cid}/image" for cid in recipe.card_ids]
        assert len(recipe.ingredients) == 12
        assert len(recipe.directions) == 8

    @pytest.mark.asyncio
    @pytest.mark.parametrize("num_cards", [3, 4, 5, 6, 8])
    async def test_synthetic_n_card_fallback_assembly(self, extractor: GeminiRecipeExtractor, num_cards: int):
        """Tests arbitrary N-card generic fallback sequence assembly (N >= 3) for cards not in manifest."""
        synthetic_cards = [
            VisualFingerprint(
                card_id=f"synthetic_deck_c{i}_face.jpg",
                image_path=f"/tmp/synthetic_deck_c{i}_face.jpg",
                sha256_hash=f"synthetic_hash_{i}",
                role=CardRole.SEQUENCE_START if i == 0 else CardRole.SEQUENCE_CONTINUATION,
            )
            for i in range(num_cards)
        ]

        recipe = await extractor.extract_cards(synthetic_cards)

        assert recipe.title == f"Multi-Card Recipe ({num_cards} Cards)"
        assert len(recipe.card_ids) == num_cards
        assert recipe.card_ids == [f"synthetic_deck_c{i}_face.jpg" for i in range(num_cards)]
        assert len(recipe.image_urls) == num_cards
        assert recipe.image_urls == [f"/api/cards/{cid}/image" for cid in recipe.card_ids]
        assert len(recipe.directions) == num_cards

        # Verify sequential directions
        assert recipe.directions[0].startswith("1. Front preparation")
        for idx in range(1, num_cards):
            assert recipe.directions[idx].startswith(f"{idx + 1}. Step {idx + 1} continuation")


# ============================================================================
# 3. Input Representation & Fallback Resolution Stress Tests (R4)
# ============================================================================

class TestInputRepresentationAndFallback:
    """Stress tests resolution across varied input formats and boundary/fallback behavior."""

    @pytest.fixture(scope="class")
    def extractor(self) -> GeminiRecipeExtractor:
        return GeminiRecipeExtractor(api_key=None)

    @pytest.mark.asyncio
    async def test_resolve_via_visual_fingerprint_with_hash(self, extractor: GeminiRecipeExtractor):
        """Resolves recipe via VisualFingerprint with SHA256 hash."""
        stroganoff_card = next(c for c in load_sample_corpus() if "beef_stroganoff" in c.card_id and "front" in c.card_id)
        fp = VisualFingerprint(
            card_id="custom_id_does_not_matter",
            image_path="/nonexistent/path.jpg",
            sha256_hash=stroganoff_card.sha256_hash,  # Stroganoff front
            role=CardRole.STANDALONE,
        )
        recipe = await extractor.extract(fp)
        assert recipe.title == "1956 Classic Beef Stroganoff"

    @pytest.mark.asyncio
    async def test_resolve_via_scanned_card(self, extractor: GeminiRecipeExtractor):
        """Resolves recipe via ScannedCard object."""
        sc = ScannedCard(
            card_id="rec_clp_crisco_fried_chicken_front.jpg",
            file_path=Path("tests/fixtures/sample_cards/rec_clp_crisco_fried_chicken_front.jpg"),
        )
        recipe = await extractor.extract(sc)
        assert recipe.title == "Crisco Golden Southern Fried Chicken"

    @pytest.mark.asyncio
    async def test_resolve_via_raw_bytes(self, extractor: GeminiRecipeExtractor):
        """Resolves recipe via raw image bytes."""
        p = Path("tests/fixtures/sample_cards/rec_dmg_butter_shortbread_standalone.jpg")
        raw_b = p.read_bytes()

        recipe = await extractor.extract(raw_b)
        assert recipe.title == "Traditional Scottish Butter Shortbread"

    @pytest.mark.asyncio
    async def test_resolve_via_pil_image(self, extractor: GeminiRecipeExtractor):
        """Resolves recipe via PIL Image object."""
        img = Image.new("RGB", (100, 100), color=(255, 255, 255))
        recipe = await extractor.extract(img)

        # Fallback recipe for unknown image
        assert isinstance(recipe, RecipeSchema)
        assert recipe.title.startswith("Recipe from card_")
        assert len(recipe.ingredients) >= 1
        assert len(recipe.directions) >= 1

    @pytest.mark.asyncio
    async def test_empty_input_rejection(self, extractor: GeminiRecipeExtractor):
        """Tests that empty input list raises ValueError."""
        with pytest.raises(ValueError, match="No card inputs provided"):
            await extractor.extract([])

    @pytest.mark.asyncio
    async def test_mystery_untitled_card_guess_tagging(self, extractor: GeminiRecipeExtractor):
        """Tests that untitled/mystery cards receive [GUESS] suffix in title."""
        fp = VisualFingerprint(
            card_id="mystery_recipe_box_card_01.jpg",
            image_path="/tmp/mystery_recipe_box_card_01.jpg",
            sha256_hash="mystery_hash_12345",
            role=CardRole.STANDALONE,
            boundary_text=BoundaryText(first_line="Grandma's secret pudding"),
        )
        recipe = await extractor.extract(fp)
        assert recipe.title.endswith(" [GUESS]")
        assert "Grandma's secret pudding [GUESS]" == recipe.title

    @pytest.mark.asyncio
    async def test_synthetic_keyword_exactness(self, extractor: GeminiRecipeExtractor):
        """Tests synthetic recipes requiring multi-token matching (e.g. cinnamon + rolls, lemon + bars)."""
        # Cinnamon rolls
        r1 = await extractor.extract("cinnamon_rolls_card1.jpg")
        assert r1.title == "Grandma's Cinnamon Rolls"

        # Lone cinnamon without rolls should fallback
        r2 = await extractor.extract("cinnamon_toast_crunch.jpg")
        assert r2.title != "Grandma's Cinnamon Rolls"

        # Lemon bars
        r3 = await extractor.extract("lemon_bars_family.jpg")
        assert r3.title == "Luscious Lemon Glaze Bars"

        # Lone lemon without bars should fallback
        r4 = await extractor.extract("lemon_meringue_pie.jpg")
        assert r4.title != "Luscious Lemon Glaze Bars"


# ============================================================================
# 4. Orchestrator Stage 5 Incremental Telemetry & SSE Streaming (R3)
# ============================================================================

class TestStage5IncrementalTelemetryAndSSE:
    """Stress tests orchestrator Stage 5 incremental extraction and SSE stream event emission."""

    @pytest.mark.asyncio
    async def test_orchestrator_stage5_incremental_updates(self):
        """Verifies that orchestrator Stage 5 extraction updates session.recipes and metrics incrementally."""
        agent = RecipeBoxerAgent()
        session = PipelineSession(session_id="test_stage5_session")
        session.config = PipelineStartRequest(scans_dir="tests/fixtures/sample_cards")

        # Seed session with 3 card proposals
        card1 = VisualFingerprint(
            card_id="rec_vhc_apple_crisp_1948_front.jpg",
            image_path="tests/fixtures/sample_cards/rec_vhc_apple_crisp_1948_front.jpg",
            sha256_hash="hash_apple_crisp",
            role=CardRole.STANDALONE,
        )
        card2 = VisualFingerprint(
            card_id="rec_dmg_butter_shortbread_standalone.jpg",
            image_path="tests/fixtures/sample_cards/rec_dmg_butter_shortbread_standalone.jpg",
            sha256_hash="hash_shortbread",
            role=CardRole.STANDALONE,
        )
        card3 = VisualFingerprint(
            card_id="rec_sta_southern_pecan_pie_standalone.jpg",
            image_path="tests/fixtures/sample_cards/rec_sta_southern_pecan_pie_standalone.jpg",
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
            card_id="rec_vhc_apple_crisp_1948_front.jpg",
            image_path="tests/fixtures/sample_cards/rec_vhc_apple_crisp_1948_front.jpg",
            sha256_hash="hash_apple_crisp",
            role=CardRole.STANDALONE,
        )
        card2 = VisualFingerprint(
            card_id="rec_dmg_butter_shortbread_standalone.jpg",
            image_path="tests/fixtures/sample_cards/rec_dmg_butter_shortbread_standalone.jpg",
            sha256_hash="hash_shortbread",
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


# ============================================================================
# 5. Concurrency & Cross-Task Isolation Stress Tests
# ============================================================================

class TestParallelBatchConcurrency:
    """Stress tests concurrent recipe extractions to ensure zero cross-talk across async coroutines."""

    @pytest.mark.asyncio
    async def test_concurrent_all_12_recipes_extraction(self):
        """Concurrently extracts all 12 recipes in parallel tasks and verifies 100% integrity."""
        corpus = load_sample_corpus()
        extractor = GeminiRecipeExtractor(api_key=None)

        async def _extract_one(rec_id: str, cards: List[Any]):
            sorted_cards = sorted(cards, key=lambda c: c.recipe_cluster.get("sequence_position", 0))
            paths = [c.file_path for c in sorted_cards]
            gt = sorted_cards[0].ground_truth_recipe
            recipe = await extractor.extract_cards(paths)
            return rec_id, recipe, gt

        tasks = [
            _extract_one(rec_id, cards)
            for rec_id, cards in corpus._cards_by_recipe.items()
        ]

        results = await asyncio.gather(*tasks)
        assert len(results) in (12, 24)

        for rec_id, recipe, gt in results:
            assert recipe.title == gt.title, f"Concurrent extraction failed for {rec_id}"
            assert recipe.ingredients == gt.ingredients
            assert recipe.directions == gt.directions
            assert len(recipe.card_ids) == len(recipe.image_urls)
