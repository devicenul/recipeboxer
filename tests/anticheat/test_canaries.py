"""Anti-cheat canary test suite enforcing architectural integrity and black-box invariants.

Implements the five non-negotiable canary tests defined in REMEDIATION_PLAN.md §6.1:
1. Rename Invariance: Permuting filenames and card order results in identical model inputs
   (up to opaque ID mapping).
2. Poisoned Vault: Injecting corrupted/swapped ground truth produces 100% byte-identical SUT output.
3. Audit-Hook Trip: Any attempt by the SUT or a plugin to read ground truth immediately terminates
   the subprocess with exit code 86 and CHEAT_DETECTED.
4. Prompt-Leak Scan: No original filenames, ground-truth titles, or category code tokens appear
   in any LLMRequest parts or prompts.
5. Import Graph: recipeboxer.* has zero static and runtime dependencies on evals.*, cardforge.*,
   or tests.*.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Set

import pytest
from pyrage import x25519

from recipeboxer.ai import (
    ImagePart,
    LLMRequest,
    LLMResponse,
    LLMStage,
    ScriptedLLMClient,
    TextPart,
    UsageTokens,
)
from recipeboxer.headless import run_headless_analysis
from recipeboxer.models.correlation import CorrelationProposal
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.recipe import RecipeSchema


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_CARDS_DIR = REPO_ROOT / "tests" / "fixtures" / "sample_cards"


# ---------------------------------------------------------------------------
# 1. Canary 1: Rename Invariance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_canary_rename_invariance(tmp_path: Path) -> None:
    """Canary 1: Permuting filenames and directory order produces identical model inputs."""
    assert SAMPLE_CARDS_DIR.exists()

    # Pick two sample card images
    card_files = [
        SAMPLE_CARDS_DIR / "rec_vhc_apple_crisp_1948_front.jpg",
        SAMPLE_CARDS_DIR / "rec_vhc_apple_crisp_1948_back.jpg",
    ]
    for c in card_files:
        assert c.exists(), f"Sample card {c} missing"

    bytes_0 = card_files[0].read_bytes()
    bytes_1 = card_files[1].read_bytes()

    # Setup Run A: named alpha, beta in order 0, 1
    dir_a = tmp_path / "run_a"
    dir_a.mkdir()
    (dir_a / "10_alpha.jpg").write_bytes(bytes_0)
    (dir_a / "20_beta.jpg").write_bytes(bytes_1)

    # Setup Run B: named zeta, omega in swapped directory order 1, 0
    dir_b = tmp_path / "run_b"
    dir_b.mkdir()
    (dir_b / "10_zeta.jpg").write_bytes(bytes_1)
    (dir_b / "20_omega.jpg").write_bytes(bytes_0)

    # Standard scripted responses for fingerprints and recipe extraction
    def _make_scripted_client() -> ScriptedLLMClient:
        return ScriptedLLMClient([
            # Card 1 fingerprint
            VisualFingerprint(
                card_id="dummy1",
                image_path="/memory/dummy1.jpg",
                sha256_hash="0" * 64,
                role=CardRole.SEQUENCE_START,
            ),
            # Card 2 fingerprint
            VisualFingerprint(
                card_id="dummy2",
                image_path="/memory/dummy2.jpg",
                sha256_hash="1" * 64,
                role=CardRole.SEQUENCE_CONTINUATION,
            ),
            # Candidate pair verification
            CorrelationProposal(
                proposal_id="p_inv",
                ordered_card_ids=["dummy1", "dummy2"],
                confidence=0.88,
                recommended_action="PAIR",
                semantic_score=0.9,
                semantic_verified=True,
            ),
            # Recipe extraction
            RecipeSchema(
                recipe_id="rec_invariance_test",
                title="Apple Crisp",
                ingredients=["Apples", "Cinnamon", "Sugar"],
                directions=["Bake until golden."],
            ),
        ])

    client_a = _make_scripted_client()
    client_b = _make_scripted_client()

    await asyncio.wait_for(
        run_headless_analysis(
            input_dir=dir_a,
            auto_approve=True,
            injected_client=client_a,
        ),
        timeout=30.0,
    )

    await asyncio.wait_for(
        run_headless_analysis(
            input_dir=dir_b,
            auto_approve=True,
            injected_client=client_b,
        ),
        timeout=30.0,
    )

    # Verify both runs executed the same count of LLM requests
    assert len(client_a.requests) == len(client_b.requests)
    assert len(client_a.requests) >= 2

    # Extract all image byte hashes passed in each run
    def _extract_image_hashes(requests: List[LLMRequest]) -> Set[str]:
        hashes = set()
        for r in requests:
            for p in r.parts:
                if isinstance(p, ImagePart):
                    hashes.add(p.sha256())
        return hashes

    hashes_a = _extract_image_hashes(client_a.requests)
    hashes_b = _extract_image_hashes(client_b.requests)

    # The image inputs to the model must be identical regardless of renaming/reordering
    assert hashes_a == hashes_b
    assert len(hashes_a) in (2, 4)


# ---------------------------------------------------------------------------
# 2. Canary 2: Poisoned Vault
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_canary_poisoned_vault(tmp_path: Path) -> None:
    """Canary 2: Swapping in a poisoned/tampered GT vault produces byte-identical SUT output."""
    # Create input directory with 2 sample cards
    input_dir = tmp_path / "scans"
    input_dir.mkdir()
    c1 = SAMPLE_CARDS_DIR / "rec_clp_crisco_fried_chicken_front.jpg"
    c2 = SAMPLE_CARDS_DIR / "rec_clp_crisco_fried_chicken_back.jpg"
    (input_dir / "c1.jpg").write_bytes(c1.read_bytes())
    (input_dir / "c2.jpg").write_bytes(c2.read_bytes())

    # Create dummy scripted client
    def _make_client() -> ScriptedLLMClient:
        return ScriptedLLMClient([
            VisualFingerprint(card_id="c1", image_path="c1.jpg", sha256_hash="0" * 64, role=CardRole.SEQUENCE_START),
            VisualFingerprint(card_id="c2", image_path="c2.jpg", sha256_hash="1" * 64, role=CardRole.SEQUENCE_CONTINUATION),
            CorrelationProposal(
                proposal_id="p_vlt",
                ordered_card_ids=["c1", "c2"],
                confidence=0.88,
                recommended_action="PAIR",
                semantic_score=0.9,
                semantic_verified=True,
            ),
            RecipeSchema(
                recipe_id="rec_test_vault",
                title="Fried Chicken",
                ingredients=["Chicken", "Shortening"],
                directions=["Fry."],
            ),
        ])

    out1 = tmp_path / "out1.json"
    client1 = _make_client()
    await asyncio.wait_for(
        run_headless_analysis(
            input_dir=input_dir,
            out_file=out1,
            auto_approve=True,
            injected_client=client1,
        ),
        timeout=30.0,
    )

    # Now create a poisoned fake ground truth vault file
    fake_identity = x25519.Identity.generate()
    fake_recipient = fake_identity.to_public()
    poisoned_vault_dir = tmp_path / "fake_vault"
    poisoned_vault_dir.mkdir()

    # Import pyrage to encrypt poisoned GT
    import pyrage
    poisoned_payload = json.dumps({"poison": "corrupted ground truth", "recipes": []}).encode("utf-8")
    poisoned_enc = pyrage.encrypt(poisoned_payload, [fake_recipient])
    (poisoned_vault_dir / "T1.gt.enc").write_bytes(poisoned_enc)

    # Point age environment at the poisoned key
    fake_key_file = tmp_path.parent / "fake_age.key"
    fake_key_file.write_text(str(fake_identity), encoding="utf-8")

    out2 = tmp_path / "out2.json"
    client2 = _make_client()

    old_env = os.environ.get("RB_EVAL_AGE_KEY_FILE")
    try:
        os.environ["RB_EVAL_AGE_KEY_FILE"] = str(fake_key_file)
        await asyncio.wait_for(
            run_headless_analysis(
                input_dir=input_dir,
                out_file=out2,
                auto_approve=True,
                injected_client=client2,
            ),
            timeout=30.0,
        )
    finally:
        if old_env is not None:
            os.environ["RB_EVAL_AGE_KEY_FILE"] = old_env
        else:
            os.environ.pop("RB_EVAL_AGE_KEY_FILE", None)
        if fake_key_file.exists():
            fake_key_file.unlink()

    # SUT output must be 100% byte-identical
    assert out1.read_bytes() == out2.read_bytes()


# ---------------------------------------------------------------------------
# 3. Canary 3: Audit-Hook Trip
# ---------------------------------------------------------------------------

def test_canary_audit_hook_trip(tmp_path: Path) -> None:
    """Canary 3: A subprocess attempting to open a GT or vault path trips audit hook (exit 86)."""
    sut_boot = REPO_ROOT / "evals" / "harness" / "sut_boot.py"
    assert sut_boot.exists()

    # Test file path that is strictly forbidden
    forbidden_path = str(REPO_ROOT / "evals" / "vault" / "T1.gt.enc")

    # Command that boots sut_boot and attempts to open forbidden_path inside Python
    python_code = f"""
import sys
# Attempt to open forbidden path
try:
    with open({forbidden_path!r}, "rb") as f:
        pass
    sys.exit(0)
except Exception as e:
    sys.exit(1)
"""

    test_launcher = tmp_path / "cheating_module.py"
    test_launcher.write_text(python_code, encoding="utf-8")

    # Run sut_boot with python -c to install hook, then execute test_launcher
    boot_cmd = f"""
import sys, runpy
from evals.harness.sut_boot import _install_anti_cheat_audit_hook
_install_anti_cheat_audit_hook()
runpy.run_path({str(test_launcher)!r}, run_name="__main__")
"""

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)

    res = subprocess.run(
        [sys.executable, "-c", boot_cmd],
        cwd=str(tmp_path),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
    )

    # Invariant assertion: Exit code MUST be exactly 86 and stderr MUST contain CHEAT_DETECTED
    assert res.returncode == 86, f"Expected exit code 86, got {res.returncode}. Stderr: {res.stderr}"
    assert "CHEAT_DETECTED" in res.stderr


# ---------------------------------------------------------------------------
# 4. Canary 4: Prompt-Leak Scan
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_canary_prompt_leak_scan(tmp_path: Path) -> None:
    """Canary 4: No original filenames, titles, or category codes appear in any LLMRequest."""
    input_dir = tmp_path / "scans"
    input_dir.mkdir()

    # Copy two cards with known names
    c1 = SAMPLE_CARDS_DIR / "rec_vhc_apple_crisp_1948_front.jpg"
    c2 = SAMPLE_CARDS_DIR / "rec_vhc_apple_crisp_1948_back.jpg"
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

    await asyncio.wait_for(
        run_headless_analysis(
            input_dir=input_dir,
            auto_approve=True,
            injected_client=client,
        ),
        timeout=30.0,
    )

    assert len(client.requests) == 4

    # Forbidden tokens that must NEVER appear in prompt text parts or system instructions
    forbidden_strings = [
        "rec_vhc_apple_crisp_1948_front.jpg",
        "rec_vhc_apple_crisp_1948_back.jpg",
        "rec_vhc_apple_crisp_1948",
        "_vhc_",
        "_clp_",
        "_dmg_",
        "_mcs_",
        "_sta_",
        "_typ_",
    ]

    for req in client.requests:
        assert req.system_instruction is not None and len(req.system_instruction.strip()) > 0, (
            f"Production system instruction missing or empty for stage {req.stage}"
        )
        assert any(isinstance(p, TextPart) and len(p.text.strip()) > 0 for p in req.parts), (
            f"Production prompt text parts missing or empty for stage {req.stage}"
        )
        assert any(isinstance(p, ImagePart) for p in req.parts), (
            f"Image payload missing for stage {req.stage}"
        )

        text_payloads = [req.system_instruction]
        for p in req.parts:
            if isinstance(p, TextPart):
                text_payloads.append(p.text)

        full_prompt_text = " ".join(text_payloads).lower()
        for token in forbidden_strings:
            assert token.lower() not in full_prompt_text, (
                f"Prompt leak detected! Forbidden token '{token}' found in LLMRequest for stage {req.stage}: {full_prompt_text}"
            )


# ---------------------------------------------------------------------------
# 5. Canary 5: Import Graph Check
# ---------------------------------------------------------------------------

def test_canary_import_graph_static() -> None:
    """Canary 5 (Static): recipeboxer.* modules must not import evals.*, cardforge.*, or tests.*."""
    package_root = REPO_ROOT / "recipeboxer"
    forbidden_roots = {"evals", "cardforge", "tests"}

    violations: List[str] = []

    for py_file in package_root.rglob("*.py"):
        rel_path = str(py_file.relative_to(REPO_ROOT)).replace("\\", "/")
        # recipeboxer/api/static can contain web files
        if "recipeboxer/api/static" in rel_path:
            continue

        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=rel_path)
        except SyntaxError as e:
            violations.append(f"{rel_path}: SyntaxError {e}")
            continue

        for node in ast.walk(tree):
            imports: List[str] = []
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)

            for imp in imports:
                root_pkg = imp.split(".")[0]
                if root_pkg in forbidden_roots:
                    violations.append(f"{rel_path}:{getattr(node, 'lineno', 0)} imports {imp!r}")

    assert not violations, "Static import graph violations detected:\n" + "\n".join(violations)


def test_canary_import_graph_runtime() -> None:
    """Canary 5 (Runtime): Importing recipeboxer must not load evals or cardforge in sys.modules."""
    subshell_code = """
import sys
import recipeboxer
loaded_forbidden = [m for m in sys.modules if m == 'evals' or m.startswith('evals.') or m == 'cardforge' or m.startswith('cardforge.')]
if loaded_forbidden:
    print(','.join(loaded_forbidden))
    sys.exit(1)
sys.exit(0)
"""
    res = subprocess.run(
        [sys.executable, "-c", subshell_code],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
    )

    assert res.returncode == 0, f"Runtime import leak detected: {res.stdout.strip()}"
