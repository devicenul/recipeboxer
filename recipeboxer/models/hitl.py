"""Human-in-the-Loop (HITL) review models for RecipeBoxer."""

from __future__ import annotations

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class HITLDecision(str, Enum):
    """Human reviewer decision for a proposed card pairing."""
    APPROVE = "APPROVE"          # Accept proposed pairing as-is
    REJECT = "REJECT"            # Reject pairing (do not extract as recipe)
    OVERRIDE = "OVERRIDE"        # Human manual pairing / reassignment of back card
    STANDALONE = "STANDALONE"    # Designated as a single-sided standalone recipe


class CardPairApproval(BaseModel):
    """Individual human review decision on a correlation proposal."""
    proposal_id: str = Field(
        ...,
        description="ID of the proposal being approved, rejected, or overridden",
    )
    front_card_id: str = Field(
        ...,
        description="Card ID of front card",
    )
    back_card_id: Optional[str] = Field(
        default=None,
        description="Card ID of assigned back card, or None for standalone recipe",
    )
    card_ids: Optional[List[str]] = Field(
        default=None,
        description="Complete list of card IDs for multi-card grouping (e.g. 3+ card sets)",
    )
    additional_card_ids: Optional[List[str]] = Field(
        default=None,
        description="Optional additional back card IDs for multi-part recipe sets",
    )
    decision: HITLDecision = Field(
        default=HITLDecision.APPROVE,
        description="Human decision for this proposal",
    )
    is_standalone: Optional[bool] = Field(
        default=False,
        description="Flag designating card as a single-sided standalone recipe",
    )
    is_rejected: Optional[bool] = Field(
        default=False,
        description="Flag designating proposal or card as rejected/discarded",
    )
    reverse_roles: Optional[bool] = Field(
        default=False,
        description="Flag indicating front and back card roles should be reversed",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Optional human reviewer notes or corrections",
    )


class ApprovalPayload(BaseModel):
    """Submitted by the user via /api/hitl/approve to resume pipeline execution."""
    session_id: str = Field(
        ...,
        description="ID of the pipeline session being reviewed",
    )
    approvals: List[CardPairApproval] = Field(
        default_factory=list,
        description="List of approved, overridden, or standalone card configurations",
    )
    rejected_card_ids: List[str] = Field(
        default_factory=list,
        description="List of card IDs explicitly discarded from extraction",
    )
    continue_pipeline: bool = Field(
        default=True,
        description="Whether to immediately resume pipeline extraction stage upon approval",
    )


class CandidateScoreBreakdown(BaseModel):
    """Granular visual and semantic feature score breakdown for candidate ranking."""
    paper_color_score: float = Field(
        default=0.0,
        description="CIELAB substrate paper color similarity (0.0 - 1.0)",
    )
    paper_ruling_score: float = Field(
        default=0.0,
        description="Ruling line pattern and spacing similarity (0.0 - 1.0)",
    )
    ink_score: float = Field(
        default=0.0,
        description="Writing instrument type and ink color similarity (0.0 - 1.0)",
    )
    stain_score: float = Field(
        default=0.0,
        description="Horizontally mirrored physical stain bleed-through similarity (0.0 - 1.0)",
    )
    boundary_text_score: float = Field(
        default=0.0,
        description="Boundary text continuation and narrative cue score (0.0 - 1.0)",
    )
    composite_heuristic_score: float = Field(
        default=0.0,
        description="Composite Stage 1 heuristic similarity score (0.0 - 1.0)",
    )
    semantic_score: float = Field(
        default=0.0,
        description="Stage 2 Gemini semantic continuity score (0.0 - 1.0)",
    )
    overall_score: float = Field(
        default=0.0,
        description="Overall weighted composite confidence score (0.0 - 1.0)",
    )


class CorrelationCandidate(BaseModel):
    """Ranked alternate candidate back card for the candidate drawer."""
    candidate_card_id: str = Field(
        ...,
        description="Card ID of the candidate matching card",
    )
    candidate_image_path: Optional[str] = Field(
        default=None,
        description="Filesystem path or streaming URL for candidate card image",
    )
    score: float = Field(
        default=0.0,
        description="Overall confidence score for ranking (0.0 - 1.0)",
    )
    heuristic_score: float = Field(
        default=0.0,
        description="Stage 1 heuristic visual similarity score",
    )
    semantic_score: float = Field(
        default=0.0,
        description="Stage 2 Gemini semantic continuity score",
    )
    composite_score: float = Field(
        default=0.0,
        description="Composite weighted score",
    )
    reasoning_snippet: str = Field(
        default="",
        description="Concise human-readable explanation of visual and semantic continuity",
    )
    score_breakdown: Optional[CandidateScoreBreakdown] = Field(
        default=None,
        description="Detailed feature breakdown for candidate drawer inspector",
    )


class CandidateListResponse(BaseModel):
    """Response payload for GET /api/hitl/candidates."""
    session_id: str = Field(
        ...,
        description="Target pipeline session ID",
    )
    front_card_id: str = Field(
        ...,
        description="Target front card ID being correlated",
    )
    candidates: List[CorrelationCandidate] = Field(
        default_factory=list,
        description="Ranked candidate cards sorted descending by confidence score",
    )
    total_candidates: int = Field(
        default=0,
        description="Total number of candidates evaluated",
    )

