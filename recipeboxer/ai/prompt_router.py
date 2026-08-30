"""Prompt Router and Modality Classification Interfaces for RecipeBoxer."""

from __future__ import annotations

from recipeboxer.vision.router import (
    CardModality,
    ModalityClassificationResult,
    ModalityClassifier,
    PromptRouter,
    RoutedPromptConfig,
    classify_card_modality,
    route_prompt_for_modality,
)

__all__ = [
    "CardModality",
    "ModalityClassifier",
    "ModalityClassificationResult",
    "PromptRouter",
    "RoutedPromptConfig",
    "classify_card_modality",
    "route_prompt_for_modality",
]
