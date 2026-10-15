"""RecipeBoxer quantitative evaluation metrics suite.

Provides pure functions for grouping, extraction, calibration, operational tracking,
and text normalization. Conforms strictly to WAVE3_CONTRACTS.md §5 Metrics API.
"""

from __future__ import annotations

from evals.metrics.calibration import ece
from evals.metrics.extraction import (
    aggregate,
    match_recipes_by_content,
    recipe_content_similarity,
    score_recipe,
)
from evals.metrics.grouping import match_recipes, score_grouping
from evals.metrics.ops import summarize_ledger
from evals.metrics.text import (
    cer,
    extract_fractions,
    levenshtein_distance,
    normalize_text,
    normalize_unit,
    quantities_match,
    wer,
)

__all__ = [
    "aggregate",
    "cer",
    "ece",
    "extract_fractions",
    "levenshtein_distance",
    "match_recipes",
    "match_recipes_by_content",
    "normalize_text",
    "normalize_unit",
    "quantities_match",
    "recipe_content_similarity",
    "score_grouping",
    "score_recipe",
    "summarize_ledger",
    "wer",
]
