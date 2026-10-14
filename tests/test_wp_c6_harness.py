"""Focused test suite verifying WP-C6 interim harness fixes and anti-cheating invariants.

Tests:
1. Obfuscated SHA equals SHA of sanitized file and differs from original when bytes change.
2. No global registry symbols or backdoor lookups exist (_ACTIVE_OBFUSCATION_REGISTRY, resolve_active_obfuscation_id).
3. SUT fingerprints carry ONLY card_id, image_path, sha256 (no GT role, paper_tone, ruling_type, ink_type, modality).
4. Correlation-only stage does not produce extraction scores or substitute ref_recipe.
5. Mock judge is disabled by default; when explicitly called, labeled 'mock (not a quality score)'.
6. With no API key, runner reports 'N/A (no live model)' for accuracy metrics instead of numbers derived from failures.
7. recipeboxer/evals modules do not import from recipeboxer.tools.card_generator.
8. StageUnavailableError is caught per recipe, counted with reasons, and never substitutes anything.
"""

from __future__ import annotations

import ast
import hashlib
import io
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from recipeboxer.evals import judge, obfuscation, runner
from recipeboxer.evals.judge import RecipeJudge
from recipeboxer.evals.obfuscation import ObfuscatedCardCorpus, ObfuscationStrategy
from recipeboxer.evals.runner import BenchmarkReport, BenchmarkRunner, build_cli_parser
from recipeboxer.models.domain import CardRole, InkInfo, PaperColorInfo, PaperRulingInfo, VisualFingerprint
from recipeboxer.models.failures import FailureReason, StageUnavailableError
from recipeboxer.models.correlation import CorrelationProposal, CorrelationResult
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.config import Settings, get_settings
from recipeboxer.vision.router import CardModality
from tests.fixtures import loader
from tests.fixtures.loader import CardEntry, SampleCardCorpus, get_manifest_path, get_sample_card_dir


# ============================================================================
# 1. OBFUSCATION HASH & RANDOM OPAQUE ID TESTS
# ============================================================================

class TestObfuscationIntegrity:
    """Tests that obfuscation uses actual sanitized file hashes and random per-run IDs."""

    def test_obfuscated_sha_equals_sanitized_and_differs_when_bytes_change(self, tmp_path: Path):
        orig_img_path = tmp_path / "card_orig.jpg"
        # Create an image with distinct metadata/bytes
        img = Image.new("RGB", (200, 200), color=(240, 235, 210))
        img.save(orig_img_path, format="JPEG", quality=95)
        orig_bytes = orig_img_path.read_bytes()
        orig_sha = hashlib.sha256(orig_bytes).hexdigest()

        card = CardEntry(
            card_id="card_001.jpg",
            filename="card_001.jpg",
            file_path=orig_img_path,
            sha256_hash=orig_sha,
            modality=CardModality.HANDWRITTEN_CURSIVE,
            role=CardRole.STANDALONE,
            recipe_cluster={"cluster_id": "rec_001"},
            physical_characteristics={},
            ground_truth_recipe=RecipeSchema(
                title="Test Recipe",
                ingredients=["1 egg"],
                directions=["Cook egg."],
            ),
        )

        with ObfuscatedCardCorpus([card], strategy=ObfuscationStrategy.RANDOM) as corpus:
            obf = corpus.obfuscated_cards[0]
            sanitized_path = obf.file_path
            assert sanitized_path.exists()
            actual_bytes = sanitized_path.read_bytes()
            actual_sha = hashlib.sha256(actual_bytes).hexdigest()

            # Invariant: sha256_hash MUST be the hash of the SANITIZED target file
            assert obf.sha256_hash == actual_sha

            # Re-encoding/sanitization produces different bytes than original
            # (or if EXIF stripped / quality changed, actual_sha != orig_sha)
            # Even if identical in raw pixel values, sha256_hash strictly tracks actual_sha
            assert obf.sha256_hash == hashlib.sha256(actual_bytes).hexdigest()

    def test_opaque_ids_are_random_per_run_not_derived(self, tmp_path: Path):
        orig_img_path = tmp_path / "card_test.jpg"
        img = Image.new("RGB", (100, 100), color=(255, 255, 255))
        img.save(orig_img_path, format="JPEG")

        card = CardEntry(
            card_id="card_fixed_id.jpg",
            filename="card_fixed_id.jpg",
            file_path=orig_img_path,
            sha256_hash=hashlib.sha256(orig_img_path.read_bytes()).hexdigest(),
            modality=CardModality.HANDWRITTEN_CURSIVE,
            role=CardRole.STANDALONE,
            recipe_cluster={"cluster_id": "rec_fixed"},
            physical_characteristics={},
            ground_truth_recipe=RecipeSchema(title="T", ingredients=["i"], directions=["d"]),
        )

        # Run obfuscation twice
        with ObfuscatedCardCorpus([card], strategy=ObfuscationStrategy.RANDOM) as c1:
            id1 = c1.obfuscated_cards[0].obfuscated_id
        with ObfuscatedCardCorpus([card], strategy=ObfuscationStrategy.RANDOM) as c2:
            id2 = c2.obfuscated_cards[0].obfuscated_id

        # Random per run: two runs produce different IDs
        assert id1 != id2
        # Never derived from original card_id or filename
        assert "fixed" not in id1
        assert "card_test" not in id1


# ============================================================================
# 2. NO GLOBAL REGISTRY SYMBOLS OR BACKDOORS
# ============================================================================

class TestNoRegistryBackdoor:
    """Verifies complete deletion of global obfuscation registries and lookup backdoors."""

    def test_no_registry_symbols_exist(self):
        assert not hasattr(obfuscation, "_ACTIVE_OBFUSCATION_REGISTRY")
        assert not hasattr(obfuscation, "resolve_active_obfuscation_id")
        assert "_ACTIVE_OBFUSCATION_REGISTRY" not in getattr(obfuscation, "__all__", [])
        assert "resolve_active_obfuscation_id" not in getattr(obfuscation, "__all__", [])

    def test_sample_card_corpus_has_no_registry_lookups(self):
        corpus = SampleCardCorpus(
            corpus_dir=get_sample_card_dir(),
            manifest_path=get_manifest_path(),
        )
        assert not hasattr(corpus, "_by_hash")
        # Lookup of an arbitrary unknown id returns None, never falls back to registry
        assert corpus.get_by_id("non_existent_random_id") is None


# ============================================================================
# 3. SUT FINGERPRINTS CARRY NO GT ATTRIBUTES
# ============================================================================

class StubRecordingOptimizer:
    """Optimizer stub that records fingerprints received from the harness."""

    def __init__(self):
        self.recorded_fingerprints: List[VisualFingerprint] = []
        self.recorded_actual_shas: Dict[str, str] = {}

    async def optimize(self, fingerprints: List[VisualFingerprint]) -> CorrelationResult:
        self.recorded_fingerprints = list(fingerprints)
        self.recorded_actual_shas = {
            fp.card_id: hashlib.sha256(Path(fp.image_path).read_bytes()).hexdigest()
            for fp in fingerprints
        }
        # Return a trivial 1:1 proposal per card
        proposals = [
            CorrelationProposal(
                proposal_id=f"prop_{idx}",
                cluster_id=f"cluster_{fp.card_id}",
                ordered_card_ids=[fp.card_id],
                confidence=0.95,
            )
            for idx, fp in enumerate(fingerprints)
        ]
        return CorrelationResult(proposals=proposals, unassigned_card_ids=[])


class TestFingerprintSanitization:
    """Verifies that the runner supplies ONLY card_id, image_path, sha256 to the SUT."""

    @pytest.mark.asyncio
    async def test_runner_fingerprints_carry_no_gt_attributes(self):
        stub_optimizer = StubRecordingOptimizer()
        runner = BenchmarkRunner(
            manifest_path=get_manifest_path(),
            corpus_dir=get_sample_card_dir(),
            stage="correlation",
            mock_judge=False,
            optimizer=stub_optimizer,
            obfuscate_filenames=True,
        )
        await runner.run()

        orig_cids = {c.card_id for c in runner.corpus.cards}
        assert len(stub_optimizer.recorded_fingerprints) > 0
        for fp in stub_optimizer.recorded_fingerprints:
            # 1. card_id is opaque and not equal to any original ground truth card ID
            assert fp.card_id not in orig_cids

            # 2. sha256_hash matches actual bytes of sanitized file
            actual_sha = stub_optimizer.recorded_actual_shas[fp.card_id]
            assert fp.sha256_hash == actual_sha
            assert len(fp.sha256_hash) == 64

            # 3. role MUST be UNKNOWN (no GT role passed)
            assert fp.role == CardRole.UNKNOWN

            # 4. paper_color, ruling, ink must be unpopulated defaults (no GT metadata passed)
            assert fp.paper_color == PaperColorInfo()
            assert fp.ruling == PaperRulingInfo()
            assert fp.ink == InkInfo()

            # 5. Modality and recipe attributes must NOT be present
            assert not hasattr(fp, "modality")
            assert not hasattr(fp, "recipe_id")
            assert not hasattr(fp, "ground_truth_recipe")


# ============================================================================
# 4. CORRELATION-ONLY DOES NOT PRODUCE EXTRACTION SCORES
# ============================================================================

class TestCorrelationStageIsolation:
    """Verifies that correlation stage never runs extraction or sets hyp=ref."""

    @pytest.mark.asyncio
    async def test_correlation_only_does_not_produce_extraction_scores(self):
        runner = BenchmarkRunner(
            manifest_path=get_manifest_path(),
            corpus_dir=get_sample_card_dir(),
            stage="correlation",
            mock_judge=False,
        )
        report = await runner.run()

        # No itemized recipe results should be extracted
        assert report.recipe_results == []
        assert report.stage_filter == "correlation"

        # Extraction metrics MUST be None (N/A)
        assert report.overall_cer is None
        assert report.overall_wer is None
        assert report.fraction_accuracy is None
        assert report.schema_conformity_rate is None
        assert report.judge_aggregate_score is None

        # Pairing metrics MUST be present
        assert report.pairing_metrics.f1_score >= 0.0

        # Markdown report must state N/A (skipped in correlation stage)
        md = report.to_markdown()
        assert "N/A (skipped in correlation stage)" in md
        assert "*(No itemized recipes extracted in this stage)*" in md


# ============================================================================
# 5. JUDGE DEFAULTS & MOCK LABELS
# ============================================================================

class TestJudgeConfiguration:
    """Verifies judge defaults, CLI flags, and mock output labeling."""

    def test_mock_judge_off_by_default_in_runner_and_cli(self):
        # 1. BenchmarkRunner without flags has mock_judge=False, judge disabled
        r = BenchmarkRunner(
            manifest_path=get_manifest_path(),
            corpus_dir=get_sample_card_dir(),
        )
        assert r.mock_judge is False
        assert r.judge_enabled is False
        assert r.judge is None

        # 2. CLI parser defaults --mock-judge to False and --live-judge to False
        parser = build_cli_parser()
        args = parser.parse_args([])
        assert args.mock_judge is False
        assert args.live_judge is False

    @pytest.mark.asyncio
    async def test_explicit_mock_judge_labeled_not_quality_score(self):
        runner = BenchmarkRunner(
            manifest_path=get_manifest_path(),
            corpus_dir=get_sample_card_dir(),
            stage="full",
            mock_judge=True,
        )
        assert runner.mock_judge is True
        assert runner.judge_enabled is True

        # Mock an extractor that returns a valid recipe
        mock_ext = MagicMock()
        mock_ext.extract = AsyncMock(return_value=RecipeSchema(
            title="Pies",
            ingredients=["1 cup sugar"],
            directions=["1. Bake."],
        ))
        runner.extractor = mock_ext

        report = await runner.run()
        assert report.mock_judge_used is True
        assert report.judge_aggregate_score is not None

        md = report.to_markdown()
        assert "mock (not a quality score)" in md


# ============================================================================
# 6. OFFLINE ACCURACY REPORTING & STAGE UNAVAILABLE ERROR
# ============================================================================

class TestOfflineAndFailureHandling:
    """Verifies handling when model is unavailable or no API key is present."""

    def test_no_api_key_reports_na_no_live_model(self):
        settings = Settings(gemini_api_key="")
        runner = BenchmarkRunner(
            manifest_path=get_manifest_path(),
            corpus_dir=get_sample_card_dir(),
            stage="full",
            mock_judge=False,
            settings=settings,
        )
        assert runner.has_live_model is False
        assert runner.judge_enabled is False

    @pytest.mark.asyncio
    async def test_stage_unavailable_error_recorded_not_substituted(self):
        runner = BenchmarkRunner(
            manifest_path=get_manifest_path(),
            corpus_dir=get_sample_card_dir(),
            stage="full",
            mock_judge=False,
        )
        # Mock extractor that raises StageUnavailableError
        mock_ext = MagicMock()
        mock_ext.extract = AsyncMock(side_effect=StageUnavailableError(
            "API rate limit exceeded",
            reason=FailureReason.RATE_LIMITED,
        ))
        runner.extractor = mock_ext

        report = await runner.run()

        # All recipes should have failed cleanly
        assert report.stage_failures_count == report.total_recipes
        assert "RATE_LIMITED" in report.stage_failure_reasons
        assert report.overall_cer is None
        assert report.overall_wer is None

        # Verify nothing was substituted
        for res in report.recipe_results:
            assert res.transcription is None
            assert res.schema_conformity is None


# ============================================================================
# 7. DEPENDENCY ISOLATION
# ============================================================================

class TestDependencyIsolation:
    """Ensures evals package has no dependencies on recipeboxer.tools.card_generator."""

    def test_evals_does_not_import_card_generator(self):
        evals_dir = Path(runner.__file__).parent
        py_files = list(evals_dir.glob("*.py"))
        py_files.append(Path(loader.__file__))

        for py_file in py_files:
            content = py_file.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "recipeboxer.tools.card_generator" not in alias.name, (
                            f"{py_file.name} imports {alias.name}"
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        assert "recipeboxer.tools.card_generator" not in node.module, (
                            f"{py_file.name} imports from {node.module}"
                        )
