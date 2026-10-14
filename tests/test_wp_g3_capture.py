"""Tests for WP-G3 capture metadata extraction, natural sorting, and scanner integration."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from PIL import ExifTags, Image
import pytest

from recipeboxer.ingestion.capture import capture_gap_seconds, read_capture_meta
from recipeboxer.ingestion.scanner import natural_sort_key, scan_directory, validate_and_load_card
from recipeboxer.models.features import CaptureMeta


def _create_synthetic_jpeg_with_exif(
    path: Path,
    dto: str = "2024:03:15 10:20:30",
    subsec: str = "450",
    offset: str = "-04:00",
    make: str = "Sony",
    model: str = "A7IV",
) -> None:
    """Create a synthetic JPEG file containing specified EXIF tags."""
    img = Image.new("RGB", (64, 64), color=(240, 235, 220))
    exif = img.getexif()
    exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)

    if dto:
        exif_ifd[36867] = dto  # DateTimeOriginal
    if subsec:
        exif_ifd[37521] = subsec  # SubSecTimeOriginal
    if offset:
        exif_ifd[36881] = offset  # OffsetTimeOriginal
    if make:
        exif[271] = make  # Make
    if model:
        exif[272] = model  # Model

    img.save(path, format="JPEG", exif=exif)


def test_read_capture_meta_exif_full(tmp_path: Path) -> None:
    """Verify full extraction of DateTimeOriginal, SubSecTimeOriginal, OffsetTimeOriginal, and Make/Model."""
    img_path = tmp_path / "card_exif.jpg"
    _create_synthetic_jpeg_with_exif(
        img_path,
        dto="2024:05:10 14:30:15",
        subsec="500",
        offset="+02:00",
        make="Canon",
        model="EOS R5",
    )

    meta = read_capture_meta(img_path, natural_sort_index=3)

    assert isinstance(meta, CaptureMeta)
    assert meta.source == "exif"
    assert meta.natural_sort_index == 3
    assert meta.device == "Canon EOS R5"
    assert meta.subsec == 500

    assert meta.taken_at is not None
    assert meta.taken_at.year == 2024
    assert meta.taken_at.month == 5
    assert meta.taken_at.day == 10
    assert meta.taken_at.hour == 14
    assert meta.taken_at.minute == 30
    assert meta.taken_at.second == 15
    assert meta.taken_at.tzinfo is not None
    assert meta.taken_at.utcoffset() == timedelta(hours=2)


def test_read_capture_meta_exif_dedup_device_make_model(tmp_path: Path) -> None:
    """Verify Make/Model formatting does not duplicate make if model already contains it."""
    img_path = tmp_path / "card_dedup.jpg"
    _create_synthetic_jpeg_with_exif(
        img_path,
        make="Google",
        model="Google Pixel 8 Pro",
    )

    meta = read_capture_meta(img_path, natural_sort_index=0)
    assert meta.device == "Google Pixel 8 Pro"


def test_read_capture_meta_exif_partial_no_offset(tmp_path: Path) -> None:
    """Verify extraction when offset and subsec are omitted."""
    img_path = tmp_path / "card_no_offset.jpg"
    _create_synthetic_jpeg_with_exif(
        img_path,
        dto="2023:11:01 09:15:00",
        subsec="",
        offset="",
        make="",
        model="Hero10 Black",
    )

    meta = read_capture_meta(img_path, natural_sort_index=1)
    assert meta.source == "exif"
    assert meta.taken_at is not None
    assert meta.taken_at.year == 2023
    assert meta.taken_at.tzinfo is None  # Naive
    assert meta.subsec is None
    assert meta.device == "Hero10 Black"


def test_read_capture_meta_mtime_fallback(tmp_path: Path) -> None:
    """Verify fallback to file mtime when image has no EXIF timestamps."""
    img_path = tmp_path / "plain_image.png"
    img = Image.new("RGB", (32, 32), color=(200, 200, 200))
    img.save(img_path, format="PNG")

    meta = read_capture_meta(img_path, natural_sort_index=7)

    assert meta.source == "mtime"
    assert meta.natural_sort_index == 7
    assert meta.taken_at is not None
    assert meta.subsec is not None
    assert meta.device is None


def test_read_capture_meta_nonexistent_file(tmp_path: Path) -> None:
    """Verify source='none' when file is nonexistent."""
    missing_path = tmp_path / "nonexistent.jpg"
    meta = read_capture_meta(missing_path, natural_sort_index=42)

    assert meta.source == "none"
    assert meta.taken_at is None
    assert meta.subsec is None
    assert meta.device is None
    assert meta.natural_sort_index == 42


def test_capture_gap_seconds() -> None:
    """Verify capture gap calculation between timestamps, subseconds, and cards."""
    # 1. Sub-second resolution gap
    meta_a = CaptureMeta(
        taken_at=datetime(2024, 1, 1, 12, 0, 0),
        subsec=200,
        source="exif",
    )
    meta_b = CaptureMeta(
        taken_at=datetime(2024, 1, 1, 12, 0, 0),
        subsec=750,
        source="exif",
    )
    gap = capture_gap_seconds(meta_a, meta_b)
    assert gap == pytest.approx(0.55, rel=1e-4)

    # 2. Multi-second gap
    meta_c = CaptureMeta(
        taken_at=datetime(2024, 1, 1, 12, 0, 10),
        subsec=200,
        source="exif",
    )
    assert capture_gap_seconds(meta_a, meta_c) == pytest.approx(10.0, rel=1e-4)

    # 3. Signed vs unsigned
    assert capture_gap_seconds(meta_c, meta_a, signed=False) == pytest.approx(10.0, rel=1e-4)
    assert capture_gap_seconds(meta_c, meta_a, signed=True) == pytest.approx(-10.0, rel=1e-4)

    # 4. Mixed timezone awareness
    meta_naive = CaptureMeta(taken_at=datetime(2024, 1, 1, 12, 0, 0), source="exif")
    meta_aware = CaptureMeta(
        taken_at=datetime(2024, 1, 1, 12, 0, 5, tzinfo=timezone.utc),
        source="mtime",
    )
    assert capture_gap_seconds(meta_naive, meta_aware) == pytest.approx(5.0, rel=1e-4)

    # 5. Missing / None timestamps
    meta_empty = CaptureMeta(taken_at=None, source="none")
    assert capture_gap_seconds(meta_a, meta_empty) is None
    assert capture_gap_seconds(None, meta_b) is None
    assert capture_gap_seconds(None, None) is None


def test_natural_sort_ordering() -> None:
    """Verify natural sort ordering (IMG_2 < IMG_10) without semantic parsing."""
    filenames = ["IMG_10.jpg", "IMG_2.jpg", "IMG_1.jpg", "IMG_20.jpg"]
    sorted_filenames = sorted(filenames, key=natural_sort_key)
    assert sorted_filenames == ["IMG_1.jpg", "IMG_2.jpg", "IMG_10.jpg", "IMG_20.jpg"]

    paths = [Path("scan_10b.jpg"), Path("scan_2.jpg"), Path("scan_10a.jpg")]
    sorted_paths = sorted(paths, key=natural_sort_key)
    assert [p.name for p in sorted_paths] == ["scan_2.jpg", "scan_10a.jpg", "scan_10b.jpg"]


def test_misleading_names_do_not_affect_metadata(tmp_path: Path) -> None:
    """Verify that misleading names ('back_1.jpg', 'front_2.jpg') do not alter metadata except sort order."""
    common_dto = "2024:06:01 12:00:00"
    common_subsec = "100"
    common_offset = "-05:00"
    common_make = "Fujifilm"
    common_model = "X-T5"

    path_back = tmp_path / "back_1.jpg"
    path_front = tmp_path / "front_1.jpg"
    path_random = tmp_path / "unrelated_recipe_photo.jpg"

    for p in (path_back, path_front, path_random):
        _create_synthetic_jpeg_with_exif(
            p,
            dto=common_dto,
            subsec=common_subsec,
            offset=common_offset,
            make=common_make,
            model=common_model,
        )

    meta_back = read_capture_meta(path_back, natural_sort_index=0)
    meta_front = read_capture_meta(path_front, natural_sort_index=1)
    meta_random = read_capture_meta(path_random, natural_sort_index=2)

    # Core metadata properties MUST be identical regardless of filename
    assert meta_back.source == meta_front.source == meta_random.source == "exif"
    assert meta_back.device == meta_front.device == meta_random.device == "Fujifilm X-T5"
    assert meta_back.subsec == meta_front.subsec == meta_random.subsec == 100
    assert meta_back.taken_at == meta_front.taken_at == meta_random.taken_at

    # Only natural_sort_index differs
    assert meta_back.natural_sort_index == 0
    assert meta_front.natural_sort_index == 1
    assert meta_random.natural_sort_index == 2


def test_scanner_wiring_additive_fields(tmp_path: Path) -> None:
    """Verify scan_directory and ScannedCard carry capture_meta and natural_sort_index."""
    scan_dir = tmp_path / "scan_box"
    scan_dir.mkdir()

    p1 = scan_dir / "IMG_2.jpg"
    p2 = scan_dir / "IMG_10.jpg"

    _create_synthetic_jpeg_with_exif(p1, dto="2024:01:01 08:00:00", subsec="100")
    _create_synthetic_jpeg_with_exif(p2, dto="2024:01:01 08:00:05", subsec="200")

    result = scan_directory(scan_dir)

    assert len(result.cards) == 2
    card0, card1 = result.cards

    # Natural sorting verification
    assert card0.filename == "IMG_2.jpg"
    assert card0.natural_sort_index == 0
    assert card0.capture_meta is not None
    assert card0.capture_meta.source == "exif"
    assert card0.capture_meta.natural_sort_index == 0
    assert card0.capture is card0.capture_meta  # property alias

    assert card1.filename == "IMG_10.jpg"
    assert card1.natural_sort_index == 1
    assert card1.capture_meta is not None
    assert card1.capture_meta.source == "exif"
    assert card1.capture_meta.natural_sort_index == 1

    # Existing ScannedCard fields intact
    assert card0.card_id == "IMG_2.jpg"
    assert card0.format == "JPEG"
    assert card0.width == 64
    assert card0.height == 64
    assert card0.sha256_hash != ""

    # Gap calculation directly with ScannedCard instances
    gap = capture_gap_seconds(card0, card1)
    assert gap == pytest.approx(5.1, rel=1e-4)


def test_validate_and_load_card_direct(tmp_path: Path) -> None:
    """Verify validate_and_load_card populates capture_meta when called directly."""
    img_path = tmp_path / "direct_card.jpg"
    _create_synthetic_jpeg_with_exif(img_path, dto="2024:02:01 12:30:00")

    card = validate_and_load_card(img_path, natural_sort_index=4)
    assert card.natural_sort_index == 4
    assert card.capture_meta is not None
    assert card.capture_meta.source == "exif"
    assert card.capture_meta.natural_sort_index == 4
