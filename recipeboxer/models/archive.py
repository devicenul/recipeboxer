"""Data models for RecipeBoxer portable project archive (.recipeboxer) manifest and assets."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field

from recipeboxer.models.domain import CardQuadrant, VisualFingerprint
from recipeboxer.models.recipe import RecipeSchema


class ArchiveAssetRecord(BaseModel):
    """Metadata record for an image asset stored inside the archive."""
    sha256_hash: str = Field(..., description="SHA-256 hex digest of image bytes")
    filename: str = Field(..., description="Original scan filename")
    archive_path: str = Field(..., description="Relative path within zip (e.g. assets/scans/<sha256>.jpg)")
    mime_type: str = Field(default="image/jpeg", description="MIME type")
    size_bytes: int = Field(default=0, ge=0, description="File size in bytes")
    width: Optional[int] = Field(default=None, description="Image pixel width")
    height: Optional[int] = Field(default=None, description="Image pixel height")


class ArchiveCardRecord(BaseModel):
    """Card scan record linking asset digest to visual fingerprint."""
    card_id: str = Field(..., description="Card scan ID")
    sha256_hash: str = Field(..., description="SHA-256 digest referencing ArchiveAssetRecord")
    filename: str = Field(..., description="Original file name")
    role: str = Field(default="STANDALONE", description="Classified CardRole string")
    role_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    fingerprint: Optional[VisualFingerprint] = Field(default=None, description="Full visual fingerprint")


class ArchiveSequenceProposal(BaseModel):
    """Persisted correlation proposal and HITL review decision."""
    proposal_id: str = Field(..., description="Unique proposal identifier")
    ordered_card_ids: List[str] = Field(..., min_length=1, description="List of card IDs in sequence order")
    part_count: int = Field(default=1, description="Number of cards in sequence")
    transition_scores: List[float] = Field(default_factory=list, description="Step transition confidence scores")
    heuristic_score: float = Field(default=0.0, description="Physical and visual heuristic similarity score")
    semantic_score: float = Field(default=0.0, description="Semantic continuation score")
    confidence: float = Field(default=0.0, description="Combined correlation confidence score")
    is_standalone: bool = Field(default=False, description="Whether this proposal represents a standalone card")
    continuity_explanation: str = Field(default="", description="Explanation of continuity rationale")
    recommended_action: str = Field(default="SEQUENCE", description="Action: 'SEQUENCE', 'MERGE', or 'KEEP_SEPARATE'")
    decision: Optional[str] = Field(default="APPROVE", description="HITL review decision: 'APPROVE', 'REJECT', 'OVERRIDE'")
    notes: Optional[str] = Field(default=None, description="Reviewer notes or feedback")


class StepImageBinding(BaseModel):
    """Explicit mapping from a recipe direction step to a card scan and optional bounding box."""
    step_index: int = Field(..., ge=1, description="1-indexed direction step number")
    card_id: str = Field(..., description="Card scan ID")
    card_sha256: str = Field(..., description="SHA-256 digest of the card scan")
    bounding_box: Optional[List[float]] = Field(
        default=None,
        description="Optional normalized coordinates [ymin, xmin, ymax, xmax] on the card",
    )
    marginalia_note: Optional[str] = Field(
        default=None,
        description="Optional handwritten marginalia note specific to this step",
    )


class MarginaliaEntry(BaseModel):
    """Granular handwritten or printed marginalia entry extracted from a card scan."""
    id: str = Field(default_factory=lambda: f"marg_{uuid.uuid4().hex[:8]}", description="Unique marginalia identifier")
    card_id: str = Field(..., description="Source card scan ID")
    card_sha256: str = Field(..., description="SHA-256 digest of the card scan")
    text: str = Field(..., description="Transcribed marginalia text")
    location_quadrant: Optional[Union[CardQuadrant, str]] = Field(default="CENTER")
    category: str = Field(
        default="family_story",
        description="Category: 'family_story', 'culinary_tweak', 'attribution', 'stain_note', 'other'",
    )
    target_field: str = Field(
        default="source_notes",
        description="Mapped recipe schema field ('description', 'source_notes', 'directions[i]')",
    )


class ArchiveRecipeRecord(RecipeSchema):
    """Extended recipe record in archive supporting step bindings and marginalia collections."""
    step_bindings: List[StepImageBinding] = Field(default_factory=list)
    marginalia: List[MarginaliaEntry] = Field(default_factory=list)


class RecipeBoxerArchiveManifest(BaseModel):
    """Top-level manifest.json schema for .recipeboxer project archives."""
    format_version: str = Field(default="1.0.0", description="Archive container format version")
    app_version: str = Field(default="1.0.0", description="RecipeBoxer application version")
    schema_version: str = Field(default="1.0.0", description="Manifest JSON schema version")
    project_id: str = Field(default_factory=lambda: f"proj_{uuid.uuid4().hex[:8]}", description="Unique project UUID")
    project_name: str = Field(default="RecipeBoxer Project", description="Human-readable project name")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    generator: str = Field(default="RecipeBoxer", description="Generator identifier string")
    session_metadata: Dict[str, Any] = Field(default_factory=dict, description="Session configuration and telemetry metadata")
    assets: List[ArchiveAssetRecord] = Field(default_factory=list, description="Content-addressed image assets stored in archive")
    cards: List[ArchiveCardRecord] = Field(default_factory=list, description="Card metadata and visual fingerprints")
    proposals: List[ArchiveSequenceProposal] = Field(default_factory=list, description="Correlation proposals and HITL decisions")
    recipes: List[ArchiveRecipeRecord] = Field(default_factory=list, description="Extracted recipe schemas with step bindings")
