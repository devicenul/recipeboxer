"""Filesystem scanning, natural sorting, and image validation for RecipeBoxer."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set, Tuple, Union
from PIL import Image

from recipeboxer.ingestion.capture import capture_gap_seconds, read_capture_meta
from recipeboxer.models.features import CaptureMeta

VALID_IMAGE_EXTENSIONS: Set[str] = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".tiff",
    ".tif",
    ".bmp",
    ".heic",
    ".heif",
    ".gif",
}


def get_image_mime_type(suffix_or_path: Union[str, Path]) -> str:
    """Return standard MIME type for given image extension, filename, or path."""
    if isinstance(suffix_or_path, Path):
        ext = suffix_or_path.suffix.lower()
    else:
        s = str(suffix_or_path).lower().strip()
        if s.startswith("."):
            ext = s
        elif "." in s:
            ext = Path(s).suffix.lower()
        else:
            ext = f".{s}"

    mapping = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
        ".bmp": "image/bmp",
        ".heic": "image/heic",
        ".heif": "image/heif",
        ".gif": "image/gif",
    }
    return mapping.get(ext, "image/jpeg")


class CorruptImageError(Exception):
    """Raised when an image file is corrupted or cannot be decoded by PIL."""
    pass


@dataclass(frozen=True)
class ScannedCard:
    """Represents a validated scanned recipe card on disk."""
    card_id: str
    file_path: Path
    filename: str = ""
    sha256_hash: str = ""
    file_size_bytes: int = 0
    width: int = 0
    height: int = 0
    format: str = "JPEG"
    mode: str = "RGB"
    capture_meta: Optional[CaptureMeta] = None
    natural_sort_index: int = 0

    def __post_init__(self) -> None:
        if not self.filename and self.file_path:
            object.__setattr__(self, "filename", self.file_path.name)

    @property
    def capture(self) -> Optional[CaptureMeta]:
        """Convenience property alias for capture_meta."""
        return self.capture_meta


@dataclass
class ScanResult:
    """Aggregated result from scanning a directory or file list."""
    cards: List[ScannedCard] = field(default_factory=list)
    total_files_found: int = 0
    valid_cards_count: int = 0
    skipped_files: List[Tuple[str, str]] = field(default_factory=list)  # (path, reason)
    corrupt_files: List[Tuple[str, str]] = field(default_factory=list)  # (path, error)


def natural_sort_key(s: Union[str, Path]) -> list:
    """Key for natural alphanumeric human sorting.

    Example: ['scan1.jpg', 'scan10.jpg', 'scan2.jpg'] -> ['scan1.jpg', 'scan2.jpg', 'scan10.jpg']
    """
    path_str = s.name if isinstance(s, Path) else str(s)
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", path_str)]


def compute_file_sha256(file_path: Path, chunk_size: int = 65536) -> str:
    """Compute SHA-256 hash of a file using streaming chunks to conserve memory."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest().lower()


def validate_and_load_card(
    file_path: Path,
    natural_sort_index: int = 0,
    capture_meta: Optional[CaptureMeta] = None,
) -> ScannedCard:
    """Validate an image file with PIL, extract metadata, and compute SHA-256 hash.

    Raises:
        FileNotFoundError: If file does not exist.
        ValueError: If file is 0 bytes or not an image.
        CorruptImageError: If PIL fails to decode/verify image.
    """
    if not file_path.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")

    file_size = file_path.stat().st_size
    if file_size == 0:
        raise ValueError(f"Empty (0-byte) image file: {file_path}")

    # 1. Compute SHA-256 hash
    sha256_hash = compute_file_sha256(file_path)

    # 2. PIL Image verification & metadata extraction
    try:
        with Image.open(file_path) as img:
            img.verify()

        # After verify(), file pointer is closed; re-open to inspect dimensions & format
        with Image.open(file_path) as img:
            width, height = img.size
            img_format = (img.format or file_path.suffix.lstrip(".").upper())
            img_mode = img.mode

            if width <= 0 or height <= 0:
                raise ValueError(f"Invalid image dimensions ({width}x{height}) for {file_path}")

    except Exception as exc:
        raise CorruptImageError(f"Failed to decode image {file_path}: {exc}") from exc

    card_id = file_path.name

    if capture_meta is None:
        capture_meta = read_capture_meta(file_path, natural_sort_index=natural_sort_index)

    return ScannedCard(
        card_id=card_id,
        file_path=file_path.resolve(),
        filename=file_path.name,
        sha256_hash=sha256_hash,
        file_size_bytes=file_size,
        width=width,
        height=height,
        format=img_format,
        mode=img_mode,
        capture_meta=capture_meta,
        natural_sort_index=natural_sort_index,
    )


def scan_directory(
    directory: Union[str, Path],
    recursive: bool = False,
    validate_images: bool = True,
    raise_on_error: bool = False,
) -> ScanResult:
    """Scan a directory for recipe card image files.

    Args:
        directory: Path to directory containing images.
        recursive: If True, search subdirectories recursively.
        validate_images: If True, verify image integrity with PIL.
        raise_on_error: If True, raise exception on corrupt image rather than recording in result.

    Returns:
        ScanResult containing naturally sorted ScannedCard items and diagnostic logs.
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        raise FileNotFoundError(f"Scan directory does not exist: {dir_path}")
    if not dir_path.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {dir_path}")

    pattern = "**/*" if recursive else "*"
    all_entries = [p for p in dir_path.glob(pattern) if p.is_file()]

    result = ScanResult()
    candidate_paths: List[Path] = []

    for entry in all_entries:
        result.total_files_found += 1

        # Skip hidden files or AppleDouble files (e.g. .DS_Store, ._scan.jpg)
        if entry.name.startswith("."):
            result.skipped_files.append((str(entry), "Hidden file"))
            continue

        suffix = entry.suffix.lower()
        if suffix not in VALID_IMAGE_EXTENSIONS:
            result.skipped_files.append((str(entry), f"Unsupported extension: {entry.suffix}"))
            continue

        candidate_paths.append(entry)

    # Sort naturally (scan1, scan2, ..., scan10)
    candidate_paths.sort(key=natural_sort_key)

    # Validate and build ScannedCard objects
    for idx, file_path in enumerate(candidate_paths):
        try:
            if validate_images:
                card = validate_and_load_card(file_path, natural_sort_index=idx)
            else:
                meta = read_capture_meta(file_path, natural_sort_index=idx)
                card = ScannedCard(
                    card_id=file_path.name,
                    file_path=file_path.resolve(),
                    filename=file_path.name,
                    sha256_hash=compute_file_sha256(file_path),
                    file_size_bytes=file_path.stat().st_size,
                    width=0,
                    height=0,
                    format=file_path.suffix.lstrip(".").upper(),
                    mode="UNKNOWN",
                    capture_meta=meta,
                    natural_sort_index=idx,
                )
            result.cards.append(card)
        except Exception as exc:
            if raise_on_error:
                raise
            result.corrupt_files.append((str(file_path), str(exc)))

    result.valid_cards_count = len(result.cards)
    return result



def scan_card_directory(
    directory: Union[str, Path],
    supported_extensions: Optional[Set[str]] = None,
    recursive: bool = False,
    validate_images: bool = True,
) -> List[ScannedCard]:
    """Scan a directory and return a list of valid ScannedCard instances."""
    res = scan_directory(directory=directory, recursive=recursive, validate_images=validate_images)
    return res.cards
