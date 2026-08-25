"""Correlation and candidate pairing models for RecipeBoxer."""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


class CorrelationProposal(BaseModel):
    """Represents a proposed front/back card correlation or standalone card."""
    proposal_id: str = Field(
        ...,
        description="Unique correlation proposal ID (e.g. 'prop_001')",
    )
    front_card_id: str = Field(
        ...,
        description="Card ID for the front card",
    )
    back_card_id: Optional[str] = Field(
        default=None,
        description="Card ID for the back card (None if proposed as STANDALONE)",
    )
    heuristic_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Stage 1 heuristic visual feature similarity score (0.0 to 1.0)",
    )
    semantic_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Stage 2 Gemini semantic continuity score (0.0 to 1.0)",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Weighted composite confidence score (0.30 * heuristic + 0.70 * semantic)",
    )
    is_standalone: bool = Field(
        default=False,
        description="True if card is proposed as a single-sided self-contained recipe",
    )
    continuity_explanation: str = Field(
        default="",
        description="Explanation of narrative flow and physical continuity",
    )
    recommended_action: str = Field(
        default="PAIR",
        description="Recommended action: PAIR, STANDALONE, UNCERTAIN, or REJECT",
    )


class CorrelationResult(BaseModel):
    """Output of the correlation pipeline presented to the HITL review stage."""
    proposals: List[CorrelationProposal] = Field(
        default_factory=list,
        description="List of proposed pairings and standalone cards",
    )
    unmatched_card_ids: List[str] = Field(
        default_factory=list,
        description="List of card IDs that could not be confidently correlated",
    )
