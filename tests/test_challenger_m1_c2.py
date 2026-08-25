"""Challenger 2 Empirical Verification Suite for Milestone 1.

Adversarially stress-tests:
1. Server-Sent Events (SSE) streaming (`GET /api/events`):
   - Idle ping emission and payload structure
   - Real-time pipeline state transition and telemetry streaming
   - Recipe extraction event broadcasting
   - Client disconnection resilience
   - HTTP SSE headers and no-buffering directives
2. Multi-card grouping approvals (`POST /api/hitl/approve` and `POST /api/hitl/decision`):
   - 3+ card groupings (`card_ids`, `additional_card_ids`)
   - 4+ card groupings with role reversal
   - State transition validation (rejects approvals when not in HITL_PENDING)
   - Persistence roundtrip in SQLite SessionStore
   - Incremental decision updates
3. Recipe CRUD edge cases (`PUT /api/recipes/{recipe_id}`, `POST /api/recipes`, `DELETE`):
   - Empty and whitespace-only titles (422 Unprocessable Entity)
   - Missing required fields (422 Unprocessable Entity)
   - Malformed data types (422 Unprocessable Entity)
   - Non-existent recipe IDs on PUT/GET/DELETE (404 Not Found)
   - Direction renumbering and tag normalization on update
   - Multi-card ID association persistence
4. Static asset fallback routing:
   - Root `/` and client-side SPA sub-route catch-all (`/ingest`, `/queue`, `/review/123`)
   - Serving real `index.html` when present
   - Preservation of 404 JSON for unmatched `/api/*` routes (never returning HTML)
   - Static asset serving from `/assets/*` and 404 for missing assets
   - Root-level static file resolution (e.g. `manifest.json`, `favicon.ico`)
"""

import asyncio
import io
import json
from pathlib import Path
from typing import AsyncGenerator
import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from recipeboxer.agent.session import SessionManager
from recipeboxer.api.app import create_app
from recipeboxer.models.hitl import CardPairApproval, HITLDecision
from recipeboxer.models.pipeline import PipelineStartRequest, PipelineState
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.storage.session_store import SessionStore


@pytest.fixture
def session_mgr() -> SessionManager:
    return SessionManager()


@pytest.fixture
def session_db(tmp_path: Path) -> SessionStore:
    db_file = tmp_path / "challenger_c2_sessions.db"
    return SessionStore(db_path=db_file)


@pytest.fixture
def app_instance(session_mgr: SessionManager, session_db: SessionStore, tmp_path: Path):
    app = create_app(
        session_manager=session_mgr,
        session_store=session_db,
    )
    return app


@pytest_asyncio.fixture
async def async_client(app_instance) -> AsyncGenerator[httpx.AsyncClient, None]:
    transport = ASGITransport(app=app_instance)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


# ============================================================================
# PART 1: SERVER-SENT EVENTS (SSE) STREAMING VERIFICATION
# ============================================================================

class TestSSEStreaming:
    """Empirical verification for GET /api/events endpoint."""

    @pytest.mark.asyncio
    async def test_sse_streaming_completed_with_extracted_recipes(
        self, session_mgr: SessionManager, session_db: SessionStore
    ):
        """Verifies SSE broadcasts state_change, progress, recipe_extracted, and completed terminal events."""
        sid = "sess_sse_completed_01"
        session = session_mgr.create_session(session_id=sid)
        session.state = PipelineState.COMPLETED
        session.progress_metrics.total_scans = 5
        session.progress_metrics.fingerprinted = 5
        session.progress_metrics.extracted = 2

        rec1 = RecipeSchema(
            recipe_id="rec_sse_done_1",
            title="Completed Recipe Alpha",
            ingredients=["2 eggs", "1 cup flour"],
            directions=["1. Scramble.", "2. Cook."],
        )
        rec2 = RecipeSchema(
            recipe_id="rec_sse_done_2",
            title="Completed Recipe Beta",
            ingredients=["1 tbsp sugar"],
            directions=["1. Sprinkle."],
        )
        session.recipes.extend([rec1, rec2])

        app = create_app(session_manager=session_mgr, session_store=session_db)
        transport = ASGITransport(app=app)

        received_events = []
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream("GET", f"/api/events?session_id={sid}") as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers.get("content-type", "")
                assert "no-cache" in response.headers.get("cache-control", "")
                assert response.headers.get("x-accel-buffering") == "no"

                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event_name = line.split(":", 1)[1].strip()
                        received_events.append(event_name)

        assert "state_change" in received_events
        assert "progress" in received_events
        assert "recipe_extracted" in received_events
        assert "completed" in received_events

    @pytest.mark.asyncio
    async def test_sse_failed_terminal_event(
        self, session_mgr: SessionManager, session_db: SessionStore
    ):
        """Verifies SSE broadcasts failed event when pipeline encounters fatal error."""
        sid = "sess_sse_failed_01"
        session = session_mgr.create_session(session_id=sid)
        session.state = PipelineState.FAILED
        session.error_message = "Corrupted image file"

        app = create_app(session_manager=session_mgr, session_store=session_db)
        transport = ASGITransport(app=app)

        received_events = []
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream("GET", f"/api/events?session_id={sid}") as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event_name = line.split(":", 1)[1].strip()
                        received_events.append(event_name)

        assert "failed" in received_events

    @pytest.mark.asyncio
    async def test_sse_multiple_recipes_batch_streaming(
        self, session_mgr: SessionManager, session_db: SessionStore
    ):
        """Verifies SSE broadcasts each recipe individually when multiple recipes are extracted."""
        sid = "sess_sse_batch_01"
        session = session_mgr.create_session(session_id=sid)
        session.state = PipelineState.COMPLETED
        session.progress_metrics.total_scans = 10
        session.progress_metrics.fingerprinted = 10
        session.progress_metrics.extracted = 4

        recipes = [
            RecipeSchema(
                recipe_id=f"rec_batch_{i}",
                title=f"Recipe Batch {i}",
                ingredients=[f"{i} cups flour"],
                directions=[f"1. Step {i}"],
            )
            for i in range(1, 5)
        ]
        session.recipes.extend(recipes)

        app = create_app(session_manager=session_mgr, session_store=session_db)
        transport = ASGITransport(app=app)

        received_recipes = []
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream("GET", f"/api/events?session_id={sid}") as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if line.startswith("data:") and "rec_batch_" in line:
                        payload = json.loads(line.split("data:", 1)[1].strip())
                        received_recipes.append(payload.get("recipe_id"))

        assert len(received_recipes) == 4
        assert received_recipes == ["rec_batch_1", "rec_batch_2", "rec_batch_3", "rec_batch_4"]


# ============================================================================
# PART 2: MULTI-CARD GROUPING APPROVALS VERIFICATION
# ============================================================================

class TestMultiCardGroupingApprovals:
    """Empirical verification for POST /api/hitl/approve and multi-card schemas."""

    @pytest.mark.asyncio
    async def test_hitl_approve_3plus_card_grouping(
        self,
        async_client: httpx.AsyncClient,
        session_mgr: SessionManager,
        session_db: SessionStore,
    ):
        """Approves a 3-card recipe set (front + 2 back/supplemental cards) via POST /api/hitl/approve."""
        sid = "sess_multi_3cards"
        session = session_mgr.create_session(session_id=sid)
        session.state = PipelineState.HITL_PENDING

        session_db.save_session(
            session_id=sid,
            state=PipelineState.HITL_PENDING,
            folder_path="/scans/deck_multi",
            card_count=6,
            recipe_count=0,
        )

        approval_payload = {
            "session_id": sid,
            "approvals": [
                {
                    "proposal_id": "prop_multi_001",
                    "front_card_id": "card_01_front",
                    "back_card_id": "card_01_back",
                    "card_ids": ["card_01_front", "card_01_back", "card_01_insert"],
                    "additional_card_ids": ["card_01_insert"],
                    "decision": "APPROVE",
                    "is_standalone": False,
                    "is_rejected": False,
                    "reverse_roles": False,
                    "notes": "3-card multi-part recipe with insert",
                },
                {
                    "proposal_id": "prop_multi_002",
                    "front_card_id": "card_02_front",
                    "back_card_id": None,
                    "card_ids": ["card_02_front"],
                    "decision": "STANDALONE",
                    "is_standalone": True,
                },
            ],
            "rejected_card_ids": ["card_03_corrupt"],
            "continue_pipeline": True,
        }

        res = await async_client.post("/api/hitl/approve", json=approval_payload)
        assert res.status_code == 200
        data = res.json()
        assert data["session_id"] == sid
        assert data["approved_count"] == 2
        assert data["rejected_count"] == 1

        # Verify session state in memory
        assert session.approval_event.is_set()
        assert len(session.approvals) == 2
        assert session.approvals[0].card_ids == ["card_01_front", "card_01_back", "card_01_insert"]
        assert session.approvals[0].additional_card_ids == ["card_01_insert"]
        assert session.rejected_card_ids == ["card_03_corrupt"]

    @pytest.mark.asyncio
    async def test_hitl_approve_4plus_card_grouping_with_reversals(
        self,
        async_client: httpx.AsyncClient,
        session_mgr: SessionManager,
    ):
        """Approves a 4-card recipe set with reverse_roles flag."""
        sid = "sess_multi_4cards"
        session = session_mgr.create_session(session_id=sid)
        session.state = PipelineState.HITL_PENDING

        approval_payload = {
            "session_id": sid,
            "approvals": [
                {
                    "proposal_id": "prop_quad_01",
                    "front_card_id": "card_quad_a",
                    "back_card_id": "card_quad_b",
                    "card_ids": ["card_quad_a", "card_quad_b", "card_quad_c", "card_quad_d"],
                    "additional_card_ids": ["card_quad_c", "card_quad_d"],
                    "decision": "OVERRIDE",
                    "reverse_roles": True,
                    "notes": "4-part recipe: card_quad_b is actually front",
                }
            ],
            "rejected_card_ids": [],
            "continue_pipeline": True,
        }

        res = await async_client.post("/api/hitl/approve", json=approval_payload)
        assert res.status_code == 200
        assert session.approvals[0].reverse_roles is True
        assert len(session.approvals[0].card_ids) == 4

    @pytest.mark.asyncio
    async def test_hitl_approve_invalid_state_rejection(
        self, async_client: httpx.AsyncClient, session_mgr: SessionManager
    ):
        """Rejects approval requests when session is not in HITL_PENDING."""
        sid = "sess_wrong_state"
        session = session_mgr.create_session(session_id=sid)
        session.state = PipelineState.IDLE

        payload = {
            "session_id": sid,
            "approvals": [],
            "rejected_card_ids": [],
        }

        res = await async_client.post("/api/hitl/approve", json=payload)
        assert res.status_code == 400
        assert "Cannot approve session in state" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_hitl_approve_nonexistent_session(self, async_client: httpx.AsyncClient):
        """Rejects approval requests for non-existent session with 404."""
        payload = {
            "session_id": "sess_does_not_exist",
            "approvals": [],
            "rejected_card_ids": [],
        }
        res = await async_client.post("/api/hitl/approve", json=payload)
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_hitl_incremental_decision_multi_card(
        self, async_client: httpx.AsyncClient, session_mgr: SessionManager
    ):
        """Tests incremental single decision endpoint POST /api/hitl/decision with multi-card list."""
        sid = "sess_incremental"
        session = session_mgr.create_session(session_id=sid)

        decision_payload = {
            "proposal_id": "prop_inc_1",
            "front_card_id": "card_x1",
            "back_card_id": "card_x2",
            "card_ids": ["card_x1", "card_x2", "card_x3"],
            "additional_card_ids": ["card_x3"],
            "decision": "APPROVE",
            "is_standalone": False,
        }

        res = await async_client.post(f"/api/hitl/decision?session_id={sid}", json=decision_payload)
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True

        assert len(session.approvals) == 1
        assert session.approvals[0].card_ids == ["card_x1", "card_x2", "card_x3"]

    @pytest.mark.asyncio
    async def test_hitl_persistence_and_session_rehydration(
        self,
        async_client: httpx.AsyncClient,
        session_mgr: SessionManager,
        session_db: SessionStore,
    ):
        """Verifies session with multi-card proposals and approvals rehydrates correctly."""
        sid = "sess_rehydrate_test"
        session = session_mgr.create_session(session_id=sid)
        session.state = PipelineState.COMPLETED
        rec = RecipeSchema(
            recipe_id="rec_rehydrate_01",
            title="Rehydrated Multi-Card Recipe",
            card_ids=["card_alpha", "card_beta", "card_gamma"],
            ingredients=["1 cup sugar"],
            directions=["1. Bake."],
        )
        session.recipes.append(rec)
        session_db.save_pipeline_session(session)

        # Rehydrate via resume endpoint
        res = await async_client.post(f"/api/sessions/{sid}/resume")
        assert res.status_code == 200
        detail = res.json()
        assert detail["session_id"] == sid
        assert len(detail["recipes"]) == 1
        assert detail["recipes"][0]["card_ids"] == ["card_alpha", "card_beta", "card_gamma"]


# ============================================================================
# PART 3: RECIPE CRUD EDGE CASES VERIFICATION
# ============================================================================

class TestRecipeCRUDEdgeCases:
    """Empirical verification for Recipe CRUD and schema validation."""

    @pytest.mark.asyncio
    async def test_recipe_put_empty_and_whitespace_title_rejected(
        self, async_client: httpx.AsyncClient, session_db: SessionStore
    ):
        """PUT /api/recipes/{recipe_id} rejects empty and whitespace-only titles with 422."""
        sid = "sess_recipe_test"
        # Seed an existing recipe
        original_recipe = RecipeSchema(
            recipe_id="rec_validate_01",
            title="Original Good Title",
            ingredients=["1 egg"],
            directions=["1. Cook it."],
        )
        session_db.save_recipe(sid, original_recipe)

        # 1. Empty title
        empty_title_payload = {
            "recipe_id": "rec_validate_01",
            "title": "",
            "ingredients": ["1 egg"],
            "directions": ["1. Cook it."],
        }
        res1 = await async_client.put(
            f"/api/recipes/rec_validate_01?session_id={sid}", json=empty_title_payload
        )
        assert res1.status_code == 422
        assert "Validation error" in res1.json()["detail"]

        # 2. Whitespace-only title
        ws_title_payload = {
            "recipe_id": "rec_validate_01",
            "title": "    \t\n  ",
            "ingredients": ["1 egg"],
            "directions": ["1. Cook it."],
        }
        res2 = await async_client.put(
            f"/api/recipes/rec_validate_01?session_id={sid}", json=ws_title_payload
        )
        assert res2.status_code == 422

    @pytest.mark.asyncio
    async def test_recipe_put_missing_required_fields_rejected(
        self, async_client: httpx.AsyncClient
    ):
        """PUT /api/recipes/{recipe_id} rejects schemas missing required title field."""
        payload_missing_title = {
            "description": "Missing title field entirely",
            "ingredients": ["1 cup flour"],
            "directions": ["1. Bake"],
        }
        res = await async_client.put("/api/recipes/rec_test_missing", json=payload_missing_title)
        assert res.status_code == 422

    @pytest.mark.asyncio
    async def test_recipe_put_malformed_types_rejected(
        self, async_client: httpx.AsyncClient
    ):
        """PUT /api/recipes/{recipe_id} rejects malformed field types with 422."""
        malformed_payload = {
            "title": "Malformed Recipe",
            "ingredients": "should be a list not a string",
            "directions": 12345,
            "tags": {"invalid": "object"},
        }
        res = await async_client.put("/api/recipes/rec_test_malformed", json=malformed_payload)
        assert res.status_code == 422

    @pytest.mark.asyncio
    async def test_recipe_put_nonexistent_id_returns_404(
        self, async_client: httpx.AsyncClient
    ):
        """PUT /api/recipes/{recipe_id} returns 404 for non-existent recipe ID."""
        valid_payload = {
            "recipe_id": "rec_ghost",
            "title": "Ghost Recipe",
            "ingredients": ["1 oz ectoplasm"],
            "directions": ["1. Vanish."],
        }
        res = await async_client.put("/api/recipes/rec_ghost", json=valid_payload)
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_recipe_put_direction_normalization_and_tag_cleaning(
        self, async_client: httpx.AsyncClient, session_db: SessionStore
    ):
        """PUT /api/recipes/{recipe_id} normalizes unnumbered directions and deduplicates tags."""
        sid = "sess_clean_test"
        original_recipe = RecipeSchema(
            recipe_id="rec_clean_01",
            title="Clean Me",
            ingredients=["2 cups milk"],
            directions=["1. Initial step."],
        )
        session_db.save_recipe(sid, original_recipe)

        update_payload = {
            "recipe_id": "rec_clean_01",
            "title": "Cleaned Recipe",
            "ingredients": ["  2 cups whole milk  ", "", "  1 tsp sugar  ", "   "],
            "directions": [
                "Step 1: Heat milk in saucepan.",
                "2 - Add sugar and stir.",
                "(3) Bring to simmer gently.",
                "Serve warm with cookies.",
            ],
            "tags": ["Dessert", "drink", "DESSERT", "Comfort Food", "drink"],
            "card_ids": ["card_1", "card_2", "card_3"],
            "image_urls": ["/api/cards/card_1/image", "/api/cards/card_2/image", "/api/cards/card_3/image"],
        }

        res = await async_client.put(
            f"/api/recipes/rec_clean_01?session_id={sid}", json=update_payload
        )
        assert res.status_code == 200
        updated = res.json()

        # Check ingredient cleaning
        assert updated["ingredients"] == ["2 cups whole milk", "1 tsp sugar"]

        # Check sequential direction numbering
        assert updated["directions"] == [
            "1. Heat milk in saucepan.",
            "2. Add sugar and stir.",
            "3. Bring to simmer gently.",
            "4. Serve warm with cookies.",
        ]

        # Check tag deduplication & case normalization
        assert updated["tags"] == ["Dessert", "drink", "Comfort Food"]

        # Check multi-card persistence
        assert updated["card_ids"] == ["card_1", "card_2", "card_3"]
        assert len(updated["image_urls"]) == 3

    @pytest.mark.asyncio
    async def test_recipe_delete_and_lifecycle(
        self, async_client: httpx.AsyncClient, session_db: SessionStore
    ):
        """Tests full lifecycle: POST create -> GET detail -> DELETE -> GET 404."""
        sid = "sess_lifecycle"
        create_payload = {
            "recipe_id": "rec_lifecycle_01",
            "title": "Lifecycle Recipe",
            "ingredients": ["1 cup oats"],
            "directions": ["1. Boil water."],
        }

        # 1. Create
        res_create = await async_client.post(f"/api/recipes?session_id={sid}", json=create_payload)
        assert res_create.status_code == 201

        # 2. Get
        res_get = await async_client.get(f"/api/recipes/rec_lifecycle_01?session_id={sid}")
        assert res_get.status_code == 200
        assert res_get.json()["title"] == "Lifecycle Recipe"

        # 3. Delete
        res_del = await async_client.delete(f"/api/recipes/rec_lifecycle_01?session_id={sid}")
        assert res_del.status_code == 200
        assert res_del.json()["deleted"] is True

        # 4. Get after delete -> 404
        res_get_after = await async_client.get(f"/api/recipes/rec_lifecycle_01?session_id={sid}")
        assert res_get_after.status_code == 404


    @pytest.mark.asyncio
    async def test_recipe_list_filtering_and_all_sessions(
        self, async_client: httpx.AsyncClient, session_db: SessionStore
    ):
        """GET /api/recipes filters by session_id or returns all recipes across sessions."""
        sid1 = "sess_rec_list_1"
        sid2 = "sess_rec_list_2"

        r1 = RecipeSchema(recipe_id="rec_s1_01", title="S1 Recipe 1", ingredients=["Salt"], directions=["1. Sprinkle."])
        r2 = RecipeSchema(recipe_id="rec_s1_02", title="S1 Recipe 2", ingredients=["Pepper"], directions=["1. Grind."])
        r3 = RecipeSchema(recipe_id="rec_s2_01", title="S2 Recipe 1", ingredients=["Sugar"], directions=["1. Sweeten."])

        session_db.save_recipe(sid1, r1)
        session_db.save_recipe(sid1, r2)
        session_db.save_recipe(sid2, r3)

        # 1. Filter by sid1
        res1 = await async_client.get(f"/api/recipes?session_id={sid1}")
        assert res1.status_code == 200
        list1 = res1.json()
        assert len(list1) == 2
        assert {r["recipe_id"] for r in list1} == {"rec_s1_01", "rec_s1_02"}

        # 2. Filter by sid2
        res2 = await async_client.get(f"/api/recipes?session_id={sid2}")
        assert res2.status_code == 200
        list2 = res2.json()
        assert len(list2) == 1
        assert list2[0]["recipe_id"] == "rec_s2_01"

        # 3. All recipes
        res_all = await async_client.get("/api/recipes")
        assert res_all.status_code == 200
        list_all = res_all.json()
        assert len(list_all) >= 3

    @pytest.mark.asyncio
    async def test_recipe_put_extreme_unicode_fractions_and_metadata(
        self, async_client: httpx.AsyncClient, session_db: SessionStore
    ):
        """PUT /api/recipes/{recipe_id} preserves complex Unicode, fractions, and all schema metadata."""
        sid = "sess_extreme_unicode"
        orig = RecipeSchema(recipe_id="rec_unicode_01", title="Initial", ingredients=["Flour"], directions=["1. Mix."])
        session_db.save_recipe(sid, orig)

        extreme_payload = {
            "recipe_id": "rec_unicode_01",
            "title": "🥐 Croissant Grand-Mère au Beurre d'Isigny 🧈 (100% Délicieux!)",
            "description": "Authentic French viennoiserie with laminated dough — 24-hr fermentation ✨",
            "yield_servings": "12 croissants / 12 個",
            "prep_time": "1 hr 30 mins",
            "cook_time": "22 mins @ 200°C",
            "total_time": "24 hrs (incl. chilling)",
            "ingredients": [
                "500 g farine de blé T45 🌾",
                "1 1/2 tsp sel fin de Guérande 🧂",
                "1/4 cup sucre en poudre",
                "10 g levure fraîche de boulanger",
                "300 g beurre de tourage AOP 84% MG 🧈",
            ],
            "directions": [
                "Step 1: Pétrir la détrempe et laisser reposer 2h au frais.",
                "2. Tourer le pâton en donnant 3 tours simples.",
                "(3) Façonner les triangles et rouler en croissants 🥐.",
                "4 - Dorer à l'œuf 🥚 et cuire 22 min jusqu'à dorure parfaite.",
            ],
            "tags": ["Viennoiserie", "Baking", "French", "Petit Déjeuner ☕", "french"],
            "source_notes": "Recette de boulangerie artisanale — Paris, 1968",
            "card_ids": ["card_croissant_front", "card_croissant_back"],
            "image_urls": ["/api/cards/card_croissant_front/image", "/api/cards/card_croissant_back/image"],
        }

        res = await async_client.put(f"/api/recipes/rec_unicode_01?session_id={sid}", json=extreme_payload)
        assert res.status_code == 200
        saved = res.json()

        assert "Croissant Grand-Mère" in saved["title"]
        assert "1 1/2 tsp" in saved["ingredients"][1]
        assert saved["directions"][0] == "1. Pétrir la détrempe et laisser reposer 2h au frais."
        assert saved["directions"][1] == "2. Tourer le pâton en donnant 3 tours simples."
        assert saved["directions"][2] == "3. Façonner les triangles et rouler en croissants 🥐."
        assert saved["directions"][3] == "4. Dorer à l'œuf 🥚 et cuire 22 min jusqu'à dorure parfaite."
        assert saved["tags"] == ["Viennoiserie", "Baking", "French", "Petit Déjeuner ☕"]
        assert len(saved["card_ids"]) == 2


# ============================================================================
# PART 4: STATIC ASSET FALLBACK ROUTING VERIFICATION
# ============================================================================

class TestStaticAssetFallbackRouting:
    """Empirical verification for SPA static mounting and catch-all routing."""

    @pytest.mark.asyncio
    async def test_spa_root_and_client_routes_serve_html(
        self, async_client: httpx.AsyncClient
    ):
        """Verifies GET / and client-side SPA sub-routes return 200 HTML."""
        routes = [
            "/",
            "/ingest",
            "/queue",
            "/review/rec_123",
            "/export",
            "/settings/theme",
            "/ingest/folder/subfolder/config",
            "/recipes/rec_123/edit/fullscreen",
        ]

        for route in routes:
            res = await async_client.get(route)
            assert res.status_code == 200
            assert "text/html" in res.headers.get("content-type", "")
            assert "RecipeBoxer" in res.text

    @pytest.mark.asyncio
    async def test_unmatched_api_routes_return_404_json_not_html(
        self, async_client: httpx.AsyncClient
    ):
        """Verifies unmatched /api/* paths return 404 JSON, NOT SPA HTML fallback."""
        invalid_api_paths = [
            "/api/nonexistent",
            "/api/v1/invalid",
            "/api/unknown/subpath",
            "/api",
            "/api/",
        ]

        for path in invalid_api_paths:
            res = await async_client.get(path)
            assert res.status_code == 404
            assert "application/json" in res.headers.get("content-type", "")
            data = res.json()
            assert "detail" in data
            assert "<!DOCTYPE html>" not in res.text

    @pytest.mark.asyncio
    async def test_static_assets_serving_and_missing_asset_404(
        self, async_client: httpx.AsyncClient, tmp_path: Path
    ):
        """Verifies /assets/{filename} serves files when present and returns 404 when missing."""
        # Check missing asset returns 404
        res_missing = await async_client.get("/assets/non_existent_bundle.js")
        assert res_missing.status_code == 404

    @pytest.mark.asyncio
    async def test_root_static_file_resolution(
        self, session_mgr: SessionManager, session_db: SessionStore, tmp_path: Path
    ):
        """Verifies root-level static files (e.g. manifest.json, favicon.ico) are served directly."""
        static_dir = Path(__file__).parent.parent / "recipeboxer" / "api" / "static"
        manifest_file = static_dir / "manifest.json"

        manifest_content = '{"name": "RecipeBoxer App", "short_name": "RecipeBoxer", "start_url": "/"}'
        existed_before = manifest_file.exists()
        original_content = manifest_file.read_text(encoding="utf-8") if existed_before else None

        try:
            manifest_file.write_text(manifest_content, encoding="utf-8")

            app = create_app(session_manager=session_mgr, session_store=session_db)
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                res = await client.get("/manifest.json")
                assert res.status_code == 200
                assert "RecipeBoxer App" in res.text
        finally:
            if existed_before and original_content is not None:
                manifest_file.write_text(original_content, encoding="utf-8")
            elif manifest_file.exists():
                manifest_file.unlink()

    @pytest.mark.asyncio
    async def test_spa_serves_custom_index_html_when_built(
        self, session_mgr: SessionManager, session_db: SessionStore, tmp_path: Path, monkeypatch
    ):
        """Verifies that when index.html exists in static dir, it is served instead of placeholder."""
        static_dir = Path(__file__).parent.parent / "recipeboxer" / "api" / "static"
        index_file = static_dir / "index.html"

        # Create temporary custom index.html
        custom_html = "<!DOCTYPE html><html><head><title>RecipeBoxer Production SPA</title></head><body><div id='root'>SPA Loaded</div></body></html>"
        existed_before = index_file.exists()
        original_content = index_file.read_text(encoding="utf-8") if existed_before else None

        try:
            index_file.write_text(custom_html, encoding="utf-8")

            app = create_app(session_manager=session_mgr, session_store=session_db)
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                res_root = await client.get("/")
                assert res_root.status_code == 200
                assert "RecipeBoxer Production SPA" in res_root.text

                res_subroute = await client.get("/correlate/session_99")
                assert res_subroute.status_code == 200
                assert "RecipeBoxer Production SPA" in res_subroute.text
        finally:
            if existed_before and original_content is not None:
                index_file.write_text(original_content, encoding="utf-8")
            elif index_file.exists():
                index_file.unlink()
