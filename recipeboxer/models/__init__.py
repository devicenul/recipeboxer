"""Domain models, schemas, and enums for RecipeBoxer."""

from recipeboxer.models.correlation import (
    CorrelationProposal,
    CorrelationResult,
)
from recipeboxer.models.domain import (
    ArtifactType,
    BoundaryText,
    CardPhysicalHints,
    CardQuadrant,
    CardRole,
    InkColor,
    InkInfo,
    InkType,
    PaperColorInfo,
    PaperColorTone,
    PaperRulingInfo,
    PaperRulingType,
    PhysicalArtifact,
    VisualFingerprint,
)
from recipeboxer.models.hitl import (
    ApprovalPayload,
    CandidateListResponse,
    CandidateScoreBreakdown,
    CardPairApproval,
    CardSequenceApproval,
    CorrelationCandidate,
    HITLDecision,
)
from recipeboxer.models.pipeline import (
    IngestionMode,
    PipelineStartRequest,
    PipelineStartResponse,
    PipelineState,
    PipelineStatus,
    ProgressMetrics,
    TokenUsage,
)
from recipeboxer.models.recipe import (
    IllegibleSpan,
    IngredientLine,
    Legibility,
    Provenance,
    Recipe,
    RecipeSection,
    RecipeSchema,
    RecipeWarning,
    Step,
    TextSpan,
    TimeValue,
    YieldValue,
)
from recipeboxer.models.session import (
    IngestionConfig,
    SessionDetail,
    SessionSummary,
)

__all__ = [
    # Domain & Fingerprint
    "CardRole",
    "PaperColorTone",
    "PaperColorInfo",
    "PaperRulingType",
    "PaperRulingInfo",
    "InkColor",
    "InkType",
    "InkInfo",
    "ArtifactType",
    "CardQuadrant",
    "PhysicalArtifact",
    "BoundaryText",
    "CardPhysicalHints",
    "VisualFingerprint",
    # Correlation
    "CorrelationProposal",
    "CorrelationResult",
    # HITL
    "HITLDecision",
    "CardPairApproval",
    "CardSequenceApproval",
    "ApprovalPayload",
    "CandidateScoreBreakdown",
    "CorrelationCandidate",
    "CandidateListResponse",
    # Recipe
    "Recipe",
    "RecipeSchema",
    "IngredientLine",
    "Step",
    "RecipeSection",
    "TextSpan",
    "IllegibleSpan",
    "Legibility",
    "RecipeWarning",
    "Provenance",
    "TimeValue",
    "YieldValue",
    # Pipeline
    "PipelineState",
    "IngestionMode",
    "ProgressMetrics",
    "TokenUsage",
    "PipelineStatus",
    "PipelineStartRequest",
    "PipelineStartResponse",
    # Session
    "SessionSummary",
    "SessionDetail",
    "IngestionConfig",
]
