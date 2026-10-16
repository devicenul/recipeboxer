"""Test suite for WP-R2 (T3 Real-Web Corpus Curation & Tooling).

WP-T3A: the antique corpus curated by WP-R2 is now the ARCHIVED opt-in tier T3A
(evals/corpora/archive/t3_antique/, evals/vault/archive/t3a.gt.enc). Its encrypted GT still carries the
legacy tier label "T3". T3 itself is the modern (1900s+) tier, pending curation.

Verifies:
1. Integrity of encrypted vault GT (schema rb-gt/1, tier T3, deck_type real_distractor).
2. Correspondence between vault images and disk assets under evals/corpora/archive/t3_antique/d01/.
3. SHA-256 integrity of all images.
4. Clean pending status of structured GT and memorization probe flags.
5. Public registry invariants (DECKS.json and ATTRIBUTION.md contain no secret GT).
6. Offline behavior of draft_structured, spotcheck, and memorization_probe tools.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from evals import vault
from evals.curation_t3.draft_structured import draft_structured_gt
from evals.curation_t3.memorization_probe import run_memorization_probe
from evals.curation_t3.spotcheck import spotcheck_vault

VAULT_FILE = Path("evals/vault/archive/t3a.gt.enc")
CORPUS_DIR = Path("evals/corpora/archive/t3_antique")
DECKS_JSON = CORPUS_DIR / "DECKS.json"
ATTRIBUTION_MD = CORPUS_DIR / "ATTRIBUTION.md"


def _resolve_identity_path() -> Path:
    """Maintainer age identity: $RB_EVAL_AGE_KEY_FILE if set, else ~/.config/recipeboxer/age.key
    (same resolution order as evals.vault.load_identity, minus the explicit argument)."""
    env_val = os.environ.get(vault.ENV_AGE_KEY_FILE)
    return Path(env_val).expanduser() if env_val else vault.DEFAULT_KEY_PATH.expanduser()


@pytest.fixture(scope="module")
def identity_path() -> Path:
    """Skips maintainer-only vault tests when no age identity is available (e.g. GitHub CI)."""
    path = _resolve_identity_path()
    if not path.is_file():
        pytest.skip(
            f"Maintainer-only: T3A vault tests need the age identity at {path} "
            f"(set {vault.ENV_AGE_KEY_FILE} or create ~/.config/recipeboxer/age.key); absent in CI"
        )
    return path


@pytest.fixture(scope="module")
def decrypted_t3_gt(identity_path: Path) -> dict:
    assert VAULT_FILE.exists(), f"Vault file {VAULT_FILE} does not exist"
    ciphertext = VAULT_FILE.read_bytes()
    plaintext = vault.decrypt_bytes(ciphertext, identity_path=identity_path)
    return json.loads(plaintext.decode("utf-8"))


def test_t3_gt_schema_and_metadata(decrypted_t3_gt: dict) -> None:
    assert decrypted_t3_gt.get("schema") == "rb-gt/1"
    assert decrypted_t3_gt.get("tier") == "T3"  # legacy label of archived tier T3A (WP-T3A)
    assert "created" in decrypted_t3_gt
    assert "generator" in decrypted_t3_gt
    assert len(decrypted_t3_gt.get("decks", [])) == 1

    deck = decrypted_t3_gt["decks"][0]
    assert deck.get("deck_id") == "d01"
    assert deck.get("deck_type") == "real_distractor"


def test_t3_images_and_disk_correspondence(decrypted_t3_gt: dict) -> None:
    deck = decrypted_t3_gt["decks"][0]
    gt_images = deck.get("images", [])
    assert len(gt_images) >= 20

    deck_disk_dir = CORPUS_DIR / "d01"
    assert deck_disk_dir.is_dir()
    disk_files = sorted(p.name for p in deck_disk_dir.glob("*.jpg"))
    gt_image_ids = sorted(img["image_id"] for img in gt_images)

    assert disk_files == gt_image_ids

    total_disk_bytes = 0
    for img in gt_images:
        iid = img["image_id"]
        sha = img["sha256"]
        modality = img["modality"]
        stock_id = img["stock_id"]

        assert modality in {"HANDWRITTEN_CURSIVE", "TYPEWRITTEN", "CLIPPING", "MIXED"}
        assert stock_id.startswith("stock_")

        img_file = deck_disk_dir / iid
        data = img_file.read_bytes()
        total_disk_bytes += len(data)
        computed_sha = hashlib.sha256(data).hexdigest()
        assert computed_sha == sha

    # Check deck size <= 30 MB
    assert total_disk_bytes <= 30 * 1024 * 1024


def test_t3_recipes_structure_and_faces_mapping(decrypted_t3_gt: dict) -> None:
    deck = decrypted_t3_gt["decks"][0]
    gt_images = {img["image_id"] for img in deck.get("images", [])}
    recipes = deck.get("recipes", [])
    assert len(recipes) >= 20

    assigned_faces = set()
    multi_page_found = False

    for r in recipes:
        assert r.get("recipe_id", "").startswith("r")
        faces = r.get("faces", [])
        verbatim = r.get("verbatim", [])
        assert len(faces) >= 1
        assert len(faces) == len(verbatim)

        if len(faces) > 1:
            multi_page_found = True

        if r.get("structured") is not None:
            assert isinstance(r["structured"], dict)
            assert r.get("structured_status") in {"drafted_unreviewed", "reviewed"}
        else:
            assert r.get("structured_status") == "pending_text_only_draft"

        diff = r.get("difficulty", {})
        assert diff.get("faces") == len(faces)
        assert diff.get("legibility") in {"good", "fair", "poor"}

        for f in faces:
            assert f in gt_images
            assert f not in assigned_faces, f"Duplicate face assignment: {f}"
            assigned_faces.add(f)

    # Every image belongs to exactly one recipe
    assert assigned_faces == gt_images
    assert multi_page_found, "Expected at least one multi-page continuation pair"


def test_t3_public_decks_json() -> None:
    assert DECKS_JSON.exists()
    content = json.loads(DECKS_JSON.read_text(encoding="utf-8"))
    assert isinstance(content, list)
    assert len(content) == 1
    deck_entry = content[0]
    assert deck_entry.get("deck_id") == "d01"
    assert "n_images" in deck_entry
    assert deck_entry["n_images"] >= 20
    # Must NOT leak deck_type or GT
    assert "deck_type" not in deck_entry
    assert "recipes" not in deck_entry


def test_t3_attribution_registry() -> None:
    assert ATTRIBUTION_MD.exists()
    text = ATTRIBUTION_MD.read_text(encoding="utf-8")
    assert "# T3 Real-Web Corpus Attribution & Provenance" in text
    assert "| Opaque Image ID | Source Institution |" in text

    # Check that disk images appear in attribution table
    deck_disk_dir = CORPUS_DIR / "d01"
    for p in deck_disk_dir.glob("*.jpg"):
        assert f"`{p.name}`" in text


def test_curation_tools_offline_safety(identity_path: Path) -> None:
    # 1. Spotcheck executes without error
    spotcheck_vault(
        vault_file=VAULT_FILE,
        identity_path=identity_path,
        sample_rate=0.2,
        seed=123,
    )

    # 2. draft_structured executes cleanly in offline mode (no key)
    res_draft = draft_structured_gt(
        vault_file=VAULT_FILE,
        identity_path=identity_path,
    )
    assert res_draft == 0

    # 3. memorization_probe executes cleanly in offline mode (no key)
    res_probe = run_memorization_probe(
        vault_file=VAULT_FILE,
        identity_path=identity_path,
    )
    assert res_probe == 0
