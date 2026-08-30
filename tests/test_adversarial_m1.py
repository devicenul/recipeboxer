"""Adversarial stress testing suite for Milestone 1 components.

Empirical verification of:
1. FingerprintCache under high concurrent reads/writes with threads and processes (WAL mode & locking)
2. IngestionRunner under extreme parallel concurrency (100+ cards, semaphore stress)
3. IngestionRunner Slow-Batch mode (chunking, precise interval timing, zero trailing sleep)
4. Cooperative cancellation (pre-cancelled, in-flight cancellation, sleep cancellation)
5. Corrupt file & 0-byte file handling and error isolation
6. Crash simulation & job resumption with cache bypass
"""

import asyncio
import multiprocessing as mp
import os
import random
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import pytest
from PIL import Image, ImageDraw

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
    scan_directory,
    validate_and_load_card,
)
from recipeboxer.models.domain import (
    BoundaryText,
    CardRole,
    InkInfo,
    PaperColorInfo,
    PaperRulingInfo,
    VisualFingerprint,
)
from recipeboxer.storage.cache import FingerprintCache


def create_synthetic_image(
    path: Path,
    title: str = "Test Card",
    width: int = 400,
    height: int = 300,
    color: Tuple[int, int, int] = (250, 240, 220),
) -> Path:
    """Helper to create a valid test image."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (width, height), color=color)
    draw = ImageDraw.Draw(img)
    draw.text((20, 20), title, fill=(20, 30, 80))
    img.save(path, format="JPEG")
    return path


def make_dummy_fp(card_id: str, sha256_hash: str, image_path: str = "") -> VisualFingerprint:
    """Helper to make a dummy VisualFingerprint."""
    return VisualFingerprint(
        card_id=card_id,
        image_path=image_path or f"/scans/{card_id}",
        sha256_hash=sha256_hash,
        role=CardRole.FRONT_ONLY,
        role_confidence=0.95,
        paper_color=PaperColorInfo(hex_approx="#F5E8C7"),
        ruling=PaperRulingInfo(has_ruling=True),
        ink=InkInfo(ink_color="BLUE", ink_type="BALLPOINT_PEN"),
        boundary_text=BoundaryText(first_line="Title", last_line="End"),
        extracted_at=datetime.now(timezone.utc),
    )


# ============================================================================
# 1. FINGERPRINT CACHE EMPIRICAL STRESS TESTS (Multi-Process & Multi-Thread)
# ============================================================================

def _mp_worker(db_path: str, worker_id: int, num_ops: int, error_queue: mp.Queue) -> None:
    """Multi-process worker executing rapid random reads, writes, and deletes on SQLite cache."""
    try:
        cache = FingerprintCache(db_path)
        for i in range(num_ops):
            op = random.choice(["put", "get", "contains", "count", "delete"])
            card_id = f"proc_{worker_id}_card_{i % 20}.jpg"
            sha = f"proc{worker_id:02d}item{i % 20:04d}".ljust(64, "0")

            if op == "put":
                fp = make_dummy_fp(card_id, sha)
                cache.put(fp)
            elif op == "get":
                _ = cache.get(sha)
            elif op == "contains":
                _ = cache.contains(sha)
            elif op == "count":
                _ = cache.count()
            elif op == "delete":
                _ = cache.delete(sha)

        cache.close()
    except Exception as exc:
        error_queue.put((worker_id, str(exc)))


class TestFingerprintCacheStress:
    """Empirical concurrency and stress tests for FingerprintCache."""

    def test_multi_process_concurrent_wal_access(self, tmp_path: Path):
        """Stress test SQLite WAL mode across 8 separate OS processes with concurrent read/write/delete."""
        db_file = str(tmp_path / "wal_mp_stress.db")
        # Initialize DB
        init_cache = FingerprintCache(db_file)
        init_cache.close()

        num_processes = 8
        ops_per_proc = 100
        error_queue: mp.Queue = mp.Queue()

        processes = [
            mp.Process(target=_mp_worker, args=(db_file, pid, ops_per_proc, error_queue))
            for pid in range(num_processes)
        ]

        for p in processes:
            p.start()
        for p in processes:
            p.join(timeout=30.0)
            assert not p.is_alive(), f"Process {p.pid} hung during execution"

        errors = []
        while not error_queue.empty():
            errors.append(error_queue.get())

        assert errors == [], f"Multi-process WAL stress encountered errors: {errors}"

        # Verify DB integrity
        with FingerprintCache(db_file) as verify_cache:
            assert verify_cache.count() >= 0
            # Test schema and WAL pragmas
            cur = verify_cache._conn.cursor()
            cur.execute("PRAGMA integrity_check;")
            row = cur.fetchone()
            assert row[0] == "ok"

    def test_high_volume_threads_same_key_race(self, tmp_path: Path):
        """Test 20 threads simultaneously writing conflicting updates to the exact same hash."""
        db_file = str(tmp_path / "race_key.db")
        cache = FingerprintCache(db_file)
        target_hash = "a" * 64
        errors = []

        import threading

        def thread_writer(tid: int):
            try:
                for i in range(50):
                    fp = make_dummy_fp(f"card_from_t_{tid}_iter_{i}.jpg", target_hash)
                    cache.put(fp)
                    retrieved = cache.get(target_hash)
                    assert retrieved is not None
                    assert retrieved.sha256_hash == target_hash
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=thread_writer, args=(t,)) for t in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        cache.close()
        assert errors == []

        # Exactly 1 record should exist because of SHA-256 PRIMARY KEY
        with FingerprintCache(db_file) as verify_cache:
            assert verify_cache.count() == 1

    def test_polymorphic_bulk_operations(self, tmp_path: Path):
        """Verify bulk insertions, queries, and polymorphic keys under load."""
        db_file = str(tmp_path / "bulk_test.db")
        cache = FingerprintCache(db_file)

        # 500 unique fingerprints
        fps = [make_dummy_fp(f"card_{i:04d}.jpg", f"{i:04x}".ljust(64, "f")) for i in range(500)]
        for fp in fps:
            cache.put(fp)

        assert cache.count() == 500
        all_fps = cache.get_all()
        assert len(all_fps) == 500

        # Query all by hash
        for fp in fps:
            found = cache.get(fp.sha256_hash)
            assert found is not None
            assert found.card_id == fp.card_id

        # Delete in batches
        for fp in fps[:250]:
            assert cache.delete(fp.sha256_hash) is True

        assert cache.count() == 250
        cache.clear()
        assert cache.count() == 0
        cache.close()


# ============================================================================
# 2. INGESTION RUNNER EMPIRICAL STRESS TESTS (Parallel, Slow-Batch, Cancellation)
# ============================================================================

class TestIngestionRunnerStress:
    """Empirical stress tests for IngestionRunner."""

    @pytest.mark.asyncio
    async def test_parallel_mode_extreme_concurrency(self, tmp_path: Path):
        """Stress test Parallel mode with 100 cards and max_concurrency=25."""
        cards_dir = tmp_path / "extreme_parallel"
        cards_dir.mkdir()

        # Create 100 cards
        cards = []
        for i in range(100):
            p = create_synthetic_image(cards_dir / f"card_{i:03d}.jpg", title=f"Extreme Card {i}")
            cards.append(validate_and_load_card(p))

        active_tasks = 0
        peak_tasks = 0
        lock = asyncio.Lock()

        async def stress_extractor(card: ScannedCard) -> VisualFingerprint:
            nonlocal active_tasks, peak_tasks
            async with lock:
                active_tasks += 1
                peak_tasks = max(peak_tasks, active_tasks)
            # Simulate variable latency
            await asyncio.sleep(random.uniform(0.005, 0.02))
            async with lock:
                active_tasks -= 1
            return make_dummy_fp(card.card_id, card.sha256_hash, str(card.file_path))

        max_concurrency = 25
        config = IngestionConfig(mode=IngestionMode.PARALLEL, max_concurrency=max_concurrency)
        cache = FingerprintCache(str(tmp_path / "cache_par.db"))
        runner = IngestionRunner(config=config, cache=cache, extractor_fn=stress_extractor)

        t0 = time.monotonic()
        result = await runner.run(cards)
        elapsed = time.monotonic() - t0

        assert result.status == IngestionStatus.COMPLETED
        assert result.total_cards == 100
        assert len(result.fingerprints) == 100
        assert result.failed_count == 0
        assert peak_tasks <= max_concurrency
        assert peak_tasks >= 10
        assert cache.count() == 100

        cache.close()

    @pytest.mark.asyncio
    async def test_slow_batch_mode_timing_and_no_trailing_sleep(self, tmp_path: Path):
        """Stress test Slow-Batch mode: 23 cards, batch_size=5 -> 5 chunks ([5, 5, 5, 5, 3]) -> 4 sleeps."""
        cards_dir = tmp_path / "slow_batch_timing"
        cards_dir.mkdir()

        cards = [
            validate_and_load_card(create_synthetic_image(cards_dir / f"card_{i:02d}.jpg", title=f"Card {i}"))
            for i in range(23)
        ]

        batch_intervals_observed = []
        last_batch_time = None

        async def timing_extractor(card: ScannedCard) -> VisualFingerprint:
            return make_dummy_fp(card.card_id, card.sha256_hash, str(card.file_path))

        batch_size = 5
        interval = 0.05
        config = IngestionConfig(
            mode=IngestionMode.SLOW_BATCH,
            slow_batch_size=batch_size,
            slow_batch_interval_seconds=interval,
        )
        runner = IngestionRunner(config=config, extractor_fn=timing_extractor)

        t0 = time.monotonic()
        result = await runner.run(cards)
        elapsed = time.monotonic() - t0

        assert result.status == IngestionStatus.COMPLETED
        assert len(result.fingerprints) == len(cards)
        # 5 chunks -> exactly 4 sleeps of 0.05s = 0.20s minimum
        assert elapsed >= 4 * interval
        # If it had a 5th trailing sleep, elapsed would be >= 5 * interval (0.25s + overhead).
        # We ensure it finishes well before 5 intervals + generous tolerance
        assert elapsed < 5 * interval + 0.15

    @pytest.mark.asyncio
    async def test_pre_cancelled_token_returns_immediately(self, tmp_path: Path):
        """Verify passing a pre-cancelled token terminates without executing any tasks."""
        cards_dir = tmp_path / "precancel"
        cards_dir.mkdir()
        cards = [
            validate_and_load_card(create_synthetic_image(cards_dir / f"card_{i:02d}.jpg"))
            for i in range(10)
        ]

        extractor_invoked = False

        async def extractor(card: ScannedCard) -> VisualFingerprint:
            nonlocal extractor_invoked
            extractor_invoked = True
            return make_dummy_fp(card.card_id, card.sha256_hash)

        cancel_token = IngestionCancellationToken()
        cancel_token.cancel()

        runner = IngestionRunner(extractor_fn=extractor)
        result = await runner.run(cards, cancellation_token=cancel_token)

        assert result.status == IngestionStatus.CANCELLED
        assert not extractor_invoked
        assert len(result.fingerprints) == 0

    @pytest.mark.asyncio
    async def test_parallel_in_flight_cancellation(self, tmp_path: Path):
        """Verify cancelling while parallel tasks are in-flight halts processing."""
        cards_dir = tmp_path / "in_flight_cancel"
        cards_dir.mkdir()
        cards = [
            validate_and_load_card(create_synthetic_image(cards_dir / f"card_{i:02d}.jpg"))
            for i in range(50)
        ]

        cancel_token = IngestionCancellationToken()
        extracted_count = 0

        async def slow_extractor(card: ScannedCard) -> VisualFingerprint:
            nonlocal extracted_count
            await asyncio.sleep(0.05)
            extracted_count += 1
            if extracted_count >= 5:
                cancel_token.cancel()
            return make_dummy_fp(card.card_id, card.sha256_hash)

        config = IngestionConfig(mode=IngestionMode.PARALLEL, max_concurrency=5)
        runner = IngestionRunner(config=config, extractor_fn=slow_extractor)

        result = await runner.run(cards, cancellation_token=cancel_token)
        assert result.status == IngestionStatus.CANCELLED
        assert len(result.fingerprints) < 50

    @pytest.mark.asyncio
    async def test_error_isolation_with_flaky_extractor(self, tmp_path: Path):
        """Verify 30% flaky failure rate isolates errors to specific cards without breaking run."""
        cards_dir = tmp_path / "flaky_run"
        cards_dir.mkdir()
        cards = [
            validate_and_load_card(create_synthetic_image(cards_dir / f"card_{i:02d}.jpg"))
            for i in range(30)
        ]

        failing_indices = {3, 7, 12, 19, 25}

        async def flaky_extractor(card: ScannedCard) -> VisualFingerprint:
            idx = int(card.filename.split("_")[1].split(".")[0])
            if idx in failing_indices:
                raise ValueError(f"Simulated extraction failure on card {idx}")
            return make_dummy_fp(card.card_id, card.sha256_hash)

        runner = IngestionRunner(extractor_fn=flaky_extractor)
        result = await runner.run(cards)

        assert result.status == IngestionStatus.COMPLETED
        assert result.total_cards == 30
        assert len(result.fingerprints) == 25
        assert result.failed_count == 5
        failed_names = {cid for cid, _ in result.failed_cards}
        expected_failed_names = {f"card_{idx:02d}.jpg" for idx in failing_indices}
        assert failed_names == expected_failed_names


# ============================================================================
# 3. SCANNER ADVERSARIAL FILE & CORRUPTION TESTS
# ============================================================================

class TestScannerAdversarial:
    """Adversarial testing on scanner file validation, 0-byte files, and corrupted headers."""

    def test_corrupt_and_zero_byte_mixed_directory(self, tmp_path: Path):
        """Directory containing mixture of valid images, 0-byte files, corrupt headers, hidden files, and unsupported formats."""
        scan_dir = tmp_path / "adversarial_scans"
        scan_dir.mkdir()

        # 3 valid images
        create_synthetic_image(scan_dir / "valid_01.jpg")
        create_synthetic_image(scan_dir / "valid_02.png")
        create_synthetic_image(scan_dir / "valid_03.webp")

        # 2 zero-byte files
        (scan_dir / "empty_01.jpg").write_bytes(b"")
        (scan_dir / "empty_02.png").write_bytes(b"")

        # 2 corrupt headers
        (scan_dir / "corrupt_01.jpg").write_bytes(b"\xFF\xD8\xFF\xE0GARBAGE_BYTES_NO_HEADER")
        (scan_dir / "corrupt_02.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00BAD_CHUNKS")

        # 2 hidden files
        (scan_dir / ".DS_Store").write_bytes(b"hidden")
        (scan_dir / "._valid_01.jpg").write_bytes(b"appledouble")

        # 2 unsupported extension files
        (scan_dir / "notes.txt").write_text("Hello")
        (scan_dir / "recipe.pdf").write_bytes(b"%PDF-1.4")

        result = scan_directory(scan_dir, raise_on_error=False)

        assert result.total_files_found == 11
        assert result.valid_cards_count == 3
        assert len(result.cards) == 3
        assert len(result.corrupt_files) == 4  # 2 empty + 2 corrupt
        assert len(result.skipped_files) == 4  # 2 hidden + 2 unsupported

    def test_natural_sorting_complex_patterns(self, tmp_path: Path):
        """Verify sorting handles varied numerical sequences."""
        scan_dir = tmp_path / "complex_sort"
        scan_dir.mkdir()

        filenames = [
            "card_100.jpg",
            "card_2.jpg",
            "card_01.jpg",
            "card_20.jpg",
            "card_10a.jpg",
            "card_10b.jpg",
            "card_3_back.jpg",
            "card_3_front.jpg",
        ]
        for fn in filenames:
            create_synthetic_image(scan_dir / fn)

        result = scan_directory(scan_dir)
        sorted_names = [c.filename for c in result.cards]
        expected = [
            "card_01.jpg",
            "card_2.jpg",
            "card_3_back.jpg",
            "card_3_front.jpg",
            "card_10a.jpg",
            "card_10b.jpg",
            "card_20.jpg",
            "card_100.jpg",
        ]
        assert sorted_names == expected


# ============================================================================
# 4. SIMULATED CRASH & RESUMPTION TEST
# ============================================================================

class TestCrashSimulationAndResumption:
    """Verify system resumes after simulated mid-job crash without re-extracting cached items."""

    @pytest.mark.asyncio
    async def test_simulated_crash_and_recovery(self, tmp_path: Path):
        """
        1. Run 30 cards with a crash triggered after card 15.
        2. Verify first 15 cards are stored in persistent SQLite cache.
        3. Re-run on all 30 cards.
        4. Verify cards 1-15 are instant CACHE HITS, cards 16-30 are newly extracted.
        """
        scan_dir = tmp_path / "crash_resumption_scans"
        scan_dir.mkdir()
        db_path = str(tmp_path / "resumption.db")

        cards = [
            validate_and_load_card(create_synthetic_image(scan_dir / f"card_{i:02d}.jpg", title=f"Card {i}"))
            for i in range(30)
        ]

        # Phase 1: Run with crash triggered at card 15
        cache_session1 = FingerprintCache(db_path)
        extracted_phase1 = 0
        cancel_token_p1 = IngestionCancellationToken()

        async def crashing_extractor(card: ScannedCard) -> VisualFingerprint:
            nonlocal extracted_phase1
            extracted_phase1 += 1
            if extracted_phase1 == 15:
                # Signal abrupt halt / crash
                cancel_token_p1.cancel()
            return make_dummy_fp(card.card_id, card.sha256_hash, str(card.file_path))

        # Use SLOW_BATCH with batch_size=15 so first batch completes, second batch fails/cancels
        config_p1 = IngestionConfig(
            mode=IngestionMode.SLOW_BATCH,
            slow_batch_size=15,
            slow_batch_interval_seconds=0.1,
        )
        runner_p1 = IngestionRunner(config=config_p1, cache=cache_session1, extractor_fn=crashing_extractor)

        res_p1 = await runner_p1.run(cards, cancellation_token=cancel_token_p1)
        assert res_p1.status == IngestionStatus.CANCELLED
        cache_session1.close()

        # Verify exactly 15 records in SQLite cache
        with FingerprintCache(db_path) as check_cache:
            assert check_cache.count() == 15

        # Phase 2: Resume pipeline with fresh runner and persistent cache
        cache_session2 = FingerprintCache(db_path)
        extractor_calls_phase2 = 0

        async def resume_extractor(card: ScannedCard) -> VisualFingerprint:
            nonlocal extractor_calls_phase2
            extractor_calls_phase2 += 1
            return make_dummy_fp(card.card_id, card.sha256_hash, str(card.file_path))

        config_p2 = IngestionConfig(mode=IngestionMode.PARALLEL, max_concurrency=10)
        runner_p2 = IngestionRunner(config=config_p2, cache=cache_session2, extractor_fn=resume_extractor)

        result_p2 = await runner_p2.run(cards)

        assert result_p2.status == IngestionStatus.COMPLETED
        assert result_p2.total_cards == 30
        assert result_p2.cache_hits == 15
        assert result_p2.newly_extracted == 15
        assert extractor_calls_phase2 == 15  # Extractor was ONLY called for remaining 15 items!
        assert len(result_p2.fingerprints) == 30
        assert cache_session2.count() == 30

        cache_session2.close()


# ============================================================================
# 5. ADVANCED EDGE CASES & ROBUSTNESS TESTS
# ============================================================================

def _lock_holding_worker(db_path: str, hold_duration: float, ready_event: mp.Event, error_queue: mp.Queue) -> None:
    """Helper process that acquires an active write lock on SQLite for a brief period."""
    try:
        conn = sqlite3.connect(db_path, timeout=10.0, isolation_level=None)
        conn.execute("BEGIN EXCLUSIVE;")
        conn.execute(
            "INSERT INTO fingerprints (sha256_hash, card_id, image_path, role, json_data, extracted_at) VALUES (?, ?, ?, ?, ?, ?);",
            ("0" * 64, "held.jpg", "/path/held.jpg", "FRONT_ONLY", "{}", "2026-08-24T00:00:00Z"),
        )
        ready_event.set()
        time.sleep(hold_duration)
        conn.execute("COMMIT;")
        conn.close()
    except Exception as e:
        error_queue.put(str(e))
        ready_event.set()


class TestAdvancedEdgeCases:
    """Tests for lock contention, duplicate batch items, failing callbacks, and unicode resilience."""

    def test_exclusive_lock_contention_and_busy_timeout(self, tmp_path: Path):
        """Verify that SQLite busy_timeout=5000 allows writer processes to wait out an exclusive lock without crashing."""
        db_file = str(tmp_path / "busy_lock.db")
        # Initialize DB and keep connection open
        cache = FingerprintCache(db_file)

        ready_event = mp.Event()
        error_queue: mp.Queue = mp.Queue()
        hold_time = 0.4  # Hold exclusive lock for 400ms
        locker_proc = mp.Process(target=_lock_holding_worker, args=(db_file, hold_time, ready_event, error_queue))
        locker_proc.start()

        # Wait until lock is actively held by child process
        assert ready_event.wait(timeout=5.0) is True

        if not error_queue.empty():
            err = error_queue.get()
            pytest.fail(f"Locker proc failed: {err}")

        # Now attempt to write from pre-existing connection (should wait up to busy_timeout and succeed)
        fp = make_dummy_fp("contention_card.jpg", "e" * 64)
        t0 = time.monotonic()
        cache.put(fp)  # Blocks until child process commits transaction
        elapsed = time.monotonic() - t0

        locker_proc.join(timeout=5.0)
        assert elapsed >= 0.15  # Verified active contention and busy_timeout waiting
        assert cache.contains("e" * 64) is True
        cache.close()

    @pytest.mark.asyncio
    async def test_duplicate_cards_in_same_ingestion_batch(self, tmp_path: Path):
        """Verify ingestion of duplicate cards (same SHA-256 hash in same batch) resolves cleanly without duplicate key error."""
        scan_dir = tmp_path / "dup_cards"
        scan_dir.mkdir()
        img_path = create_synthetic_image(scan_dir / "base_card.jpg")
        card = validate_and_load_card(img_path)

        # List with 5 duplicates of the exact same ScannedCard
        cards = [card] * 5

        cache = FingerprintCache(str(tmp_path / "dup_cache.db"))
        extracted_count = 0

        async def extractor(c: ScannedCard) -> VisualFingerprint:
            nonlocal extracted_count
            extracted_count += 1
            return make_dummy_fp(c.card_id, c.sha256_hash)

        config = IngestionConfig(mode=IngestionMode.PARALLEL, max_concurrency=5)
        runner = IngestionRunner(config=config, cache=cache, extractor_fn=extractor)

        result = await runner.run(cards)
        assert result.status == IngestionStatus.COMPLETED
        assert result.total_cards == 5
        assert len(result.fingerprints) == 5
        # In SQLite cache, ON CONFLICT DO UPDATE handles duplicate hashes seamlessly
        assert cache.count() == 1
        cache.close()

    @pytest.mark.asyncio
    async def test_failing_progress_callback_does_not_break_ingestion(self, tmp_path: Path):
        """Verify that an exception inside progress_callback is safely caught and does not fail card ingestion."""
        scan_dir = tmp_path / "callback_fail"
        scan_dir.mkdir()
        cards = [
            validate_and_load_card(create_synthetic_image(scan_dir / f"card_{i:02d}.jpg"))
            for i in range(5)
        ]

        def broken_callback(p: IngestionProgress):
            raise RuntimeError("Intentional progress callback crash!")

        async def extractor(c: ScannedCard) -> VisualFingerprint:
            return make_dummy_fp(c.card_id, c.sha256_hash)

        runner = IngestionRunner(extractor_fn=extractor)
        result = await runner.run(cards, progress_callback=broken_callback)

        assert result.status == IngestionStatus.COMPLETED
        assert len(result.fingerprints) == 5
        assert result.failed_count == 0

    @pytest.mark.asyncio
    async def test_missing_extractor_fn_fails_gracefully(self, tmp_path: Path):
        """Verify that running without an extractor_fn on cache miss returns status FAILED with diagnostics."""
        scan_dir = tmp_path / "no_extractor"
        scan_dir.mkdir()
        cards = [
            validate_and_load_card(create_synthetic_image(scan_dir / f"card_{i:02d}.jpg"))
            for i in range(3)
        ]

        runner = IngestionRunner(extractor_fn=None)
        result = await runner.run(cards)

        assert result.status == IngestionStatus.FAILED
        assert result.failed_count == 3
        assert len(result.fingerprints) == 0
        for _, err in result.failed_cards:
            assert "No extractor_fn provided" in err

    @pytest.mark.asyncio
    async def test_slow_batch_size_larger_than_dataset(self, tmp_path: Path):
        """Verify slow-batch handles batch_size > len(cards) as a single immediate chunk with 0 sleep."""
        scan_dir = tmp_path / "large_batch_size"
        scan_dir.mkdir()
        cards = [
            validate_and_load_card(create_synthetic_image(scan_dir / f"card_{i:02d}.jpg"))
            for i in range(4)
        ]

        async def extractor(c: ScannedCard) -> VisualFingerprint:
            return make_dummy_fp(c.card_id, c.sha256_hash)

        config = IngestionConfig(
            mode=IngestionMode.SLOW_BATCH,
            slow_batch_size=50,  # Max allowed, >> 4
            slow_batch_interval_seconds=2.0,  # Should NOT sleep because 1 chunk only
        )
        runner = IngestionRunner(config=config, extractor_fn=extractor)

        t0 = time.monotonic()
        result = await runner.run(cards)
        elapsed = time.monotonic() - t0

        assert result.status == IngestionStatus.COMPLETED
        assert len(result.fingerprints) == 4
        # Single chunk means 0 inter-batch intervals -> should take < 0.2s
        assert elapsed < 0.2

    def test_schema_unicode_and_special_characters_resilience(self, tmp_path: Path):
        """Verify models and cache handle unicode, emojis, fractions, quotes, and newlines."""
        db_file = str(tmp_path / "unicode_cache.db")
        cache = FingerprintCache(db_file)

        complex_fp = VisualFingerprint(
            card_id="Grandma's Café & Bakery — Crème Brûlée #1 🍰.jpg",
            image_path="/recipes/Grandma's Café & Bakery — Crème Brûlée #1 🍰.jpg",
            sha256_hash="d" * 64,
            role=CardRole.FRONT_ONLY,
            role_confidence=0.999,
            paper_color=PaperColorInfo(
                tone_name="Aged parchment 📜",
                hex_approx="#F5E8C7",
                lab_l=92.5,
                lab_a=-1.2,
                lab_b=14.3,
            ),
            ruling=PaperRulingInfo(has_ruling=True),
            ink=InkInfo(ink_color="Royal Blue 🖋️", ink_type="FOUNTAIN_PEN"),
            boundary_text=BoundaryText(
                first_line="« Grandma's Special » — 1½ cups double cream & ½ vanilla pod",
                last_line="Whisk vigorously; see reverse -> 🧑‍🍳",
                has_continuation_symbol=True,
                is_cut_off_mid_sentence=False,
            ),
            extracted_at=datetime(2026, 8, 24, 12, 34, 56, tzinfo=timezone.utc),
        )

        cache.put(complex_fp)
        retrieved = cache.get("d" * 64)

        assert retrieved is not None
        assert retrieved.card_id == complex_fp.card_id
        assert retrieved.boundary_text.first_line == complex_fp.boundary_text.first_line
        assert retrieved.boundary_text.last_line == complex_fp.boundary_text.last_line
        assert retrieved.paper_color.tone_name == "Aged parchment 📜"
        cache.close()

