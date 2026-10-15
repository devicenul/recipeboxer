"""RT3 canaries (WP-RT3FIX): SUT isolation, full-pipeline prompt-leak scan, fabricated confidences.

Kills the red-team #3 mutants that survived the Gate B canaries:
- A2 (card_id injected into the set_verify_v2 ext-anchor prompt): full >= 10-card prompt scan over every LLM
  stage incl. set_verify_v2, plus a deterministic SetVerifier ext-anchor scan.
- B2 (SUT globs ``$HOME/.cache/projects/*/.../t3gt/spotcheck/*.html``): headless run in a subprocess under the
  strict ``sut_boot`` sandbox with a planted fake GT file; plus direct hook-trip probes.
- C / C2 (constant recipe confidence; constant ``grp_conf`` prior): headless runs asserting
  ``recipes[].confidence`` is None without grouping evidence and never a constant otherwise; AST scan.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from recipeboxer.ai import TextPart
from recipeboxer.headless import run_headless_analysis
from recipeboxer.models.domain import CardRole, VisualFingerprint

from .fake_stage_client import StageScriptedClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_CARDS_DIR = REPO_ROOT / "tests" / "fixtures" / "sample_cards"
SUT_BOOT = REPO_ROOT / "evals" / "harness" / "sut_boot.py"
FAKE_CLIENT_SRC = Path(__file__).resolve().parent / "fake_stage_client.py"

N_DECK = 12  # >= GroupingEngine.MIN_CARDS_FITTED so the fitted path (ranked screening, ext anchors) runs

CATEGORY_CODES = ["_vhc_", "_clp_", "_dmg_", "_mcs_", "_sta_", "_typ_"]


def _deck(tmp: Path, n: int = N_DECK) -> List[Path]:
    cards = sorted(SAMPLE_CARDS_DIR.glob("*.jpg"))[:n]
    assert len(cards) == n
    tmp.mkdir(parents=True, exist_ok=True)
    for c in cards:
        shutil.copy(c, tmp / c.name)
    return cards


def _forbidden_tokens(cards: List[Path]) -> List[str]:
    toks = set(CATEGORY_CODES)
    for c in cards:
        toks.add(c.name)
        toks.add(c.stem)
    return sorted(toks, key=len, reverse=True)


# ---------------------------------------------------------------------------------------------------------
# 1. Audit-hook probes (RT3-01)
# ---------------------------------------------------------------------------------------------------------
def _run_hooked(code: str, cwd: Path, env_extra: Optional[Dict[str, str]] = None,
                sandbox: Optional[dict] = None, timeout: int = 60) -> subprocess.CompletedProcess:
    boot = (
        "import sys\n"
        "from evals.harness.sut_boot import install_from_env\n"
        "install_from_env()\n"
        f"exec(compile({code!r}, 'probe', 'exec'))\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env.pop("RB_SUT_SANDBOX", None)
    if sandbox is not None:
        env["RB_SUT_SANDBOX"] = json.dumps(sandbox)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-c", boot], cwd=str(cwd), env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, timeout=timeout)


def _plant(path: Path, text: str = "<html>FAKE GT recipe_r999</html>") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("rel", [
    "fakehome/.cache/projects/recipeboxer-x/t3gt/spotcheck/recipe_r999.html",   # $PCD/t3gt pattern
    "fakehome/.cache/projects/recipeboxer-x/t3_cache/raw_transcripts/rw-001.txt",
    "fakehome/.cache/projects/recipeboxer-x/gutenberg_cache/pg123.txt",
    "fakehome/.cache/projects/recipeboxer-x/some_innocent_name/notes.txt",     # deny root, no name cue
    "AntiGravity/.gemini/antigravity/brain/abc/scratch/spot/recipe_r1.html",   # agent artifact dir
    "elsewhere/t3gt/spotcheck/recipe_r999.html",                               # name pattern anywhere
])
def test_hook_blocks_fake_gt_reads(tmp_path: Path, rel: str) -> None:
    work = tmp_path / "work"
    work.mkdir()
    fake = _plant(tmp_path / rel)
    proc = _run_hooked(f"open({str(fake)!r}).read()\nprint('READ OK')", work,
                       env_extra={"HOME": str(tmp_path / "fakehome")})
    assert proc.returncode == 86, (proc.returncode, proc.stdout, proc.stderr)
    assert "CHEAT_DETECTED" in proc.stderr and "READ OK" not in proc.stdout


def test_hook_blocks_project_cache_glob_and_listing(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    home = tmp_path / "fakehome"
    _plant(home / ".cache/projects/p1/redteam3/fakegt/t3gt/spotcheck/recipe_r999.html")
    code = ("from pathlib import Path\nimport os\n"
            "b = Path(os.environ['HOME']) / '.cache' / 'projects'\n"
            "print(list(b.glob('*/redteam3/fakegt/t3' + 'gt/spot' + 'check/*.html')))\n")
    proc = _run_hooked(code, work, env_extra={"HOME": str(home)})
    assert proc.returncode == 86, (proc.stdout, proc.stderr)
    proc = _run_hooked("import os\nprint(os.listdir(os.path.expanduser('~/.cache/projects')))", work,
                       env_extra={"HOME": str(home)})
    assert proc.returncode == 86, (proc.stdout, proc.stderr)


def test_hook_configured_deny_root_blocks_real_home_cache(tmp_path: Path) -> None:
    """Harness mode: HOME is private, but the *real* project cache passed as deny root stays blocked."""
    work, real = tmp_path / "work", tmp_path / "realhome" / ".cache" / "projects"
    work.mkdir()
    fake = _plant(real / "recipeboxer-x" / "notes" / "gt.json", "{}")
    sb = {"strict": True, "allow": [str(tmp_path / "sbx")], "deny": [str(real)]}
    (tmp_path / "sbx" / "home").mkdir(parents=True)
    proc = _run_hooked(f"open({str(fake)!r}).read()", work, sandbox=sb,
                       env_extra={"HOME": str(tmp_path / "sbx" / "home")})
    assert proc.returncode == 86, (proc.stdout, proc.stderr)


def test_hook_strict_allowlist(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "ok.txt").write_text("fine", encoding="utf-8")
    outside = _plant(tmp_path / "outside" / "x.txt", "secret")
    sbx = tmp_path / "sbx"
    (sbx / "home").mkdir(parents=True)
    (sbx / "tmp").mkdir()
    sb = {"strict": True, "allow": [str(sbx)], "deny": []}
    env = {"HOME": str(sbx / "home"), "TMPDIR": str(sbx / "tmp")}
    ok = _run_hooked("import json, numpy, tempfile, ssl\n"
                     "print(open('ok.txt').read())\n"
                     "with tempfile.NamedTemporaryFile() as f: f.write(b'x')\n"
                     "import recipeboxer.headless\nprint('IMPORT OK')", work, sandbox=sb, env_extra=env, timeout=120)
    assert ok.returncode == 0, (ok.stdout, ok.stderr)
    assert "IMPORT OK" in ok.stdout
    bad = _run_hooked(f"open({str(outside)!r}).read()", work, sandbox=sb, env_extra=env)
    assert bad.returncode == 86 and "allowlist" in bad.stderr, (bad.stdout, bad.stderr)
    # The sandbox config is consumed: the SUT cannot read it back from its environment.
    env_probe = _run_hooked("import os\nprint('CFG' if 'RB_SUT_SANDBOX' in os.environ else 'NOCFG')", work,
                            sandbox=sb, env_extra=env)
    assert "NOCFG" in env_probe.stdout


# ---------------------------------------------------------------------------------------------------------
# 2. Headless pipeline under the strict harness sandbox, with a planted fake GT file (RT3-01, mutant B2)
# ---------------------------------------------------------------------------------------------------------
_SUBPROCESS_RUN = r'''
import asyncio, json, os, sys
from evals.harness.sut_boot import install_from_env
install_from_env()
sys.path.insert(0, os.getcwd())
from fake_stage_client import StageScriptedClient
from recipeboxer.headless import run_headless_analysis
client = StageScriptedClient()
res = asyncio.run(run_headless_analysis(input_dir="input", out_file="out.json", auto_approve=True,
                                        injected_client=client))
print("RECIPES", len(res["recipes"]), "STATUS", res["status"])
'''


def test_headless_under_strict_sandbox_with_planted_gt(tmp_path: Path) -> None:
    from evals.harness.run import build_scrubbed_env, prepare_sut_sandbox

    work = tmp_path / "work"
    _deck(work / "input")
    shutil.copy(FAKE_CLIENT_SRC, work / "fake_stage_client.py")
    # A HOME that (as on the eval host) holds a project cache with GT-adjacent plaintext.
    home = tmp_path / "fakehome"
    _plant(home / ".cache/projects/recipeboxer-x/redteam3/fakegt/t3gt/spotcheck/recipe_r999.html")
    _plant(home / ".cache/projects/recipeboxer-x/t3gt/spotcheck/recipe_r001.html")
    sandbox = prepare_sut_sandbox(tmp_path / "sbx")
    env = build_scrubbed_env(allow_live_key=False, sandbox=sandbox, allow_roots=[str(work)])
    cfg = json.loads(env["RB_SUT_SANDBOX"])
    assert cfg["strict"] is True
    # Defense in depth: even if HOME isolation failed, the cache under HOME is unreachable.
    env["HOME"] = str(home)
    cfg["allow"].append(str(home))
    env["RB_SUT_SANDBOX"] = json.dumps(cfg)
    env["PYTHONPATH"] = str(REPO_ROOT)
    if os.environ.get("PYTHONPYCACHEPREFIX"):
        env["PYTHONPYCACHEPREFIX"] = os.environ["PYTHONPYCACHEPREFIX"]
    for k in ("GEMINI_API_KEY", "RECIPEBOXER_GEMINI_API_KEY"):
        env.pop(k, None)
    proc = subprocess.run([sys.executable, "-c", _SUBPROCESS_RUN], cwd=str(work), env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=900)
    assert proc.returncode == 0 and "CHEAT_DETECTED" not in proc.stderr, (
        f"rc={proc.returncode}\nSTDOUT:{proc.stdout[-2000:]}\nSTDERR:{proc.stderr[-4000:]}"
    )
    out = json.loads((work / "out.json").read_text())
    assert len(out["recipes"]) >= 1
    assert out["provenance"]["up3_models"]["loaded"] is True  # RT3-08 provenance present


# ---------------------------------------------------------------------------------------------------------
# 3. Full-pipeline prompt-leak scan on a >= 10-card deck (RT3-04(1), mutants A / A2)
# ---------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_prompt_leak_scan_all_stages_fitted_path(tmp_path: Path, monkeypatch) -> None:
    from recipeboxer.correlation.engine import GroupingEngine
    from recipeboxer.prompts import get_prompt

    # Verify every back / continuation anchor that has candidates (exercises set_verify_v2 deterministically).
    monkeypatch.setattr(GroupingEngine, "ANCHOR_EXT_P", 0.0)
    cards = _deck(tmp_path / "scans")
    client = StageScriptedClient()
    out = tmp_path / "out.json"
    await asyncio.wait_for(run_headless_analysis(input_dir=tmp_path / "scans", out_file=out, auto_approve=True,
                                                 injected_client=client), timeout=600)
    texts = client.request_texts()
    stages = {s for s, _, _ in texts}
    for st in ("FINGERPRINT", "SET_VERIFY", "TRANSCRIBE", "STRUCTURE", "GROUNDING", "REPAIR"):
        assert st in stages, f"stage {st} never requested; scanned {sorted(stages)}"
    sys_insts = [r.system_instruction for r in client.requests]
    assert get_prompt("set_verify_v2").text in sys_insts, "ext-anchor (set_verify_v2) path not exercised"
    assert get_prompt("consistency_v1").text in sys_insts, "consistency second read not exercised"
    toks = _forbidden_tokens(cards)
    for stage, _meta, text in texts:
        low = text.lower()
        for tok in toks:
            assert tok.lower() not in low, f"Prompt leak: {tok!r} in {stage} request"


@pytest.mark.asyncio
async def test_set_verifier_ext_anchor_prompt_has_no_card_ids() -> None:
    from recipeboxer.config import Settings
    from recipeboxer.correlation.semantic import SetVerifier
    from recipeboxer.prompts import get_prompt

    def fp(cid: str, role: CardRole, back: bool) -> VisualFingerprint:
        return VisualFingerprint(card_id=cid, image_path=f"/scans/{cid}", sha256_hash=(cid.encode().hex() * 8)[:64],
                                 role=role, looks_like_back=back)

    anchor = fp("rec_vhc_plum_cake_1950_back.jpg", CardRole.SEQUENCE_CONTINUATION, True)
    cands = [fp("rec_vhc_plum_cake_1950_card2_front.jpg", CardRole.SEQUENCE_CONTINUATION, False),
             fp("rec_clp_corn_fritters_front.jpg", CardRole.SEQUENCE_START, False)]
    for a in (anchor, cands[1]):
        client = StageScriptedClient()
        ver = SetVerifier(llm_client=client, settings=Settings(gemini_api_key=None))
        await ver.verify(a, [c for c in cands + [anchor] if c.card_id != a.card_id], images=None)
        assert len(client.requests) == 1
        req = client.requests[0]
        if a is anchor:
            assert req.system_instruction == get_prompt("set_verify_v2").text
        text = " ".join([req.system_instruction or ""] + [p.text for p in req.parts if isinstance(p, TextPart)])
        for c in cands + [anchor]:
            for tok in (c.card_id, Path(c.card_id).stem):
                assert tok not in text, f"card id {tok!r} leaked into SET_VERIFY prompt"
        for code in CATEGORY_CODES:
            assert code not in text


# ---------------------------------------------------------------------------------------------------------
# 4. Fabricated-confidence canaries (RT3-03, RT3-04(2)(3), mutants C / C2)
# ---------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_grouping_evidence_means_no_recipe_confidence(tmp_path: Path, monkeypatch) -> None:
    """engine.group fails -> standalone fallback proposals carry no confidence -> recipes[].confidence is None."""
    from recipeboxer.correlation.engine import GroupingEngine

    async def _boom(self, *a, **k):
        raise RuntimeError("grouping unavailable (canary)")

    monkeypatch.setattr(GroupingEngine, "group", _boom)
    _deck(tmp_path / "scans", n=4)
    client = StageScriptedClient()
    out = tmp_path / "out.json"
    await asyncio.wait_for(run_headless_analysis(input_dir=tmp_path / "scans", out_file=out, auto_approve=True,
                                                 injected_client=client), timeout=300)
    data = json.loads(out.read_text())
    assert data["recipes"], data.get("failures")
    confs = [r["confidence"] for r in data["recipes"]]
    assert all(c is None for c in confs), f"fabricated recipe confidence without grouping evidence: {confs}"


@pytest.mark.asyncio
async def test_recipe_confidence_is_evidence_driven_not_constant(tmp_path: Path) -> None:
    _deck(tmp_path / "scans")
    client = StageScriptedClient()
    out = tmp_path / "out.json"
    await asyncio.wait_for(run_headless_analysis(input_dir=tmp_path / "scans", out_file=out, auto_approve=True,
                                                 injected_client=client), timeout=600)
    confs = [r["confidence"] for r in json.loads(out.read_text())["recipes"]]
    numeric = [c for c in confs if c is not None]
    assert numeric, confs
    assert len(set(numeric)) > 1, f"recipe confidences are a constant: {confs}"
    for c in numeric:
        assert c not in (0.95, 0.9, 0.8, 1.0), f"suspicious constant recipe confidence {c}: {confs}"


@pytest.mark.asyncio
async def test_headless_emits_actual_proposal_confidence(tmp_path: Path) -> None:
    """Promoted from test_wp_rt1_redteam: the real proposal confidence feeds recipe_confidence (mutant C)."""
    from recipeboxer.ai import ScriptedLLMClient
    from recipeboxer.models.correlation import CorrelationProposal
    from recipeboxer.models.recipe import RecipeSchema
    import recipeboxer.correlation.linkmodel as _lm

    c1 = SAMPLE_CARDS_DIR / "rec_vhc_apple_crisp_1948_front.jpg"
    c2 = SAMPLE_CARDS_DIR / "rec_vhc_apple_crisp_1948_back.jpg"
    input_dir = tmp_path / "scans"
    input_dir.mkdir()
    for c in (c1, c2):
        (input_dir / c.name).write_bytes(c.read_bytes())
    expected = 0.77
    client = ScriptedLLMClient([
        VisualFingerprint(card_id=c1.name, image_path=str(c1), sha256_hash="0" * 64, role=CardRole.SEQUENCE_START),
        VisualFingerprint(card_id=c2.name, image_path=str(c2), sha256_hash="1" * 64,
                          role=CardRole.SEQUENCE_CONTINUATION),
        CorrelationProposal(proposal_id="p1", ordered_card_ids=[c1.name, c2.name], confidence=expected,
                            recommended_action="PAIR", semantic_score=0.77, semantic_verified=True),
        RecipeSchema(recipe_id="r1", title="Apple Crisp", ingredients=["Apples"], directions=["Bake"]),
    ])
    seen: list = []
    orig = _lm.recipe_confidence

    def _spy(group_conf, recipe, n_cards, models=None):
        res = orig(group_conf, recipe, n_cards) if models is None else orig(group_conf, recipe, n_cards, models)
        seen.append((group_conf, res))
        return res

    out = tmp_path / "out.json"
    _lm.recipe_confidence = _spy
    try:
        await run_headless_analysis(input_dir=input_dir, out_file=out, auto_approve=True, injected_client=client)
    finally:
        _lm.recipe_confidence = orig
    recipes = json.loads(out.read_text())["recipes"]
    assert len(recipes) == 1
    assert seen and seen[0][0] == expected
    assert recipes[0]["confidence"] == seen[0][1]
    assert recipes[0]["confidence"] not in (1.0, 0.95)


_CONF_NAMES = ("confidence", "conf", "grp_conf", "rec_conf", "chain_conf", "group_conf", "eff_confidence")
_STATIC_FILES = [
    "recipeboxer/headless.py",
    "recipeboxer/agent/orchestrator.py",
    "recipeboxer/correlation/engine.py",
    "recipeboxer/correlation/optimizer.py",
    "recipeboxer/correlation/linkmodel.py",
    "recipeboxer/correlation/semantic.py",
]
# Reviewed exceptions: oracle-track GT grouping input (not a SUT estimate).
_ALLOWED = {
    ("recipeboxer/headless.py", "confidence", 1.0),
    # legacy optimizer: local filter sentinel for a proposal without any score (dropped by min_pair_confidence)
    ("recipeboxer/correlation/optimizer.py", "conf", 0.0),
}


def _const_num(node: ast.AST) -> Optional[float]:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    return None


def test_no_literal_confidence_assignments() -> None:
    """AST scan: no numeric literal assigned to a confidence variable / keyword in grouping+headless code."""
    violations = []
    for rel in _STATIC_FILES:
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            pairs = []
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    name = t.id if isinstance(t, ast.Name) else (t.attr if isinstance(t, ast.Attribute) else None)
                    pairs.append((name, node.value))
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                t = node.target
                name = t.id if isinstance(t, ast.Name) else (t.attr if isinstance(t, ast.Attribute) else None)
                pairs.append((name, node.value))
            elif isinstance(node, ast.keyword):
                pairs.append((node.arg, node.value))
            elif isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        pairs.append((k.value, v))
            for name, val in pairs:
                if name not in _CONF_NAMES:
                    continue
                num = _const_num(val)
                if num is None:
                    continue
                if (rel, name, num) in _ALLOWED:
                    continue
                violations.append(f"{rel}:{getattr(val, 'lineno', '?')} {name} = {num}")
    assert not violations, "Literal confidence values (INV-AC4):\n" + "\n".join(violations)
