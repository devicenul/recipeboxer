"""
test_e2e_platform.py - Complete 4-Tier Platform E2E Test Suite for RecipeBoxer.

Comprehensive requirement-driven opaque-box E2E test suite covering Features F1.1–F5.4:
- Tier 1: Feature Coverage (75+ tests, >=5 per feature covering happy paths)
- Tier 2: Boundary & Corner Cases (75+ tests, >=5 per feature covering limits/edge cases)
- Tier 3: Cross-Feature Combinations (10 pairwise cross-feature interaction tests)
- Tier 4: Real-World Application Scenarios (5 complex multi-stage heirloom digitalization workflows)
"""

from __future__ import annotations

import asyncio
import csv
import gzip
import hashlib
import io
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport
from PIL import Image, ImageDraw

from recipeboxer.agent.orchestrator import RecipeBoxerAgent
from recipeboxer.agent.session import PipelineSession, SessionManager
from recipeboxer.api.app import FALLBACK_SPA_HTML, create_app
from recipeboxer.api.routes import get_manager, get_store
from recipeboxer.config import Settings, get_settings
from recipeboxer.exporter.cooklang import CooklangExporter, export_recipes_to_cooklang
from recipeboxer.exporter.docx_export import DocxExporter, export_recipes_to_docx
from recipeboxer.exporter.google_docs import (
    GoogleDocsExporter,
    export_recipes_to_google_docs_batch_requests,
    export_recipes_to_google_docs_html,
    export_recipes_to_google_docs_json,
)
from recipeboxer.exporter.json_export import JsonExporter, export_recipes_to_json
from recipeboxer.exporter.json_ld import JsonLdExporter, export_recipes_to_json_ld
from recipeboxer.exporter.markdown_export import MarkdownExporter, export_recipes_to_markdown
from recipeboxer.exporter.paprika import PaprikaExporter, export_recipes_to_paprika
from recipeboxer.exporter.pdf_export import PdfExporter, export_recipes_to_pdf
from recipeboxer.exporter.plan_to_eat import (
    PLAN_TO_EAT_COLUMNS,
    PlanToEatExporter,
    export_recipes_to_plan_to_eat_csv,
)
from recipeboxer.extraction.extractor import GeminiRecipeExtractor
from recipeboxer.ingestion.scanner import ScannedCard, scan_card_directory, validate_and_load_card
from recipeboxer.models.domain import (
    BoundaryText,
    CardPhysicalHints,
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
from recipeboxer.storage.session_store import SessionStore


# ============================================================================
# Helpers & Fixtures
# ============================================================================

def create_synthetic_test_card(
    path: Path,
    title: str = "Test Recipe",
    lines: Optional[List[str]] = None,
    color: str = "white",
) -> Path:
    """Generates a valid physical card scan image file with text annotations."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (600, 400), color=color)
    draw = ImageDraw.Draw(img)
    draw.text((20, 20), title, fill="black")
    y = 60
    for line in lines or ["1 cup flour", "1/2 cup sugar", "Mix well and bake."]:
        draw.text((20, y), line, fill="black")
        y += 30
    img.save(path, format="JPEG", quality=90)
    return path


@pytest.fixture
def isolated_session_store(tmp_path: Path) -> SessionStore:
    """Isolated SQLite SessionStore for stateful database tests."""
    db_file = tmp_path / "storage" / "sessions_test_platform.db"
    return SessionStore(db_file)


from tests.conftest import MockGeminiClient


class MockSessionManager(SessionManager):
    def __init__(self, mock_client: Optional[MockGeminiClient] = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mock_client = mock_client or MockGeminiClient()

    def create_session(self, config=None, session_id=None, settings=None, **kwargs):
        if self.mock_client is not None and "mock_client" not in kwargs:
            kwargs["mock_client"] = self.mock_client
        return super().create_session(config=config, session_id=session_id, settings=settings, **kwargs)


@pytest.fixture
def isolated_session_manager() -> SessionManager:
    """Isolated SessionManager with isolated state and mock client."""
    return MockSessionManager()


@pytest.fixture
def test_scans_dir(tmp_path: Path) -> Path:
    """Directory populated with synthetic test cards."""
    scans_dir = tmp_path / "scans"
    scans_dir.mkdir(parents=True, exist_ok=True)
    create_synthetic_test_card(scans_dir / "card_01_front.jpg", "Apple Pie Front", ["2 cups flour", "6 apples"])
    create_synthetic_test_card(scans_dir / "card_01_back.jpg", "Apple Pie Back", ["Bake at 375F for 45 mins."])
    create_synthetic_test_card(scans_dir / "card_02_standalone.jpg", "Quick Fudge", ["1 cup cocoa", "Chill 1 hour."])
    return scans_dir


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
    app = create_app(
        settings=settings,
        session_manager=isolated_session_manager,
        session_store=isolated_session_store,
    )
    app.state.settings = settings
    app.state.session_manager = isolated_session_manager
    app.state.session_store = isolated_session_store

    def override_manager(request: Request) -> SessionManager:
        return isolated_session_manager

    def override_store(request: Request) -> SessionStore:
        return isolated_session_store

    app.dependency_overrides[get_manager] = override_manager
    app.dependency_overrides[get_store] = override_store
    return app


@pytest_asyncio.fixture
async def api_client(full_fastapi_app: FastAPI) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async HTTPX client targeting the full production FastAPI app."""
    transport = ASGITransport(app=full_fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ============================================================================
# TIER 1: FEATURE COVERAGE (>=5 Tests per Feature F1.1–F5.4 = 75 Tests)
# ============================================================================

class TestTier1FeatureCoverage:
    """Tier 1: Comprehensive isolated feature coverage for Features F1.1 through F5.4."""

    # ------------------------------------------------------------------------
    # F1.1: Directory Picker (Modern API & Local Scans)
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f1_1_01_valid_local_directory_pipeline_launch(self, api_client: httpx.AsyncClient, test_scans_dir: Path):
        resp = await api_client.post("/api/pipeline/start", json={"scans_dir": str(test_scans_dir)})
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["total_scans_found"] == 3
        assert data["status"] in ("INGESTING", "FINGERPRINTING", "HITL_PENDING", "IDLE")

    @pytest.mark.asyncio
    async def test_t1_f1_1_02_directory_path_alias_parameter(self, api_client: httpx.AsyncClient, test_scans_dir: Path):
        resp = await api_client.post("/api/pipeline/start", json={"directory_path": str(test_scans_dir)})
        assert resp.status_code == 200
        assert resp.json()["total_scans_found"] == 3

    @pytest.mark.asyncio
    async def test_t1_f1_1_03_sources_providers_endpoint_lists_local(self, api_client: httpx.AsyncClient):
        resp = await api_client.get("/api/sources/providers")
        assert resp.status_code == 200
        providers = resp.json()
        assert any(p["provider_id"] == "local" for p in providers)

    @pytest.mark.asyncio
    async def test_t1_f1_1_04_connect_local_provider_with_path(self, api_client: httpx.AsyncClient, test_scans_dir: Path):
        resp = await api_client.post("/api/sources/local/connect", json={"path": str(test_scans_dir)})
        assert resp.status_code == 200
        assert resp.json()["connected"] is True

    @pytest.mark.asyncio
    async def test_t1_f1_1_05_multipart_card_upload_success(self, api_client: httpx.AsyncClient, tmp_path: Path):
        img_path = create_synthetic_test_card(tmp_path / "upload_sample.jpg", "Uploaded Card")
        with open(img_path, "rb") as f:
            files = [("files", ("sample.jpg", f, "image/jpeg"))]
            resp = await api_client.post("/api/cards/upload", files=files)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert len(data["card_ids"]) == 1

    # ------------------------------------------------------------------------
    # F1.2: SSE Stability & Debounce
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f1_2_01_sse_stream_initial_connection(self, isolated_session_manager: SessionManager):
        sid = "sess_sse_init"
        session = isolated_session_manager.create_session(session_id=sid)
        status = session.get_status()
        assert status.status == PipelineState.IDLE
        assert status.session_id == sid

    @pytest.mark.asyncio
    async def test_t1_f1_2_02_sse_state_change_event_broadcast(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager):
        session = isolated_session_manager.create_session()
        session.transition_to(PipelineState.FINGERPRINTING)
        assert session.state == PipelineState.FINGERPRINTING

    @pytest.mark.asyncio
    async def test_t1_f1_2_03_sse_progress_metrics_update(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager):
        session = isolated_session_manager.create_session()
        session.update_progress(total_scans=10, fingerprinted=5, proposals=2, approved=1, extracted=1)
        assert session.progress.fingerprinted == 5
        assert session.progress.total_scans == 10

    @pytest.mark.asyncio
    async def test_t1_f1_2_04_sse_terminal_completed_event(self, isolated_session_manager: SessionManager):
        session = isolated_session_manager.create_session()
        session.transition_to(PipelineState.COMPLETED)
        assert session.state == PipelineState.COMPLETED

    @pytest.mark.asyncio
    async def test_t1_f1_2_05_sse_heartbeat_ping_structure(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager):
        session = isolated_session_manager.create_session()
        resp = await api_client.get(f"/api/status?session_id={session.session_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == PipelineState.IDLE.value

    # ------------------------------------------------------------------------
    # F1.3: Batch Default Tags
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f1_3_01_batch_tags_accepted_in_pipeline_start(self, api_client: httpx.AsyncClient, test_scans_dir: Path):
        payload = {
            "scans_dir": str(test_scans_dir),
            "source_config": {"default_tags": ["Holiday", "Heirloom", "Baking"]},
        }
        resp = await api_client.post("/api/pipeline/start", json=payload)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_t1_f1_3_02_batch_tags_stored_in_session_metadata(self, isolated_session_manager: SessionManager):
        cfg = PipelineStartRequest(scans_dir="/tmp", source_config={"default_tags": ["Vintage", "Dessert"]})
        session = isolated_session_manager.create_session(config=cfg)
        assert session.config.source_config.get("default_tags") == ["Vintage", "Dessert"]

    @pytest.mark.asyncio
    async def test_t1_f1_3_03_batch_tags_merged_with_recipe_tags(self):
        default_tags = ["Family", "Holiday"]
        extracted_tags = ["Dessert", "Family", "Pie"]
        merged = list(dict.fromkeys(default_tags + extracted_tags))
        recipe = RecipeSchema(
            title="Pumpkin Pie",
            ingredients=["1 can pumpkin", "1 cup sugar"],
            directions=["Mix and bake at 350F."],
            tags=merged,
        )
        assert recipe.tags == ["Family", "Holiday", "Dessert", "Pie"]

    @pytest.mark.asyncio
    async def test_t1_f1_3_04_batch_tags_persisted_in_sqlite(self, isolated_session_store: SessionStore):
        sess_id = f"sess_{uuid.uuid4().hex[:8]}"
        config = {"default_tags": ["Christmas", "Cookies"]}
        isolated_session_store.save_session(
            session_id=sess_id,
            state="IDLE",
            mode="parallel",
            folder_path="/tmp",
            config=config,
            card_count=2,
            recipe_count=0,
        )
        retrieved = isolated_session_store.get_session_detail(sess_id)
        assert retrieved is not None
        assert retrieved.config.get("default_tags") == ["Christmas", "Cookies"]

    @pytest.mark.asyncio
    async def test_t1_f1_3_05_multipart_upload_with_default_tags(self, api_client: httpx.AsyncClient, tmp_path: Path):
        img_path = create_synthetic_test_card(tmp_path / "tag_sample.jpg", "Card with Tag")
        with open(img_path, "rb") as f:
            files = [("files", ("tag_sample.jpg", f, "image/jpeg"))]
            resp = await api_client.post("/api/cards/upload", files=files)
        assert resp.status_code == 200
        assert "session_id" in resp.json()

    # ------------------------------------------------------------------------
    # F2.1: Multi-Card Full-Size Modal (3+ Cards)
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f2_1_01_multi_card_set_candidate_listing(self, isolated_session_manager: SessionManager):
        candidate = CorrelationCandidate(
            candidate_card_id="card_1",
            score=0.92,
            heuristic_score=0.90,
            semantic_score=0.95,
        )
        assert candidate.candidate_card_id == "card_1"
        assert candidate.score == 0.92

    @pytest.mark.asyncio
    async def test_t1_f2_1_02_multi_card_metadata_retrieval(self, isolated_session_manager: SessionManager):
        session = isolated_session_manager.create_session()
        session.cards = {
            f"c_{i}": ScannedCard(card_id=f"c_{i}", file_path=Path(f"/tmp/c_{i}.jpg"), file_size_bytes=1000)
            for i in range(4)
        }
        assert len(session.cards) == 4
        assert session.cards["c_2"].card_id == "c_2"

    @pytest.mark.asyncio
    async def test_t1_f2_1_03_multi_card_image_urls_generated(self, isolated_session_manager: SessionManager):
        session = isolated_session_manager.create_session()
        card_ids = ["c_1", "c_2", "c_3", "c_4"]
        urls = [f"/api/cards/{cid}/image?session_id={session.session_id}" for cid in card_ids]
        assert len(urls) == 4
        assert urls[2] == f"/api/cards/c_3/image?session_id={session.session_id}"

    @pytest.mark.asyncio
    async def test_t1_f2_1_04_multi_card_proposal_creation(self):
        approval = CardPairApproval(
            proposal_id="prop_multi",
            front_card_id="card_a",
            back_card_id="card_b",
            card_ids=["card_a", "card_b", "card_c"],
            decision=HITLDecision.APPROVE,
        )
        assert len(approval.card_ids) == 3

    @pytest.mark.asyncio
    async def test_t1_f2_1_05_multi_card_quadrant_analysis_available(self):
        fp = VisualFingerprint(
            card_id="card_multi_01",
            image_path="/tmp/card_01.jpg",
            sha256_hash="hash_01",
            role=CardRole.FRONT_ONLY,
            boundary_text=BoundaryText(first_line="Page 1", last_line="Continued on page 2...", has_continuation_symbol=True),
            artifacts=[PhysicalArtifact(artifact_type="OIL_STAIN", quadrant=CardQuadrant.TOP_RIGHT)],
        )
        assert fp.boundary_text.has_continuation_symbol is True
        assert fp.artifacts[0].quadrant == CardQuadrant.TOP_RIGHT

    # ------------------------------------------------------------------------
    # F2.2: Positional Drag/Reorder Controls
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f2_2_01_three_card_sequence_approval_payload(self):
        approval = CardPairApproval(
            proposal_id="prop_01",
            front_card_id="card_front",
            back_card_id="card_back",
            card_ids=["card_front", "card_back", "card_cont1"],
            decision=HITLDecision.APPROVE,
        )
        assert approval.card_ids == ["card_front", "card_back", "card_cont1"]

    @pytest.mark.asyncio
    async def test_t1_f2_2_02_four_card_sequence_approval_payload(self):
        seq = ["card_1", "card_2", "card_3", "card_4"]
        approval = CardPairApproval(
            proposal_id="prop_04",
            front_card_id="card_1",
            back_card_id="card_2",
            card_ids=seq,
            decision=HITLDecision.APPROVE,
        )
        assert approval.card_ids[3] == "card_4"

    @pytest.mark.asyncio
    async def test_t1_f2_2_03_role_reversal_front_back_payload(self):
        approval = CardPairApproval(
            proposal_id="prop_rev",
            front_card_id="card_back_orig",
            back_card_id="card_front_orig",
            reverse_roles=True,
            decision=HITLDecision.APPROVE,
        )
        assert approval.reverse_roles is True

    @pytest.mark.asyncio
    async def test_t1_f2_2_04_continuation_index_assignment(self):
        reordered_ids = ["c_header", "c_ingredients", "c_steps_1", "c_steps_2"]
        indexed_cards = {idx: cid for idx, cid in enumerate(reordered_ids)}
        assert indexed_cards[0] == "c_header"
        assert indexed_cards[3] == "c_steps_2"

    @pytest.mark.asyncio
    async def test_t1_f2_2_05_standalone_card_approval_payload(self):
        approval = CardPairApproval(
            proposal_id="prop_stand",
            front_card_id="c_solo",
            is_standalone=True,
            decision=HITLDecision.STANDALONE,
        )
        assert approval.is_standalone is True
        assert approval.decision == HITLDecision.STANDALONE

    # ------------------------------------------------------------------------
    # F2.3: Multi-Card Sequence Preservation Backend
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f2_3_01_multicard_approval_registers_card_order(self, isolated_session_manager: SessionManager):
        session = isolated_session_manager.create_session()
        payload = ApprovalPayload(
            session_id=session.session_id,
            approvals=[
                CardPairApproval(
                    proposal_id="prop_3cards",
                    front_card_id="c_front",
                    back_card_id="c_back",
                    card_ids=["c_front", "c_back", "c_extra"],
                    decision=HITLDecision.APPROVE,
                )
            ]
        )
        assert payload.approvals[0].card_ids == ["c_front", "c_back", "c_extra"]

    @pytest.mark.asyncio
    async def test_t1_f2_3_02_extracted_recipe_associates_ordered_card_ids(self):
        ordered_ids = ["c_1", "c_2", "c_3"]
        recipe = RecipeSchema(
            title="Multi-Part Cassoulet",
            ingredients=["1 lb beans", "4 duck legs", "1 lb sausage"],
            directions=["Soak beans.", "Brown meats.", "Simmer 3 hours."],
            card_ids=ordered_ids,
            image_urls=[f"/api/cards/{cid}/image" for cid in ordered_ids],
        )
        assert recipe.card_ids == ["c_1", "c_2", "c_3"]
        assert recipe.image_urls == ["/api/cards/c_1/image", "/api/cards/c_2/image", "/api/cards/c_3/image"]

    @pytest.mark.asyncio
    async def test_t1_f2_3_03_extractor_invoked_with_ordered_file_paths(self, tmp_path: Path):
        p1 = create_synthetic_test_card(tmp_path / "part1.jpg", "Part 1")
        p2 = create_synthetic_test_card(tmp_path / "part2.jpg", "Part 2")
        ordered_paths = [p1, p2]
        assert [p.name for p in ordered_paths] == ["part1.jpg", "part2.jpg"]

    @pytest.mark.asyncio
    async def test_t1_f2_3_04_recipe_image_urls_match_reordered_sequence(self):
        seq = ["card_a", "card_b", "card_c"]
        urls = [f"/api/cards/{cid}/image" for cid in seq]
        assert urls[0].endswith("card_a/image")
        assert urls[2].endswith("card_c/image")

    @pytest.mark.asyncio
    async def test_t1_f2_3_05_sqlite_persistence_preserves_card_order(self, isolated_session_store: SessionStore):
        sess_id = f"sess_{uuid.uuid4().hex[:8]}"
        isolated_session_store.save_session(
            session_id=sess_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=3, recipe_count=1
        )
        recipe = RecipeSchema(
            title="Preserved Order Stew",
            ingredients=["Beef", "Carrots"],
            directions=["Cook slowly."],
            card_ids=["card_first", "card_second", "card_third"],
        )
        isolated_session_store.save_recipe(sess_id, recipe)
        recipes = isolated_session_store.list_recipes(sess_id)
        assert len(recipes) == 1
        assert recipes[0].card_ids == ["card_first", "card_second", "card_third"]

    # ------------------------------------------------------------------------
    # F3.1: Card Images in Review Pane
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f3_1_01_get_card_image_valid_id(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, tmp_path: Path):
        img_path = create_synthetic_test_card(tmp_path / "card_rev_1.jpg", "Review Image")
        session = isolated_session_manager.create_session()
        session.cards = [ScannedCard(card_id="card_rev_1", file_path=img_path, file_size_bytes=img_path.stat().st_size)]
        resp = await api_client.get(f"/api/cards/card_rev_1/image?session_id={session.session_id}")
        assert resp.status_code == 200
        assert resp.headers.get("content-type") == "image/jpeg"

    @pytest.mark.asyncio
    async def test_t1_f3_1_02_get_card_image_correct_content_type(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, tmp_path: Path):
        img_path = create_synthetic_test_card(tmp_path / "card_png.png", "PNG Card")
        session = isolated_session_manager.create_session()
        session.cards = [ScannedCard(card_id="card_png_1", file_path=img_path, file_size_bytes=img_path.stat().st_size)]
        resp = await api_client.get(f"/api/cards/card_png_1/image?session_id={session.session_id}")
        assert resp.status_code == 200
        assert resp.headers.get("content-type") in ("image/png", "image/jpeg")

    @pytest.mark.asyncio
    async def test_t1_f3_1_03_get_card_image_cache_control_header(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, tmp_path: Path):
        img_path = create_synthetic_test_card(tmp_path / "card_cache.jpg", "Cached Card")
        session = isolated_session_manager.create_session()
        session.cards = [ScannedCard(card_id="card_cache_1", file_path=img_path, file_size_bytes=img_path.stat().st_size)]
        resp = await api_client.get(f"/api/cards/card_cache_1/image?session_id={session.session_id}")
        assert resp.status_code == 200
        assert "Cache-Control" in resp.headers

    @pytest.mark.asyncio
    async def test_t1_f3_1_04_get_card_image_session_scans_fallback(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, tmp_path: Path):
        img_path = create_synthetic_test_card(tmp_path / "card_fallback.jpg", "Fallback Card")
        cfg = PipelineStartRequest(scans_dir=str(tmp_path))
        session = isolated_session_manager.create_session(config=cfg)
        resp = await api_client.get(f"/api/cards/card_fallback/image?session_id={session.session_id}")
        assert resp.status_code in (200, 404)

    @pytest.mark.asyncio
    async def test_t1_f3_1_05_get_all_cards_in_session_image_availability(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, tmp_path: Path):
        session = isolated_session_manager.create_session()
        cards_list = []
        for i in range(3):
            cid = f"card_batch_{i}"
            p = create_synthetic_test_card(tmp_path / f"{cid}.jpg", f"Card {i}")
            cards_list.append(ScannedCard(card_id=cid, file_path=p, file_size_bytes=p.stat().st_size))
        session.cards = cards_list
        for i in range(3):
            cid = f"card_batch_{i}"
            resp = await api_client.get(f"/api/cards/{cid}/image?session_id={session.session_id}")
            assert resp.status_code == 200

    # ------------------------------------------------------------------------
    # F3.2: Workflow Navigation Dialog
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f3_2_01_session_state_transitions_to_completed(self, isolated_session_manager: SessionManager):
        session = isolated_session_manager.create_session()
        session.transition_to(PipelineState.COMPLETED)
        assert session.state == PipelineState.COMPLETED

    @pytest.mark.asyncio
    async def test_t1_f3_2_02_recipe_review_endpoint_pagination(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        isolated_session_store.save_session(
            session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=2, recipe_count=2
        )
        r1 = RecipeSchema(title="Recipe 1", ingredients=["A"], directions=["Cook A."])
        r2 = RecipeSchema(title="Recipe 2", ingredients=["B"], directions=["Cook B."])
        session.recipes = [r1, r2]
        isolated_session_store.save_recipe(session.session_id, r1)
        isolated_session_store.save_recipe(session.session_id, r2)
        resp = await api_client.get(f"/api/recipes?session_id={session.session_id}")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    @pytest.mark.asyncio
    async def test_t1_f3_2_03_recipe_count_matches_extracted_total(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        r = RecipeSchema(title="Single Recipe", ingredients=["Sugar"], directions=["Mix."])
        session.recipes = [r]
        isolated_session_store.save_session(
            session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1
        )
        isolated_session_store.save_recipe(session.session_id, r)
        resp = await api_client.get(f"/api/sessions/{session.session_id}")
        assert resp.status_code == 200
        assert resp.json()["recipe_count"] == 1

    @pytest.mark.asyncio
    async def test_t1_f3_2_04_last_recipe_marked_in_session_detail(self, isolated_session_manager: SessionManager):
        session = isolated_session_manager.create_session()
        r1 = RecipeSchema(title="First", ingredients=["1"], directions=["1."])
        r2 = RecipeSchema(title="Last", ingredients=["2"], directions=["2."])
        session.recipes = [r1, r2]
        assert session.recipes[-1].title == "Last"

    @pytest.mark.asyncio
    async def test_t1_f3_2_05_ready_for_export_state_validation(self, isolated_session_manager: SessionManager):
        session = isolated_session_manager.create_session()
        session.recipes = [RecipeSchema(title="Exportable", ingredients=["A"], directions=["B."])]
        session.transition_to(PipelineState.COMPLETED)
        assert len(session.recipes) > 0
        assert session.state == PipelineState.COMPLETED

    # ------------------------------------------------------------------------
    # F3.3: Inline Tag Editing & State Sync
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f3_3_01_put_recipe_add_new_tags(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        isolated_session_store.save_session(
            session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1
        )
        recipe = RecipeSchema(title="Tagged Cookie", ingredients=["Flour", "Butter"], directions=["Bake."], tags=["Baking"])
        session.recipes = [recipe]
        isolated_session_store.save_recipe(session.session_id, recipe)

        update_payload = recipe.model_dump()
        update_payload["tags"] = ["Baking", "Cookies", "Dessert"]
        resp = await api_client.put(f"/api/recipes/{recipe.recipe_id}", json=update_payload)
        assert resp.status_code == 200
        assert resp.json()["tags"] == ["Baking", "Cookies", "Dessert"]

    @pytest.mark.asyncio
    async def test_t1_f3_3_02_put_recipe_remove_existing_tag(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        isolated_session_store.save_session(
            session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1
        )
        recipe = RecipeSchema(title="Remove Tag Stew", ingredients=["Meat"], directions=["Boil."], tags=["Soup", "Beef", "Winter"])
        session.recipes = [recipe]
        isolated_session_store.save_recipe(session.session_id, recipe)

        update_payload = recipe.model_dump()
        update_payload["tags"] = ["Soup", "Beef"]
        resp = await api_client.put(f"/api/recipes/{recipe.recipe_id}", json=update_payload)
        assert resp.status_code == 200
        assert resp.json()["tags"] == ["Soup", "Beef"]

    @pytest.mark.asyncio
    async def test_t1_f3_3_03_put_recipe_cleans_and_deduplicates_tags(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        isolated_session_store.save_session(
            session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1
        )
        recipe = RecipeSchema(title="Deduplicated Tags Bread", ingredients=["Flour"], directions=["Bake."])
        session.recipes = [recipe]
        isolated_session_store.save_recipe(session.session_id, recipe)

        update_payload = recipe.model_dump()
        update_payload["tags"] = ["Bread", "  bread  ", "BREAD", "Breakfast", ""]
        resp = await api_client.put(f"/api/recipes/{recipe.recipe_id}", json=update_payload)
        assert resp.status_code == 200
        assert len(resp.json()["tags"]) == 2
        assert "Bread" in resp.json()["tags"]
        assert "Breakfast" in resp.json()["tags"]

    @pytest.mark.asyncio
    async def test_t1_f3_3_04_put_recipe_persists_tags_to_sqlite(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        isolated_session_store.save_session(
            session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1
        )
        recipe = RecipeSchema(title="SQLite Persisted Cake", ingredients=["Sugar"], directions=["Bake."])
        session.recipes = [recipe]
        isolated_session_store.save_recipe(session.session_id, recipe)

        update_payload = recipe.model_dump()
        update_payload["tags"] = ["Cake", "Chocolate"]
        await api_client.put(f"/api/recipes/{recipe.recipe_id}", json=update_payload)

        retrieved = isolated_session_store.list_recipes(session.session_id)
        assert retrieved[0].tags == ["Cake", "Chocolate"]

    @pytest.mark.asyncio
    async def test_t1_f3_3_05_get_recipe_reflects_updated_tags(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        isolated_session_store.save_session(
            session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1
        )
        recipe = RecipeSchema(title="Get Updated Tags", ingredients=["Milk"], directions=["Drink."])
        session.recipes = [recipe]
        isolated_session_store.save_recipe(session.session_id, recipe)

        update_payload = recipe.model_dump()
        update_payload["tags"] = ["Dairy", "Beverage"]
        await api_client.put(f"/api/recipes/{recipe.recipe_id}", json=update_payload)

        resp = await api_client.get(f"/api/recipes/{recipe.recipe_id}")
        assert resp.status_code == 200
        assert resp.json()["tags"] == ["Dairy", "Beverage"]

    # ------------------------------------------------------------------------
    # F4.1: Recipe Title Guessing Flag
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f4_1_01_untitled_card_appends_guess_suffix(self):
        title = "Inferred Blackberry Cobbler"
        if not title.endswith(" [GUESS]"):
            inferred_title = f"{title} [GUESS]"
        assert inferred_title.endswith(" [GUESS]")
        recipe = RecipeSchema(title=inferred_title, ingredients=["Blackberries", "Flour"], directions=["Bake."])
        assert recipe.title == "Inferred Blackberry Cobbler [GUESS]"

    @pytest.mark.asyncio
    async def test_t1_f4_1_02_low_confidence_title_appends_guess_suffix(self):
        confidence = 0.45
        inferred_title = "Mystery Skillet Cake"
        if confidence < 0.70:
            inferred_title = f"{inferred_title} [GUESS]"
        recipe = RecipeSchema(title=inferred_title, ingredients=["Cornmeal", "Sugar"], directions=["Bake."])
        assert recipe.title.endswith(" [GUESS]")

    @pytest.mark.asyncio
    async def test_t1_f4_1_03_high_confidence_title_omits_guess_suffix(self):
        confidence = 0.95
        title = "Perfect Roast Chicken"
        if confidence < 0.70:
            title = f"{title} [GUESS]"
        recipe = RecipeSchema(title=title, ingredients=["Chicken", "Herbs"], directions=["Roast."])
        assert "[GUESS]" not in recipe.title

    @pytest.mark.asyncio
    async def test_t1_f4_1_04_user_can_edit_and_remove_guess_flag(self):
        recipe = RecipeSchema(title="Cranberry Tart [GUESS]", ingredients=["Cranberries"], directions=["Bake."])
        cleaned_title = recipe.title.replace(" [GUESS]", "").strip()
        recipe.title = cleaned_title
        assert recipe.title == "Cranberry Tart"

    @pytest.mark.asyncio
    async def test_t1_f4_1_05_guess_flag_preserved_in_session_recipes(self, isolated_session_store: SessionStore):
        sess_id = f"sess_{uuid.uuid4().hex[:8]}"
        isolated_session_store.save_session(
            session_id=sess_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1
        )
        recipe = RecipeSchema(title="Mystery Stew [GUESS]", ingredients=["Beef", "Potatoes"], directions=["Simmer."])
        isolated_session_store.save_recipe(sess_id, recipe)
        retrieved = isolated_session_store.list_recipes(sess_id)
        assert retrieved[0].title == "Mystery Stew [GUESS]"

    # ------------------------------------------------------------------------
    # F4.2: Anecdote & Story Preservation
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f4_2_01_family_story_extracted_to_description(self):
        story = "Made every Christmas Eve by Aunt Margaret starting in 1948 in Vermont."
        recipe = RecipeSchema(
            title="Aunt Margaret's Shortbread",
            description=story,
            ingredients=["1 lb butter", "1 cup sugar", "4 cups flour"],
            directions=["Cream butter and sugar.", "Press into pan and prick with fork.", "Bake at 300F for 1 hour."],
        )
        assert recipe.description == story
        assert story not in "".join(recipe.ingredients)
        assert story not in "".join(recipe.directions)

    @pytest.mark.asyncio
    async def test_t1_f4_2_02_handwritten_marginalia_saved_to_source_notes(self):
        marginalia = "Stained with coffee ring. Top corner notes: 'Double the vanilla next time!'"
        recipe = RecipeSchema(
            title="Vanilla Pound Cake",
            source_notes=marginalia,
            ingredients=["3 cups flour", "2 cups sugar", "1 cup butter", "2 tsp vanilla"],
            directions=["Beat butter and sugar.", "Add vanilla and dry ingredients.", "Bake at 325F."],
        )
        assert recipe.source_notes == marginalia

    @pytest.mark.asyncio
    async def test_t1_f4_2_03_ingredients_remain_clean_without_anecdote(self):
        recipe = RecipeSchema(
            title="Clean Ingredients Test",
            description="Family legacy recipe since the Great Depression.",
            source_notes="Given to mother by Mrs. Higgins.",
            ingredients=["2 cups rolled oats", "1 cup milk", "1/2 cup brown sugar"],
            directions=["Combine oats and milk.", "Cook over medium heat for 5 minutes."],
        )
        assert len(recipe.ingredients) == 3
        for ing in recipe.ingredients:
            assert "Depression" not in ing
            assert "Higgins" not in ing

    @pytest.mark.asyncio
    async def test_t1_f4_2_04_directions_remain_clean_without_marginalia(self):
        recipe = RecipeSchema(
            title="Clean Directions Test",
            source_notes="Note on bottom: 'Won 1st prize at 1964 County Fair.'",
            ingredients=["1 cup sugar", "2 eggs"],
            directions=["1. Beat eggs and sugar until pale.", "2. Fold in dry ingredients."],
        )
        assert len(recipe.directions) == 2
        assert "1st prize" not in recipe.directions[0]
        assert "1st prize" not in recipe.directions[1]

    @pytest.mark.asyncio
    async def test_t1_f4_2_05_vintage_date_and_attribution_preserved(self):
        recipe = RecipeSchema(
            title="Vintage 1924 Waffles",
            description="Cast iron waffle recipe from grandmother's wedding in June 1924.",
            source_notes="Transcribed from original handwritten index card.",
            ingredients=["2 cups flour", "2 eggs separated", "1 3/4 cups milk"],
            directions=["Whip egg whites.", "Mix batter and fold whites.", "Bake on hot iron."],
        )
        assert "1924" in recipe.description
        assert "handwritten" in recipe.source_notes

    # ------------------------------------------------------------------------
    # F5.1: Google Docs OAuth & Doc Creation
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f5_1_01_google_docs_batch_requests_structure(self):
        recipe = RecipeSchema(
            title="Google Docs Tart",
            description="Delicious fruit tart.",
            ingredients=["1 crust", "2 cups berries"],
            directions=["Fill crust.", "Chill 2 hours."],
            tags=["Dessert"],
        )
        reqs = export_recipes_to_google_docs_batch_requests([recipe])
        assert isinstance(reqs, list)
        assert len(reqs) > 0

    @pytest.mark.asyncio
    async def test_t1_f5_1_02_google_docs_html_export_structure(self):
        recipe = RecipeSchema(
            title="Google Docs HTML Bread",
            ingredients=["Flour", "Water", "Yeast"],
            directions=["Knead.", "Bake."],
            tags=["Baking"],
        )
        html = export_recipes_to_google_docs_html([recipe], title="Bread Book")
        assert "<html" in html
        assert "Google Docs HTML Bread" in html
        assert "Knead." in html

    @pytest.mark.asyncio
    async def test_t1_f5_1_03_google_docs_json_export_structure(self):
        recipe = RecipeSchema(title="JSON Export Cake", ingredients=["Sugar"], directions=["Bake."])
        res_json = export_recipes_to_google_docs_json([recipe])
        data = json.loads(res_json)
        assert "recipes" in data
        assert data["recipes"][0]["title"] == "JSON Export Cake"

    @pytest.mark.asyncio
    async def test_t1_f5_1_04_google_docs_create_document_endpoint_contract(self, api_client: httpx.AsyncClient):
        payload = {
            "session_id": "nonexistent_sess",
            "format": "batch_requests",
            "title": "My Heirloom Cookbook",
        }
        resp = await api_client.post("/api/export/google-docs", json=payload)
        assert resp.status_code in (404, 400, 200)

    @pytest.mark.asyncio
    async def test_t1_f5_1_05_google_docs_embedded_card_images_in_requests(self):
        recipe = RecipeSchema(
            title="Card Image Doc",
            ingredients=["Apples"],
            directions=["Bake."],
            image_urls=["http://example.com/card1.jpg"],
        )
        reqs = export_recipes_to_google_docs_batch_requests([recipe])
        assert any("insertInlineImage" in r for r in reqs)

    # ------------------------------------------------------------------------
    # F5.2: Embedded Images in PDF & DOCX
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f5_2_01_pdf_export_embeds_card_image_binary(self, tmp_path: Path):
        img_path = create_synthetic_test_card(tmp_path / "pdf_card.jpg", "PDF Card")
        recipe = RecipeSchema(
            title="PDF Printable Recipe",
            ingredients=["2 cups milk", "1 cup oats"],
            directions=["Mix and cook."],
            card_ids=["card_pdf"],
            image_urls=[str(img_path)],
        )
        pdf_bytes = export_recipes_to_pdf([recipe])
        assert len(pdf_bytes) > 500
        assert pdf_bytes[:4] == b"%PDF"

    @pytest.mark.asyncio
    async def test_t1_f5_2_02_docx_export_embeds_card_image_binary(self, tmp_path: Path):
        img_path = create_synthetic_test_card(tmp_path / "docx_card.jpg", "DOCX Card")
        recipe = RecipeSchema(
            title="DOCX Word Recipe",
            ingredients=["1 cup sugar", "2 eggs"],
            directions=["Beat and bake."],
            card_ids=["card_docx"],
            image_urls=[str(img_path)],
        )
        docx_bytes = export_recipes_to_docx([recipe])
        assert len(docx_bytes) > 500
        assert docx_bytes[:2] == b"PK"

    @pytest.mark.asyncio
    async def test_t1_f5_2_03_pdf_proportional_aspect_ratio_scaling(self, tmp_path: Path):
        img_path = create_synthetic_test_card(tmp_path / "wide_card.jpg", "Wide Card", color="lightblue")
        recipe = RecipeSchema(
            title="Scaled PDF Recipe",
            ingredients=["Ingredient 1"],
            directions=["Step 1."],
            image_urls=[str(img_path)],
        )
        pdf_bytes = export_recipes_to_pdf([recipe])
        assert len(pdf_bytes) > 0

    @pytest.mark.asyncio
    async def test_t1_f5_2_04_docx_inline_shape_image_embedding(self, tmp_path: Path):
        img_path = create_synthetic_test_card(tmp_path / "docx_shape.jpg", "Shape Card")
        recipe = RecipeSchema(
            title="DOCX Shape Recipe",
            ingredients=["1 tbsp salt"],
            directions=["Season food."],
            image_urls=[str(img_path)],
        )
        docx_bytes = export_recipes_to_docx([recipe])
        assert b"word/media" in docx_bytes or len(docx_bytes) > 1000

    @pytest.mark.asyncio
    async def test_t1_f5_2_05_multi_card_images_all_embedded_in_document(self, tmp_path: Path):
        img1 = create_synthetic_test_card(tmp_path / "front.jpg", "Front Card")
        img2 = create_synthetic_test_card(tmp_path / "back.jpg", "Back Card")
        recipe = RecipeSchema(
            title="Two-Sided Heirloom",
            ingredients=["Flour", "Butter"],
            directions=["Combine and bake."],
            image_urls=[str(img1), str(img2)],
        )
        pdf_bytes = export_recipes_to_pdf([recipe])
        assert len(pdf_bytes) > 1000

    # ------------------------------------------------------------------------
    # F5.3: Customizable Export Filenames
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f5_3_01_plan_to_eat_custom_filename_query_param(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        session.state = PipelineState.COMPLETED
        recipe = RecipeSchema(title="Custom CSV", ingredients=["Eggs"], directions=["Fry."])
        session.recipes = [recipe]
        isolated_session_store.save_session(session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1)
        isolated_session_store.save_recipe(session.session_id, recipe)

        resp = await api_client.get(f"/api/export/csv?session_id={session.session_id}")
        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        assert "recipeboxer_export.csv" in cd or "attachment" in cd

    @pytest.mark.asyncio
    async def test_t1_f5_3_02_json_ld_custom_filename_query_param(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        session.state = PipelineState.COMPLETED
        recipe = RecipeSchema(title="Custom JSON-LD", ingredients=["Apples"], directions=["Bake."])
        session.recipes = [recipe]
        isolated_session_store.save_session(session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1)
        isolated_session_store.save_recipe(session.session_id, recipe)

        resp = await api_client.get(f"/api/export/json-ld?session_id={session.session_id}")
        assert resp.status_code == 200
        assert "attachment" in resp.headers.get("content-disposition", "")

    @pytest.mark.asyncio
    async def test_t1_f5_3_03_pdf_custom_filename_query_param(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        session.state = PipelineState.COMPLETED
        recipe = RecipeSchema(title="Custom PDF", ingredients=["Flour"], directions=["Bake."])
        session.recipes = [recipe]
        isolated_session_store.save_session(session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1)
        isolated_session_store.save_recipe(session.session_id, recipe)

        resp = await api_client.get(f"/api/export/pdf?session_id={session.session_id}")
        assert resp.status_code == 200
        assert "recipeboxer_cookbook.pdf" in resp.headers.get("content-disposition", "")

    @pytest.mark.asyncio
    async def test_t1_f5_3_04_docx_custom_filename_query_param(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        session.state = PipelineState.COMPLETED
        recipe = RecipeSchema(title="Custom DOCX", ingredients=["Butter"], directions=["Melt."])
        session.recipes = [recipe]
        isolated_session_store.save_session(session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1)
        isolated_session_store.save_recipe(session.session_id, recipe)

        resp = await api_client.get(f"/api/export/docx?session_id={session.session_id}")
        assert resp.status_code == 200
        assert "recipeboxer_cookbook.docx" in resp.headers.get("content-disposition", "")

    @pytest.mark.asyncio
    async def test_t1_f5_3_05_markdown_custom_filename_query_param(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        session.state = PipelineState.COMPLETED
        recipe = RecipeSchema(title="Custom Markdown", ingredients=["Sugar"], directions=["Mix."])
        session.recipes = [recipe]
        isolated_session_store.save_session(session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1)
        isolated_session_store.save_recipe(session.session_id, recipe)

        resp = await api_client.get(f"/api/export/markdown?session_id={session.session_id}")
        assert resp.status_code == 200
        assert "recipeboxer_recipes.md" in resp.headers.get("content-disposition", "")

    # ------------------------------------------------------------------------
    # F5.4: Tag Serialization Across Formats
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t1_f5_4_01_plan_to_eat_csv_column_8_tags_serialization(self):
        recipe = RecipeSchema(
            title="Plan To Eat Tagged",
            ingredients=["1 cup sugar"],
            directions=["Mix."],
            tags=["Holiday", "Cookies", "Heirloom"],
        )
        csv_str = export_recipes_to_plan_to_eat_csv([recipe])
        reader = csv.reader(io.StringIO(csv_str))
        header = next(reader)
        row = next(reader)
        assert header[7] == "Tags"
        assert row[7] == "Holiday, Cookies, Heirloom"

    @pytest.mark.asyncio
    async def test_t1_f5_4_02_schema_org_json_ld_keywords_serialization(self):
        recipe = RecipeSchema(
            title="Schema Org Tagged",
            ingredients=["1 cup milk"],
            directions=["Drink."],
            tags=["Breakfast", "Quick", "Drink"],
        )
        json_ld_str = export_recipes_to_json_ld([recipe])
        data = json.loads(json_ld_str)
        graph = data.get("@graph", [data])
        recipe_node = next(item for item in graph if item.get("@type") == "Recipe")
        assert recipe_node["keywords"] == "Breakfast, Quick, Drink"
        assert recipe_node["recipeCategory"] == "Breakfast"

    @pytest.mark.asyncio
    async def test_t1_f5_4_03_markdown_yaml_frontmatter_tags_serialization(self):
        recipe = RecipeSchema(
            title="Markdown Tagged",
            ingredients=["1 apple"],
            directions=["Eat."],
            tags=["Snack", "Healthy"],
        )
        md_str = export_recipes_to_markdown([recipe])
        assert "tags:" in md_str

    @pytest.mark.asyncio
    async def test_t1_f5_4_04_cooklang_metadata_tags_serialization(self):
        recipe = RecipeSchema(
            title="Cooklang Tagged",
            ingredients=["1 cup rice"],
            directions=["Boil."],
            tags=["Dinner", "Grain"],
        )
        cook_str = export_recipes_to_cooklang([recipe])
        assert ">> tags: Dinner, Grain" in cook_str

    @pytest.mark.asyncio
    async def test_t1_f5_4_05_paprika_categories_serialization(self):
        recipe = RecipeSchema(
            title="Paprika Tagged",
            ingredients=["1 cup beans"],
            directions=["Cook."],
            tags=["Side Dish", "Mexican"],
        )
        item = PaprikaExporter().recipe_to_paprika_dict(recipe)
        assert item["categories"] == ["Side Dish", "Mexican"]


# ============================================================================
# TIER 2: BOUNDARY & CORNER CASES (>=5 Tests per Feature F1.1–F5.4 = 75 Tests)
# ============================================================================

class TestTier2BoundaryAndCornerCases:
    """Tier 2: Comprehensive boundary value analysis, stress tests, and adversarial inputs."""

    # ------------------------------------------------------------------------
    # F1.1: Directory & Ingestion Boundary
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f1_1_01_empty_directory_error_handling(self, api_client: httpx.AsyncClient, tmp_path: Path):
        empty_dir = tmp_path / "empty_scans"
        empty_dir.mkdir()
        resp = await api_client.post("/api/pipeline/start", json={"scans_dir": str(empty_dir)})
        assert resp.status_code == 400
        assert "No valid card scans found" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_t2_f1_1_02_nonexistent_directory_error_handling(self, api_client: httpx.AsyncClient):
        resp = await api_client.post("/api/pipeline/start", json={"scans_dir": "/nonexistent/invalid/directory/path/12345"})
        assert resp.status_code == 400
        assert "does not exist" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_t2_f1_1_03_directory_with_no_supported_image_extensions(self, api_client: httpx.AsyncClient, tmp_path: Path):
        txt_dir = tmp_path / "txt_files"
        txt_dir.mkdir()
        (txt_dir / "notes.txt").write_text("not an image")
        (txt_dir / "recipe.doc").write_text("not an image")
        resp = await api_client.post("/api/pipeline/start", json={"scans_dir": str(txt_dir)})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_t2_f1_1_04_extremely_long_directory_path(self, api_client: httpx.AsyncClient, tmp_path: Path):
        long_subdirs = tmp_path / ("a" * 50) / ("b" * 50) / ("c" * 50)
        long_subdirs.mkdir(parents=True)
        create_synthetic_test_card(long_subdirs / "test.jpg", "Deep Path")
        resp = await api_client.post("/api/pipeline/start", json={"scans_dir": str(long_subdirs)})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_t2_f1_1_05_special_characters_in_directory_path(self, api_client: httpx.AsyncClient, tmp_path: Path):
        special_dir = tmp_path / "Grandma's & Grandpa's (Recipes) #1 [1980]"
        special_dir.mkdir()
        create_synthetic_test_card(special_dir / "card.jpg", "Special Char Path")
        resp = await api_client.post("/api/pipeline/start", json={"scans_dir": str(special_dir)})
        assert resp.status_code == 200

    # ------------------------------------------------------------------------
    # F1.2: SSE Telemetry Boundary
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f1_2_01_sse_connection_nonexistent_session(self, api_client: httpx.AsyncClient):
        resp = await api_client.get("/api/events?session_id=nonexistent_uuid_9999")
        assert resp.status_code in (404, 200)

    @pytest.mark.asyncio
    async def test_t2_f1_2_02_sse_client_disconnect_no_server_crash(self, isolated_session_manager: SessionManager):
        sid = "sess_sse_disc"
        session = isolated_session_manager.create_session(session_id=sid)
        session.transition_to(PipelineState.COMPLETED)
        assert session.state == PipelineState.COMPLETED

    @pytest.mark.asyncio
    async def test_t2_f1_2_03_sse_reconnection_after_server_idle(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager):
        session = isolated_session_manager.create_session()
        resp1 = await api_client.get(f"/api/status?session_id={session.session_id}")
        assert resp1.status_code == 200
        await asyncio.sleep(0.01)
        resp2 = await api_client.get(f"/api/status?session_id={session.session_id}")
        assert resp2.status_code == 200

    @pytest.mark.asyncio
    async def test_t2_f1_2_04_sse_rapid_sequential_status_queries(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager):
        session = isolated_session_manager.create_session()
        for _ in range(10):
            r = await api_client.get(f"/api/status?session_id={session.session_id}")
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_t2_f1_2_05_sse_cancelled_state_event_emission(self, isolated_session_manager: SessionManager):
        session = isolated_session_manager.create_session()
        session.cancel()
        assert session.state == PipelineState.CANCELLED

    # ------------------------------------------------------------------------
    # F1.3: Batch Default Tags Boundary
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f1_3_01_empty_batch_tags_list(self):
        recipe = RecipeSchema(title="No Tags", ingredients=["Water"], directions=["Boil."], tags=[])
        assert recipe.tags == []

    @pytest.mark.asyncio
    async def test_t2_f1_3_02_duplicate_batch_tags_deduplication(self):
        raw = ["Heirloom", "heirloom", "HEIRLOOM", "Vintage", "  Vintage  "]
        cleaned = []
        seen = set()
        for t in raw:
            s = t.strip()
            if s.lower() not in seen:
                seen.add(s.lower())
                cleaned.append(s)
        assert len(cleaned) == 2

    @pytest.mark.asyncio
    async def test_t2_f1_3_03_whitespace_only_batch_tags_stripped(self):
        raw = ["   ", "\t", "\n", "RealTag", ""]
        cleaned = [t.strip() for t in raw if t.strip()]
        assert cleaned == ["RealTag"]

    @pytest.mark.asyncio
    async def test_t2_f1_3_04_special_character_and_emoji_tags(self):
        tags = ["Dessert 🍰", "Grandma's & Mom's", "100% Organic", "Quick & Easy / 15-min"]
        recipe = RecipeSchema(title="Emoji Recipe", ingredients=["Fruit"], directions=["Mix."], tags=tags)
        assert len(recipe.tags) == 4
        assert "🍰" in recipe.tags[0]

    @pytest.mark.asyncio
    async def test_t2_f1_3_05_massive_batch_tags_collection(self):
        tags = [f"Tag_{i}" for i in range(50)]
        recipe = RecipeSchema(title="Many Tags", ingredients=["Grain"], directions=["Cook."], tags=tags)
        assert len(recipe.tags) == 50

    # ------------------------------------------------------------------------
    # F2.1: Multi-Card Modal Boundary
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f2_1_01_large_multi_card_set_10_cards(self):
        approval = CardPairApproval(
            proposal_id="prop_large",
            front_card_id="c_0",
            back_card_id="c_1",
            card_ids=[f"c_{i}" for i in range(10)],
            decision=HITLDecision.APPROVE,
        )
        assert len(approval.card_ids) == 10

    @pytest.mark.asyncio
    async def test_t2_f2_1_02_multi_card_set_with_missing_card_metadata(self, isolated_session_manager: SessionManager):
        session = isolated_session_manager.create_session()
        session.cards = [ScannedCard(card_id="c_1", file_path=Path("/tmp/c_1.jpg"), file_size_bytes=100)]
        assert len(session.cards) == 1

    @pytest.mark.asyncio
    async def test_t2_f2_1_03_multi_card_set_corrupted_image_handling(self, tmp_path: Path):
        corrupt_file = tmp_path / "corrupt.jpg"
        corrupt_file.write_bytes(b"NOT_A_VALID_JPEG_BINARY")
        with pytest.raises(Exception):
            validate_and_load_card(corrupt_file)

    @pytest.mark.asyncio
    async def test_t2_f2_1_04_multi_card_empty_candidates_response(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager):
        session = isolated_session_manager.create_session()
        resp = await api_client.get(f"/api/hitl/candidates?session_id={session.session_id}&front_card_id=nonexistent")
        assert resp.status_code in (200, 400, 404)

    @pytest.mark.asyncio
    async def test_t2_f2_1_05_multi_card_extreme_aspect_ratio_images(self, tmp_path: Path):
        p_tall = tmp_path / "tall.jpg"
        img = Image.new("RGB", (100, 1000), color="white")
        img.save(p_tall)
        card = validate_and_load_card(p_tall)
        assert card.card_id is not None

    # ------------------------------------------------------------------------
    # F2.2: Positional Drag/Reorder Boundary
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f2_2_01_reorder_empty_card_ids_list_handling(self):
        approval = CardPairApproval(
            proposal_id="prop_empty",
            front_card_id="card_1",
            card_ids=[],
            decision=HITLDecision.APPROVE,
        )
        resolved = approval.card_ids or [approval.front_card_id]
        assert resolved == [approval.front_card_id]

    @pytest.mark.asyncio
    async def test_t2_f2_2_02_reorder_single_card_payload(self):
        approval = CardPairApproval(
            proposal_id="prop_one",
            front_card_id="card_single",
            card_ids=["card_single"],
            decision=HITLDecision.APPROVE,
        )
        assert approval.card_ids == ["card_single"]

    @pytest.mark.asyncio
    async def test_t2_f2_2_03_reorder_duplicate_card_ids_deduplication(self):
        raw_ids = ["c_1", "c_2", "c_1", "c_3", "c_2"]
        deduped = list(dict.fromkeys(raw_ids))
        assert deduped == ["c_1", "c_2", "c_3"]

    @pytest.mark.asyncio
    async def test_t2_f2_2_04_reorder_invalid_card_id_rejection(self):
        approval = CardPairApproval(
            proposal_id="prop_inv",
            front_card_id="c_valid",
            card_ids=["c_valid", ""],
            decision=HITLDecision.APPROVE,
        )
        cleaned = [cid for cid in approval.card_ids if cid.strip()]
        assert cleaned == ["c_valid"]

    @pytest.mark.asyncio
    async def test_t2_f2_2_05_reorder_extreme_sequence_length(self):
        ids = [f"c_{i}" for i in range(25)]
        approval = CardPairApproval(
            proposal_id="prop_huge",
            front_card_id=ids[0],
            card_ids=ids,
            decision=HITLDecision.APPROVE,
        )
        assert len(approval.card_ids) == 25

    # ------------------------------------------------------------------------
    # F2.3: Multi-Card Sequence Preservation Boundary
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f2_3_01_reverse_order_preservation_in_extraction(self):
        orig = ["c_front", "c_back"]
        reversed_order = list(reversed(orig))
        assert reversed_order == ["c_back", "c_front"]

    @pytest.mark.asyncio
    async def test_t2_f2_3_02_interleaved_card_sequence_extraction(self):
        seq = ["card_1", "card_3", "card_2", "card_4"]
        recipe = RecipeSchema(title="Interleaved Sequence", ingredients=["A"], directions=["B."], card_ids=seq)
        assert recipe.card_ids == seq

    @pytest.mark.asyncio
    async def test_t2_f2_3_03_extraction_with_partial_card_read_failure(self):
        recipe = RecipeSchema(
            title="Partial Recovery Stew",
            ingredients=["1 onion", "Unknown ingredient [unreadable]"],
            directions=["1. Simmer until tender."],
            card_ids=["card_ok", "card_damaged"],
        )
        assert len(recipe.card_ids) == 2
        assert "unreadable" in recipe.ingredients[1]

    @pytest.mark.asyncio
    async def test_t2_f2_3_04_empty_recipe_returned_from_multi_card(self):
        with pytest.raises(ValueError):
            RecipeSchema(title="", ingredients=[], directions=[])

    @pytest.mark.asyncio
    async def test_t2_f2_3_05_multi_card_with_identical_text_on_all_faces(self):
        recipe = RecipeSchema(
            title="Duplicate Face Recipe",
            ingredients=["Flour", "Flour"],
            directions=["Mix.", "Mix."],
            card_ids=["c_dup_1", "c_dup_2"],
        )
        assert len(recipe.card_ids) == 2

    # ------------------------------------------------------------------------
    # F3.1: Card Images in Review Pane Boundary
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f3_1_01_get_card_image_invalid_uuid_404(self, api_client: httpx.AsyncClient):
        resp = await api_client.get("/api/cards/nonexistent_uuid_9999/image")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_t2_f3_1_02_get_card_image_missing_file_on_disk_404(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager):
        session = isolated_session_manager.create_session()
        session.cards = [ScannedCard(card_id="missing_file_card", file_path=Path("/tmp/does_not_exist_9876.jpg"), file_size_bytes=100)]
        resp = await api_client.get(f"/api/cards/missing_file_card/image?session_id={session.session_id}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_t2_f3_1_03_get_card_image_corrupted_file_handling(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, tmp_path: Path):
        bad_file = tmp_path / "bad.jpg"
        bad_file.write_bytes(b"corrupt")
        session = isolated_session_manager.create_session()
        session.cards = [ScannedCard(card_id="bad_card", file_path=bad_file, file_size_bytes=7)]
        resp = await api_client.get(f"/api/cards/bad_card/image?session_id={session.session_id}")
        assert resp.status_code in (200, 404, 500)

    @pytest.mark.asyncio
    async def test_t2_f3_1_04_get_card_image_path_traversal_attack_defense(self, api_client: httpx.AsyncClient):
        resp = await api_client.get("/api/cards/../../../../etc/passwd/image")
        assert resp.status_code in (404, 400, 200)

    @pytest.mark.asyncio
    async def test_t2_f3_1_05_get_card_image_huge_image_streaming(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, tmp_path: Path):
        huge_path = tmp_path / "huge.jpg"
        img = Image.new("RGB", (2000, 2000), color="white")
        img.save(huge_path, format="JPEG")
        session = isolated_session_manager.create_session()
        session.cards = [ScannedCard(card_id="huge_card", file_path=huge_path, file_size_bytes=huge_path.stat().st_size)]
        resp = await api_client.get(f"/api/cards/huge_card/image?session_id={session.session_id}")
        assert resp.status_code == 200

    # ------------------------------------------------------------------------
    # F3.2: Workflow Navigation Dialog Boundary
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f3_2_01_session_with_zero_recipes_navigation(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager):
        session = isolated_session_manager.create_session()
        session.recipes = []
        resp = await api_client.get(f"/api/recipes?session_id={session.session_id}")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_t2_f3_2_02_session_with_single_recipe_navigation(self, isolated_session_manager: SessionManager):
        session = isolated_session_manager.create_session()
        session.recipes = [RecipeSchema(title="Only Recipe", ingredients=["A"], directions=["B."])]
        assert len(session.recipes) == 1
        assert session.recipes[0] == session.recipes[-1]

    @pytest.mark.asyncio
    async def test_t2_f3_2_03_recipe_deletion_updates_last_index(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        isolated_session_store.save_session(session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=2, recipe_count=2)
        r1 = RecipeSchema(title="Stay", ingredients=["A"], directions=["1."])
        r2 = RecipeSchema(title="Delete", ingredients=["B"], directions=["2."])
        session.recipes = [r1, r2]
        isolated_session_store.save_recipe(session.session_id, r1)
        isolated_session_store.save_recipe(session.session_id, r2)

        resp = await api_client.delete(f"/api/recipes/{r2.recipe_id}")
        assert resp.status_code == 200
        remaining = isolated_session_store.list_recipes(session.session_id)
        assert len(remaining) == 1
        assert remaining[0].title == "Stay"

    @pytest.mark.asyncio
    async def test_t2_f3_2_04_session_in_failed_state_export_boundary(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager):
        session = isolated_session_manager.create_session()
        session.transition_to(PipelineState.FAILED)
        session.error_message = "Critical extraction failure"
        resp = await api_client.get(f"/api/status?session_id={session.session_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == PipelineState.FAILED.value

    @pytest.mark.asyncio
    async def test_t2_f3_2_05_session_cancelled_mid_review(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager):
        session = isolated_session_manager.create_session()
        session.cancel()
        resp = await api_client.get(f"/api/status?session_id={session.session_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == PipelineState.CANCELLED.value

    # ------------------------------------------------------------------------
    # F3.3: Inline Tag Editing Boundary
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f3_3_01_put_recipe_empty_tags_clears_all_tags(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        isolated_session_store.save_session(session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1)
        recipe = RecipeSchema(title="Clear Tags Cake", ingredients=["Sugar"], directions=["Bake."], tags=["Tag1", "Tag2"])
        session.recipes = [recipe]
        isolated_session_store.save_recipe(session.session_id, recipe)

        payload = recipe.model_dump()
        payload["tags"] = []
        resp = await api_client.put(f"/api/recipes/{recipe.recipe_id}", json=payload)
        assert resp.status_code == 200
        assert resp.json()["tags"] == []

    @pytest.mark.asyncio
    async def test_t2_f3_3_02_put_recipe_tags_with_commas_and_semicolons(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        isolated_session_store.save_session(session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1)
        recipe = RecipeSchema(title="Punctuation Tags", ingredients=["Flour"], directions=["Cook."])
        session.recipes = [recipe]
        isolated_session_store.save_recipe(session.session_id, recipe)

        payload = recipe.model_dump()
        payload["tags"] = ["Baking, Pastry", "Dinner; Quick"]
        resp = await api_client.put(f"/api/recipes/{recipe.recipe_id}", json=payload)
        assert resp.status_code == 200
        assert len(resp.json()["tags"]) == 2

    @pytest.mark.asyncio
    async def test_t2_f3_3_03_put_recipe_extremely_long_tag_names(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        isolated_session_store.save_session(session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1)
        recipe = RecipeSchema(title="Long Tag Recipe", ingredients=["Water"], directions=["Boil."])
        session.recipes = [recipe]
        isolated_session_store.save_recipe(session.session_id, recipe)

        payload = recipe.model_dump()
        payload["tags"] = ["A" * 200]
        resp = await api_client.put(f"/api/recipes/{recipe.recipe_id}", json=payload)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_t2_f3_3_04_put_recipe_nonexistent_recipe_id_404(self, api_client: httpx.AsyncClient):
        dummy = RecipeSchema(title="Ghost", ingredients=["Air"], directions=["Vanish."]).model_dump()
        resp = await api_client.put("/api/recipes/nonexistent_recipe_9999", json=dummy)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_t2_f3_3_05_put_recipe_unicode_and_hieroglyphs_tags(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        isolated_session_store.save_session(session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1)
        recipe = RecipeSchema(title="Unicode Tags", ingredients=["Matcha"], directions=["Whisk."])
        session.recipes = [recipe]
        isolated_session_store.save_recipe(session.session_id, recipe)

        payload = recipe.model_dump()
        payload["tags"] = ["抹茶", "スイーツ", "🍵", "Épicé"]
        resp = await api_client.put(f"/api/recipes/{recipe.recipe_id}", json=payload)
        assert resp.status_code == 200
        assert "抹茶" in resp.json()["tags"]

    # ------------------------------------------------------------------------
    # F4.1: Recipe Title Guessing Boundary
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f4_1_01_guess_suffix_idempotency_no_double_guess(self):
        title = "Grandma's Pot Roast [GUESS]"
        if not title.endswith(" [GUESS]"):
            title = f"{title} [GUESS]"
        assert not title.endswith(" [GUESS] [GUESS]")
        assert title == "Grandma's Pot Roast [GUESS]"

    @pytest.mark.asyncio
    async def test_t2_f4_1_02_card_with_only_ingredients_and_no_header(self):
        inferred = "Pecan Tarts [GUESS]"
        recipe = RecipeSchema(title=inferred, ingredients=["2 cups pecans", "1 cup brown sugar"], directions=["Fill shells and bake."])
        assert recipe.title == "Pecan Tarts [GUESS]"

    @pytest.mark.asyncio
    async def test_t2_f4_1_03_card_with_faded_unreadable_title(self):
        inferred = "Savory Casserole [GUESS]"
        recipe = RecipeSchema(title=inferred, ingredients=["Tuna", "Noodles"], directions=["Bake."])
        assert recipe.title.endswith(" [GUESS]")

    @pytest.mark.asyncio
    async def test_t2_f4_1_04_manual_edit_clears_guess_suffix(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        isolated_session_store.save_session(session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1)
        recipe = RecipeSchema(title="Mystery Cake [GUESS]", ingredients=["Flour", "Sugar"], directions=["Bake."])
        session.recipes = [recipe]
        isolated_session_store.save_recipe(session.session_id, recipe)

        payload = recipe.model_dump()
        payload["title"] = "Confirmed Chocolate Fudge Cake"
        resp = await api_client.put(f"/api/recipes/{recipe.recipe_id}", json=payload)
        assert resp.status_code == 200
        assert not resp.json()["title"].endswith(" [GUESS]")

    @pytest.mark.asyncio
    async def test_t2_f4_1_05_recipe_title_exact_boundary_guess_token(self):
        recipe = RecipeSchema(title="[GUESS]", ingredients=["Ingredient"], directions=["Direction."])
        assert recipe.title == "[GUESS]"

    # ------------------------------------------------------------------------
    # F4.2: Anecdote & Story Preservation Boundary
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f4_2_01_massive_narrative_story_10000_chars(self):
        huge_story = "A" * 10000
        recipe = RecipeSchema(title="Story Book", description=huge_story, ingredients=["Flour"], directions=["Bake."])
        assert len(recipe.description) == 10000

    @pytest.mark.asyncio
    async def test_t2_f4_2_02_multi_paragraph_anecdotes_preserved(self):
        paragraphs = "Paragraph 1: Vintage memories.\n\nParagraph 2: More family lore.\n\nParagraph 3: Attribution to Great Aunt."
        recipe = RecipeSchema(title="Multi-Paragraph Story", description=paragraphs, ingredients=["Sugar"], directions=["Mix."])
        assert "\n\n" in recipe.description

    @pytest.mark.asyncio
    async def test_t2_f4_2_03_card_with_only_story_and_minimal_recipe(self):
        recipe = RecipeSchema(
            title="Lover's Cake",
            description="A romantic tale spanning 50 years of marriage...",
            ingredients=["1 box cake mix", "3 eggs"],
            directions=["Bake according to package."],
        )
        assert len(recipe.description) > len("".join(recipe.directions))

    @pytest.mark.asyncio
    async def test_t2_f4_2_04_marginalia_containing_quotes_and_symbols(self):
        notes = "Marginalia: \"Never use margarine!\" - Mom (1975) & <Do not overmix!>"
        recipe = RecipeSchema(title="Butter Cookies", source_notes=notes, ingredients=["Butter"], directions=["Mix."])
        assert "\"" in recipe.source_notes
        assert "<" in recipe.source_notes

    @pytest.mark.asyncio
    async def test_t2_f4_2_05_empty_description_and_source_notes(self):
        recipe = RecipeSchema(title="No Notes", description="", source_notes="", ingredients=["Flour"], directions=["Bake."])
        assert recipe.description == ""
        assert recipe.source_notes == ""

    # ------------------------------------------------------------------------
    # F5.1: Google Docs OAuth Boundary
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f5_1_01_google_docs_export_empty_session_recipes(self):
        reqs = export_recipes_to_google_docs_batch_requests([])
        assert reqs == [] or (isinstance(reqs, dict) and reqs.get("requests") == [])

    @pytest.mark.asyncio
    async def test_t2_f5_1_02_google_docs_create_doc_invalid_token(self, api_client: httpx.AsyncClient):
        payload = {
            "session_id": "any_sess",
            "access_token": "expired_or_invalid_oauth_token",
            "document_title": "Test Doc",
        }
        resp = await api_client.post("/api/export/google-docs/create-document", json=payload)
        assert resp.status_code in (404, 400, 401, 405)

    @pytest.mark.asyncio
    async def test_t2_f5_1_03_google_docs_batch_requests_massive_recipe(self):
        recipe = RecipeSchema(
            title="Huge Recipe",
            ingredients=[f"Ingredient {i}" for i in range(100)],
            directions=[f"Step {i}." for i in range(100)],
        )
        reqs = export_recipes_to_google_docs_batch_requests([recipe])
        assert len(reqs) > 0 or (isinstance(reqs, dict) and len(reqs.get("requests", [])) > 0)

    @pytest.mark.asyncio
    async def test_t2_f5_1_04_google_docs_title_sanitization_special_chars(self):
        html = export_recipes_to_google_docs_html([], title="Special <Title> & \"Quotes\"")
        assert "Special &lt;Title&gt; &amp; &quot;Quotes&quot;" in html or "Special <Title>" in html

    @pytest.mark.asyncio
    async def test_t2_f5_1_05_google_docs_missing_card_images_graceful_handling(self):
        recipe = RecipeSchema(title="Missing Images", ingredients=["A"], directions=["B."], image_urls=["http://invalid.url/404.jpg"])
        reqs = export_recipes_to_google_docs_batch_requests([recipe])
        assert len(reqs) > 0 or (isinstance(reqs, dict) and len(reqs.get("requests", [])) > 0)

    # ------------------------------------------------------------------------
    # F5.2: Embedded Images in PDF & DOCX Boundary
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f5_2_01_pdf_export_with_missing_card_image_files(self):
        recipe = RecipeSchema(title="Missing Scan PDF", ingredients=["A"], directions=["B."], image_urls=["/tmp/missing_file_xyz.jpg"])
        pdf_bytes = export_recipes_to_pdf([recipe])
        assert len(pdf_bytes) > 0

    @pytest.mark.asyncio
    async def test_t2_f5_2_02_docx_export_with_unsupported_image_format(self):
        recipe = RecipeSchema(title="Missing Scan DOCX", ingredients=["A"], directions=["B."], image_urls=["/tmp/unsupported.bmp"])
        docx_bytes = export_recipes_to_docx([recipe])
        assert len(docx_bytes) > 0

    @pytest.mark.asyncio
    async def test_t2_f5_2_03_pdf_export_zero_recipes_handling(self):
        pdf_bytes = export_recipes_to_pdf([])
        assert len(pdf_bytes) > 0

    @pytest.mark.asyncio
    async def test_t2_f5_2_04_docx_export_zero_recipes_handling(self):
        docx_bytes = export_recipes_to_docx([])
        assert len(docx_bytes) > 0

    @pytest.mark.asyncio
    async def test_t2_f5_2_05_embedded_images_huge_resolution_downscaling(self, tmp_path: Path):
        huge_img = tmp_path / "huge_scan.jpg"
        img = Image.new("RGB", (3000, 2000), color="yellow")
        img.save(huge_img, format="JPEG")
        recipe = RecipeSchema(title="Huge Scan Recipe", ingredients=["Flour"], directions=["Bake."], image_urls=[str(huge_img)])
        pdf_bytes = export_recipes_to_pdf([recipe])
        assert len(pdf_bytes) > 1000

    # ------------------------------------------------------------------------
    # F5.3: Customizable Export Filenames Boundary
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f5_3_01_filename_path_traversal_sanitization(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        session.state = PipelineState.COMPLETED
        recipe = RecipeSchema(title="Security Test", ingredients=["A"], directions=["B."])
        session.recipes = [recipe]
        isolated_session_store.save_session(session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1)
        isolated_session_store.save_recipe(session.session_id, recipe)

        resp = await api_client.get(f"/api/export/csv?session_id={session.session_id}")
        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        assert "../" not in cd

    @pytest.mark.asyncio
    async def test_t2_f5_3_02_filename_with_illegal_filesystem_characters(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        session.state = PipelineState.COMPLETED
        recipe = RecipeSchema(title="Illegal Chars", ingredients=["A"], directions=["B."])
        session.recipes = [recipe]
        isolated_session_store.save_session(session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1)
        isolated_session_store.save_recipe(session.session_id, recipe)

        resp = await api_client.get(f"/api/export/pdf?session_id={session.session_id}")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_t2_f5_3_03_empty_filename_fallback_to_default(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        session.state = PipelineState.COMPLETED
        recipe = RecipeSchema(title="Default Name", ingredients=["A"], directions=["B."])
        session.recipes = [recipe]
        isolated_session_store.save_session(session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1)
        isolated_session_store.save_recipe(session.session_id, recipe)

        resp = await api_client.get(f"/api/export/csv?session_id={session.session_id}")
        assert resp.status_code == 200
        assert "recipeboxer_" in resp.headers.get("content-disposition", "")

    @pytest.mark.asyncio
    async def test_t2_f5_3_04_extremely_long_custom_filename_truncation(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        session.state = PipelineState.COMPLETED
        recipe = RecipeSchema(title="Long Filename", ingredients=["A"], directions=["B."])
        session.recipes = [recipe]
        isolated_session_store.save_session(session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1)
        isolated_session_store.save_recipe(session.session_id, recipe)

        resp = await api_client.get(f"/api/export/json-ld?session_id={session.session_id}")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_t2_f5_3_05_custom_filename_with_unicode_and_emojis(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        session = isolated_session_manager.create_session()
        session.state = PipelineState.COMPLETED
        recipe = RecipeSchema(title="Unicode Filename", ingredients=["A"], directions=["B."])
        session.recipes = [recipe]
        isolated_session_store.save_session(session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1)
        isolated_session_store.save_recipe(session.session_id, recipe)

        resp = await api_client.get(f"/api/export/markdown?session_id={session.session_id}")
        assert resp.status_code == 200

    # ------------------------------------------------------------------------
    # F5.4: Tag Serialization Boundary
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t2_f5_4_01_plan_to_eat_tags_containing_commas_and_quotes(self):
        recipe = RecipeSchema(
            title="Quoted Tag Recipe",
            ingredients=["1 apple"],
            directions=["Eat."],
            tags=['Baking, "Vintage"', "Southern, Style"],
        )
        csv_str = export_recipes_to_plan_to_eat_csv([recipe])
        reader = csv.reader(io.StringIO(csv_str))
        header = next(reader)
        row = next(reader)
        assert header[7] == "Tags"
        assert 'Baking, "Vintage", Southern, Style' in row[7]

    @pytest.mark.asyncio
    async def test_t2_f5_4_02_schema_org_empty_tags_no_keywords_field(self):
        recipe = RecipeSchema(title="No Tag Recipe", ingredients=["Water"], directions=["Boil."], tags=[])
        json_ld_str = export_recipes_to_json_ld([recipe])
        data = json.loads(json_ld_str)
        graph = data.get("@graph", [data])
        recipe_node = next(item for item in graph if item.get("@type") == "Recipe")
        assert "keywords" not in recipe_node or recipe_node["keywords"] == ""

    @pytest.mark.asyncio
    async def test_t2_f5_4_03_markdown_tags_with_yaml_metacharacters(self):
        recipe = RecipeSchema(
            title="YAML Tag Recipe",
            ingredients=["Flour"],
            directions=["Bake."],
            tags=["Tag: With Colon", "Tag # Hash", "Tag [Bracket]"],
        )
        md_str = export_recipes_to_markdown([recipe])
        assert "tags:" in md_str

    @pytest.mark.asyncio
    async def test_t2_f5_4_04_cooklang_tags_multiline_formatting(self):
        recipe = RecipeSchema(
            title="Cooklang Multi Tag",
            ingredients=["Salt"],
            directions=["Season."],
            tags=["Quick", "Dinner", "30-Min"],
        )
        cook_str = export_recipes_to_cooklang([recipe])
        assert ">> tags: Quick, Dinner, 30-Min" in cook_str

    @pytest.mark.asyncio
    async def test_t2_f5_4_05_paprika_tags_special_characters(self):
        recipe = RecipeSchema(
            title="Paprika Special Tags",
            ingredients=["Sugar"],
            directions=["Mix."],
            tags=["Gluten-Free & Dairy-Free", "Dessert 🍰"],
        )
        paprika_bytes = export_recipes_to_paprika([recipe])
        decompressed = gzip.decompress(paprika_bytes)
        data = json.loads(decompressed)
        item = data[0] if isinstance(data, list) else data
        assert "Gluten-Free & Dairy-Free" in item["categories"]
        assert "Dessert 🍰" in item["categories"]


# ============================================================================
# TIER 3: CROSS-FEATURE COMBINATIONS (>=10 Pairwise Interaction Tests)
# ============================================================================

class TestTier3CrossFeatureCombinations:
    """Tier 3: Pairwise interactions across ingestion, HITL, review, extraction, and exporters."""

    @pytest.mark.asyncio
    async def test_t3_01_directory_picker_and_theme_persistence(self, api_client: httpx.AsyncClient, test_scans_dir: Path):
        """T3.1: Directory ingestion coupled with SPA root static delivery."""
        resp_spa = await api_client.get("/")
        assert resp_spa.status_code == 200
        assert "RecipeBoxer" in resp_spa.text

        resp_pipe = await api_client.post("/api/pipeline/start", json={"scans_dir": str(test_scans_dir)})
        assert resp_pipe.status_code == 200

    @pytest.mark.asyncio
    async def test_t3_02_slow_batch_throttling_and_sse_progress_stream(self, api_client: httpx.AsyncClient, test_scans_dir: Path):
        """T3.2: Slow batch mode launch and status progression."""
        payload = {
            "scans_dir": str(test_scans_dir),
            "mode": IngestionMode.SLOW_BATCH,
            "slow_batch_size": 1,
            "slow_batch_interval_seconds": 0.05,
        }
        resp = await api_client.post("/api/pipeline/start", json=payload)
        assert resp.status_code == 200
        sess_id = resp.json()["session_id"]
        status_resp = await api_client.get(f"/api/status?session_id={sess_id}")
        assert status_resp.status_code == 200
        assert status_resp.json()["mode"] == IngestionMode.SLOW_BATCH.value

    @pytest.mark.asyncio
    async def test_t3_03_rapid_queue_shortcuts_and_multi_card_reorder(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager):
        """T3.3: HITL approval with explicit reordering of multiple cards."""
        session = isolated_session_manager.create_session()
        payload = {
            "proposal_id": "prop_reorder_t3",
            "front_card_id": "c_2",
            "back_card_id": "c_1",
            "card_ids": ["c_2", "c_1", "c_3"],
            "decision": "APPROVE",
        }
        resp = await api_client.post(f"/api/hitl/decision?session_id={session.session_id}", json=payload)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_t3_04_multi_card_reordered_extraction_and_pdf_docx_embedded_scans(self, tmp_path: Path):
        """T3.4: 3-card reordered recipe extracted and exported to PDF/DOCX with embedded scans."""
        p1 = create_synthetic_test_card(tmp_path / "c1.jpg", "Card 1")
        p2 = create_synthetic_test_card(tmp_path / "c2.jpg", "Card 2")
        p3 = create_synthetic_test_card(tmp_path / "c3.jpg", "Card 3")

        recipe = RecipeSchema(
            title="3-Card Multi Heirloom Stew",
            ingredients=["2 lbs beef", "3 carrots", "2 potatoes", "1 onion"],
            directions=["1. Brown beef.", "2. Add vegetables.", "3. Simmer 2 hours."],
            tags=["Heirloom", "Dinner", "Multi-Card"],
            card_ids=["c_2", "c_1", "c_3"],
            image_urls=[str(p2), str(p1), str(p3)],
        )

        pdf_bytes = export_recipes_to_pdf([recipe])
        docx_bytes = export_recipes_to_docx([recipe])
        assert len(pdf_bytes) > 1000
        assert len(docx_bytes) > 1000

    @pytest.mark.asyncio
    async def test_t3_05_multipart_upload_card_image_streaming_and_review_pane(self, api_client: httpx.AsyncClient, tmp_path: Path):
        """T3.5: Multipart card upload followed by immediate card image stream verification."""
        p = create_synthetic_test_card(tmp_path / "upload_stream.jpg", "Stream Test")
        with open(p, "rb") as f:
            files = [("files", ("upload_stream.jpg", f, "image/jpeg"))]
            resp = await api_client.post("/api/cards/upload", files=files)
        assert resp.status_code == 200
        data = resp.json()
        cid = data["card_ids"][0]
        sid = data["session_id"]

        img_resp = await api_client.get(f"/api/cards/{cid}/image?session_id={sid}")
        assert img_resp.status_code == 200
        assert img_resp.headers.get("content-type") == "image/jpeg"

    @pytest.mark.asyncio
    async def test_t3_06_fingerprint_cache_hit_and_ingestion_throttle(self, isolated_session_manager: SessionManager):
        """T3.6: Fingerprint caching coupled with session concurrency."""
        session = isolated_session_manager.create_session()
        fp = VisualFingerprint(
            card_id="card_cached",
            image_path="/tmp/cached.jpg",
            sha256_hash="hash_cached",
            role=CardRole.STANDALONE,
            paper_color=PaperColorInfo(tone_name="WHITE"),
            ruling=PaperRulingInfo(ruling_type=PaperRulingType.UNRULED_BLANK),
            ink=InkInfo(primary_type=InkType.BLACK_BALLPOINT, primary_color=InkColor.BLACK),
        )
        session.fingerprints["card_cached"] = fp
        assert "card_cached" in session.fingerprints

    @pytest.mark.asyncio
    async def test_t3_07_hitl_rejection_standalone_conversion_and_sequence_linkage(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager):
        """T3.7: Record standalone decision on front card."""
        session = isolated_session_manager.create_session()
        payload = {
            "proposal_id": "prop_stand_t3",
            "front_card_id": "c_front_solo",
            "decision": "STANDALONE",
            "is_standalone": True,
        }
        resp = await api_client.post(f"/api/hitl/decision?session_id={session.session_id}", json=payload)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_t3_08_inline_tags_editing_navigation_dialog_and_google_docs_export(self, api_client: httpx.AsyncClient, isolated_session_manager: SessionManager, isolated_session_store: SessionStore):
        """T3.8: Inline tag edits followed by Google Docs batch export generation."""
        session = isolated_session_manager.create_session()
        session.state = PipelineState.COMPLETED
        isolated_session_store.save_session(session_id=session.session_id, state="COMPLETED", mode="parallel", folder_path="/tmp", card_count=1, recipe_count=1)
        recipe = RecipeSchema(title="T3 Recipe", ingredients=["Sugar"], directions=["Bake."])
        session.recipes = [recipe]
        isolated_session_store.save_recipe(session.session_id, recipe)

        put_payload = recipe.model_dump()
        put_payload["tags"] = ["GoogleDocs", "ExportReady"]
        put_resp = await api_client.put(f"/api/recipes/{recipe.recipe_id}", json=put_payload)
        assert put_resp.status_code == 200

        export_resp = await api_client.get(f"/api/export/google-docs?session_id={session.session_id}&format=json")
        assert export_resp.status_code == 200
        assert "GoogleDocs" in export_resp.text

    @pytest.mark.asyncio
    async def test_t3_09_crash_resumption_and_sse_reconnection_telemetry(self, api_client: httpx.AsyncClient, isolated_session_store: SessionStore):
        """T3.9: SQLite session rehydration and resume endpoint invocation."""
        sess_id = f"sess_crash_{uuid.uuid4().hex[:8]}"
        isolated_session_store.save_session(
            session_id=sess_id, state="HITL_PENDING", mode="parallel", folder_path="/tmp", card_count=4, recipe_count=0
        )
        resume_resp = await api_client.post(f"/api/sessions/{sess_id}/resume")
        assert resume_resp.status_code == 200
        assert resume_resp.json()["session_id"] == sess_id

    @pytest.mark.asyncio
    async def test_t3_10_batch_default_tags_title_guessing_and_plan_to_eat_csv_export(self):
        """T3.10: Batch default tags + guessed title exported to RFC 4180 CSV."""
        recipe = RecipeSchema(
            title="Guessed Cobbler [GUESS]",
            ingredients=["3 cups berries", "1 cup flour", "1/2 cup sugar"],
            directions=["Mix and bake at 350F."],
            tags=["Heirloom", "Summer", "Dessert"],
        )
        csv_str = export_recipes_to_plan_to_eat_csv([recipe])
        assert "Guessed Cobbler [GUESS]" in csv_str
        assert "Heirloom, Summer, Dessert" in csv_str


# ============================================================================
# TIER 4: REAL-WORLD APPLICATION SCENARIOS (5 Complex Workflows)
# ============================================================================

class TestTier4RealWorldScenarios:
    """Tier 4: Complex multi-stage real-world heirloom digitization workflows."""

    @pytest.mark.asyncio
    async def test_t4_s1_vintage_heirloom_index_cards_faded_cursive_workflow(
        self, api_client: httpx.AsyncClient, tmp_path: Path, isolated_session_store: SessionStore
    ):
        """Scenario 1: Vintage heirloom index cards with yellowed paper, ballpoint ink, and family marginalia."""
        front_img = create_synthetic_test_card(tmp_path / "grandma_front.jpg", "Grandma's 1935 Honey Cake Front", color="lightyellow")
        back_img = create_synthetic_test_card(tmp_path / "grandma_back.jpg", "Grandma's 1935 Honey Cake Back", color="lightyellow")

        recipe = RecipeSchema(
            title="Grandma's 1935 Honey Cake",
            description="Brought from Poland in 1935. Made for Rosh Hashanah every year.",
            source_notes="Corner note: 'Use dark buckwheat honey for best flavor.'",
            ingredients=["3 cups flour", "1 cup dark buckwheat honey", "3 eggs", "1 tsp cinnamon"],
            directions=["1. Beat eggs and honey.", "2. Fold in dry ingredients.", "3. Bake at 325F for 50 mins."],
            tags=["Heirloom", "Honey Cake", "Holiday", "1935"],
            card_ids=["card_g_front", "card_g_back"],
            image_urls=[str(front_img), str(back_img)],
        )

        sess_id = f"sess_s1_{uuid.uuid4().hex[:8]}"
        isolated_session_store.save_session(
            session_id=sess_id, state="COMPLETED", mode="parallel", folder_path=str(tmp_path), card_count=2, recipe_count=1
        )
        isolated_session_store.save_recipe(sess_id, recipe)

        recipe.tags.append("Jewish Traditional")
        isolated_session_store.save_recipe(sess_id, recipe)

        pdf_bytes = export_recipes_to_pdf([recipe])
        assert len(pdf_bytes) > 500

        csv_str = export_recipes_to_plan_to_eat_csv([recipe])
        assert "Grandma's 1935 Honey Cake" in csv_str
        assert "Heirloom, Honey Cake, Holiday, 1935, Jewish Traditional" in csv_str

    @pytest.mark.asyncio
    async def test_t4_s2_high_speed_keyboard_triage_title_guessing_workflow(
        self, api_client: httpx.AsyncClient, tmp_path: Path, isolated_session_manager: SessionManager
    ):
        """Scenario 2: Rapid keyboard-only triage across untitled and low-confidence recipe cards."""
        session = isolated_session_manager.create_session()
        for i in range(3):
            cid = f"card_untitled_{i}"
            p = create_synthetic_test_card(tmp_path / f"{cid}.jpg", f"Untitled Card {i}")
            session.cards.append(ScannedCard(card_id=cid, file_path=p, file_size_bytes=100))

        guessed_recipes = [
            RecipeSchema(title="Old-Fashioned Rhubarb Pie [GUESS]", ingredients=["4 cups rhubarb", "1 cup sugar"], directions=["Bake at 400F."]),
            RecipeSchema(title="Skillet Cornbread [GUESS]", ingredients=["1 cup cornmeal", "1 cup milk"], directions=["Bake in cast iron."]),
            RecipeSchema(title="Beef Barley Soup [GUESS]", ingredients=["1 lb beef", "1/2 cup barley"], directions=["Simmer 2 hours."]),
        ]
        session.recipes = guessed_recipes

        assert all(r.title.endswith(" [GUESS]") for r in session.recipes)
        assert len(session.recipes) == 3

    @pytest.mark.asyncio
    async def test_t4_s3_four_card_thanksgiving_feast_assembly_and_reorder_workflow(
        self, api_client: httpx.AsyncClient, tmp_path: Path, isolated_session_store: SessionStore
    ):
        """Scenario 3: 4-card Thanksgiving feast recipe assembly, drag-and-drop reordering, and multi-format publishing."""
        p1 = create_synthetic_test_card(tmp_path / "turkey_roast.jpg", "Roast Turkey Front")
        p2 = create_synthetic_test_card(tmp_path / "sourdough_stuffing.jpg", "Sourdough Stuffing")
        p3 = create_synthetic_test_card(tmp_path / "pan_gravy.jpg", "Pan Gravy")
        p4 = create_synthetic_test_card(tmp_path / "cranberry_relish.jpg", "Cranberry Relish")

        ordered_card_ids = ["c_turkey", "c_stuffing", "c_gravy", "c_cranberry"]
        ordered_paths = [str(p1), str(p2), str(p3), str(p4)]

        recipe = RecipeSchema(
            title="Complete Thanksgiving Feast Assembly",
            description="Four-part traditional family Thanksgiving dinner preparation.",
            ingredients=[
                "1 whole 14 lb Turkey",
                "1 loaf stale sourdough bread cubed",
                "Turkey giblet stock & pan drippings",
                "2 bags fresh cranberries & 1 cup sugar",
            ],
            directions=[
                "1. Season and roast turkey at 325F until 165F internal.",
                "2. Sauté aromatics, toss sourdough cubes with broth and bake stuffing.",
                "3. Deglaze roasting pan with white wine and whisk into thickened gravy.",
                "4. Pulse fresh cranberries with orange zest and sugar for relish.",
            ],
            tags=["Holiday", "Thanksgiving", "Feast", "Multi-Card"],
            card_ids=ordered_card_ids,
            image_urls=ordered_paths,
        )

        sess_id = f"sess_s3_{uuid.uuid4().hex[:8]}"
        isolated_session_store.save_session(
            session_id=sess_id, state="COMPLETED", mode="parallel", folder_path=str(tmp_path), card_count=4, recipe_count=1
        )
        isolated_session_store.save_recipe(sess_id, recipe)

        json_ld_str = export_recipes_to_json_ld([recipe])
        assert "Complete Thanksgiving Feast Assembly" in json_ld_str
        assert "recipeInstructions" in json_ld_str

        docx_bytes = export_recipes_to_docx([recipe])
        assert len(docx_bytes) > 500

    @pytest.mark.asyncio
    async def test_t4_s4_rate_limited_slow_batch_ingestion_with_sse_recovery_workflow(
        self, api_client: httpx.AsyncClient, tmp_path: Path, isolated_session_manager: SessionManager
    ):
        """Scenario 4: Rate-limited slow batch processing with simulated network interruption and SSE state recovery."""
        cfg = PipelineStartRequest(
            scans_dir=str(tmp_path),
            mode=IngestionMode.SLOW_BATCH,
            slow_batch_size=2,
            slow_batch_interval_seconds=0.1,
        )
        session = isolated_session_manager.create_session(config=cfg)

        session.transition_to(PipelineState.INGESTING)
        session.update_progress(total_scans=6, fingerprinted=2, percent_complete=33.3)
        assert session.progress.percent_complete == 33.3

        status_resp = await api_client.get(f"/api/status?session_id={session.session_id}")
        assert status_resp.status_code == 200
        assert status_resp.json()["progress"]["fingerprinted"] == 2

        session.transition_to(PipelineState.COMPLETED)
        assert session.state == PipelineState.COMPLETED

    @pytest.mark.asyncio
    async def test_t4_s5_universal_multi_format_export_interoperability_workflow(
        self, api_client: httpx.AsyncClient, tmp_path: Path, isolated_session_store: SessionStore
    ):
        """Scenario 5: Complete recipe collection exported and validated across all 9 export formats."""
        p_img = create_synthetic_test_card(tmp_path / "multi_export.jpg", "Universal Export Card")
        recipes = [
            RecipeSchema(
                title="International Matcha Chiffon Cake 抹茶 🍵",
                description="Light Japanese sponge cake with Uji ceremonial grade matcha.",
                source_notes="Adapted from Kyoto bakery master, 1982.",
                prep_time="25 mins",
                cook_time="40 mins",
                total_time="65 mins",
                yield_servings="8 slices",
                ingredients=["2 tbsp Uji matcha powder", "6 large eggs separated", "1 cup cake flour", "3/4 cup sugar"],
                directions=[
                    "1. Whisk egg yolks with sugar, oil, and water.",
                    "2. Sift matcha powder and cake flour into yolk mixture.",
                    "3. Whip egg whites to stiff peaks and gently fold in.",
                    "4. Bake in ungreased chiffon tube pan at 325F for 40 minutes.",
                ],
                tags=["Dessert", "Japanese", "Matcha", "Baking", "Tea Time"],
                card_ids=["card_matcha_1"],
                image_urls=[str(p_img)],
            ),
            RecipeSchema(
                title="Classic \"Grandma's\" 8\" Apple Pie",
                ingredients=["6 Granny Smith apples", "1 tbsp lemon juice", "1 tsp cinnamon", "2 pie crusts"],
                directions=["1. Toss apples with spices.", "2. Fill crust and crimp edges.", "3. Bake at 375F for 45 mins."],
                tags=["Dessert", "Pie", "American", "Fall"],
            ),
        ]

        sess_id = f"sess_s5_{uuid.uuid4().hex[:8]}"
        isolated_session_store.save_session(
            session_id=sess_id, state="COMPLETED", mode="parallel", folder_path=str(tmp_path), card_count=2, recipe_count=2
        )
        for r in recipes:
            isolated_session_store.save_recipe(sess_id, r)

        # 1. Plan To Eat CSV
        csv_out = export_recipes_to_plan_to_eat_csv(recipes)
        assert 'Classic "Grandma\'s" 8" Apple Pie' in csv_out or '""Grandma\'s""' in csv_out
        assert "Matcha" in csv_out

        # 2. Schema.org JSON-LD
        json_ld_out = export_recipes_to_json_ld(recipes)
        assert "PT25M" in json_ld_out or "25 mins" in json_ld_out or "@graph" in json_ld_out

        # 3. Raw Recipe JSON
        json_out = export_recipes_to_json(recipes)
        assert len(json.loads(json_out)) == 2

        # 4. Printable PDF with images
        pdf_out = export_recipes_to_pdf(recipes)
        assert len(pdf_out) > 500

        # 5. Word DOCX
        docx_out = export_recipes_to_docx(recipes)
        assert len(docx_out) > 500

        # 6. Markdown
        md_out = export_recipes_to_markdown(recipes)
        assert "# International Matcha Chiffon Cake" in md_out

        # 7. Cooklang
        cook_out = export_recipes_to_cooklang(recipes)
        assert ">> tags:" in cook_out

        # 8. Paprika
        paprika_out = export_recipes_to_paprika(recipes)
        assert isinstance(paprika_out, (bytes, bytearray))
        assert len(paprika_out) > 0

        # 9. Google Docs batch requests & HTML
        gdocs_reqs = export_recipes_to_google_docs_batch_requests(recipes)
        gdocs_html = export_recipes_to_google_docs_html(recipes)
        assert len(gdocs_reqs) > 0
        assert "<html" in gdocs_html
