"""Recipe extraction package for RecipeBoxer.

Provides multimodal transcription and structuring pipelines for transforming
scanned recipe cards into validated Recipe Schema v2 models.
"""

from recipeboxer.extraction.extractor import (
    RECIPE_EXTRACTION_SYSTEM_INSTRUCTION,
    RECIPE_EXTRACTION_SYSTEM_PROMPT,
    GeminiRecipeExtractor,
    RecipeExtractor,
)
from recipeboxer.extraction.image_prep import PreparedFace, prepare_faces
from recipeboxer.extraction.pipeline import ExtractionPipeline, ExtractionResult
from recipeboxer.extraction.structure import structure
from recipeboxer.extraction.transcribe import (
    FaceTranscript,
    FaceTranscriptList,
    Transcript,
    transcribe,
)

__all__ = [
    "GeminiRecipeExtractor",
    "RecipeExtractor",
    "ExtractionPipeline",
    "ExtractionResult",
    "transcribe",
    "Transcript",
    "FaceTranscript",
    "FaceTranscriptList",
    "structure",
    "PreparedFace",
    "prepare_faces",
    "RECIPE_EXTRACTION_SYSTEM_INSTRUCTION",
    "RECIPE_EXTRACTION_SYSTEM_PROMPT",
]
