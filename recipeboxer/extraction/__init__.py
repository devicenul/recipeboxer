"""Recipe extraction package for RecipeBoxer.

Provides multimodal Gemini recipe transcription for transcribing scanned recipe cards into RecipeSchema models.
"""

from recipeboxer.extraction.extractor import (
    RECIPE_EXTRACTION_SYSTEM_INSTRUCTION,
    RECIPE_EXTRACTION_SYSTEM_PROMPT,
    GeminiRecipeExtractor,
    RecipeExtractor,
)

__all__ = [
    "GeminiRecipeExtractor",
    "RecipeExtractor",
    "RECIPE_EXTRACTION_SYSTEM_INSTRUCTION",
    "RECIPE_EXTRACTION_SYSTEM_PROMPT",
]
