"""Tier 5 Adversarial Stress and Robustness Test Suite for RecipeBoxer.

Empirical verification of:
1. Severely corrupted/truncated image files during batch scanning & ingestion.
2. High-throughput parallel concurrency stress (e.g. 50+ cards with max_concurrency=10).
3. Abrupt cancellation during slow-batch interval sleep & in-flight extraction.
4. Concurrent multi-session pipeline launches, polling, approvals, and CSV exports (race stress).
5. Corrupted/locked SQLite cache recovery and deserialization error resilience.
6. Edge case recipe normalization, unicode robustness, and RFC 4180 CSV export compliance.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import multiprocessing as mp
import os
import random
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from PIL import Image, ImageDraw

from recipeboxer.agent.orchestrator import RecipeBoxerAgent
from recipeboxer.agent.session import PipelineSession, SessionManager
from recipeboxer.api.app import create_app
from recipeboxer.config import Settings, get_settings
from recipeboxer.correlation.optimizer import PairingOptimizer
from recipeboxer.correlation.semantic import GeminiSemanticVerifier
from recipeboxer.exporter.plan_to_eat import PlanToEatExporter, export_recipes_to_plan_to_eat_csv
from recipeboxer.extraction.extractor import GeminiRecipeExtractor
from recipeboxer.ingestion.runner import (
    IngestionCancellationToken,
    IngestionConfig,
    IngestionMode,
    IngestionProgress,
    IngestionRunner,
    IngestionStatus,
)
from recipeboxer.ingestion.scanner import (
    CorruptImageError,
    ScannedCard,
    compute_file_sha256,
    scan_card_directory,
    scan_directory,
    validate_and_load_card,
)
from recipeboxer.models.correlation import CorrelationProposal, CorrelationResult
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
from recipeboxer.models.hitl import ApprovalPayload, CardPairApproval, HITLDecision
from recipeboxer.models.pipeline import (
    PipelineStartRequest,
    PipelineState,
    PipelineStatus,
    ProgressMetrics,
)
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.storage.cache import FingerprintCache
from recipeboxer.vision.fingerprint import GeminiVisualExtractor
from recipeboxer.vision.heuristics import (
    calculate_color_similarity,
    calculate_heuristic_distance,
    calculate_ink_similarity,
    calculate_proximity_score,
    calculate_role_compatibility,
    calculate_ruling_similarity,
    calculate_stain_similarity,
    rgb_to_lab,
    screen_candidate_pairs,
)
from tests.conftest import (
    MockGeminiClient,
    SyntheticDeckInfo,
    create_corrupted_image_file,
    create_synthetic_card_image,
)


# ============================================================================
# TEST FIXTURES
# ============================================================================

@pytest.fixture
def adversarial_session_manager() -> SessionManager:
    """Isolated SessionManager instance for adversarial tests."""
    return SessionManager()


@pytest.fixture
def adversarial_app(adversarial_session_manager: SessionManager) -> Any:
    """FastAPI application with injected isolated SessionManager."""
    return create_app(session_manager=adversarial_session_manager)


@pytest_asyncio.fixture
async def adversarial_client(adversarial_app: Any) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async HTTP test client targeting the adversarial FastAPI application."""
    transport = ASGITransport(app=adversarial_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


def make_test_card_image(
    path: Path,
    title: str = "Test Card",
    width: int = 400,
    height: int = 300,
    color: Tuple[int, int, int] = (250, 240, 220),
) -> Path:
    """Creates a valid JPEG image file for stress testing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (width, height), color=color)
    draw = ImageDraw.Draw(img)
    draw.text((20, 20), title, fill=(20, 30, 80))
    img.save(path, format="JPEG")
    return path


def make_dummy_fingerprint(card_id: str, sha256_hash: str, image_path: str = "") -> VisualFingerprint:
    """Generates a valid dummy VisualFingerprint model for testing."""
    return VisualFingerprint(
        card_id=card_id,
        image_path=image_path or f"/scans/{card_id}",
        sha256_hash=sha256_hash,
        role=CardRole.FRONT_ONLY,
        role_confidence=0.95,
        paper_color=PaperColorInfo(hex_approx="#F5E8C7", tone_name="Aged Cream"),
        ruling=PaperRulingInfo(has_ruling=True),
        ink=InkInfo(ink_color=InkColor.BLUE, ink_type=InkType.BLUE_BALLPOINT),
        boundary_text=BoundaryText(first_line="Title", last_line="End"),
        extracted_at=datetime.now(timezone.utc),
    )


# ============================================================================
# 1. SEVERELY CORRUPTED & TRUNCATED IMAGE FILES DURING SCANNING
# ============================================================================

class TestCorruptedAndTruncatedImageScanning:
    """Stress tests on corrupted, truncated, empty, and malformed files during batch scanning."""

    def test_corrupted_image_variants_scan_isolation(self, tmp_path: Path):
        """Scan directory containing diverse corrupted files, ensuring scanner isolates failures."""
        scan_dir = tmp_path / "corrupted_variants"
        scan_dir.mkdir()

        # 1. Valid images
        make_test_card_image(scan_dir / "valid_01.jpg", title="Valid 1")
        make_test_card_image(scan_dir / "valid_02.png", title="Valid 2")

        # 2. 0-byte file with image extension
        (scan_dir / "empty_file.jpg").write_bytes(b"")

        # 3. Truncated JPEG header (starts with SOI marker \xFF\xD8 but truncates abruptly)
        (scan_dir / "truncated_jpeg.jpg").write_bytes(b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00")

        # 4. Truncated PNG header
        (scan_dir / "truncated_png.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        # 5. Pseudo-random binary noise
        (scan_dir / "random_noise.webp").write_bytes(os.urandom(512))

        # 6. Plaintext ASCII file pretending to be TIFF
        (scan_dir / "fake_text.tiff").write_text("This is not a tiff image file!")

        # 7. Null-byte stream
        (scan_dir / "null_bytes.jpg").write_bytes(b"\x00" * 2048)

        # 8. AppleDouble and hidden files
        (scan_dir / "._valid_01.jpg").write_bytes(b"\x00\x05\x16\x07AppleDouble")
        (scan_dir / ".DS_Store").write_bytes(b"DesktopServicesStore")

        result = scan_directory(scan_dir, raise_on_error=False)

        # Verified 2 valid cards extracted
        assert result.valid_cards_count == 2
        assert len(result.cards) == 2
        valid_filenames = {c.filename for c in result.cards}
        assert valid_filenames == {"valid_01.jpg", "valid_02.png"}

        # Verified corrupted files cataloged with error messages
        corrupted_names = {Path(p).name for p, _ in result.corrupt_files}
        assert "empty_file.jpg" in corrupted_names
        assert "truncated_jpeg.jpg" in corrupted_names
        assert "truncated_png.png" in corrupted_names
        assert "random_noise.webp" in corrupted_names
        assert "fake_text.tiff" in corrupted_names
        assert "null_bytes.jpg" in corrupted_names

        # Verified hidden files skipped cleanly
        skipped_names = {Path(p).name for p, _ in result.skipped_files}
        assert "._valid_01.jpg" in skipped_names
        assert ".DS_Store" in skipped_names

    def test_scan_directory_raise_on_error_mode(self, tmp_path: Path):
        """When raise_on_error=True, scan_directory immediately raises on corrupted files."""
        scan_dir = tmp_path / "raise_corrupt"
        scan_dir.mkdir()
        make_test_card_image(scan_dir / "good.jpg")
        (scan_dir / "bad.jpg").write_bytes(b"CORRUPT")

        with pytest.raises((CorruptImageError, ValueError)):
            scan_directory(scan_dir, raise_on_error=True)

    @pytest.mark.asyncio
    async def test_full_pipeline_mixed_corrupt_and_valid_cards(
        self,
        adversarial_client: httpx.AsyncClient,
        adversarial_session_manager: SessionManager,
        tmp_path: Path,
    ):
        """Pipeline processes directory containing valid cards and corrupted scans without crashing."""
        scan_dir = tmp_path / "mixed_pipeline_scans"
        scan_dir.mkdir()

        # 2 Valid cards (1 standalone, 1 front)
        make_test_card_image(scan_dir / "01_cinnamon_rolls_front.jpg", title="Cinnamon Rolls")
        make_test_card_image(scan_dir / "02_chocolate_fudge_standalone.jpg", title="Chocolate Fudge")

        # 3 Corrupted cards
        (scan_dir / "03_corrupt_empty.jpg").write_bytes(b"")
        (scan_dir / "04_corrupt_header.jpg").write_bytes(b"\xFF\xD8CORRUPT_EOF")
        (scan_dir / "05_corrupt_text.png").write_text("Not a PNG")

        start_res = await adversarial_client.post("/api/pipeline/start", json={
            "scans_dir": str(scan_dir),
            "mode": "parallel",
        })
        assert start_res.status_code == 200
        sid = start_res.json()["session_id"]
        assert start_res.json()["total_scans_found"] == 5

        # Wait for HITL_PENDING
        pending_data = None
        for _ in range(100):
            res_hitl = await adversarial_client.get(f"/api/hitl/pending?session_id={sid}")
            if res_hitl.status_code == 200:
                pending_data = res_hitl.json()
                break
            await asyncio.sleep(0.02)

        assert pending_data is not None
        assert pending_data["status"] == "HITL_PENDING"

        # Valid cards were fingerprinted
        session = adversarial_session_manager.get_session(sid)
        assert session is not None
        assert len(session.fingerprints) == 2

        # Approve and finish pipeline
        approvals = [
            {
                "proposal_id": p["proposal_id"],
                "front_card_id": p["front_card_id"],
                "back_card_id": p["back_card_id"],
                "decision": "APPROVE",
            }
            for p in pending_data["proposed_pairs"]
        ]
        approve_res = await adversarial_client.post("/api/hitl/approve", json={
            "session_id": sid,
            "approvals": approvals,
        })
        assert approve_res.status_code == 200

        # Wait for completion
        for _ in range(100):
            s_res = await adversarial_client.get(f"/api/status?session_id={sid}")
            if s_res.json()["status"] == "COMPLETED":
                break
            await asyncio.sleep(0.02)

        final_s = await adversarial_client.get(f"/api/status?session_id={sid}")
        assert final_s.json()["status"] == "COMPLETED"
        assert len(session.recipes) == len(approvals)


# ============================================================================
# 2. HIGH-THROUGHPUT PARALLEL CONCURRENCY STRESS (50+ CARDS, MAX_CONCURRENCY=10)
# ============================================================================

class TestHighThroughputParallelConcurrencyStress:
    """Stress tests on parallel ingestion throughput, semaphore bound adherence, and progress monotonicity."""

    @pytest.mark.asyncio
    async def test_high_throughput_parallel_50_plus_cards(self, tmp_path: Path):
        """Stress test: Ingest 60 synthetic cards concurrently throttled by max_concurrency=10."""
        scan_dir = tmp_path / "parallel_stress_60"
        scan_dir.mkdir()

        card_count = 60
        cards: List[ScannedCard] = []
        for i in range(card_count):
            p = make_test_card_image(scan_dir / f"card_{i:03d}.jpg", title=f"Stress Recipe {i}")
            cards.append(validate_and_load_card(p))

        active_workers = 0
        peak_workers = 0
        lock = asyncio.Lock()
        extracted_cards = []

        async def stress_extractor(card: ScannedCard) -> VisualFingerprint:
            nonlocal active_workers, peak_workers
            async with lock:
                active_workers += 1
                peak_workers = max(peak_workers, active_workers)

            # Variable simulated network jitter (5ms to 25ms)
            await asyncio.sleep(random.uniform(0.005, 0.025))

            async with lock:
                active_workers -= 1
                extracted_cards.append(card.card_id)

            return make_dummy_fingerprint(card.card_id, card.sha256_hash, str(card.file_path))

        max_concurrency = 10
        config = IngestionConfig(
            mode=IngestionMode.PARALLEL,
            max_concurrency=max_concurrency,
        )
        cache = FingerprintCache(str(tmp_path / "stress_parallel.db"))
        runner = IngestionRunner(config=config, cache=cache, extractor_fn=stress_extractor)

        progress_snapshots: List[IngestionProgress] = []

        def track_progress(p: IngestionProgress):
            progress_snapshots.append(p.model_copy())

        t0 = time.monotonic()
        result = await runner.run(cards, progress_callback=track_progress)
        elapsed = time.monotonic() - t0

        # Assertions
        assert result.status == IngestionStatus.COMPLETED
        assert result.total_cards == card_count
        assert len(result.fingerprints) == card_count
        assert result.failed_count == 0
        assert result.newly_extracted == card_count
        assert result.cache_hits == 0

        # Concurrency bounds
        assert peak_workers <= max_concurrency, f"Peak workers ({peak_workers}) exceeded limit ({max_concurrency})"
        assert peak_workers >= 5, f"Peak workers ({peak_workers}) did not achieve expected parallelism"

        # Cache persistence
        assert cache.count() == card_count
        cache.close()

        # Monotonic progress assertions
        assert len(progress_snapshots) > 0
        last_pct = 0.0
        for p in progress_snapshots:
            assert p.percent_complete >= last_pct
            last_pct = p.percent_complete
        assert progress_snapshots[-1].percent_complete == 100.0

    @pytest.mark.asyncio
    async def test_parallel_concurrency_cache_warmup_and_instant_replay(self, tmp_path: Path):
        """First run extracts all cards into SQLite; second run gets 100% cache hits with zero extractor calls."""
        scan_dir = tmp_path / "cache_warmup_test"
        scan_dir.mkdir()
        cards = [
            validate_and_load_card(make_test_card_image(scan_dir / f"card_{i:02d}.jpg", title=f"Warmup {i}"))
            for i in range(25)
        ]

        extractor_call_count = 0

        async def counting_extractor(card: ScannedCard) -> VisualFingerprint:
            nonlocal extractor_call_count
            extractor_call_count += 1
            await asyncio.sleep(0.005)
            return make_dummy_fingerprint(card.card_id, card.sha256_hash, str(card.file_path))

        db_path = str(tmp_path / "warmup.db")
        cache = FingerprintCache(db_path)
        runner = IngestionRunner(
            config=IngestionConfig(mode=IngestionMode.PARALLEL, max_concurrency=8),
            cache=cache,
            extractor_fn=counting_extractor,
        )

        # Run 1: Cold cache -> 25 newly extracted
        res1 = await runner.run(cards)
        assert res1.status == IngestionStatus.COMPLETED
        assert res1.newly_extracted == 25
        assert res1.cache_hits == 0
        assert extractor_call_count == 25

        # Run 2: Warm cache -> 25 cache hits, 0 extractor calls
        res2 = await runner.run(cards)
        assert res2.status == IngestionStatus.COMPLETED
        assert res2.newly_extracted == 0
        assert res2.cache_hits == 25
        assert extractor_call_count == 25  # Extractor not called once!
        assert len(res2.fingerprints) == 25

        cache.close()


# ============================================================================
# 3. ABRUPT CANCELLATION DURING SLOW-BATCH INTERVAL SLEEP
# ============================================================================

class TestSlowBatchCancellationAndIntervals:
    """Stress tests on slow-batch chunking, inter-batch delays, and abrupt cancellation."""

    @pytest.mark.asyncio
    async def test_cancellation_during_slow_batch_interval_sleep_wakes_immediately(self, tmp_path: Path):
        """Cancel token during a long slow_batch_interval_seconds (e.g. 5.0s); runner must wake immediately."""
        scan_dir = tmp_path / "slow_cancel_sleep"
        scan_dir.mkdir()
        cards = [
            validate_and_load_card(make_test_card_image(scan_dir / f"card_{i:02d}.jpg", title=f"Card {i}"))
            for i in range(10)
        ]

        cancel_token = IngestionCancellationToken()
        extracted_count = 0

        async def fast_extractor(card: ScannedCard) -> VisualFingerprint:
            nonlocal extracted_count
            extracted_count += 1
            return make_dummy_fingerprint(card.card_id, card.sha256_hash, str(card.file_path))

        # 10 cards, batch size 2 -> 5 chunks. Interval = 5.0 seconds between chunks.
        config = IngestionConfig(
            mode=IngestionMode.SLOW_BATCH,
            slow_batch_size=2,
            slow_batch_interval_seconds=5.0,
        )
        runner = IngestionRunner(config=config, extractor_fn=fast_extractor)

        # Trigger cancellation after 150ms (during the sleep between chunk 1 and chunk 2)
        async def cancel_after_delay():
            await asyncio.sleep(0.15)
            cancel_token.cancel()

        cancel_task = asyncio.create_task(cancel_after_delay())
        t0 = time.monotonic()
        result = await runner.run(cards, cancellation_token=cancel_token)
        elapsed = time.monotonic() - t0
        await cancel_task

        assert result.status == IngestionStatus.CANCELLED
        # Extracted only chunk 1 (2 cards)
        assert extracted_count == 2
        assert len(result.fingerprints) == 2
        # Elapsed time must be significantly less than the 5.0s interval sleep!
        assert elapsed < 1.0, f"Runner did not abort sleep promptly: elapsed={elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_slow_batch_zero_interval_fast_path(self, tmp_path: Path):
        """Slow-batch with slow_batch_interval_seconds=0.0 executes as fast batch with 0 sleep."""
        scan_dir = tmp_path / "slow_zero_interval"
        scan_dir.mkdir()
        cards = [
            validate_and_load_card(make_test_card_image(scan_dir / f"card_{i:02d}.jpg"))
            for i in range(16)
        ]

        async def extractor(card: ScannedCard) -> VisualFingerprint:
            return make_dummy_fingerprint(card.card_id, card.sha256_hash)

        config = IngestionConfig(
            mode=IngestionMode.SLOW_BATCH,
            slow_batch_size=4,
            slow_batch_interval_seconds=0.0,
        )
        runner = IngestionRunner(config=config, extractor_fn=extractor)

        t0 = time.monotonic()
        result = await runner.run(cards)
        elapsed = time.monotonic() - t0

        assert result.status == IngestionStatus.COMPLETED
        assert len(result.fingerprints) == 16
        assert elapsed < 0.2


# ============================================================================
# 4. CONCURRENT MULTI-SESSION PIPELINE LAUNCHES & RACE CONDITION STRESS
# ============================================================================

class TestMultiSessionPipelineConcurrencyAndRaces:
    """Stress tests on simultaneous multi-session pipeline runs, rapid state polling, and race conditions."""

    @pytest.mark.asyncio
    async def test_simultaneous_multi_session_pipeline_execution(
        self,
        adversarial_client: httpx.AsyncClient,
        adversarial_session_manager: SessionManager,
        tmp_path: Path,
    ):
        """Launch 8 pipeline sessions concurrently, poll statuses, approve in parallel, and verify isolation."""
        num_sessions = 8
        session_dirs = []

        # Setup 8 distinct scan directories with different recipes
        for i in range(num_sessions):
            s_dir = tmp_path / f"multi_sess_deck_{i:02d}"
            s_dir.mkdir()
            # 1 front/back pair + 1 standalone in each directory
            make_test_card_image(
                s_dir / f"deck_{i:02d}_cinnamon_rolls_front.jpg",
                title=f"Deck {i} Cinnamon Rolls",
            )
            make_test_card_image(
                s_dir / f"deck_{i:02d}_cinnamon_rolls_back.jpg",
                title=f"Deck {i} Cinnamon Back",
            )
            make_test_card_image(
                s_dir / f"deck_{i:02d}_chocolate_fudge_standalone.jpg",
                title=f"Deck {i} Fudge Standalone",
            )
            session_dirs.append(s_dir)

        # 1. Launch all 8 sessions simultaneously via POST /api/pipeline/start
        async def _start_session(d: Path) -> str:
            resp = await adversarial_client.post("/api/pipeline/start", json={
                "scans_dir": str(d),
                "mode": "parallel",
                "max_concurrency": 2,
            })
            assert resp.status_code == 200
            return resp.json()["session_id"]

        session_ids = await asyncio.gather(*[_start_session(d) for d in session_dirs])
        assert len(set(session_ids)) == num_sessions

        # 2. Concurrently poll all sessions until each reaches HITL_PENDING
        async def _wait_for_hitl(sid: str) -> Dict[str, Any]:
            for _ in range(120):
                res = await adversarial_client.get(f"/api/hitl/pending?session_id={sid}")
                if res.status_code == 200:
                    return res.json()
                await asyncio.sleep(0.02)
            raise TimeoutError(f"Session {sid} did not reach HITL_PENDING")

        pending_results = await asyncio.gather(*[_wait_for_hitl(sid) for sid in session_ids])
        assert len(pending_results) == num_sessions

        # 3. Concurrently submit approvals for all 8 sessions
        async def _approve_session(pending: Dict[str, Any]) -> None:
            sid = pending["session_id"]
            approvals = [
                {
                    "proposal_id": p["proposal_id"],
                    "front_card_id": p["front_card_id"],
                    "back_card_id": p["back_card_id"],
                    "decision": "APPROVE",
                }
                for p in pending["proposed_pairs"]
            ]
            resp = await adversarial_client.post("/api/hitl/approve", json={
                "session_id": sid,
                "approvals": approvals,
            })
            assert resp.status_code == 200

        await asyncio.gather(*[_approve_session(p) for p in pending_results])

        # 4. Concurrently wait for all sessions to reach COMPLETED
        async def _wait_for_completed(sid: str) -> None:
            for _ in range(120):
                res = await adversarial_client.get(f"/api/status?session_id={sid}")
                if res.status_code == 200 and res.json()["status"] == "COMPLETED":
                    return
                await asyncio.sleep(0.02)
            raise TimeoutError(f"Session {sid} did not complete")

        await asyncio.gather(*[_wait_for_completed(sid) for sid in session_ids])

        # 5. Concurrently download and verify CSV exports from all 8 sessions
        async def _verify_export(sid: str, deck_idx: int) -> None:
            exp_res = await adversarial_client.get(f"/api/export/csv?session_id={sid}")
            assert exp_res.status_code == 200
            csv_content = exp_res.text
            reader = list(csv.reader(io.StringIO(csv_content)))
            assert len(reader) >= 2  # header + recipes
            assert reader[0] == ["Title", "Description", "Ingredients", "Directions", "Prep Time", "Total Time", "Yield", "Tags", "Source"]

        await asyncio.gather(*[_verify_export(sid, i) for i, sid in enumerate(session_ids)])

    @pytest.mark.asyncio
    async def test_rapid_concurrent_polling_under_load(
        self,
        adversarial_client: httpx.AsyncClient,
        tmp_path: Path,
    ):
        """Send 50 rapid concurrent GET /api/status requests during active ingestion."""
        scan_dir = tmp_path / "polling_load_deck"
        scan_dir.mkdir()
        for i in range(10):
            make_test_card_image(scan_dir / f"card_{i:02d}.jpg", title=f"Poll Recipe {i}")

        start_res = await adversarial_client.post("/api/pipeline/start", json={
            "scans_dir": str(scan_dir),
            "mode": "parallel",
        })
        sid = start_res.json()["session_id"]

        # Launch 50 status requests in parallel
        status_responses = await asyncio.gather(
            *[adversarial_client.get(f"/api/status?session_id={sid}") for _ in range(50)]
        )

        for r in status_responses:
            assert r.status_code == 200
            assert r.json()["session_id"] == sid
            assert "status" in r.json()
            assert "progress" in r.json()


# ============================================================================
# 5. CORRUPTED / LOCKED SQLITE CACHE RECOVERY
# ============================================================================

def _external_locker(db_path: str, hold_seconds: float, lock_acquired: mp.Event, err_q: mp.Queue) -> None:
    """Helper process to hold an EXCLUSIVE SQLite write transaction."""
    try:
        conn = sqlite3.connect(db_path, timeout=5.0, isolation_level=None)
        conn.execute("BEGIN EXCLUSIVE;")
        conn.execute(
            "INSERT INTO fingerprints (sha256_hash, card_id, image_path, role, json_data, extracted_at) VALUES (?, ?, ?, ?, ?, ?);",
            ("1" * 64, "locked.jpg", "/path/locked.jpg", "FRONT_ONLY", "{}", "2026-08-24T00:00:00Z"),
        )
        lock_acquired.set()
        time.sleep(hold_seconds)
        conn.execute("COMMIT;")
        conn.close()
    except Exception as e:
        err_q.put(str(e))
        lock_acquired.set()


class TestCorruptedAndLockedSQLiteCacheRecovery:
    """Stress tests on SQLite cache lock contention, busy timeouts, and corrupt row deserialization."""

    def test_sqlite_exclusive_lock_contention_and_busy_timeout_recovery(self, tmp_path: Path):
        """External process holds exclusive lock for 300ms; cache.put() waits out busy_timeout and succeeds."""
        db_path = str(tmp_path / "busy_contention.db")
        cache = FingerprintCache(db_path)

        lock_acquired = mp.Event()
        err_q: mp.Queue = mp.Queue()
        hold_time = 0.35  # 350ms lock hold

        p = mp.Process(target=_external_locker, args=(db_path, hold_time, lock_acquired, err_q))
        p.start()

        # Wait until lock is active in child process
        assert lock_acquired.wait(timeout=5.0) is True
        if not err_q.empty():
            pytest.fail(f"Locker process failed: {err_q.get()}")

        # Attempt to write into cache from main thread
        fp = make_dummy_fingerprint("main_thread_card.jpg", "2" * 64)
        t0 = time.monotonic()
        cache.put(fp)  # Blocks until child process commits
        elapsed = time.monotonic() - t0

        p.join(timeout=5.0)
        assert elapsed >= 0.15, "Did not wait for lock release"
        assert cache.contains("2" * 64) is True
        cache.close()

    def test_corrupted_json_row_deserialization_fallback(self, tmp_path: Path):
        """Directly insert corrupt invalid JSON string into fingerprints table; cache.get() returns None without crash."""
        db_path = str(tmp_path / "corrupt_row.db")
        cache = FingerprintCache(db_path)

        corrupt_hash = "3" * 64
        # Insert malformed JSON directly into SQLite
        with cache._lock:
            cur = cache._conn.cursor()
            cur.execute("""
                INSERT INTO fingerprints (sha256_hash, card_id, image_path, role, json_data, extracted_at)
                VALUES (?, ?, ?, ?, ?, ?);
            """, (corrupt_hash, "corrupt_row.jpg", "/scans/corrupt.jpg", "FRONT_ONLY", "{NOT_VALID_JSON: true", "2026-08-24T00:00:00Z"))

        # Verify get() returns None instead of raising JSONDecodeError
        retrieved = cache.get(corrupt_hash)
        assert retrieved is None

        # Verify get_all() skips corrupt row gracefully
        valid_fp = make_dummy_fingerprint("valid.jpg", "4" * 64)
        cache.put(valid_fp)

        all_fps = cache.get_all()
        assert len(all_fps) == 1
        assert all_fps[0].sha256_hash == "4" * 64

        cache.close()

    @pytest.mark.asyncio
    async def test_concurrent_async_cache_reads_and_writes(self, tmp_path: Path):
        """Launch 100 concurrent async cache reads, writes, and checks."""
        db_path = str(tmp_path / "async_cache_stress.db")
        cache = FingerprintCache(db_path)

        async def _cache_worker(idx: int):
            sha = f"{idx:04x}".rjust(64, "0")
            fp = make_dummy_fingerprint(f"card_{idx:04d}.jpg", sha)
            # Write
            await cache.aput(fp)
            # Check contains
            assert await cache.acontains(sha) is True
            # Read back
            found = await cache.aget(sha)
            assert found is not None
            assert found.card_id == f"card_{idx:04d}.jpg"

        await asyncio.gather(*[_cache_worker(i) for i in range(100)])
        assert await cache.acount() == 100
        cache.close()


# ============================================================================
# 6. EDGE CASE RECIPE NORMALIZATION & RFC 4180 CSV COMPLIANCE
# ============================================================================

class TestRecipeNormalizationAndRFC4180CSVCompliance:
    """Stress tests on recipe directions renumbering, tags deduplication, unicode, and RFC 4180 CSV export."""

    def test_recipe_directions_messy_numbering_normalization(self):
        """RecipeSchema directions validator cleans up and sequentially renumbers irregular step strings."""
        raw_directions = [
            "  Step 1: Dissolve yeast in warm water.  ",
            "2 - Knead dough on floured surface.  ",
            "(3) Let rise in warm place for 1 hour.",
            "4. Roll dough into rectangle.",
            "Spread softened butter and cinnamon.",  # Missing number
            "6) Roll up tightly.",
            "  ",  # Interleaved empty string
            "Step 7 - Slice into 12 rolls.",
            "8. Bake at 375°F for 25 minutes.",
        ]

        recipe = RecipeSchema(
            title="Cinnamon Rolls",
            ingredients=["Flour", "Yeast", "Cinnamon"],
            directions=raw_directions,
        )

        expected = [
            "1. Dissolve yeast in warm water.",
            "2. Knead dough on floured surface.",
            "3. Let rise in warm place for 1 hour.",
            "4. Roll dough into rectangle.",
            "5. Spread softened butter and cinnamon.",
            "6. Roll up tightly.",
            "7. Slice into 12 rolls.",
            "8. Bake at 375°F for 25 minutes.",
        ]

        assert recipe.directions == expected

    def test_recipe_tags_case_insensitive_deduplication(self):
        """RecipeSchema tags validator strips whitespace and deduplicates preserving case of first appearance."""
        raw_tags = [
            "  Dessert  ",
            "Baking",
            "dessert",  # duplicate lower
            "DESSERT",  # duplicate upper
            "Holiday",
            "baking",   # duplicate lower
            "  Vintage Family Recipe  ",
        ]

        recipe = RecipeSchema(
            title="Holiday Cake",
            ingredients=["Sugar"],
            directions=["1. Bake."],
            tags=raw_tags,
        )

        assert recipe.tags == ["Dessert", "Baking", "Holiday", "Vintage Family Recipe"]

    def test_rfc_4180_csv_embedded_quotes_commas_and_newlines(self, tmp_path: Path):
        """PlanToEatExporter properly escapes embedded double quotes (" -> ""), commas, and multi-line strings."""
        recipe = RecipeSchema(
            recipe_id="rec_rfc4180",
            title='Grandma\'s "Award-Winning" Apple Pie, Special Edition',
            description='A recipe from 1954.\nContains Grandma\'s "secret" crust method,\nfeaturing 8" pie pans.',
            yield_servings='1 9" pie (8 servings)',
            prep_time="30 mins",
            cook_time="50 mins",
            total_time="1 hr 20 mins",
            ingredients=[
                '6 cups sliced "Honeycrisp" apples',
                '1 cup sugar, divided',
                '1 tsp ground cinnamon, plus extra for dusting',
                '2 tbsp butter, cut into small pieces',
            ],
            directions=[
                '1. Preheat oven to 400°F (205°C).',
                '2. Roll out bottom crust into 9" pan.',
                '3. Toss apples with sugar and cinnamon; mound into crust.',
                '4. Dot with butter pieces.\nCover with top crust and crimp edges.',
                '5. Bake 50 minutes until bubbly and golden.',
            ],
            tags=["Dessert", "Pie", "Apple, Cinnamon"],
            source_notes='Family Cookbook "Treasures" (p. 42)',
        )

        exporter = PlanToEatExporter()
        csv_string = exporter.export_to_csv_string([recipe])

        # Validate with built-in csv.reader
        reader = list(csv.reader(io.StringIO(csv_string)))
        assert len(reader) == 2  # 1 header row + 1 data row

        header = reader[0]
        row = reader[1]

        assert len(header) == 9
        assert len(row) == 9

        assert row[0] == 'Grandma\'s "Award-Winning" Apple Pie, Special Edition'
        assert 'Contains Grandma\'s "secret" crust method' in row[1]
        assert '6 cups sliced "Honeycrisp" apples' in row[2]
        assert '1. Preheat oven to 400°F (205°C).' in row[3]
        assert row[6] == '1 9" pie (8 servings)'
        assert row[7] == 'Dessert, Pie, Apple, Cinnamon'
        assert row[8] == 'Family Cookbook "Treasures" (p. 42)'

        # Write to file and validate
        out_file = tmp_path / "rfc4180_export.csv"
        exporter.export_to_file([recipe], out_file)
        assert out_file.exists()
        assert exporter.validate_csv_format(out_file.read_text(encoding="utf-8")) is True
