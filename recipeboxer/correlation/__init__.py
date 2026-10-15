"""Two-stage card correlation and global pairing package for RecipeBoxer."""

from recipeboxer.correlation.engine import (
    FeatureToggles,
    FileImageProvider,
    GroupingEngine,
    GroupingResult,
    ImageProvider,
)
from recipeboxer.correlation.optimizer import PairingOptimizer, path_cover
from recipeboxer.correlation.semantic import GeminiSemanticVerifier, SetVerifier

__all__ = [
    "FeatureToggles",
    "FileImageProvider",
    "GeminiSemanticVerifier",
    "GroupingEngine",
    "GroupingResult",
    "ImageProvider",
    "PairingOptimizer",
    "SetVerifier",
    "path_cover",
]
