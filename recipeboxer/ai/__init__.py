"""AI, Multimodal LLM Extraction, Semantic Verification, and Prompt Routing for RecipeBoxer."""

from typing import Any

from recipeboxer.ai.client import (
    AsyncTokenBucket,
    CassetteFingerprintMismatchError,
    GenAIClient,
    ImagePart,
    InMemoryUsageLedger,
    LLMClient,
    LLMRequest,
    LLMResponse,
    LLMStage,
    RecordingLLMClient,
    ReplayLLMClient,
    ScriptedLLMClient,
    TextPart,
    UsageLedger,
    UsageRecord,
    UsageTokens,
    create_llm_client,
    create_stage_error,
    sniff_mime_type,
)
from recipeboxer.ai.cost import (
    BudgetExceeded,
    BudgetGuard,
    CostLedger,
    PriceTable,
    estimate_run_cost,
)

from recipeboxer.ai.gemini import (
    sanitize_prompt_card_id,
    sanitize_prompt_text,
)
from recipeboxer.ai.prompt_router import (
    CardModality,
    ModalityClassificationResult,
    ModalityClassifier,
    PromptRouter,
    RoutedPromptConfig,
    classify_card_modality,
    route_prompt_for_modality,
)


def __getattr__(name: str) -> Any:
    if name == "GeminiRecipeExtractor":
        from recipeboxer.extraction.extractor import GeminiRecipeExtractor
        return GeminiRecipeExtractor
    if name == "GeminiSemanticVerifier":
        from recipeboxer.correlation.semantic import GeminiSemanticVerifier
        return GeminiSemanticVerifier
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "GeminiRecipeExtractor",
    "GeminiSemanticVerifier",
    "sanitize_prompt_card_id",
    "sanitize_prompt_text",
    "CardModality",
    "ModalityClassifier",
    "ModalityClassificationResult",
    "PromptRouter",
    "RoutedPromptConfig",
    "classify_card_modality",
    "route_prompt_for_modality",
    "LLMStage",
    "sniff_mime_type",
    "TextPart",
    "ImagePart",
    "UsageTokens",
    "LLMRequest",
    "LLMResponse",
    "LLMClient",
    "UsageRecord",
    "UsageLedger",
    "InMemoryUsageLedger",
    "AsyncTokenBucket",
    "GenAIClient",
    "create_llm_client",
    "create_stage_error",
    "ScriptedLLMClient",
    "RecordingLLMClient",
    "ReplayLLMClient",
    "CassetteFingerprintMismatchError",
    "PriceTable",
    "CostLedger",
    "BudgetGuard",
    "BudgetExceeded",
    "estimate_run_cost",
]

