"""Typed pipeline failure models (INV-AC4: no silent fabrication).

When a model-backed stage (fingerprinting, modality routing, semantic verification,
extraction) cannot produce a real result, it must NOT fabricate content. Instead it
raises one of the exceptions below, or returns a ``StageFailure`` record that the
orchestrator surfaces in the HITL queue and in the session status.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PipelineStage(str, Enum):
    """Pipeline stages that can fail."""

    INGESTION = "INGESTION"
    FINGERPRINT = "FINGERPRINT"
    MODALITY = "MODALITY"
    CORRELATION = "CORRELATION"
    SEMANTIC_VERIFICATION = "SEMANTIC_VERIFICATION"
    EXTRACTION = "EXTRACTION"
    EXPORT = "EXPORT"


class FailureReason(str, Enum):
    """Why a stage failed."""

    NO_API_KEY = "NO_API_KEY"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    SCHEMA_VALIDATION = "SCHEMA_VALIDATION"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    INPUT_UNREADABLE = "INPUT_UNREADABLE"
    UNKNOWN = "UNKNOWN"


class StageFailure(BaseModel):
    """Serializable record of a stage failure, surfaced to HITL and session status."""

    stage: PipelineStage
    reason: FailureReason = FailureReason.UNKNOWN
    message: str = ""
    card_ids: List[str] = Field(default_factory=list)
    proposal_id: Optional[str] = None
    retryable: bool = True
    details: Dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StageUnavailableError(RuntimeError):
    """Base exception: a model-backed stage could not produce a real result."""

    stage: PipelineStage = PipelineStage.EXTRACTION

    def __init__(
        self,
        message: str,
        *,
        reason: FailureReason = FailureReason.UNKNOWN,
        card_ids: Optional[List[str]] = None,
        retryable: bool = True,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.card_ids = list(card_ids or [])
        self.retryable = retryable
        self.details = dict(details or {})

    def to_failure(self, proposal_id: Optional[str] = None) -> StageFailure:
        """Convert to a serializable ``StageFailure`` record."""
        return StageFailure(
            stage=self.stage,
            reason=self.reason,
            message=str(self),
            card_ids=self.card_ids,
            proposal_id=proposal_id,
            retryable=self.retryable,
            details=self.details,
        )


class ExtractionUnavailable(StageUnavailableError):
    """Recipe extraction could not run (no key, API failure, invalid output)."""

    stage = PipelineStage.EXTRACTION


class FingerprintUnavailable(StageUnavailableError):
    """Model-backed visual fingerprinting could not run."""

    stage = PipelineStage.FINGERPRINT


class SemanticVerificationUnavailable(StageUnavailableError):
    """Model-backed semantic continuity verification could not run."""

    stage = PipelineStage.SEMANTIC_VERIFICATION


class ModalityUnavailable(StageUnavailableError):
    """Modality classification could not run."""

    stage = PipelineStage.MODALITY
