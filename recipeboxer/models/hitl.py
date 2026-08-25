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
    decision: HITLDecision = Field(
        default=HITLDecision.APPROVE,
        description="Human decision for this proposal",
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
