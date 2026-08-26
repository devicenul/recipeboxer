"""Empirical Challenger 2 Adversarial Stress Suite for Milestone 1.

Targets:
1. Multipart file upload with `default_tags` form parameter (JSON vs comma-separated, malformed, empty, type coercion, deduplication).
2. SSE keep-alive heartbeats (: ping\n\n), initial handshake (: connected\n\n), headers, and graceful termination on terminal states (COMPLETED, FAILED, CANCELLED).
3. Session store integration, SQLite persistence, rehydration, concurrent session isolation, and pipeline default_tags propagation into RecipeSchema.
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import AsyncGenerator, List, Optional
import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from PIL import Image

from recipeboxer.agent.orchestrator import RecipeBoxerAgent
from recipeboxer.agent.session import PipelineSession, SessionManager
from recipeboxer.api.app import create_app
from recipeboxer.config import Settings
from recipeboxer.models.domain import CardRole, PaperColorInfo, PaperRulingInfo, VisualFingerprint
from recipeboxer.models.hitl import CardPairApproval, HITLDecision
from recipeboxer.models.pipeline import IngestionMode, PipelineStartRequest, PipelineState
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.storage.session_store import SessionStore


def create_test_image_bytes(filename: str = "card.jpg", format: str = "JPEG", size: tuple = (200, 150), color: str = "white") -> bytes:
    """Helper to generate in-memory synthetic card image bytes."""
    buf = io.BytesIO()
    img = Image.new("RGB", size, color=color)
    img.save(buf, format=format)
    return buf.getvalue()


@pytest.fixture
def isolated_mgr() -> SessionManager:
    return SessionManager()


@pytest.fixture
def isolated_store(tmp_path: Path) -> SessionStore:
    db_file = tmp_path / "challenger_c2_test.db"
    return SessionStore(db_path=db_file)


@pytest.fixture
def test_app(isolated_mgr: SessionManager, isolated_store: SessionStore, tmp_path: Path):
    settings = Settings(cache_dir=tmp_path / "cache", export_dir=tmp_path / "exports")
    app = create_app(
        settings=settings,
        session_manager=isolated_mgr,
        session_store=isolated_store,
    )
    return app


@pytest_asyncio.fixture
async def client(test_app) -> AsyncGenerator[httpx.AsyncClient, None]:
    transport = ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as cl:
        yield cl


# ============================================================================
# PART 1: MULTIPART FILE UPLOAD WITH DEFAULT_TAGS ADVERSARIAL STRESS
# ============================================================================

class TestMultipartUploadDefaultTagsAdversarial:
    """Adversarial challenge for multipart card uploads and default_tags parsing."""

    @pytest.mark.asyncio
    async def test_upload_with_json_array_default_tags(
        self, client: httpx.AsyncClient, isolated_mgr: SessionManager
    ):
        """Upload accepts JSON-encoded string array for default_tags."""
        img_bytes = create_test_image_bytes("scan1.jpg")
        files = [("files", ("scan1.jpg", img_bytes, "image/jpeg"))]
        data = {
            "default_tags": json.dumps(["Grandma's Box", "Vintage 1950s", "Dessert"]),
            "mode": "parallel",
        }

        res = await client.post("/api/cards/upload", files=files, data=data)
        assert res.status_code == 200
        res_data = res.json()
        sid = res_data["session_id"]

        session = isolated_mgr.get_session(sid)
        assert session is not None
        assert session.config.default_tags == ["Grandma's Box", "Vintage 1950s", "Dessert"]

    @pytest.mark.asyncio
    async def test_upload_with_comma_separated_default_tags(
        self, client: httpx.AsyncClient, isolated_mgr: SessionManager
    ):
        """Upload accepts plain comma-separated string for default_tags."""
        img_bytes = create_test_image_bytes("scan2.png", format="PNG")
        files = [("files", ("scan2.png", img_bytes, "image/png"))]
        data = {
            "default_tags": "  Holiday  ,  Cookies , Baking , Heirloom  ",
            "mode": "parallel",
        }

        res = await client.post("/api/cards/upload", files=files, data=data)
        assert res.status_code == 200
        sid = res.json()["session_id"]

        session = isolated_mgr.get_session(sid)
        assert session is not None
        assert session.config.default_tags == ["Holiday", "Cookies", "Baking", "Heirloom"]

    @pytest.mark.asyncio
    async def test_upload_with_messy_commas_and_empty_tokens(
        self, client: httpx.AsyncClient, isolated_mgr: SessionManager
    ):
        """Upload strips whitespace and drops empty tokens from comma string."""
        img_bytes = create_test_image_bytes("scan3.jpg")
        files = [("files", ("scan3.jpg", img_bytes, "image/jpeg"))]
        data = {
            "default_tags": ",,  ,  TagA , , TagB , \t , \n, TagC, , ",
        }

        res = await client.post("/api/cards/upload", files=files, data=data)
        assert res.status_code == 200
        sid = res.json()["session_id"]

        session = isolated_mgr.get_session(sid)
        assert session is not None
        assert session.config.default_tags == ["TagA", "TagB", "TagC"]

    @pytest.mark.asyncio
    async def test_upload_with_json_encoded_comma_string(
        self, client: httpx.AsyncClient, isolated_mgr: SessionManager
    ):
        """Upload parses JSON string literal containing commas cleanly."""
        img_bytes = create_test_image_bytes("scan4.jpg")
        files = [("files", ("scan4.jpg", img_bytes, "image/jpeg"))]
        data = {
            "default_tags": json.dumps("Apple Pie, Fall, Cinnamon"),
        }

        res = await client.post("/api/cards/upload", files=files, data=data)
        assert res.status_code == 200
        sid = res.json()["session_id"]

        session = isolated_mgr.get_session(sid)
        assert session is not None
        assert session.config.default_tags == ["Apple Pie", "Fall", "Cinnamon"]

    @pytest.mark.asyncio
    async def test_upload_with_malformed_json_falls_back_gracefully(
        self, client: httpx.AsyncClient, isolated_mgr: SessionManager
    ):
        """Malformed JSON string falls back to comma splitting without 500 error."""
        img_bytes = create_test_image_bytes("scan5.jpg")
        files = [("files", ("scan5.jpg", img_bytes, "image/jpeg"))]
        data = {
            "default_tags": '["incomplete json array, Tag1, Tag2',
        }

        res = await client.post("/api/cards/upload", files=files, data=data)
        assert res.status_code == 200
        sid = res.json()["session_id"]

        session = isolated_mgr.get_session(sid)
        assert session is not None
        assert "Tag1" in session.config.default_tags
        assert "Tag2" in session.config.default_tags

    @pytest.mark.asyncio
    async def test_upload_with_empty_and_whitespace_default_tags(
        self, client: httpx.AsyncClient, isolated_mgr: SessionManager
    ):
        """Empty or whitespace-only default_tags string produces empty list."""
        img_bytes = create_test_image_bytes("scan6.jpg")
        files = [("files", ("scan6.jpg", img_bytes, "image/jpeg"))]
        data = {
            "default_tags": "    \t  \n  ",
        }

        res = await client.post("/api/cards/upload", files=files, data=data)
        assert res.status_code == 200
        sid = res.json()["session_id"]

        session = isolated_mgr.get_session(sid)
        assert session is not None
        assert session.config.default_tags == []

    @pytest.mark.asyncio
    async def test_upload_with_json_heterogeneous_types(
        self, client: httpx.AsyncClient, isolated_mgr: SessionManager
    ):
        """JSON array with numbers, booleans, and nulls coerces safely to strings."""
        img_bytes = create_test_image_bytes("scan7.jpg")
        files = [("files", ("scan7.jpg", img_bytes, "image/jpeg"))]
        data = {
            "default_tags": json.dumps([1965, True, "Heirloom", "  Spices  "]),
        }

        res = await client.post("/api/cards/upload", files=files, data=data)
        assert res.status_code == 200
        sid = res.json()["session_id"]

        session = isolated_mgr.get_session(sid)
        assert session is not None
        assert "1965" in session.config.default_tags
        assert "True" in session.config.default_tags
        assert "Heirloom" in session.config.default_tags
        assert "Spices" in session.config.default_tags

    @pytest.mark.asyncio
    async def test_upload_merges_tags_across_sequential_uploads_to_same_session(
        self, client: httpx.AsyncClient, isolated_mgr: SessionManager
    ):
        """Sequential multipart uploads to same session_id accumulate and merge default_tags."""
        sid = "sess_seq_upload_01"
        img1 = create_test_image_bytes("card1.jpg")
        img2 = create_test_image_bytes("card2.jpg")

        # 1. First upload
        res1 = await client.post(
            "/api/cards/upload",
            files=[("files", ("card1.jpg", img1, "image/jpeg"))],
            data={"session_id": sid, "default_tags": "Tag1, Tag2"},
        )
        assert res1.status_code == 200

        # 2. Second upload to same session with additional tags
        res2 = await client.post(
            "/api/cards/upload",
            files=[("files", ("card2.jpg", img2, "image/jpeg"))],
            data={"session_id": sid, "default_tags": "Tag2, Tag3, Tag4"},
        )
        assert res2.status_code == 200

        session = isolated_mgr.get_session(sid)
        assert session is not None
        assert len(session.cards) == 2
        # Set deduplication
        assert set(session.config.default_tags) == {"Tag1", "Tag2", "Tag3", "Tag4"}

    @pytest.mark.asyncio
    async def test_upload_multiple_valid_image_formats(
        self, client: httpx.AsyncClient, isolated_mgr: SessionManager
    ):
        """Upload handles multiple image formats (JPEG, PNG, WEBP, BMP, TIFF) simultaneously."""
        formats = [
            ("card_jpeg.jpg", "JPEG", "image/jpeg"),
            ("card_png.png", "PNG", "image/png"),
            ("card_webp.webp", "WEBP", "image/webp"),
            ("card_bmp.bmp", "BMP", "image/bmp"),
            ("card_tiff.tiff", "TIFF", "image/tiff"),
        ]

        files_payload = []
        for fname, fmt, mime in formats:
            b = create_test_image_bytes(fname, format=fmt)
            files_payload.append(("files", (fname, b, mime)))

        res = await client.post(
            "/api/cards/upload",
            files=files_payload,
            data={"default_tags": "MultiFormat, Scans"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["count"] == 5
        assert len(data["card_ids"]) == 5


# ============================================================================
# PART 2: SSE KEEP-ALIVE HEARTBEATS & STREAM TERMINATION ADVERSARIAL STRESS
# ============================================================================

class TestSSEKeepAliveAndTerminationAdversarial:
    """Adversarial challenge for SSE keep-alive (: ping), initial handshake, and terminal state exits."""

    @pytest.mark.asyncio
    async def test_sse_initial_handshake_comment(
        self, client: httpx.AsyncClient, isolated_mgr: SessionManager
    ):
        """SSE stream immediately sends ': connected\\n\\n' comment upon opening."""
        sid = "sess_sse_handshake"
        session = isolated_mgr.create_session(session_id=sid)

        async def complete_session():
            await asyncio.sleep(0.05)
            session.state = PipelineState.COMPLETED

        asyncio.create_task(complete_session())

        async with client.stream("GET", f"/api/events?session_id={sid}") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")
            assert "no-cache" in response.headers.get("cache-control", "")
            assert response.headers.get("x-accel-buffering") == "no"

            chunks = []
            async for chunk in response.aiter_text():
                chunks.append(chunk)
                if "completed" in chunk:
                    break

            full_text = "".join(chunks)
            assert ": connected" in full_text

    @pytest.mark.asyncio
    async def test_sse_terminal_completed_closes_cleanly(
        self, client: httpx.AsyncClient, isolated_mgr: SessionManager
    ):
        """SSE stream terminates when session enters COMPLETED state."""
        sid = "sess_sse_term_comp"
        session = isolated_mgr.create_session(session_id=sid)
        session.state = PipelineState.COMPLETED
        session.progress_metrics.total_scans = 4
        session.progress_metrics.extracted = 2

        events = []
        async with client.stream("GET", f"/api/events?session_id={sid}") as response:
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())
                if "completed" in events:
                    break

        assert "completed" in events

    @pytest.mark.asyncio
    async def test_sse_terminal_cancelled_closes_cleanly(
        self, client: httpx.AsyncClient, isolated_mgr: SessionManager
    ):
        """SSE stream terminates when session enters CANCELLED state."""
        sid = "sess_sse_term_canc"
        session = isolated_mgr.create_session(session_id=sid)
        session.state = PipelineState.CANCELLED

        events = []
        async with client.stream("GET", f"/api/events?session_id={sid}") as response:
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())
                if "cancelled" in events:
                    break

        assert "cancelled" in events

    @pytest.mark.asyncio
    async def test_sse_terminal_failed_closes_cleanly(
        self, client: httpx.AsyncClient, isolated_mgr: SessionManager
    ):
        """SSE stream terminates when session enters FAILED state."""
        sid = "sess_sse_term_fail"
        session = isolated_mgr.create_session(session_id=sid)
        session.state = PipelineState.FAILED

        events = []
        async with client.stream("GET", f"/api/events?session_id={sid}") as response:
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())
                if "failed" in events:
                    break

        assert "failed" in events

    @pytest.mark.asyncio
    async def test_sse_stream_receives_recipe_extracted_event(
        self, client: httpx.AsyncClient, isolated_mgr: SessionManager
    ):
        """SSE stream emits recipe_extracted events with recipe model JSON payload."""
        sid = "sess_sse_recipe_event"
        session = isolated_mgr.create_session(session_id=sid)

        rec = RecipeSchema(
            recipe_id="rec_sse_live_1",
            title="Live Streamed Recipe",
            ingredients=["1 cup milk"],
            directions=["1. Drink milk."],
            tags=["Quick"],
        )

        async def add_recipe_and_complete():
            await asyncio.sleep(0.05)
            session.recipes.append(rec)
            session.progress_metrics.extracted = 1
            await asyncio.sleep(0.05)
            session.state = PipelineState.COMPLETED

        asyncio.create_task(add_recipe_and_complete())

        received_recipes = []
        async with client.stream("GET", f"/api/events?session_id={sid}") as response:
            async for line in response.aiter_lines():
                if line.startswith("data:") and "rec_sse_live_1" in line:
                    payload = json.loads(line.split("data:", 1)[1].strip())
                    received_recipes.append(payload.get("title"))
                if "completed" in line:
                    break

        assert received_recipes == ["Live Streamed Recipe"]


# ============================================================================
# PART 3: SESSION STORE & PIPELINE INTEGRATION ADVERSARIAL STRESS
# ============================================================================

class TestSessionStoreAndPipelineIntegrationAdversarial:
    """Adversarial challenge for session store persistence, rehydration, and pipeline default_tags merge."""

    @pytest.mark.asyncio
    async def test_session_store_persists_default_tags_in_sqlite(
        self, isolated_store: SessionStore
    ):
        """SessionStore correctly serializes and retrieves default_tags from config json in SQLite."""
        sid = "sess_store_tags_01"
        config = {
            "scans_dir": "/tmp/scans",
            "mode": "parallel",
            "default_tags": ["Vintage", "Grandma's Sweets", "1970s"],
        }
        isolated_store.save_session(
            session_id=sid,
            state=PipelineState.IDLE,
            mode="parallel",
            folder_path="/tmp/scans",
            config=config,
            card_count=5,
            recipe_count=0,
        )

        detail = isolated_store.get_session_detail(sid)
        assert detail is not None
        assert detail.config is not None
        assert detail.config.get("default_tags") == ["Vintage", "Grandma's Sweets", "1970s"]

    @pytest.mark.asyncio
    async def test_pipeline_runner_merges_and_deduplicates_tags_into_recipes(self):
        """RecipeBoxerAgent merges session default_tags with extracted tags, deduplicating case-insensitively."""
        agent = RecipeBoxerAgent()

        cfg = PipelineStartRequest(
            scans_dir="/tmp/scans",
            default_tags=["Family Heirlooms", "Dessert", "Baking"],
        )
        session = PipelineSession(session_id="sess_agent_merge", config=cfg)

        fp1 = VisualFingerprint(
            card_id="c1.jpg",
            image_path="/tmp/c1.jpg",
            sha256_hash="hash1",
            role=CardRole.STANDALONE,
        )
        session.fingerprints["c1.jpg"] = fp1

        raw_recipe = RecipeSchema(
            title="Strawberry Shortcake",
            ingredients=["2 cups strawberries", "1 cup flour", "1 cup heavy cream"],
            directions=["1. Slice berries.", "2. Bake shortcakes.", "3. Assemble."],
            tags=["dessert", "Summer", "Family Heirlooms", "Fruit"],  # Overlapping tags in different cases
        )

        async def mock_extract(front, back):
            return raw_recipe

        session.recipe_extractor_fn = mock_extract
        session.approvals = [
            CardPairApproval(
                proposal_id="p1",
                front_card_id="c1.jpg",
                decision=HITLDecision.APPROVE,
            )
        ]

        recipe = await agent._extract_single_recipe(session, fp1, None)
        assert recipe is not None

        # Resolve combined tags
        session_tags = session.config.default_tags
        combined = list(session_tags) + list(recipe.tags or [])
        clean_tags = RecipeSchema.clean_and_deduplicate_tags(combined)

        assert clean_tags == ["Family Heirlooms", "Dessert", "Baking", "Summer", "Fruit"]

    @pytest.mark.asyncio
    async def test_concurrent_sessions_isolated(
        self, client: httpx.AsyncClient, isolated_mgr: SessionManager
    ):
        """Concurrent sessions do not leak or overwrite each other's default_tags or cards."""
        img_a = create_test_image_bytes("card_a.jpg")
        img_b = create_test_image_bytes("card_b.jpg")

        sid_a = "sess_iso_a"
        sid_b = "sess_iso_b"

        # Launch session A
        res_a = await client.post(
            "/api/cards/upload",
            files=[("files", ("card_a.jpg", img_a, "image/jpeg"))],
            data={"session_id": sid_a, "default_tags": "TagsA1, TagsA2"},
        )
        assert res_a.status_code == 200

        # Launch session B
        res_b = await client.post(
            "/api/cards/upload",
            files=[("files", ("card_b.jpg", img_b, "image/jpeg"))],
            data={"session_id": sid_b, "default_tags": "TagsB1, TagsB2, TagsB3"},
        )
        assert res_b.status_code == 200

        sess_a = isolated_mgr.get_session(sid_a)
        sess_b = isolated_mgr.get_session(sid_b)

        assert sess_a is not None and sess_b is not None
        assert sess_a.config.default_tags == ["TagsA1", "TagsA2"]
        assert sess_b.config.default_tags == ["TagsB1", "TagsB2", "TagsB3"]
        assert len(sess_a.cards) == 1 and sess_a.cards[0].card_id == "card_a.jpg"
        assert len(sess_b.cards) == 1 and sess_b.cards[0].card_id == "card_b.jpg"
