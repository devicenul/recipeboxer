import asyncio
import io
from pathlib import Path
from typing import AsyncGenerator
import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from PIL import Image

from recipeboxer.agent.session import SessionManager
from recipeboxer.api.app import create_app
from recipeboxer.models.pipeline import PipelineState
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.storage.session_store import SessionStore
from tests.conftest import MockGeminiClient, SQLiteFingerprintCache, SyntheticDeckInfo


def _create_dummy_image_bytes(format_name: str = "JPEG") -> bytes:
    """Helper to generate dummy valid image bytes for testing."""
    buf = io.BytesIO()
    img = Image.new("RGB", (100, 100), color=(240, 230, 200))
    img.save(buf, format=format_name)
    return buf.getvalue()


@pytest.fixture
def test_session_manager() -> SessionManager:
    return SessionManager()


@pytest.fixture
def test_session_store(tmp_path: Path) -> SessionStore:
    db_file = tmp_path / "test_api_sessions.db"
    return SessionStore(db_path=db_file)


@pytest.fixture
def app(test_session_manager: SessionManager, test_session_store: SessionStore):
    return create_app(
        session_manager=test_session_manager,
        session_store=test_session_store,
    )


@pytest_asyncio.fixture
async def client(app) -> AsyncGenerator[httpx.AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestAPIRoutes:
    """Tests covering session persistence, card uploads, image streaming, candidate ranking, recipe CRUD, and exports."""

    # ------------------------------------------------------------------------
    # 1. Session Persistence Endpoints
    # ------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_sessions_list_and_detail(
        self, client: httpx.AsyncClient, test_session_manager: SessionManager, test_session_store: SessionStore
    ):
        """Test GET /api/sessions and GET /api/sessions/{session_id}."""
        # Create in memory and in store
        sess = test_session_manager.create_session(session_id="sess_alpha")
        test_session_store.save_session(
            session_id="sess_alpha",
            state=PipelineState.COMPLETED,
            folder_path="/scans/folder_a",
            card_count=8,
            recipe_count=3,
        )

        res = await client.get("/api/sessions")
        assert res.status_code == 200
        sessions = res.json()
        assert len(sessions) >= 1
        assert any(s["session_id"] == "sess_alpha" for s in sessions)

        res_detail = await client.get("/api/sessions/sess_alpha")
        assert res_detail.status_code == 200
        detail = res_detail.json()
        assert detail["session_id"] == "sess_alpha"
        assert detail["card_count"] == 8
        assert detail["recipe_count"] == 3

    @pytest.mark.asyncio
    async def test_session_detail_not_found(self, client: httpx.AsyncClient):
        """Test GET /api/sessions/{session_id} returns 404 for unknown session."""
        res = await client.get("/api/sessions/sess_nonexistent")
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_session(
        self, client: httpx.AsyncClient, test_session_manager: SessionManager, test_session_store: SessionStore
    ):
        """Test DELETE /api/sessions/{session_id}."""
        sid = "sess_to_del"
        test_session_manager.create_session(session_id=sid)
        test_session_store.save_session(session_id=sid, state=PipelineState.COMPLETED)

        res = await client.delete(f"/api/sessions/{sid}")
        assert res.status_code == 200
        assert res.json()["deleted"] is True

        res_check = await client.get(f"/api/sessions/{sid}")
        assert res_check.status_code == 404

    @pytest.mark.asyncio
    async def test_resume_session(
        self, client: httpx.AsyncClient, test_session_store: SessionStore
    ):
        """Test POST /api/sessions/{session_id}/resume rehydrates session."""
        sid = "sess_rehydrate"
        test_session_store.save_session(
            session_id=sid,
            state=PipelineState.COMPLETED,
            card_count=4,
            recipe_count=2,
        )

        res = await client.post(f"/api/sessions/{sid}/resume")
        assert res.status_code == 200
        data = res.json()
        assert data["session_id"] == sid
        assert data["status"] == "COMPLETED"

    # ------------------------------------------------------------------------
    # 2. Card Upload & Image Streaming Endpoints
    # ------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_multipart_card_upload(self, client: httpx.AsyncClient):
        """Test POST /api/cards/upload accepts multipart image files."""
        img1 = _create_dummy_image_bytes("JPEG")
        img2 = _create_dummy_image_bytes("PNG")

        files = [
            ("files", ("card_front.jpg", img1, "image/jpeg")),
            ("files", ("card_back.png", img2, "image/png")),
        ]
        data = {
            "mode": "parallel",
            "max_concurrency": 2,
        }

        res = await client.post("/api/cards/upload", files=files, data=data)
        assert res.status_code == 200
        result = res.json()
        assert "session_id" in result
        assert result["count"] == 2
        assert len(result["card_ids"]) == 2

    @pytest.mark.asyncio
    async def test_card_image_streaming(
        self, client: httpx.AsyncClient, test_session_store: SessionStore, tmp_path: Path
    ):
        """Test GET /api/cards/{card_id}/image streams image file."""
        img_bytes = _create_dummy_image_bytes("JPEG")
        img_file = tmp_path / "card_01.jpg"
        img_file.write_bytes(img_bytes)

        sid = "sess_img"
        test_session_store.save_session(sid, state=PipelineState.INGESTING)
        test_session_store.save_card(sid, card_id="card_01.jpg", image_path=str(img_file))

        res = await client.get(f"/api/cards/card_01.jpg/image?session_id={sid}")
        assert res.status_code == 200
        assert "image/jpeg" in res.headers["content-type"]
        assert len(res.content) == len(img_bytes)

    @pytest.mark.asyncio
    async def test_card_image_not_found_404(self, client: httpx.AsyncClient):
        """Test GET /api/cards/{card_id}/image returns 404 for unknown image."""
        res = await client.get("/api/cards/unknown_card.jpg/image")
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_card_image_path_traversal_attempts_blocked(
        self, client: httpx.AsyncClient, test_session_store: SessionStore, tmp_path: Path
    ):
        """Test GET /api/cards/{card_id}/image rejects path traversal payloads and never leaks host files."""
        # Create a legitimate session and card in store
        scans_dir = tmp_path / "legit_scans"
        scans_dir.mkdir(parents=True, exist_ok=True)
        legit_img = scans_dir / "valid_card.jpg"
        legit_img.write_bytes(_create_dummy_image_bytes("JPEG"))

        sid = "sess_sec_test"
        test_session_store.save_session(sid, state=PipelineState.INGESTING, folder_path=str(scans_dir))
        test_session_store.save_card(sid, card_id="valid_card.jpg", image_path=str(legit_img))

        # 1. Legitimate access works
        res_legit = await client.get(f"/api/cards/valid_card.jpg/image?session_id={sid}")
        assert res_legit.status_code == 200

        # 2. Traversal payloads must all fail with 404 or 400
        traversal_payloads = [
            "..%2F..%2Fetc%2Fpasswd",
            "..%2F..%2F..%2F..%2Fetc%2Fhosts",
            "%2Fetc%2Fpasswd",
            "%2Fetc%2Fhosts",
            "/etc/passwd",
            "/etc/hosts",
            "pyproject.toml",
            "..%2F..%2Frecipeboxer%2Fapi%2Fapp.py",
            "valid_card.jpg%00.png",
            "nonexistent_file.txt",
        ]

        for payload in traversal_payloads:
            res = await client.get(f"/api/cards/{payload}/image")
            assert res.status_code in (400, 404), f"Payload '{payload}' returned {res.status_code} without session_id"
            assert "root:" not in res.text
            assert "[project]" not in res.text

            res_with_sid = await client.get(f"/api/cards/{payload}/image?session_id={sid}")
            assert res_with_sid.status_code in (400, 404), f"Payload '{payload}' returned {res_with_sid.status_code} with session_id"
            assert "root:" not in res_with_sid.text
            assert "[project]" not in res_with_sid.text

    # ------------------------------------------------------------------------
    # 3. Candidate Drawer & HITL Endpoints
    # ------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_candidate_ranking_endpoint(
        self, client: httpx.AsyncClient, test_session_manager: SessionManager, test_session_store: SessionStore
    ):
        """Test GET /api/hitl/candidates returns ranked alternatives with feature breakdown."""
        sid = "sess_candidates"
        session = test_session_manager.create_session(session_id=sid)
        test_session_store.save_session(sid, state=PipelineState.HITL_PENDING)

        test_session_store.save_card(sid, card_id="front_1.jpg", image_path="/path/front_1.jpg")
        test_session_store.save_card(sid, card_id="back_1.jpg", image_path="/path/back_1.jpg")
        test_session_store.save_card(sid, card_id="back_2.jpg", image_path="/path/back_2.jpg")

        res = await client.get(f"/api/hitl/candidates?session_id={sid}&card_id=front_1.jpg")
        assert res.status_code == 200
        data = res.json()
        assert data["session_id"] == sid
        assert data["card_id"] == "front_1.jpg"
        assert len(data["candidates"]) == 2
        assert data["total_candidates"] == 2
        assert "score" in data["candidates"][0]

    @pytest.mark.asyncio
    async def test_hitl_single_decision(
        self, client: httpx.AsyncClient, test_session_manager: SessionManager
    ):
        """Test POST /api/hitl/decision records incremental decision."""
        sid = "sess_decision"
        session = test_session_manager.create_session(session_id=sid)
        session.state = PipelineState.HITL_PENDING

        decision_payload = {
            "proposal_id": "prop_01",
            "ordered_card_ids": ["card_01.jpg", "card_02.jpg", "card_03.jpg"],
            "decision": "APPROVE",
        }

        res = await client.post(f"/api/hitl/decision?session_id={sid}", json=decision_payload)
        assert res.status_code == 200
        assert res.json()["success"] is True
        assert len(session.approvals) == 1
        assert session.approvals[0].ordered_card_ids == ["card_01.jpg", "card_02.jpg", "card_03.jpg"]

    # ------------------------------------------------------------------------
    # 4. Recipe CRUD Endpoints
    # ------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_recipe_crud_endpoints(
        self, client: httpx.AsyncClient, test_session_store: SessionStore
    ):
        """Test full recipe CRUD lifecycle via REST API."""
        sid = "sess_recipe_crud"
        test_session_store.save_session(sid, state=PipelineState.COMPLETED)

        # 1. Create Recipe
        new_recipe = {
            "title": "Lemon Drizzle Cake",
            "description": "Zesty sponge cake with sweet lemon syrup glaze.",
            "yield_servings": "8 servings",
            "prep_time": "20 mins",
            "cook_time": "35 mins",
            "total_time": "55 mins",
            "ingredients": ["225g butter", "225g caster sugar", "4 eggs", "225g flour", "1 lemon"],
            "directions": ["1. Cream butter and sugar.", "2. Beat in eggs and fold in flour.", "3. Bake at 180°C."],
            "tags": ["Baking", "Dessert", "Citrus"],
        }

        res_create = await client.post(f"/api/recipes?session_id={sid}", json=new_recipe)
        assert res_create.status_code == 201
        created = res_create.json()
        assert created["title"] == "Lemon Drizzle Cake"
        rec_id = created["recipe_id"]

        # 2. List Recipes
        res_list = await client.get(f"/api/recipes?session_id={sid}")
        assert res_list.status_code == 200
        recipes = res_list.json()
        assert len(recipes) == 1
        assert recipes[0]["recipe_id"] == rec_id

        # 3. Get Single Recipe
        res_get = await client.get(f"/api/recipes/{rec_id}?session_id={sid}")
        assert res_get.status_code == 200
        assert res_get.json()["title"] == "Lemon Drizzle Cake"

        # 4. Update Recipe (Inline editor)
        update_payload = dict(created)
        update_payload["title"] = "Glazed Lemon Drizzle Cake"
        update_payload["yield_servings"] = "10 slices"

        res_update = await client.put(f"/api/recipes/{rec_id}?session_id={sid}", json=update_payload)
        assert res_update.status_code == 200
        updated = res_update.json()
        assert updated["title"] == "Glazed Lemon Drizzle Cake"
        assert updated["yield_servings"] == "10 slices"

        # 5. Delete Recipe
        res_del = await client.delete(f"/api/recipes/{rec_id}?session_id={sid}")
        assert res_del.status_code == 200
        assert res_del.json()["deleted"] is True

        res_after = await client.get(f"/api/recipes/{rec_id}?session_id={sid}")
        assert res_after.status_code == 404

    # ------------------------------------------------------------------------
    # 5. Multi-Format Export Endpoints
    # ------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_export_endpoints(
        self, client: httpx.AsyncClient, test_session_store: SessionStore
    ):
        """Test GET /api/export/csv, GET /api/export/json-ld, and GET /api/export/json."""
        sid = "sess_export"
        test_session_store.save_session(sid, state=PipelineState.COMPLETED)

        recipe = RecipeSchema(
            title="Blueberry Muffins",
            description="Fresh morning blueberry muffins with streusel topping.",
            yield_servings="12 muffins",
            prep_time="15 mins",
            cook_time="20 mins",
            total_time="35 mins",
            ingredients=["2 cups flour", "1 cup blueberries", "1/2 cup sugar"],
            directions=["1. Mix dry ingredients.", "2. Fold in blueberries.", "3. Bake at 375°F."],
            tags=["Breakfast", "Muffins"],
        )
        test_session_store.save_recipe(sid, recipe)

        # 1. Plan To Eat CSV
        res_csv = await client.get(f"/api/export/csv?session_id={sid}")
        assert res_csv.status_code == 200
        assert "text/csv" in res_csv.headers["content-type"]
        assert "Blueberry Muffins" in res_csv.text

        # 2. Schema.org JSON-LD
        res_json_ld = await client.get(f"/api/export/json-ld?session_id={sid}")
        assert res_json_ld.status_code == 200
        assert "application/ld+json" in res_json_ld.headers["content-type"]
        assert "https://schema.org" in res_json_ld.text
        assert "Blueberry Muffins" in res_json_ld.text

        # 3. Raw JSON
        res_json = await client.get(f"/api/export/json?session_id={sid}")
        assert res_json.status_code == 200
        assert "application/json" in res_json.headers["content-type"]
        assert "Blueberry Muffins" in res_json.text

    # ------------------------------------------------------------------------
    # 6. Ingestion Batch Default Tags & SSE Stream Stability Tests
    # ------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_pipeline_start_with_batch_default_tags(
        self, client: httpx.AsyncClient, tmp_path: Path, test_session_manager: SessionManager
    ):
        """Test POST /api/pipeline/start stores default_tags in session configuration."""
        scans_dir = tmp_path / "batch_tags_scans"
        scans_dir.mkdir()
        img_file = scans_dir / "card1.jpg"
        img_file.write_bytes(_create_dummy_image_bytes("JPEG"))

        payload = {
            "scans_dir": str(scans_dir),
            "mode": "parallel",
            "default_tags": ["Grandma's Box", "Baking", "1950s"],
        }

        res = await client.post("/api/pipeline/start", json=payload)
        assert res.status_code == 200
        data = res.json()
        sid = data["session_id"]

        session = test_session_manager.get_session(sid)
        assert session is not None
        assert getattr(session.config, "default_tags", None) == ["Grandma's Box", "Baking", "1950s"]

    @pytest.mark.asyncio
    async def test_upload_cards_with_batch_default_tags(
        self, client: httpx.AsyncClient, test_session_manager: SessionManager
    ):
        """Test POST /api/cards/upload accepts default_tags form parameter."""
        img1 = _create_dummy_image_bytes("JPEG")
        files = [("files", ("card_test.jpg", img1, "image/jpeg"))]
        data = {
            "mode": "parallel",
            "default_tags": '["Heirloom", "Casseroles"]',
        }

        res = await client.post("/api/cards/upload", files=files, data=data)
        assert res.status_code == 200
        sid = res.json()["session_id"]

        session = test_session_manager.get_session(sid)
        assert session is not None
        assert getattr(session.config, "default_tags", None) == ["Heirloom", "Casseroles"]

    @pytest.mark.asyncio
    async def test_sse_stream_initial_handshake_and_keepalive(
        self, client: httpx.AsyncClient, test_session_manager: SessionManager
    ):
        """Test GET /api/events sends initial handshake and keeps alive without abrupt closure."""
        sid = "sess_sse_test"
        session = test_session_manager.create_session(session_id=sid)

        async def complete_session():
            await asyncio.sleep(0.05)
            session.state = PipelineState.COMPLETED

        asyncio.create_task(complete_session())

        async with client.stream("GET", f"/api/events?session_id={sid}") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
            chunks = []
            async for chunk in response.aiter_text():
                chunks.append(chunk)
                if "completed" in chunk:
                    break
            joined = "".join(chunks)
            assert ": connected" in joined
            assert "event: state_change" in joined or "event: completed" in joined

    @pytest.mark.asyncio
    async def test_sse_stream_sqlite_fallback_for_restarted_session(
        self, client: httpx.AsyncClient, test_session_store: SessionStore
    ):
        """Test GET /api/events gracefully streams persisted terminal status from SQLite without 404."""
        sid = "sess_restarted_sqlite"
        test_session_store.save_session(
            session_id=sid,
            state=PipelineState.COMPLETED,
            mode="parallel",
            recipe_count=1,
        )

        async with client.stream("GET", f"/api/events?session_id={sid}") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
            chunks = []
            async for chunk in response.aiter_text():
                chunks.append(chunk)
            joined = "".join(chunks)
            assert ": connected" in joined
            assert "event: state_change" in joined
            assert "event: completed" in joined

    @pytest.mark.asyncio
    async def test_sse_stream_nonexistent_session_returns_404(
        self, client: httpx.AsyncClient
    ):
        """Test GET /api/events with nonexistent session in both memory and SQLite returns 404."""
        resp = await client.get("/api/events?session_id=nonexistent_missing_session")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_sources_providers_unconfigured_cloud_fast_probe(
        self, client: httpx.AsyncClient
    ):
        """Test GET /api/sources/providers returns immediately with connected=False for unconfigured S3/GCS."""
        resp = await client.get("/api/sources/providers")
        assert resp.status_code == 200
        providers = {p["provider_id"]: p for p in resp.json()}
        assert "s3" in providers
        assert "gcs" in providers
        assert providers["s3"]["connected"] is False
        assert providers["gcs"]["connected"] is False

    @pytest.mark.asyncio
    async def test_sse_stream_empty_or_none_sentinel_session_id(
        self, client: httpx.AsyncClient
    ):
        """Test GET /api/events with empty, whitespace, 'none', 'null', 'undefined' (case-insensitive) returns IDLE cleanly without 404."""
        for param in ["", "none", "None", "NONE", "null", "NULL", "undefined", "Undefined", "   "]:
            async with client.stream("GET", f"/api/events?session_id={param}") as response:
                assert response.status_code == 200
                chunks = []
                async for chunk in response.aiter_text():
                    chunks.append(chunk)
                joined = "".join(chunks)
                assert ": connected" in joined
                assert '"status": "IDLE"' in joined

    @pytest.mark.asyncio
    async def test_sse_stream_active_session_when_omitted(
        self, client: httpx.AsyncClient, test_session_manager: SessionManager
    ):
        """Test GET /api/events with no session_id streams active session when present in manager."""
        session = test_session_manager.create_session()
        session.state = PipelineState.COMPLETED
        session.progress_metrics.percent_complete = 100.0

        async with client.stream("GET", "/api/events") as response:
            assert response.status_code == 200
            chunks = []
            async for chunk in response.aiter_text():
                chunks.append(chunk)
            joined = "".join(chunks)
            assert ": connected" in joined
            assert "event: state_change" in joined or "event: completed" in joined
            assert session.session_id in joined

    @pytest.mark.asyncio
    async def test_sse_stream_sqlite_fallback_slow_batch_and_lowercase_state(
        self, client: httpx.AsyncClient, test_session_store: SessionStore
    ):
        """Test GET /api/events and /api/pipeline/status correctly normalize slow-batch / slow_batch modes and lowercase states."""
        sid = "sess_slow_batch_sqlite"
        test_session_store.save_session(
            session_id=sid,
            state="completed",
            mode="slow-batch",
            recipe_count=2,
        )

        async with client.stream("GET", f"/api/events?session_id={sid}") as response:
            assert response.status_code == 200
            chunks = []
            async for chunk in response.aiter_text():
                chunks.append(chunk)
            joined = "".join(chunks)
            assert ": connected" in joined
            assert '"mode":"slow_batch"' in joined or '"mode": "slow_batch"' in joined
            assert '"status":"COMPLETED"' in joined or '"status": "COMPLETED"' in joined
            assert "event: completed" in joined

        resp = await client.get(f"/api/pipeline/status?session_id={sid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "slow_batch"
        assert data["status"] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_pipeline_status_unknown_session_returns_none_session_id(
        self, client: httpx.AsyncClient
    ):
        """Test GET /api/pipeline/status and /api/status for unknown session returns session_id='none' and status=IDLE."""
        resp = await client.get("/api/pipeline/status?session_id=totally_unknown_session_id")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "none"
        assert data["status"] == "IDLE"

        resp_alias = await client.get("/api/status?session_id=totally_unknown_session_id")
        assert resp_alias.status_code == 200
        data_alias = resp_alias.json()
        assert data_alias["session_id"] == "none"
        assert data_alias["status"] == "IDLE"


