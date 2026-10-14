"""Global configuration management for RecipeBoxer."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Quality profiles mapping table (Section 7.1).
# NOTE: model IDs are provisional. They must be validated against the live
# models list (GenAIClient.list_available_models) during WP-B0/WP-B2 before
# being relied on; "balanced" must match the project's default model.
_DEFAULT_FLASH = "gemini-3.7-flash"
_DEFAULT_PRO = "gemini-3.7-pro"

QUALITY_PROFILES: Dict[str, Dict[str, str]] = {
    "economy": {
        "FINGERPRINT": _DEFAULT_FLASH,
        "SET_VERIFY": _DEFAULT_FLASH,
        "TRANSCRIBE": _DEFAULT_FLASH,
        "STRUCTURE": _DEFAULT_FLASH,
        "GROUNDING": _DEFAULT_FLASH,
        "REPAIR": _DEFAULT_FLASH,
        "JUDGE": _DEFAULT_FLASH,
        "MODALITY": _DEFAULT_FLASH,
    },
    "balanced": {
        "FINGERPRINT": _DEFAULT_FLASH,
        "SET_VERIFY": _DEFAULT_FLASH,
        "TRANSCRIBE": _DEFAULT_FLASH,
        "STRUCTURE": _DEFAULT_FLASH,
        "GROUNDING": _DEFAULT_FLASH,
        "REPAIR": _DEFAULT_FLASH,
        "JUDGE": _DEFAULT_PRO,
        "MODALITY": _DEFAULT_FLASH,
    },
    "best": {
        "FINGERPRINT": _DEFAULT_FLASH,
        "SET_VERIFY": _DEFAULT_PRO,
        "TRANSCRIBE": _DEFAULT_PRO,
        "STRUCTURE": _DEFAULT_PRO,
        "GROUNDING": _DEFAULT_PRO,
        "REPAIR": _DEFAULT_PRO,
        "JUDGE": _DEFAULT_PRO,
        "MODALITY": _DEFAULT_FLASH,
    },
}

DEFAULT_STAGE_TIMEOUTS: Dict[str, float] = {
    "FINGERPRINT": 30.0,
    "SET_VERIFY": 45.0,
    "TRANSCRIBE": 60.0,
    "STRUCTURE": 45.0,
    "GROUNDING": 30.0,
    "REPAIR": 30.0,
    "JUDGE": 30.0,
    "MODALITY": 20.0,
}


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
    set_verify_model: Optional[str] = Field(
        default=None,
        description="Per-stage model override for set-level verification",
    )
    transcribe_model: Optional[str] = Field(
        default=None,
        description="Per-stage model override for verbatim card transcription",
    )
    structure_model: Optional[str] = Field(
        default=None,
        description="Per-stage model override for recipe structuring",
    )
    judge_model: Optional[str] = Field(
        default=None,
        description="Per-stage model override for LLM evaluation judge",
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
    adaptive_thinking: bool = Field(
        default=False,
        description="Whether to dynamically allocate thinking budget tokens based on card modality",
    )

    # --- Unified LLM Client Settings (WP-X1) ---
    llm_max_retries: int = Field(
        default=4,
        ge=0,
        le=10,
        description="Maximum retry attempts on transient LLM failures (429/5xx/timeouts)",
    )
    llm_timeout_s: float = Field(
        default=60.0,
        ge=1.0,
        description="Default timeout in seconds for LLM operations",
    )
    stage_timeouts: dict[str, float] = Field(
        default_factory=lambda: dict(DEFAULT_STAGE_TIMEOUTS),
        description="Per-stage timeout limits in seconds",
    )
    llm_max_concurrency: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Maximum concurrent LLM generation calls",
    )
    llm_rpm_limit: int = Field(
        default=60,
        ge=1,
        description="Maximum LLM requests per minute (token bucket limiter)",
    )
    quality_profile: str = Field(
        default="balanced",
        description="Active quality profile ('economy', 'balanced', 'best')",
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
    def get_stage_model(self, stage: Any) -> str:
        """Returns the effective model identifier for a given pipeline/LLM stage."""
        stage_str = stage.value if hasattr(stage, "value") else str(stage)
        stage_upper = stage_str.upper()

        # Check explicit per-stage override fields
        if stage_upper == "FINGERPRINT" and self.fingerprint_model:
            return self.fingerprint_model
        if stage_upper in ("SET_VERIFY", "CORRELATION"):
            if self.set_verify_model:
                return self.set_verify_model
            if self.correlation_model:
                return self.correlation_model
        if stage_upper in ("TRANSCRIBE",):
            if self.transcribe_model:
                return self.transcribe_model
            if self.extraction_model:
                return self.extraction_model
        if stage_upper in ("STRUCTURE", "EXTRACTION"):
            if self.structure_model:
                return self.structure_model
            if self.extraction_model:
                return self.extraction_model
        if stage_upper == "JUDGE" and self.judge_model:
            return self.judge_model

        # Fall back to quality profile table
        profile_map = QUALITY_PROFILES.get(self.quality_profile.lower(), QUALITY_PROFILES["balanced"])
        if stage_upper in profile_map:
            return profile_map[stage_upper]

        return self.default_model

    def get_stage_timeout(self, stage: Any) -> float:
        """Returns the effective timeout in seconds for a given pipeline/LLM stage."""
        stage_str = stage.value if hasattr(stage, "value") else str(stage)
        stage_upper = stage_str.upper()
        if self.stage_timeouts and stage_upper in self.stage_timeouts:
            return float(self.stage_timeouts[stage_upper])
        return float(self.llm_timeout_s)

    def get_fingerprint_model(self) -> str:
        """Returns effective model identifier for visual fingerprinting."""
        return self.fingerprint_model or self.get_stage_model("FINGERPRINT")

    def get_correlation_model(self) -> str:
        """Returns effective model identifier for semantic correlation."""
        return self.correlation_model or self.set_verify_model or self.get_stage_model("SET_VERIFY")

    def get_extraction_model(self) -> str:
        """Returns effective model identifier for structured recipe extraction."""
        return self.extraction_model or self.structure_model or self.get_stage_model("STRUCTURE")

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
