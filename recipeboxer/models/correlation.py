"""Correlation and candidate sequence models for RecipeBoxer."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator


class CorrelationProposal(BaseModel):
    """Represents a proposed sequence of correlated cards (N >= 1) or standalone card."""
    proposal_id: str = Field(
        ...,
        description="Unique correlation proposal ID (e.g. 'prop_001')",
    )
    ordered_card_ids: List[str] = Field(
        ...,
        min_length=1,
        description="Ordered list of card IDs in this recipe proposal (Part 1 .. Part N)",
    )
    part_count: int = Field(
        default=1,
        ge=1,
        description="Total number of cards in this proposed sequence",
    )
    transition_scores: List[float] = Field(
        default_factory=list,
        description="Pairwise directed transition confidence scores [P(C1->C2), P(C2->C3), ...]",
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
        description="Overall sequence confidence score",
    )
    is_standalone: bool = Field(
        default=False,
        description="True if card is proposed as a single-sided self-contained recipe (N=1)",
    )
    continuity_explanation: str = Field(
        default="",
        description="Explanation of narrative flow, physical continuity, and transition logic",
    )
    recommended_action: str = Field(
        default="SEQUENCE",
        description="Recommended action: SEQUENCE, PAIR, STANDALONE, UNCERTAIN, or REJECT",
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

    @model_validator(mode="before")
    @classmethod
    def _normalize_proposal_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Backward compatibility translation for legacy front_card_id / back_card_id dicts
            if "ordered_card_ids" not in data or not data["ordered_card_ids"]:
                cards: List[str] = []
                if data.get("front_card_id"):
                    cards.append(str(data["front_card_id"]))
                if data.get("back_card_id"):
                    cards.append(str(data["back_card_id"]))
                if data.get("card_ids"):
                    cards = list(data["card_ids"])
                if cards:
                    data["ordered_card_ids"] = cards

            if "ordered_card_ids" in data and isinstance(data["ordered_card_ids"], list):
                if "part_count" not in data or data["part_count"] is None:
                    data["part_count"] = len(data["ordered_card_ids"])
                if "is_standalone" not in data or data["is_standalone"] is None:
                    data["is_standalone"] = (len(data["ordered_card_ids"]) == 1)

        return data


class CorrelationResult(BaseModel):
    """Output of the correlation pipeline presented to the HITL review stage."""
    proposals: List[CorrelationProposal] = Field(
        default_factory=list,
        description="List of proposed card sequences and standalone cards",
    )
    unmatched_card_ids: List[str] = Field(
        default_factory=list,
        description="List of card IDs that could not be confidently correlated",
    )
