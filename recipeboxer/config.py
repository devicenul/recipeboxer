"""Global configuration management for RecipeBoxer."""

from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Global configuration settings for RecipeBoxer."""

    # --- Gemini Model Configuration ---
    default_model: str = Field(
        default="gemini-3.7-flash",
        description="Default Gemini model identifier used across all pipeline stages",
    )
    fingerprint_model: Optional[str] = Field(
        default=None,
        description="Per-stage model override for visual fingerprinting",
    )
    correlation_model: Optional[str] = Field(
        default=None,
        description="Per-stage model override for semantic continuity correlation",
    )
    extraction_model: Optional[str] = Field(
        default=None,
        description="Per-stage model override for structured recipe extraction",
    )
    thinking_mode: bool = Field(
        default=False,
        description="Whether to enable Gemini extended reasoning / thinking mode",
    )
    thinking_budget_tokens: Optional[int] = Field(
        default=None,
        ge=0,
        description="Maximum budget cap for thinking tokens when thinking_mode is active",
    )

    # --- Ingestion & Throttling Settings ---
    max_concurrency: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Maximum concurrent asynchronous tasks in parallel ingestion mode",
    )
    slow_batch_size: int = Field(
        default=2,
        ge=1,
        le=50,
        description="Number of cards per batch chunk in slow-batch ingestion mode",
    )
    slow_batch_interval_seconds: float = Field(
        default=2.0,
        ge=0.0,
        description="Delay in seconds between batch chunks in slow-batch ingestion mode",
    )
    supported_image_extensions: Tuple[str, ...] = (
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".tiff",
        ".tif",
    )

    # --- Storage, Cache & Export Paths ---
    cache_dir: Path = Field(
        default=Path(".cache/recipeboxer"),
        description="Directory for persistent SQLite cache and intermediate artifacts",
    )
    cache_db_filename: str = Field(
        default="fingerprints.db",
        description="Filename of the SQLite fingerprint database",
    )
    export_dir: Path = Field(
        default=Path("exports"),
        description="Directory where exported Plan To Eat CSV files are saved",
    )

    # --- API & Server Settings ---
    api_host: str = Field(
        default="127.0.0.1",
        description="FastAPI host address to bind",
    )
    api_port: int = Field(
        default=8000,
        ge=1024,
        le=65535,
        description="FastAPI port to bind",
    )
    debug: bool = Field(
        default=False,
        description="Enable debug logging and verbose tracebacks",
    )

    # --- Credentials & API Keys ---
    gemini_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("RECIPEBOXER_GEMINI_API_KEY", "GEMINI_API_KEY"),
        description="Google Gemini API Key",
    )

    # --- Correlation Algorithm Weights & Thresholds ---
    heuristic_screen_threshold: float = Field(
        default=0.50,
        ge=0.0,
        le=1.0,
        description="Stage 1 heuristic visual similarity cutoff threshold",
    )
    heuristic_weight: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description="Weight of Stage 1 heuristic score in composite confidence calculation",
    )
    semantic_weight: float = Field(
        default=0.70,
        ge=0.0,
        le=1.0,
        description="Weight of Stage 2 semantic score in composite confidence calculation",
    )

    # Pydantic Settings V2 Configuration
    model_config = SettingsConfigDict(
        env_prefix="RECIPEBOXER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Model Resolution Helpers ---
    def get_fingerprint_model(self) -> str:
        """Returns effective model identifier for visual fingerprinting."""
        return self.fingerprint_model or self.default_model

    def get_correlation_model(self) -> str:
        """Returns effective model identifier for semantic correlation."""
        return self.correlation_model or self.default_model

    def get_extraction_model(self) -> str:
        """Returns effective model identifier for structured recipe extraction."""
        return self.extraction_model or self.default_model

    @property
    def cache_db_path(self) -> Path:
        """Returns the full resolved path to the SQLite cache database."""
        return self.cache_dir / self.cache_db_filename

    def ensure_directories(self) -> None:
        """Creates necessary cache and export directories if they do not exist."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)


@lru_cache()
def get_settings() -> Settings:
    """Returns a cached singleton instance of application Settings."""
    return Settings()
