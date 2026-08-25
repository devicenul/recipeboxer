"""Unit tests for RecipeBoxer Ingestion Engine (scanner.py & runner.py)."""

import asyncio
import hashlib
import time
from pathlib import Path
from typing import List
from PIL import Image, ImageDraw
import pytest

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
    natural_sort_key,
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


def create_test_image(
    path: Path,
    width: int = 400,
    height: int = 300,
    color: tuple = (250, 240, 220),
    fmt: str = "JPEG",
) -> Path:
    """Helper to generate a clean synthetic PIL image on disk with unique content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (width, height), color=color)
    draw = ImageDraw.Draw(img)
    draw.text((10, 10), f"Card Scan: {path.name}", fill=(10, 20, 50))
    img.save(path, format=fmt)
    return path


# ============================================================================
# 1. SCANNER TESTS (recipeboxer/ingestion/scanner.py)
# ============================================================================

def test_scanner_valid_extensions_filter(tmp_path: Path):
    """Verify only supported image formats are included; non-images are skipped."""
    scan_dir = tmp_path / "mixed_files"
    scan_dir.mkdir()

    create_test_image(scan_dir / "card1.jpg")
    create_test_image(scan_dir / "card2.jpeg")
    create_test_image(scan_dir / "card3.png", fmt="PNG")
    create_test_image(scan_dir / "card4.webp", fmt="WEBP")
    create_test_image(scan_dir / "card5.tiff", fmt="TIFF")

    # Non-image files
    (scan_dir / "notes.txt").write_text("Some text")
    (scan_dir / "document.pdf").write_bytes(b"%PDF-1.4...")
    (scan_dir / "data.csv").write_text("a,b,c")

    result = scan_directory(scan_dir)
    assert result.total_files_found == 8
    assert result.valid_cards_count == 5
    assert len(result.cards) == 5
    assert len(result.skipped_files) == 3


def test_scanner_case_insensitive_extensions(tmp_path: Path):
    """Verify scanner handles uppercase and mixed case extensions (.JPG, .Png, .WEBP, .TIFF)."""
    scan_dir = tmp_path / "case_test"
    scan_dir.mkdir()

    create_test_image(scan_dir / "scan1.JPG")
    create_test_image(scan_dir / "scan2.Png", fmt="PNG")
    create_test_image(scan_dir / "scan3.WEBP", fmt="WEBP")
    create_test_image(scan_dir / "scan4.TIFF", fmt="TIFF")

    result = scan_directory(scan_dir)
    assert result.valid_cards_count == 4
    filenames = [c.filename for c in result.cards]
    assert "scan1.JPG" in filenames
    assert "scan2.Png" in filenames


def test_scanner_natural_sorting_order(tmp_path: Path):
    """Verify alphanumeric human sorting (e.g. card_1, card_2, card_10, card_20)."""
    scan_dir = tmp_path / "natural_sort"
    scan_dir.mkdir()

    create_test_image(scan_dir / "card_1.jpg")
    create_test_image(scan_dir / "card_10.jpg")
    create_test_image(scan_dir / "card_2.jpg")
    create_test_image(scan_dir / "card_20.jpg")

    result = scan_directory(scan_dir)
    card_ids = [c.filename for c in result.cards]
    assert card_ids == ["card_1.jpg", "card_2.jpg", "card_10.jpg", "card_20.jpg"]


def test_scanner_corrupt_image_detection(tmp_path: Path):
    """Verify corrupt images are caught and recorded in corrupt_files."""
    scan_dir = tmp_path / "corrupt_test"
    scan_dir.mkdir()

    valid_card = scan_dir / "good.jpg"
    create_test_image(valid_card)

    corrupt_card = scan_dir / "bad.jpg"
    corrupt_card.write_bytes(b"NOT_A_VALID_IMAGE_HEADER_GARBAGE")

    # 1. validate_and_load_card raises CorruptImageError
    with pytest.raises(CorruptImageError):
        validate_and_load_card(corrupt_card)

    # 2. scan_directory(raise_on_error=False) records corrupt file
    result = scan_directory(scan_dir, raise_on_error=False)
    assert result.valid_cards_count == 1
    assert len(result.corrupt_files) == 1
    assert "bad.jpg" in result.corrupt_files[0][0]

    # 3. scan_directory(raise_on_error=True) raises exception
    with pytest.raises(CorruptImageError):
        scan_directory(scan_dir, raise_on_error=True)


def test_scanner_zero_byte_file(tmp_path: Path):
    """Verify 0-byte image files are rejected."""
    empty_file = tmp_path / "empty.jpg"
    empty_file.write_bytes(b"")

    with pytest.raises(ValueError, match="Empty"):
        validate_and_load_card(empty_file)


def test_scanner_metadata_extraction(tmp_path: Path):
    """Verify dimension, format, and mode metadata extraction."""
    img_path = tmp_path / "meta_test.png"
    create_test_image(img_path, width=500, height=350, fmt="PNG")

    card = validate_and_load_card(img_path)
    assert card.width == 500
    assert card.height == 350
    assert card.format == "PNG"
    assert card.mode == "RGB"
    assert card.file_size_bytes > 0
    assert len(card.sha256_hash) == 64


def test_scanner_hidden_and_appledouble_files(tmp_path: Path):
    """Verify .DS_Store and AppleDouble ._* files are skipped."""
    scan_dir = tmp_path / "hidden_test"
    scan_dir.mkdir()

    create_test_image(scan_dir / "card1.jpg")
    (scan_dir / ".DS_Store").write_bytes(b"macOS desktop services store")
    (scan_dir / "._card1.jpg").write_bytes(b"AppleDouble resource fork")

    result = scan_directory(scan_dir)
    assert result.valid_cards_count == 1
    assert len(result.skipped_files) == 2


def test_scanner_missing_and_empty_directory(tmp_path: Path):
    """Verify missing directory raises FileNotFoundError and empty directory returns 0 cards."""
    missing_dir = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError):
        scan_directory(missing_dir)

    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()
    result = scan_directory(empty_dir)
    assert result.total_files_found == 0
    assert result.valid_cards_count == 0


# ============================================================================
# 2. RUNNER TESTS (recipeboxer/ingestion/runner.py)
# ============================================================================

def make_mock_fingerprint(card: ScannedCard, role: CardRole = CardRole.STANDALONE) -> VisualFingerprint:
    """Helper to construct a mock VisualFingerprint for testing."""
    return VisualFingerprint(
        card_id=card.card_id,
        image_path=str(card.file_path),
        sha256_hash=card.sha256_hash,
        role=role,
        role_confidence=0.98,
        paper_color=PaperColorInfo(hex_approx="#FAF6EE"),
        ruling=PaperRulingInfo(has_ruling=True),
        ink=InkInfo(ink_color="BLUE", ink_type="BALLPOINT_PEN"),
        boundary_text=BoundaryText(first_line="Title", last_line="Done"),
    )


@pytest.mark.asyncio
async def test_runner_parallel_semaphore_concurrency(tmp_path: Path):
    """Verify parallel mode honors max_concurrency cap under asyncio.Semaphore."""
    scan_dir = tmp_path / "parallel_cards"
    scan_dir.mkdir()
    cards = [validate_and_load_card(create_test_image(scan_dir / f"card_{i:02d}.jpg")) for i in range(8)]

    max_concurrency = 3
    active_tasks = 0
    peak_active_tasks = 0

    async def mock_extractor(card: ScannedCard) -> VisualFingerprint:
        nonlocal active_tasks, peak_active_tasks
        active_tasks += 1
        peak_active_tasks = max(peak_active_tasks, active_tasks)
        await asyncio.sleep(0.03)  # simulate vision work
        active_tasks -= 1
        return make_mock_fingerprint(card)

    config = IngestionConfig(mode=IngestionMode.PARALLEL, max_concurrency=max_concurrency)
    runner = IngestionRunner(config=config, extractor_fn=mock_extractor)

    result = await runner.run(cards)
    assert result.status == IngestionStatus.COMPLETED
    assert len(result.fingerprints) == 8
    assert peak_active_tasks <= max_concurrency
    assert peak_active_tasks > 1


@pytest.mark.asyncio
async def test_runner_slow_batch_chunking_and_no_trailing_sleep(tmp_path: Path):
    """Verify slow-batch processes chunks with interval sleep and no trailing sleep after the final batch."""
    scan_dir = tmp_path / "slow_cards"
    scan_dir.mkdir()
    cards = [validate_and_load_card(create_test_image(scan_dir / f"card_{i:02d}.jpg")) for i in range(5)]

    async def mock_extractor(card: ScannedCard) -> VisualFingerprint:
        return make_mock_fingerprint(card)

    # 5 cards with batch_size=2 -> 3 chunks ([2, 2, 1]) -> exactly 2 sleeps
    batch_size = 2
    interval = 0.05
    config = IngestionConfig(
        mode=IngestionMode.SLOW_BATCH,
        slow_batch_size=batch_size,
        slow_batch_interval_seconds=interval,
    )
    runner = IngestionRunner(config=config, extractor_fn=mock_extractor)

    t0 = time.monotonic()
    result = await runner.run(cards)
    elapsed = time.monotonic() - t0

    assert result.status == IngestionStatus.COMPLETED
    assert len(result.fingerprints) == 5
    assert elapsed >= 2 * interval
    # Should not take 3 intervals (no trailing sleep)
    assert elapsed < 3 * interval + 0.10


@pytest.mark.asyncio
async def test_runner_cache_hit_skips_extractor(tmp_path: Path):
    """Verify pre-cached cards skip extractor function calls entirely."""
    scan_dir = tmp_path / "cache_skip"
    scan_dir.mkdir()
    cards = [validate_and_load_card(create_test_image(scan_dir / f"card_{i:02d}.jpg")) for i in range(4)]

    cache = FingerprintCache(":memory:")
    # Pre-populate cache with first 2 cards
    cache.put(make_mock_fingerprint(cards[0]))
    cache.put(make_mock_fingerprint(cards[1]))

    extractor_call_count = 0

    async def mock_extractor(card: ScannedCard) -> VisualFingerprint:
        nonlocal extractor_call_count
        extractor_call_count += 1
        return make_mock_fingerprint(card)

    runner = IngestionRunner(cache=cache, extractor_fn=mock_extractor)
    result = await runner.run(cards)

    assert result.total_cards == 4
    assert result.cache_hits == 2
    assert result.newly_extracted == 2
    assert extractor_call_count == 2
    assert len(result.fingerprints) == 4


@pytest.mark.asyncio
async def test_runner_cache_write_through(tmp_path: Path):
    """Verify newly extracted fingerprints are automatically written through to cache."""
    scan_dir = tmp_path / "cache_write"
    scan_dir.mkdir()
    cards = [validate_and_load_card(create_test_image(scan_dir / f"card_{i:02d}.jpg")) for i in range(3)]

    cache = FingerprintCache(":memory:")
    assert cache.count() == 0

    async def mock_extractor(card: ScannedCard) -> VisualFingerprint:
        return make_mock_fingerprint(card)

    runner = IngestionRunner(cache=cache, extractor_fn=mock_extractor)
    result = await runner.run(cards)

    assert result.status == IngestionStatus.COMPLETED
    assert result.newly_extracted == 3
    assert cache.count() == 3
    for c in cards:
        assert cache.contains(c.sha256_hash) is True


@pytest.mark.asyncio
async def test_runner_error_isolation_partial_success(tmp_path: Path):
    """Verify failure on one card does not abort remaining cards (error isolation)."""
    scan_dir = tmp_path / "error_iso"
    scan_dir.mkdir()
    cards = [validate_and_load_card(create_test_image(scan_dir / f"card_{i:02d}.jpg")) for i in range(4)]

    async def mock_extractor(card: ScannedCard) -> VisualFingerprint:
        if card.filename == "card_02.jpg":
            raise RuntimeError("API quota exhausted on card 2")
        return make_mock_fingerprint(card)

    runner = IngestionRunner(extractor_fn=mock_extractor)
    result = await runner.run(cards)

    assert result.total_cards == 4
    assert len(result.fingerprints) == 3
    assert result.failed_count == 1
    assert result.failed_cards[0][0] == "card_02.jpg"
    assert "API quota exhausted" in result.failed_cards[0][1]
    assert result.status == IngestionStatus.COMPLETED


@pytest.mark.asyncio
async def test_runner_cooperative_cancellation_during_sleep(tmp_path: Path):
    """Verify cancellation token wakes up immediately during slow-batch sleep."""
    scan_dir = tmp_path / "cancel_sleep"
    scan_dir.mkdir()
    cards = [validate_and_load_card(create_test_image(scan_dir / f"card_{i:02d}.jpg")) for i in range(6)]

    cancel_token = IngestionCancellationToken()

    async def mock_extractor(card: ScannedCard) -> VisualFingerprint:
        return make_mock_fingerprint(card)

    # Set long interval (5 seconds)
    config = IngestionConfig(
        mode=IngestionMode.SLOW_BATCH,
        slow_batch_size=2,
        slow_batch_interval_seconds=5.0,
    )
    runner = IngestionRunner(config=config, extractor_fn=mock_extractor)

    # Trigger cancellation after 0.05s
    async def cancel_task():
        await asyncio.sleep(0.05)
        cancel_token.cancel()

    t0 = time.monotonic()
    asyncio.create_task(cancel_task())
    result = await runner.run(cards, cancellation_token=cancel_token)
    elapsed = time.monotonic() - t0

    assert result.status == IngestionStatus.CANCELLED
    # Exited almost immediately (< 0.5s instead of waiting 5.0s)
    assert elapsed < 0.5


@pytest.mark.asyncio
async def test_runner_progress_callback_lifecycle(tmp_path: Path):
    """Verify progress callback receives monotonic updates."""
    scan_dir = tmp_path / "progress_test"
    scan_dir.mkdir()
    cards = [validate_and_load_card(create_test_image(scan_dir / f"card_{i:02d}.jpg")) for i in range(4)]

    progress_history: List[IngestionProgress] = []

    async def on_progress(p: IngestionProgress):
        progress_history.append(p.model_copy())

    async def mock_extractor(card: ScannedCard) -> VisualFingerprint:
        return make_mock_fingerprint(card)

    runner = IngestionRunner(extractor_fn=mock_extractor)
    result = await runner.run(cards, progress_callback=on_progress)

    assert result.status == IngestionStatus.COMPLETED
    assert len(progress_history) > 0
    final_p = progress_history[-1]
    assert final_p.processed_cards == 4
    assert final_p.percent_complete == 100.0


@pytest.mark.asyncio
async def test_runner_empty_card_list():
    """Verify running on empty card list returns immediately with 0 cards."""
    runner = IngestionRunner()
    result = await runner.run([])
    assert result.total_cards == 0
    assert len(result.fingerprints) == 0
    assert result.status == IngestionStatus.COMPLETED
