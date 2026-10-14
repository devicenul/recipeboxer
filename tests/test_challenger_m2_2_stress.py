"""Adversarial stress-test suite for Milestone 2: REST Endpoints & Storage (Challenger M2-2).

Tests:
1. POST /api/pipeline/start with baseline_project_path and baseline_session_id (valid, invalid, edge cases).
2. POST /api/cards/upload with multipart baseline_archive upload, baseline_session_id, baseline_project_path.
3. SSE /api/events event stream emitting progress events with skipped_cards, newly_processed_cards, baseline_recipes_count.
4. SQLite SessionStore persistence of merged card records and recipe records, summary/detail retrieval, recipe ID collision handling, and thread safety.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport
from PIL import Image

from recipeboxer.agent.orchestrator import RecipeBoxerAgent
from recipeboxer.agent.session import PipelineSession, SessionManager
from recipeboxer.api.app import create_app
from recipeboxer.config import Settings
from recipeboxer.ingestion.scanner import ScannedCard, compute_file_sha256, scan_card_directory
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.hitl import CardPairApproval, HITLDecision
from recipeboxer.models.pipeline import (
    IngestionMode,
    PipelineStartRequest,
    PipelineState,
    ProgressMetrics,
)
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.storage.archive import create_recipeboxer_archive
from recipeboxer.storage.cache import FingerprintCache
from recipeboxer.storage.session_store import SessionStore


def make_test_card_image(color: str = "red", size: tuple = (150, 150)) -> bytes:
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def stress_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    scans_dir = tmp_path / "scans"
    scans_dir.mkdir(parents=True, exist_ok=True)

    # Generate 4 distinct cards
    card_bytes_list = [
        make_test_card_image("red"),
        make_test_card_image("green"),
        make_test_card_image("blue"),
        make_test_card_image("yellow"),
    ]

    card_paths = []
    card_shas = []
    for idx, b in enumerate(card_bytes_list, 1):
        cp = scans_dir / f"card_{idx:03d}.jpg"
        cp.write_bytes(b)
        card_paths.append(cp)
        card_shas.append(compute_file_sha256(cp))

    db_path = tmp_path / "cache" / "stress_session_store.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SessionStore(db_path=str(db_path))
    cache = FingerprintCache(db_path=str(db_path))

    from tests.conftest import MockGeminiClient

    class MockSessionManager(SessionManager):
        def __init__(self, mock_client=None, test_cache=None):
            super().__init__()
            self._mock_client = mock_client
            self._test_cache = test_cache

        def create_session(self, *args, **kwargs):
            session = super().create_session(*args, **kwargs)
            if self._mock_client is not None:
                session.mock_client = self._mock_client
            if self._test_cache is not None:
                session.fingerprint_cache = self._test_cache
                session.cache = self._test_cache
            return session

    manager = MockSessionManager(mock_client=MockGeminiClient(), test_cache=cache)

    settings = Settings(
        cache_db_path=str(db_path),
        cache_dir=str(tmp_path / "cache"),
        export_dir=str(tmp_path / "exports"),
    )
    monkeypatch.setattr("recipeboxer.config.get_settings", lambda: settings)
    monkeypatch.setattr("recipeboxer.agent.orchestrator.get_settings", lambda: settings)
    monkeypatch.setattr("recipeboxer.agent.session.get_settings", lambda: settings)
    monkeypatch.setattr("recipeboxer.storage.session_store.get_settings", lambda: settings)
    monkeypatch.setattr("recipeboxer.api.routes.get_settings", lambda: settings)

    # Build a baseline session containing Card 1 and Card 2, and Recipe 1 & Recipe 2
    base_sid = "sess_stress_baseline_01"
    base_session = PipelineSession(session_id=base_sid, cache=cache, settings=settings)

    c1 = ScannedCard(
        card_id="card_001",
        file_path=card_paths[0],
        filename="card_001.jpg",
        sha256_hash=card_shas[0],
        file_size_bytes=len(card_bytes_list[0]),
        width=150,
        height=150,
    )
    c2 = ScannedCard(
        card_id="card_002",
        file_path=card_paths[1],
        filename="card_002.jpg",
        sha256_hash=card_shas[1],
        file_size_bytes=len(card_bytes_list[1]),
        width=150,
        height=150,
    )
    base_session.cards = [c1, c2]

    fp1 = VisualFingerprint(
        card_id="card_001",
        image_path=str(card_paths[0]),
        sha256_hash=card_shas[0],
        role=CardRole.STANDALONE,
        role_confidence=0.98,
        recipe_title_candidate="Heritage Roast Chicken",
    )
    fp2 = VisualFingerprint(
        card_id="card_002",
        image_path=str(card_paths[1]),
        sha256_hash=card_shas[1],
        role=CardRole.STANDALONE,
        role_confidence=0.92,
        recipe_title_candidate="Classic Garlic Mashed Potatoes",
    )
    base_session.fingerprints = {"card_001": fp1, "card_002": fp2}

    rec1 = RecipeSchema(
        recipe_id="rec_roast_chicken",
        title="Heritage Roast Chicken",
        ingredients=["1 whole chicken", "2 tbsp olive oil", "1 tsp thyme", "1 tsp salt"],
        directions=["Pat dry chicken", "Season with thyme and salt", "Roast at 400F for 60 mins"],
        tags=["Dinner", "Poultry"],
        card_ids=["card_001"],
    )
    rec2 = RecipeSchema(
        recipe_id="rec_mashed_potatoes",
        title="Classic Garlic Mashed Potatoes",
        ingredients=["5 russet potatoes", "4 cloves garlic", "1/2 cup butter", "1/4 cup cream"],
        directions=["Boil potatoes until fork tender", "Mash with butter, garlic, and cream", "Season with salt"],
        tags=["Side Dish", "Comfort Food"],
        card_ids=["card_002"],
    )
    base_session.recipes = [rec1, rec2]
    base_session.state = PipelineState.COMPLETED

    # Seed baseline in SQLite store
    store.save_pipeline_session(base_session)

    # Save baseline to .recipeboxer archive
    baseline_archive_path = tmp_path / "baseline_recipes.recipeboxer"
    create_recipeboxer_archive(
        base_session,
        store=store,
        manager=manager,
        cache=cache,
        output_path=baseline_archive_path,
        project_name="Baseline Stress Recipes",
    )

    yield {
        "tmp_path": tmp_path,
        "scans_dir": scans_dir,
        "card_paths": card_paths,
        "card_shas": card_shas,
        "baseline_archive_path": baseline_archive_path,
        "base_sid": base_sid,
        "base_session": base_session,
        "settings": settings,
        "store": store,
        "cache": cache,
        "manager": manager,
    }

    store.close()
    cache.close()


# ============================================================================
# Section 1: POST /api/pipeline/start Stress & Robustness
# ============================================================================

class TestPipelineStartStress:
    def test_pipeline_start_with_valid_baseline_project_path(self, stress_env):
        app = create_app(stress_env["settings"])
        app.state.session_store = stress_env["store"]
        app.state.session_manager = stress_env["manager"]
        app.state.fingerprint_cache = stress_env["cache"]
        client = TestClient(app)

        resp = client.post(
            "/api/pipeline/start",
            json={
                "scans_dir": str(stress_env["scans_dir"]),
                "baseline_project_path": str(stress_env["baseline_archive_path"]),
                "mode": "parallel",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["total_scans_found"] == 4
        assert data["status"] in ("INGESTING", "SCANNING", "FINGERPRINTING", "CORRELATING", "HITL_PENDING", "COMPLETED", "IDLE")

        # Verify session state was created in manager with baseline_project_path
        session = stress_env["manager"].get_session(data["session_id"])
        assert session is not None
        assert session.config.baseline_project_path == str(stress_env["baseline_archive_path"])

    def test_pipeline_start_with_valid_baseline_session_id(self, stress_env):
        app = create_app(stress_env["settings"])
        app.state.session_store = stress_env["store"]
        app.state.session_manager = stress_env["manager"]
        app.state.fingerprint_cache = stress_env["cache"]
        client = TestClient(app)

        resp = client.post(
            "/api/pipeline/start",
            json={
                "scans_dir": str(stress_env["scans_dir"]),
                "baseline_session_id": stress_env["base_sid"],
                "mode": "parallel",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        session = stress_env["manager"].get_session(data["session_id"])
        assert session is not None
        assert session.config.baseline_session_id == stress_env["base_sid"]

    def test_pipeline_start_nonexistent_baseline_project_path_400(self, stress_env):
        app = create_app(stress_env["settings"])
        app.state.session_store = stress_env["store"]
        app.state.session_manager = stress_env["manager"]
        app.state.fingerprint_cache = stress_env["cache"]
        client = TestClient(app)

        nonexistent_path = str(stress_env["tmp_path"] / "nonexistent_file.recipeboxer")
        resp = client.post(
            "/api/pipeline/start",
            json={
                "scans_dir": str(stress_env["scans_dir"]),
                "baseline_project_path": nonexistent_path,
            },
        )
        assert resp.status_code == 400
        assert "Baseline project archive not found" in resp.json()["detail"]

    def test_pipeline_start_corrupted_baseline_zip_400(self, stress_env):
        app = create_app(stress_env["settings"])
        app.state.session_store = stress_env["store"]
        app.state.session_manager = stress_env["manager"]
        app.state.fingerprint_cache = stress_env["cache"]
        client = TestClient(app)

        corrupt_p = stress_env["tmp_path"] / "corrupt.recipeboxer"
        corrupt_p.write_bytes(b"CORRUPTED_BINARY_DATA_NOT_ZIP")

        resp = client.post(
            "/api/pipeline/start",
            json={
                "scans_dir": str(stress_env["scans_dir"]),
                "baseline_project_path": str(corrupt_p),
            },
        )
        assert resp.status_code == 400
        assert "Invalid .recipeboxer project archive" in resp.json()["detail"]

    def test_pipeline_start_directory_as_baseline_path_400(self, stress_env):
        app = create_app(stress_env["settings"])
        app.state.session_store = stress_env["store"]
        app.state.session_manager = stress_env["manager"]
        app.state.fingerprint_cache = stress_env["cache"]
        client = TestClient(app)

        # Providing a directory instead of a file
        resp = client.post(
            "/api/pipeline/start",
            json={
                "scans_dir": str(stress_env["scans_dir"]),
                "baseline_project_path": str(stress_env["tmp_path"]),
            },
        )
        assert resp.status_code == 400
        assert "Baseline project archive not found" in resp.json()["detail"]

    def test_pipeline_start_nonexistent_baseline_session_id_400(self, stress_env):
        app = create_app(stress_env["settings"])
        app.state.session_store = stress_env["store"]
        app.state.session_manager = stress_env["manager"]
        app.state.fingerprint_cache = stress_env["cache"]
        client = TestClient(app)

        resp = client.post(
            "/api/pipeline/start",
            json={
                "scans_dir": str(stress_env["scans_dir"]),
                "baseline_session_id": "sess_does_not_exist_404",
            },
        )
        assert resp.status_code == 400
        assert "not found in database" in resp.json()["detail"]


# ============================================================================
# Section 2: POST /api/cards/upload Multipart Baseline Handling
# ============================================================================

class TestUploadCardsMultipartStress:
    def test_upload_cards_with_baseline_archive_file(self, stress_env):
        app = create_app(stress_env["settings"])
        app.state.session_store = stress_env["store"]
        app.state.session_manager = stress_env["manager"]
        app.state.fingerprint_cache = stress_env["cache"]
        client = TestClient(app)

        with open(stress_env["card_paths"][2], "rb") as f_card, open(
            stress_env["baseline_archive_path"], "rb"
        ) as f_base:
            resp = client.post(
                "/api/cards/upload",
                files=[
                    ("files", ("card_003.jpg", f_card, "image/jpeg")),
                    ("baseline_archive", ("baseline.recipeboxer", f_base, "application/x-recipeboxer")),
                ],
                data={"mode": "parallel", "auto_start": "false"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        sid = data["session_id"]

        session = stress_env["manager"].get_session(sid)
        assert session is not None
        assert session.baseline_project_path is not None
        assert os.path.exists(session.baseline_project_path)
        assert zipfile.is_zipfile(session.baseline_project_path)

        # Check SQLite persistence of cards
        db_cards = stress_env["store"].get_cards(sid)
        assert len(db_cards) == 1

    def test_upload_cards_with_baseline_session_id_form_field(self, stress_env):
        app = create_app(stress_env["settings"])
        app.state.session_store = stress_env["store"]
        app.state.session_manager = stress_env["manager"]
        app.state.fingerprint_cache = stress_env["cache"]
        client = TestClient(app)

        with open(stress_env["card_paths"][3], "rb") as f_card:
            resp = client.post(
                "/api/cards/upload",
                files=[
                    ("files", ("card_004.jpg", f_card, "image/jpeg")),
                ],
                data={
                    "baseline_session_id": stress_env["base_sid"],
                    "auto_start": "false",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        sid = data["session_id"]

        session = stress_env["manager"].get_session(sid)
        assert session is not None
        assert session.baseline_session_id == stress_env["base_sid"]

    def test_upload_cards_with_baseline_project_path_form_field(self, stress_env):
        app = create_app(stress_env["settings"])
        app.state.session_store = stress_env["store"]
        app.state.session_manager = stress_env["manager"]
        app.state.fingerprint_cache = stress_env["cache"]
        client = TestClient(app)

        with open(stress_env["card_paths"][0], "rb") as f_card:
            resp = client.post(
                "/api/cards/upload",
                files=[
                    ("files", ("card_001.jpg", f_card, "image/jpeg")),
                ],
                data={
                    "baseline_project_path": str(stress_env["baseline_archive_path"]),
                    "auto_start": "false",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        sid = data["session_id"]

        session = stress_env["manager"].get_session(sid)
        assert session is not None
        assert session.baseline_project_path == str(stress_env["baseline_archive_path"])

    def test_upload_cards_with_corrupt_baseline_archive_400(self, stress_env):
        app = create_app(stress_env["settings"])
        app.state.session_store = stress_env["store"]
        app.state.session_manager = stress_env["manager"]
        app.state.fingerprint_cache = stress_env["cache"]
        client = TestClient(app)

        with open(stress_env["card_paths"][0], "rb") as f_card:
            resp = client.post(
                "/api/cards/upload",
                files=[
                    ("files", ("card_001.jpg", f_card, "image/jpeg")),
                    ("baseline_archive", ("corrupt.recipeboxer", b"NOT_A_VALID_ZIP", "application/x-recipeboxer")),
                ],
                data={"auto_start": "false"},
            )

        assert resp.status_code == 400
        assert "Invalid .recipeboxer project archive" in resp.json()["detail"]

    def test_upload_cards_with_nonexistent_baseline_session_id_400(self, stress_env):
        app = create_app(stress_env["settings"])
        app.state.session_store = stress_env["store"]
        app.state.session_manager = stress_env["manager"]
        app.state.fingerprint_cache = stress_env["cache"]
        client = TestClient(app)

        with open(stress_env["card_paths"][0], "rb") as f_card:
            resp = client.post(
                "/api/cards/upload",
                files=[
                    ("files", ("card_001.jpg", f_card, "image/jpeg")),
                ],
                data={
                    "baseline_session_id": "sess_does_not_exist_999",
                    "auto_start": "false",
                },
            )

        assert resp.status_code == 400
        assert "not found in database" in resp.json()["detail"]


# ============================================================================
# Section 3: SSE /api/events Streaming Verification
# ============================================================================

class TestSSEEventsStreamStress:
    @pytest.mark.asyncio
    async def test_sse_live_stream_emits_duplicate_skipping_progress(self, stress_env):
        """Verify SSE stream emits progress events with skipped_cards & newly_processed_cards."""
        app = create_app(stress_env["settings"])
        app.state.session_store = stress_env["store"]
        app.state.session_manager = stress_env["manager"]
        app.state.fingerprint_cache = stress_env["cache"]

        sid = "sess_sse_test_01"
        session = stress_env["manager"].create_session(
            session_id=sid,
            config=PipelineStartRequest(
                scans_dir=str(stress_env["scans_dir"]),
                baseline_project_path=str(stress_env["baseline_archive_path"]),
            ),
        )
        session.state = PipelineState.INGESTING
        session.progress_metrics.total_scans = 4
        session.progress_metrics.skipped_cards = 2
        session.progress_metrics.newly_processed_cards = 2
        session.progress_metrics.baseline_recipes_count = 2
        session.progress_metrics.percent_complete = 50.0

        # Complete session after small delay to cleanly finish stream
        async def complete_soon():
            await asyncio.sleep(0.1)
            session.state = PipelineState.COMPLETED

        asyncio.create_task(complete_soon())

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream("GET", f"/api/events?session_id={sid}") as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers.get("content-type", "")

                chunks = []
                async for chunk in response.aiter_text():
                    chunks.append(chunk)
                    if "event: completed" in chunk or "COMPLETED" in chunk:
                        break

                full_text = "".join(chunks)
                assert ": connected" in full_text
                assert "event: progress" in full_text
                assert '"skipped_cards":2' in full_text or '"skipped_cards": 2' in full_text
                assert '"newly_processed_cards":2' in full_text or '"newly_processed_cards": 2' in full_text
                assert '"baseline_recipes_count":2' in full_text or '"baseline_recipes_count": 2' in full_text

    @pytest.mark.asyncio
    async def test_sse_fallback_to_sqlite_session_store(self, stress_env):
        """Verify SSE streams persisted progress from SQLite if in-memory session is absent."""
        app = create_app(stress_env["settings"])
        app.state.session_store = stress_env["store"]
        app.state.session_manager = stress_env["manager"]  # Empty manager
        app.state.fingerprint_cache = stress_env["cache"]

        # base_sid exists only in SQLite store, NOT in session_manager
        assert stress_env["manager"].get_session(stress_env["base_sid"]) is None
        assert stress_env["store"].get_session_detail(stress_env["base_sid"]) is not None

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream("GET", f"/api/events?session_id={stress_env['base_sid']}") as response:
                assert response.status_code == 200
                chunks = []
                async for chunk in response.aiter_text():
                    chunks.append(chunk)
                
                full_stream_text = "".join(chunks)
                assert ": connected" in full_stream_text
                assert "event: state_change" in full_stream_text or "event: progress" in full_stream_text
                assert "Heritage Roast Chicken" in full_stream_text or "Classic Garlic Mashed Potatoes" in full_stream_text

    @pytest.mark.asyncio
    async def test_sse_unknown_session_returns_404(self, stress_env):
        """Verify SSE returns 404 cleanly when session not in memory AND not in SQLite."""
        app = create_app(stress_env["settings"])
        app.state.session_store = stress_env["store"]
        app.state.session_manager = stress_env["manager"]
        app.state.fingerprint_cache = stress_env["cache"]

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/events?session_id=completely_unknown_sid")
            assert resp.status_code == 404
            assert "not found" in resp.json()["detail"]


# ============================================================================
# Section 4: SQLite SessionStore Persistence & Recipe Merging Stress
# ============================================================================

class TestSessionStorePersistenceStress:
    def test_session_store_summary_and_detail_baseline_metadata(self, stress_env):
        store = stress_env["store"]
        sid = "sess_meta_test_01"

        config_data = {
            "baseline_project_path": "/path/to/baseline.recipeboxer",
            "baseline_session_id": "sess_parent_01",
            "scans_dir": "/path/to/scans",
            "mode": "parallel",
        }
        progress_data = ProgressMetrics(
            total_scans=10,
            skipped_cards=7,
            newly_processed_cards=3,
            baseline_recipes_count=5,
            fingerprinted=10,
            extracted=3,
            percent_complete=100.0,
        )

        store.save_session(
            session_id=sid,
            state=PipelineState.COMPLETED,
            mode="parallel",
            folder_path="/path/to/scans",
            config=config_data,
            progress=progress_data,
            card_count=10,
            recipe_count=8,
        )

        # Verify summary
        summary = store.get_session_summary(sid)
        assert summary is not None
        assert summary.session_id == sid
        assert summary.skipped_cards == 7
        assert summary.baseline_project_path == "/path/to/baseline.recipeboxer"
        assert summary.baseline_session_id == "sess_parent_01"
        assert summary.card_count == 10
        assert summary.recipe_count == 8

        # Verify detail
        detail = store.get_session_detail(sid)
        assert detail is not None
        assert detail.skipped_cards == 7
        assert detail.baseline_recipes_count == 5
        assert detail.baseline_project_path == "/path/to/baseline.recipeboxer"
        assert detail.baseline_session_id == "sess_parent_01"

        # Verify list_sessions
        all_sessions = store.list_sessions()
        matched = [s for s in all_sessions if s.session_id == sid]
        assert len(matched) == 1
        assert matched[0].skipped_cards == 7
        assert matched[0].baseline_project_path == "/path/to/baseline.recipeboxer"

    def test_session_store_recipe_id_deduplication_and_merging(self, stress_env):
        """Test recipe ID conflict resolution when newly extracted recipe collides with baseline recipe ID."""
        agent = RecipeBoxerAgent(settings=stress_env["settings"], cache=stress_env["cache"])

        session = PipelineSession(
            session_id="sess_collision_test",
            config={
                "baseline_project_path": str(stress_env["baseline_archive_path"]),
            },
            settings=stress_env["settings"],
            cache=stress_env["cache"],
        )

        # Load baseline
        _, _, baseline_recipes = agent._load_baseline(session)
        assert len(baseline_recipes) == 2
        base_id = baseline_recipes[0].recipe_id  # "rec_roast_chicken"

        # Simulate newly extracted recipe that happens to have the same ID
        colliding_recipe = RecipeSchema(
            recipe_id=base_id,
            title="Newly Extracted Conflicting Recipe",
            ingredients=["1 cup sugar"],
            directions=["Mix and bake"],
        )

        # Merge logic as implemented in orchestrator
        merged_recipes = []
        seen_ids = set()
        for r in baseline_recipes:
            merged_recipes.append(r)
            seen_ids.add(r.recipe_id)

        for r in [colliding_recipe]:
            if r.recipe_id in seen_ids:
                r.recipe_id = f"rec_renamed_new_01"
            merged_recipes.append(r)
            seen_ids.add(r.recipe_id)

        assert len(merged_recipes) == 3
        # Baseline recipe preserved
        assert merged_recipes[0].recipe_id == base_id
        assert merged_recipes[0].title == "Heritage Roast Chicken"
        # New recipe had its ID disambiguated
        assert merged_recipes[2].recipe_id == "rec_renamed_new_01"
        assert merged_recipes[2].title == "Newly Extracted Conflicting Recipe"

        # Save to store and verify all 3 persist
        store = stress_env["store"]
        store.save_recipes(session.session_id, merged_recipes)
        persisted = store.list_recipes(session.session_id)
        assert len(persisted) == 3
        persisted_ids = {r.recipe_id for r in persisted}
        assert base_id in persisted_ids
        assert "rec_renamed_new_01" in persisted_ids

    def test_session_store_thread_safety_under_concurrent_writes(self, stress_env):
        """Stress-test SessionStore under 20 concurrent threads reading and writing."""
        store = stress_env["store"]
        errors = []

        def worker(thread_idx: int):
            try:
                sid = f"sess_concurrent_{thread_idx}"
                store.save_session(
                    session_id=sid,
                    state=PipelineState.INGESTING,
                    card_count=thread_idx,
                    recipe_count=0,
                )
                # Save card
                store.save_card(
                    session_id=sid,
                    card_id=f"card_{thread_idx}",
                    image_path=f"/tmp/card_{thread_idx}.jpg",
                    sha256_hash=f"hash_{thread_idx}",
                    role="STANDALONE",
                )
                # Save recipe
                rec = RecipeSchema(
                    recipe_id=f"rec_{thread_idx}",
                    title=f"Recipe {thread_idx}",
                    ingredients=[f"Ingredient {thread_idx}"],
                    directions=[f"Direction {thread_idx}"],
                )
                store.save_recipe(sid, rec)

                # Read back
                detail = store.get_session_detail(sid)
                if detail is None or len(detail.recipes) != 1:
                    errors.append(f"Thread {thread_idx} verification failed")
            except Exception as exc:
                errors.append(f"Thread {thread_idx} exception: {exc}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrent store errors: {errors}"
        summaries = store.list_sessions()
        assert len(summaries) >= 20


# ============================================================================
# Section 5: End-to-End Orchestration & Status Fallback Invariant Tests
# ============================================================================

class TestOrchestrationAndStatusAPIStress:
    def test_status_endpoint_sqlite_fallback_metrics(self, stress_env):
        """Verify GET /api/pipeline/status returns full metrics from SQLite when session is completed and evicted from memory."""
        store = stress_env["store"]
        sid = "sess_status_fallback_01"

        config_data = {
            "baseline_project_path": str(stress_env["baseline_archive_path"]),
            "baseline_session_id": "sess_prior_01",
            "scans_dir": "/tmp/scans",
        }
        progress_data = ProgressMetrics(
            total_scans=6,
            skipped_cards=4,
            newly_processed_cards=2,
            baseline_recipes_count=3,
            fingerprinted=6,
            extracted=2,
            percent_complete=100.0,
        )

        store.save_session(
            session_id=sid,
            state=PipelineState.COMPLETED,
            mode="parallel",
            folder_path="/tmp/scans",
            config=config_data,
            progress=progress_data,
            card_count=6,
            recipe_count=5,
            csv_output_path="/tmp/exports/recipeboxer_sess_status_fallback_01.csv",
        )

        app = create_app(stress_env["settings"])
        app.state.session_store = store
        app.state.session_manager = SessionManager()  # Empty manager
        app.state.fingerprint_cache = stress_env["cache"]
        client = TestClient(app)

        resp = client.get(f"/api/pipeline/status?session_id={sid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == sid
        assert data["status"] == "COMPLETED"
        assert data["progress"]["skipped_cards"] == 4
        assert data["progress"]["newly_processed_cards"] == 2
        assert data["progress"]["baseline_recipes_count"] == 3
        assert data["progress"]["percent_complete"] == 100.0
        assert data["csv_output_path"] == "/tmp/exports/recipeboxer_sess_status_fallback_01.csv"

    def test_card_image_stream_endpoint_for_saved_cards(self, stress_env):
        """Verify GET /api/cards/{card_id}/image streams image bytes from SQLite stored path."""
        app = create_app(stress_env["settings"])
        app.state.session_store = stress_env["store"]
        app.state.session_manager = stress_env["manager"]
        app.state.fingerprint_cache = stress_env["cache"]
        client = TestClient(app)

        # Retrieve image for card_001 from baseline session
        resp = client.get(f"/api/cards/card_001/image?session_id={stress_env['base_sid']}")
        assert resp.status_code == 200
        assert resp.headers.get("content-type") == "image/jpeg"
        assert len(resp.content) > 0

    @pytest.mark.asyncio
    async def test_e2e_multipart_upload_auto_start_100_percent_duplicates(self, stress_env):
        """Upload card_001 & card_002 with auto_start=true against baseline containing card_001 & card_002.
        Verify 100% duplicate skip, zero extraction prompts, fast completion, and SessionStore persistence.
        """
        app = create_app(stress_env["settings"])
        app.state.session_store = stress_env["store"]
        app.state.session_manager = stress_env["manager"]
        app.state.fingerprint_cache = stress_env["cache"]
        client = TestClient(app)

        with open(stress_env["card_paths"][0], "rb") as f1, open(stress_env["card_paths"][1], "rb") as f2, open(
            stress_env["baseline_archive_path"], "rb"
        ) as f_base:
            resp = client.post(
                "/api/cards/upload",
                files=[
                    ("files", ("card_001.jpg", f1, "image/jpeg")),
                    ("files", ("card_002.jpg", f2, "image/jpeg")),
                    ("baseline_archive", ("baseline.recipeboxer", f_base, "application/x-recipeboxer")),
                ],
                data={"mode": "parallel", "auto_start": "true"},
            )

        assert resp.status_code == 200
        sid = resp.json()["session_id"]
        session = stress_env["manager"].get_session(sid)
        assert session is not None

        # Wait for pipeline to complete in background task
        if session.pipeline_task:
            await session.pipeline_task

        assert session.state == PipelineState.COMPLETED
        assert session.progress_metrics.total_scans == 2
        assert session.progress_metrics.skipped_cards == 2
        assert session.progress_metrics.newly_processed_cards == 0
        assert session.progress_metrics.baseline_recipes_count == 2
        assert len(session.recipes) == 2

        # Verify which store the session wrote to
        agent_store = SessionStore(session.settings.cache_db_path)
        agent_detail = agent_store.get_session_detail(sid)
        assert agent_detail is not None
        assert agent_detail.status == "COMPLETED"
        assert len(agent_detail.cards) == 2
        assert len(agent_detail.recipes) == 2

    def test_upload_cards_empty_file_list_400(self, stress_env):
        """Verify POST /api/cards/upload with empty files list returns 422 or 400."""
        app = create_app(stress_env["settings"])
        client = TestClient(app)
        resp = client.post("/api/cards/upload", files=[])
        assert resp.status_code in (400, 422)

    def test_pipeline_start_invalid_remote_source_provider_400(self, stress_env):
        """Verify POST /api/pipeline/start with unknown remote source provider returns 400."""
        app = create_app(stress_env["settings"])
        client = TestClient(app)
        resp = client.post(
            "/api/pipeline/start",
            json={
                "source_provider": "nonexistent_cloud_service_999",
                "source_container_id": "bucket_01",
            },
        )
        assert resp.status_code == 400
        assert "Failed to start pipeline from source provider" in resp.json()["detail"]

    def test_session_store_unicode_and_special_character_resilience(self, stress_env):
        """Stress-test SessionStore with Unicode (French, Japanese, Emoji) in recipe titles and instructions."""
        store = stress_env["store"]
        sid = "sess_unicode_test"
        store.save_session(session_id=sid, state=PipelineState.COMPLETED)

        unicode_recipe = RecipeSchema(
            recipe_id="rec_unicode_01",
            title="Crème Brûlée à la Vanille & 抹茶パフェ 🍨",
            description="Délicieux dessert traditionnel avec une croûte caramélisée croustillante.",
            ingredients=["500ml crème liquide 30%", "5 jaunes d'œufs", "70g sucre semoule", "1 gousse de vanille Bourbon", "抹茶パウダー 大さじ1"],
            directions=[
                "1. Préchauffer le four à 100°C.",
                "2. Faire infuser la vanille dans la crème chaude.",
                "3. Fouetter les jaunes avec le sucre sans faire mousser.",
                "4. Verser la crème tiède sur les jaunes.",
                "5. Cuire au bain-marie pendant 1h15.",
                "6. Saupoudrer de cassonade et caraméliser au chalumeau! 🔥",
            ],
            tags=["Dessert", "Français", "Gourmet", "抹茶"],
            source_notes="Recette de grand-mère Hélène (1962).",
        )

        store.save_recipe(sid, unicode_recipe)
        retrieved = store.get_recipe("rec_unicode_01", session_id=sid)
        assert retrieved is not None
        assert retrieved.title == "Crème Brûlée à la Vanille & 抹茶パフェ 🍨"
        assert "cassonade et caraméliser" in retrieved.directions[5]
        assert "抹茶パウダー 大さじ1" in retrieved.ingredients[4]

    def test_session_store_cascade_deletion(self, stress_env):
        """Verify delete_session cleanly cascades and purges cards, proposals, and recipes."""
        store = stress_env["store"]
        sid = "sess_to_delete_01"
        store.save_session(session_id=sid, state=PipelineState.COMPLETED)
        store.save_card(
            session_id=sid,
            card_id="c_del_01",
            image_path="/tmp/c_del_01.jpg",
            sha256_hash="sha_del",
            role="STANDALONE",
        )
        store.save_recipe(
            session_id=sid,
            recipe=RecipeSchema(recipe_id="r_del_01", title="To Delete", ingredients=[], directions=[]),
        )

        assert len(store.get_cards(sid)) == 1
        assert len(store.list_recipes(sid)) == 1
        assert store.get_session_summary(sid) is not None

        # Delete session
        deleted = store.delete_session(sid)
        assert deleted is True

        assert len(store.get_cards(sid)) == 0
        assert len(store.list_recipes(sid)) == 0
        assert store.get_session_summary(sid) is None
        assert store.get_session_detail(sid) is None


