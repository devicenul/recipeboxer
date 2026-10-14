"""Golden Dataset Benchmark Runner and CLI for RecipeBoxer.

Executes quantitative evaluations against the curated historical sample card corpus,
benchmarking pairing precision/recall/F1, transcription CER/WER, schema conformity,
and LLM-as-a-Judge semantic scores across historical modalities.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import random
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from pydantic import BaseModel, Field

from recipeboxer.config import Settings, get_settings
from recipeboxer.correlation.optimizer import PairingOptimizer
from recipeboxer.correlation.semantic import GeminiSemanticVerifier
from recipeboxer.evals.judge import JudgeEvaluationResult, RecipeJudge
from recipeboxer.evals.metrics import (
    PairingMetricResult,
    SchemaConformityResult,
    TranscriptionMetricResult,
    calculate_pairing_metrics,
    calculate_transcription_metrics,
    validate_schema_conformity,
)
from recipeboxer.evals.obfuscation import ObfuscatedCardCorpus, ObfuscationStrategy
from recipeboxer.exporter.plan_to_eat import PlanToEatExporter

from recipeboxer.extraction.extractor import GeminiRecipeExtractor
from recipeboxer.models.correlation import CorrelationProposal, CorrelationResult
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.failures import FailureReason, StageUnavailableError
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.vision.router import CardModality
from recipeboxer.vision.heuristics import calculate_heuristic_distance


try:
    from tests.fixtures.loader import CardEntry, SampleCardCorpus, get_manifest_path, get_sample_card_dir
except ImportError:
    _repo_root = Path(__file__).resolve().parent.parent.parent
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))
    from tests.fixtures.loader import CardEntry, SampleCardCorpus, get_manifest_path, get_sample_card_dir

logger = logging.getLogger(__name__)

# Canonical historical modality names
HISTORICAL_MODALITIES = [
    "Vintage Handwritten Cursive",
    "Typewritten & Stamped",
    "Magazine Clippings & Labels",
    "Stained / Damaged / Edge-Folded",
    "Multi-Card Sets (3+ Cards)",
    "Standalone Single-Sided Cards",
]


# ============================================================================
# 1. BENCHMARK REPORT DATA MODELS
# ============================================================================

class ModalityReport(BaseModel):
    """Benchmark results aggregated for a specific historical modality."""
    modality_name: str = Field(..., description="Canonical modality category name")
    card_count: int = Field(default=0, ge=0, description="Total card scans in modality")
    recipe_count: int = Field(default=0, ge=0, description="Total distinct recipes in modality")
    pairing_precision: float = Field(default=0.0, ge=0.0, le=1.0)
    pairing_recall: float = Field(default=0.0, ge=0.0, le=1.0)
    pairing_f1: float = Field(default=0.0, ge=0.0, le=1.0)
    overall_cer: Optional[float] = Field(default=None, ge=0.0)
    overall_wer: Optional[float] = Field(default=None, ge=0.0)
    ingredient_cer: Optional[float] = Field(default=None, ge=0.0)
    direction_cer: Optional[float] = Field(default=None, ge=0.0)
    fraction_accuracy: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    schema_conformity_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    judge_aggregate_score: Optional[float] = Field(default=None, ge=1.0, le=5.0)
    judge_pass_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class RecipeBenchmarkResult(BaseModel):
    """Per-recipe benchmark metrics across all evaluation stages."""
    recipe_id: str
    title: str = "N/A"
    modalities: List[str] = Field(default_factory=list)
    card_count: int = 1
    transcription: Optional[TranscriptionMetricResult] = None
    schema_conformity: Optional[SchemaConformityResult] = None
    judge_evaluation: Optional[JudgeEvaluationResult] = None
    extraction_latency_ms: float = 0.0
    extraction_status: str = "success"
    error_message: Optional[str] = None


class BenchmarkReport(BaseModel):
    """Comprehensive benchmark execution report for RecipeBoxer."""
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    dataset_name: str = "RecipeBoxer Curated Historical Recipe Card Corpus"
    model_name: str = "gemini-3.7-flash"
    stage_filter: str = "full"
    modality_filter: Optional[str] = None
    grouping_track: str = Field(
        default="oracle_grouping",
        description="Grouping track strategy: oracle_grouping (interim GT clusters) or predicted_clustering",
    )
    total_cards: int = 0
    total_recipes: int = 0
    pairing_metrics: PairingMetricResult = Field(default_factory=PairingMetricResult)
    overall_cer: Optional[float] = None
    overall_wer: Optional[float] = None
    ingredient_cer: Optional[float] = None
    direction_cer: Optional[float] = None
    fraction_accuracy: Optional[float] = None
    schema_conformity_rate: Optional[float] = None
    judge_aggregate_score: Optional[float] = None
    judge_pass_rate: Optional[float] = None
    judge_dimension_scores: Dict[str, float] = Field(default_factory=dict)
    modality_breakdown: Dict[str, ModalityReport] = Field(default_factory=dict)
    recipe_results: List[RecipeBenchmarkResult] = Field(default_factory=list)
    total_token_usage: Dict[str, int] = Field(
        default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    )
    total_duration_seconds: float = 0.0
    obfuscation_enabled: bool = Field(
        default=True,
        description="Whether card filenames and metadata were obfuscated during evaluation",
    )
    has_live_model: bool = Field(
        default=False,
        description="Whether a live Gemini API model was used for evaluation",
    )
    stage_failures_count: int = Field(
        default=0,
        description="Total count of stage failures caught during evaluation",
    )
    stage_failure_reasons: Dict[str, int] = Field(
        default_factory=dict,
        description="Breakdown of failure reasons",
    )
    mock_judge_used: bool = Field(
        default=False,
        description="Whether mock judge was explicitly called",
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serializes report to dictionary."""
        return self.model_dump()

    def to_json(self, indent: int = 2) -> str:
        """Serializes report to formatted JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def to_markdown(self) -> str:
        """Generates rich, publication-ready GitHub-flavored Markdown benchmark report."""
        lines: List[str] = []
        lines.append(f"# RecipeBoxer Benchmark Evaluation Report")
        lines.append(f"")
        lines.append(f"- **Timestamp**: `{self.timestamp}`")
        lines.append(f"- **Dataset**: {self.dataset_name} ({self.total_cards} cards across {self.total_recipes} recipes)")
        lines.append(f"- **Target Model**: `{self.model_name}`")
        lines.append(f"- **Stage Filter**: `{self.stage_filter}`")
        lines.append(f"- **Modality Filter**: `{self.modality_filter or 'All Modalities'}`")
        lines.append(f"- **Grouping Track**: `{self.grouping_track}` (interim oracle grouping; WP-E4 replaces with predicted clusters)")
        lines.append(f"- **Obfuscation**: `{'Active (Anti-Cheating Enabled)' if self.obfuscation_enabled else 'Disabled'}`")
        lines.append(f"- **Total Duration**: `{self.total_duration_seconds:.2f}s`")
        lines.append(f"")
        lines.append(f"---")

        lines.append(f"")
        lines.append(f"## 1. Executive Summary")
        lines.append(f"")
        lines.append(f"| Metric Dimension | Result | Target Benchmark | Status |")
        lines.append(f"|:---|:---:|:---:|:---:|")

        # Helpers for formatting metrics
        def _fmt_pct(val: Optional[float]) -> str:
            if val is not None:
                return f"{val * 100:.2f}%"
            if self.stage_filter == "correlation":
                return "N/A (skipped in correlation stage)"
            if not self.has_live_model:
                return "N/A (no live model)"
            return "N/A"

        def _fmt_frac(val: Optional[float]) -> str:
            if val is not None:
                return f"{val * 100:.1f}%"
            if self.stage_filter == "correlation":
                return "N/A (skipped in correlation stage)"
            if not self.has_live_model:
                return "N/A (no live model)"
            return "N/A"

        # Pairing F1
        p_status = "PASS" if self.pairing_metrics.f1_score >= 0.85 else "WARN"
        lines.append(f"| **Correlation Pairing F1** | **{self.pairing_metrics.f1_score * 100:.1f}%** (P: {self.pairing_metrics.precision * 100:.1f}%, R: {self.pairing_metrics.recall * 100:.1f}%) | $\\ge 85.0\\%$ | `{p_status}` |")

        # CER
        cer_status = "PASS" if self.overall_cer is not None and self.overall_cer <= 0.15 else ("N/A" if self.overall_cer is None else "WARN")
        lines.append(f"| **Overall Character Error Rate (CER)** | **{_fmt_pct(self.overall_cer)}** | $\\le 15.0\\%$ | `{cer_status}` |")

        # WER
        wer_status = "PASS" if self.overall_wer is not None and self.overall_wer <= 0.25 else ("N/A" if self.overall_wer is None else "WARN")
        lines.append(f"| **Overall Word Error Rate (WER)** | **{_fmt_pct(self.overall_wer)}** | $\\le 25.0\\%$ | `{wer_status}` |")

        # Fraction Accuracy
        frac_status = "PASS" if self.fraction_accuracy is not None and self.fraction_accuracy >= 0.90 else ("N/A" if self.fraction_accuracy is None else "WARN")
        lines.append(f"| **Fraction Extraction Accuracy** | **{_fmt_frac(self.fraction_accuracy)}** | $\\ge 90.0\\%$ | `{frac_status}` |")

        # Schema Conformity
        schema_status = "PASS" if self.schema_conformity_rate is not None and self.schema_conformity_rate >= 0.95 else ("N/A" if self.schema_conformity_rate is None else "WARN")
        lines.append(f"| **Plan To Eat & Schema.org Conformity** | **{_fmt_frac(self.schema_conformity_rate)}** | $\\ge 95.0\\%$ | `{schema_status}` |")

        # Judge Aggregate Score
        if self.mock_judge_used and self.judge_aggregate_score is not None:
            j_str = f"{self.judge_aggregate_score:.2f} / 5.0 [mock (not a quality score)]"
            j_status = "PASS" if self.judge_aggregate_score >= 3.5 else "WARN"
        elif self.judge_aggregate_score is not None:
            j_str = f"{self.judge_aggregate_score:.2f} / 5.0" + (f" ({self.judge_pass_rate * 100:.1f}% pass rate)" if self.judge_pass_rate is not None else "")
            j_status = "PASS" if self.judge_aggregate_score >= 3.5 else "WARN"
        else:
            if self.stage_filter == "correlation":
                j_str = "N/A (skipped in correlation stage)"
            elif not self.has_live_model:
                j_str = "N/A (no live model)"
            else:
                j_str = "N/A (judge disabled)"
            j_status = "N/A"
        lines.append(f"| **LLM-as-a-Judge Aggregate Score** | **{j_str}** | $\\ge 3.5 / 5.0$ | `{j_status}` |")

        lines.append(f"")
        lines.append(f"---")

        if self.stage_failures_count > 0:
            lines.append(f"")
            lines.append(f"## Stage Failures (INV-AC4)")
            lines.append(f"")
            lines.append(f"- **Failed Extractions**: `{self.stage_failures_count}`")
            for reason, count in sorted(self.stage_failure_reasons.items()):
                lines.append(f"- **{reason}**: `{count}` failures")
            lines.append(f"")
            lines.append(f"---")

        lines.append(f"")
        lines.append(f"## 2. Modality Breakdown (6 Historical Categories)")
        lines.append(f"")
        lines.append(f"| Historical Modality | Cards | Recipes | Pairing F1 | Overall CER | Overall WER | Fraction Acc | Conformity | Judge Score |")
        lines.append(f"|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")

        for mod_name, rep in self.modality_breakdown.items():
            cer_col = f"{rep.overall_cer * 100:.2f}%" if rep.overall_cer is not None else "N/A"
            wer_col = f"{rep.overall_wer * 100:.2f}%" if rep.overall_wer is not None else "N/A"
            frac_col = f"{rep.fraction_accuracy * 100:.1f}%" if rep.fraction_accuracy is not None else "N/A"
            conf_col = f"{rep.schema_conformity_rate * 100:.1f}%" if rep.schema_conformity_rate is not None else "N/A"
            if rep.judge_aggregate_score is not None:
                j_col = f"{rep.judge_aggregate_score:.2f} / 5.0" + (" [mock]" if self.mock_judge_used else "")
            else:
                j_col = "N/A"
            lines.append(
                f"| **{mod_name}** | {rep.card_count} | {rep.recipe_count} | "
                f"{rep.pairing_f1 * 100:.1f}% | {cer_col} | "
                f"{wer_col} | {frac_col} | "
                f"{conf_col} | {j_col} |"
            )

        lines.append(f"")
        lines.append(f"---")
        lines.append(f"")
        judge_header = "## 3. LLM-as-a-Judge Qualitative Rubric Dimensions"
        if self.mock_judge_used:
            judge_header += " [mock (not a quality score)]"
        lines.append(judge_header)
        lines.append(f"")
        if not self.judge_dimension_scores:
            lines.append("*(No judge qualitative scores evaluated)*")
        else:
            lines.append(f"| Rubric Dimension | Mean Score (1-5) | Target | Description |")
            lines.append(f"|:---|:---:|:---:|:---|")
            dim_targets = {
                "semantic_fidelity": ("Semantic Culinary Fidelity", ">= 3.5", "Faithfulness of cooking techniques, temperatures, and units"),
                "vintage_headnote_preservation": ("Vintage Headnote Preservation", ">= 3.0", "Preservation of family stories, marginalia, and notes"),
                "ingredient_direction_cross_consistency": ("Ingredient-Direction Consistency", ">= 4.0", "Cross-check that ingredients match directions"),
                "step_ordering_logic": ("Step Ordering Logic", ">= 4.0", "Chronological progression of prep and cooking steps"),
            }
            for dim_key, (dim_title, target, desc) in dim_targets.items():
                score = self.judge_dimension_scores.get(dim_key, 0.0)
                lines.append(f"| **{dim_title}** | **{score:.2f}** | `{target}` | {desc} |")

        lines.append(f"")
        lines.append(f"---")
        lines.append(f"")
        lines.append(f"## 4. Itemized Recipe Results")
        lines.append(f"")
        if not self.recipe_results:
            lines.append("*(No itemized recipes extracted in this stage)*")
        else:
            lines.append(f"| Recipe ID | Title | Cards | Modalities | CER | WER | Fraction Acc | Conformity | Judge Score |")
            lines.append(f"|:---|:---|:---:|:---|:---:|:---:|:---:|:---:|:---:|")

            for r in self.recipe_results:
                j_score = f"{r.judge_evaluation.aggregate_score:.2f}" if r.judge_evaluation else "N/A"
                if self.mock_judge_used and r.judge_evaluation:
                    j_score += " [mock]"
                if r.transcription is not None:
                    cer_str = f"{r.transcription.overall_cer * 100:.1f}%"
                    wer_str = f"{r.transcription.overall_wer * 100:.1f}%"
                    frac_str = f"{r.transcription.fraction_accuracy * 100:.0f}%"
                else:
                    cer_str = "N/A"
                    wer_str = "N/A"
                    frac_str = "N/A"
                c_status = "PASS" if r.schema_conformity and r.schema_conformity.is_valid else ("FAIL" if r.schema_conformity else "N/A")
                mods = ", ".join(r.modalities[:2])
                lines.append(
                    f"| `{r.recipe_id}` | {r.title} | {r.card_count} | {mods} | "
                    f"{cer_str} | {wer_str} | "
                    f"{frac_str} | `{c_status}` | {j_score} |"
                )

        lines.append(f"")
        lines.append(f"---")
        lines.append(f"")
        lines.append(f"## 5. Execution Telemetry")
        lines.append(f"")
        lines.append(f"- **Total Latency**: `{self.total_duration_seconds:.2f} seconds`")
        lines.append(f"- **Prompt Tokens**: `{self.total_token_usage.get('prompt_tokens', 0):,}`")
        lines.append(f"- **Completion Tokens**: `{self.total_token_usage.get('completion_tokens', 0):,}`")
        lines.append(f"- **Total Tokens**: `{self.total_token_usage.get('total_tokens', 0):,}`")

        return "\n".join(lines)

    def save(self, output_dir: Union[str, Path]) -> Tuple[Path, Path]:
        """Saves both JSON and Markdown benchmark reports to output directory.

        Returns:
            Tuple of (json_path, markdown_path).
        """
        out_p = Path(output_dir).resolve()
        out_p.mkdir(parents=True, exist_ok=True)

        slug = f"benchmark_report_{self.model_name.replace(':', '_')}_{int(time.time())}"
        json_path = out_p / f"{slug}.json"
        md_path = out_p / f"{slug}.md"

        json_path.write_text(self.to_json(indent=2), encoding="utf-8")
        md_path.write_text(self.to_markdown(), encoding="utf-8")
        logger.info(f"Saved benchmark reports to: {json_path} and {md_path}")
        return json_path, md_path


# ============================================================================
# 2. MODALITY TAGGING HELPER
# ============================================================================

def get_card_historical_categories(card: CardEntry) -> List[str]:
    """Classifies a card entry into one or more of the 6 canonical historical modalities.

    Categories:
    1. 'Vintage Handwritten Cursive' (HANDWRITTEN_CURSIVE)
    2. 'Typewritten & Stamped' (TYPEWRITTEN)
    3. 'Magazine Clippings & Labels' (PRINTED_CLIPPING)
    4. 'Stained / Damaged / Edge-Folded' (Physical stains, tears, coffee rings, MIXED_MEDIA)
    5. 'Multi-Card Sets (3+ Cards)' (Clusters with >= 3 cards)
    6. 'Standalone Single-Sided Cards' (Single card recipes)
    """
    categories: List[str] = []

    # 0. Check explicit historical dimension metadata if available
    dim = card.recipe_cluster.get("dimension", "")
    if dim:
        if "Handwritten" in dim or "Cursive" in dim:
            categories.append("Vintage Handwritten Cursive")
        elif "Typewritten" in dim or "Stamped" in dim:
            categories.append("Typewritten & Stamped")
        elif "Magazine" in dim or "Clipping" in dim or "Label" in dim:
            categories.append("Magazine Clippings & Labels")
        elif "Stained" in dim or "Damaged" in dim or "Worn" in dim:
            categories.append("Stained / Damaged / Edge-Folded")
        elif "Multi-Card" in dim:
            categories.append("Multi-Card Sets (3+ Cards)")
        elif "Standalone" in dim:
            categories.append("Standalone Single-Sided Cards")

    if categories:
        return categories

    # 1. Modality base
    if card.modality == CardModality.HANDWRITTEN_CURSIVE:
        categories.append("Vintage Handwritten Cursive")
    elif card.modality == CardModality.TYPEWRITTEN:
        categories.append("Typewritten & Stamped")
    elif card.modality == CardModality.PRINTED_CLIPPING:
        categories.append("Magazine Clippings & Labels")

    # 2. Stained / Damaged
    artifacts = card.physical_characteristics.get("artifacts", [])
    if artifacts or card.modality == CardModality.MIXED_MEDIA:
        categories.append("Stained / Damaged / Edge-Folded")

    # 3. Multi-Card Sets (3+)
    total_cards = card.recipe_cluster.get("total_cards", 1)
    if total_cards >= 3:
        categories.append("Multi-Card Sets (3+ Cards)")

    # 4. Standalone
    if card.role == CardRole.STANDALONE or total_cards == 1:
        categories.append("Standalone Single-Sided Cards")

    # Fallback to modality string if empty
    if not categories:
        categories.append(card.modality.value)

    return categories



# ============================================================================
# 3. BENCHMARK RUNNER ENGINE
# ============================================================================

class BenchmarkRunner:
    """Automated benchmark runner evaluating RecipeBoxer models against golden dataset."""

    def __init__(
        self,
        manifest_path: Optional[Union[str, Path]] = None,
        corpus_dir: Optional[Union[str, Path]] = None,
        model_name: Optional[str] = None,
        stage: str = "full",
        modality: Optional[str] = None,
        judge_enabled: Optional[bool] = None,
        mock_judge: bool = False,
        live_judge: bool = False,
        obfuscate_filenames: bool = True,
        obfuscation_strategy: ObfuscationStrategy = ObfuscationStrategy.RANDOM,
        extractor: Optional[Any] = None,
        judge: Optional[Any] = None,
        optimizer: Optional[Any] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        """Initialize BenchmarkRunner.

        Args:
            manifest_path: Path to sample card corpus manifest.json.
            corpus_dir: Directory containing sample card scan images.
            model_name: Gemini model name override (e.g. 'gemini-3.7-flash').
            stage: Stage filter: 'full', 'correlation', or 'extraction'.
            modality: Modality filter string (e.g. 'HANDWRITTEN_CURSIVE', 'MULTI_CARD').
            judge_enabled: Explicit enable/disable for judge scoring pass.
            mock_judge: If True, uses deterministic offline judge mock (for plumbing tests only).
            live_judge: If True, uses live Gemini API judge (requires live API key).
            obfuscate_filenames: If True, sanitizes filenames/metadata to opaque identifiers for anti-cheating.
            obfuscation_strategy: Naming strategy for opaque identifiers.
            extractor: Optional custom RecipeExtractor instance.
            judge: Optional custom RecipeJudge instance.
            optimizer: Optional custom PairingOptimizer instance.
            settings: Optional custom Settings.
        """
        self.settings = settings or get_settings()
        self.manifest_path = Path(manifest_path or get_manifest_path()).resolve()
        self.corpus_dir = Path(corpus_dir or get_sample_card_dir()).resolve()
        self.model_name = model_name or self.settings.default_model
        self.stage = stage.lower().strip()
        self.modality_filter = modality
        self.obfuscate_filenames = obfuscate_filenames
        self.obfuscation_strategy = obfuscation_strategy

        self.corpus = SampleCardCorpus(
            corpus_dir=self.corpus_dir,
            manifest_path=self.manifest_path,
        )

        # Initialize extractor
        self.extractor = extractor or GeminiRecipeExtractor(
            model_name=self.model_name,
            settings=self.settings,
        )

        api_key = getattr(self.extractor, "api_key", None) or self.settings.gemini_api_key
        self.has_live_model = bool(api_key and not str(api_key).startswith("mock") and api_key != "MOCK_KEY")

        # Judge setup:
        # Without --live-judge (and a key), the judge is disabled and no judge scores are reported.
        # Mock judge is only callable explicitly for plumbing tests (mock_judge=True).
        if judge is not None:
            self.judge = judge
            self.judge_enabled = True if judge_enabled is None else judge_enabled
            self.mock_judge = getattr(judge, "mock", False)
        elif mock_judge:
            self.mock_judge = True
            self.judge_enabled = True if judge_enabled is None else judge_enabled
            self.judge = RecipeJudge(
                model_name=self.model_name,
                mock=True,
                settings=self.settings,
            )
        elif live_judge and self.has_live_model:
            self.mock_judge = False
            self.judge_enabled = True if judge_enabled is None else judge_enabled
            self.judge = RecipeJudge(
                model_name=self.model_name,
                mock=False,
                settings=self.settings,
            )
        else:
            self.mock_judge = False
            self.judge_enabled = False
            self.judge = None

        self.optimizer = optimizer or PairingOptimizer()

    def filter_cards(self) -> List[CardEntry]:
        """Applies modality filters to the sample card dataset."""
        if not self.modality_filter:
            return list(self.corpus.cards)

        mod_filter_norm = self.modality_filter.upper().replace("-", "_").replace(" ", "_")

        matched: List[CardEntry] = []
        for card in self.corpus.cards:
            cats = [c.upper().replace("-", "_").replace(" ", "_") for c in get_card_historical_categories(card)]
            card_mod = card.modality.value.upper().replace("-", "_").replace(" ", "_")
            if (
                mod_filter_norm in cats
                or mod_filter_norm == card_mod
                or any(mod_filter_norm in c for c in cats)
            ):
                matched.append(card)

        return matched

    async def run(self) -> BenchmarkReport:
        """Executes the full benchmark evaluation workflow asynchronously.

        Returns:
            Populated and validated BenchmarkReport.
        """
        start_time = time.perf_counter()
        cards_to_eval = self.filter_cards()

        if not cards_to_eval:
            raise ValueError(f"No cards matched filter criteria: modality='{self.modality_filter}'")

        # Group cards by recipe cluster
        recipes_map: Dict[str, List[CardEntry]] = {}
        for card in cards_to_eval:
            rec_id = card.ground_truth_recipe.recipe_id
            if rec_id not in recipes_map:
                recipes_map[rec_id] = []
            recipes_map[rec_id].append(card)

        if self.obfuscate_filenames:
            with ObfuscatedCardCorpus(cards_to_eval, strategy=self.obfuscation_strategy) as obfuscator:
                return await self._run_with_cards(cards_to_eval, recipes_map, obfuscator, start_time)
        else:
            return await self._run_with_cards(cards_to_eval, recipes_map, None, start_time)

    async def _run_with_cards(
        self,
        cards_to_eval: List[CardEntry],
        recipes_map: Dict[str, List[CardEntry]],
        obfuscator: Optional[ObfuscatedCardCorpus],
        start_time: float,
    ) -> BenchmarkReport:
        """Internal execution pass with optional active obfuscation."""
        # --------------------------------------------------------------------
        # 1. STAGE 1 & 2: CORRELATION & PAIRING BENCHMARK
        # --------------------------------------------------------------------
        logger.info(
            f"Running pairing evaluation across {len(cards_to_eval)} cards "
            f"(obfuscation={'active' if obfuscator else 'disabled'})..."
        )
        # Build ground-truth pairs and clusters
        gt_clusters: List[Set[str]] = []
        for rec_id, rcards in recipes_map.items():
            gt_clusters.append({c.card_id for c in rcards})

        # Run heuristic pairing proposals via PairingOptimizer
        # Build VisualFingerprint for the SUT with ONLY card_id (opaque), image_path,
        # and sha256 of the actual file. Never pass role, paper_tone, ruling_type,
        # ink_type, modality or any manifest field to the system under test.
        if obfuscator is not None:
            fingerprints = [
                VisualFingerprint(
                    card_id=obf_card.obfuscated_id,
                    image_path=str(obf_card.file_path),
                    sha256_hash=obf_card.sha256_hash,
                    role=CardRole.UNKNOWN,
                )
                for obf_card in obfuscator.obfuscated_cards
            ]
            raw_correlation_result = await self.optimizer.optimize(fingerprints)
            correlation_result = obfuscator.map_correlation_result(raw_correlation_result)
        else:
            fingerprints = [
                VisualFingerprint(
                    card_id=c.card_id,
                    image_path=str(c.file_path),
                    sha256_hash=hashlib.sha256(Path(c.file_path).read_bytes()).hexdigest(),
                    role=CardRole.UNKNOWN,
                )
                for c in cards_to_eval
            ]
            correlation_result = await self.optimizer.optimize(fingerprints)

        pairing_metrics = calculate_pairing_metrics(correlation_result, recipes_map)

        # --------------------------------------------------------------------
        # 2. STAGE 3: RECIPE EXTRACTION, TRANSCRIPTION & SCHEMA CONFORMITY
        # --------------------------------------------------------------------
        recipe_results: List[RecipeBenchmarkResult] = []
        total_tokens = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        stage_failures_count = 0
        stage_failure_reasons: Dict[str, int] = {}

        if self.stage in ("full", "extraction"):
            for rec_id, rcards in recipes_map.items():
                ref_recipe = rcards[0].ground_truth_recipe
                categories = list(set(get_card_historical_categories(rcards[0])))

                # TODO(WP-E4): Stage-3 extraction currently iterates GT clusters ('oracle_grouping');
                # WP-E4 replaces this with predicted clusters from correlation stage.
                # Give the extractor the face paths in a RANDOM order (not GT sequence_position)
                shuffled_cards = list(rcards)
                random.shuffle(shuffled_cards)

                shuffled_paths = [
                    obfuscator.get_obfuscated_path(c.card_id) if obfuscator else c.file_path
                    for c in shuffled_cards
                ]

                # Extraction
                ext_start = time.perf_counter()
                try:
                    if len(shuffled_paths) == 1:
                        hyp_recipe = await self.extractor.extract(shuffled_paths[0])
                    elif len(shuffled_paths) == 2:
                        hyp_recipe = await self.extractor.extract(shuffled_paths[0], shuffled_paths[1])
                    else:
                        hyp_recipe = await self.extractor.extract(shuffled_paths)
                except StageUnavailableError as sue:
                    stage_failures_count += 1
                    reason_name = sue.reason.value if hasattr(sue.reason, "value") else str(sue.reason)
                    stage_failure_reasons[reason_name] = stage_failure_reasons.get(reason_name, 0) + 1
                    logger.warning(f"Recipe extraction unavailable for {rec_id}: {sue}")
                    res_entry = RecipeBenchmarkResult(
                        recipe_id=rec_id,
                        title=ref_recipe.title,
                        modalities=categories,
                        card_count=len(rcards),
                        transcription=None,
                        schema_conformity=None,
                        judge_evaluation=None,
                        extraction_latency_ms=round((time.perf_counter() - ext_start) * 1000.0, 2),
                    )
                    recipe_results.append(res_entry)
                    continue

                ext_latency_ms = (time.perf_counter() - ext_start) * 1000.0

                # Compute transcription error rates
                trans_metrics = calculate_transcription_metrics(ref_recipe, hyp_recipe, recipe_id=rec_id)

                # Compute schema conformity
                conformity = validate_schema_conformity(hyp_recipe)

                # Compute qualitative judge scores if enabled
                judge_res: Optional[JudgeEvaluationResult] = None
                if self.judge_enabled and self.judge is not None:
                    try:
                        judge_res = await self.judge.evaluate(
                            candidate_recipe=hyp_recipe,
                            reference_recipe=ref_recipe,
                            card_notes=f"Modality: {rcards[0].modality.value}",
                        )
                        if judge_res.token_usage:
                            for k, v in judge_res.token_usage.items():
                                total_tokens[k] = total_tokens.get(k, 0) + v
                    except Exception as j_err:
                        logger.warning(f"Judge evaluation failed for {rec_id}: {j_err}")
                        judge_res = None

                res_entry = RecipeBenchmarkResult(
                    recipe_id=rec_id,
                    title=hyp_recipe.title,
                    modalities=categories,
                    card_count=len(rcards),
                    transcription=trans_metrics,
                    schema_conformity=conformity,
                    judge_evaluation=judge_res,
                    extraction_latency_ms=round(ext_latency_ms, 2),
                )
                recipe_results.append(res_entry)
        else:
            # In correlation stage, do NOT set hyp_recipe = ref_recipe; skip extraction metrics instead
            pass

        # --------------------------------------------------------------------
        # 3. AGGREGATE SUMMARY & MODALITY BREAKDOWN
        # --------------------------------------------------------------------
        successful_results = [r for r in recipe_results if r.transcription is not None]
        num_succ = len(successful_results)

        if num_succ > 0:
            overall_cer = round(sum(r.transcription.overall_cer for r in successful_results) / num_succ, 4)
            overall_wer = round(sum(r.transcription.overall_wer for r in successful_results) / num_succ, 4)
            ingredient_cer = round(sum(r.transcription.ingredient_cer for r in successful_results) / num_succ, 4)
            direction_cer = round(sum(r.transcription.direction_cer for r in successful_results) / num_succ, 4)
            fraction_acc = round(sum(r.transcription.fraction_accuracy for r in successful_results) / num_succ, 4)
            conformity_rate = round(sum(1.0 for r in successful_results if r.schema_conformity and r.schema_conformity.is_valid) / num_succ, 4)
        else:
            overall_cer = None
            overall_wer = None
            ingredient_cer = None
            direction_cer = None
            fraction_acc = None
            conformity_rate = None

        judge_scores = [r.judge_evaluation.aggregate_score for r in recipe_results if r.judge_evaluation and r.judge_evaluation.aggregate_score is not None]
        if judge_scores:
            judge_agg = round(sum(judge_scores) / len(judge_scores), 2)
            judge_passes = sum(1.0 for r in recipe_results if r.judge_evaluation and r.judge_evaluation.passed)
            judge_pass_rate = round(judge_passes / len(judge_scores), 4)
        else:
            judge_agg = None
            judge_pass_rate = None

        # Judge dimension breakdown
        dim_sums: Dict[str, float] = {}
        for r in recipe_results:
            if r.judge_evaluation and r.judge_evaluation.dimension_scores:
                for d_key, score in r.judge_evaluation.dimension_scores.items():
                    dim_sums[d_key] = dim_sums.get(d_key, 0.0) + score
        judge_dims = {k: round(v / len(judge_scores), 2) for k, v in dim_sums.items()} if judge_scores else {}

        # 4. Modality-specific aggregation
        modality_breakdown: Dict[str, ModalityReport] = {}
        for mod_name in HISTORICAL_MODALITIES:
            mod_keyword = mod_name.upper().replace("-", "_").replace(" ", "_")
            matched_recipe_ids = []
            for rid, rcards in recipes_map.items():
                card_cats = [c.upper().replace("-", "_").replace(" ", "_") for c in get_card_historical_categories(rcards[0])]
                if mod_keyword in card_cats or any(mod_keyword in c for c in card_cats):
                    matched_recipe_ids.append(rid)

            if matched_recipe_ids:
                m_count = len(matched_recipe_ids)
                m_cards = sum(len(recipes_map[rid]) for rid in matched_recipe_ids)

                mod_gt_recipes = {rid: recipes_map[rid] for rid in matched_recipe_ids}
                mod_card_ids = {c.card_id for cards in mod_gt_recipes.values() for c in cards}
                mod_proposals = [
                    p for p in correlation_result.proposals
                    if any(cid in mod_card_ids for cid in p.ordered_card_ids)
                ]
                mod_pairing = calculate_pairing_metrics(mod_proposals, mod_gt_recipes)

                mod_succ = [
                    r for r in recipe_results
                    if r.recipe_id in matched_recipe_ids and r.transcription is not None
                ]
                if mod_succ:
                    n_succ = len(mod_succ)
                    m_cer = round(sum(r.transcription.overall_cer for r in mod_succ) / n_succ, 4)
                    m_wer = round(sum(r.transcription.overall_wer for r in mod_succ) / n_succ, 4)
                    m_ing_cer = round(sum(r.transcription.ingredient_cer for r in mod_succ) / n_succ, 4)
                    m_dir_cer = round(sum(r.transcription.direction_cer for r in mod_succ) / n_succ, 4)
                    m_frac = round(sum(r.transcription.fraction_accuracy for r in mod_succ) / n_succ, 4)
                    m_conf = round(sum(1.0 for r in mod_succ if r.schema_conformity and r.schema_conformity.is_valid) / n_succ, 4)
                else:
                    m_cer = None
                    m_wer = None
                    m_ing_cer = None
                    m_dir_cer = None
                    m_frac = None
                    m_conf = None

                mod_j_scores = [
                    r.judge_evaluation.aggregate_score
                    for r in recipe_results
                    if r.recipe_id in matched_recipe_ids and r.judge_evaluation and r.judge_evaluation.aggregate_score is not None
                ]
                if mod_j_scores:
                    m_j_agg = round(sum(mod_j_scores) / len(mod_j_scores), 2)
                    m_j_passes = sum(1.0 for r in recipe_results if r.recipe_id in matched_recipe_ids and r.judge_evaluation and r.judge_evaluation.passed)
                    m_j_pass = round(m_j_passes / len(mod_j_scores), 4)
                else:
                    m_j_agg = None
                    m_j_pass = None

                modality_breakdown[mod_name] = ModalityReport(
                    modality_name=mod_name,
                    card_count=m_cards,
                    recipe_count=m_count,
                    pairing_precision=mod_pairing.precision,
                    pairing_recall=mod_pairing.recall,
                    pairing_f1=mod_pairing.f1_score,
                    overall_cer=m_cer,
                    overall_wer=m_wer,
                    ingredient_cer=m_ing_cer,
                    direction_cer=m_dir_cer,
                    fraction_accuracy=m_frac,
                    schema_conformity_rate=m_conf,
                    judge_aggregate_score=m_j_agg,
                    judge_pass_rate=m_j_pass,
                )

        total_elapsed = time.perf_counter() - start_time

        return BenchmarkReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            dataset_name=self.corpus.manifest.get("dataset_name", "RecipeBoxer Curated Historical Recipe Card Corpus"),
            model_name=self.model_name,
            stage_filter=self.stage,
            modality_filter=self.modality_filter,
            obfuscation_enabled=bool(obfuscator is not None),
            grouping_track="oracle_grouping",
            has_live_model=self.has_live_model,
            stage_failures_count=stage_failures_count,
            stage_failure_reasons=stage_failure_reasons,
            mock_judge_used=self.mock_judge and (self.judge_enabled or any(r.judge_evaluation for r in recipe_results)),
            total_cards=len(cards_to_eval),
            total_recipes=len(recipes_map),
            pairing_metrics=pairing_metrics,
            overall_cer=overall_cer,
            overall_wer=overall_wer,
            ingredient_cer=ingredient_cer,
            direction_cer=direction_cer,
            fraction_accuracy=fraction_acc,
            schema_conformity_rate=conformity_rate,
            judge_aggregate_score=judge_agg,
            judge_pass_rate=judge_pass_rate,
            judge_dimension_scores=judge_dims,
            modality_breakdown=modality_breakdown,
            recipe_results=recipe_results,
            total_token_usage=total_tokens,
            total_duration_seconds=round(total_elapsed, 2),
        )


    def run_sync(self) -> BenchmarkReport:
        """Synchronously executes the benchmark runner."""
        return asyncio.run(self.run())


# ============================================================================
# 4. CLI INTERFACE
# ============================================================================

def build_cli_parser() -> argparse.ArgumentParser:
    """Constructs the command-line interface argument parser for evals runner."""
    parser = argparse.ArgumentParser(
        prog="recipeboxer.evals.runner",
        description="RecipeBoxer Quantitative & LLM-as-a-Judge Evaluation Benchmark Runner",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-m",
        "--manifest",
        type=str,
        default=str(get_manifest_path()),
        help="Path to ground-truth manifest.json",
    )
    parser.add_argument(
        "--corpus-dir",
        type=str,
        default=str(get_sample_card_dir()),
        help="Path to sample card scan images directory",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="gemini-3.7-flash",
        help="Target Gemini model identifier",
    )
    parser.add_argument(
        "-s",
        "--stage",
        type=str,
        choices=["full", "correlation", "extraction"],
        default="full",
        help="Evaluation pipeline stage filter",
    )
    parser.add_argument(
        "--modality",
        type=str,
        default=None,
        help="Filter evaluation to specific modality (e.g. 'HANDWRITTEN_CURSIVE', 'MULTI_CARD', 'STANDALONE')",
    )
    parser.add_argument(
        "--no-judge",
        action="store_true",
        help="Disable LLM-as-a-Judge qualitative evaluation pass",
    )
    parser.add_argument(
        "--mock-judge",
        action="store_true",
        default=False,
        help="Use deterministic offline mock for judge evaluation (for plumbing tests only, labeled 'mock (not a quality score)')",
    )
    parser.add_argument(
        "--live-judge",
        action="store_true",
        default=False,
        help="Enable live Gemini API judge evaluation (requires live API key)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=str,
        default="evals/reports",
        help="Directory to store output Markdown and JSON benchmark reports",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=["all", "markdown", "json", "stdout"],
        default="all",
        help="Output report format",
    )
    parser.add_argument(
        "--no-obfuscate",
        action="store_true",
        help="Disable filename and metadata obfuscation during evaluation",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose debug logging",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint for running benchmarks."""
    parser = build_cli_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    runner = BenchmarkRunner(
        manifest_path=args.manifest,
        corpus_dir=args.corpus_dir,
        model_name=args.model,
        stage=args.stage,
        modality=args.modality,
        judge_enabled=not args.no_judge if (args.live_judge or args.mock_judge) else False,
        mock_judge=args.mock_judge,
        live_judge=args.live_judge,
        obfuscate_filenames=not args.no_obfuscate,
    )


    try:
        report = runner.run_sync()

        if args.format in ("all", "stdout", "markdown"):
            print("\n" + report.to_markdown() + "\n")

        if args.format in ("all", "markdown", "json"):
            json_p, md_p = report.save(args.output_dir)
            print(f"\n[INFO] Benchmark reports successfully saved:")
            print(f"  - Markdown: {md_p}")
            print(f"  - JSON:     {json_p}\n")

        return 0
    except Exception as exc:
        logger.error(f"Benchmark execution failed: {exc}", exc_info=args.verbose)
        print(f"\n[ERROR] Benchmark execution failed: {exc}\n", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
