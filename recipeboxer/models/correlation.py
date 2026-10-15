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
    semantic_score: Optional[float] = Field(
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
    semantic_verified: bool = Field(
        default=True,
        description="True if verified by multimodal LLM, False if offline or degraded",
    )
    degraded_reason: Optional[str] = Field(
        default=None,
        description="Reason for degraded or unverified correlation (e.g. 'NO_MODEL', 'API_ERROR: ...')",
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


class FeatureEvidence(BaseModel):
    """Raw and collection-relative likelihood ratio evidence for a single feature between two cards."""
    name: str                 # e.g. "paper_lab", "ruling", "ink_hue", "capture_gap", "sort_adjacency",
                              # "show_through", "mirrored_wear", "outline", "header_score",
                              # "content_xref", "sentence_continuity", "step_continuity", "title_position",
                              # "embedding_sim", "ocr_cue"
    raw: Optional[float] = None         # raw similarity / measurement (None = not computable)
    log_lr: Optional[float] = None      # log likelihood ratio vs this deck's background (G10); None if not computable
    weight: float = 1.0
    note: Optional[str] = None


class PairEvidence(BaseModel):
    """Directed pairwise correlation evidence between card A and card B (hypothesis: A precedes B)."""
    a: str                    # ordered: a precedes b (directed successor hypothesis)
    b: str
    features: List[FeatureEvidence] = Field(default_factory=list)
    total_log_lr: float = 0.0 # sum(weight*log_lr) over computable features
    p_link: Optional[float] = None      # calibrated probability (G8) or None before calibration
    content_evidence_used: bool = False


class SetVerification(BaseModel):
    """Output of multimodal LLM set-level verification for an anchor card and candidate pool (G6)."""
    anchor: str
    continuations: List[str] = Field(default_factory=list)   # ordered successors chosen from candidates (may be empty)
    rejected: List[str] = Field(default_factory=list)
    evidence_quotes: List[str] = Field(default_factory=list) # verbatim visible text cited by the model
    confidence: Optional[float] = None
    flags: List[str] = Field(default_factory=list)
    model: str
    prompt_id: str
    prompt_version: str

