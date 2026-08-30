"""AI, Multimodal LLM Extraction, Semantic Verification, and Prompt Routing for RecipeBoxer."""

from typing import Any

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
]
