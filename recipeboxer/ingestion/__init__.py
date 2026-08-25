"""Ingestion engine for RecipeBoxer."""

from recipeboxer.ingestion.runner import (
    CardTaskResult,
    ExtractorCallable,
    IngestionCancellationToken,
    IngestionConfig,
    IngestionMode,
    IngestionProgress,
    IngestionResult,
    IngestionRunner,
    IngestionStatus,
    ProgressCallback,
)
from recipeboxer.ingestion.scanner import (
    VALID_IMAGE_EXTENSIONS,
    CorruptImageError,
    ScanResult,
    ScannedCard,
    compute_file_sha256,
    get_image_mime_type,
    natural_sort_key,
    scan_directory,
    validate_and_load_card,
)

__all__ = [
    # Scanner
    "VALID_IMAGE_EXTENSIONS",
    "get_image_mime_type",
    "CorruptImageError",
    "ScannedCard",
    "ScanResult",
    "natural_sort_key",
    "compute_file_sha256",
    "validate_and_load_card",
    "scan_directory",
    # Runner
    "IngestionMode",
    "IngestionStatus",
    "IngestionConfig",
    "CardTaskResult",
    "IngestionProgress",
    "IngestionResult",
    "IngestionCancellationToken",
    "ExtractorCallable",
    "ProgressCallback",
    "IngestionRunner",
]
