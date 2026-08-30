"""Integration tests for RecipeBoxer FastAPI REST API endpoints."""

import asyncio
from pathlib import Path
from typing import Any, AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from recipeboxer.agent.session import SessionManager
from recipeboxer.api.app import create_app
from recipeboxer.config import Settings
from recipeboxer.models.pipeline import PipelineState
from tests.conftest import (
    MockGeminiClient,
    SQLiteFingerprintCache,
    SyntheticDeckInfo,
    create_synthetic_card_image,
)


@pytest.fixture
def test_session_manager(test_cache: SQLiteFingerprintCache, mock_gemini_client: MockGeminiClient) -> SessionManager:
    """Provides an isolated SessionManager for API tests with mock components."""
    manager = SessionManager()
    return manager


@pytest.fixture
def app(test_session_manager: SessionManager) -> Any:
    """Creates a FastAPI app instance with injected test session manager."""
    return create_app(session_manager=test_session_manager)


@pytest_asyncio.fixture
async def client(app) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Provides httpx.AsyncClient with ASGITransport."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestFastAPIRoutes:
    """Test suite covering all FastAPI REST endpoints and error paths."""

    # ------------------------------------------------------------------------
    # 1. POST /api/pipeline/start
    # ------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_start_pipeline_missing_dir_400(self, client: httpx.AsyncClient):
        """Test starting pipeline with non-existent directory returns 400 Bad Request."""
        res = await client.post("/api/pipeline/start", json={"scans_dir": "/nonexistent/invalid/dir"})
        assert res.status_code == 400
        assert "does not exist" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_start_pipeline_empty_dir_400(self, client: httpx.AsyncClient, tmp_path: Path):
        """Test starting pipeline with empty directory returns 400 Bad Request."""
        empty_dir = tmp_path / "empty_dir"
        empty_dir.mkdir()
        res = await client.post("/api/pipeline/start", json={"scans_dir": str(empty_dir)})
        assert res.status_code == 400
        assert "No valid card scans" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_start_pipeline_directory_path_alias(
        self, client: httpx.AsyncClient, synthetic_card_deck: SyntheticDeckInfo
    ):
        """Test starting pipeline using directory_path alias parameter."""
        res = await client.post("/api/pipeline/start", json={
            "directory_path": str(synthetic_card_deck.directory),
            "mode": "parallel",
            "max_concurrency": 2,
        })
        assert res.status_code == 200
        data = res.json()
        assert "session_id" in data
        assert data["total_scans_found"] > 0
        assert data["mode"] == "parallel"

    # ------------------------------------------------------------------------
    # 2. GET /api/status
    # ------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_status_endpoint_idle_when_no_sessions(self, client: httpx.AsyncClient):
        """Test GET /api/status returns IDLE state when no active session exists."""
        res = await client.get("/api/status")
        assert res.status_code == 200
        assert res.json()["status"] == "IDLE"

    @pytest.mark.asyncio
    async def test_status_endpoint_with_explicit_session_id(
        self, client: httpx.AsyncClient, synthetic_card_deck: SyntheticDeckInfo
    ):
        """Test GET /api/status returns telemetry for specific session_id."""
        start_res = await client.post("/api/pipeline/start", json={
            "scans_dir": str(synthetic_card_deck.directory),
        })
        sid = start_res.json()["session_id"]

        status_res = await client.get(f"/api/status?session_id={sid}")
        assert status_res.status_code == 200
        status_data = status_res.json()
        assert status_data["session_id"] == sid
        assert "progress" in status_data
        assert "token_usage" in status_data
        assert "elapsed_seconds" in status_data

    # ------------------------------------------------------------------------
    # 3. GET /api/hitl/pending
    # ------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_hitl_pending_unknown_session_404(self, client: httpx.AsyncClient):
        """Test GET /api/hitl/pending for non-existent session returns 404 Not Found."""
        res = await client.get("/api/hitl/pending?session_id=sess_nonexistent")
        assert res.status_code == 404
        assert "Session not found" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_hitl_pending_not_pending_state_400(
        self, client: httpx.AsyncClient, test_session_manager: SessionManager
    ):
        """Test GET /api/hitl/pending returns 400 if session is still IDLE."""
        session = test_session_manager.create_session()
        res = await client.get(f"/api/hitl/pending?session_id={session.session_id}")
        assert res.status_code == 400
        assert "not 'HITL_PENDING'" in res.json()["detail"]

    # ------------------------------------------------------------------------
    # 4. POST /api/hitl/approve
    # ------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_hitl_approve_unknown_session_404(self, client: httpx.AsyncClient):
        """Test POST /api/hitl/approve with unknown session returns 404 Not Found."""
        res = await client.post("/api/hitl/approve", json={
            "session_id": "sess_unknown",
            "approvals": [],
        })
        assert res.status_code == 404
        assert "Session not found" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_hitl_approve_invalid_state_400(
        self, client: httpx.AsyncClient, test_session_manager: SessionManager
    ):
        """Test POST /api/hitl/approve when not in HITL_PENDING returns 400 Bad Request."""
        session = test_session_manager.create_session()
        res = await client.post("/api/hitl/approve", json={
            "session_id": session.session_id,
            "approvals": [],
        })
        assert res.status_code == 400
        assert "Cannot approve session in state" in res.json()["detail"]

    # ------------------------------------------------------------------------
    # 5. GET /api/export/csv
    # ------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_export_csv_unknown_session_404(self, client: httpx.AsyncClient):
        """Test GET /api/export/csv for unknown session returns 404 Not Found."""
        res = await client.get("/api/export/csv?session_id=sess_unknown")
        assert res.status_code == 404
        assert "Session not found" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_export_csv_before_completion_400(
        self, client: httpx.AsyncClient, test_session_manager: SessionManager
    ):
        """Test GET /api/export/csv before COMPLETED state returns 400 Bad Request."""
        session = test_session_manager.create_session()
        res = await client.get(f"/api/export/csv?session_id={session.session_id}")
        assert res.status_code == 400
        assert "CSV export not ready" in res.json()["detail"]

    # ------------------------------------------------------------------------
    # 6. End-to-End API Pipeline Lifecycle
    # ------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_full_pipeline_api_lifecycle(
        self, client: httpx.AsyncClient, synthetic_card_deck: SyntheticDeckInfo
    ):
        """Test complete 5-endpoint API workflow: start -> status -> pending -> approve -> completed -> export."""
        # 1. Start Pipeline
        res_start = await client.post("/api/pipeline/start", json={
            "scans_dir": str(synthetic_card_deck.directory),
            "mode": "parallel",
            "max_concurrency": 4,
        })
        assert res_start.status_code == 200
        start_data = res_start.json()
        sid = start_data["session_id"]
        assert sid is not None

        # 2. Check Status while running
        res_status = await client.get(f"/api/status?session_id={sid}")
        assert res_status.status_code == 200

        # 3. Poll /api/hitl/pending until HITL_PENDING
        pending_data = None
        for _ in range(100):
            res_hitl = await client.get(f"/api/hitl/pending?session_id={sid}")
            if res_hitl.status_code == 200:
                pending_data = res_hitl.json()
                break
            await asyncio.sleep(0.02)

        assert pending_data is not None
        assert pending_data["status"] == "HITL_PENDING"
        assert pending_data["total_proposals"] > 0

        # 4. Approve Proposals via POST /api/hitl/approve
        proposals = pending_data["proposed_pairs"]
        approvals = [
            {
                "proposal_id": p["proposal_id"],
                "ordered_card_ids": p.get("ordered_card_ids", [p.get("front_card_id")]),
                "decision": "APPROVE",
            }
            for p in proposals
        ]
        res_approve = await client.post("/api/hitl/approve", json={
            "session_id": sid,
            "approvals": approvals,
            "rejected_card_ids": [],
            "continue_pipeline": True,
        })
        assert res_approve.status_code == 200
        assert res_approve.json()["approved_count"] == len(approvals)

        # 5. Wait for COMPLETED status
        for _ in range(100):
            s_res = await client.get(f"/api/status?session_id={sid}")
            if s_res.json()["status"] == "COMPLETED":
                break
            await asyncio.sleep(0.02)

        final_status = await client.get(f"/api/status?session_id={sid}")
        assert final_status.json()["status"] == "COMPLETED"

        # 6. Download CSV via GET /api/export/csv
        res_export = await client.get(f"/api/export/csv?session_id={sid}")
        assert res_export.status_code == 200
        assert "text/csv" in res_export.headers["content-type"]
        assert 'filename="recipeboxer_export.csv"' in res_export.headers["content-disposition"]
        assert "Title,Description,Ingredients,Directions" in res_export.text
        assert "Grandma's Cinnamon Rolls" in res_export.text
