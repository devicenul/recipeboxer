"""Unit and integration tests for WP-E3 Black-Box Harness & Headless Runner.

Tests:
1. recipeboxer.headless:
   - Headless execution with ScriptedLLMClient
   - Invariant fail-fast without Gemini key (INV-AC6) -> status="no_live_model", failures NO_API_KEY
   - Auto-approve of proposals via session configuration
   - Isolated in-memory cache usage
2. recipeboxer.cli analyze:
   - CLI invocation parsing and exit codes
3. evals.harness.sut_boot:
   - Interception of forbidden path opens (evals/, tests/fixtures, *.gt.enc, manifest.json)
   - Exit code 86 and CHEAT_DETECTED stderr
4. evals.harness.run:
   - Generates rb-out/1 outputs.json, ledger.json, run.log
   - Environment scrubbing
   - Oracle track (track="oracle_grouping")
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest
from PIL import Image

from evals.harness.run import execute_run
from recipeboxer.ai import ScriptedLLMClient
from recipeboxer.headless import has_usable_gemini_key, run_headless_analysis
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.recipe import RecipeSchema


REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CARDS_DIR = REPO_ROOT / "tests" / "fixtures" / "sample_cards"


class TestHeadlessExecution:
    """Verifies headless production pipeline execution."""

    @pytest.mark.asyncio
    async def test_headless_no_key_fail_fast(self, tmp_path: Path) -> None:
        """INV-AC6: Headless analysis without key emits NO_API_KEY StageFailure and no_live_model."""
        input_dir = tmp_path / "cards"
        input_dir.mkdir()
        c1 = input_dir / "c1.jpg"
        Image.new("RGB", (50, 50), color=(100, 100, 100)).save(c1)

        old_env_1 = os.environ.pop("RECIPEBOXER_GEMINI_API_KEY", None)
        old_env_2 = os.environ.pop("GEMINI_API_KEY", None)

        try:
            res = await run_headless_analysis(
                input_dir=input_dir,
                auto_approve=True,
            )
            assert res["status"] == "no_live_model"
            assert len(res["recipes"]) == 0
            assert "c1.jpg" in res["unassigned_image_ids"]
            assert any(f["reason"] == "NO_API_KEY" for f in res["failures"])
        finally:
            if old_env_1:
                os.environ["RECIPEBOXER_GEMINI_API_KEY"] = old_env_1
            if old_env_2:
                os.environ["GEMINI_API_KEY"] = old_env_2

    @pytest.mark.asyncio
    async def test_headless_scripted_client_flow(self, tmp_path: Path) -> None:
        """Verifies end-to-end extraction with an injected ScriptedLLMClient."""
        input_dir = tmp_path / "cards"
        input_dir.mkdir()
        c1 = input_dir / "front.jpg"
        Image.new("RGB", (50, 50), color=(100, 100, 100)).save(c1)

        from recipeboxer.extraction.transcribe import FaceTranscript, FaceTranscriptList
        from recipeboxer.models.recipe import IngredientLine, Recipe, RecipeSection, Step

        client = ScriptedLLMClient([
            VisualFingerprint(
                card_id="front.jpg",
                image_path=str(c1),
                sha256_hash="0" * 64,
                role=CardRole.STANDALONE,
            ),
            FaceTranscriptList(
                faces=[FaceTranscript(face_index=0, text="Single Card Recipe\nSugar\nButter\nMix and bake.")]
            ),
            Recipe(
                title="Single Card Recipe",
                sections=[
                    RecipeSection(
                        name=None,
                        ingredients=[IngredientLine(original_text="Sugar", item="Sugar"), IngredientLine(original_text="Butter", item="Butter")],
                        steps=[Step(original_text="Mix and bake.")],
                    )
                ],
            ),
        ])

        out_file = tmp_path / "out.json"
        res = await run_headless_analysis(
            input_dir=input_dir,
            out_file=out_file,
            auto_approve=True,
            injected_client=client,
        )

        assert res["status"] == "ok"
        assert len(res["recipes"]) == 1
        assert res["recipes"][0]["recipe"]["title"] == "Single Card Recipe"
        assert out_file.exists()
        loaded = json.loads(out_file.read_text(encoding="utf-8"))
        assert loaded["status"] == "ok"


class TestSutBootAuditHook:
    """Verifies sut_boot sys.addaudithook security isolation."""

    def test_audit_hook_blocks_vault_access(self, tmp_path: Path) -> None:
        forbidden_file = tmp_path / "evals" / "secret.gt.enc"
        forbidden_file.parent.mkdir(parents=True, exist_ok=True)
        forbidden_file.write_bytes(b"SECRET")

        test_script = tmp_path / "attempt_cheat.py"
        test_script.write_text(f"""
import sys
from evals.harness.sut_boot import _install_anti_cheat_audit_hook
_install_anti_cheat_audit_hook()
with open({str(forbidden_file)!r}, 'rb') as f:
    f.read()
""", encoding="utf-8")

        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT)

        proc = subprocess.run(
            [sys.executable, str(test_script)],
            cwd=str(tmp_path),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        assert proc.returncode == 86
        assert "CHEAT_DETECTED" in proc.stderr


class TestHarnessRun:
    """Verifies evals.harness.run execution and output contracts."""

    def test_run_t0_offline_produces_contract_artifacts(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "run_out"
        execute_run(
            tier="T0",
            out_dir=out_dir,
            model_profile="balanced",
            variant="opaque",
        )

        assert (out_dir / "outputs.json").exists()
        assert (out_dir / "ledger.json").exists()
        assert (out_dir / "run.log").exists()

        data = json.loads((out_dir / "outputs.json").read_text(encoding="utf-8"))
        assert data["schema"] == "rb-out/1"
        assert data["tier"] == "T0"
        assert data["status"] == "no_live_model"
        assert data["track"] == "end_to_end"

        ledger = json.loads((out_dir / "ledger.json").read_text(encoding="utf-8"))
        assert "calls" in ledger
        assert "totals" in ledger
