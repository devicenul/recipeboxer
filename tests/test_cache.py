"""Unit tests for SQLite content-addressable FingerprintCache (recipeboxer/storage/cache.py)."""

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
import pytest

from recipeboxer.models.domain import (
    BoundaryText,
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
from recipeboxer.storage.cache import FingerprintCache


@pytest.fixture
def sample_fingerprint() -> VisualFingerprint:
    """Fixture providing a complete VisualFingerprint model."""
    return VisualFingerprint(
        card_id="card_001.jpg",
        image_path="/scans/card_001.jpg",
        sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        role=CardRole.FRONT_ONLY,
        role_confidence=0.96,
        paper_color=PaperColorInfo(
            tone_name="Aged Cream",
            hex_approx="#FDF6E2",
            lab_l=95.0,
            lab_a=1.2,
            lab_b=8.5,
            tone_enum=PaperColorTone.OFF_WHITE_CREAM,
        ),
        ruling=PaperRulingInfo(
            has_ruling=True,
            ruling_type=PaperRulingType.HORIZONTAL_LINES,
            line_spacing_px=24.0,
        ),
        ink=InkInfo(ink_color=InkColor.BLUE, ink_type=InkType.BLUE_BALLPOINT),
        artifacts=[
            PhysicalArtifact(
                artifact_type="OIL_STAIN",
                quadrant="TOP_RIGHT",
                description="Butter smudge",
            )
        ],
        boundary_text=BoundaryText(
            first_line="Grandma's Chocolate Chip Cookies",
            last_line="Turn over for baking instructions ->",
            has_continuation_symbol=True,
            is_cut_off_mid_sentence=False,
        ),
        extracted_at=datetime.now(timezone.utc),
    )


def test_cache_crud_in_memory(sample_fingerprint: VisualFingerprint):
    """Verify in-memory SQLite CRUD operations."""
    with FingerprintCache(":memory:") as cache:
        assert cache.count() == 0
        assert not cache.contains(sample_fingerprint.sha256_hash)

        # 1. Put
        cache.put(sample_fingerprint)
        assert cache.count() == 1
        assert cache.contains(sample_fingerprint.sha256_hash)

        # 2. Get by hash
        retrieved = cache.get(sample_fingerprint.sha256_hash)
        assert retrieved is not None
        assert retrieved.card_id == sample_fingerprint.card_id
        assert retrieved.role == CardRole.FRONT_ONLY
        assert retrieved.paper_color.hex_approx == "#FDF6E2"
        assert len(retrieved.artifacts) == 1

        # 3. Get by hash alias
        retrieved_alias = cache.get_by_hash(sample_fingerprint.sha256_hash)
        assert retrieved_alias is not None
        assert retrieved_alias.card_id == sample_fingerprint.card_id

        # 4. Get by card_id
        by_card = cache.get_by_card_id("card_001.jpg")
        assert by_card is not None
        assert by_card.sha256_hash == sample_fingerprint.sha256_hash

        # 5. Get by role
        by_role_front = cache.get_by_role(CardRole.FRONT_ONLY)
        assert len(by_role_front) == 1
        by_role_back = cache.get_by_role(CardRole.BACK_ONLY)
        assert len(by_role_back) == 0

        # 6. Get all
        all_fps = cache.get_all()
        assert len(all_fps) == 1
        assert all_fps[0].card_id == "card_001.jpg"

        # 7. Delete
        assert cache.delete(sample_fingerprint.sha256_hash) is True
        assert cache.count() == 0
        assert cache.get(sample_fingerprint.sha256_hash) is None

        # 8. Clear
        cache.put(sample_fingerprint)
        assert cache.count() == 1
        deleted_count = cache.clear()
        assert deleted_count == 1
        assert cache.count() == 0


def test_file_persistence_and_directory_creation(sample_fingerprint: VisualFingerprint, tmp_path: Path):
    """Verify file persistence across connection closing and automatic directory creation."""
    db_file = tmp_path / "deep" / "nested" / "dir" / "fingerprints.db"

    # Session 1: Write to cache and close
    with FingerprintCache(db_file) as cache1:
        cache1.put(sample_fingerprint)
        assert cache1.count() == 1

    # Session 2: Re-open existing database and verify persistence
    with FingerprintCache(db_file) as cache2:
        assert cache2.count() == 1
        cached = cache2.get(sample_fingerprint.sha256_hash)
        assert cached is not None
        assert cached.card_id == sample_fingerprint.card_id
        assert cached.role == CardRole.FRONT_ONLY


def test_polymorphic_hash_computation(tmp_path: Path):
    """Verify compute_hash handles bytes, Path, str path, and 64-char hex strings."""
    data = b"RecipeBoxer image scan payload sample content"
    expected_hash = "68c92a95c96ad6a10dfa6c3ddfa1bc0f1c305cbe0cbb00b46eb022f300c99f1b"

    # 1. From bytes
    from_bytes = FingerprintCache.compute_hash(data)
    assert len(from_bytes) == 64

    # 2. From file Path
    img_file = tmp_path / "test_scan.jpg"
    img_file.write_bytes(data)
    from_path = FingerprintCache.compute_hash(img_file)
    assert from_path == from_bytes

    # 3. From str file path
    from_str_path = FingerprintCache.compute_hash(str(img_file))
    assert from_str_path == from_bytes

    # 4. From valid 64-char hex string (case insensitive)
    from_hex = FingerprintCache.compute_hash(from_bytes.upper())
    assert from_hex == from_bytes.lower()

    # 5. Invalid inputs raise ValueError
    with pytest.raises(ValueError):
        FingerprintCache.compute_hash("short_string_not_a_path")


def test_concurrent_multithreaded_access(sample_fingerprint: VisualFingerprint, tmp_path: Path):
    """Verify thread-safety and zero database locking errors under concurrent load."""
    db_file = tmp_path / "concurrent_test.db"
    cache = FingerprintCache(db_file)
    errors = []

    def writer_worker(worker_id: int):
        try:
            for i in range(20):
                fp = sample_fingerprint.model_copy(deep=True)
                fp.card_id = f"card_{worker_id}_{i}.jpg"
                fp.sha256_hash = f"{worker_id:02x}{i:02x}" + "a" * 60
                cache.put(fp)
        except Exception as exc:
            errors.append(exc)

    def reader_worker():
        try:
            for _ in range(40):
                _ = cache.count()
                _ = cache.get_all()
        except Exception as exc:
            errors.append(exc)

    writers = [threading.Thread(target=writer_worker, args=(w,)) for w in range(4)]
    readers = [threading.Thread(target=reader_worker) for _ in range(4)]

    for t in writers + readers:
        t.start()
    for t in writers + readers:
        t.join()

    cache.close()
    assert errors == []
    # 4 writers * 20 items = 80 total records
    with FingerprintCache(db_file) as verify_cache:
        assert verify_cache.count() == 80


@pytest.mark.asyncio
async def test_async_interface(sample_fingerprint: VisualFingerprint):
    """Verify non-blocking async wrapper methods."""
    async with FingerprintCache(":memory:") as cache:
        assert await cache.acount() == 0
        assert not await cache.acontains(sample_fingerprint.sha256_hash)

        # aput
        await cache.aput(sample_fingerprint)
        assert await cache.acount() == 1
        assert await cache.acontains(sample_fingerprint.sha256_hash)

        # aget & aget_by_hash
        retrieved = await cache.aget(sample_fingerprint.sha256_hash)
        assert retrieved is not None
        assert retrieved.card_id == sample_fingerprint.card_id

        retrieved_alias = await cache.aget_by_hash(sample_fingerprint.sha256_hash)
        assert retrieved_alias is not None

        # aget_by_card_id & aget_by_role
        by_card = await cache.aget_by_card_id("card_001.jpg")
        assert by_card is not None
        by_role = await cache.aget_by_role(CardRole.FRONT_ONLY)
        assert len(by_role) == 1

        # aget_all
        all_fps = await cache.aget_all()
        assert len(all_fps) == 1

        # adelete
        assert await cache.adelete(sample_fingerprint.sha256_hash) is True
        assert await cache.acount() == 0

        # aclear
        await cache.aput(sample_fingerprint)
        assert await cache.aclear() == 1
        assert await cache.acount() == 0


def test_corrupt_json_handling(tmp_path: Path):
    """Verify cache returns None gracefully when database contains corrupt JSON."""
    db_file = tmp_path / "corrupt_json.db"
    with FingerprintCache(db_file) as cache:
        pass

    # Insert corrupt JSON directly via sqlite3
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            "INSERT INTO fingerprints (sha256_hash, card_id, image_path, role, json_data, extracted_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("f" * 64, "corrupt.jpg", "/tmp/corrupt.jpg", "FRONT_ONLY", "{bad_json", "2026-08-24T20:00:00Z"),
        )
        conn.commit()

    with FingerprintCache(db_file) as cache:
        assert cache.contains("f" * 64) is True
        assert cache.get("f" * 64) is None  # Returns None instead of crashing


def test_image_path_override(sample_fingerprint: VisualFingerprint):
    """Verify get(sha256, image_path=...) overrides image_path if card moved."""
    with FingerprintCache(":memory:") as cache:
        cache.put(sample_fingerprint)
        new_path = "/new/moved/path/card_001.jpg"
        retrieved = cache.get(sample_fingerprint.sha256_hash, image_path=new_path)
        assert retrieved is not None
        assert retrieved.image_path == new_path
