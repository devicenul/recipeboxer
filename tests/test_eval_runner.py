"""Comprehensive unit and integration tests for RecipeBoxer LLM-as-a-Judge and Benchmark Runner.

Tests:
1. JudgeEvaluationResult schema validation and scoring boundaries.
2. RecipeJudge deterministic offline mock mode (rubric scoring, cross-consistency, step logic).
3. RecipeJudge live API retry handling and rate limit resilience.
4. BenchmarkRunner execution against golden dataset manifest across all 6 modalities.
5. BenchmarkReport Markdown and JSON serialization.
6. CLI argument parsing and execution entrypoints.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from recipeboxer.evals.judge import (
    JudgeEvaluationResult,
    RecipeJudge,
    _evaluate_deterministic_mock,
)
from recipeboxer.evals.metrics import PairingMetricResult, TranscriptionMetricResult
from recipeboxer.evals.runner import (
    HISTORICAL_MODALITIES,
    BenchmarkReport,
    BenchmarkRunner,
    ModalityReport,
    RecipeBenchmarkResult,
    build_cli_parser,
    get_card_historical_categories,
    main,
)
from recipeboxer.models.domain import CardRole
from recipeboxer.models.recipe import RecipeSchema
from cardforge import CardModality
from tests.fixtures.loader import CardEntry, SampleCardCorpus, get_manifest_path, get_sample_card_dir


# ============================================================================
# 1. JUDGE EVALUATION RESULT & MOCK JUDGE TESTS
# ============================================================================

class TestJudgeEvaluationResult:
    """Tests for JudgeEvaluationResult schema and validation."""

    def test_valid_judge_result_creation(self):
        res = JudgeEvaluationResult(
            recipe_id="rec_001",
            semantic_fidelity=5,
            vintage_headnote_preservation=4,
            ingredient_direction_cross_consistency=5,
            step_ordering_logic=4,
            aggregate_score=4.5,
            passed=True,
            threshold=3.5,
            dimension_scores={
                "semantic_fidelity": 5,
                "vintage_headnote_preservation": 4,
                "ingredient_direction_cross_consistency": 5,
                "step_ordering_logic": 4,
            },
            itemized_reasoning={"semantic_fidelity": "Excellent fidelity"},
            overall_feedback="High quality transcription",
        )
        assert res.passed is True
        assert res.aggregate_score == 4.5
        assert res.semantic_fidelity == 5
        assert res.dimension_scores["vintage_headnote_preservation"] == 4

    def test_judge_result_score_bounds_validation(self):
        # Value < 1 should raise ValueError
        with pytest.raises(Exception):
            JudgeEvaluationResult(
                recipe_id="rec_bad",
                semantic_fidelity=0,  # invalid: ge=1
            )

        # Value > 5 should raise ValueError
        with pytest.raises(Exception):
            JudgeEvaluationResult(
                recipe_id="rec_bad",
                semantic_fidelity=6,  # invalid: le=5
            )

    def test_judge_result_auto_sync_dimensions_and_aggregate_score(self):
        # When dimension_scores and aggregate_score are omitted, they are auto-calculated and synced
        res_fail = JudgeEvaluationResult(
            recipe_id="rec_low",
            semantic_fidelity=1,
            vintage_headnote_preservation=1,
            ingredient_direction_cross_consistency=2,
            step_ordering_logic=2,
            threshold=3.5,
        )
        assert res_fail.aggregate_score == 1.5
        assert res_fail.passed is False
        assert res_fail.dimension_scores == {
            "semantic_fidelity": 1,
            "vintage_headnote_preservation": 1,
            "ingredient_direction_cross_consistency": 2,
            "step_ordering_logic": 2,
        }

        res_pass = JudgeEvaluationResult(
            recipe_id="rec_high",
            semantic_fidelity=5,
            vintage_headnote_preservation=4,
            ingredient_direction_cross_consistency=5,
            step_ordering_logic=4,
            threshold=3.5,
        )
        assert res_pass.aggregate_score == 4.5
        assert res_pass.passed is True


class TestRecipeJudgeDeterministicMock:
    """Tests for RecipeJudge deterministic offline mock engine."""

    @pytest.fixture
    def sample_recipes(self) -> tuple[RecipeSchema, RecipeSchema]:
        ref = RecipeSchema(
            recipe_id="rec_apple_crisp",
            title="Grandma's Apple Crisp",
            description="Handwritten 1948 family recipe.",
            ingredients=[
                "5 medium baking apples",
                "1/2 cup sugar",
                "3/4 cup flour",
                "1/3 cup butter",
                "1/4 tsp salt",
            ],
            directions=[
                "1. Preheat oven to 375°F.",
                "2. Toss sliced apples with sugar.",
                "3. Mix flour, butter, and salt until crumbly.",
                "4. Bake 35 minutes until golden.",
            ],
            source_notes="1948 Card",
        )
        cand = ref.model_copy(deep=True)
        return ref, cand

    def test_mock_judge_high_fidelity(self, sample_recipes: tuple[RecipeSchema, RecipeSchema]):
        ref, cand = sample_recipes
        judge = RecipeJudge(mock=True, threshold=3.5)
        res = judge.evaluate_sync(candidate_recipe=cand, reference_recipe=ref)

        assert res.passed is True
        assert res.aggregate_score >= 4.0
        assert res.semantic_fidelity >= 4
        assert res.vintage_headnote_preservation >= 4
        assert res.ingredient_direction_cross_consistency >= 4
        assert res.step_ordering_logic == 5
        assert "semantic_fidelity" in res.itemized_reasoning

    def test_mock_judge_catches_missing_ingredients_in_directions(self, sample_recipes: tuple[RecipeSchema, RecipeSchema]):
        ref, cand = sample_recipes
        # Add unreferenced ingredients
        cand.ingredients = [
            "5 medium baking apples",
            "1/2 cup sugar",
            "3/4 cup flour",
            "1/3 cup butter",
            "1/4 tsp salt",
            "2 cups blueberries",
            "1 tbsp vanilla",
            "1 cup pecans",
        ]
        judge = RecipeJudge(mock=True)
        res = judge.evaluate_sync(candidate_recipe=cand, reference_recipe=ref)
        assert res.ingredient_direction_cross_consistency < 5

    def test_mock_judge_catches_missing_headnotes(self, sample_recipes: tuple[RecipeSchema, RecipeSchema]):
        ref, cand = sample_recipes
        cand.description = ""
        cand.source_notes = ""
        judge = RecipeJudge(mock=True)
        res = judge.evaluate_sync(candidate_recipe=cand, reference_recipe=ref)
        assert res.vintage_headnote_preservation < 5

    def test_mock_judge_step_numbering_prefixes(self, sample_recipes: tuple[RecipeSchema, RecipeSchema]):
        ref, _ = sample_recipes
        cand = RecipeSchema.model_construct(
            recipe_id="rec_step_prefixes",
            title="Grandma's Apple Crisp",
            ingredients=["5 apples", "1/2 cup sugar", "3/4 cup flour", "1/3 cup butter", "1/4 tsp salt"],
            directions=["(1) Preheat oven to 375°F.", "(2) Toss apples.", "(3) Mix flour and butter.", "(4) Bake 35 mins."],
            source_notes="1948 Card",
        )
        judge = RecipeJudge(mock=True)
        res = judge.evaluate_sync(candidate_recipe=cand, reference_recipe=ref)
        assert res.step_ordering_logic == 5

    def test_mock_judge_bracketed_and_prefixed_steps(self, sample_recipes: tuple[RecipeSchema, RecipeSchema]):
        ref, _ = sample_recipes
        cand = RecipeSchema.model_construct(
            recipe_id="rec_bracket_steps",
            title="Grandma's Apple Crisp",
            ingredients=["5 apples", "1/2 cup sugar", "3/4 cup flour", "1/3 cup butter", "1/4 tsp salt"],
            directions=["[1] Preheat oven.", "[2] Toss apples.", "[3] Mix topping.", "[4] Bake."],
            source_notes="1948 Card",
        )
        judge = RecipeJudge(mock=True)
        res = judge.evaluate_sync(candidate_recipe=cand, reference_recipe=ref)
        assert res.step_ordering_logic == 5

    def test_mock_judge_singular_plural_morphology(self, sample_recipes: tuple[RecipeSchema, RecipeSchema]):
        ref, _ = sample_recipes
        # Candidate has plural ingredients and singular direction nouns (e.g. eggs -> egg, berries -> berry)
        cand = RecipeSchema.model_construct(
            recipe_id="rec_morph",
            title="Grandma's Apple Crisp",
            ingredients=["2 large eggs", "1 cup blueberries", "1/2 cup sugar"],
            directions=["1. Beat egg in small bowl.", "2. Toss blueberry with sugar.", "3. Bake 20 mins."],
            source_notes="1948 Card",
        )
        judge = RecipeJudge(mock=True)
        res = judge.evaluate_sync(candidate_recipe=cand, reference_recipe=ref)
        assert res.ingredient_direction_cross_consistency == 5

    @pytest.mark.asyncio
    async def test_judge_async_and_batch_evaluation(self, sample_recipes: tuple[RecipeSchema, RecipeSchema]):
        ref, cand = sample_recipes
        judge = RecipeJudge(mock=True)
        # Single async
        res = await judge.evaluate(candidate_recipe=cand, reference_recipe=ref)
        assert res.passed is True

        # Batch async
        batch_items = [(cand, ref), (cand, None)]
        batch_results = await judge.evaluate_batch(batch_items, concurrency=2)
        assert len(batch_results) == 2
        assert all(isinstance(r, JudgeEvaluationResult) for r in batch_results)


class TestRecipeJudgeLiveApiAndRetries:
    """Tests for live Gemini API invocation, error handling, and retry loops."""

    @pytest.mark.asyncio
    async def test_judge_fallback_to_mock_on_network_failure(self):
        judge = RecipeJudge(api_key="valid_looking_key", mock=False)
        cand = RecipeSchema(
            recipe_id="rec_test",
            title="Test Recipe",
            ingredients=["1 cup sugar"],
            directions=["1. Stir."],
        )
        # With invalid API endpoint or network failure, it should gracefully fall back to mock
        with patch("httpx.AsyncClient.post", side_effect=Exception("Connection refused")):
            res = await judge.evaluate(candidate_recipe=cand)
            assert isinstance(res, JudgeEvaluationResult)
            assert res.recipe_id == "rec_test"


# ============================================================================
# 2. BENCHMARK RUNNER TESTS
# ============================================================================

class TestBenchmarkRunner:
    """Tests for BenchmarkRunner execution across modalities and stage filters."""

    @pytest.fixture
    def runner(self) -> BenchmarkRunner:
        return BenchmarkRunner(
            manifest_path=get_manifest_path(),
            corpus_dir=get_sample_card_dir(),
            mock_judge=True,
            stage="full",
        )

    def test_runner_initialization_and_corpus_loading(self, runner: BenchmarkRunner):
        assert len(runner.corpus) >= 20
        assert runner.stage == "full"
        assert runner.mock_judge is True

    def test_filter_cards_by_modality(self, runner: BenchmarkRunner):
        # Filter by cursive
        runner.modality_filter = "HANDWRITTEN_CURSIVE"
        cursive_cards = runner.filter_cards()
        assert len(cursive_cards) > 0
        assert all(c.modality == CardModality.HANDWRITTEN_CURSIVE for c in cursive_cards)

        # Filter by multi-card
        runner.modality_filter = "MULTI_CARD"
        multi_cards = runner.filter_cards()
        assert len(multi_cards) > 0
        assert all(c.recipe_cluster.get("total_cards", 1) >= 3 for c in multi_cards)

        # Test filtering across all six canonical historical modalities
        for mod_name in HISTORICAL_MODALITIES:
            runner.modality_filter = mod_name
            matched = runner.filter_cards()
            assert len(matched) > 0, f"Expected cards for modality '{mod_name}'"

    @pytest.mark.asyncio
    async def test_run_benchmark_correlation_stage(self):
        runner = BenchmarkRunner(
            manifest_path=get_manifest_path(),
            corpus_dir=get_sample_card_dir(),
            stage="correlation",
            mock_judge=True,
        )
        report = await runner.run()
        assert isinstance(report, BenchmarkReport)
        assert report.total_cards >= 20
        assert report.total_recipes >= 10
        assert report.pairing_metrics.f1_score >= 0.80
        assert report.stage_filter == "correlation"

    @pytest.mark.asyncio
    async def test_run_benchmark_full_pipeline_all_modalities(self):
        runner = BenchmarkRunner(
            manifest_path=get_manifest_path(),
            corpus_dir=get_sample_card_dir(),
            stage="full",
            mock_judge=True,
        )

        async def mock_extract(front_path, back_path=None):
            card = runner.corpus.get_by_id(Path(str(front_path)).name)
            if card:
                return card.ground_truth_recipe
            return RecipeSchema(title="Mock Recipe", ingredients=["1 cup item"], directions=["1. Mix."])

        mock_extractor = MagicMock()
        mock_extractor.extract = AsyncMock(side_effect=mock_extract)
        runner.extractor = mock_extractor

        report = await runner.run()
        assert isinstance(report, BenchmarkReport)
        assert report.total_cards >= 20
        assert report.total_recipes >= 10

        # Verify pairing metrics
        assert report.pairing_metrics.f1_score >= 0.80
        assert report.pairing_metrics.precision >= 0.80
        assert report.pairing_metrics.recall >= 0.80

        # Verify transcription error rates
        assert report.overall_cer <= 0.15
        assert report.overall_wer <= 0.25
        assert report.fraction_accuracy >= 0.90

        # Verify schema conformity
        assert report.schema_conformity_rate >= 0.90

        # Verify judge scores
        assert report.judge_aggregate_score >= 3.5
        assert len(report.judge_dimension_scores) == 4

        # Verify all 6 historical modalities are present in breakdown
        for mod_name in HISTORICAL_MODALITIES:
            assert mod_name in report.modality_breakdown
            mod_rep = report.modality_breakdown[mod_name]
            assert mod_rep.card_count > 0
            assert mod_rep.recipe_count > 0
            assert mod_rep.schema_conformity_rate >= 0.80
            assert mod_rep.pairing_f1 >= 0.80
            assert mod_rep.pairing_precision >= 0.80
            assert mod_rep.pairing_recall >= 0.70

    @pytest.mark.asyncio
    async def test_run_benchmark_default_offline_extractor(self):
        # Runs with default offline heuristic extractor
        runner = BenchmarkRunner(
            manifest_path=get_manifest_path(),
            corpus_dir=get_sample_card_dir(),
            stage="full",
            mock_judge=True,
        )
        report = await runner.run()
        assert isinstance(report, BenchmarkReport)
        assert report.total_cards >= 20
        assert report.schema_conformity_rate >= 0.80
        assert report.judge_aggregate_score >= 3.0


# ============================================================================
# 3. BENCHMARK REPORT SERIALIZATION TESTS
# ============================================================================

class TestBenchmarkReportSerialization:
    """Tests for Markdown and JSON benchmark report generation and export."""

    @pytest.fixture
    def sample_report(self) -> BenchmarkReport:
        pairing = PairingMetricResult(
            precision=0.95,
            recall=0.90,
            f1_score=0.924,
            true_positives=18,
            false_positives=1,
            false_negatives=2,
            total_predicted=19,
            total_ground_truth=20,
        )
        return BenchmarkReport(
            timestamp="2026-08-25T12:00:00Z",
            dataset_name="Curated Test Dataset",
            model_name="gemini-3.7-flash",
            stage_filter="full",
            total_cards=23,
            total_recipes=12,
            pairing_metrics=pairing,
            overall_cer=0.032,
            overall_wer=0.051,
            fraction_accuracy=0.98,
            schema_conformity_rate=1.0,
            judge_aggregate_score=4.65,
            judge_pass_rate=1.0,
            judge_dimension_scores={
                "semantic_fidelity": 4.8,
                "vintage_headnote_preservation": 4.5,
                "ingredient_direction_cross_consistency": 4.7,
                "step_ordering_logic": 4.6,
            },
            modality_breakdown={
                "Vintage Handwritten Cursive": ModalityReport(
                    modality_name="Vintage Handwritten Cursive",
                    card_count=4,
                    recipe_count=2,
                    pairing_f1=1.0,
                    overall_cer=0.04,
                    overall_wer=0.06,
                    fraction_accuracy=1.0,
                    schema_conformity_rate=1.0,
                    judge_aggregate_score=4.6,
                    judge_pass_rate=1.0,
                )
            },
            total_duration_seconds=5.42,
        )

    def test_json_serialization(self, sample_report: BenchmarkReport):
        json_str = sample_report.to_json(indent=2)
        parsed = json.loads(json_str)
        assert parsed["model_name"] == "gemini-3.7-flash"
        assert parsed["pairing_metrics"]["f1_score"] == 0.924
        assert parsed["overall_cer"] == 0.032
        assert "Vintage Handwritten Cursive" in parsed["modality_breakdown"]

    def test_markdown_generation(self, sample_report: BenchmarkReport):
        md = sample_report.to_markdown()
        assert "# RecipeBoxer Benchmark Evaluation Report" in md
        assert "Executive Summary" in md
        assert "Modality Breakdown" in md
        assert "LLM-as-a-Judge Qualitative Rubric Dimensions" in md
        assert "Execution Telemetry" in md
        assert "92.4%" in md  # Pairing F1 formatted
        assert "3.20%" in md  # CER formatted

    def test_save_report_to_disk(self, sample_report: BenchmarkReport, tmp_path: Path):
        json_p, md_p = sample_report.save(tmp_path)
        assert json_p.exists() and json_p.is_file()
        assert md_p.exists() and md_p.is_file()
        assert json_p.stat().st_size > 0
        assert md_p.stat().st_size > 0


# ============================================================================
# 4. CLI INTERFACE TESTS
# ============================================================================

class TestCLIInterface:
    """Tests for CLI arguments, parser, and execution."""

    def test_cli_parser_defaults(self):
        parser = build_cli_parser()
        args = parser.parse_args([])
        assert args.stage == "full"
        assert args.model == "gemini-3.7-flash"
        assert args.no_judge is False
        assert args.mock_judge is True
        assert args.live_judge is False
        assert args.format == "all"

    def test_cli_parser_custom_args(self):
        parser = build_cli_parser()
        args = parser.parse_args([
            "--stage", "correlation",
            "--model", "gemini-3.1-pro",
            "--modality", "HANDWRITTEN_CURSIVE",
            "--mock-judge",
            "-f", "json",
            "-o", "/tmp/reports",
        ])
        assert args.stage == "correlation"
        assert args.model == "gemini-3.1-pro"
        assert args.modality == "HANDWRITTEN_CURSIVE"
        assert args.mock_judge is True
        assert args.format == "json"
        assert args.output_dir == "/tmp/reports"

    def test_cli_main_invocation(self, tmp_path: Path):
        # Run main with stage=correlation, format=json, output-dir=tmp_path
        exit_code = main([
            "--stage", "correlation",
            "--mock-judge",
            "-f", "json",
            "-o", str(tmp_path),
        ])
        assert exit_code == 0
        generated_files = list(tmp_path.glob("*.json"))
        assert len(generated_files) == 1

    def test_run_benchmarks_entrypoint_script_execution(self, tmp_path: Path):
        import subprocess
        result = subprocess.run(
            [sys.executable, "evals/run_benchmarks.py", "--stage", "correlation", "-f", "json", "-o", str(tmp_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        generated = list(tmp_path.glob("*.json"))
        assert len(generated) >= 1

    def test_cli_invalid_modality_returns_error(self):
        exit_code = main(["--modality", "NON_EXISTENT_MODALITY_123"])
        assert exit_code == 1
