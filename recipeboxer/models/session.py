"""Pipeline session summary, detail, and ingestion configuration models."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field

from recipeboxer.models.pipeline import PipelineState, ProgressMetrics, TokenUsage
from recipeboxer.models.recipe import RecipeSchema


class SessionSummary(BaseModel):
    """Compact summary of a historical or active digitization session."""
    session_id: str = Field(
        ...,
        description="Unique identifier for the session",
    )
    folder_path: Optional[str] = Field(
        default=None,
        description="Source directory of scanned card images",
    )
    status: Union[PipelineState, str] = Field(
        default=PipelineState.IDLE,
        description="Current state of the pipeline session",
    )
    created_at: str = Field(
        ...,
        description="ISO 8601 timestamp of session creation",
    )
    updated_at: Optional[str] = Field(
        default=None,
        description="ISO 8601 timestamp of last session state update",
    )
    card_count: int = Field(
        default=0,
        description="Total number of scanned cards processed in this session",
    )
    recipe_count: int = Field(
        default=0,
        description="Total number of extracted recipes produced in this session",
    )
    mode: Optional[str] = Field(
        default="parallel",
        description="Ingestion mode (parallel or slow-batch)",
    )
    error: Optional[str] = Field(
        default=None,
        description="Last error message if session failed",
    )


class SessionDetail(BaseModel):
    """Detailed view of a pipeline session including metrics, cards, proposals, and recipes."""
    session_id: str = Field(
        ...,
        description="Unique identifier for the session",
    )
    status: Union[PipelineState, str] = Field(
        default=PipelineState.IDLE,
        description="Current state of the pipeline session",
    )
    mode: Optional[str] = Field(
        default="parallel",
        description="Ingestion mode (parallel or slow-batch)",
    )
    folder_path: Optional[str] = Field(
        default=None,
        description="Source directory of scanned card images",
    )
    config: Dict[str, Any] = Field(
        default_factory=dict,
        description="Pipeline launch configuration options",
    )
    progress: ProgressMetrics = Field(
        default_factory=ProgressMetrics,
        description="Granular progress metrics across all stages",
    )
    token_usage: TokenUsage = Field(
        default_factory=TokenUsage,
        description="Cumulative Gemini API token telemetry",
    )
    elapsed_seconds: float = Field(
        default=0.0,
        description="Total execution time in seconds",
    )
    card_count: int = Field(
        default=0,
        description="Total number of scanned cards in this session",
    )
    recipe_count: int = Field(
        default=0,
        description="Total number of extracted recipes in this session",
    )
    cards: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="List of scanned card metadata dictionaries",
    )
    proposals: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="List of correlation proposals awaiting or approved",
    )
    recipes: List[RecipeSchema] = Field(
        default_factory=list,
        description="List of extracted structured recipes",
    )
    created_at: str = Field(
        default="",
        description="ISO 8601 timestamp of session creation",
    )
    updated_at: str = Field(
        default="",
        description="ISO 8601 timestamp of last update",
    )
    csv_output_path: Optional[str] = Field(
        default=None,
        description="Path to exported CSV file if available",
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message if session failed",
    )


class IngestionConfig(BaseModel):
    """Configuration options for initiating card ingestion."""
    folder_path: Optional[str] = Field(
        default=None,
        description="Local directory path containing recipe card scans",
    )
    default_tags: List[str] = Field(
        default_factory=list,
        description="Initial batch default tags to apply to all recipes scanned in this session",
    )
    max_concurrency: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Maximum concurrent workers for parallel ingestion",
    )
    slow_batch_size: int = Field(
        default=2,
        ge=1,
        le=50,
        description="Number of cards per batch chunk in slow-batch mode",
    )
    slow_batch_interval_seconds: float = Field(
        default=2.0,
        ge=0.0,
        description="Delay in seconds between batch chunks in slow-batch mode",
    )
    model: str = Field(
        default="gemini-3.7-flash",
        description="Gemini model identifier to use across pipeline stages",
    )
    mode: str = Field(
        default="parallel",
        description="Ingestion mode ('parallel' or 'slow-batch')",
    )
