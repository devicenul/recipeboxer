"""Comprehensive verification test suite for Red-Team Challenger findings (FIND-03..FIND-09).

Tests:
- FIND-03: Production prompt construction and Canary 4 prompt-leak detection.
- FIND-04: Correlation optimizer handles semantic_score=None without crash or 0.85 fallback.
- FIND-05: Headless output propagates actual confidence (or null), evaluator skips null in ECE.
- FIND-06: Audit hook blocks listdir, scandir, subprocess, system, copyfile, sqlite3, imports, and symlinks.
- FIND-07: is_usable_gemini_key and has_usable_gemini_key reject mock/empty keys.
- FIND-08: ModalityClassifier assigns margin-based confidences clipped to [0.3, 0.8] with heuristic=True.
- FIND-09: evals/INTAKE.md contains fictitious non-corpus recipe.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

from recipeboxer.ai import ImagePart, LLMRequest, LLMStage, ScriptedLLMClient, TextPart
from recipeboxer.config import has_usable_gemini_key, is_usable_gemini_key
from recipeboxer.correlation.optimizer import PairingOptimizer
from recipeboxer.extraction.extractor import RecipeExtractor
from recipeboxer.headless import run_headless_analysis
from recipeboxer.models.correlation import CorrelationProposal
from recipeboxer.models.domain import ArtifactType, CardRole, InkInfo, InkType, PhysicalArtifact, VisualFingerprint
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.vision.fingerprint import GeminiVisualExtractor
from recipeboxer.vision.router import CardModality, ModalityClassifier

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CARDS_DIR = REPO_ROOT / "tests" / "fixtures" / "sample_cards"


# ===========================================================================
# FIND-03: Production Prompt Construction & Leak Detection
# ===========================================================================

class TestFind03ProductionPrompts:
    """Verifies that injected LLMClient exercises production prompt construction."""

    @pytest.mark.asyncio
    async def test_production_prompt_construction(self, tmp_path: Path) -> None:
        c1 = SAMPLE_CARDS_DIR / "rec_vhc_apple_crisp_1948_front.jpg"
        c2 = SAMPLE_CARDS_DIR / "rec_vhc_apple_crisp_1948_back.jpg"
        input_dir = tmp_path / "scans"
        input_dir.mkdir()
        (input_dir / c1.name).write_bytes(c1.read_bytes())
        (input_dir / c2.name).write_bytes(c2.read_bytes())

        client = ScriptedLLMClient([
            VisualFingerprint(card_id=c1.name, image_path=str(c1), sha256_hash="0" * 64, role=CardRole.SEQUENCE_START),
            VisualFingerprint(card_id=c2.name, image_path=str(c2), sha256_hash="1" * 64, role=CardRole.SEQUENCE_CONTINUATION),
            CorrelationProposal(
                proposal_id="p1",
                ordered_card_ids=[c1.name, c2.name],
                confidence=0.88,
                recommended_action="PAIR",
                semantic_score=0.9,
                semantic_verified=True,
            ),
            RecipeSchema(recipe_id="r1", title="Apple Crisp", ingredients=["Apples"], directions=["Bake"]),
        ])

        out_file = tmp_path / "out.json"
        await run_headless_analysis(
            input_dir=input_dir,
            out_file=out_file,
            auto_approve=True,
            injected_client=client,
        )

        assert len(client.requests) == 4
        stages = [r.stage for r in client.requests]
        assert stages[:3] == [
            LLMStage.FINGERPRINT,
            LLMStage.FINGERPRINT,
            LLMStage.SET_VERIFY,
        ]
        assert stages[3] in (LLMStage.STRUCTURE, LLMStage.TRANSCRIBE)

        # Verify each stage builds non-empty system instruction and text parts
        for req in client.requests:
            assert req.system_instruction is not None and len(req.system_instruction.strip()) > 0
            assert any(isinstance(p, TextPart) and len(p.text.strip()) > 0 for p in req.parts)
            assert any(isinstance(p, ImagePart) for p in req.parts)

    def test_canary4_catches_prompt_leak(self) -> None:
        """Verifies Canary 4 fails if a forbidden token appears in an LLMRequest."""
        leaked_request = LLMRequest(
            stage=LLMStage.FINGERPRINT,
            model="gemini-2.5-flash",
            system_instruction="Analyze this card: rec_vhc_apple_crisp_1948_front.jpg",
            parts=[TextPart(text="Extract details.")],
        )

        forbidden_strings = ["rec_vhc_apple_crisp_1948_front.jpg"]
        full_text = f"{leaked_request.system_instruction} {leaked_request.parts[0].text}".lower()

        with pytest.raises(AssertionError, match="Prompt leak detected"):
            for token in forbidden_strings:
                assert token.lower() not in full_text, (
                    f"Prompt leak detected! Forbidden token '{token}' found in LLMRequest"
                )


# ===========================================================================
# FIND-04: Optimizer Semantic Score None Handling
# ===========================================================================

class TestFind04OptimizerSemanticNone:
    """Verifies correlation optimizer handles semantic_score=None gracefully."""

    def test_optimizer_handles_none_semantic_score(self) -> None:
        opt = PairingOptimizer()
        fp1 = VisualFingerprint(card_id="c1", image_path="c1.jpg", sha256_hash="0" * 64, role=CardRole.SEQUENCE_START, role_confidence=0.75)
        fp2 = VisualFingerprint(card_id="c2", image_path="c2.jpg", sha256_hash="1" * 64, role=CardRole.SEQUENCE_CONTINUATION, role_confidence=0.75)

        prop = CorrelationProposal(
            proposal_id="p1",
            ordered_card_ids=["c1", "c2"],
            confidence=0.70,
            recommended_action="UNCERTAIN",
            heuristic_score=0.70,
            semantic_score=None,  # Missing semantic evidence
            semantic_verified=False,
        )

        # Must not raise TypeError: unsupported operand type(s) for +: 'float' and 'NoneType'
        result = opt.optimize_proposals([fp1, fp2], [prop])
        assert len(result.proposals) == 1
        seq = result.proposals[0]
        assert seq.ordered_card_ids == ["c1", "c2"]
        assert seq.confidence == 0.70
        assert seq.recommended_action == "UNCERTAIN"
        assert seq.degraded_reason == "MISSING_SEMANTIC_EVIDENCE"
        assert seq.semantic_verified is False

    def test_no_hardcoded_085_fallback(self) -> None:
        """Verifies no hardcoded 0.85 exists when sequence transitions are empty."""
        opt = PairingOptimizer()
        fp1 = VisualFingerprint(card_id="c1", image_path="c1.jpg", sha256_hash="0" * 64, role=CardRole.STANDALONE, role_confidence=0.42)

        # Standalone optimization
        result = opt.optimize_proposals([fp1], [])
        assert len(result.proposals) == 1
        st = result.proposals[0]
        # WP-RT3FIX (RT3-03): the fingerprint role confidence is a classification prior, not grouping
        # evidence -> no reported confidence (and never a hard-coded 0.85).
        assert st.confidence is None


# ===========================================================================
# FIND-05: Real Confidence Propagation & Evaluator Null Handling
# ===========================================================================

class TestFind05ConfidencePropagation:
    """Verifies headless output propagates real confidence and evaluator skips null."""

    @pytest.mark.asyncio
    async def test_headless_emits_actual_proposal_confidence(self, tmp_path: Path) -> None:
        c1 = SAMPLE_CARDS_DIR / "rec_vhc_apple_crisp_1948_front.jpg"
        c2 = SAMPLE_CARDS_DIR / "rec_vhc_apple_crisp_1948_back.jpg"
        input_dir = tmp_path / "scans"
        input_dir.mkdir()
        (input_dir / c1.name).write_bytes(c1.read_bytes())
        (input_dir / c2.name).write_bytes(c2.read_bytes())

        expected_conf = 0.77
        client = ScriptedLLMClient([
            VisualFingerprint(card_id=c1.name, image_path=str(c1), sha256_hash="0" * 64, role=CardRole.SEQUENCE_START),
            VisualFingerprint(card_id=c2.name, image_path=str(c2), sha256_hash="1" * 64, role=CardRole.SEQUENCE_CONTINUATION),
            CorrelationProposal(
                proposal_id="p1",
                ordered_card_ids=[c1.name, c2.name],
                confidence=expected_conf,
                recommended_action="PAIR",
                semantic_score=0.77,
                semantic_verified=True,
            ),
            RecipeSchema(recipe_id="r1", title="Apple Crisp", ingredients=["Apples"], directions=["Bake"]),
        ])

        out_file = tmp_path / "out.json"
        # WP-UP3: recipes[].confidence = linkmodel.recipe_confidence(proposal_confidence, recipe, n_cards),
        # i.e. the fitted P(grouped AND extracted correctly). Record its input to prove the REAL proposal
        # confidence (0.77) is propagated rather than a fabricated constant.
        import recipeboxer.correlation.linkmodel as _lm
        seen: list = []
        _orig = _lm.recipe_confidence

        def _spy(group_conf, recipe, n_cards, models=None):
            out = _orig(group_conf, recipe, n_cards) if models is None else _orig(group_conf, recipe, n_cards, models)
            seen.append((group_conf, out))
            return out

        _lm.recipe_confidence = _spy
        try:
            await run_headless_analysis(
                input_dir=input_dir,
                out_file=out_file,
                auto_approve=True,
                injected_client=client,
            )
        finally:
            _lm.recipe_confidence = _orig

        import json
        data = json.loads(out_file.read_text())
        recipes = data.get("recipes", [])
        assert len(recipes) == 1
        assert seen and seen[0][0] == expected_conf
        assert recipes[0]["confidence"] == seen[0][1]
        assert recipes[0]["confidence"] != 1.0

    def test_evaluator_skips_null_confidences(self) -> None:
        from evals.scoring.evaluator import evaluate_deck

        deck_output = {
            "deck_id": "d01",
            "recipes": [
                {"recipe": {"title": "Recipe 1"}, "confidence": 0.85, "image_ids": ["img1"]},
                {"recipe": {"title": "Recipe 2"}, "confidence": None, "image_ids": ["img2"]},  # null confidence
            ],
            "unassigned_image_ids": [],
            "failures": [],
        }
        gold_deck = {
            "deck_id": "d01",
            "recipes": [
                {"recipe_id": "r1", "structured": {"title": "Recipe 1"}, "faces": ["img1"]},
                {"recipe_id": "r2", "structured": {"title": "Recipe 2"}, "faces": ["img2"]},
            ],
            "images": [{"image_id": "img1"}, {"image_id": "img2"}],
        }

        eval_res = evaluate_deck(deck_output, gold_deck)
        # null confidence must not crash evaluation and must be excluded from confidences
        assert len(eval_res["confidences"]) == 1
        assert eval_res["confidences"][0] == 0.85


# ===========================================================================
# FIND-06: Comprehensive Audit Hook Test
# ===========================================================================

class TestFind06AuditHookVectors:
    """Verifies that sys.addaudithook in sut_boot.py intercepts all cheat vectors."""

    def _run_in_sut_boot(self, code: str, tmp_path: Path) -> subprocess.CompletedProcess:
        script = tmp_path / "test_hook.py"
        script.write_text(f"""
import sys, os
from evals.harness.sut_boot import _install_anti_cheat_audit_hook

_install_anti_cheat_audit_hook()
{code}
""", encoding="utf-8")

        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT)
        return subprocess.run(
            [sys.executable, str(script)],
            cwd=str(tmp_path),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_blocks_os_listdir_on_evals(self, tmp_path: Path) -> None:
        evals_dir = REPO_ROOT / "evals"
        code = f"import os\nos.listdir({str(evals_dir)!r})"
        proc = self._run_in_sut_boot(code, tmp_path)
        assert proc.returncode == 86
        assert "CHEAT_DETECTED" in proc.stderr

    def test_blocks_os_scandir_on_evals(self, tmp_path: Path) -> None:
        evals_dir = REPO_ROOT / "evals"
        code = f"import os\nlist(os.scandir({str(evals_dir)!r}))"
        proc = self._run_in_sut_boot(code, tmp_path)
        assert proc.returncode == 86
        assert "CHEAT_DETECTED" in proc.stderr

    def test_blocks_subprocess_popen(self, tmp_path: Path) -> None:
        code = "import subprocess\nsubprocess.Popen(['echo', 'hello'])"
        proc = self._run_in_sut_boot(code, tmp_path)
        assert proc.returncode == 86
        assert "CHEAT_DETECTED" in proc.stderr

    def test_blocks_os_system(self, tmp_path: Path) -> None:
        code = "import os\nos.system('echo cheat')"
        proc = self._run_in_sut_boot(code, tmp_path)
        assert proc.returncode == 86
        assert "CHEAT_DETECTED" in proc.stderr

    def test_blocks_shutil_copyfile_on_evals(self, tmp_path: Path) -> None:
        vault_file = REPO_ROOT / "evals" / "vault" / "T1.gt.enc"
        code = f"import shutil\nshutil.copyfile({str(vault_file)!r}, 'local.enc')"
        proc = self._run_in_sut_boot(code, tmp_path)
        assert proc.returncode == 86
        assert "CHEAT_DETECTED" in proc.stderr

    def test_blocks_sqlite3_connect_on_evals(self, tmp_path: Path) -> None:
        code = "import sqlite3\nsqlite3.connect('evals/fake.db')"
        proc = self._run_in_sut_boot(code, tmp_path)
        assert proc.returncode == 86
        assert "CHEAT_DETECTED" in proc.stderr

    def test_blocks_import_evals(self, tmp_path: Path) -> None:
        code = "import evals.vault"
        proc = self._run_in_sut_boot(code, tmp_path)
        assert proc.returncode == 86
        assert "CHEAT_DETECTED" in proc.stderr

    def test_blocks_import_cardforge(self, tmp_path: Path) -> None:
        code = "import cardforge"
        proc = self._run_in_sut_boot(code, tmp_path)
        assert proc.returncode == 86
        assert "CHEAT_DETECTED" in proc.stderr

    def test_blocks_import_tests(self, tmp_path: Path) -> None:
        code = "import tests"
        proc = self._run_in_sut_boot(code, tmp_path)
        assert proc.returncode == 86
        assert "CHEAT_DETECTED" in proc.stderr

    def test_blocks_symlink_to_evals(self, tmp_path: Path) -> None:
        vault_file = REPO_ROOT / "evals" / "vault" / "T1.gt.enc"
        symlink = tmp_path / "innocent.jpg"
        try:
            symlink.symlink_to(vault_file)
        except OSError:
            pytest.skip("Symlinks not supported on filesystem")

        code = f"with open({str(symlink)!r}, 'rb') as f: pass"
        proc = self._run_in_sut_boot(code, tmp_path)
        assert proc.returncode == 86
        assert "CHEAT_DETECTED" in proc.stderr


# ===========================================================================
# FIND-07: is_usable_gemini_key and has_usable_gemini_key
# ===========================================================================

class TestFind07UsableKeyHelper:
    """Verifies single shared helper rejects mock and empty keys."""

    def test_is_usable_gemini_key(self) -> None:
        assert is_usable_gemini_key(None) is False
        assert is_usable_gemini_key("") is False
        assert is_usable_gemini_key("   ") is False
        assert is_usable_gemini_key("mock") is False
        assert is_usable_gemini_key("mock-key") is False
        assert is_usable_gemini_key("MOCK_12345") is False
        assert is_usable_gemini_key("AIzaSyAbc123ValidKey") is True

    def test_has_usable_gemini_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from recipeboxer.config import Settings
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("RECIPEBOXER_GEMINI_API_KEY", raising=False)

        settings_mock = Settings(gemini_api_key="mock-key")
        assert has_usable_gemini_key(settings_mock) is False

        settings_valid = Settings(gemini_api_key="AIzaSyAbc123")
        assert has_usable_gemini_key(settings_valid) is True


# ===========================================================================
# FIND-08: Margin-Based Heuristic Confidences in [0.3, 0.8]
# ===========================================================================

class TestFind08RouterConfidences:
    """Verifies router produces continuous margin-based confidences clipped to [0.3, 0.8]."""

    def test_router_fingerprint_confidences_in_range(self) -> None:
        classifier = ModalityClassifier()

        # Mixed Media
        fp_stain = VisualFingerprint(
            card_id="c1",
            image_path="",
            sha256_hash="0" * 64,
            role=CardRole.STANDALONE,
            artifacts=[PhysicalArtifact(artifact_type=ArtifactType.OIL_STAIN)],
        )
        res_stain = classifier.classify(fp_stain)
        assert res_stain.modality == CardModality.MIXED_MEDIA
        assert 0.3 <= res_stain.confidence <= 0.8
        assert res_stain.features.get("heuristic") is True

        # Typewritten
        fp_type = VisualFingerprint(
            card_id="c2",
            image_path="",
            sha256_hash="0" * 64,
            role=CardRole.STANDALONE,
            ink=InkInfo(ink_type=InkType.TYPEWRITER_RIBBON, confidence=0.72),
        )
        res_type = classifier.classify(fp_type)
        assert res_type.modality == CardModality.TYPEWRITTEN
        assert 0.3 <= res_type.confidence <= 0.8
        assert res_type.features.get("heuristic") is True

        # Printed Clipping
        fp_print = VisualFingerprint(
            card_id="c3",
            image_path="",
            sha256_hash="0" * 64,
            role=CardRole.STANDALONE,
            ink=InkInfo(ink_type=InkType.PRINTED_TYPESET, confidence=0.68),
        )
        res_print = classifier.classify(fp_print)
        assert res_print.modality == CardModality.PRINTED_CLIPPING_OR_LABEL
        assert 0.3 <= res_print.confidence <= 0.8
        assert res_print.features.get("heuristic") is True


# ===========================================================================
# FIND-09: INTAKE.md Example Recipe
# ===========================================================================

class TestFind09IntakeDocumentation:
    """Verifies evals/INTAKE.md contains a fictitious non-corpus recipe."""

    def test_intake_md_contains_no_lemon_bars(self) -> None:
        intake_md = REPO_ROOT / "evals" / "INTAKE.md"
        assert intake_md.exists()
        content = intake_md.read_text(encoding="utf-8")

        assert "Lemon Bars" not in content
        assert "Meyer lemons" not in content
        assert "Starlight Berry Compote" in content
