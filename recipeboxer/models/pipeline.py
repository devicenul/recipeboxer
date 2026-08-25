"""Pipeline lifecycle, status, and API communication models for RecipeBoxer."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class PipelineState(str, Enum):
    """Execution states across the RecipeBoxer pipeline lifecycle."""
    IDLE = "IDLE"
    INGESTING = "INGESTING"
    SCANNING = "SCANNING"  # Backward-compatible alias for INGESTING
    FINGERPRINTING = "FINGERPRINTING"
    CORRELATING = "CORRELATING"
    HITL_PENDING = "HITL_PENDING"
    EXTRACTING = "EXTRACTING"
    EXPORTING = "EXPORTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class IngestionMode(str, Enum):
    """Ingestion mode for rate and concurrency management."""
    PARALLEL = "parallel"
    SLOW_BATCH = "slow_batch"


class ProgressMetrics(BaseModel):
    """Granular counter metrics for pipeline stages."""
    total_scans: int = Field(default=0, ge=0)
    fingerprinted: int = Field(default=0, ge=0)
    proposals: int = Field(default=0, ge=0)
    approved: int = Field(default=0, ge=0)
    extracted: int = Field(default=0, ge=0)
    percent_complete: float = Field(default=0.0, ge=0.0, le=100.0)


class TokenUsage(BaseModel):
    """Gemini API token usage tracking."""
    prompt_tokens: int = Field(default=0, ge=0)
    candidates_tokens: int = Field(default=0, ge=0)
    thoughts_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class PipelineStatus(BaseModel):
    """Overall status and telemetry returned by GET /api/status."""
    session_id: str = Field(..., description="Active session ID")
    status: PipelineState = Field(..., description="Current pipeline execution state")
    mode: IngestionMode = Field(default=IngestionMode.PARALLEL, description="Active ingestion mode")
    progress: ProgressMetrics = Field(default_factory=ProgressMetrics)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    elapsed_seconds: float = Field(default=0.0, ge=0.0)
    csv_output_path: Optional[str] = Field(default=None, description="Path to generated CSV file when COMPLETED")
    error: Optional[str] = Field(default=None, description="Error message if state is FAILED")


class PipelineStartRequest(BaseModel):
    """Request schema for POST /api/pipeline/start."""
    scans_dir: Optional[str] = Field(default=None, description="Filesystem directory containing recipe card scans")
    directory_path: Optional[str] = Field(default=None, description="Alias for scans_dir")
    mode: IngestionMode = Field(default=IngestionMode.PARALLEL, description="Ingestion mode: parallel or slow_batch")
    max_concurrency: Optional[int] = Field(default=None, ge=1, le=50, description="Override for max concurrent tasks")
    slow_batch_size: Optional[int] = Field(default=None, ge=1, le=20, description="Override for slow batch chunk size")
    slow_batch_interval_seconds: Optional[float] = Field(default=None, ge=0.0, description="Override for interval between batches")
    fingerprint_model: Optional[str] = Field(default=None)
    correlation_model: Optional[str] = Field(default=None)
    extraction_model: Optional[str] = Field(default=None)
    thinking_mode: Optional[bool] = Field(default=None)
    thinking_budget_tokens: Optional[int] = Field(default=None)

    def model_post_init(self, __context: Any) -> None:
        if not self.scans_dir and self.directory_path:
            self.scans_dir = self.directory_path
        elif not self.scans_dir and not self.directory_path:
            raise ValueError("scans_dir or directory_path must be provided")
        if not self.directory_path and self.scans_dir:
            self.directory_path = self.scans_dir


class PipelineStartResponse(BaseModel):
    """Response schema for POST /api/pipeline/start."""
    session_id: str
    status: PipelineState
    total_scans_found: int
    mode: IngestionMode
    message: str
