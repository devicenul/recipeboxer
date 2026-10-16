"""WP-T3A: antique T3 set archived as opt-in tier T3A; T3 = modern real-world (1900s+), pending curation.

Hermetic: no API keys, no network, no real age identity. Repo-state-independent where T3 content is
concerned (the modern T3 set will be imported later), via monkeypatched repo roots / vault dirs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from evals import tiers
from evals.tiers import TierPendingCurationError

REPO_ROOT = Path(__file__).resolve().parents[1]
PENDING_MSG = "T3 corpus pending curation; see evals/INTAKE.md"
ARCHIVE_CORPUS = REPO_ROOT / "evals" / "corpora" / "archive" / "t3_antique"
ARCHIVE_VAULT = REPO_ROOT / "evals" / "vault" / "archive" / "t3a.gt.enc"


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("RECIPEBOXER_GEMINI_API_KEY", raising=False)


# --------------------------------------------------------------------------- registry
def test_registry_t3a_archived_and_excluded_from_defaults() -> None:
    assert tiers.is_archived("t3a") and tiers.is_known("T3A")
    assert "T3A" not in tiers.ACTIVE_TIERS
    assert "T3A" not in tiers.DEFAULT_TIERS
    assert "T3" in tiers.DEFAULT_TIERS and "T3" in tiers.PENDING_CURATION_TIERS
    assert tiers.legacy_label("T3A") == "T3"
    assert tiers.legacy_label("T1") is None
    assert tiers.corpus_dir_for("T3A", REPO_ROOT) == ARCHIVE_CORPUS
    assert tiers.vault_candidates_for("T3A", REPO_ROOT / "evals" / "vault") == (ARCHIVE_VAULT,)


def test_pending_error_is_typed_and_clear() -> None:
    err = TierPendingCurationError("t3")
    assert isinstance(err, FileNotFoundError)
    assert PENDING_MSG in str(err)
    assert "T3A" in str(err)


def test_archived_files_moved_not_deleted() -> None:
    assert (ARCHIVE_CORPUS / "DECKS.json").is_file()
    assert (ARCHIVE_CORPUS / "ATTRIBUTION.md").is_file()
    assert (ARCHIVE_CORPUS / "CANDIDATES.md").is_file()
    assert (ARCHIVE_CORPUS / "README.md").is_file()
    assert ARCHIVE_VAULT.is_file()
    assert not (REPO_ROOT / "evals" / "corpora" / "real_web").exists()
    decks = json.loads((ARCHIVE_CORPUS / "DECKS.json").read_text(encoding="utf-8"))
    imgs = sorted(p for p in (ARCHIVE_CORPUS / "d01").iterdir() if p.suffix.lower() == ".jpg")
    assert decks == [{"deck_id": "d01", "n_images": len(imgs)}]


# --------------------------------------------------------------------------- harness
def test_harness_resolves_archived_t3a() -> None:
    from evals.harness.run import resolve_deck_images

    decks = resolve_deck_images("T3A")
    assert list(decks) == ["d01"]
    assert decks["d01"] and all(p.parent == ARCHIVE_CORPUS / "d01" for p in decks["d01"])


@pytest.mark.parametrize("make_dir", [False, True])
def test_harness_t3_pending_when_missing_or_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_dir: bool) -> None:
    import evals.harness.run as run_mod

    if make_dir:
        (tmp_path / "evals" / "corpora" / "t3").mkdir(parents=True)
        (tmp_path / "evals" / "corpora" / "t3" / "README.md").write_text("pending\n", encoding="utf-8")
    monkeypatch.setattr(run_mod, "get_repo_root", lambda: tmp_path)
    with pytest.raises(TierPendingCurationError, match="T3 corpus pending curation; see evals/INTAKE.md"):
        run_mod.resolve_deck_images("T3")


def test_harness_other_missing_tier_still_plain_filenotfound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import evals.harness.run as run_mod

    monkeypatch.setattr(run_mod, "get_repo_root", lambda: tmp_path)
    with pytest.raises(FileNotFoundError) as ei:
        run_mod.resolve_deck_images("T4")
    assert not isinstance(ei.value, TierPendingCurationError)


def test_harness_cli_pending_exit_code(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    import evals.harness.run as run_mod

    def _raise(**_kw):
        raise TierPendingCurationError("T3")

    monkeypatch.setattr(run_mod, "run_benchmark", _raise)
    assert run_mod.main(["--tier", "T3", "--out", "unused"]) == 2
    assert PENDING_MSG in capsys.readouterr().err


# --------------------------------------------------------------------------- GT loader / scorer
def test_gt_loader_t3_pending_and_t3a_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import evals.vault as vault_mod
    from evals.scoring.gt import load_ground_truth

    with pytest.raises(TierPendingCurationError, match="pending curation"):
        load_ground_truth("T3", vault_dir=tmp_path)

    (tmp_path / "archive").mkdir()
    payload = {"schema": "rb-gt/1", "tier": "T3", "decks": []}
    (tmp_path / "archive" / "t3a.gt.enc").write_bytes(json.dumps(payload).encode("utf-8"))
    monkeypatch.setattr(vault_mod, "decrypt_bytes", lambda b, identity_path=None: b)
    assert load_ground_truth("t3a", vault_dir=tmp_path) == payload

    with pytest.raises(FileNotFoundError) as ei:
        load_ground_truth("T4", vault_dir=tmp_path)
    assert not isinstance(ei.value, TierPendingCurationError)


def _write_outputs(d: Path, tier: str, status: str) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "outputs.json").write_text(json.dumps({"tier": tier, "status": status, "run_id": "r", "decks": []}),
                                    encoding="utf-8")
    return d


def test_score_cli_t3_pending_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                      capsys: pytest.CaptureFixture) -> None:
    import evals.scoring.gt as gt_mod
    from evals.score import main

    (tmp_path / "evals" / "vault").mkdir(parents=True)
    monkeypatch.setattr(gt_mod, "get_repo_root", lambda: tmp_path)
    out = _write_outputs(tmp_path / "out", "T3", "ok")
    rc = main(["--tier", "T3", "--outputs", str(out), "--report", str(tmp_path / "rep")])
    assert rc == 2
    assert PENDING_MSG in capsys.readouterr().err


def test_score_relabels_legacy_t3_outputs_as_t3a(tmp_path: Path) -> None:
    from evals.score import main

    out = _write_outputs(tmp_path / "out", "T3", "no_live_model")  # no GT needed
    rc = main(["--tier", "T3A", "--outputs", str(out), "--report", str(tmp_path / "rep")])
    assert rc == 0
    rep = json.loads((tmp_path / "rep" / "report.json").read_text(encoding="utf-8"))
    assert json.dumps(rep).count('"T3A"') >= 1


def test_score_t3a_per_item_feedback_refused(tmp_path: Path) -> None:
    from evals.score import main

    out = _write_outputs(tmp_path / "out", "T3", "no_live_model")
    with pytest.raises(ValueError, match="per_item"):
        main(["--tier", "T3A", "--outputs", str(out), "--report", str(tmp_path / "rep"), "--feedback", "per_item"])


# --------------------------------------------------------------------------- vault / intake / calibration
def test_vault_rekey_includes_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import evals.vault as vault_mod

    (tmp_path / "archive").mkdir()
    (tmp_path / "t1.gt.enc").write_bytes(b"a")
    (tmp_path / "archive" / "t3a.gt.enc").write_bytes(b"b")
    monkeypatch.setattr(vault_mod, "load_recipients", lambda p=None: ["r"])
    monkeypatch.setattr(vault_mod, "load_identity", lambda p=None: object())
    monkeypatch.setattr(vault_mod, "decrypt_bytes", lambda b, identity=None, **k: b)
    monkeypatch.setattr(vault_mod, "encrypt_bytes", lambda b, recipients=None, **k: b + b"!")
    done = vault_mod.rekey(tmp_path)
    assert {p.name for p in done} == {"t1.gt.enc", "t3a.gt.enc"}
    assert (tmp_path / "archive" / "t3a.gt.enc").read_bytes() == b"b!"


def test_intake_refuses_archived_tier(tmp_path: Path) -> None:
    from evals.intake_real import intake_photos

    with pytest.raises(ValueError, match="archived"):
        intake_photos(tmp_path, tmp_path, tier="T3A", corpora_dir=tmp_path / "c", vault_dir=tmp_path / "v")


def test_calibration_fit_forbids_archive_paths() -> None:
    from evals.calibration_fit import assert_not_forbidden_path

    for p in ("evals/corpora/archive/t3_antique/d01/x.jpg", "evals/vault/archive/t3a.gt.enc"):
        with pytest.raises(AssertionError, match="FORBIDDEN EVALUATION PATH"):
            assert_not_forbidden_path(p)


# --------------------------------------------------------------------------- curation tools (era-neutral)
def test_fetch_specs_are_data_and_antique_preset_frozen(tmp_path: Path) -> None:
    from evals.curation_t3 import fetch

    assert not hasattr(fetch, "CANDIDATE_SPECS")
    with pytest.raises(ValueError, match="INTAKE.md"):
        fetch.load_candidate_specs()
    antique = fetch.load_candidate_specs(preset="t3a_antique")
    assert len(antique) == 25
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"recipe_ref": "x"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="missing keys"):
        fetch.load_candidate_specs(bad)
    good = tmp_path / "good.json"
    good.write_text(json.dumps([{
        "recipe_ref": "m1", "stock_id": "stock_a", "institution": "I", "license": "PD", "legibility": "good",
        "faces": [{"face_ref": "f1", "display_url": "https://example.invalid/p", "attribution_desc": "d",
                   "modality": "TYPEWRITTEN"}],
    }]), encoding="utf-8")
    assert fetch.load_candidate_specs(good)[0]["recipe_ref"] == "m1"
    with pytest.raises(ValueError):
        fetch.fetch_t3_corpus(tmp_path / "c", tmp_path / "v.enc", tmp_path / "cache")  # no specs


def test_curation_prompts_era_neutral_keep_verbatim_spelling() -> None:
    from evals.curation_t3.draft_structured import draft_structured_prompt

    prompt = draft_structured_prompt(["1 cup sugar"])
    low = prompt.lower()
    for word in ("historical", "17th", "manuscript", "archaic", "archival"):
        assert word not in low, word
    assert "PRESERVE ORIGINAL SPELLING" in prompt
    assert "verbatim" in low
    src = (REPO_ROOT / "evals" / "curation_t3" / "memorization_probe.py").read_text(encoding="utf-8")
    assert "historical" not in src.lower()


# --------------------------------------------------------------------------- anticheat: archive is GT-adjacent
@pytest.mark.parametrize("rel", ["evals/corpora/archive/t3_antique/DECKS.json", "evals/vault/archive/t3a.gt.enc"])
def test_sut_audit_hook_denies_archive_paths(tmp_path: Path, rel: str) -> None:
    target = str(REPO_ROOT / rel)
    launcher = tmp_path / "cheat.py"
    launcher.write_text(f"import sys\ntry:\n    open({target!r}, 'rb').close()\n    sys.exit(0)\nexcept Exception:\n"
                        f"    sys.exit(1)\n", encoding="utf-8")
    boot = ("import runpy\nfrom evals.harness.sut_boot import _install_anti_cheat_audit_hook\n"
            f"_install_anti_cheat_audit_hook()\nrunpy.run_path({str(launcher)!r}, run_name='__main__')\n")
    env = {k: v for k, v in os.environ.items() if k not in ("GEMINI_API_KEY", "RECIPEBOXER_GEMINI_API_KEY")}
    env["PYTHONPATH"] = str(REPO_ROOT)
    res = subprocess.run([sys.executable, "-c", boot], cwd=str(tmp_path), env=env, capture_output=True, text=True,
                         timeout=30)
    assert res.returncode == 86, res.stderr
    assert "CHEAT_DETECTED" in res.stderr
