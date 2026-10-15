"""WP-RT3FIX unit tests: scorer calibration populations, harness sandbox, safe tar, minor RT3 findings."""

from __future__ import annotations

import io
import json
import logging
import tarfile
from pathlib import Path

import pytest

from recipeboxer.config import Settings
from recipeboxer.correlation.engine import GroupingEngine
from recipeboxer.correlation.linkmodel import Up3Models
from recipeboxer.models.correlation import PairEvidence
from recipeboxer.models.domain import CardRole
from tests.test_wp_up3_models import InMemoryImageProvider, _deck, _stub_models, _Verifier


# ------------------------------------------------------------------------------------------ RT3-02 scorer
def _gold(rid: str, faces, title: str):
    return {"recipe_id": rid, "faces": faces,
            "structured": {"title": title, "ingredients": ["2 cups flour", "1 tsp salt"], "directions": ["Mix."]}}


def _pred(img, title: str, conf):
    return {"image_ids": img, "confidence": conf,
            "recipe": {"title": title, "ingredients": ["2 cups flour", "1 tsp salt"], "directions": ["Mix."]}}


def test_ece_all_counts_unmatched_predictions_and_coverage() -> None:
    from evals.scoring.evaluator import evaluate_deck, score_evaluation

    gt = {"deck_id": "d01", "recipes": [_gold("r1", ["a.jpg"], "Plum Cake")]}
    out = {"deck_id": "d01", "recipes": [
        _pred(["a.jpg"], "Plum Cake", 0.9),                 # matched, correct
        _pred(["z.jpg"], "Totally Different Thing", 0.9),   # hallucinated -> incorrect
        _pred(["y.jpg"], "Another Fragment", None),         # abstention
    ], "unassigned_image_ids": [], "failures": []}
    res = evaluate_deck(out, gt)
    cal = res["calibration"]
    assert cal["n_pred"] == 3 and cal["n_with_confidence"] == 2
    assert cal["confidence_coverage"] == pytest.approx(2 / 3, abs=1e-3)
    assert cal["ece_matched"] == pytest.approx(0.1, abs=1e-6)
    # bin 0.9: mean conf 0.9, accuracy 0.5 -> 0.4
    assert cal["ece_all"] == pytest.approx(0.4, abs=1e-6)
    assert res["calibration_ece"] == cal["ece_all"]

    rep = score_evaluation({"tier": "T0", "status": "ok", "decks": [out]}, {"decks": [gt]})
    c = rep["metrics"]["calibration"]
    assert c["ece"] == c["ece_all"] == pytest.approx(0.4, abs=1e-6)
    assert c["ece_matched"] == pytest.approx(0.1, abs=1e-6)
    assert c["confidence_coverage"] == pytest.approx(2 / 3, abs=1e-3)


def test_report_markdown_shows_both_eces() -> None:
    from evals.report import _render_markdown

    rep = {"run_id": "r", "status": "ok", "metrics": {"grouping": {}, "extraction": {}, "judge": {}, "ops": {},
           "calibration": {"ece": 0.4, "ece_all": 0.4, "ece_matched": 0.1, "confidence_coverage": 0.6667,
                           "n_pred": 3, "n_with_confidence": 2}}}
    md = _render_markdown(rep, "T1", "aggregate")
    assert "ECE (all predictions)" in md and "ECE (matched only)" in md and "Confidence Coverage" in md
    assert "0.4000" in md and "0.1000" in md


# ------------------------------------------------------------------------------------------ RT3-01 harness
def test_build_scrubbed_env_sandbox(tmp_path: Path, monkeypatch) -> None:
    from evals.harness.run import build_scrubbed_env, prepare_sut_sandbox, real_deny_roots

    monkeypatch.setenv("PROJECT_CACHE_DIR", "/somewhere/projects/x")
    sb = prepare_sut_sandbox(tmp_path / "sbx")
    env = build_scrubbed_env(allow_live_key=False, sandbox=sb, allow_roots=[str(tmp_path / "deck")])
    assert env["HOME"] == str(sb["home"]) and env["TMPDIR"] == str(sb["tmp"])
    for k in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR"):
        assert env[k].startswith(str(sb["root"]))
    assert "PROJECT_CACHE_DIR" not in env
    cfg = json.loads(env["RB_SUT_SANDBOX"])
    assert cfg["strict"] is True
    assert str(tmp_path / "deck") in cfg["allow"] and str(sb["root"]) in cfg["allow"]
    import os
    real = os.path.expanduser("~")
    assert os.path.join(real, ".cache", "projects") in cfg["deny"]
    assert os.path.join(real, ".config", "recipeboxer") in cfg["deny"]
    assert cfg["deny"] == real_deny_roots()
    # No sandbox -> legacy behaviour (no config var)
    assert "RB_SUT_SANDBOX" not in build_scrubbed_env(allow_live_key=False)


def _tar_with(name: str, data: bytes = b"x", link: bool = False) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        ti = tarfile.TarInfo(name)
        if link:
            ti.type = tarfile.SYMTYPE
            ti.linkname = "/etc/passwd"
            tf.addfile(ti)
        else:
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


@pytest.mark.parametrize("name,link", [("../escape.jpg", False), ("/abs/escape.jpg", False), ("d01/lnk.jpg", True)])
def test_safe_extractall_rejects_unsafe_members(tmp_path: Path, name: str, link: bool) -> None:
    from evals.harness.run import _safe_extractall

    dest = tmp_path / "t2"
    dest.mkdir()
    with tarfile.open(fileobj=io.BytesIO(_tar_with(name, link=link))) as tf:
        try:
            _safe_extractall(tf, dest)
        except (tarfile.TarError, OSError):
            pass
    assert not (tmp_path / "escape.jpg").exists()
    assert not Path("/abs/escape.jpg").exists()
    lnk = dest / "d01" / "lnk.jpg"
    assert not (lnk.is_symlink() and Path(lnk.readlink()).is_absolute())


def test_safe_extractall_extracts_regular_files(tmp_path: Path) -> None:
    from evals.harness.run import _safe_extractall

    dest = tmp_path / "t2"
    dest.mkdir()
    with tarfile.open(fileobj=io.BytesIO(_tar_with("d01/a.jpg", b"img"))) as tf:
        _safe_extractall(tf, dest)
    assert (dest / "d01" / "a.jpg").read_bytes() == b"img"


# ------------------------------------------------------------------------------------------ RT3-03
def test_grouping_failure_fallback_has_no_confidence_static() -> None:
    src = Path(__file__).resolve().parent.parent / "recipeboxer" / "agent" / "orchestrator.py"
    assert "confidence=fp.role_confidence" not in src.read_text(encoding="utf-8")


# ------------------------------------------------------------------------------------------ RT3-05
@pytest.mark.asyncio
async def test_legacy_explanation_does_not_claim_calibration() -> None:
    eng = GroupingEngine(set_verifier=_Verifier({"a1": ["a2"]}), models=Up3Models({}))
    res = await eng.group(_deck(), InMemoryImageProvider({}), settings=Settings(gemini_api_key=None))
    multi = [p for p in res.proposals if len(p.ordered_card_ids) > 1]
    assert multi, res.proposals
    for p in multi:
        assert "Calibrated p=" not in (p.continuity_explanation or ""), p.continuity_explanation


@pytest.mark.asyncio
async def test_fitted_explanation_says_calibrated() -> None:
    eng = GroupingEngine(set_verifier=_Verifier({"a1": ["a2"]}), models=_stub_models())
    eng.MIN_CARDS_FITTED = 0
    res = await eng.group(_deck(), InMemoryImageProvider({}), settings=Settings(gemini_api_key=None))
    multi = [p for p in res.proposals if len(p.ordered_card_ids) > 1]
    assert multi and all("Calibrated p=" in (p.continuity_explanation or "") for p in multi)


# ------------------------------------------------------------------------------------------ RT3-07
def test_pair_evidence_has_gate_marker_fields() -> None:
    ev = PairEvidence(a="x", b="y")
    assert ev.gate is None and ev.p_link_ungated is None
    ev.gate = "rescue_unconfirmed"
    ev.p_link_ungated = 0.61
    ev.p_link = 0.02
    d = ev.model_dump()
    assert d["gate"] == "rescue_unconfirmed" and d["p_link_ungated"] == 0.61


def test_engine_records_gate_on_evidence_static() -> None:
    src = (Path(__file__).resolve().parent.parent / "recipeboxer" / "correlation" / "engine.py").read_text()
    i = src.index("p = min(p, GATE_CAP)")
    assert "ev.gate = gate" in src[i - 400:i]


# ------------------------------------------------------------------------------------------ RT3-08
def test_model_bundle_load_failure_is_logged_and_recorded(tmp_path: Path, caplog) -> None:
    bad = tmp_path / "calibration_default.json"
    bad.write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="recipeboxer.correlation.linkmodel"):
        m = Up3Models.load(bad)
    assert m.get("recipe") is None
    prov = m.provenance()
    assert prov["loaded"] is False and prov["error"]
    assert any("could not be loaded" in r.getMessage() for r in caplog.records)


def test_model_bundle_ok_provenance() -> None:
    m = Up3Models.load()
    prov = m.provenance()
    assert prov["loaded"] is True and "recipe" in prov["active_models"] and prov["error"] is None
