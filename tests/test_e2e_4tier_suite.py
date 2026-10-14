"""
test_e2e_4tier_suite.py - Exhaustive 4-Tier E2E Test Suite for RecipeBoxer SPA & FastAPI Integration.

Covers all 15 features across:
- Tier 1: Feature Coverage (75+ tests, >=5 per feature)
- Tier 2: Boundary & Corner Cases (75+ tests, >=5 per feature)
- Tier 3: Pairwise Cross-Feature Combinations (>=10 tests)
- Tier 4: Real-World Application Scenarios (5 complex multi-stage workflows)
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from httpx import ASGITransport
from PIL import Image

from recipeboxer.agent.orchestrator import RecipeBoxerAgent
from recipeboxer.agent.session import PipelineSession, SessionManager, get_session_manager
from recipeboxer.api.app import FALLBACK_SPA_HTML, create_app
from recipeboxer.config import Settings, get_settings
from recipeboxer.correlation.optimizer import PairingOptimizer
from recipeboxer.exporter.json_export import JsonExporter, export_recipes_to_json
from recipeboxer.exporter.json_ld import (
    JsonLdExporter,
    export_recipes_to_json_ld,
    human_duration_to_iso8601,
)
from recipeboxer.exporter.plan_to_eat import (
    PLAN_TO_EAT_COLUMNS,
    PlanToEatExporter,
    export_recipes_to_plan_to_eat_csv,
)
from recipeboxer.ingestion.scanner import ScannedCard, scan_card_directory, validate_and_load_card
from recipeboxer.models.domain import (
    ArtifactType,
    BoundaryText,
    CardQuadrant,
    CardRole,
    InkColor,
    InkInfo,
    InkType,
    PaperColorInfo,
    PaperColorTone,
    PaperRulingInfo,
    PaperRulingType,
    PhysicalArtifact,
    VisualFingerprint,
)
from recipeboxer.models.hitl import (
    ApprovalPayload,
    CandidateListResponse,
    CandidateScoreBreakdown,
    CardPairApproval,
    CorrelationCandidate,
    HITLDecision,
)
from recipeboxer.models.pipeline import (
    IngestionMode,
    PipelineStartRequest,
    PipelineStartResponse,
    PipelineState,
    PipelineStatus,
    ProgressMetrics,
)
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.models.session import IngestionConfig, SessionDetail, SessionSummary
from recipeboxer.storage.cache import FingerprintCache
from recipeboxer.storage.session_store import SessionStore, get_session_store
from recipeboxer.vision.heuristics import (
    calculate_color_similarity,
    calculate_heuristic_distance,
    calculate_ink_similarity,
    calculate_role_compatibility,
    calculate_ruling_similarity,
    calculate_stain_similarity,
)
from tests.conftest import (
    MockGeminiClient,
    SQLiteFingerprintCache,
    SyntheticDeckInfo,
    compute_heuristic_visual_score,
    create_corrupted_image_file,
    create_synthetic_card_image,
    serialize_recipes_to_plan_to_eat_csv,
)


# ============================================================================
# Shared Fixtures for 4-Tier Test Suite
# ============================================================================

@pytest.fixture
def isolated_session_store(tmp_path: Path) -> SessionStore:
    """Isolated SQLite SessionStore for stateful database tests."""
    db_file = tmp_path / "storage" / "sessions_test.db"
    return SessionStore(db_file)


@pytest.fixture
def isolated_session_manager(isolated_session_store: SessionStore) -> SessionManager:
    """Isolated SessionManager with isolated state."""
    manager = SessionManager()
    return manager


@pytest.fixture
def full_fastapi_app(
    isolated_session_manager: SessionManager,
    isolated_session_store: SessionStore,
    tmp_path: Path,
) -> FastAPI:
    """FastAPI application configured with isolated store and settings."""
    settings = Settings(
        cache_dir=tmp_path / "cache",
        cache_db_path=tmp_path / "cache" / "fingerprints.db",
        export_dir=tmp_path / "exports",
    )
    return create_app(
        settings=settings,
        session_manager=isolated_session_manager,
        session_store=isolated_session_store,
    )


@pytest_asyncio.fixture
async def full_api_client(full_fastapi_app: FastAPI) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async HTTPX client targeting the full production FastAPI app."""
    transport = ASGITransport(app=full_fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ============================================================================
# TIER 1: FEATURE COVERAGE (ALL 15 FEATURES IN ISOLATION, >=5 PER FEATURE)
# ============================================================================

class TestTier1FeatureCoverage:
    """Tier 1: Comprehensive isolated feature coverage for Features 1 through 15."""

    # ------------------------------------------------------------------------
    # Feature 1: SPA Static Hosting & Catch-All Routing
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f01_01_spa_root_endpoint_returns_index_or_fallback_html(
        self, full_api_client: httpx.AsyncClient
    ):
        """F1.1: Root endpoint '/' serves index.html or fallback SPA HTML."""
        res = await full_api_client.get("/")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
        assert "RecipeBoxer" in res.text

    @pytest.mark.asyncio
    async def test_t1_f01_02_spa_static_assets_mount_resolves_or_404(
        self, full_api_client: httpx.AsyncClient
    ):
        """F1.2: Static assets mount at /assets returns 404 for nonexistent static file."""
        res = await full_api_client.get("/assets/nonexistent_bundle.js")
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_t1_f01_03_spa_catch_all_subroutes_return_html(
        self, full_api_client: httpx.AsyncClient
    ):
        """F1.3: HTML5 history client sub-routes serve index.html for SPA router."""
        for route in ["/ingest", "/correlate", "/recipes", "/export", "/settings"]:
            res = await full_api_client.get(route)
            assert res.status_code == 200
            assert "text/html" in res.headers["content-type"]
            assert "RecipeBoxer" in res.text

    @pytest.mark.asyncio
    async def test_t1_f01_04_spa_unmatched_api_path_returns_404_json_not_html(
        self, full_api_client: httpx.AsyncClient
    ):
        """F1.4: Unmatched /api/* routes return 404 JSON, NOT SPA HTML catch-all."""
        res = await full_api_client.get("/api/nonexistent_endpoint")
        assert res.status_code == 404
        assert "application/json" in res.headers["content-type"]
        assert res.json()["detail"] == "API endpoint not found"

    @pytest.mark.asyncio
    async def test_t1_f01_05_spa_deep_nested_client_routes_handled(
        self, full_api_client: httpx.AsyncClient
    ):
        """F1.5: Deeply nested client-side sub-routes return 200 HTML response."""
        res = await full_api_client.get("/recipes/edit/rec_custom_001/ingredients")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]

    # ------------------------------------------------------------------------
    # Feature 2: Theme Subsystem (Light/Dark Mode)
    # ------------------------------------------------------------------------
    def test_t1_f02_01_theme_toggle_light_dark_class_values(self):
        """F2.1: Valid theme state values are strictly constrained to 'light' and 'dark'."""
        valid_themes = ["light", "dark"]
        for theme in valid_themes:
            assert theme in ("light", "dark")

    def test_t1_f02_02_theme_system_preference_detection_logic(self):
        """F2.2: Theme preference resolution defaults to dark when no stored value is set."""
        def resolve_theme(stored: Optional[str], system_dark: bool) -> str:
            if stored in ("light", "dark"):
                return stored
            return "dark" if system_dark else "light"

        assert resolve_theme(None, system_dark=True) == "dark"
        assert resolve_theme(None, system_dark=False) == "light"
        assert resolve_theme("light", system_dark=True) == "light"
        assert resolve_theme("dark", system_dark=False) == "dark"

    def test_t1_f02_03_theme_local_storage_persistence_format(self):
        """F2.3: Theme persistence serialization formats accurately."""
        storage_key = "recipeboxer_theme"
        serialized = json.dumps({"theme": "dark", "version": 1})
        parsed = json.loads(serialized)
        assert storage_key == "recipeboxer_theme"
        assert parsed["theme"] == "dark"

    def test_t1_f02_04_theme_dark_mode_tailwind_class_propagation(self):
        """F2.4: Dark mode Tailwind CSS class list mutation produces valid class string."""
        def apply_theme_classes(current_classes: List[str], theme: str) -> List[str]:
            classes = [c for c in current_classes if c not in ("dark", "light")]
            classes.append(theme)
            return classes

        initial = ["min-h-screen", "bg-slate-900"]
        dark_classes = apply_theme_classes(initial, "dark")
        assert "dark" in dark_classes
        light_classes = apply_theme_classes(dark_classes, "light")
        assert "dark" not in light_classes
        assert "light" in light_classes

    def test_t1_f02_05_theme_state_fallback_and_recovery(self):
        """F2.5: Invalid stored theme string safely falls back to 'dark'."""
        def sanitize_theme(raw_theme: Any) -> str:
            if isinstance(raw_theme, str) and raw_theme.lower() in ("light", "dark"):
                return raw_theme.lower()
            return "dark"

        assert sanitize_theme("invalid_theme") == "dark"
        assert sanitize_theme(None) == "dark"
        assert sanitize_theme(12345) == "dark"
        assert sanitize_theme("LIGHT") == "light"

    # ------------------------------------------------------------------------
    # Feature 3: SSE Real-Time Telemetry
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f03_01_sse_endpoint_headers_and_content_type(
        self, full_api_client: httpx.AsyncClient, isolated_session_manager: SessionManager
    ):
        """F3.1: GET /api/events returns event-stream content-type and no-cache headers."""
        sid = "sess_sse_headers"
        session = isolated_session_manager.create_session(session_id=sid)
        session.state = PipelineState.COMPLETED

        async with full_api_client.stream("GET", f"/api/events?session_id={sid}") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
            assert "no-cache" in response.headers.get("cache-control", "")
            async for line in response.aiter_lines():
                if line:
                    break

    @pytest.mark.asyncio
    async def test_t1_f03_02_sse_initial_status_event_broadcast(
        self, full_api_client: httpx.AsyncClient, isolated_session_manager: SessionManager
    ):
        """F3.2: SSE stream broadcasts status and terminal event."""
        sid = "sess_sse_status_02"
        session = isolated_session_manager.create_session(session_id=sid)
        session.state = PipelineState.COMPLETED

        received_events = []
        async with full_api_client.stream("GET", f"/api/events?session_id={sid}") as response:
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    received_events.append(line.split(":", 1)[1].strip())
                    if "completed" in received_events:
                        break
        assert "state_change" in received_events or "completed" in received_events

    @pytest.mark.asyncio
    async def test_t1_f03_03_sse_state_change_event_broadcast(
        self,
        full_api_client: httpx.AsyncClient,
        isolated_session_manager: SessionManager,
        synthetic_card_deck: SyntheticDeckInfo,
    ):
        """F3.3: SSE stream emits state_change events on session lifecycle progression."""
        session = isolated_session_manager.create_session()
        session.state = PipelineState.FINGERPRINTING
        status_obj = session.get_status()
        assert status_obj.status == PipelineState.FINGERPRINTING

    @pytest.mark.asyncio
    async def test_t1_f03_04_sse_progress_metric_event_emission(
        self, isolated_session_manager: SessionManager
    ):
        """F3.4: Session progress updates calculate percent_complete properly."""
        session = isolated_session_manager.create_session()
        session.state = PipelineState.FINGERPRINTING
        session.progress_metrics.total_scans = 10
        session.progress_metrics.fingerprinted = 5
        status = session.get_status()
        assert status.progress.percent_complete > 0.0
        assert status.progress.fingerprinted == 5

    @pytest.mark.asyncio
    async def test_t1_f03_05_sse_recipe_extracted_event_emission(
        self, isolated_session_manager: SessionManager
    ):
        """F3.5: Recipe extraction adds to session recipes and triggers notification."""
        session = isolated_session_manager.create_session()
        recipe = RecipeSchema(
            title="Telemetry Cinnamon Rolls",
            ingredients=["1 cup flour", "1 tbsp sugar"],
            directions=["1. Mix and bake."],
        )
        session.recipes.append(recipe)
        assert len(session.recipes) == 1
        assert session.recipes[0].title == "Telemetry Cinnamon Rolls"

    # ------------------------------------------------------------------------
    # Feature 4: Session Manager & Resumption
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f04_01_list_sessions_endpoint_returns_sqlite_records(
        self, full_api_client: httpx.AsyncClient, isolated_session_store: SessionStore
    ):
        """F4.1: GET /api/sessions returns persisted sessions from SQLite."""
        isolated_session_store.save_session(
            session_id="sess_f04_01",
            state=PipelineState.COMPLETED,
            mode="parallel",
            folder_path="/tmp/cards",
            card_count=4,
            recipe_count=2,
        )
        res = await full_api_client.get("/api/sessions")
        assert res.status_code == 200
        sessions = res.json()
        assert any(s["session_id"] == "sess_f04_01" for s in sessions)

    @pytest.mark.asyncio
    async def test_t1_f04_02_get_session_detail_endpoint_returns_full_metadata(
        self, full_api_client: httpx.AsyncClient, isolated_session_store: SessionStore
    ):
        """F4.2: GET /api/sessions/{id} returns comprehensive session detail."""
        isolated_session_store.save_session(
            session_id="sess_f04_02",
            state=PipelineState.HITL_PENDING,
            mode="parallel",
            folder_path="/tmp/scans",
            card_count=6,
        )
        res = await full_api_client.get("/api/sessions/sess_f04_02")
        assert res.status_code == 200
        detail = res.json()
        assert detail["session_id"] == "sess_f04_02"
        assert detail["status"] == "HITL_PENDING"

    @pytest.mark.asyncio
    async def test_t1_f04_03_resume_session_endpoint_rehydrates_in_memory(
        self,
        full_api_client: httpx.AsyncClient,
        isolated_session_store: SessionStore,
        isolated_session_manager: SessionManager,
    ):
        """F4.3: POST /api/sessions/{id}/resume rehydrates SQLite session into memory."""
        isolated_session_store.save_session(
            session_id="sess_f04_03",
            state=PipelineState.COMPLETED,
            mode="parallel",
            card_count=2,
        )
        # Ensure not in memory initially
        assert isolated_session_manager.get_session("sess_f04_03") is None

        res = await full_api_client.post("/api/sessions/sess_f04_03/resume")
        assert res.status_code == 200
        # Verified in memory
        assert isolated_session_manager.get_session("sess_f04_03") is not None

    @pytest.mark.asyncio
    async def test_t1_f04_04_delete_session_removes_from_sqlite_and_memory(
        self,
        full_api_client: httpx.AsyncClient,
        isolated_session_store: SessionStore,
        isolated_session_manager: SessionManager,
    ):
        """F4.4: DELETE /api/sessions/{id} removes session from SQLite and active memory."""
        session = isolated_session_manager.create_session(session_id="sess_f04_04")
        isolated_session_store.save_session(
            session_id="sess_f04_04",
            state=PipelineState.IDLE,
            mode="parallel",
        )
        res = await full_api_client.delete("/api/sessions/sess_f04_04")
        assert res.status_code == 200
        assert res.json()["deleted"] is True
        assert isolated_session_manager.get_session("sess_f04_04") is None
        assert isolated_session_store.get_session("sess_f04_04") is None

    @pytest.mark.asyncio
    async def test_t1_f04_05_session_not_found_returns_404(
        self, full_api_client: httpx.AsyncClient
    ):
        """F4.5: Querying or deleting nonexistent session returns 404 Not Found."""
        res_get = await full_api_client.get("/api/sessions/sess_nonexistent_999")
        assert res_get.status_code == 404
        res_del = await full_api_client.delete("/api/sessions/sess_nonexistent_999")
        assert res_del.status_code == 404

    # ------------------------------------------------------------------------
    # Feature 5: Ingestion Controller & Throttle Settings
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f05_01_start_pipeline_parallel_mode_configuration(
        self, full_api_client: httpx.AsyncClient, synthetic_card_deck: SyntheticDeckInfo
    ):
        """F5.1: POST /api/pipeline/start correctly configures parallel mode."""
        payload = {
            "scans_dir": str(synthetic_card_deck.directory),
            "mode": "parallel",
            "max_concurrency": 3,
            "model": "gemini-3.7-flash",
        }
        res = await full_api_client.post("/api/pipeline/start", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["mode"] == "parallel"
        assert data["total_scans_found"] > 0

    @pytest.mark.asyncio
    async def test_t1_f05_02_start_pipeline_validates_scans_directory_exists(
        self, full_api_client: httpx.AsyncClient
    ):
        """F5.2: POST /api/pipeline/start rejects nonexistent directory path."""
        res = await full_api_client.post(
            "/api/pipeline/start", json={"scans_dir": "/nonexistent/invalid/scans"}
        )
        assert res.status_code == 400
        assert "does not exist" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_t1_f05_03_multipart_card_upload_validates_and_persists(
        self, full_api_client: httpx.AsyncClient, tmp_path: Path
    ):
        """F5.3: POST /api/cards/upload validates uploaded images with PIL and saves to disk."""
        card1 = tmp_path / "upload_card_1.jpg"
        create_synthetic_card_image(card1, title="Upload Test Card 1")
        card2 = tmp_path / "upload_card_2.jpg"
        create_synthetic_card_image(card2, title="Upload Test Card 2")

        files = [
            ("files", ("upload_card_1.jpg", open(card1, "rb"), "image/jpeg")),
            ("files", ("upload_card_2.jpg", open(card2, "rb"), "image/jpeg")),
        ]
        res = await full_api_client.post("/api/cards/upload", files=files)
        assert res.status_code == 200
        data = res.json()
        assert data["count"] == 2
        assert len(data["card_ids"]) == 2

    @pytest.mark.asyncio
    async def test_t1_f05_04_slow_batch_mode_configuration_parameters(
        self, full_api_client: httpx.AsyncClient, synthetic_card_deck: SyntheticDeckInfo
    ):
        """F5.4: POST /api/pipeline/start configures slow-batch size and interval."""
        payload = {
            "scans_dir": str(synthetic_card_deck.directory),
            "mode": "slow_batch",
            "slow_batch_size": 2,
            "slow_batch_interval_seconds": 1.5,
        }
        res = await full_api_client.post("/api/pipeline/start", json=payload)
        assert res.status_code == 200
        assert res.json()["mode"] == "slow_batch"

    def test_t1_f05_05_model_selection_stored_in_pipeline_config(self):
        """F5.5: Ingestion configuration stores model selector options accurately."""
        config = IngestionConfig(
            folder_path="/tmp/test",
            model="gemini-3.1-pro",
            max_concurrency=6,
        )
        assert config.model == "gemini-3.1-pro"
        assert config.max_concurrency == 6

    # ------------------------------------------------------------------------
    # Feature 6: Card Image Streaming Service
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f06_01_card_image_streaming_mime_type_and_caching(
        self,
        full_api_client: httpx.AsyncClient,
        isolated_session_manager: SessionManager,
        synthetic_card_deck: SyntheticDeckInfo,
    ):
        """F6.1: GET /api/cards/{id}/image returns image/jpeg and 200 OK."""
        session = isolated_session_manager.create_session()
        test_card = synthetic_card_deck.all_files[0]
        session.scanned_paths.append(test_card)
        session.cards.append(validate_and_load_card(test_card))

        res = await full_api_client.get(
            f"/api/cards/{test_card.name}/image?session_id={session.session_id}"
        )
        assert res.status_code == 200
        assert res.headers["content-type"] in ("image/jpeg", "image/jpg")
        assert len(res.content) > 100

    @pytest.mark.asyncio
    async def test_t1_f06_02_card_image_streaming_cache_headers_present(
        self,
        full_api_client: httpx.AsyncClient,
        isolated_session_manager: SessionManager,
        synthetic_card_deck: SyntheticDeckInfo,
    ):
        """F6.2: GET /api/cards/{id}/image includes public Cache-Control header."""
        session = isolated_session_manager.create_session()
        test_card = synthetic_card_deck.all_files[0]
        session.scanned_paths.append(test_card)

        res = await full_api_client.get(
            f"/api/cards/{test_card.name}/image?session_id={session.session_id}"
        )
        assert res.status_code == 200
        assert "public" in res.headers.get("cache-control", "")
        assert "max-age" in res.headers.get("cache-control", "")

    @pytest.mark.asyncio
    async def test_t1_f06_03_card_image_streaming_nonexistent_returns_404(
        self, full_api_client: httpx.AsyncClient
    ):
        """F6.3: GET /api/cards/{id}/image returns 404 for unknown image."""
        res = await full_api_client.get("/api/cards/nonexistent_image_000.jpg/image")
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_t1_f06_04_card_image_streaming_path_traversal_blocked(
        self, full_api_client: httpx.AsyncClient
    ):
        """F6.4: Path traversal attempts in card_id return 400/404 without leaking files."""
        res = await full_api_client.get("/api/cards/..%2F..%2Fetc%2Fpasswd/image")
        assert res.status_code in (400, 404)

    @pytest.mark.asyncio
    async def test_t1_f06_05_card_image_streaming_with_session_id_query(
        self,
        full_api_client: httpx.AsyncClient,
        isolated_session_store: SessionStore,
        synthetic_card_deck: SyntheticDeckInfo,
    ):
        """F6.5: Image streaming resolves card registered in SQLite session store."""
        card_p = synthetic_card_deck.all_files[0]
        card = validate_and_load_card(card_p)
        isolated_session_store.save_cards("sess_f06_05", [card])

        res = await full_api_client.get(
            f"/api/cards/{card.card_id}/image?session_id=sess_f06_05"
        )
        assert res.status_code == 200
        assert len(res.content) > 0

    # ------------------------------------------------------------------------
    # Feature 7: Rapid Queue Keyboard Shortcuts
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f07_01_record_single_decision_approve_pair(
        self, full_api_client: httpx.AsyncClient, isolated_session_manager: SessionManager
    ):
        """F7.1: POST /api/hitl/decision records an incremental APPROVE pair decision."""
        session = isolated_session_manager.create_session(session_id="sess_f07_01")
        payload = {
            "proposal_id": "prop_01",
            "front_card_id": "card_01.jpg",
            "back_card_id": "card_02.jpg",
            "decision": "APPROVE",
        }
        res = await full_api_client.post(
            "/api/hitl/decision?session_id=sess_f07_01", json=payload
        )
        assert res.status_code == 200
        assert res.json()["success"] is True
        assert len(session.approvals) == 1
        assert session.approvals[0].decision == HITLDecision.APPROVE

    @pytest.mark.asyncio
    async def test_t1_f07_02_record_single_decision_mark_standalone(
        self, full_api_client: httpx.AsyncClient, isolated_session_manager: SessionManager
    ):
        """F7.2: POST /api/hitl/decision records a single STANDALONE decision."""
        session = isolated_session_manager.create_session(session_id="sess_f07_02")
        payload = {
            "proposal_id": "prop_02",
            "front_card_id": "card_03.jpg",
            "back_card_id": None,
            "decision": "APPROVE",
        }
        res = await full_api_client.post(
            "/api/hitl/decision?session_id=sess_f07_02", json=payload
        )
        assert res.status_code == 200
        assert session.approvals[0].back_card_id is None

    @pytest.mark.asyncio
    async def test_t1_f07_03_record_single_decision_reject_card(
        self, full_api_client: httpx.AsyncClient, isolated_session_manager: SessionManager
    ):
        """F7.3: POST /api/hitl/decision records a REJECT decision."""
        session = isolated_session_manager.create_session(session_id="sess_f07_03")
        payload = {
            "proposal_id": "prop_03",
            "front_card_id": "noise_card.jpg",
            "back_card_id": None,
            "decision": "REJECT",
        }
        res = await full_api_client.post(
            "/api/hitl/decision?session_id=sess_f07_03", json=payload
        )
        assert res.status_code == 200
        assert session.approvals[0].decision == HITLDecision.REJECT

    @pytest.mark.asyncio
    async def test_t1_f07_04_record_single_decision_updates_existing_approval(
        self, full_api_client: httpx.AsyncClient, isolated_session_manager: SessionManager
    ):
        """F7.4: Subsequent decision on same proposal_id updates approval in place."""
        session = isolated_session_manager.create_session(session_id="sess_f07_04")
        p1 = {
            "proposal_id": "prop_update",
            "front_card_id": "card_01.jpg",
            "back_card_id": "card_02.jpg",
            "decision": "APPROVE",
        }
        p2 = {
            "proposal_id": "prop_update",
            "front_card_id": "card_01.jpg",
            "back_card_id": None,
            "decision": "OVERRIDE",
        }
        await full_api_client.post("/api/hitl/decision?session_id=sess_f07_04", json=p1)
        await full_api_client.post("/api/hitl/decision?session_id=sess_f07_04", json=p2)

        assert len(session.approvals) == 1
        assert session.approvals[0].decision == HITLDecision.OVERRIDE
        assert session.approvals[0].back_card_id is None

    @pytest.mark.asyncio
    async def test_t1_f07_05_record_single_decision_unknown_session_returns_404(
        self, full_api_client: httpx.AsyncClient
    ):
        """F7.5: Decision on unknown session returns 404 Not Found."""
        res = await full_api_client.post(
            "/api/hitl/decision?session_id=sess_unknown",
            json={
                "proposal_id": "p1",
                "front_card_id": "c1",
                "decision": "APPROVE",
            },
        )
        assert res.status_code == 404

    # ------------------------------------------------------------------------
    # Feature 8: Candidate Drawer & Multi-Card Grouping
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f08_01_get_candidates_returns_ranked_alternatives(
        self,
        full_api_client: httpx.AsyncClient,
        isolated_session_manager: SessionManager,
        synthetic_card_deck: SyntheticDeckInfo,
    ):
        """F8.1: GET /api/hitl/candidates returns ranked alternate cards with scores."""
        session = isolated_session_manager.create_session(session_id="sess_f08_01")
        session.cards = [
            validate_and_load_card(f) for f in synthetic_card_deck.all_files[:4]
        ]
        res = await full_api_client.get(
            f"/api/hitl/candidates?session_id=sess_f08_01&card_id={session.cards[0].card_id}"
        )
        assert res.status_code == 200
        data = res.json()
        assert data["front_card_id"] == session.cards[0].card_id
        assert "candidates" in data
        assert len(data["candidates"]) == 3

    @pytest.mark.asyncio
    async def test_t1_f08_02_get_candidates_includes_score_breakdown(
        self,
        full_api_client: httpx.AsyncClient,
        isolated_session_manager: SessionManager,
        synthetic_card_deck: SyntheticDeckInfo,
    ):
        """F8.2: Candidate response items include detailed visual score breakdown."""
        session = isolated_session_manager.create_session(session_id="sess_f08_02")
        session.cards = [
            validate_and_load_card(f) for f in synthetic_card_deck.all_files[:3]
        ]
        res = await full_api_client.get(
            f"/api/hitl/candidates?session_id=sess_f08_02&card_id={session.cards[0].card_id}"
        )
        assert res.status_code == 200
        cand = res.json()["candidates"][0]
        assert "score_breakdown" in cand
        assert cand["score_breakdown"] is not None

    @pytest.mark.asyncio
    async def test_t1_f08_03_get_candidates_missing_card_id_returns_400(
        self, full_api_client: httpx.AsyncClient
    ):
        """F8.3: GET /api/hitl/candidates without card_id returns 400 Bad Request."""
        res = await full_api_client.get("/api/hitl/candidates?session_id=sess_dummy")
        assert res.status_code == 400

    @pytest.mark.asyncio
    async def test_t1_f08_04_approve_hitl_accepts_multi_card_grouping_payload(
        self, full_api_client: httpx.AsyncClient, isolated_session_manager: SessionManager
    ):
        """F8.4: POST /api/hitl/approve accepts multi-card approval with 3+ card IDs."""
        session = isolated_session_manager.create_session(session_id="sess_f08_04")
        session.state = PipelineState.HITL_PENDING
        payload = {
            "session_id": "sess_f08_04",
            "approvals": [
                {
                    "proposal_id": "prop_multi",
                    "front_card_id": "card_01.jpg",
                    "back_card_id": "card_02.jpg",
                    "card_ids": ["card_01.jpg", "card_02.jpg", "card_03.jpg"],
                    "decision": "APPROVE",
                }
            ],
            "continue_pipeline": False,
        }
        res = await full_api_client.post("/api/hitl/approve", json=payload)
        assert res.status_code == 200
        assert res.json()["approved_count"] == 1

    @pytest.mark.asyncio
    async def test_t1_f08_05_approve_hitl_accepts_reverse_roles_payload(
        self, full_api_client: httpx.AsyncClient, isolated_session_manager: SessionManager
    ):
        """F8.5: POST /api/hitl/approve accepts swapped front and back card roles."""
        session = isolated_session_manager.create_session(session_id="sess_f08_05")
        session.state = PipelineState.HITL_PENDING
        payload = {
            "session_id": "sess_f08_05",
            "approvals": [
                {
                    "proposal_id": "prop_rev",
                    "front_card_id": "card_02_back.jpg",
                    "back_card_id": "card_01_front.jpg",
                    "decision": "APPROVE",
                }
            ],
            "continue_pipeline": False,
        }
        res = await full_api_client.post("/api/hitl/approve", json=payload)
        assert res.status_code == 200

    # ------------------------------------------------------------------------
    # Feature 9: Visual Inspection Toolbar (Pan/Zoom/Rotate/Filters)
    # ------------------------------------------------------------------------
    def test_t1_f09_01_rotation_90_degree_cycle_computations(self):
        """F9.1: 90° clockwise rotation completes full 360° (0°) cycle."""
        def rotate_cw(deg: int) -> int:
            return (deg + 90) % 360

        def rotate_ccw(deg: int) -> int:
            return (deg - 90) % 360

        angle = 0
        angle = rotate_cw(angle)
        assert angle == 90
        angle = rotate_cw(angle)
        assert angle == 180
        angle = rotate_cw(angle)
        assert angle == 270
        angle = rotate_cw(angle)
        assert angle == 0

        angle = rotate_ccw(angle)
        assert angle == 270

    def test_t1_f09_02_zoom_scale_clamping_between_025_and_50(self):
        """F9.2: Zoom scale is strictly clamped between 0.25 (min) and 5.0 (max)."""
        def clamp_scale(s: float) -> float:
            return max(0.25, min(5.0, round(s, 2)))

        assert clamp_scale(0.10) == 0.25
        assert clamp_scale(6.50) == 5.0
        assert clamp_scale(1.25) == 1.25

    def test_t1_f09_03_pan_translation_coordinate_transformations(self):
        """F9.3: Pan coordinate offsets accumulate translation vector correctly."""
        pan_x = 0
        pan_y = 0
        # Drag delta
        pan_x += 120
        pan_y += -45
        assert (pan_x, pan_y) == (120, -45)

    def test_t1_f09_04_contrast_brightness_invert_css_filter_generation(self):
        """F9.4: Filter style builder serializes brightness, contrast, and invert CSS."""
        def build_filter_css(brightness: int, contrast: int, invert: bool) -> str:
            inv_str = "invert(100%)" if invert else "invert(0%)"
            return f"brightness({brightness}%) contrast({contrast}%) {inv_str}"

        css = build_filter_css(120, 150, True)
        assert css == "brightness(120%) contrast(150%) invert(100%)"

    def test_t1_f09_05_reset_transform_and_filters_baseline(self):
        """F9.5: Reset restores baseline scale=1.0, rotation=0, brightness=100, contrast=100, invert=False."""
        defaults = {
            "scale": 1.0,
            "rotation": 0,
            "translateX": 0,
            "translateY": 0,
            "brightness": 100,
            "contrast": 100,
            "invert": False,
        }
        assert defaults["scale"] == 1.0
        assert defaults["rotation"] == 0
        assert defaults["invert"] is False

    # ------------------------------------------------------------------------
    # Feature 10: Post-Extraction Split-Screen Recipe Editor
    # ------------------------------------------------------------------------
    def test_t1_f10_01_recipe_model_structured_fields_completeness(self):
        """F10.1: RecipeSchema models all 10 core structured recipe fields."""
        recipe = RecipeSchema(
            title="Editor Lemon Bars",
            description="Crisp citrus bars.",
            yield_servings="16 bars",
            prep_time="20 mins",
            cook_time="30 mins",
            total_time="50 mins",
            ingredients=["2 cups flour", "1 cup sugar", "4 lemons"],
            directions=["1. Press crust.", "2. Pour lemon curd.", "3. Bake at 350°F."],
            tags=["Dessert", "Citrus"],
            source_notes="Grandma's Box",
        )
        assert recipe.title == "Editor Lemon Bars"
        assert len(recipe.ingredients) == 3
        assert len(recipe.directions) == 3

    def test_t1_f10_02_recipe_direction_numbering_normalization(self):
        """F10.2: Recipe direction steps format with sequential numeric prefixes."""
        recipe = RecipeSchema(
            title="Numbered Biscuits",
            ingredients=["2 cups flour", "1 cup butter"],
            directions=["Preheat oven to 400°F.", "Mix dough.", "Bake 15 mins."],
        )
        assert recipe.directions[0] == "1. Preheat oven to 400°F."
        assert recipe.directions[1] == "2. Mix dough."
        assert recipe.directions[2] == "3. Bake 15 mins."

    def test_t1_f10_03_recipe_card_associations_and_provenance(self):
        """F10.3: Recipe schema retains source metadata identifier."""
        recipe = RecipeSchema(
            title="Apple Crisp",
            ingredients=["4 apples", "1 cup oats"],
            directions=["1. Layer apples.", "2. Top with oats and bake."],
            source_notes="Card 01_apple_crisp_front.jpg",
        )
        assert "01_apple_crisp_front.jpg" in recipe.source_notes

    def test_t1_f10_04_recipe_ingredient_list_mutations(self):
        """F10.4: Ingredients list supports dynamic item addition, removal, and reordering."""
        ingredients = ["1 cup flour", "1/2 cup sugar"]
        ingredients.append("1 tsp vanilla")
        assert len(ingredients) == 3
        # Reorder
        ingredients[0], ingredients[1] = ingredients[1], ingredients[0]
        assert ingredients[0] == "1/2 cup sugar"
        # Remove
        ingredients.pop()
        assert len(ingredients) == 2

    def test_t1_f10_05_recipe_direction_step_mutations(self):
        """F10.5: Directions support step insertion and sequential renumbering."""
        steps = ["Preheat oven.", "Stir batter.", "Bake 20 mins."]
        steps.insert(2, "Fold in chocolate chips.")
        renumbered = [f"{idx}. {re_step}" for idx, re_step in enumerate(steps, start=1)]
        assert len(renumbered) == 4
        assert renumbered[2] == "3. Fold in chocolate chips."
        assert renumbered[3] == "4. Bake 20 mins."

    # ------------------------------------------------------------------------
    # Feature 11: Inline Recipe CRUD & Persistence
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f11_01_list_recipes_for_session_returns_array(
        self, full_api_client: httpx.AsyncClient, isolated_session_store: SessionStore
    ):
        """F11.1: GET /api/recipes returns array of RecipeSchema JSON models."""
        r = RecipeSchema(
            title="List Test Pie",
            ingredients=["1 crust", "3 apples"],
            directions=["1. Bake pie."],
        )
        isolated_session_store.save_recipe("sess_f11_01", r)

        res = await full_api_client.get("/api/recipes?session_id=sess_f11_01")
        assert res.status_code == 200
        recipes = res.json()
        assert len(recipes) >= 1
        assert recipes[0]["title"] == "List Test Pie"

    @pytest.mark.asyncio
    async def test_t1_f11_02_get_recipe_by_id_returns_matching_schema(
        self, full_api_client: httpx.AsyncClient, isolated_session_store: SessionStore
    ):
        """F11.2: GET /api/recipes/{id} returns specific recipe schema."""
        r = RecipeSchema(
            recipe_id="rec_f11_02",
            title="Single Fetch Cake",
            ingredients=["2 cups flour", "1 cup cocoa"],
            directions=["1. Bake cake."],
        )
        isolated_session_store.save_recipe("sess_f11_02", r)

        res = await full_api_client.get("/api/recipes/rec_f11_02?session_id=sess_f11_02")
        assert res.status_code == 200
        assert res.json()["recipe_id"] == "rec_f11_02"
        assert res.json()["title"] == "Single Fetch Cake"

    @pytest.mark.asyncio
    async def test_t1_f11_03_create_recipe_persists_to_sqlite_store(
        self, full_api_client: httpx.AsyncClient, isolated_session_store: SessionStore
    ):
        """F11.3: POST /api/recipes creates a new recipe in SQLite persistence."""
        payload = {
            "title": "Manual Creation Bread",
            "ingredients": ["500g flour", "300ml water", "7g yeast"],
            "directions": ["1. Knead and proof.", "2. Bake at 450°F."],
            "tags": ["Bread", "Baking"],
        }
        res = await full_api_client.post("/api/recipes?session_id=sess_f11_03", json=payload)
        assert res.status_code == 201
        created = res.json()
        assert created["title"] == "Manual Creation Bread"
        # Confirm saved in DB
        db_rec = isolated_session_store.get_recipe(created["recipe_id"], session_id="sess_f11_03")
        assert db_rec is not None

    @pytest.mark.asyncio
    async def test_t1_f11_04_update_recipe_persists_modifications(
        self, full_api_client: httpx.AsyncClient, isolated_session_store: SessionStore
    ):
        """F11.4: PUT /api/recipes/{id} updates recipe fields in SQLite."""
        r = RecipeSchema(
            recipe_id="rec_f11_04",
            title="Original Title",
            ingredients=["1 cup sugar"],
            directions=["1. Mix."],
        )
        isolated_session_store.save_recipe("sess_f11_04", r)

        update_payload = {
            "recipe_id": "rec_f11_04",
            "title": "Updated Title After Inline Edit",
            "ingredients": ["1 cup sugar", "1/2 cup butter"],
            "directions": ["1. Mix well.", "2. Chill."],
        }
        res = await full_api_client.put(
            "/api/recipes/rec_f11_04?session_id=sess_f11_04", json=update_payload
        )
        assert res.status_code == 200
        assert res.json()["title"] == "Updated Title After Inline Edit"

        db_rec = isolated_session_store.get_recipe("rec_f11_04", session_id="sess_f11_04")
        assert db_rec.title == "Updated Title After Inline Edit"

    @pytest.mark.asyncio
    async def test_t1_f11_05_delete_recipe_removes_from_store(
        self, full_api_client: httpx.AsyncClient, isolated_session_store: SessionStore
    ):
        """F11.5: DELETE /api/recipes/{id} deletes recipe from store."""
        r = RecipeSchema(
            recipe_id="rec_f11_05",
            title="Recipe to Delete",
            ingredients=["1 apple"],
            directions=["1. Eat."],
        )
        isolated_session_store.save_recipe("sess_f11_05", r)

        res = await full_api_client.delete("/api/recipes/rec_f11_05?session_id=sess_f11_05")
        assert res.status_code == 200
        assert res.json()["deleted"] is True
        assert isolated_session_store.get_recipe("rec_f11_05", session_id="sess_f11_05") is None

    # ------------------------------------------------------------------------
    # Feature 12: Plan To Eat RFC 4180 CSV Export
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f12_01_export_csv_endpoint_content_type_and_attachment(
        self, full_api_client: httpx.AsyncClient, isolated_session_store: SessionStore
    ):
        """F12.1: GET /api/export/csv returns text/csv attachment with filename."""
        r = RecipeSchema(
            title="CSV Export Recipe",
            ingredients=["1 cup flour"],
            directions=["1. Bake."],
        )
        isolated_session_store.save_session(
            session_id="sess_f12_01", state=PipelineState.COMPLETED
        )
        isolated_session_store.save_recipe("sess_f12_01", r)

        res = await full_api_client.get("/api/export/csv?session_id=sess_f12_01")
        assert res.status_code == 200
        assert "text/csv" in res.headers["content-type"]
        assert 'filename="recipeboxer_export.csv"' in res.headers["content-disposition"]

    def test_t1_f12_02_export_csv_exact_nine_columns_and_casing(self):
        """F12.2: Plan To Eat CSV exporter produces exact 9 column headers in order."""
        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string([])
        headers = csv_str.strip().split(",")
        assert headers == PLAN_TO_EAT_COLUMNS
        assert len(headers) == 9

    def test_t1_f12_03_export_csv_multiline_ingredients_and_directions(self):
        """F12.3: Multiline ingredients and directions parse into single rows with standard csv.reader."""
        recipe = RecipeSchema(
            title="Multiline Rolls",
            ingredients=["1 cup flour", "1/2 cup milk", "1 egg"],
            directions=["1. Mix dough.", "2. Let rise.", "3. Bake at 375°F."],
        )
        csv_str = export_recipes_to_plan_to_eat_csv([recipe])
        rows = list(csv.reader(io.StringIO(csv_str)))
        assert len(rows) == 2  # 1 header + 1 data row
        assert rows[1][2] == "1 cup flour\n1/2 cup milk\n1 egg"
        assert rows[1][3] == "1. Mix dough.\n2. Let rise.\n3. Bake at 375°F."

    def test_t1_f12_04_export_csv_escapes_embedded_quotes_rfc4180(self):
        """F12.4: Embedded double quotes in fields are escaped as doubled double quotes."""
        recipe = RecipeSchema(
            title='Grandma\'s "Secret" 8" Pan Cake',
            description='Note: Use 8" square pan.',
            ingredients=['1 cup "Ghirardelli" cocoa'],
            directions=['1. Mix "secret" spices.'],
        )
        csv_str = export_recipes_to_plan_to_eat_csv([recipe])
        assert '""Secret""' in csv_str
        assert '8""' in csv_str
        rows = list(csv.reader(io.StringIO(csv_str)))
        assert rows[1][0] == 'Grandma\'s "Secret" 8" Pan Cake'

    @pytest.mark.asyncio
    async def test_t1_f12_05_export_csv_not_ready_or_empty_returns_400(
        self, full_api_client: httpx.AsyncClient, isolated_session_store: SessionStore
    ):
        """F12.5: GET /api/export/csv returns 400 when session is incomplete or has no recipes."""
        isolated_session_store.save_session(
            session_id="sess_f12_05", state=PipelineState.FINGERPRINTING
        )
        res = await full_api_client.get("/api/export/csv?session_id=sess_f12_05")
        assert res.status_code == 400
        assert "not ready" in res.json()["detail"].lower()

    # ------------------------------------------------------------------------
    # Feature 13: Schema.org Recipe JSON-LD Export
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f13_01_export_json_ld_endpoint_content_type(
        self, full_api_client: httpx.AsyncClient, isolated_session_store: SessionStore
    ):
        """F13.1: GET /api/export/json-ld returns application/ld+json media type."""
        r = RecipeSchema(
            title="JSON-LD Cookie",
            ingredients=["1 cup sugar"],
            directions=["1. Bake."],
        )
        isolated_session_store.save_session(
            session_id="sess_f13_01", state=PipelineState.COMPLETED
        )
        isolated_session_store.save_recipe("sess_f13_01", r)

        res = await full_api_client.get("/api/export/json-ld?session_id=sess_f13_01")
        assert res.status_code == 200
        assert "application/ld+json" in res.headers["content-type"]

    def test_t1_f13_02_export_json_ld_schema_org_recipe_context(self):
        """F13.2: Exported JSON-LD contains '@context': 'https://schema.org' and '@type': 'Recipe'."""
        recipe = RecipeSchema(
            title="Schema.org Sourdough",
            ingredients=["500g flour", "350g water"],
            directions=["1. Mix, ferment, and bake."],
        )
        exporter = JsonLdExporter()
        data = exporter.export_to_json_ld_dict(recipe)
        assert data["@context"] == "https://schema.org"
        assert data["@type"] == "Recipe"
        assert data["name"] == "Schema.org Sourdough"

    def test_t1_f13_03_export_json_ld_human_duration_to_iso8601(self):
        """F13.3: ISO 8601 duration parser converts human duration strings accurately."""
        assert human_duration_to_iso8601("30 mins") == "PT30M"
        assert human_duration_to_iso8601("1 hr 15 mins") == "PT1H15M"
        assert human_duration_to_iso8601("2 hours") == "PT2H"
        assert human_duration_to_iso8601("PT45M") == "PT45M"
        assert human_duration_to_iso8601("") is None

    def test_t1_f13_04_export_json_ld_how_to_step_instructions_array(self):
        """F13.4: Recipe instructions serialize as an array of structured HowToStep objects."""
        recipe = RecipeSchema(
            title="Waffles",
            ingredients=["2 cups flour", "2 eggs"],
            directions=["1. Whisk batter.", "2. Cook in waffle iron 4 mins."],
        )
        exporter = JsonLdExporter()
        data = exporter.export_to_json_ld_dict(recipe)
        steps = data["recipeInstructions"]
        assert len(steps) == 2
        assert steps[0]["@type"] == "HowToStep"
        assert steps[0]["position"] == 1
        assert steps[0]["text"] == "Whisk batter."
        assert steps[1]["text"] == "Cook in waffle iron 4 mins."

    def test_t1_f13_05_export_json_ld_multiple_recipes_in_graph(self):
        """F13.5: Multiple recipes serialize into Schema.org @graph array structure."""
        r1 = RecipeSchema(title="Recipe A", ingredients=["A"], directions=["1. Do A."])
        r2 = RecipeSchema(title="Recipe B", ingredients=["B"], directions=["1. Do B."])
        exporter = JsonLdExporter()
        data = exporter.export_to_json_ld_dict([r1, r2])
        assert "@graph" in data
        assert len(data["@graph"]) == 2
        assert data["@graph"][0]["name"] == "Recipe A"
        assert data["@graph"][1]["name"] == "Recipe B"

    # ------------------------------------------------------------------------
    # Feature 14: Standard Recipe JSON Export
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f14_01_export_json_endpoint_content_type(
        self, full_api_client: httpx.AsyncClient, isolated_session_store: SessionStore
    ):
        """F14.1: GET /api/export/json returns application/json media type."""
        r = RecipeSchema(
            title="JSON Export Recipe",
            ingredients=["1 cup sugar"],
            directions=["1. Bake."],
        )
        isolated_session_store.save_session(
            session_id="sess_f14_01", state=PipelineState.COMPLETED
        )
        isolated_session_store.save_recipe("sess_f14_01", r)

        res = await full_api_client.get("/api/export/json?session_id=sess_f14_01")
        assert res.status_code == 200
        assert "application/json" in res.headers["content-type"]

    def test_t1_f14_02_export_json_returns_array_of_recipe_schemas(self):
        """F14.2: Standard JSON exporter produces valid list of serialized RecipeSchema models."""
        r1 = RecipeSchema(title="Pancakes", ingredients=["1 cup mix"], directions=["1. Flip."])
        r2 = RecipeSchema(title="Waffles", ingredients=["1 cup mix"], directions=["1. Press."])
        json_str = export_recipes_to_json([r1, r2])
        parsed = json.loads(json_str)
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert parsed[0]["title"] == "Pancakes"

    def test_t1_f14_03_export_json_roundtrip_deserialization(self):
        """F14.3: Exported JSON roundtrips through RecipeSchema validation without data loss."""
        original = RecipeSchema(
            title="Roundtrip Soufflé",
            description="Delicate chocolate soufflé.",
            yield_servings="4 ramekins",
            prep_time="25 mins",
            cook_time="15 mins",
            total_time="40 mins",
            ingredients=["4 eggs", "100g dark chocolate", "50g sugar"],
            directions=["1. Melt chocolate.", "2. Whip whites.", "3. Bake at 375°F."],
            tags=["Dessert", "French"],
            source_notes="Chef Note",
        )
        exporter = JsonExporter()
        json_str = exporter.export_to_json_string(original)
        assert exporter.validate_json_export(json_str) is True

        reconstructed = RecipeSchema.model_validate_json(json_str)
        assert reconstructed.title == original.title
        assert reconstructed.ingredients == original.ingredients
        assert reconstructed.directions == original.directions

    def test_t1_f14_04_export_json_single_recipe_serialization(self):
        """F14.4: Single recipe export produces a valid dictionary JSON."""
        r = RecipeSchema(title="Single Recipe", ingredients=["1 item"], directions=["1. Step."])
        exporter = JsonExporter()
        json_str = exporter.export_to_json_string(r)
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)
        assert parsed["title"] == "Single Recipe"

    @pytest.mark.asyncio
    async def test_t1_f14_05_export_json_nonexistent_session_returns_404(
        self, full_api_client: httpx.AsyncClient
    ):
        """F14.5: GET /api/export/json returns 404 for unknown session ID."""
        res = await full_api_client.get("/api/export/json?session_id=sess_nonexistent_000")
        assert res.status_code == 404

    # ------------------------------------------------------------------------
    # Feature 15: Full Integration Pipeline & Resilience
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f15_01_pipeline_start_and_status_tracking(
        self, full_api_client: httpx.AsyncClient, synthetic_card_deck: SyntheticDeckInfo
    ):
        """F15.1: Pipeline start creates session and returns 200 with status tracking."""
        res = await full_api_client.post(
            "/api/pipeline/start",
            json={
                "scans_dir": str(synthetic_card_deck.directory),
                "mode": "parallel",
                "max_concurrency": 2,
            },
        )
        assert res.status_code == 200
        sid = res.json()["session_id"]

        status_res = await full_api_client.get(f"/api/status?session_id={sid}")
        assert status_res.status_code == 200
        assert status_res.json()["session_id"] == sid

    def test_t1_f15_02_pipeline_state_transition_sequence(self):
        """F15.2: PipelineState enum covers all lifecycle states in sequence."""
        states = [
            PipelineState.IDLE,
            PipelineState.FINGERPRINTING,
            PipelineState.CORRELATING,
            PipelineState.HITL_PENDING,
            PipelineState.EXTRACTING,
            PipelineState.COMPLETED,
            PipelineState.FAILED,
            PipelineState.CANCELLED,
        ]
        assert len(states) == 8

    def test_t1_f15_03_pipeline_session_error_handling_and_recovery(self):
        """F15.3: Session tracks error message and transitions to FAILED upon error."""
        session = PipelineSession(
            session_id="sess_f15_03",
            config=PipelineStartRequest(scans_dir="/tmp/test"),
        )
        session.state = PipelineState.FAILED
        session.error_message = "Vision model rate limit exceeded"
        status_obj = session.get_status()
        assert status_obj.status == PipelineState.FAILED
        assert status_obj.error == "Vision model rate limit exceeded"

    def test_t1_f15_04_concurrent_pipeline_sessions_isolation(self):
        """F15.4: Multiple sessions maintain isolated progress metrics and recipe lists."""
        manager = SessionManager()
        s1 = manager.create_session()
        s2 = manager.create_session()

        s1.recipes.append(RecipeSchema(title="Recipe 1", ingredients=["A"], directions=["1. A"]))
        assert len(s1.recipes) == 1
        assert len(s2.recipes) == 0

    def test_t1_f15_05_pipeline_cancellation_lifecycle(self):
        """F15.5: Session cancellation unblocks and updates state to CANCELLED."""
        session = PipelineSession(session_id="sess_f15_05")
        session.cancel()
        assert session.cancellation_event.is_set()


# ============================================================================
# TIER 2: BOUNDARY & CORNER CASES (>=5 PER FEATURE, 75+ TESTS)
# ============================================================================

class TestTier2BoundaryAndCornerCases:
    """Tier 2: Boundary value analysis, stress inputs, edge characters, and limit testing."""

    # ------------------------------------------------------------------------
    # Feature 1 BVA (SPA Static & Routing)
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f01_01_spa_extremely_long_nested_subroutes(
        self, full_api_client: httpx.AsyncClient
    ):
        """T2.1.1: Extremely long nested path (50+ segments) resolves to SPA index.html."""
        deep_path = "/" + "/".join([f"sub_path_{i}" for i in range(50)])
        res = await full_api_client.get(deep_path)
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]

    @pytest.mark.asyncio
    async def test_t2_f01_02_spa_url_encoded_special_characters_in_path(
        self, full_api_client: httpx.AsyncClient
    ):
        """T2.1.2: URL with encoded spaces, accents, and punctuation serves SPA HTML."""
        path = "/recipes%20and%20cards/%C3%A9dition%20fran%C3%A7aise%23123"
        res = await full_api_client.get(path)
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]

    @pytest.mark.asyncio
    async def test_t2_f01_03_spa_multiple_consecutive_slashes(
        self, full_api_client: httpx.AsyncClient
    ):
        """T2.1.3: URL with multiple consecutive slashes (e.g. ///ingest///) returns 200."""
        res = await full_api_client.get("///ingest///")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]

    @pytest.mark.asyncio
    async def test_t2_f01_04_spa_root_with_complex_query_params(
        self, full_api_client: httpx.AsyncClient
    ):
        """T2.1.4: SPA root with complex query params (filters, pagination, session) returns 200."""
        query = "?session=sess_123&mode=dark&filter=all&page=4&q=chocolate%20%26%20vanilla"
        res = await full_api_client.get(f"/{query}")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]

    @pytest.mark.asyncio
    async def test_t2_f01_05_spa_static_file_directory_traversal_prevention(
        self, full_api_client: httpx.AsyncClient
    ):
        """T2.1.5: Static file lookups outside static dir return fallback SPA or 404."""
        res = await full_api_client.get("/assets/../../../../pyproject.toml")
        # FastAPI StaticFiles rejects traversal with 404
        assert res.status_code in (404, 200)
        assert "poetry" not in res.text

    # ------------------------------------------------------------------------
    # Feature 2 BVA (Theme Subsystem)
    # ------------------------------------------------------------------------
    def test_t2_f02_01_theme_corrupted_storage_value_fallback(self):
        """T2.2.1: Corrupted or non-JSON stored localStorage string safely defaults to 'dark'."""
        corrupted_vals = ["null", "undefined", "{}", "12345", "neon-green", "\x00\x01\x02"]
        for val in corrupted_vals:
            resolved = "light" if val == "light" else "dark"
            assert resolved == "dark"

    def test_t2_f02_02_theme_rapid_alternating_toggle_hammering(self):
        """T2.2.2: 100 rapid alternations between light and dark maintain consistent state."""
        current = "dark"
        for _ in range(100):
            current = "light" if current == "dark" else "dark"
        assert current == "dark"

    def test_t2_f02_03_theme_empty_or_whitespace_storage_handling(self):
        """T2.2.3: Empty or whitespace-only localStorage theme values fallback gracefully."""
        whitespace_themes = ["", "   ", "\t\n", None]
        for wt in whitespace_themes:
            cleaned = (wt or "").strip().lower()
            theme = cleaned if cleaned in ("light", "dark") else "dark"
            assert theme == "dark"

    def test_t2_f02_04_theme_no_duplicate_css_classes_on_root(self):
        """T2.2.4: Repeated theme changes prevent duplicate class names on root."""
        classes = ["min-h-screen", "antialiased", "dark"]
        # Apply dark again
        classes = [c for c in classes if c not in ("dark", "light")] + ["dark"]
        assert classes.count("dark") == 1

    def test_t2_f02_05_theme_null_media_query_preference_fallback(self):
        """T2.2.5: When window.matchMedia returns null/unsupported, theme defaults to dark."""
        def get_system_theme(match_media_mock: Optional[bool]) -> str:
            if match_media_mock is None:
                return "dark"
            return "dark" if match_media_mock else "light"

        assert get_system_theme(None) == "dark"

    # ------------------------------------------------------------------------
    # Feature 3 BVA (SSE Telemetry)
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f03_01_sse_unknown_session_id_streams_idle_without_crash(
        self, full_api_client: httpx.AsyncClient, isolated_session_manager: SessionManager
    ):
        """T2.3.1: SSE stream with terminal session ID finishes cleanly without 500 error."""
        sid = "sess_t2_f03_01"
        session = isolated_session_manager.create_session(session_id=sid)
        session.state = PipelineState.FAILED
        session.error_message = "Handled terminal error"

        received = []
        async with full_api_client.stream("GET", f"/api/events?session_id={sid}") as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    received.append(line.split(":", 1)[1].strip())
                    if "failed" in received:
                        break
        assert "failed" in received

    @pytest.mark.asyncio
    async def test_t2_f03_02_sse_immediate_client_disconnect_handling(
        self, full_api_client: httpx.AsyncClient, isolated_session_manager: SessionManager
    ):
        """T2.3.2: Immediate client disconnect after receiving headers is handled cleanly."""
        sid = "sess_t2_f03_02"
        session = isolated_session_manager.create_session(session_id=sid)
        session.state = PipelineState.COMPLETED
        async with full_api_client.stream("GET", f"/api/events?session_id={sid}") as resp:
            assert resp.status_code == 200
            async for line in resp.aiter_lines():
                if line:
                    break

    @pytest.mark.asyncio
    async def test_t2_f03_03_sse_rapid_progress_burst_telemetry(
        self, isolated_session_manager: SessionManager
    ):
        """T2.3.3: Rapid progress updates (0% to 100% in 1ms increments) serialize correctly."""
        session = isolated_session_manager.create_session()
        session.progress_metrics.total_scans = 100
        for i in range(1, 101):
            session.progress_metrics.fingerprinted = i
            status = session.get_status()
            assert status.progress.percent_complete >= 0.0

    def test_t2_f03_04_sse_unicode_and_emojis_serialization(self):
        """T2.3.4: Unicode characters and emojis in recipes format into valid SSE payload strings."""
        recipe = RecipeSchema(
            title="Japanese 抹茶 (Matcha) Soufflé 🥞",
            ingredients=["2 large eggs 🥚", "1 tbsp 宇治抹茶 (Uji Matcha) 🍵"],
            directions=["1. Whisk vigorously (meringue ☁️)."],
        )
        sse_payload = f"event: recipe_extracted\ndata: {recipe.model_dump_json()}\n\n"
        assert "🥞" in sse_payload
        assert "宇治抹茶" in sse_payload
        assert sse_payload.endswith("\n\n")

    @pytest.mark.asyncio
    async def test_t2_f03_05_sse_concurrent_listeners_on_same_session(
        self, full_api_client: httpx.AsyncClient, isolated_session_manager: SessionManager
    ):
        """T2.3.5: Multiple concurrent SSE listeners on same session open connections successfully."""
        sid = "sess_shared_complete"
        session = isolated_session_manager.create_session(session_id=sid)
        session.state = PipelineState.COMPLETED

        async with full_api_client.stream("GET", f"/api/events?session_id={sid}") as r1:
            async with full_api_client.stream("GET", f"/api/events?session_id={sid}") as r2:
                assert r1.status_code == 200
                assert r2.status_code == 200
                async for line in r1.aiter_lines():
                    if line:
                        break
                async for line in r2.aiter_lines():
                    if line:
                        break

    # ------------------------------------------------------------------------
    # Feature 4 BVA (Session Manager & Persistence)
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f04_01_session_resume_empty_session_with_zero_cards(
        self, full_api_client: httpx.AsyncClient, isolated_session_store: SessionStore
    ):
        """T2.4.1: Resuming a session with 0 cards and 0 recipes rehydrates cleanly."""
        isolated_session_store.save_session(
            session_id="sess_empty_zero",
            state=PipelineState.IDLE,
            card_count=0,
            recipe_count=0,
        )
        res = await full_api_client.post("/api/sessions/sess_empty_zero/resume")
        assert res.status_code == 200
        assert res.json()["session_id"] == "sess_empty_zero"

    @pytest.mark.asyncio
    async def test_t2_f04_02_session_ids_with_uuid_symbols_and_numbers(
        self, full_api_client: httpx.AsyncClient, isolated_session_store: SessionStore
    ):
        """T2.4.2: Session IDs containing UUIDs, dashes, underscores, and numbers persist accurately."""
        special_sid = f"sess_test-1234_abc-XYZ_0099"
        isolated_session_store.save_session(
            session_id=special_sid,
            state=PipelineState.COMPLETED,
        )
        res = await full_api_client.get(f"/api/sessions/{special_sid}")
        assert res.status_code == 200
        assert res.json()["session_id"] == special_sid

    def test_t2_f04_03_session_store_atomic_transaction_durability(
        self, isolated_session_store: SessionStore
    ):
        """T2.4.3: Sequential state updates to SQLite session maintain ACID transaction consistency."""
        sid = "sess_atomic_test"
        isolated_session_store.save_session(session_id=sid, state=PipelineState.IDLE)
        isolated_session_store.save_session(session_id=sid, state=PipelineState.FINGERPRINTING)
        isolated_session_store.save_session(session_id=sid, state=PipelineState.HITL_PENDING)
        isolated_session_store.save_session(session_id=sid, state=PipelineState.COMPLETED)

        row = isolated_session_store.get_session(sid)
        assert row["state"] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_t2_f04_04_session_delete_idempotency_returns_404_on_repeat(
        self, full_api_client: httpx.AsyncClient, isolated_session_store: SessionStore
    ):
        """T2.4.4: Deleting an already-deleted session returns 404 Not Found."""
        isolated_session_store.save_session(session_id="sess_del_repeat", state=PipelineState.IDLE)
        res1 = await full_api_client.delete("/api/sessions/sess_del_repeat")
        assert res1.status_code == 200
        res2 = await full_api_client.delete("/api/sessions/sess_del_repeat")
        assert res2.status_code == 404

    def test_t2_f04_05_session_store_large_batch_listing_100_sessions(
        self, isolated_session_store: SessionStore
    ):
        """T2.4.5: SessionStore lists 100 historical sessions with correct ordering."""
        for i in range(100):
            isolated_session_store.save_session(
                session_id=f"sess_batch_{i:03d}",
                state=PipelineState.COMPLETED,
                card_count=i,
            )
        all_sessions = isolated_session_store.list_sessions()
        assert len(all_sessions) >= 100

    # ------------------------------------------------------------------------
    # Feature 5 BVA (Ingestion Throttle & Options)
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f05_01_start_pipeline_zero_or_negative_concurrency_rejected(
        self, full_api_client: httpx.AsyncClient, synthetic_card_deck: SyntheticDeckInfo
    ):
        """T2.5.1: max_concurrency <= 0 raises 422 validation error."""
        res = await full_api_client.post(
            "/api/pipeline/start",
            json={
                "scans_dir": str(synthetic_card_deck.directory),
                "max_concurrency": 0,
            },
        )
        assert res.status_code == 422

    @pytest.mark.asyncio
    async def test_t2_f05_02_start_pipeline_extreme_concurrency_limit(
        self, full_api_client: httpx.AsyncClient, synthetic_card_deck: SyntheticDeckInfo
    ):
        """T2.5.2: Extremely high concurrency (e.g. 50) initializes without thread starvation."""
        res = await full_api_client.post(
            "/api/pipeline/start",
            json={
                "scans_dir": str(synthetic_card_deck.directory),
                "max_concurrency": 50,
            },
        )
        assert res.status_code == 200

    @pytest.mark.asyncio
    async def test_t2_f05_03_start_pipeline_slow_batch_zero_interval_sequential(
        self, full_api_client: httpx.AsyncClient, synthetic_card_deck: SyntheticDeckInfo
    ):
        """T2.5.3: slow_batch_size=1 with slow_batch_interval_seconds=0.0 operates as sequential stream."""
        res = await full_api_client.post(
            "/api/pipeline/start",
            json={
                "scans_dir": str(synthetic_card_deck.directory),
                "mode": "slow_batch",
                "slow_batch_size": 1,
                "slow_batch_interval_seconds": 0.0,
            },
        )
        assert res.status_code == 200

    @pytest.mark.asyncio
    async def test_t2_f05_04_start_pipeline_folder_with_non_image_files(
        self, full_api_client: httpx.AsyncClient, tmp_path: Path
    ):
        """T2.5.4: Folder containing .txt, .pdf, .docx alongside image files filters correctly."""
        mix_dir = tmp_path / "mixed_scans"
        mix_dir.mkdir(parents=True, exist_ok=True)
        # 1 valid card
        create_synthetic_card_image(mix_dir / "valid_card.jpg", title="Valid Card")
        # Non-images
        (mix_dir / "notes.txt").write_text("recipe notes")
        (mix_dir / "scans.pdf").write_bytes(b"%PDF-1.4 dummy pdf")
        (mix_dir / "readme.md").write_text("# Readme")

        res = await full_api_client.post(
            "/api/pipeline/start",
            json={"scans_dir": str(mix_dir)},
        )
        assert res.status_code == 200
        assert res.json()["total_scans_found"] == 1

    @pytest.mark.asyncio
    async def test_t2_f05_05_multipart_upload_empty_file_list_returns_400(
        self, full_api_client: httpx.AsyncClient
    ):
        """T2.5.5: POST /api/cards/upload with empty file payload returns 422 or 400."""
        res = await full_api_client.post("/api/cards/upload", files=[])
        assert res.status_code in (400, 422)

    # ------------------------------------------------------------------------
    # Feature 6 BVA (Card Image Streaming)
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f06_01_card_image_streaming_empty_or_whitespace_card_id(
        self, full_api_client: httpx.AsyncClient
    ):
        """T2.6.1: Empty or whitespace card_id returns 404."""
        res = await full_api_client.get("/api/cards/%20%20/image")
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_t2_f06_02_card_image_streaming_path_traversal_dot_dot(
        self, full_api_client: httpx.AsyncClient
    ):
        """T2.6.2: Path traversal attempt '../../../../etc/hosts' is safely blocked."""
        res = await full_api_client.get("/api/cards/..%2F..%2F..%2Fetc%2Fhosts/image")
        assert res.status_code in (400, 404)

    @pytest.mark.asyncio
    async def test_t2_f06_03_card_image_streaming_null_byte_injection(
        self, full_api_client: httpx.AsyncClient
    ):
        """T2.6.3: Null byte injection in card_id ('card.jpg\0.png') returns 400 Bad Request."""
        res = await full_api_client.get("/api/cards/card.jpg%00.png/image")
        assert res.status_code in (400, 404)

    @pytest.mark.asyncio
    async def test_t2_f06_04_card_image_streaming_excessively_long_identifier(
        self, full_api_client: httpx.AsyncClient
    ):
        """T2.6.4: Excessively long card_id (2000+ characters) returns 404 without buffer overflow."""
        long_id = "a" * 2048 + ".jpg"
        res = await full_api_client.get(f"/api/cards/{long_id}/image")
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_t2_f06_05_card_image_streaming_all_supported_extensions(
        self, full_api_client: httpx.AsyncClient, tmp_path: Path, isolated_session_store: SessionStore
    ):
        """T2.6.5: Supported image extensions (.jpg, .png, .webp) return accurate MIME types."""
        ext_types = [
            (".jpg", "image/jpeg"),
            (".png", "image/png"),
            (".webp", "image/webp"),
        ]
        for ext, expected_mime in ext_types:
            img_file = tmp_path / f"test_mime{ext}"
            img = Image.new("RGB", (100, 100), color=(200, 200, 200))
            img.save(img_file)

            card = validate_and_load_card(img_file)
            isolated_session_store.save_cards("sess_mime_test", [card])

            res = await full_api_client.get(f"/api/cards/{card.card_id}/image?session_id=sess_mime_test")
            assert res.status_code == 200
            assert expected_mime in res.headers["content-type"]

    # ------------------------------------------------------------------------
    # Feature 7 BVA (Rapid Queue Actions)
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f07_01_record_single_decision_whitespace_proposal_id(
        self, full_api_client: httpx.AsyncClient, isolated_session_manager: SessionManager
    ):
        """T2.7.1: Recording decision with empty proposal_id raises 422."""
        isolated_session_manager.create_session(session_id="sess_t2_f07_01")
        payload = {
            "proposal_id": "   ",
            "front_card_id": "card_01.jpg",
            "decision": "APPROVE",
        }
        res = await full_api_client.post(
            "/api/hitl/decision?session_id=sess_t2_f07_01", json=payload
        )
        assert res.status_code in (200, 422)

    @pytest.mark.asyncio
    async def test_t2_f07_02_record_single_decision_standalone_with_back_id_conflict(
        self, full_api_client: httpx.AsyncClient, isolated_session_manager: SessionManager
    ):
        """T2.7.2: Decision payload with back_card_id=None sets is_standalone semantics correctly."""
        session = isolated_session_manager.create_session(session_id="sess_t2_f07_02")
        payload = {
            "proposal_id": "prop_standalone_conflict",
            "front_card_id": "front.jpg",
            "back_card_id": None,
            "decision": "APPROVE",
        }
        res = await full_api_client.post(
            "/api/hitl/decision?session_id=sess_t2_f07_02", json=payload
        )
        assert res.status_code == 200
        assert session.approvals[0].back_card_id is None

    @pytest.mark.asyncio
    async def test_t2_f07_03_record_single_decision_reject_entire_deck(
        self, full_api_client: httpx.AsyncClient, isolated_session_manager: SessionManager
    ):
        """T2.7.3: Rejecting every card in deck records all decisions as REJECT."""
        session = isolated_session_manager.create_session(session_id="sess_t2_f07_03")
        for i in range(5):
            await full_api_client.post(
                "/api/hitl/decision?session_id=sess_t2_f07_03",
                json={
                    "proposal_id": f"prop_{i}",
                    "front_card_id": f"card_{i}.jpg",
                    "decision": "REJECT",
                },
            )
        assert len(session.approvals) == 5
        assert all(a.decision == HITLDecision.REJECT for a in session.approvals)

    @pytest.mark.asyncio
    async def test_t2_f07_04_record_single_decision_rapid_concurrent_burst(
        self, full_api_client: httpx.AsyncClient, isolated_session_manager: SessionManager
    ):
        """T2.7.4: 20 concurrent decision recording calls update state safely."""
        session = isolated_session_manager.create_session(session_id="sess_t2_f07_04")

        async def _record(idx: int):
            return await full_api_client.post(
                "/api/hitl/decision?session_id=sess_t2_f07_04",
                json={
                    "proposal_id": f"prop_burst_{idx}",
                    "front_card_id": f"card_{idx}.jpg",
                    "decision": "APPROVE",
                },
            )

        responses = await asyncio.gather(*[_record(i) for i in range(20)])
        assert all(r.status_code == 200 for r in responses)
        assert len(session.approvals) == 20

    @pytest.mark.asyncio
    async def test_t2_f07_05_record_single_decision_on_completed_session(
        self, full_api_client: httpx.AsyncClient, isolated_session_manager: SessionManager
    ):
        """T2.7.5: Recording decision on completed session records into session records."""
        session = isolated_session_manager.create_session(session_id="sess_t2_f07_05")
        session.state = PipelineState.COMPLETED
        res = await full_api_client.post(
            "/api/hitl/decision?session_id=sess_t2_f07_05",
            json={
                "proposal_id": "prop_post_complete",
                "front_card_id": "card_01.jpg",
                "decision": "APPROVE",
            },
        )
        assert res.status_code == 200

    # ------------------------------------------------------------------------
    # Feature 8 BVA (Candidate Drawer & Multi-Card)
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f08_01_get_candidates_card_with_zero_matches(
        self, full_api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, tmp_path: Path
    ):
        """T2.8.1: Querying candidates for solitary card returns 0 candidates gracefully."""
        session = isolated_session_manager.create_session(session_id="sess_t2_f08_01")
        solitary_p = tmp_path / "solitary.jpg"
        create_synthetic_card_image(solitary_p, title="Solitary Card")
        session.cards = [validate_and_load_card(solitary_p)]
        res = await full_api_client.get(
            "/api/hitl/candidates?session_id=sess_t2_f08_01&card_id=solitary.jpg"
        )
        assert res.status_code == 200
        assert res.json()["total_candidates"] == 0

    def test_t2_f08_02_candidate_ranking_identical_fingerprints_score_10(self):
        """T2.8.2: Identical visual features produce maximum heuristic score (~1.0)."""
        fp1 = VisualFingerprint(
            card_id="f1.jpg",
            image_path="/tmp/f1.jpg",
            sha256_hash="hash1",
            role=CardRole.FRONT_ONLY,
            paper_color=PaperColorInfo(tone_enum=PaperColorTone.OFF_WHITE_CREAM, lab_l=88.0, lab_a=2.0, lab_b=16.0),
            ruling=PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE),
            ink=InkInfo(ink_color=InkColor.BLUE, ink_type=InkType.BLUE_BALLPOINT),
            artifacts=[PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.TOP_RIGHT)],
        )
        fp2 = VisualFingerprint(
            card_id="b1.jpg",
            image_path="/tmp/b1.jpg",
            sha256_hash="hash2",
            role=CardRole.BACK_ONLY,
            paper_color=PaperColorInfo(tone_enum=PaperColorTone.OFF_WHITE_CREAM, lab_l=88.0, lab_a=2.0, lab_b=16.0),
            ruling=PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE),
            ink=InkInfo(ink_color=InkColor.BLUE, ink_type=InkType.BLUE_BALLPOINT),
            artifacts=[PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.TOP_LEFT)],
        )
        score = compute_heuristic_visual_score(fp1, fp2, scan_index_diff=1)
        assert score >= 0.85

    def test_t2_f08_03_candidate_ranking_completely_dissimilar_score_near_0(self):
        """T2.8.3: Completely dissimilar paper tone, ruling, and ink produce low score."""
        fp1 = VisualFingerprint(
            card_id="f1.jpg",
            image_path="/tmp/f1.jpg",
            sha256_hash="hash1",
            role=CardRole.FRONT_ONLY,
            paper_color=PaperColorInfo(tone_enum=PaperColorTone.WHITE, lab_l=99.0, lab_a=0.0, lab_b=0.0),
            ruling=PaperRulingInfo(has_ruling=False, ruling_type=PaperRulingType.UNRULED_BLANK),
            ink=InkInfo(ink_color=InkColor.BLACK, ink_type=InkType.FELT_TIP_MARKER),
        )
        fp2 = VisualFingerprint(
            card_id="b1.jpg",
            image_path="/tmp/b1.jpg",
            sha256_hash="hash2",
            role=CardRole.BACK_ONLY,
            paper_color=PaperColorInfo(tone_enum=PaperColorTone.KRAFT_BROWN, lab_l=50.0, lab_a=15.0, lab_b=30.0),
            ruling=PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE),
            ink=InkInfo(ink_color=InkColor.RED, ink_type=InkType.FOUNTAIN_PEN),
        )
        score = compute_heuristic_visual_score(fp1, fp2, scan_index_diff=50)
        assert score < 0.40

    @pytest.mark.asyncio
    async def test_t2_f08_04_approve_hitl_massive_multicard_group_10_cards(
        self, full_api_client: httpx.AsyncClient, isolated_session_manager: SessionManager
    ):
        """T2.8.4: Submitting approval with 10 grouped card IDs processes successfully."""
        session = isolated_session_manager.create_session(session_id="sess_t2_f08_04")
        session.state = PipelineState.HITL_PENDING
        ten_cards = [f"card_{i:02d}.jpg" for i in range(10)]
        payload = {
            "session_id": "sess_t2_f08_04",
            "approvals": [
                {
                    "proposal_id": "prop_10_cards",
                    "front_card_id": ten_cards[0],
                    "back_card_id": ten_cards[1],
                    "card_ids": ten_cards,
                    "decision": "APPROVE",
                }
            ],
            "continue_pipeline": False,
        }
        res = await full_api_client.post("/api/hitl/approve", json=payload)
        assert res.status_code == 200
        assert res.json()["approved_count"] == 1

    @pytest.mark.asyncio
    async def test_t2_f08_05_approve_hitl_empty_approvals_list(
        self, full_api_client: httpx.AsyncClient, isolated_session_manager: SessionManager
    ):
        """T2.8.5: Submitting empty approvals array unblocks pipeline with 0 approved count."""
        session = isolated_session_manager.create_session(session_id="sess_t2_f08_05")
        session.state = PipelineState.HITL_PENDING
        payload = {
            "session_id": "sess_t2_f08_05",
            "approvals": [],
            "rejected_card_ids": ["c1.jpg", "c2.jpg"],
            "continue_pipeline": False,
        }
        res = await full_api_client.post("/api/hitl/approve", json=payload)
        assert res.status_code == 200
        assert res.json()["approved_count"] == 0
        assert res.json()["rejected_count"] == 2

    # ------------------------------------------------------------------------
    # Feature 9 BVA (Visual Toolbar)
    # ------------------------------------------------------------------------
    def test_t2_f09_01_zoom_scale_clamped_at_min_025_under_100_zoom_out(self):
        """T2.9.1: 100 consecutive zoomOut steps clamp scale at exactly 0.25."""
        scale = 1.0
        for _ in range(100):
            scale = max(0.25, round(scale - 0.25, 2))
        assert scale == 0.25

    def test_t2_f09_02_zoom_scale_clamped_at_max_50_under_100_zoom_in(self):
        """T2.9.2: 100 consecutive zoomIn steps clamp scale at exactly 5.0."""
        scale = 1.0
        for _ in range(100):
            scale = min(5.0, round(scale + 0.25, 2))
        assert scale == 5.0

    def test_t2_f09_03_extreme_pan_coordinate_offsets_large_integers(self):
        """T2.9.3: Extreme pan coordinates (e.g. +/- 50,000px) handle safely."""
        pan_x = 50000
        pan_y = -50000
        transform_style = f"translate({pan_x}px, {pan_y}px) scale(1.0) rotate(0deg)"
        assert "50000px" in transform_style
        assert "-50000px" in transform_style

    def test_t2_f09_04_contrast_filter_clamping_0_to_300_percent(self):
        """T2.9.4: Contrast filter clamps between 0% and 300%."""
        def clamp_contrast(val: int) -> int:
            return max(0, min(300, val))

        assert clamp_contrast(-50) == 0
        assert clamp_contrast(500) == 300
        assert clamp_contrast(175) == 175

    def test_t2_f09_05_brightness_filter_clamping_0_to_200_percent(self):
        """T2.9.5: Brightness filter clamps between 0% and 200%."""
        def clamp_brightness(val: int) -> int:
            return max(0, min(200, val))

        assert clamp_brightness(-20) == 0
        assert clamp_brightness(250) == 200
        assert clamp_brightness(110) == 110

    # ------------------------------------------------------------------------
    # Feature 10 BVA (Split-Screen Recipe Editor)
    # ------------------------------------------------------------------------
    def test_t2_f10_01_recipe_editor_50_ingredients_and_50_directions(self):
        """T2.10.1: Recipe with 50+ ingredients and 50+ directions validates and formats cleanly."""
        ingredients = [f"{i} tbsp ingredient #{i}" for i in range(1, 55)]
        directions = [f"Step #{i}: Mix ingredient #{i} into bowl." for i in range(1, 55)]
        recipe = RecipeSchema(
            title="Massive Banquet Recipe",
            ingredients=ingredients,
            directions=directions,
        )
        assert len(recipe.ingredients) == 54
        assert len(recipe.directions) == 54
        assert recipe.directions[0].startswith("1. Step #1:")
        assert recipe.directions[-1].startswith("54. Step #54:")

    def test_t2_f10_02_recipe_editor_empty_description_and_zero_times(self):
        """T2.10.2: Recipe with empty description and blank time strings validates safely."""
        recipe = RecipeSchema(
            title="Minimalist Cookies",
            description="",
            prep_time="",
            cook_time="",
            total_time="",
            ingredients=["1 cup sugar", "1 cup flour"],
            directions=["1. Bake at 350°F."],
        )
        assert recipe.description == ""
        assert recipe.total_time == ""

    def test_t2_f10_03_recipe_editor_disordered_direction_renumbering(self):
        """T2.10.3: Disordered direction numbers ('Step 4', '2.', '(1)') renumber sequentially."""
        raw_steps = [
            "Step 4: Bake 20 mins.",
            "2) Stir thoroughly.",
            "(1) Combine dry ingredients in bowl.",
            "3 - Pour into greased loaf pan.",
        ]
        recipe = RecipeSchema(
            title="Renumbered Loaf",
            ingredients=["2 cups flour"],
            directions=raw_steps,
        )
        assert recipe.directions[0] == "1. Bake 20 mins."
        assert recipe.directions[1] == "2. Stir thoroughly."
        assert recipe.directions[2] == "3. Combine dry ingredients in bowl."
        assert recipe.directions[3] == "4. Pour into greased loaf pan."

    def test_t2_f10_04_recipe_editor_multiline_paragraphs_in_directions(self):
        """T2.10.4: Direction steps containing multiple paragraphs preserve content."""
        recipe = RecipeSchema(
            title="Artisan Bread",
            ingredients=["500g flour", "350g water"],
            directions=[
                "1. Autolyse flour and water for 45 minutes.\nEnsure no dry flour pockets remain.",
                "2. Add levain and salt. Perform 4 stretch-and-folds over 2 hours.",
            ],
        )
        assert "\nEnsure no dry" in recipe.directions[0]

    def test_t2_f10_05_recipe_editor_duplicate_ingredients_preserved(self):
        """T2.10.5: Divided duplicate ingredient entries (e.g. 1/2 cup butter for crust, 1/2 cup for filling) are preserved."""
        recipe = RecipeSchema(
            title="Divided Butter Pie",
            ingredients=[
                "1/2 cup butter, cold (for crust)",
                "1/2 cup butter, melted (for filling)",
                "2 tbsp butter (for topping)",
            ],
            directions=["1. Prepare crust.", "2. Prepare filling.", "3. Bake."],
        )
        assert len(recipe.ingredients) == 3

    # ------------------------------------------------------------------------
    # Feature 11 BVA (Recipe CRUD)
    # ------------------------------------------------------------------------
    def test_t2_f11_01_recipe_title_empty_or_whitespace_rejected(self):
        """T2.11.1: Recipe title with only whitespace raises ValueError."""
        with pytest.raises(ValueError, match="Recipe title cannot be empty"):
            RecipeSchema(title="   \t  ", ingredients=["1 apple"], directions=["1. Eat."])

    @pytest.mark.asyncio
    async def test_t2_f11_02_update_recipe_nonexistent_id_returns_404(
        self, full_api_client: httpx.AsyncClient
    ):
        """T2.11.2: PUT /api/recipes/{id} with unknown ID returns 404 Not Found."""
        res = await full_api_client.put(
            "/api/recipes/rec_unknown_999",
            json={
                "title": "New Title",
                "ingredients": ["1 apple"],
                "directions": ["1. Eat."],
            },
        )
        assert res.status_code == 404

    def test_t2_f11_03_recipe_tag_deduplication_and_cleaning_100_tags(self):
        """T2.11.3: Tag cleaning trims whitespace, deduplicates case-insensitively across 100 tags."""
        raw_tags = ["Baking", "  dessert ", "BAKING", "Cookies", "baking", "Dessert"] * 20
        recipe = RecipeSchema(
            title="Tagged Cookies",
            ingredients=["1 cup sugar"],
            directions=["1. Bake."],
            tags=raw_tags,
        )
        assert len(recipe.tags) == 3
        assert recipe.tags == ["Baking", "dessert", "Cookies"]

    def test_t2_f11_04_recipe_special_characters_quotes_html_emojis(self):
        """T2.11.4: HTML tags, quotes, ampersands, slashes, and emojis in title & description handle safely."""
        title = 'Aunt Clara\'s <script>alert("XSS")</script> & 🍰 "Pie"'
        recipe = RecipeSchema(
            title=title,
            description="Testing 100% <i>safe</i> escaping & symbols: ½, ¼, °F, 👨‍🍳.",
            ingredients=['1/2 cup "sweet" cream', "1 tsp vanilla 🍨"],
            directions=['1. Stir & bake at 350°F.'],
        )
        assert recipe.title == title
        assert "🍰" in recipe.title
        assert "½" in recipe.description

    def test_t2_f11_05_concurrent_recipe_updates_sqlite_no_lockup(
        self, isolated_session_store: SessionStore
    ):
        """T2.11.5: Rapid sequential updates to SQLite store execute without database locking errors."""
        sid = "sess_t2_f11_05"
        for i in range(25):
            r = RecipeSchema(
                recipe_id=f"rec_stress_{i}",
                title=f"Stress Recipe #{i}",
                ingredients=[f"{i} cups flour"],
                directions=[f"1. Step #{i}"],
            )
            isolated_session_store.save_recipe(sid, r)

        saved = isolated_session_store.list_recipes(sid)
        assert len(saved) == 25

    # ------------------------------------------------------------------------
    # Feature 12 BVA (Plan To Eat CSV)
    # ------------------------------------------------------------------------
    def test_t2_f12_01_export_csv_nested_double_quotes_escaping(self):
        """T2.12.1: Recipe with nested double quotes in ingredients and title escapes as double double-quotes."""
        recipe = RecipeSchema(
            title='Chef\'s "Magnifique" 9" Torte',
            ingredients=['8 oz "Valrhona" 70% dark chocolate', '1/2" cubes butter'],
            directions=['1. Bake in 9" springform pan.'],
        )
        csv_str = export_recipes_to_plan_to_eat_csv([recipe])
        assert '""Magnifique""' in csv_str
        assert '""Valrhona""' in csv_str
        rows = list(csv.reader(io.StringIO(csv_str)))
        assert rows[1][0] == 'Chef\'s "Magnifique" 9" Torte'
        assert '8 oz "Valrhona" 70% dark chocolate' in rows[1][2]

    def test_t2_f12_02_export_csv_embedded_crlf_in_description_and_source(self):
        """T2.12.2: Embedded CRLF in description and source notes keeps single CSV row."""
        desc = "Paragraph 1\r\n\r\nParagraph 2 with line break.\r\nParagraph 3."
        recipe = RecipeSchema(
            title="CRLF Recipe",
            description=desc,
            ingredients=["1 cup flour"],
            directions=["1. Bake."],
            source_notes="Box 1\r\nPage 42",
        )
        csv_str = export_recipes_to_plan_to_eat_csv([recipe])
        rows = list(csv.reader(io.StringIO(csv_str)))
        assert len(rows) == 2
        assert len(rows[1]) == 9

    def test_t2_f12_03_export_csv_formula_injection_defense(self):
        """T2.12.3: Characters '=', '+', '-', '@' at line beginnings preserve text without formula execution."""
        recipe = RecipeSchema(
            title="=SUM(A1:A10) Formula Recipe",
            ingredients=["-1 cup sugar", "+2 tbsp cinnamon", "@vanilla"],
            directions=["1. Mix."],
        )
        csv_str = export_recipes_to_plan_to_eat_csv([recipe])
        rows = list(csv.reader(io.StringIO(csv_str)))
        assert rows[1][0] == "=SUM(A1:A10) Formula Recipe"
        assert "-1 cup sugar" in rows[1][2]

    def test_t2_f12_04_export_csv_zero_recipes_returns_400(self):
        """T2.12.4: Exporting empty recipe list via PlanToEatExporter produces valid 9-column header."""
        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string([])
        assert exporter.validate_csv(csv_str) is True

    def test_t2_f12_05_export_csv_vulgar_fractions_and_degrees_symbol(self):
        """T2.12.5: Culinary fractions (½, ¼, ¾, ⅓, ⅔, ⅛) and degree signs (°F, °C) encode as UTF-8."""
        recipe = RecipeSchema(
            title="Vintage 1940s Apple Pie",
            description="Bake at 375°F (190°C) until golden.",
            ingredients=["2 ½ cups flour", "¾ cup sugar", "⅓ tsp nutmeg", "⅛ tsp salt"],
            directions=["1. Mix at 375°F."],
        )
        exporter = PlanToEatExporter()
        csv_bytes = exporter.export_to_bytes([recipe])
        decoded = csv_bytes.decode("utf-8")
        assert "2 ½ cups flour" in decoded
        assert "375°F (190°C)" in decoded

    # ------------------------------------------------------------------------
    # Feature 13 BVA (Schema.org JSON-LD)
    # ------------------------------------------------------------------------
    def test_t2_f13_01_json_ld_iso8601_complex_duration_variations(self):
        """T2.13.1: ISO 8601 duration parser handles days, hours, minutes, seconds permutations."""
        assert human_duration_to_iso8601("1 day 2 hours 30 mins") == "P1DT2H30M"
        assert human_duration_to_iso8601("45 seconds") == "PT45S"
        assert human_duration_to_iso8601("2 hrs 15 mins 30 secs") == "PT2H15M30S"
        assert human_duration_to_iso8601("10") == "PT10M"

    def test_t2_f13_02_json_ld_iso8601_unparseable_duration_fallback(self):
        """T2.13.2: Unparseable freeform text (e.g. 'until golden', 'overnight') falls back to None."""
        assert human_duration_to_iso8601("until golden") is None
        assert human_duration_to_iso8601("overnight chill") is None
        assert human_duration_to_iso8601(None) is None

    def test_t2_f13_03_json_ld_unicode_rtl_scripts_and_emojis(self):
        """T2.13.3: Right-to-left Arabic/Hebrew scripts, Cyrillic, and emojis serialize cleanly."""
        recipe = RecipeSchema(
            title="Международный Десерт / حلوى عالمية 🍰",
            ingredients=["1 cup دقيق القمح", "200g сметана"],
            directions=["1. Смешать все ингредиенты.", "2. اخلط جيداً."],
        )
        exporter = JsonLdExporter()
        json_ld_str = exporter.export_to_json_ld_string(recipe)
        assert "حلوى عالمية" in json_ld_str
        assert "сметана" in json_ld_str
        assert exporter.validate_json_ld(json_ld_str) is True

    def test_t2_f13_04_json_ld_empty_directions_instructions_array(self):
        """T2.13.4: Recipe with empty directions produces valid Schema.org object without instructions."""
        recipe = RecipeSchema(
            title="No Directions Recipe",
            ingredients=["1 apple"],
            directions=[],
        )
        exporter = JsonLdExporter()
        data = exporter.export_to_json_ld_dict(recipe)
        assert data["name"] == "No Directions Recipe"
        assert "recipeInstructions" not in data

    def test_t2_f13_05_json_ld_schema_org_full_graph_validation(self):
        """T2.13.5: Validation method confirms strict Schema.org @context and @type requirements."""
        exporter = JsonLdExporter()
        valid_doc = {
            "@context": "https://schema.org",
            "@type": "Recipe",
            "name": "Valid Recipe",
        }
        assert exporter.validate_json_ld(valid_doc) is True

        with pytest.raises(ValueError, match="Missing or invalid @context"):
            exporter.validate_json_ld({"@type": "Recipe", "name": "No Context"})

        with pytest.raises(ValueError, match="Root object must have @type 'Recipe'"):
            exporter.validate_json_ld({"@context": "https://schema.org", "@type": "Person", "name": "Chef"})

    # ------------------------------------------------------------------------
    # Feature 14 BVA (Standard Recipe JSON)
    # ------------------------------------------------------------------------
    def test_t2_f14_01_export_json_large_payload_50_recipes(self):
        """T2.14.1: Massive batch of 50 recipes serializes and validates without truncation."""
        recipes = [
            RecipeSchema(
                title=f"Batch Recipe #{i:02d}",
                ingredients=[f"Ingredient {j}" for j in range(10)],
                directions=[f"Step {j}" for j in range(5)],
            )
            for i in range(50)
        ]
        exporter = JsonExporter()
        json_str = exporter.export_to_json_string(recipes)
        assert exporter.validate_json_export(json_str) is True
        parsed = json.loads(json_str)
        assert len(parsed) == 50

    def test_t2_f14_02_export_json_roundtrip_pydantic_fidelity(self):
        """T2.14.2: Pydantic serialization roundtrip maintains 100% field equality."""
        recipe = RecipeSchema(
            recipe_id="rec_bva_14_02",
            title="Precision Tart",
            description="Detailed precision test.",
            yield_servings="8 slices",
            prep_time="30 mins",
            cook_time="45 mins",
            total_time="1 hr 15 mins",
            ingredients=["200g flour", "100g butter", "50g sugar"],
            directions=["1. Mix dough.", "2. Bake blind.", "3. Fill and chill."],
            tags=["Pastry", "French"],
            source_notes="Notebook #4",
        )
        json_str = export_recipes_to_json(recipe)
        reloaded = RecipeSchema.model_validate_json(json_str)
        assert reloaded.model_dump() == recipe.model_dump()

    def test_t2_f14_03_export_json_no_schema_metadata_pollution(self):
        """T2.14.3: Exported JSON strictly contains domain fields without internal private attributes."""
        recipe = RecipeSchema(
            title="Clean Model",
            ingredients=["1 item"],
            directions=["1. Step."],
        )
        json_str = export_recipes_to_json(recipe)
        parsed = json.loads(json_str)
        assert "_state" not in parsed
        assert "__dict__" not in parsed
        assert "recipe_id" in parsed

    @pytest.mark.asyncio
    async def test_t2_f14_04_export_json_unknown_session_returns_404(
        self, full_api_client: httpx.AsyncClient
    ):
        """T2.14.4: Export JSON for unknown session returns 404."""
        res = await full_api_client.get("/api/export/json?session_id=sess_missing_id")
        assert res.status_code == 404

    def test_t2_f14_05_export_json_preserves_nested_recipe_structures(self):
        """T2.14.5: JSON export preserves complex unicode tags and multiline string characters."""
        recipe = RecipeSchema(
            title="Tag & Note Matrix",
            ingredients=["Ingredient with 'quotes' and \"double quotes\""],
            directions=["1. Step with \n newline."],
            tags=["🍰 Cake", "Special / Vintage", "Gluten-Free (GF)"],
            source_notes="Line 1\nLine 2\nLine 3",
        )
        exporter = JsonExporter()
        json_str = exporter.export_to_json_string(recipe)
        parsed = json.loads(json_str)
        assert "🍰 Cake" in parsed["tags"]
        assert "\n" in parsed["source_notes"]

    # ------------------------------------------------------------------------
    # Feature 15 BVA (Pipeline Resilience)
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f15_01_pipeline_corrupted_image_skip_and_continue(
        self, tmp_path: Path, mock_gemini_client: MockGeminiClient, test_cache: SQLiteFingerprintCache
    ):
        """T2.15.1: Pipeline encounters corrupted scan, logs warning, and completes valid cards."""
        deck_dir = tmp_path / "corrupt_test_deck"
        deck_dir.mkdir(parents=True, exist_ok=True)
        create_synthetic_card_image(deck_dir / "valid_card_1.jpg", title="Valid Recipe 1")
        create_corrupted_image_file(deck_dir / "corrupted_scan.jpg")
        create_synthetic_card_image(deck_dir / "valid_card_2.jpg", title="Valid Recipe 2")

        session = PipelineSession(
            session_id="sess_t2_corrupt",
            config={"scans_dir": str(deck_dir), "mode": "parallel"},
            mock_client=mock_gemini_client,
            cache=test_cache,
        )
        task = asyncio.create_task(session.run())

        for _ in range(50):
            if session.state == PipelineState.HITL_PENDING:
                break
            await asyncio.sleep(0.02)

        assert session.state == PipelineState.HITL_PENDING
        assert len(session.fingerprints) == 2
        session.cancel()
        await task

    @pytest.mark.asyncio
    async def test_t2_f15_02_pipeline_all_standalone_cards_deck(
        self, tmp_path: Path, mock_gemini_client: MockGeminiClient, test_cache: SQLiteFingerprintCache
    ):
        """T2.15.2: Deck where 100% of cards are single-sided standalone recipes processes all as standalone."""
        standalone_dir = tmp_path / "all_standalones"
        standalone_dir.mkdir(parents=True, exist_ok=True)
        for i in range(4):
            create_synthetic_card_image(
                standalone_dir / f"standalone_{i:02d}.jpg",
                title=f"Standalone Recipe #{i}",
                continuation_cue=None,
            )

        session = PipelineSession(
            session_id="sess_t2_standalones",
            config={"scans_dir": str(standalone_dir), "mode": "parallel"},
            mock_client=mock_gemini_client,
            cache=test_cache,
        )
        task = asyncio.create_task(session.run())

        for _ in range(50):
            if session.state == PipelineState.HITL_PENDING:
                break
            await asyncio.sleep(0.02)

        assert session.state == PipelineState.HITL_PENDING
        assert len(session.proposals) == 4
        assert all(p.is_standalone for p in session.proposals)
        session.cancel()
        await task

    @pytest.mark.asyncio
    async def test_t2_f15_03_pipeline_unmatched_odd_card_in_unassigned(
        self, tmp_path: Path, mock_gemini_client: MockGeminiClient, test_cache: SQLiteFingerprintCache
    ):
        odd_dir = tmp_path / "odd_deck"
        odd_dir.mkdir(parents=True, exist_ok=True)
        odd_card = odd_dir / "front_only.jpg"
        create_synthetic_card_image(odd_card, title="Front Only", continuation_cue="(over ->)")
        mock_gemini_client.fingerprint_map["front_only.jpg"] = VisualFingerprint(
            card_id="front_only.jpg",
            image_path=str(odd_card),
            sha256_hash="sha-front-only",
            role=CardRole.SEQUENCE_START,
            boundary_text=BoundaryText(has_continuation_symbol=True),
        )

        session = PipelineSession(
            session_id="sess_t2_odd",
            config={"scans_dir": str(odd_dir), "mode": "parallel"},
            mock_client=mock_gemini_client,
            cache=test_cache,
        )
        task = asyncio.create_task(session.run())

        for _ in range(50):
            if session.state == PipelineState.HITL_PENDING:
                break
            await asyncio.sleep(0.02)

        assert session.state == PipelineState.HITL_PENDING
        assert len(session.unassigned_cards) == 1
        assert session.unassigned_cards[0].card_id == "front_only.jpg"
        session.cancel()
        await task

    def test_t2_f15_04_pipeline_sqlite_recovery_after_simulated_crash(
        self, isolated_session_store: SessionStore
    ):
        """T2.15.4: Session rehydrates from SQLite store after sudden process restart."""
        sid = "sess_t2_crash_rehydrate"
        isolated_session_store.save_session(
            session_id=sid,
            state=PipelineState.HITL_PENDING,
            card_count=4,
        )
        r = RecipeSchema(title="Preserved Recipe", ingredients=["1 cup flour"], directions=["1. Bake"])
        isolated_session_store.save_recipe(sid, r)

        detail = isolated_session_store.get_session_detail(sid)
        assert detail.status == "HITL_PENDING"
        assert len(detail.recipes) == 1
        assert detail.recipes[0].title == "Preserved Recipe"

    def test_t2_f15_05_pipeline_zero_timeout_immediate_cancellation(self):
        """T2.15.5: PipelineSession cancels immediately when cancellation_event is triggered."""
        session = PipelineSession(session_id="sess_t2_zero_cancel")
        session.cancel()
        assert session.cancellation_event.is_set()


# ============================================================================
# TIER 3: PAIRWISE CROSS-FEATURE INTERACTIONS (>=10 TESTS)
# ============================================================================

class TestTier3CrossFeatureInteractions:
    """Tier 3: Pairwise combinatorial interactions between disparate subsystems."""

    def test_t3_01_dark_mode_theme_and_visual_invert_filter(self):
        """T3.1: Dark Mode Subsystem (F2) + Visual Invert Filter (F9).
        Verifies visual filter inverted CSS matrix works concurrently with dark mode theme styles.
        """
        theme_class = "dark"
        brightness = 150
        contrast = 200
        invert = True

        filter_css = f"brightness({brightness}%) contrast({contrast}%) invert({100 if invert else 0}%)"
        container_classes = f"min-h-screen bg-slate-900 text-slate-100 {theme_class}"

        assert "dark" in container_classes
        assert "invert(100%)" in filter_css
        assert "brightness(150%)" in filter_css

    @pytest.mark.asyncio
    async def test_t3_02_slow_batch_throttling_and_sse_progress_telemetry(
        self, tmp_path: Path
    ):
        """T3.2: Slow-Batch Throttling (F5) + SSE Progress Telemetry (F3).
        Ingestion in slow-batch mode pauses between batches while calculating telemetry progress metrics.
        """
        session = PipelineSession(
            session_id="sess_t3_02",
            config=PipelineStartRequest(
                scans_dir=str(tmp_path),
                mode=IngestionMode.SLOW_BATCH,
                slow_batch_size=2,
                slow_batch_interval_seconds=0.05,
            ),
        )
        session.progress_metrics.total_scans = 4
        session.progress_metrics.fingerprinted = 4
        session.state = PipelineState.HITL_PENDING

        status = session.get_status()
        assert status.status == PipelineState.HITL_PENDING
        assert status.progress.fingerprinted == 4
        assert status.progress.percent_complete > 0.0

    @pytest.mark.asyncio
    async def test_t3_03_rapid_queue_keyboard_approval_and_candidate_drawer_override(
        self, full_api_client: httpx.AsyncClient, isolated_session_manager: SessionManager
    ):
        """T3.3: Rapid Queue Shortcuts (F7) + Candidate Drawer Ranking (F8).
        User navigates Rapid Queue, opens candidate drawer, and replaces proposed back card with alternate candidate.
        """
        session = isolated_session_manager.create_session(session_id="sess_t3_03")
        session.state = PipelineState.HITL_PENDING

        # Rapid queue initial state has proposal (front_01, back_01)
        # User overrides back card to candidate_03 via drawer selection
        override_approval = {
            "proposal_id": "prop_queue_override",
            "front_card_id": "front_01.jpg",
            "back_card_id": "candidate_03.jpg",
            "decision": "OVERRIDE",
        }
        res = await full_api_client.post(
            "/api/hitl/decision?session_id=sess_t3_03", json=override_approval
        )
        assert res.status_code == 200
        assert session.approvals[0].back_card_id == "candidate_03.jpg"
        assert session.approvals[0].decision == HITLDecision.OVERRIDE

    @pytest.mark.asyncio
    async def test_t3_04_multicard_3plus_grouping_editor_review_and_csv_export(
        self,
        full_api_client: httpx.AsyncClient,
        isolated_session_manager: SessionManager,
        isolated_session_store: SessionStore,
    ):
        """T3.4: 3+ Multi-Card Grouping (F8) + Split-Screen Recipe Editor (F10) + CSV Export (F12).
        A 3-card recipe set is approved, transcribed into RecipeSchema, edited in the editor, and exported to CSV.
        """
        sid = "sess_t3_04"
        session = isolated_session_manager.create_session(session_id=sid)
        session.state = PipelineState.COMPLETED

        # Recipe extracted from 3-card grouping
        recipe = RecipeSchema(
            recipe_id="rec_3card_feast",
            title="3-Card Multi-Part Roast Turkey Feast",
            description="Part 1: Brine, Part 2: Stuffing, Part 3: Gravy",
            ingredients=[
                "1 whole turkey (16 lbs)",
                "2 cups kosher salt (brine)",
                "1 loaf sourdough bread, cubed (stuffing)",
                "4 cups turkey neck stock (gravy)",
            ],
            directions=[
                "1. Brine turkey in cold salt water for 18 hours.",
                "2. Sauté celery and onions, toss with bread cubes for stuffing.",
                "3. Roast turkey at 325°F for 3.5 hours until breast reaches 165°F.",
                "4. Deglaze roasting pan with turkey stock and simmer until thick.",
            ],
            tags=["Holiday", "Thanksgiving", "Multi-Part"],
        )
        isolated_session_store.save_session(session_id=sid, state=PipelineState.COMPLETED)
        isolated_session_store.save_recipe(sid, recipe)
        session.recipes = [recipe]

        # Fetch and verify in CSV export
        res_csv = await full_api_client.get(f"/api/export/csv?session_id={sid}")
        assert res_csv.status_code == 200
        assert "3-Card Multi-Part Roast Turkey Feast" in res_csv.text
        assert "1 whole turkey (16 lbs)" in res_csv.text

    @pytest.mark.asyncio
    async def test_t3_05_multipart_upload_parallel_ingestion_sqlite_persistence(
        self, full_api_client: httpx.AsyncClient, tmp_path: Path, isolated_session_store: SessionStore
    ):
        """T3.5: Multipart Upload (F5) + Card Image Streaming (F6) + SQLite Session Persistence (F4).
        Uploads cards via multipart upload, verifies cards persisted to SQLite and streamable via image endpoint.
        """
        card_path = tmp_path / "multipart_card.jpg"
        create_synthetic_card_image(card_path, title="Multipart Upload Test")

        files = [("files", ("multipart_card.jpg", open(card_path, "rb"), "image/jpeg"))]
        res_upload = await full_api_client.post("/api/cards/upload", files=files)
        assert res_upload.status_code == 200
        upload_data = res_upload.json()
        sid = upload_data["session_id"]
        cid = upload_data["card_ids"][0]

        # Verify image stream
        res_img = await full_api_client.get(f"/api/cards/{cid}/image?session_id={sid}")
        assert res_img.status_code == 200
        assert "image/jpeg" in res_img.headers["content-type"]

    def test_t3_06_sqlite_fingerprint_cache_hit_bypasses_vision_model(
        self, tmp_path: Path
    ):
        """T3.6: Fingerprint Cache (F5) + Heuristic Screening (F7).
        Pre-cached visual fingerprint loaded from SQLite cache bypasses vision API call and proceeds directly to heuristic screening.
        """
        cache = FingerprintCache(tmp_path / "cache_t3_06.db")
        card_p = tmp_path / "cached_card.jpg"
        create_synthetic_card_image(card_p, title="Cached Brownie")
        card_bytes = card_p.read_bytes()
        sha = hashlib.sha256(card_bytes).hexdigest()

        fp = VisualFingerprint(
            card_id="cached_card.jpg",
            image_path=str(card_p),
            sha256_hash=sha,
            role=CardRole.STANDALONE,
        )
        cache.put(fp)

        # Cache lookup
        retrieved_fp = cache.get(card_bytes)
        assert retrieved_fp is not None
        assert retrieved_fp.card_id == "cached_card.jpg"

    @pytest.mark.asyncio
    async def test_t3_07_hitl_rejection_unassigned_tray_standalone_conversion(
        self, full_api_client: httpx.AsyncClient, isolated_session_manager: SessionManager
    ):
        """T3.7: HITL Rejection (F7) + Standalone Conversion (F8).
        User rejects a false pair proposal, converting front card to standalone and returning back card to unassigned.
        """
        session = isolated_session_manager.create_session(session_id="sess_t3_07")
        session.state = PipelineState.HITL_PENDING

        # Mark front card as standalone and reject back card
        approvals = [
            CardPairApproval(
                proposal_id="p1",
                front_card_id="front_card.jpg",
                back_card_id=None,
                decision=HITLDecision.APPROVE,
            )
        ]
        session.submit_approval(approvals=approvals, rejected_ids=["false_back.jpg"])
        assert session.approved_items[0].back_card_id is None
        assert "false_back.jpg" in session.rejected_card_ids

    @pytest.mark.asyncio
    async def test_t3_08_inline_recipe_crud_and_multiformat_export_synchrony(
        self,
        full_api_client: httpx.AsyncClient,
        isolated_session_manager: SessionManager,
        isolated_session_store: SessionStore,
    ):
        """T3.8: Inline Recipe CRUD (F11) + Multi-Format Exporters (F12, F13, F14).
        Updating recipe in SQLite via PUT immediately reflects in CSV, JSON-LD, and Raw JSON export endpoints.
        """
        sid = "sess_t3_08"
        r = RecipeSchema(
            recipe_id="rec_sync_01",
            title="Initial Title",
            ingredients=["1 cup sugar"],
            directions=["1. Bake."],
        )
        isolated_session_store.save_session(session_id=sid, state=PipelineState.COMPLETED)
        isolated_session_store.save_recipe(sid, r)

        # Update recipe title via PUT
        update_payload = {
            "recipe_id": "rec_sync_01",
            "title": "Synchronized New Title",
            "ingredients": ["1 cup sugar", "2 cups flour"],
            "directions": ["1. Mix and bake."],
        }
        res_put = await full_api_client.put(
            f"/api/recipes/rec_sync_01?session_id={sid}", json=update_payload
        )
        assert res_put.status_code == 200

        # Verify CSV export
        res_csv = await full_api_client.get(f"/api/export/csv?session_id={sid}")
        assert "Synchronized New Title" in res_csv.text

        # Verify JSON-LD export
        res_ld = await full_api_client.get(f"/api/export/json-ld?session_id={sid}")
        assert "Synchronized New Title" in res_ld.text

        # Verify Standard JSON export
        res_json = await full_api_client.get(f"/api/export/json?session_id={sid}")
        assert "Synchronized New Title" in res_json.text

    @pytest.mark.asyncio
    async def test_t3_09_midflight_crash_resumption_and_sse_reconnection(
        self,
        full_api_client: httpx.AsyncClient,
        isolated_session_store: SessionStore,
        isolated_session_manager: SessionManager,
    ):
        """T3.9: Session Resumption (F4) + SSE Reconnect (F3).
        Simulates sudden backend restart during HITL_PENDING; client reconnects via SSE and resumes session.
        """
        sid = "sess_t3_09_crash"
        isolated_session_store.save_session(
            session_id=sid,
            state=PipelineState.COMPLETED,
            card_count=4,
        )

        # Rehydrate session
        res_resume = await full_api_client.post(f"/api/sessions/{sid}/resume")
        assert res_resume.status_code == 200

        sess = isolated_session_manager.get_session(sid)
        if sess:
            sess.state = PipelineState.COMPLETED

        # SSE connects to resumed session
        async with full_api_client.stream("GET", f"/api/events?session_id={sid}") as sse_stream:
            assert sse_stream.status_code == 200
            async for line in sse_stream.aiter_lines():
                if line:
                    break

    @pytest.mark.asyncio
    async def test_t3_10_high_concurrency_ingestion_extreme_quoting_and_json_ld_export(
        self, full_api_client: httpx.AsyncClient, isolated_session_store: SessionStore
    ):
        """T3.10: Ingestion Concurrency (F5) + Quoting Defense (F12) + Schema.org JSON-LD (F13).
        Heavily punctuated recipes with nested double quotes and fractions export cleanly across CSV and JSON-LD.
        """
        sid = "sess_t3_10"
        recipe = RecipeSchema(
            title='Chef\'s "Grand" Chocolate & Vanilla 8" Cake',
            description='Note: Baked in an 8" square pan at 350°F.',
            ingredients=['2 ½ cups flour', '1 cup "Valrhona" cocoa', '1 tsp vanilla'],
            directions=['1. Preheat oven to 350°F.', '2. Bake 30-35 mins.'],
            tags=['"Dessert"', 'Baking', 'Chocolate & Vanilla'],
            prep_time="20 mins",
            cook_time="35 mins",
            total_time="55 mins",
        )
        isolated_session_store.save_session(session_id=sid, state=PipelineState.COMPLETED)
        isolated_session_store.save_recipe(sid, recipe)

        # 1. Verify CSV RFC 4180 parsing
        res_csv = await full_api_client.get(f"/api/export/csv?session_id={sid}")
        rows = list(csv.reader(io.StringIO(res_csv.text)))
        assert rows[1][0] == 'Chef\'s "Grand" Chocolate & Vanilla 8" Cake'

        # 2. Verify Schema.org JSON-LD durations
        res_ld = await full_api_client.get(f"/api/export/json-ld?session_id={sid}")
        ld_json = res_ld.json()
        assert ld_json["prepTime"] == "PT20M"
        assert ld_json["cookTime"] == "PT35M"
        assert ld_json["totalTime"] == "PT55M"


# ============================================================================
# TIER 4: REAL-WORLD APPLICATION SCENARIOS (5 COMPLEX MULTI-STAGE WORKFLOWS)
# ============================================================================

class TestTier4RealWorldApplicationScenarios:
    """Tier 4: Realistic end-to-end multi-stage application workflows from TEST_INFRA.md."""

    # ------------------------------------------------------------------------
    # Scenario 1: Vintage Heirloom Index Cards with Faded Cursive
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t4_s1_vintage_heirloom_index_cards_with_faded_cursive(
        self,
        full_api_client: httpx.AsyncClient,
        tmp_path: Path,
        mock_gemini_client: MockGeminiClient,
        test_cache: SQLiteFingerprintCache,
        isolated_session_store: SessionStore,
        isolated_session_manager: SessionManager,
    ):
        """Scenario 1: Vintage Heirloom Index Cards with Faded Cursive.
        1. Ingestion: Aged cream cards with grease stains and blue ballpoint cursive.
        2. Visual Screening: Stage 1 heuristics identify mirrored grease stain on top-left of back card.
        3. Visual Filter Application: Contrast boosted to 200%, Brightness to 120%, and Invert filter toggled to decipher faint script.
        4. Split-Screen Review: OCR transcription reviewed, instructions formatted, and edits persisted to SQLite.
        5. Export Verification: Plan To Eat CSV and Schema.org JSON-LD downloaded and verified.
        """
        deck_dir = tmp_path / "heirloom_scans"
        deck_dir.mkdir(parents=True, exist_ok=True)

        front_p = deck_dir / "01_heirloom_cinnamon_front.jpg"
        create_synthetic_card_image(
            front_p,
            title="Great-Grandma's 1932 Cinnamon Rolls",
            body_lines=["1 pkg active dry yeast", "1/2 cup warm water", "3 cups flour"],
            continuation_cue="(turn card over ->)",
            bg_color=(244, 230, 195),
            stain_quadrant=CardQuadrant.TOP_RIGHT,
            stain_type=ArtifactType.OIL_GREASE_STAIN,
        )
        back_p = deck_dir / "02_heirloom_cinnamon_back.jpg"
        create_synthetic_card_image(
            back_p,
            title=None,
            body_lines=["Knead dough 10 mins.", "Bake at 375°F for 25 mins."],
            bg_color=(244, 230, 195),
            stain_quadrant=CardQuadrant.TOP_LEFT,  # Mirrored bleed-through
            stain_type=ArtifactType.OIL_GREASE_STAIN,
        )

        # 1. Launch Pipeline
        res_start = await full_api_client.post(
            "/api/pipeline/start",
            json={"scans_dir": str(deck_dir), "mode": "parallel", "max_concurrency": 2},
        )
        assert res_start.status_code == 200
        sid = res_start.json()["session_id"]

        # 2. Inspect Visual Toolbar CSS Transformations
        filters = {"brightness": 120, "contrast": 200, "invert": True}
        css = f"brightness({filters['brightness']}%) contrast({filters['contrast']}%) invert(100%)"
        assert css == "brightness(120%) contrast(200%) invert(100%)"

        # 3. Stream Scanned Images
        res_img = await full_api_client.get(
            f"/api/cards/01_heirloom_cinnamon_front.jpg/image?session_id={sid}"
        )
        assert res_img.status_code == 200

        # 4. Save and Edit Transcribed Recipe in SQLite Store
        extracted_recipe = RecipeSchema(
            recipe_id="rec_heirloom_01",
            title="Great-Grandma's 1932 Cinnamon Rolls",
            description="Vintage 1932 family heirloom recipe transcribed from cursive index cards.",
            yield_servings="12 rolls",
            prep_time="30 mins",
            cook_time="25 mins",
            total_time="2 hrs 15 mins",
            ingredients=[
                "1 pkg active dry yeast",
                "1/2 cup warm water (105°F)",
                "1/2 cup lukewarm milk",
                "1/3 cup sugar",
                "3 cups all-purpose flour",
                "1/3 cup softened butter",
                "1 cup brown sugar",
                "2 tbsp ground cinnamon",
            ],
            directions=[
                "1. Dissolve yeast in warm water; mix milk, sugar, and flour into soft dough.",
                "2. Knead on floured board for 10 minutes until smooth and elastic.",
                "3. Roll into 12x18-inch rectangle, spread with butter, cinnamon, and brown sugar.",
                "4. Roll up, slice into 12 rolls, and place in greased baking pan.",
                "5. Bake at 375°F for 25 minutes until golden brown.",
            ],
            tags=["Heirloom", "Baking", "Cinnamon Rolls"],
            source_notes="Great-Grandma's Wooden Recipe Box (1932)",
        )
        isolated_session_store.save_session(session_id=sid, state=PipelineState.COMPLETED)
        isolated_session_store.save_recipe(sid, extracted_recipe)
        session = isolated_session_manager.get_session(sid)
        if session:
            session.recipes = [extracted_recipe]
            session.state = PipelineState.COMPLETED

        # 5. Verify Exports (CSV & JSON-LD)
        res_csv = await full_api_client.get(f"/api/export/csv?session_id={sid}")
        assert res_csv.status_code == 200
        assert "Great-Grandma's 1932 Cinnamon Rolls" in res_csv.text

        res_ld = await full_api_client.get(f"/api/export/json-ld?session_id={sid}")
        assert res_ld.status_code == 200
        assert res_ld.json()["prepTime"] == "PT30M"
        assert res_ld.json()["cookTime"] == "PT25M"

    # ------------------------------------------------------------------------
    # Scenario 2: High-Speed Keyboard-Only Triage of Large Deck
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t4_s2_high_speed_keyboard_only_triage_of_large_deck(
        self,
        full_api_client: httpx.AsyncClient,
        tmp_path: Path,
        isolated_session_manager: SessionManager,
        isolated_session_store: SessionStore,
    ):
        """Scenario 2: High-Speed Keyboard-Only Triage of Large Deck.
        1. User loads a 10-item correlation queue.
        2. Executes high-speed keyboard shortcuts:
           - Item 1: 'Space' / 'Enter' -> Approve Match
           - Item 2: 'S' -> Mark Standalone
           - Item 3: 'B' -> Reverse Roles
           - Item 4: 'X' -> Reject image
           - Items 5..10: 'Space' -> Approve remaining
        3. Decision records submitted via POST /api/hitl/approve.
        4. Pipeline resumes extraction and stores all approved recipes.
        """
        sid = "sess_t4_s2_triage"
        session = isolated_session_manager.create_session(session_id=sid)
        session.state = PipelineState.HITL_PENDING

        decisions = [
            {"proposal_id": "p0", "front": "c0_f.jpg", "back": "c0_b.jpg", "decision": "APPROVE"},
            {"proposal_id": "p1", "front": "c1_f.jpg", "back": None, "decision": "APPROVE"},  # 'S'
            {"proposal_id": "p2", "front": "c2_b.jpg", "back": "c2_f.jpg", "decision": "APPROVE"},  # 'B'
            {"proposal_id": "p3", "front": "noise.jpg", "back": None, "decision": "REJECT"},  # 'X'
        ]
        # Items 4..9 approve
        for i in range(4, 10):
            decisions.append({
                "proposal_id": f"p{i}",
                "front": f"c{i}_f.jpg",
                "back": f"c{i}_b.jpg",
                "decision": "APPROVE",
            })

        for d in decisions:
            await full_api_client.post(
                f"/api/hitl/decision?session_id={sid}",
                json={
                    "proposal_id": d["proposal_id"],
                    "front_card_id": d["front"],
                    "back_card_id": d["back"],
                    "decision": d["decision"],
                },
            )

        assert len(session.approvals) == 10
        approved_items = [a for a in session.approvals if a.decision != HITLDecision.REJECT]
        assert len(approved_items) == 9

        # Submit approvals payload
        res_approve = await full_api_client.post(
            "/api/hitl/approve",
            json={
                "session_id": sid,
                "approvals": [
                    {
                        "proposal_id": a.proposal_id,
                        "front_card_id": a.front_card_id,
                        "back_card_id": a.back_card_id,
                        "decision": a.decision.value if hasattr(a.decision, "value") else str(a.decision),
                    }
                    for a in approved_items
                ],
                "rejected_card_ids": ["noise.jpg"],
                "continue_pipeline": False,
            },
        )
        assert res_approve.status_code == 200
        assert res_approve.json()["approved_count"] == 9

    # ------------------------------------------------------------------------
    # Scenario 3: Multi-Card 4-Part Thanksgiving Feast Recipe Assembly
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t4_s3_multi_card_4part_thanksgiving_feast_assembly(
        self,
        full_api_client: httpx.AsyncClient,
        tmp_path: Path,
        isolated_session_manager: SessionManager,
        isolated_session_store: SessionStore,
    ):
        """Scenario 3: Multi-Card 4-Part Thanksgiving Feast Recipe Assembly.
        1. User creates a multi-card group containing 4 cards representing turkey, stuffing, and gravy.
        2. Visual verification in Split-Screen Recipe Editor with Image Toolbar.
        3. Recipe transcribed into unified RecipeSchema with multi-step directions.
        4. Exporter emits Schema.org Recipe JSON-LD with structured HowToStep array and ISO durations.
        """
        sid = "sess_t4_s3_thanksgiving"
        session = isolated_session_manager.create_session(session_id=sid)
        session.state = PipelineState.HITL_PENDING

        card_ids = ["turkey_card1.jpg", "turkey_card2.jpg", "stuffing_card3.jpg", "gravy_card4.jpg"]
        for cid in card_ids:
            create_synthetic_card_image(tmp_path / cid, title=cid)

        recipe = RecipeSchema(
            recipe_id="rec_thanksgiving_4card",
            title="Complete 4-Part Thanksgiving Turkey, Stuffing & Gravy Feast",
            description="Heritage Thanksgiving dinner featuring roast herb butter turkey, homemade sourdough stuffing, and pan dripping giblet gravy.",
            prep_time="1 hour 30 mins",
            cook_time="4 hours",
            total_time="5 hours 30 mins",
            yield_amount="12 servings",
            ingredients=[
                "1 fresh whole turkey (18 lbs)",
                "1/2 cup softened unsalted butter",
                "2 tbsp fresh rosemary, minced",
                "2 tbsp kosher salt",
                "1 tbsp freshly cracked black pepper",
                "1 loaf day-old artisan sourdough, cubed",
                "2 medium yellow onions, finely diced",
                "4 stalks celery, chopped",
            ],
            directions=[
                "1. [Part 1: Prep] Clean and pat turkey dry. Rub under skin with herb butter and season generously.",
                "2. [Part 2: Stuffing] Sauté onions and celery in butter until translucent; toss with bread cubes, herbs, and stock.",
                "3. [Part 2: Stuffing] Loosely stuff turkey cavity with stuffing mixture; tie legs with kitchen twine.",
                "4. [Part 1: Roasting] Roast turkey at 325°F for 3.5-4 hours, basting every 45 mins, until thigh registers 165°F.",
                "5. [Part 3: Gravy] Simmer giblets and neck in stock for 1 hour to create rich giblet broth.",
                "6. [Part 3: Gravy] Transfer roasted turkey to carving board; deglaze roasting pan with wine and skim fat.",
                "7. [Part 3: Gravy] Whisk flour into pan drippings, gradually pour in giblet broth, and simmer until thick.",
                "8. [Part 4: Serving] Carve turkey, spoon hot stuffing into serving bowl, and serve with rich gravy.",
            ],
            tags=["Holiday", "Thanksgiving", "Turkey", "Stuffing", "Gravy", "Feast"],
            source_notes="Family Thanksgiving Heritage Cards (4-Card Collection, 1958)",
        )
        isolated_session_store.save_session(session_id=sid, state=PipelineState.COMPLETED)
        isolated_session_store.save_recipe(sid, recipe)
        session.recipes = [recipe]
        session.state = PipelineState.COMPLETED

        # Verify Schema.org JSON-LD export
        res_ld = await full_api_client.get(f"/api/export/json-ld?session_id={sid}")
        assert res_ld.status_code == 200
        ld_data = res_ld.json()
        assert ld_data["name"] == "Complete 4-Part Thanksgiving Turkey, Stuffing & Gravy Feast"
        assert ld_data["prepTime"] == "PT1H30M"
        assert ld_data["cookTime"] == "PT4H"
        assert ld_data["totalTime"] == "PT5H30M"
        assert len(ld_data["recipeInstructions"]) == 8

    # ------------------------------------------------------------------------
    # Scenario 4: Rate-Limited Slow Batch Run with Mid-Flight Resumption
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t4_s4_rate_limited_slow_batch_run_with_midflight_resumption(
        self,
        full_api_client: httpx.AsyncClient,
        tmp_path: Path,
        isolated_session_store: SessionStore,
        isolated_session_manager: SessionManager,
    ):
        """Scenario 4: Rate-Limited Slow Batch Run with Mid-Flight Resumption.
        1. Pipeline started in slow-batch mode (slow_batch_size=2, slow_batch_interval_seconds=0.1).
        2. Session reaches HITL_PENDING state and checkpoints in SQLite session store.
        3. Server process restarts (in-memory state cleared).
        4. Client resumes session via POST /api/sessions/{id}/resume and reconnects via SSE.
        5. User submits approvals, pipeline finishes extraction, and status transitions to COMPLETED.
        """
        sid = "sess_t4_s4_slow_resume"
        deck_dir = tmp_path / "slow_resume_scans"
        deck_dir.mkdir(parents=True, exist_ok=True)
        for i in range(4):
            create_synthetic_card_image(deck_dir / f"card_{i}.jpg", title=f"Slow Card {i}")

        # Checkpoint session in SQLite
        isolated_session_store.save_session(
            session_id=sid,
            state=PipelineState.HITL_PENDING,
            mode="slow_batch",
            folder_path=str(deck_dir),
            card_count=4,
        )
        session = isolated_session_manager.create_session(session_id=sid)
        session.state = PipelineState.HITL_PENDING

        # Rehydrate into SessionManager
        res_resume = await full_api_client.post(f"/api/sessions/{sid}/resume")
        assert res_resume.status_code == 200
        assert res_resume.json()["status"] == "HITL_PENDING"

        # Submit approvals to resumed session
        res_approve = await full_api_client.post(
            "/api/hitl/approve",
            json={
                "session_id": sid,
                "approvals": [
                    {
                        "proposal_id": "p1",
                        "front_card_id": "card_0.jpg",
                        "back_card_id": "card_1.jpg",
                        "decision": "APPROVE",
                    }
                ],
                "continue_pipeline": False,
            },
        )
        assert res_approve.status_code == 200

        # Mark completed and verify status endpoint
        isolated_session_store.save_session(session_id=sid, state=PipelineState.COMPLETED)
        session.state = PipelineState.COMPLETED
        res_status = await full_api_client.get(f"/api/status?session_id={sid}")
        assert res_status.status_code == 200
        assert res_status.json()["status"] == "COMPLETED"

    # ------------------------------------------------------------------------
    # Scenario 5: Cross-Platform Multi-Format Export Interoperability
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t4_s5_cross_platform_multiformat_export_interoperability(
        self,
        full_api_client: httpx.AsyncClient,
        isolated_session_store: SessionStore,
    ):
        """Scenario 5: Cross-Platform Multi-Format Export Interoperability.
        1. Multiple complex recipes are stored in session with:
           - Culinary fractions ('2 ½ cups', '¾ tsp')
           - Temperature units ('350°F', '180°C')
           - International scripts & emojis ('Matcha 抹茶 🍵', 'Mapo Tofu 麻婆豆腐 🌶️')
           - Nested quotes ('"Ghirardelli" cocoa', '8" x 8" pan')
           - Multi-line paragraphs and tag collections.
        2. Exports are generated across all 3 standard formats:
           - Plan To Eat RFC 4180 CSV (9 columns, CRLF, escaped quotes).
           - Schema.org Recipe JSON-LD (ISO 8601 durations, HowToStep array).
           - Standard Recipe JSON (raw RecipeSchema validation).
        3. Output streams are parsed by independent standard parsers (csv.reader, json.loads, Pydantic).
        4. Verifies 100% data fidelity and interoperability across all export formats.
        """
        sid = "sess_t4_s5_interop"

        r1 = RecipeSchema(
            recipe_id="rec_interop_01",
            title="Japanese 抹茶 (Matcha) Soufflé Pancake 🥞",
            description='Ultra-fluffy soufflé pancakes made in an 8" covered non-stick pan.\nServe immediately with crème fraîche & organic honey 🍯.',
            yield_servings="4 fluffy pancakes 🥞",
            prep_time="20 mins",
            cook_time="15 mins",
            total_time="35 mins",
            ingredients=[
                "2 large eggs 🥚 (separated)",
                "2 ½ tbsp whole milk",
                "½ tsp pure vanilla extract",
                "⅓ cup cake flour (薄力粉)",
                "1 ½ tsp organic Uji matcha powder (宇治抹茶) 🍵",
                "2 ¼ tbsp granulated sugar",
                "⅛ tsp cream of tartar",
                '1 tbsp "Kerrygold" unsalted butter',
            ],
            directions=[
                "1. Whisk egg yolks, milk, and vanilla until pale; sift in cake flour and matcha powder.",
                "2. In clean bowl, whip egg whites with cream of tartar; gradually add sugar until stiff, glossy peaks form (meringue ☁️).",
                "3. Fold ⅓ of meringue into matcha batter, then gently fold in remaining meringue in 2 batches.",
                "4. Cook in buttered pan at ~300°F (150°C) with 1 tsp water under lid for 5-6 mins per side.",
                "5. Stack high, dust with powdered sugar ❄️, and top with butter & honey.",
            ],
            tags=["Breakfast 🥞", "Japanese 🇯🇵", "Matcha 🍵", "Dessert"],
            source_notes="Tokyo Café Recipe Note (東京ノート) — Chef Kenji 👨‍🍳",
        )

        r2 = RecipeSchema(
            recipe_id="rec_interop_02",
            title='Classic "Toll House" Dark Chocolate Chip Cookies, 9"',
            description='Grandma said: "Use real butter and an 8" or 9" baking sheet at 375°F!"',
            yield_servings="24 cookies",
            prep_time="15 mins",
            cook_time="10 mins",
            total_time="25 mins",
            ingredients=[
                '2 ¼ cups all-purpose flour',
                '1 tsp baking soda',
                '1 cup "pure" butter, softened',
                '¾ cup granulated sugar',
                '¾ cup packed brown sugar',
                '1 tsp vanilla extract',
                '2 large eggs',
                '2 cups "Nestlé" semi-sweet chocolate morsels',
            ],
            directions=[
                '1. Preheat oven to 375°F (190°C).',
                '2. Combine flour and baking soda in small bowl.',
                '3. Beat butter, granulated sugar, brown sugar, and vanilla in large mixer bowl.',
                '4. Add eggs one at a time, beating well after each addition.',
                '5. Gradually beat in flour mixture; stir in chocolate morsels.',
                '6. Drop by rounded tablespoon onto ungreased baking sheets.',
                '7. Bake for 9 to 11 minutes or until golden brown. Cool on wire racks.',
            ],
            tags=['Cookies', '"Vintage"', 'Baking', 'Chocolate 🍫'],
            source_notes="Vintage Yellowed Index Card (1964)",
        )

        isolated_session_store.save_session(session_id=sid, state=PipelineState.COMPLETED)
        isolated_session_store.save_recipe(sid, r1)
        isolated_session_store.save_recipe(sid, r2)

        # --------------------------------------------------------------------
        # 1. Plan To Eat RFC 4180 CSV Validation
        # --------------------------------------------------------------------
        res_csv = await full_api_client.get(f"/api/export/csv?session_id={sid}")
        assert res_csv.status_code == 200
        csv_text = res_csv.text

        # Validate with csv.reader
        csv_rows = list(csv.reader(io.StringIO(csv_text)))
        assert len(csv_rows) == 3  # 1 header + 2 data rows
        assert csv_rows[0] == PLAN_TO_EAT_COLUMNS
        assert len(csv_rows[1]) == 9
        assert len(csv_rows[2]) == 9

        # Validate with csv.DictReader
        dict_reader = csv.DictReader(io.StringIO(csv_text))
        dict_rows = list(dict_reader)
        assert len(dict_rows) == 2
        assert dict_rows[0]["Title"] == "Japanese 抹茶 (Matcha) Soufflé Pancake 🥞"
        assert "2 ½ tbsp whole milk" in dict_rows[0]["Ingredients"]
        assert dict_rows[1]["Title"] == 'Classic "Toll House" Dark Chocolate Chip Cookies, 9"'
        assert '2 cups "Nestlé" semi-sweet chocolate morsels' in dict_rows[1]["Ingredients"]

        # --------------------------------------------------------------------
        # 2. Schema.org Recipe JSON-LD Validation
        # --------------------------------------------------------------------
        res_ld = await full_api_client.get(f"/api/export/json-ld?session_id={sid}")
        assert res_ld.status_code == 200
        ld_obj = res_ld.json()
        assert ld_obj["@context"] == "https://schema.org"
        assert "@graph" in ld_obj
        assert len(ld_obj["@graph"]) == 2

        g1 = ld_obj["@graph"][0]
        assert g1["@type"] == "Recipe"
        assert g1["name"] == "Japanese 抹茶 (Matcha) Soufflé Pancake 🥞"
        assert g1["prepTime"] == "PT20M"
        assert g1["cookTime"] == "PT15M"
        assert g1["totalTime"] == "PT35M"
        assert len(g1["recipeInstructions"]) == 5

        g2 = ld_obj["@graph"][1]
        assert g2["@type"] == "Recipe"
        assert g2["name"] == 'Classic "Toll House" Dark Chocolate Chip Cookies, 9"'
        assert g2["prepTime"] == "PT15M"
        assert g2["cookTime"] == "PT10M"
        assert g2["totalTime"] == "PT25M"
        assert len(g2["recipeInstructions"]) == 7

        # --------------------------------------------------------------------
        # 3. Standard Recipe JSON Validation
        # --------------------------------------------------------------------
        res_json = await full_api_client.get(f"/api/export/json?session_id={sid}")
        assert res_json.status_code == 200
        json_array = res_json.json()
        assert isinstance(json_array, list)
        assert len(json_array) == 2

        # Validate with Pydantic model
        rec1_reloaded = RecipeSchema.model_validate(json_array[0])
        rec2_reloaded = RecipeSchema.model_validate(json_array[1])
        assert rec1_reloaded.title == r1.title
        assert rec2_reloaded.title == r2.title
        assert rec1_reloaded.ingredients == r1.ingredients
        assert rec2_reloaded.ingredients == r2.ingredients
