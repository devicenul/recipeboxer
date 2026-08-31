"""4-Tier automated test suite for Milestone 2: Incremental Ingestion with Duplicate-Skipping."""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

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


@pytest.fixture
def sample_jpeg_bytes() -> bytes:
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c"
        b"\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c"
        b"\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01"
        b"\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01"
        b"\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06"
        b"\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
    )


@pytest.fixture
def incremental_fixture(tmp_path: Path, sample_jpeg_bytes: bytes):
    scans_dir = tmp_path / "scans"
    scans_dir.mkdir(parents=True, exist_ok=True)

    # Card 1 (Baseline card A)
    card1_bytes = sample_jpeg_bytes
    card1_path = scans_dir / "card_001.jpg"
    card1_path.write_bytes(card1_bytes)
    card1_sha = compute_file_sha256(card1_path)

    # Card 2 (Baseline card B)
    card2_bytes = sample_jpeg_bytes[:-2] + b"\x01\xff\xd9"
    card2_path = scans_dir / "card_002.jpg"
    card2_path.write_bytes(card2_bytes)
    card2_sha = compute_file_sha256(card2_path)

    # Card 3 (New card C)
    card3_bytes = sample_jpeg_bytes[:-2] + b"\x02\xff\xd9"
    card3_path = scans_dir / "card_003.jpg"
    card3_path.write_bytes(card3_bytes)
    card3_sha = compute_file_sha256(card3_path)

    # Setup SessionStore & Cache
    db_path = tmp_path / "cache" / "test_incremental.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SessionStore(db_path=str(db_path))
    cache = FingerprintCache(db_path=str(db_path))
    manager = SessionManager()

    settings = Settings(
        cache_db_path=str(db_path),
        cache_dir=str(tmp_path / "cache"),
        export_dir=str(tmp_path / "exports"),
    )

    # Create baseline session with Card 1 and Card 2 and Recipe 1
    base_session = PipelineSession(
        session_id="sess_baseline_01",
        cache=cache,
        settings=settings,
    )
    c1 = ScannedCard(
        card_id="card_001",
        file_path=card1_path,
        filename="card_001.jpg",
        sha256_hash=card1_sha,
        file_size_bytes=len(card1_bytes),
        width=100,
        height=100,
    )
    c2 = ScannedCard(
        card_id="card_002",
        file_path=card2_path,
        filename="card_002.jpg",
        sha256_hash=card2_sha,
        file_size_bytes=len(card2_bytes),
        width=100,
        height=100,
    )
    base_session.cards = [c1, c2]

    fp1 = VisualFingerprint(
        card_id="card_001",
        image_path=str(card1_path),
        sha256_hash=card1_sha,
        role=CardRole.STANDALONE,
        role_confidence=0.95,
        recipe_title_candidate="Grandma's Apple Pie",
    )
    fp2 = VisualFingerprint(
        card_id="card_002",
        image_path=str(card2_path),
        sha256_hash=card2_sha,
        role=CardRole.STANDALONE,
        role_confidence=0.92,
        recipe_title_candidate="Old Fashioned Sugar Cookies",
    )
    base_session.fingerprints = {"card_001": fp1, "card_002": fp2}

    rec1 = RecipeSchema(
        recipe_id="rec_baseline_001",
        title="Grandma's Apple Pie",
        ingredients=["6 apples", "1 cup sugar", "1 tsp cinnamon"],
        directions=["Peel apples", "Mix ingredients", "Bake at 350F"],
        card_ids=["card_001"],
    )
    base_session.recipes = [rec1]
    base_session.state = PipelineState.COMPLETED

    # Save baseline to .recipeboxer archive on disk
    baseline_archive_path = tmp_path / "baseline_project.recipeboxer"
    create_recipeboxer_archive(
        base_session,
        store=store,
        manager=manager,
        cache=cache,
        output_path=baseline_archive_path,
        project_name="Baseline Family Recipes",
    )

    yield {
        "tmp_path": tmp_path,
        "scans_dir": scans_dir,
        "card1_path": card1_path,
        "card2_path": card2_path,
        "card3_path": card3_path,
        "card1_sha": card1_sha,
        "card2_sha": card2_sha,
        "card3_sha": card3_sha,
        "baseline_archive_path": baseline_archive_path,
        "base_session": base_session,
        "settings": settings,
        "store": store,
        "cache": cache,
        "manager": manager,
    }

    store.close()
    cache.close()


# ============================================================================
# Tier 1: Unit Tests (Hashing, Baseline Loading & Model Partitioning)
# ============================================================================

class TestIncrementalIngestionTier1Unit:
    def test_baseline_manifest_loading_and_indexing(self, incremental_fixture):
        agent = RecipeBoxerAgent(
            settings=incremental_fixture["settings"],
            cache=incremental_fixture["cache"],
        )
        session = PipelineSession(
            session_id="sess_test_load",
            config={
                "baseline_project_path": str(incremental_fixture["baseline_archive_path"]),
            },
            settings=incremental_fixture["settings"],
            cache=incremental_fixture["cache"],
        )
        manifest, cards_by_hash, recipes = agent._load_baseline(session)

        assert manifest is not None
        assert len(cards_by_hash) == 2
        assert incremental_fixture["card1_sha"].lower() in cards_by_hash
        assert incremental_fixture["card2_sha"].lower() in cards_by_hash
        assert len(recipes) == 1
        assert recipes[0].title == "Grandma's Apple Pie"

    def test_card_partitioning_skipped_vs_new(self, incremental_fixture):
        agent = RecipeBoxerAgent(
            settings=incremental_fixture["settings"],
            cache=incremental_fixture["cache"],
        )
        # Create a session with 3 cards: card1 & card2 (baseline), card3 (new)
        session = PipelineSession(
            session_id="sess_test_part",
            config={
                "scans_dir": str(incremental_fixture["scans_dir"]),
                "baseline_project_path": str(incremental_fixture["baseline_archive_path"]),
            },
            settings=incremental_fixture["settings"],
            cache=incremental_fixture["cache"],
        )

        manifest, cards_by_hash, recipes = agent._load_baseline(session)
        cards = scan_card_directory(incremental_fixture["scans_dir"])
        assert len(cards) == 3

        skipped = []
        new = []
        for c in cards:
            if c.sha256_hash.lower() in cards_by_hash:
                skipped.append(c)
            else:
                new.append(c)

        assert len(skipped) == 2
        assert len(new) == 1
        assert new[0].filename == "card_003.jpg"

    def test_progress_metrics_duplicate_accounting(self):
        metrics = ProgressMetrics(
            total_scans=10,
            skipped_cards=6,
            newly_processed_cards=4,
            fingerprinted=10,
            baseline_recipes_count=5,
        )
        assert metrics.skipped_cards == 6
        assert metrics.newly_processed_cards == 4
        assert metrics.baseline_recipes_count == 5

        session = PipelineSession(session_id="sess_pct_test")
        session.progress_metrics = metrics
        session.state = PipelineState.COMPLETED
        status = session.get_status()
        assert status.progress.percent_complete == 100.0


# ============================================================================
# Tier 2: Pipeline Orchestration & Stage Bypass Verification
# ============================================================================

class TestIncrementalIngestionTier2Pipeline:
    @pytest.mark.asyncio
    async def test_incremental_ingestion_100_percent_duplicates_bypasses_stages(self, incremental_fixture):
        # Create directory with ONLY card1 and card2
        dup_dir = incremental_fixture["tmp_path"] / "dup_scans"
        dup_dir.mkdir()
        shutil.copy2(incremental_fixture["card1_path"], dup_dir / "card_001.jpg")
        shutil.copy2(incremental_fixture["card2_path"], dup_dir / "card_002.jpg")

        extractor_call_count = 0

        async def mock_extractor(card: ScannedCard):
            nonlocal extractor_call_count
            extractor_call_count += 1
            return VisualFingerprint(
                card_id=card.card_id,
                image_path=str(card.file_path),
                sha256_hash=card.sha256_hash,
                role=CardRole.STANDALONE,
                recipe_title_candidate="Mock Title",
            )

        session = PipelineSession(
            session_id="sess_100_dup",
            config=PipelineStartRequest(
                scans_dir=str(dup_dir),
                baseline_project_path=str(incremental_fixture["baseline_archive_path"]),
            ),
            settings=incremental_fixture["settings"],
            cache=incremental_fixture["cache"],
            extractor=mock_extractor,
        )

        agent = RecipeBoxerAgent(
            settings=incremental_fixture["settings"],
            cache=incremental_fixture["cache"],
            visual_extractor=mock_extractor,
        )

        await agent.run_pipeline(session)

        # 1. Zero Gemini Vision API calls made!
        assert extractor_call_count == 0

        # 2. Duplicate skipping metrics recorded
        assert session.progress_metrics.total_scans == 2
        assert session.progress_metrics.skipped_cards == 2
        assert session.progress_metrics.newly_processed_cards == 0
        assert session.progress_metrics.baseline_recipes_count == 1

        # 3. Restored baseline recipes seamlessly
        assert len(session.recipes) == 1
        assert session.recipes[0].title == "Grandma's Apple Pie"

        # 4. Successfully completed without requiring HITL intervention
        assert session.state == PipelineState.COMPLETED

    @pytest.mark.asyncio
    async def test_incremental_ingestion_partial_duplicates_processes_only_new(self, incremental_fixture):
        # Scans directory contains 3 cards (card1, card2 duplicates, card3 brand new)
        extractor_called_cards = []

        async def mock_extractor(card: ScannedCard):
            extractor_called_cards.append(card.card_id)
            return VisualFingerprint(
                card_id=card.card_id,
                image_path=str(card.file_path),
                sha256_hash=card.sha256_hash,
                role=CardRole.STANDALONE,
                role_confidence=0.9,
                recipe_title_candidate="Chocolate Fudge Brownies",
            )

        async def mock_recipe_extractor(fps):
            return RecipeSchema(
                recipe_id="rec_new_003",
                title="Chocolate Fudge Brownies",
                ingredients=["Cocoa", "Sugar", "Butter", "Flour"],
                directions=["Melt butter", "Mix dry ingredients", "Bake at 325F"],
                card_ids=[fp.card_id for fp in fps],
            )

        session = PipelineSession(
            session_id="sess_partial_dup",
            config=PipelineStartRequest(
                scans_dir=str(incremental_fixture["scans_dir"]),
                baseline_project_path=str(incremental_fixture["baseline_archive_path"]),
            ),
            settings=incremental_fixture["settings"],
            cache=incremental_fixture["cache"],
            extractor=mock_extractor,
            recipe_extractor_fn=mock_recipe_extractor,
        )

        agent = RecipeBoxerAgent(
            settings=incremental_fixture["settings"],
            cache=incremental_fixture["cache"],
            visual_extractor=mock_extractor,
            recipe_extractor=mock_recipe_extractor,
        )

        # Launch pipeline in background task so we can approve HITL for the new card
        task = agent.run_in_background(session)

        # Wait for HITL state
        for _ in range(50):
            if session.state == PipelineState.HITL_PENDING:
                break
            await asyncio.sleep(0.05)

        assert session.state == PipelineState.HITL_PENDING
        # Only 1 proposal for the newly scanned card!
        assert len(session.proposals) == 1
        new_card_id = session.new_cards[0].card_id
        assert session.proposals[0].ordered_card_ids == [new_card_id]

        # Resume HITL with approval
        session.resume_with_approvals([
            CardPairApproval(
                proposal_id=session.proposals[0].proposal_id,
                ordered_card_ids=[new_card_id],
                decision=HITLDecision.APPROVE,
            )
        ])

        await task

        # Extractor called ONLY for the single new card!
        assert extractor_called_cards == [new_card_id]

        # Assert final metrics & merged recipes
        assert session.progress_metrics.total_scans == 3
        assert session.progress_metrics.skipped_cards == 2
        assert session.progress_metrics.newly_processed_cards == 1
        assert session.progress_metrics.baseline_recipes_count == 1
        assert session.progress_metrics.extracted == 1

        # Merged recipes: 1 from baseline + 1 newly extracted = 2 total
        assert len(session.recipes) == 2
        titles = {r.title for r in session.recipes}
        assert "Grandma's Apple Pie" in titles
        assert "Chocolate Fudge Brownies" in titles
        assert session.state == PipelineState.COMPLETED


# ============================================================================
# Tier 3: API Endpoint Integration Tests
# ============================================================================

class TestIncrementalIngestionTier3Endpoints:
    def test_pipeline_start_with_baseline_archive(self, incremental_fixture):
        app = create_app(incremental_fixture["settings"])
        app.state.session_store = incremental_fixture["store"]
        app.state.session_manager = incremental_fixture["manager"]
        app.state.fingerprint_cache = incremental_fixture["cache"]

        client = TestClient(app)
        response = client.post(
            "/api/pipeline/start",
            json={
                "scans_dir": str(incremental_fixture["scans_dir"]),
                "baseline_project_path": str(incremental_fixture["baseline_archive_path"]),
                "mode": "parallel",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert data["total_scans_found"] == 3

    def test_pipeline_start_with_baseline_session_id(self, incremental_fixture):
        app = create_app(incremental_fixture["settings"])
        app.state.session_store = incremental_fixture["store"]
        app.state.session_manager = incremental_fixture["manager"]
        app.state.fingerprint_cache = incremental_fixture["cache"]

        client = TestClient(app)

        # Seed baseline session in store
        incremental_fixture["store"].save_session(
            session_id="sess_seeded_base",
            state=PipelineState.COMPLETED,
            folder_path=str(incremental_fixture["scans_dir"]),
            card_count=2,
            recipe_count=1,
        )

        response = client.post(
            "/api/pipeline/start",
            json={
                "scans_dir": str(incremental_fixture["scans_dir"]),
                "baseline_session_id": "sess_seeded_base",
                "mode": "parallel",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data

    def test_upload_cards_with_baseline_archive_multipart(self, incremental_fixture):
        app = create_app(incremental_fixture["settings"])
        app.state.session_store = incremental_fixture["store"]
        app.state.session_manager = incremental_fixture["manager"]
        app.state.fingerprint_cache = incremental_fixture["cache"]

        client = TestClient(app)

        with open(incremental_fixture["card1_path"], "rb") as f1, open(
            incremental_fixture["baseline_archive_path"], "rb"
        ) as f_base:
            response = client.post(
                "/api/cards/upload",
                files=[
                    ("files", ("card1.jpg", f1, "image/jpeg")),
                    ("baseline_archive", ("baseline.recipeboxer", f_base, "application/x-recipeboxer")),
                ],
                data={"auto_start": "false"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        sid = data["session_id"]

        session = incremental_fixture["manager"].get_session(sid)
        assert session is not None
        assert session.baseline_project_path is not None


# ============================================================================
# Tier 4: Edge Cases & Adversarial Invariant Tests
# ============================================================================

class TestIncrementalIngestionTier4EdgeCases:
    @pytest.mark.asyncio
    async def test_modified_image_content_causes_reprocessing(self, incremental_fixture):
        # Image with same filename "card_001.jpg", but modified image content (SHA mismatch)
        mod_dir = incremental_fixture["tmp_path"] / "mod_scans"
        mod_dir.mkdir()
        modified_bytes = incremental_fixture["card1_path"].read_bytes()[:-2] + b"\x99\xff\xd9"
        (mod_dir / "card_001.jpg").write_bytes(modified_bytes)

        extractor_called = False

        async def mock_extractor(card: ScannedCard):
            nonlocal extractor_called
            extractor_called = True
            return VisualFingerprint(
                card_id=card.card_id,
                image_path=str(card.file_path),
                sha256_hash=card.sha256_hash,
                role=CardRole.STANDALONE,
                recipe_title_candidate="Modified Card Recipe",
            )

        session = PipelineSession(
            session_id="sess_mod_test",
            config=PipelineStartRequest(
                scans_dir=str(mod_dir),
                baseline_project_path=str(incremental_fixture["baseline_archive_path"]),
            ),
            settings=incremental_fixture["settings"],
            cache=incremental_fixture["cache"],
            extractor=mock_extractor,
        )

        agent = RecipeBoxerAgent(
            settings=incremental_fixture["settings"],
            cache=incremental_fixture["cache"],
            visual_extractor=mock_extractor,
        )

        task = agent.run_in_background(session)
        for _ in range(50):
            if session.state in (PipelineState.HITL_PENDING, PipelineState.COMPLETED):
                break
            await asyncio.sleep(0.05)

        # Since SHA-256 differed, it was NOT skipped and extractor was called!
        assert extractor_called is True
        assert session.progress_metrics.skipped_cards == 0
        assert session.progress_metrics.newly_processed_cards == 1

        session.cancel()

    def test_corrupted_baseline_file_returns_400(self, incremental_fixture):
        app = create_app(incremental_fixture["settings"])
        app.state.session_store = incremental_fixture["store"]
        app.state.session_manager = incremental_fixture["manager"]
        app.state.fingerprint_cache = incremental_fixture["cache"]

        client = TestClient(app)

        corrupt_file = incremental_fixture["tmp_path"] / "corrupt.recipeboxer"
        corrupt_file.write_bytes(b"NOT_A_VALID_ZIP_HEADER")

        response = client.post(
            "/api/pipeline/start",
            json={
                "scans_dir": str(incremental_fixture["scans_dir"]),
                "baseline_project_path": str(corrupt_file),
            },
        )
        assert response.status_code == 400
        assert "Invalid .recipeboxer project archive" in response.json()["detail"]

    def test_nonexistent_baseline_session_id_returns_400(self, incremental_fixture):
        app = create_app(incremental_fixture["settings"])
        app.state.session_store = incremental_fixture["store"]
        app.state.session_manager = incremental_fixture["manager"]
        app.state.fingerprint_cache = incremental_fixture["cache"]

        client = TestClient(app)

        response = client.post(
            "/api/pipeline/start",
            json={
                "scans_dir": str(incremental_fixture["scans_dir"]),
                "baseline_session_id": "sess_nonexistent_999",
            },
        )
        assert response.status_code == 400
        assert "not found in database" in response.json()["detail"]
