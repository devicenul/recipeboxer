"""Capture metadata extraction and temporal gap calculation for RecipeBoxer.

Implements WP-G3 capture metadata extraction from EXIF (DateTimeOriginal,
SubSecTimeOriginal, OffsetTimeOriginal, Make/Model) with fallback to file mtime,
and temporal proximity utilities.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Union
from PIL import ExifTags, Image

from recipeboxer.models.features import CaptureMeta


def _get_exif_tag(exif: Any, ifd: Any, *tag_ids: int) -> Any:
    """Retrieve the first matching EXIF tag value from IFD or top-level EXIF."""
    for tid in tag_ids:
        if ifd and hasattr(ifd, "get") and ifd.get(tid) is not None:
            return ifd.get(tid)
        if exif and hasattr(exif, "get") and exif.get(tid) is not None:
            return exif.get(tid)
    return None


def _parse_datetime(dt_str: str) -> Optional[datetime]:
    """Parse EXIF datetime string into a datetime instance."""
    if not dt_str:
        return None
    cleaned = str(dt_str).strip().replace("\x00", "")
    if not cleaned or cleaned.startswith("0000:00:00") or cleaned.startswith("0000-00-00"):
        return None

    for fmt in (
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y:%m:%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y:%m:%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
    ):
        try:
            return datetime.strptime(cleaned, fmt)
        except (ValueError, TypeError):
            continue

    try:
        return datetime.fromisoformat(cleaned.replace(" ", "T", 1))
    except (ValueError, TypeError):
        pass

    m = re.match(r"^(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})", cleaned)
    if m:
        try:
            return datetime(
                year=int(m.group(1)),
                month=int(m.group(2)),
                day=int(m.group(3)),
                hour=int(m.group(4)),
                minute=int(m.group(5)),
                second=int(m.group(6)),
            )
        except (ValueError, TypeError):
            pass

    return None


def _parse_offset(val: str) -> Optional[timezone]:
    """Parse timezone offset string (e.g. '+02:00', '-05:00', 'Z') into timezone."""
    if not val:
        return None
    cleaned = str(val).strip().replace("\x00", "")
    if not cleaned:
        return None
    if cleaned.upper() == "Z":
        return timezone.utc

    m = re.match(r"^([+-])(\d{1,2}):?(\d{2})$", cleaned)
    if m:
        sign = -1 if m.group(1) == "-" else 1
        hours = int(m.group(2))
        minutes = int(m.group(3))
        if hours < 24 and minutes < 60:
            return timezone(sign * timedelta(hours=hours, minutes=minutes))
    return None


def _parse_subsec(val: Any) -> Optional[int]:
    """Parse subsecond fraction representation into an integer."""
    if val is None:
        return None
    cleaned = str(val).strip().replace("\x00", "")
    digits = re.findall(r"\d+", cleaned)
    if not digits:
        return None
    try:
        return int("".join(digits))
    except ValueError:
        return None


def _extract_device(make: Optional[str], model: Optional[str]) -> Optional[str]:
    """Clean and combine camera Make and Model into a device descriptor string."""
    m_clean = str(make or "").strip().replace("\x00", "")
    mo_clean = str(model or "").strip().replace("\x00", "")
    if m_clean and mo_clean:
        if mo_clean.lower().startswith(m_clean.lower()):
            return mo_clean
        return f"{m_clean} {mo_clean}"
    if mo_clean:
        return mo_clean
    if m_clean:
        return m_clean
    return None


def _mtime_fallback(path: Path, device: Optional[str], natural_sort_index: int) -> CaptureMeta:
    """Fall back to filesystem mtime when EXIF timestamps are unavailable."""
    try:
        st = path.stat()
        mtime = st.st_mtime
        ns = getattr(st, "st_mtime_ns", int(mtime * 1e9))
        subsec_ms = (ns % 1_000_000_000) // 1_000_000
        taken_at = datetime.fromtimestamp(mtime, tz=timezone.utc)
        return CaptureMeta(
            taken_at=taken_at,
            subsec=subsec_ms,
            device=device,
            source="mtime",
            natural_sort_index=natural_sort_index,
        )
    except (OSError, ValueError):
        return CaptureMeta(
            taken_at=None,
            subsec=None,
            device=device,
            source="none",
            natural_sort_index=natural_sort_index,
        )


def read_capture_meta(path: Union[Path, str], natural_sort_index: int = 0) -> CaptureMeta:
    """Extract capture metadata (EXIF or mtime fallback) for a card scan file.

    Extracts:
        - DateTimeOriginal (+ SubSecTimeOriginal, OffsetTimeOriginal)
        - Device Make / Model
        - Source indicator ("exif", "mtime", or "none")
        - Natural sort index (order only, no semantic naming inferences)

    Args:
        path: Path to the image file on disk.
        natural_sort_index: Position in natural sort ordering.

    Returns:
        CaptureMeta model instance.
    """
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        return CaptureMeta(
            taken_at=None,
            subsec=None,
            device=None,
            source="none",
            natural_sort_index=natural_sort_index,
        )

    device: Optional[str] = None
    exif_taken_at: Optional[datetime] = None
    exif_subsec: Optional[int] = None

    try:
        with Image.open(file_path) as img:
            exif = img.getexif()
            if exif:
                exif_ifd = exif.get_ifd(ExifTags.IFD.Exif) if hasattr(exif, "get_ifd") else {}

                make = _get_exif_tag(exif, exif_ifd, 271)  # Make
                model = _get_exif_tag(exif, exif_ifd, 272)  # Model
                device = _extract_device(make, model)

                raw_dto = _get_exif_tag(exif, exif_ifd, 36867, 36868, 306)
                if raw_dto:
                    dt = _parse_datetime(str(raw_dto))
                    if dt:
                        raw_offset = _get_exif_tag(exif, exif_ifd, 36881, 36880, 36882)
                        if raw_offset and dt.tzinfo is None:
                            tz = _parse_offset(str(raw_offset))
                            if tz:
                                dt = dt.replace(tzinfo=tz)
                        exif_taken_at = dt

                raw_subsec = _get_exif_tag(exif, exif_ifd, 37521, 37520, 37522)
                if raw_subsec is not None:
                    exif_subsec = _parse_subsec(raw_subsec)
    except Exception:
        pass

    if exif_taken_at is not None:
        return CaptureMeta(
            taken_at=exif_taken_at,
            subsec=exif_subsec,
            device=device,
            source="exif",
            natural_sort_index=natural_sort_index,
        )

    return _mtime_fallback(file_path, device=device, natural_sort_index=natural_sort_index)


def _extract_time_seconds(val: Any) -> Optional[float]:
    """Extract normalized timestamp in seconds (including subseconds) from capture input."""
    if val is None:
        return None
    if isinstance(val, datetime):
        dt = val
        subsec = getattr(dt, "microsecond", 0) // 1000
    elif hasattr(val, "capture_meta") and getattr(val, "capture_meta") is not None:
        meta = val.capture_meta
        dt = meta.taken_at
        subsec = meta.subsec
    elif hasattr(val, "capture") and getattr(val, "capture") is not None:
        meta = val.capture
        dt = meta.taken_at
        subsec = meta.subsec
    elif isinstance(val, CaptureMeta):
        dt = val.taken_at
        subsec = val.subsec
    else:
        return None

    if dt is None:
        return None

    if dt.tzinfo is not None:
        ts = dt.timestamp()
    else:
        ts = dt.replace(tzinfo=timezone.utc).timestamp()

    if dt.microsecond == 0 and subsec is not None and subsec > 0:
        if subsec < 1000:
            ts += subsec / 1000.0
        elif subsec < 1_000_000:
            ts += subsec / 1_000_000.0
        else:
            ts += subsec / 1_000_000_000.0

    return ts


def capture_gap_seconds(
    a: Union[CaptureMeta, Any, datetime, None],
    b: Union[CaptureMeta, Any, datetime, None],
    signed: bool = False,
) -> Optional[float]:
    """Calculate the gap in seconds between two captures.

    Args:
        a: First capture (CaptureMeta, ScannedCard, or datetime).
        b: Second capture (CaptureMeta, ScannedCard, or datetime).
        signed: If True, return directed (b - a). If False (default), return abs(b - a).

    Returns:
        Gap in seconds as float, or None if either timestamp is unavailable.
    """
    ts_a = _extract_time_seconds(a)
    ts_b = _extract_time_seconds(b)
    if ts_a is None or ts_b is None:
        return None
    diff = ts_b - ts_a
    return round(diff if signed else abs(diff), 6)
