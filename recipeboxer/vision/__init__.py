"""Vision and physical fingerprinting package for RecipeBoxer."""

from recipeboxer.vision.fingerprint import GeminiVisualExtractor
from recipeboxer.vision.heuristics import (
    QUADRANT_MIRROR_MAP,
    calculate_color_similarity,
    calculate_heuristic_distance,
    calculate_ink_similarity,
    calculate_proximity_score,
    calculate_role_compatibility,
    calculate_ruling_similarity,
    calculate_stain_similarity,
    hex_to_lab,
    rgb_to_lab,
    screen_candidate_pairs,
)

__all__ = [
    "GeminiVisualExtractor",
    "QUADRANT_MIRROR_MAP",
    "calculate_color_similarity",
    "calculate_proximity_score",
    "calculate_stain_similarity",
    "calculate_ruling_similarity",
    "calculate_ink_similarity",
    "calculate_role_compatibility",
    "calculate_heuristic_distance",
    "screen_candidate_pairs",
    "rgb_to_lab",
    "hex_to_lab",
]
