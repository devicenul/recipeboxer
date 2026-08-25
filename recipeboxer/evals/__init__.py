"""RecipeBoxer Evaluation Harness & Quantitative Benchmarking Suite.

Provides tools for evaluating recipe correlation, multimodal transcription accuracy,
schema conformity, and LLM-as-a-Judge semantic fidelity across historical recipe card scans.
"""

from recipeboxer.evals.judge import (
    JudgeEvaluationResult,
    RecipeJudge,
)
from recipeboxer.evals.metrics import (
    PairingMetricResult,
    SchemaConformityResult,
    TranscriptionMetricResult,
    calculate_direction_sequence_score,
    calculate_fraction_accuracy,
    calculate_pairing_metrics,
    calculate_transcription_metrics,
    character_error_rate,
    extract_fractions,
    levenshtein_distance,
    normalize_text,
    validate_schema_conformity,
    word_error_rate,
)
_RUNNER_ATTRS = {
    "HISTORICAL_MODALITIES",
    "BenchmarkReport",
    "BenchmarkRunner",
    "ModalityReport",
    "RecipeBenchmarkResult",
    "get_card_historical_categories",
    "main",
}


def __getattr__(name: str):
    if name in _RUNNER_ATTRS:
        from recipeboxer.evals import runner
        return getattr(runner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    # Metrics
    "calculate_pairing_metrics",
    "character_error_rate",
    "word_error_rate",
    "levenshtein_distance",
    "normalize_text",
    "extract_fractions",
    "calculate_transcription_metrics",
    "calculate_fraction_accuracy",
    "calculate_direction_sequence_score",
    "validate_schema_conformity",
    "PairingMetricResult",
    "TranscriptionMetricResult",
    "SchemaConformityResult",
    # Judge
    "RecipeJudge",
    "JudgeEvaluationResult",
    # Runner
    "BenchmarkRunner",
    "BenchmarkReport",
    "ModalityReport",
    "RecipeBenchmarkResult",
    "HISTORICAL_MODALITIES",
    "get_card_historical_categories",
    "main",
]
