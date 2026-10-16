"""WP-T3I: modern real-world T3 import - scorer/oracle/tooling support + public corpus invariants.

Offline and hermetic: fictitious fixtures only (ids like ``imgA.jpg``), stubbed LLM clients, vault
encrypt/decrypt monkeypatched. The real T3 GT is never decrypted here; the corpus checks only look at the
public files (images, DECKS.json, ATTRIBUTION.md, README.md).
"""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from evals.scoring.evaluator import (
    evaluate_deck,
    has_text_gt,
    image_components,
    is_excluded_recipe,
    score_evaluation,
)
from evals.scoring.gt import create_oracle_plan, merge_overlapping_sets

REPO_ROOT = Path(__file__).resolve().parent.parent
T3_DIR = REPO_ROOT / "evals" / "corpora" / "t3"


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("RECIPEBOXER_GEMINI_API_KEY", raising=False)


# --------------------------------------------------------------------------- fixtures (fictitious)
def _pending(rid: str, faces: List[str], **extra: Any) -> Dict[str, Any]:
    rec = {
        "recipe_id": rid, "faces": faces, "verbatim": [], "verbatim_status": "pending",
        "structured": {"title": "", "yield": None, "source_note": None, "ingredients": [], "directions": [], "notes": []},
        "structured_status": "pending_draft", "difficulty": {"legibility": "unrated", "faces": len(faces)},
        "score_policy": "score", "excluded_reason": None,
    }
    rec.update(extra)
    return rec


def _texted(rid: str, faces: List[str], title: str, ings: List[str], steps: List[str]) -> Dict[str, Any]:
    return {
        "recipe_id": rid, "faces": faces,
        "verbatim": [title + "\n" + "\n".join(ings) + "\n" + " ".join(steps)],
        "structured": {"title": title, "yield": None, "source_note": None,
                       "ingredients": [{"raw": i, "quantity": None, "unit": None, "item": i, "section": None} for i in ings],
                       "directions": steps, "notes": []},
        "structured_status": "drafted_unreviewed", "difficulty": {"legibility": "good", "faces": len(faces)},
    }


def _pending_deck() -> Dict[str, Any]:
    # imgA carries 3 recipes; one of them continues on imgB; imgC single; imgD front/back with imgE;
    # imgX/imgY are distractors; r005 is a curator-excluded tail fragment on imgB.
    return {
        "deck_id": "d01", "deck_type": "real_modern", "multi_recipe_images": True,
        "images": [{"image_id": i, "modality": "HANDWRITTEN", "role": "recipe"} for i in
                   ("imgA.jpg", "imgB.jpg", "imgC.jpg", "imgD.jpg", "imgE.jpg")]
        + [{"image_id": "imgX.jpg", "modality": "PRINTED", "role": "distractor"},
           {"image_id": "imgY.jpg", "modality": "BLANK", "role": "distractor"}],
        "non_recipe_images": ["imgX.jpg", "imgY.jpg"],
        "recipes": [
            _pending("r001", ["imgA.jpg"]),
            _pending("r002", ["imgA.jpg"]),
            _pending("r003", ["imgA.jpg", "imgB.jpg"]),
            _pending("r004", ["imgC.jpg"]),
            _pending("r005", ["imgB.jpg"], score_policy="exclude", excluded_reason="partial_fragment"),
            _pending("r006", ["imgD.jpg", "imgE.jpg"], pairing="inferred", pairing_inferred=True),
        ],
    }


def _out(recipes: List[List[str]], tier: str = "T3") -> Dict[str, Any]:
    return {"tier": tier, "status": "ok", "decks": [{"deck_id": "d01", "recipes": [
        {"pred_id": f"p{i}", "image_ids": ids, "confidence": 0.7,
         "recipe": {"title": "Some Cake", "ingredients": [{"raw": "1 cup sugar"}], "directions": ["Bake."]}}
        for i, ids in enumerate(recipes)]}]}


# --------------------------------------------------------------------------- helpers
def test_image_components_merges_shared_images_and_keeps_disjoint() -> None:
    assert image_components([["a"], ["a", "b"], ["c"], ["b"]]) == [["a", "b"], ["c"]]
    assert image_components([["x", "y"], ["z"]]) == [["x", "y"], ["z"]]
    assert merge_overlapping_sets([["a"], ["a", "b"], ["c"]]) == [["a", "b"], ["c"]]
    assert merge_overlapping_sets([["p", "q"], ["r"]]) == [["p", "q"], ["r"]]


def test_flags_excluded_and_pending() -> None:
    assert is_excluded_recipe({"score_policy": "exclude"})
    assert is_excluded_recipe({"excluded_reason": "memorization_probe_match (overlap: 0.9)"})
    assert not is_excluded_recipe({"score_policy": "score", "excluded_reason": None})
    assert not has_text_gt(_pending("r1", ["a.jpg"]))
    assert has_text_gt(_texted("r1", ["a.jpg"], "T", ["x"], ["y"]))
    # legacy statuses keep the legacy (content-matched) behaviour
    assert has_text_gt({"structured": None, "structured_status": "pending_text_only_draft", "verbatim": ["v"]})
    assert has_text_gt({"structured": {}, "structured_status": None})


# --------------------------------------------------------------------------- scorer: pending GT
def test_pending_gt_grouping_computed_extraction_not_available() -> None:
    gt = {"schema": "rb-gt/1", "tier": "T3", "decks": [_pending_deck()]}
    perfect = _out([["imgA.jpg"], ["imgA.jpg"], ["imgA.jpg", "imgB.jpg"], ["imgC.jpg"], ["imgB.jpg"],
                    ["imgD.jpg", "imgE.jpg"]])
    rep = score_evaluation(perfect, gt)
    g = rep["metrics"]["grouping"]
    assert g["bcubed_f1"] == 1.0 and g["ari"] == 1.0 and g["exact_cluster_acc"] == 1.0
    ext = rep["metrics"]["extraction"]
    assert ext["status"] == "not_available" and ext["reason"] == "structured GT pending"
    for k in ("hallucinated_recipe_rate", "omitted_recipe_rate", "n_matched", "n_unmatched_pred"):
        assert ext[k] is None, k  # N/A, never 0
    assert ext["overall"] == {}
    assert ext["n_pending_structured_gt"] == 5 and ext["n_excluded_gold"] == 1
    assert rep["metrics"]["calibration"]["ece_all"] is None
    gd = rep["metrics"]["grouping_detail"]
    assert gd["modes"] == ["image_components"]
    assert gd["n_gold_recipes_scored"] == 5 and gd["n_distractor_images"] == 2
    assert gd["distractor_rejection_rate"] == 1.0
    assert gd["recipe_set_exact"] == 5 and gd["recipe_set_exact_acc"] == 1.0

    from evals.report import render
    _json, md = render(rep, feedback="aggregate")
    assert "**Title CER** | `N/A`" in md and "**Ingredient F1** | `N/A`" in md
    assert "**B-Cubed F1** | `1.0000`" in md


def test_pending_gt_under_split_and_distractor_penalised() -> None:
    gt = {"schema": "rb-gt/1", "tier": "T3", "decks": [_pending_deck()]}
    # One recipe per image, imgA/imgB not linked, front/back split, one distractor taken as a recipe.
    naive = _out([["imgA.jpg"], ["imgB.jpg"], ["imgC.jpg"], ["imgD.jpg"], ["imgE.jpg"], ["imgX.jpg"]])
    rep = score_evaluation(naive, gt)
    g = rep["metrics"]["grouping"]
    assert 0.0 < g["bcubed_f1"] < 1.0
    gd = rep["metrics"]["grouping_detail"]
    assert gd["distractor_rejection_rate"] == 0.5
    assert gd["recipe_set_exact_acc"] < 1.0
    assert rep["metrics"]["extraction"]["status"] == "not_available"


def test_mixed_text_and_pending_recipes() -> None:
    deck = {
        "deck_id": "d01", "deck_type": "real_modern",
        "images": [{"image_id": i, "modality": "TYPEWRITTEN"} for i in ("m1.jpg", "m2.jpg", "m3.jpg")],
        "recipes": [
            _texted("r001", ["m1.jpg"], "Lemon Squares", ["2 lemons", "1 cup sugar"], ["Mix and bake."]),
            _pending("r002", ["m2.jpg"]),
            _texted("r003", ["m3.jpg"], "Bean Soup", ["1 lb beans"], ["Simmer."]),
        ],
    }
    deck["recipes"][2]["score_policy"] = "exclude"
    out = {"tier": "T3", "status": "ok", "decks": [{"deck_id": "d01", "recipes": [
        {"pred_id": "p1", "image_ids": ["m1.jpg"], "confidence": 0.9,
         "recipe": {"title": "Lemon Squares", "ingredients": [{"raw": "2 lemons"}, {"raw": "1 cup sugar"}],
                    "directions": ["Mix and bake."]}},
        {"pred_id": "p2", "image_ids": ["m2.jpg"], "confidence": 0.9,
         "recipe": {"title": "Unknown", "ingredients": [], "directions": []}},
    ]}]}
    res = evaluate_deck(out["decks"][0], deck)
    ext = res["extraction"]
    assert res["extraction_available"] is True
    assert ext["n_gold_text"] == 1  # excluded + pending recipes are not content-matched
    assert ext["n_matched"] == 1 and ext["n_pending_structured_gt"] == 1 and ext["n_excluded_gold"] == 1
    assert ext["hallucinated_recipe_rate"] is None  # p2 may be the pending recipe: rate would be inflated
    rep = score_evaluation(out, {"tier": "T3", "decks": [deck]})
    assert rep["metrics"]["extraction"]["overall"]["ingredient_f1"]["mean"] == 1.0
    assert rep["metrics"]["extraction"]["hallucinated_recipe_rate"] is None


def test_legacy_deck_unchanged_shape() -> None:
    deck = {
        "deck_id": "d01", "deck_type": "mixed",
        "images": [{"image_id": i, "modality": "TYPEWRITTEN"} for i in ("a.jpg", "b.jpg")],
        "recipes": [_texted("r001", ["a.jpg", "b.jpg"], "Fudge", ["2 cups sugar"], ["Boil."])],
    }
    out = {"tier": "T1", "status": "ok", "decks": [{"deck_id": "d01", "recipes": [
        {"pred_id": "p1", "image_ids": ["a.jpg", "b.jpg"], "confidence": 0.8,
         "recipe": {"title": "Fudge", "ingredients": [{"raw": "2 cups sugar"}], "directions": ["Boil."]}},
        {"pred_id": "p2", "image_ids": [], "confidence": 0.8,
         "recipe": {"title": "Ghost", "ingredients": [{"raw": "air"}], "directions": ["None."]}},
    ]}]}
    rep = score_evaluation(out, {"tier": "T1", "decks": [deck]})
    assert "grouping_detail" not in rep["metrics"]
    ext = rep["metrics"]["extraction"]
    assert "status" not in ext and "n_pending_structured_gt" not in ext
    assert ext["hallucinated_recipe_rate"] == 0.5 and ext["n_matched"] == 1


def test_oracle_plan_merges_overlaps_and_keeps_disjoint_legacy_order() -> None:
    plan = create_oracle_plan({"decks": [_pending_deck()]}, seed=7)
    sets = sorted(sorted(s) for s in plan["decks"][0]["sets"])
    assert sets == [["imgA.jpg", "imgB.jpg"], ["imgC.jpg"], ["imgD.jpg", "imgE.jpg"]]

    import random
    disjoint = {"decks": [{"deck_id": "d01", "recipes": [
        {"faces": ["a", "b", "c"]}, {"faces": ["d"]}, {"faces": ["e", "f"]}]}]}
    rng = random.Random(3)  # legacy algorithm: shuffle each recipe's faces, then the list of sets
    legacy = []
    for r in disjoint["decks"][0]["recipes"]:
        f = list(r["faces"])
        rng.shuffle(f)
        legacy.append(f)
    rng.shuffle(legacy)
    assert create_oracle_plan(disjoint, seed=3)["decks"][0]["sets"] == legacy


# --------------------------------------------------------------------------- harness
def test_harness_t3_resolves_once_imported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import evals.harness.run as run_mod

    d = tmp_path / "evals" / "corpora" / "t3" / "d01"
    d.mkdir(parents=True)
    for n in ("aa11.jpg", "bb22.jpg"):
        (d / n).write_bytes(b"\xff\xd8\xff\xd9")
    (d.parent / "DECKS.json").write_text(json.dumps([{"deck_id": "d01", "n_images": 2}]))
    monkeypatch.setattr(run_mod, "get_repo_root", lambda: tmp_path)
    decks = run_mod.resolve_deck_images("T3")
    assert list(decks) == ["d01"] and len(decks["d01"]) == 2


# --------------------------------------------------------------------------- curation tools (stubbed)
class _FakeTypes:
    class Part:
        @staticmethod
        def from_bytes(data: bytes, mime_type: str) -> Dict[str, Any]:
            return {"mime": mime_type, "n": len(data)}


def _patch_vault(monkeypatch: pytest.MonkeyPatch, gt: Dict[str, Any]) -> Dict[str, Any]:
    import evals.vault as v_mod

    saved: Dict[str, Any] = {}
    monkeypatch.setattr(v_mod, "decrypt_bytes", lambda b, identity_path=None: json.dumps(gt).encode())

    def _enc(b: bytes, recipients_path: Any = None) -> bytes:
        saved["payload"] = json.loads(b.decode())
        return b"NEW"

    monkeypatch.setattr(v_mod, "encrypt_bytes", _enc)
    return saved


def test_draft_from_images_with_stub_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from evals.curation_t3 import draft_structured as ds

    corpus = tmp_path / "corpus"
    (corpus / "d01").mkdir(parents=True)
    for n in ("imgA.jpg", "imgB.jpg", "imgC.jpg"):
        (corpus / "d01" / n).write_bytes(b"\xff\xd8fake\xff\xd9")
    gt = {"schema": "rb-gt/1", "tier": "T3", "decks": [{"deck_id": "d01", "recipes": [
        _pending("r001", ["imgA.jpg", "imgB.jpg"], draft_hint={"title": "Oat Drops", "note": "back continues"}),
        _pending("r002", ["imgC.jpg"], score_policy="exclude", excluded_reason="partial_fragment"),
    ]}]}
    vault_file = tmp_path / "t3.gt.enc"
    vault_file.write_bytes(b"ENC")

    # Without --from-images nothing is drafted and the client is never called.
    saved = _patch_vault(monkeypatch, gt)
    idle = MagicMock()
    code, summary = ds.draft_structured_gt(vault_file=vault_file, client=idle, corpus_dir=corpus)
    assert code == 0 and summary["needs_image_draft"] == 1 and summary["pending"] == 0
    idle.models.generate_content.assert_not_called()
    assert "payload" not in saved

    resp = MagicMock()
    resp.text = json.dumps({
        "verbatim": ["Oat Drops\n1 cup oats\n1 egg", "Drop on sheet. Bake 10 min."],
        "structured": {"title": "Oat Drops", "yield": None, "source_note": None,
                       "ingredients": [{"raw": "1 cup oats", "quantity": "1", "unit": "cup", "item": "oats", "section": None},
                                       {"raw": "1 egg", "quantity": "1", "unit": None, "item": "egg", "section": None}],
                       "directions": ["Drop on sheet.", "Bake 10 min."], "notes": []},
    })
    resp.usage_metadata = MagicMock(prompt_token_count=2000, candidates_token_count=300, thoughts_token_count=500)
    client = MagicMock()
    client.models.generate_content.return_value = resp

    import google.genai as genai_pkg  # noqa: F401  (types is imported lazily inside the drafter)
    monkeypatch.setattr(ds, "load_face_parts",
                        lambda recipe, deck_id, corpus_dir, types_mod: [
                            _FakeTypes.Part.from_bytes((Path(corpus_dir) / deck_id / f).read_bytes(), "image/jpeg")
                            for f in recipe["faces"]])
    code, summary = ds.draft_structured_gt(vault_file=vault_file, client=client, from_images=True, corpus_dir=corpus)
    assert code == 0 and summary["drafted"] == 1 and summary["drafted_from_images"] == 1 and summary["failed"] == 0
    call = client.models.generate_content.call_args
    contents = call.kwargs["contents"]
    assert isinstance(contents, list) and len(contents) == 3  # prompt + 2 face images in reading order
    assert "Oat Drops" in contents[0] and "exactly 2 string(s)" in contents[0]
    rec = saved["payload"]["decks"][0]["recipes"][0]
    assert rec["verbatim"][1].startswith("Drop on sheet")
    assert rec["structured_status"] == "drafted_unreviewed" and rec["verbatim_status"] == "drafted_unreviewed"
    assert rec["structured_source"] == "image_draft" and rec["structured"]["title"] == "Oat Drops"
    excl = saved["payload"]["decks"][0]["recipes"][1]
    assert excl["structured_status"] == "pending_draft" and excl["verbatim"] == []


def test_load_face_parts_missing_image_raises(tmp_path: Path) -> None:
    from evals.curation_t3.draft_structured import load_face_parts

    (tmp_path / "d01").mkdir()
    (tmp_path / "d01" / "ok.jpg").write_bytes(b"x")
    parts = load_face_parts({"faces": ["ok.jpg"]}, "d01", tmp_path, _FakeTypes)
    assert parts == [{"mime": "image/jpeg", "n": 1}]
    with pytest.raises(FileNotFoundError):
        load_face_parts({"faces": ["missing.jpg"]}, "d01", tmp_path, _FakeTypes)


def test_memorization_probe_skips_pending_and_keeps_curator_exclusion(tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    from evals.curation_t3.memorization_probe import run_memorization_probe

    texted = _texted("r002", ["q.jpg"], "Prune Fluff", ["1 cup prunes"], ["Whip."])
    texted["excluded_reason"] = "partial_fragment"  # curator flag must survive a CLEAN probe result
    gt = {"decks": [{"deck_id": "d01", "recipes": [_pending("r001", ["p.jpg"]), texted]}]}
    saved = _patch_vault(monkeypatch, gt)
    resp = MagicMock()
    resp.text = "Something entirely different about bread."
    resp.usage_metadata = None
    client = MagicMock()
    client.models.generate_content.return_value = resp
    vault_file = tmp_path / "v.enc"
    vault_file.write_bytes(b"x")
    code, summary = run_memorization_probe(vault_file=vault_file, client=client)
    assert code == 0 and summary["probed"] == 1 and summary["skipped_no_text_gt"] == 1
    assert client.models.generate_content.call_count == 1
    recs = saved["payload"]["decks"][0]["recipes"]
    assert recs[0].get("memorization_score") is None
    assert recs[1]["excluded_reason"] == "partial_fragment" and recs[1]["memorization_score"] is not None


def test_spotcheck_skips_excluded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    from evals.curation_t3.spotcheck import spotcheck_vault

    gt = {"schema": "rb-gt/1", "tier": "T3", "decks": [{"deck_id": "d01", "images": [], "recipes": [
        _pending("r001", ["a.jpg"]), _pending("r002", ["b.jpg"], score_policy="exclude")]}]}
    _patch_vault(monkeypatch, gt)
    vault_file = tmp_path / "v.enc"
    vault_file.write_bytes(b"x")
    spotcheck_vault(vault_file=vault_file, show_all=True)
    out = capsys.readouterr().out
    assert "Recipe ID: r001" in out and "Recipe ID: r002" not in out


# --------------------------------------------------------------------------- public corpus invariants
def _jpeg_marker_segments(data: bytes) -> List[int]:
    i, markers = 2, []
    while i + 4 <= len(data) and data[i] == 0xFF:
        m = data[i + 1]
        if m == 0xDA:
            break
        markers.append(m)
        i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return markers


def test_t3_public_corpus_invariants() -> None:
    from PIL import Image

    decks = json.loads((T3_DIR / "DECKS.json").read_text(encoding="utf-8"))
    files = sorted(p for p in (T3_DIR / "d01").iterdir() if p.is_file())
    assert decks == [{"deck_id": "d01", "n_images": len(files)}] and len(files) == 50
    total = 0
    for p in files:
        assert re.fullmatch(r"[0-9a-f]{12}\.jpg", p.name), p.name
        data = p.read_bytes()
        total += len(data)
        # No APP1 (EXIF/XMP), APP2 (ICC), APP13 (IPTC), APP14, COM segments
        assert not set(_jpeg_marker_segments(data)) & {0xE1, 0xE2, 0xED, 0xEE, 0xFE}, p.name
        with Image.open(p) as im:
            assert max(im.size) <= 2400 and not im.getexif()
    assert total <= 26_000_000


def test_t3_public_docs_do_not_reveal_grouping() -> None:
    att = (T3_DIR / "ATTRIBUTION.md").read_text(encoding="utf-8")
    readme = (T3_DIR / "README.md").read_text(encoding="utf-8")
    names = {p.name for p in (T3_DIR / "d01").iterdir()}
    rows = [ln for ln in att.splitlines() if ln.startswith("| `")]
    assert {re.match(r"\| `([^`]+)`", ln).group(1) for ln in rows} == names
    assert [r for r in rows] == sorted(rows)  # sorted by opaque filename, not by source/group order
    for text in (att, readme):
        assert not re.search(r"\br\d{3}\b", text)  # no GT recipe ids
        assert "draft_hint" not in text and "score_policy" not in text
    assert "distractor" not in att.lower()
    for n in names:  # README never maps a filename to anything
        assert n not in readme
