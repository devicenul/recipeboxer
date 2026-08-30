"""Human-in-the-Loop (HITL) review models for RecipeBoxer."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator


class HITLDecision(str, Enum):
    """Human reviewer decision for a proposed card sequence."""
    APPROVE = "APPROVE"          # Accept proposed sequence as-is
    REJECT = "REJECT"            # Reject sequence (do not extract as recipe)
    OVERRIDE = "OVERRIDE"        # Human manual sequence reordering or assignment
    STANDALONE = "STANDALONE"    # Designated as a single-sided standalone recipe


class CardSequenceApproval(BaseModel):
    """Individual human review decision on an ordered sequence proposal."""
    proposal_id: str = Field(
        ...,
        description="ID of the proposal being approved, rejected, or overridden",
    )
    ordered_card_ids: List[str] = Field(
        ...,
        min_length=1,
        description="Authoritative ordered list of card IDs for this recipe sequence",
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
        description="Legacy flag for front/back role reversal",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Optional human reviewer notes or corrections",
    )

    @property
    def front_card_id(self) -> str:
        return self.ordered_card_ids[0] if self.ordered_card_ids else ""

    @property
    def back_card_id(self) -> Optional[str]:
        return self.ordered_card_ids[1] if len(self.ordered_card_ids) > 1 else None

    @property
    def card_ids(self) -> List[str]:
        return self.ordered_card_ids

    @property
    def additional_card_ids(self) -> List[str]:
        return self.ordered_card_ids[2:] if len(self.ordered_card_ids) > 2 else []

    @model_validator(mode="before")
    @classmethod
    def _normalize_approval_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "ordered_card_ids" not in data or not data["ordered_card_ids"]:
                cards: List[str] = []
                if data.get("card_ids"):
                    cards = list(data["card_ids"])
                elif data.get("front_card_id"):
                    if data.get("reverse_roles") and data.get("back_card_id"):
                        cards = [str(data["back_card_id"]), str(data["front_card_id"])]
                    else:
                        cards = [str(data["front_card_id"])]
                        if data.get("back_card_id"):
                            cards.append(str(data["back_card_id"]))
                    if data.get("additional_card_ids"):
                        for cid in data["additional_card_ids"]:
                            if cid not in cards:
                                cards.append(str(cid))
                if cards:
                    data["ordered_card_ids"] = cards
        return data


# Backward-compatible clean-break alias
CardPairApproval = CardSequenceApproval


class ApprovalPayload(BaseModel):
    """Submitted by the user via /api/hitl/approve to resume pipeline execution."""
    session_id: str = Field(
        ...,
        description="ID of the pipeline session being reviewed",
    )
    approvals: List[CardSequenceApproval] = Field(
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
    """Ranked alternate candidate card for the candidate drawer."""
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
    card_id: str = Field(
        ...,
        description="Target card ID being extended or correlated",
    )
    front_card_id: Optional[str] = Field(
        default=None,
        description="Legacy alias for card_id",
    )
    candidates: List[CorrelationCandidate] = Field(
        default_factory=list,
        description="Ranked candidate cards sorted descending by confidence score",
    )
    total_candidates: int = Field(
        default=0,
        description="Total number of candidates evaluated",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_candidate_response(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "card_id" not in data and "front_card_id" in data:
                data["card_id"] = data["front_card_id"]
            if "front_card_id" not in data and "card_id" in data:
                data["front_card_id"] = data["card_id"]
        return data

    def model_post_init(self, __context: Any) -> None:
        if not self.front_card_id and self.card_id:
            self.front_card_id = self.card_id

    @property
    def preceding_card_id(self) -> str:
        return self.card_id
