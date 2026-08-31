"""
Empirical Stress Test & Adversarial Verification Suite for Milestone 1:
.recipeboxer Project Archive Storage Engine, Manifest Validation,
ZipSlip Defenses, SHA-256 Integrity Verification, and Merge Resolution.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from recipeboxer.agent.session import PipelineSession, SessionManager
from recipeboxer.api.app import create_app
from recipeboxer.config import Settings
from recipeboxer.ingestion.scanner import ScannedCard
from recipeboxer.models.archive import (
    ArchiveAssetRecord,
    ArchiveCardRecord,
    ArchiveRecipeRecord,
    ArchiveSequenceProposal,
    MarginaliaEntry,
    RecipeBoxerArchiveManifest,
    StepImageBinding,
)
from recipeboxer.models.domain import (
    BoundaryText,
    CardQuadrant,
    CardRole,
    InkInfo,
    PaperColorInfo,
    PaperRulingInfo,
    VisualFingerprint,
)
from recipeboxer.models.pipeline import PipelineState, TokenUsage
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.storage.archive import (
    create_recipeboxer_archive,
    extract_recipeboxer_archive,
    merge_recipeboxer_archives,
    restore_archive_to_session,
    validate_archive_manifest,
)
from recipeboxer.storage.cache import FingerprintCache
from recipeboxer.storage.session_store import SessionStore


# ============================================================================
# HELPER FIXTURES & UTILITIES
# ============================================================================

def generate_test_image(color: str = "red", size: tuple[int, int] = (100, 100)) -> bytes:
    """Generates valid JPEG image bytes with distinct colors/dimensions."""
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def workspace_env(tmp_path: Path):
    """Sets up a fully isolated environment with SQLite session store, cache, and test scans."""
    scans_dir = tmp_path / "scans"
    scans_dir.mkdir(parents=True, exist_ok=True)

    db_path = tmp_path / "cache" / "test_store.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        cache_db_path=str(db_path),
        cache_dir=str(tmp_path / "cache"),
        export_dir=str(tmp_path / "exports"),
    )
    store = SessionStore(db_path=str(db_path))
    cache = FingerprintCache(db_path=str(db_path))
    manager = SessionManager()

    yield {
        "tmp_path": tmp_path,
        "scans_dir": scans_dir,
        "settings": settings,
        "store": store,
        "cache": cache,
        "manager": manager,
    }

    store.close()
    cache.close()


@pytest.fixture
def api_test_client(workspace_env):
    """FastAPI TestClient backed by the isolated test workspace."""
    app = create_app(
        settings=workspace_env["settings"],
        session_manager=workspace_env["manager"],
        session_store=workspace_env["store"],
    )
    client = TestClient(app)
    return client, workspace_env


# ============================================================================
# 1. ZIPSLIP VULNERABILITY ADVERSARIAL STRESS SUITE
# ============================================================================

class TestZipSlipAdversarialVectors:
    """Rigorous challenge against various archive directory traversal vectors."""

    @pytest.mark.parametrize(
        "evil_filename",
        [
            "../../evil.txt",
            "../../../evil.txt",
            "..\\..\\evil.txt",
            "..\\..\\..\\evil.txt",
            "assets/scans/../../../evil.txt",
            "assets/scans/..\\..\\..\\evil.txt",
            "C:/evil.txt",
            "C:\\evil.txt",
            "D:/assets/scans/evil.txt",
            "nested/dir/../../../../../../tmp/evil.txt",
        ],
    )
    def test_zipslip_escaping_paths_rejected(self, workspace_env, evil_filename: str):
        tmp_path = workspace_env["tmp_path"]
        target_dir = tmp_path / "safe_extract"
        zip_path = tmp_path / "malicious.recipeboxer"

        manifest_data = {
            "format_version": "1.0.0",
            "project_id": "proj_malicious",
            "project_name": "ZipSlip Vector Test",
        }

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest_data))
            zf.writestr(evil_filename, b"MALICIOUS INJECTION CONTENT")

        zip_path.write_bytes(buf.getvalue())

        # Verify extraction raises ValueError due to ZipSlip detection
        with pytest.raises(ValueError, match="ZipSlip path traversal attempt|Failed to unpack"):
            extract_recipeboxer_archive(zip_path, target_dir, verify_hashes=False)

        # Confirm evil file was NOT created outside target_dir or on drive root
        assert not (tmp_path / "evil.txt").exists()
        assert not (tmp_path.parent / "evil.txt").exists()

    @pytest.mark.parametrize(
        "contained_path",
        [
            "/etc/passwd",
            "/evil.txt",
            "\\evil.txt",
            "\\Windows\\System32\\evil.dll",
            "assets/scans/../../contained_root.txt",
        ],
    )
    def test_leading_slash_and_contained_paths_do_not_escape(self, workspace_env, contained_path: str):
        tmp_path = workspace_env["tmp_path"]
        target_dir = tmp_path / "contained_extract"
        zip_path = tmp_path / "contained.recipeboxer"

        manifest_data = {
            "format_version": "1.0.0",
            "project_id": "proj_contained",
            "project_name": "Contained Path Test",
        }

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest_data))
            zf.writestr(contained_path, b"CONTAINED TEST CONTENT")

        zip_path.write_bytes(buf.getvalue())

        # Safe extraction should strip leading slashes and keep files safely confined inside target_dir
        extract_recipeboxer_archive(zip_path, target_dir, verify_hashes=False)

        # Verify absolutely nothing was written outside target_dir
        assert not (tmp_path / "evil.txt").exists()
        assert not (tmp_path / "passwd").exists()
        assert not Path("C:/evil.txt").exists()
        # Verify file was written strictly inside target_dir
        target_dir_files = list(target_dir.rglob("*"))
        for f in target_dir_files:
            assert str(f.resolve()).startswith(str(target_dir.resolve()))


# ============================================================================
# 2. SHA-256 INTEGRITY & TAMPERING DETECTION SUITE
# ============================================================================

class TestSHA256IntegrityAndTampering:
    """Verifies content-addressed SHA-256 verification and detection of tampering."""

    def test_single_byte_payload_modification_detected(self, workspace_env):
        tmp_path = workspace_env["tmp_path"]
        img_bytes = generate_test_image("blue")
        true_sha = hashlib.sha256(img_bytes).hexdigest()

        # Modify a single byte in the image payload
        tampered_bytes = img_bytes[:-1] + (b"\x00" if img_bytes[-1:] != b"\x00" else b"\x01")
        assert hashlib.sha256(tampered_bytes).hexdigest() != true_sha

        manifest = {
            "format_version": "1.0.0",
            "project_id": "proj_tamper_1",
            "project_name": "Tampered Payload Test",
            "assets": [
                {
                    "sha256_hash": true_sha,
                    "filename": "card1.jpg",
                    "archive_path": f"assets/scans/{true_sha}.jpg",
                    "mime_type": "image/jpeg",
                    "size_bytes": len(img_bytes),
                }
            ],
            "cards": [],
            "proposals": [],
            "recipes": [],
        }

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr(f"assets/scans/{true_sha}.jpg", tampered_bytes)

        target_dir = tmp_path / "tamper_extract_1"
        with pytest.raises(ValueError, match="Asset integrity check failed"):
            extract_recipeboxer_archive(buf.getvalue(), target_dir, verify_hashes=True)

    def test_missing_asset_payload_detected(self, workspace_env):
        tmp_path = workspace_env["tmp_path"]
        img_bytes = generate_test_image("green")
        true_sha = hashlib.sha256(img_bytes).hexdigest()

        # Manifest lists the asset, but it is missing from zip archive
        manifest = {
            "format_version": "1.0.0",
            "project_id": "proj_missing_asset",
            "project_name": "Missing Asset Test",
            "assets": [
                {
                    "sha256_hash": true_sha,
                    "filename": "missing.jpg",
                    "archive_path": f"assets/scans/{true_sha}.jpg",
                    "mime_type": "image/jpeg",
                    "size_bytes": len(img_bytes),
                }
            ],
            "cards": [],
            "proposals": [],
            "recipes": [],
        }

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))

        target_dir = tmp_path / "tamper_extract_2"
        with pytest.raises(ValueError, match="Missing asset file in extracted archive"):
            extract_recipeboxer_archive(buf.getvalue(), target_dir, verify_hashes=True)

    def test_manifest_hash_case_insensitivity(self, workspace_env):
        tmp_path = workspace_env["tmp_path"]
        img_bytes = generate_test_image("yellow")
        true_sha_lower = hashlib.sha256(img_bytes).hexdigest()
        true_sha_upper = true_sha_lower.upper()

        manifest = {
            "format_version": "1.0.0",
            "project_id": "proj_upper_hash",
            "project_name": "Upper Hash Test",
            "assets": [
                {
                    "sha256_hash": true_sha_upper,
                    "filename": "yellow.jpg",
                    "archive_path": f"assets/scans/{true_sha_lower}.jpg",
                    "mime_type": "image/jpeg",
                    "size_bytes": len(img_bytes),
                }
            ],
            "cards": [],
            "proposals": [],
            "recipes": [],
        }

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr(f"assets/scans/{true_sha_lower}.jpg", img_bytes)

        target_dir = tmp_path / "upper_extract"
        manifest_obj, out_path = extract_recipeboxer_archive(buf.getvalue(), target_dir, verify_hashes=True)
        assert len(manifest_obj.assets) == 1
        assert (out_path / f"assets/scans/{true_sha_lower}.jpg").is_file()

    def test_corrupted_manifest_json_syntax_rejected(self, workspace_env):
        tmp_path = workspace_env["tmp_path"]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", b"{INVALID JSON SYNTAX :::: ")

        target_dir = tmp_path / "bad_json_extract"
        with pytest.raises(ValueError):
            extract_recipeboxer_archive(buf.getvalue(), target_dir, verify_hashes=False)

    def test_missing_manifest_root_file_rejected(self, workspace_env):
        tmp_path = workspace_env["tmp_path"]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("not_manifest.txt", b"something else")

        target_dir = tmp_path / "no_manifest_extract"
        with pytest.raises(ValueError, match="Archive is missing required 'manifest.json'"):
            extract_recipeboxer_archive(buf.getvalue(), target_dir, verify_hashes=False)


# ============================================================================
# 3. EMPTY SESSION PERSISTENCE & MERGE SUITE
# ============================================================================

class TestEmptySessionScenarios:
    """Stress tests archive engine on empty sessions with zero cards and zero recipes."""

    def test_empty_session_archive_roundtrip(self, workspace_env):
        store: SessionStore = workspace_env["store"]
        manager: SessionManager = workspace_env["manager"]
        cache: FingerprintCache = workspace_env["cache"]

        sid = "sess_empty_01"
        session = PipelineSession(session_id=sid, cache=cache)
        session.state = PipelineState.IDLE
        session.cards = []
        session.recipes = []
        manager.register_session(session)
        store.save_pipeline_session(session)

        # 1. Create archive for empty session
        archive_bytes = create_recipeboxer_archive(
            session_or_id=session,
            store=store,
            manager=manager,
            cache=cache,
            project_name="Empty Project",
        )
        assert len(archive_bytes) > 0

        # 2. Extract archive
        target_dir = workspace_env["tmp_path"] / "empty_extracted"
        manifest, out_path = extract_recipeboxer_archive(archive_bytes, target_dir, verify_hashes=True)
        assert manifest.project_name == "Empty Project"
        assert len(manifest.assets) == 0
        assert len(manifest.cards) == 0
        assert len(manifest.recipes) == 0

        # 3. Restore archive to new session
        restored_detail = restore_archive_to_session(
            archive_file_or_bytes=archive_bytes,
            store=store,
            manager=manager,
            cache=cache,
            target_session_id="sess_empty_restored",
        )
        assert restored_detail.session_id == "sess_empty_restored"
        assert restored_detail.card_count == 0
        assert restored_detail.recipe_count == 0

    def test_merge_empty_archive_with_populated_archive(self, workspace_env):
        store: SessionStore = workspace_env["store"]
        manager: SessionManager = workspace_env["manager"]
        cache: FingerprintCache = workspace_env["cache"]
        scans_dir: Path = workspace_env["scans_dir"]

        # Populated base session
        card1_file = scans_dir / "card_pop.jpg"
        card1_file.write_bytes(generate_test_image("orange"))

        sess_base = PipelineSession(session_id="sess_base_pop", cache=cache)
        sess_base.cards = [ScannedCard(card_id="card_pop.jpg", file_path=card1_file, filename="card_pop.jpg")]
        sess_base.recipes = [
            RecipeSchema(
                recipe_id="rec_pop",
                title="Orange Cake",
                ingredients=["Oranges", "Flour"],
                directions=["1. Bake."],
                card_ids=["card_pop.jpg"],
            )
        ]
        base_bytes = create_recipeboxer_archive(sess_base, store=store, manager=manager, cache=cache)

        # Empty incoming session
        sess_empty = PipelineSession(session_id="sess_inc_empty", cache=cache)
        sess_empty.cards = []
        sess_empty.recipes = []
        empty_bytes = create_recipeboxer_archive(sess_empty, store=store, manager=manager, cache=cache)

        # Merge empty into base
        merged_manifest, merged_bytes = merge_recipeboxer_archives(
            base_archive=base_bytes,
            incoming_archive_or_session=empty_bytes,
            conflict_strategy="keep_both",
            store=store,
            manager=manager,
            cache=cache,
        )

        assert len(merged_manifest.assets) == 1
        assert len(merged_manifest.recipes) == 1
        assert merged_manifest.recipes[0].title == "Orange Cake"

        # Merge base into empty
        merged_manifest_2, _ = merge_recipeboxer_archives(
            base_archive=empty_bytes,
            incoming_archive_or_session=base_bytes,
            conflict_strategy="keep_both",
            store=store,
            manager=manager,
            cache=cache,
        )
        assert len(merged_manifest_2.assets) == 1
        assert len(merged_manifest_2.recipes) == 1
        assert merged_manifest_2.recipes[0].title == "Orange Cake"


# ============================================================================
# 4. LARGE MULTI-CARD MULTI-STEP ARCHIVE FIDELITY SUITE
# ============================================================================

class TestLargeMultiCardMultiStepFidelity:
    """Stress tests high card counts, complex step-bindings, and rich marginalia notes."""

    def test_20_card_multi_recipe_bindings_and_marginalia_roundtrip(self, workspace_env):
        store: SessionStore = workspace_env["store"]
        manager: SessionManager = workspace_env["manager"]
        cache: FingerprintCache = workspace_env["cache"]
        scans_dir: Path = workspace_env["scans_dir"]

        cards: List[ScannedCard] = []
        card_sha_map: Dict[str, str] = {}
        fingerprints: Dict[str, VisualFingerprint] = {}

        # Create 20 distinct cards
        for i in range(1, 21):
            cid = f"card_{i:03d}.jpg"
            cpath = scans_dir / cid
            # Distinct image bytes
            img_bytes = generate_test_image(color=(i * 10 % 255, (255 - i * 10) % 255, (i * 25) % 255))
            cpath.write_bytes(img_bytes)
            csha = hashlib.sha256(img_bytes).hexdigest()
            card_sha_map[cid] = csha

            sc = ScannedCard(
                card_id=cid,
                file_path=cpath,
                filename=cid,
                sha256_hash=csha,
                file_size_bytes=len(img_bytes),
                width=100,
                height=100,
            )
            cards.append(sc)

            fp = VisualFingerprint(
                card_id=cid,
                image_path=str(cpath),
                sha256_hash=csha,
                role=CardRole.SEQUENCE_START if i % 2 == 1 else CardRole.SEQUENCE_CONTINUATION,
                role_confidence=0.98,
                paper_color=PaperColorInfo(primary_name="off-white", hex_code="#fcfbf7"),
                paper_ruling=PaperRulingInfo(ruled=True, line_spacing_mm=7.1),
                ink=InkInfo(primary_type="ballpoint", primary_color="blue"),
                header_text=BoundaryText(text=f"Header Note for Card {i}"),
                footer_text=BoundaryText(text=f"Page {i} of collection"),
            )
            fingerprints[cid] = fp
            cache.put(fp)

        # Build 5 recipes with multi-step bindings and marginalia
        recipes: List[ArchiveRecipeRecord] = []
        for r_idx in range(1, 6):
            r_card_ids = [f"card_{(r_idx*4 - 3):03d}.jpg", f"card_{(r_idx*4 - 2):03d}.jpg"]
            bindings = [
                StepImageBinding(
                    step_index=1,
                    card_id=r_card_ids[0],
                    card_sha256=card_sha_map[r_card_ids[0]],
                    bounding_box=[0.1, 0.1, 0.4, 0.9],
                    marginalia_note="Stir vigorously clockwise.",
                ),
                StepImageBinding(
                    step_index=2,
                    card_id=r_card_ids[1],
                    card_sha256=card_sha_map[r_card_ids[1]],
                    bounding_box=[0.5, 0.1, 0.9, 0.9],
                    marginalia_note="Bake until golden brown.",
                ),
            ]
            marginalia = [
                MarginaliaEntry(
                    id=f"marg_{r_idx}_1",
                    card_id=r_card_ids[0],
                    card_sha256=card_sha_map[r_card_ids[0]],
                    text=f"Grandma's favorite holiday treat #{r_idx}",
                    location_quadrant=CardQuadrant.TOP_RIGHT,
                    category="family_story",
                    target_field="source_notes",
                ),
                MarginaliaEntry(
                    id=f"marg_{r_idx}_2",
                    card_id=r_card_ids[1],
                    card_sha256=card_sha_map[r_card_ids[1]],
                    text="Can substitute honey for maple syrup.",
                    location_quadrant=CardQuadrant.BOTTOM_LEFT,
                    category="culinary_tweak",
                    target_field="directions[2]",
                ),
            ]

            recipe = ArchiveRecipeRecord(
                recipe_id=f"rec_complex_{r_idx:02d}",
                title=f"Complex Heirloom Recipe #{r_idx}",
                description=f"Description for recipe {r_idx}",
                yield_servings=f"{r_idx * 2} servings",
                prep_time="25 mins",
                cook_time="45 mins",
                total_time="1 hr 10 mins",
                ingredients=[f"Ingredient A{r_idx}", f"Ingredient B{r_idx}", f"Ingredient C{r_idx}"],
                directions=[f"1. Step one for {r_idx}", f"2. Step two for {r_idx}"],
                tags=["Heirloom", "Baking", f"Batch{r_idx}"],
                source_notes=f"Source note for recipe {r_idx}",
                card_ids=r_card_ids,
                step_bindings=bindings,
                marginalia=marginalia,
            )
            recipes.append(recipe)

        # Build proposals
        proposals: List[ArchiveSequenceProposal] = []
        for p_idx in range(1, 6):
            c_front = f"card_{(p_idx*4 - 3):03d}.jpg"
            c_back = f"card_{(p_idx*4 - 2):03d}.jpg"
            prop = ArchiveSequenceProposal(
                proposal_id=f"prop_pair_{p_idx}",
                ordered_card_ids=[c_front, c_back],
                part_count=2,
                confidence=0.94,
                heuristic_score=0.92,
                semantic_score=0.96,
                continuity_explanation="Strong visual ruling and semantic continuation.",
                decision="APPROVE",
                notes="Approved during review.",
            )
            proposals.append(prop)

        # Build session
        sid = "sess_large_complex_01"
        session = PipelineSession(session_id=sid, cache=cache)
        session.state = PipelineState.COMPLETED
        session.cards = cards
        session.fingerprints = fingerprints
        session.recipes = recipes
        session.proposals = proposals
        session.token_usage = TokenUsage(prompt_tokens=5000, completion_tokens=1500, total_tokens=6500)
        manager.register_session(session)
        store.save_pipeline_session(session)

        # 1. Package Archive
        archive_bytes = create_recipeboxer_archive(
            session_or_id=session,
            store=store,
            manager=manager,
            cache=cache,
            project_name="Large Heirloom Archive",
        )
        assert len(archive_bytes) > 0

        # 2. Extract Archive
        target_dir = workspace_env["tmp_path"] / "large_extracted"
        manifest, out_path = extract_recipeboxer_archive(archive_bytes, target_dir, verify_hashes=True)

        assert manifest.project_name == "Large Heirloom Archive"
        assert len(manifest.assets) == 20
        assert len(manifest.cards) == 20
        assert len(manifest.recipes) == 5
        assert len(manifest.proposals) == 5

        # Check step bindings & marginalia fidelity
        for r_rec in manifest.recipes:
            assert len(r_rec.step_bindings) == 2
            assert r_rec.step_bindings[0].step_index == 1
            assert r_rec.step_bindings[0].bounding_box == [0.1, 0.1, 0.4, 0.9]
            assert len(r_rec.marginalia) == 2
            assert "Grandma's favorite holiday treat" in r_rec.marginalia[0].text

        # 3. Restore to new session
        restored_sid = "sess_large_restored_01"
        restored_detail = restore_archive_to_session(
            archive_file_or_bytes=archive_bytes,
            store=store,
            manager=manager,
            cache=cache,
            target_session_id=restored_sid,
        )

        assert restored_detail.session_id == restored_sid
        assert restored_detail.card_count == 20
        assert restored_detail.recipe_count == 5

        # Query restored database records directly
        db_recipes = store.list_recipes(restored_sid)
        assert len(db_recipes) == 5
        assert db_recipes[0].title.startswith("Complex Heirloom Recipe")


# ============================================================================
# 5. MISSING IMAGE FALLBACK RESILIENCE SUITE
# ============================================================================

class TestMissingImageFallbackResilience:
    """Verifies that missing scan files on disk fallback to 1x1 placeholder JPEG without crashing."""

    def test_missing_card_image_on_disk_uses_placeholder_jpeg(self, workspace_env):
        store: SessionStore = workspace_env["store"]
        manager: SessionManager = workspace_env["manager"]
        cache: FingerprintCache = workspace_env["cache"]

        sid = "sess_missing_img_01"
        sess = PipelineSession(session_id=sid, cache=cache)
        sess.state = PipelineState.COMPLETED

        # Card file does NOT exist on disk
        ghost_path = workspace_env["tmp_path"] / "nonexistent_scan.jpg"
        assert not ghost_path.exists()

        sc = ScannedCard(
            card_id="card_ghost.jpg",
            file_path=ghost_path,
            filename="card_ghost.jpg",
        )
        sess.cards = [sc]
        sess.recipes = [
            RecipeSchema(
                recipe_id="rec_ghost",
                title="Ghost Recipe",
                ingredients=["Spices"],
                directions=["1. Stir quietly."],
                card_ids=["card_ghost.jpg"],
            )
        ]
        manager.register_session(sess)

        # Packing should NOT crash with FileNotFoundError; should use fallback 1x1 JPEG
        archive_bytes = create_recipeboxer_archive(sess, store=store, manager=manager, cache=cache)
        assert len(archive_bytes) > 0

        # Unpack and verify integrity of fallback image
        target_dir = workspace_env["tmp_path"] / "ghost_extracted"
        manifest, out_path = extract_recipeboxer_archive(archive_bytes, target_dir, verify_hashes=True)
        assert len(manifest.assets) == 1
        asset = manifest.assets[0]

        # Verify extracted image file exists and matches its computed SHA-256
        extracted_img_file = out_path / asset.archive_path
        assert extracted_img_file.is_file()
        assert hashlib.sha256(extracted_img_file.read_bytes()).hexdigest() == asset.sha256_hash


# ============================================================================
# 6. MERGE CONFLICT RESOLUTION STRATEGIES SUITE
# ============================================================================

class TestMergeConflictStrategies:
    """Tests keep_both, prefer_incoming, prefer_base, and card ID collision remapping."""

    @pytest.fixture
    def setup_merge_conflict_archives(self, workspace_env):
        store: SessionStore = workspace_env["store"]
        manager: SessionManager = workspace_env["manager"]
        cache: FingerprintCache = workspace_env["cache"]
        scans_dir: Path = workspace_env["scans_dir"]

        # Base card and recipe
        card_a_file = scans_dir / "card_base.jpg"
        card_a_bytes = generate_test_image("blue")
        card_a_file.write_bytes(card_a_bytes)
        card_a_sha = hashlib.sha256(card_a_bytes).hexdigest()

        sess_base = PipelineSession(session_id="sess_base_conflict", cache=cache)
        sess_base.cards = [ScannedCard(card_id="card_shared.jpg", file_path=card_a_file, filename="card_shared.jpg", sha256_hash=card_a_sha)]
        sess_base.recipes = [
            RecipeSchema(
                recipe_id="rec_conflict_01",
                title="Chocolate Chip Cookies",
                description="Base recipe: 1 cup sugar, semi-sweet chips",
                ingredients=["2 cups flour", "1 cup white sugar", "1 cup semi-sweet chips"],
                directions=["1. Cream butter and sugar.", "2. Mix in chips.", "3. Bake at 375F."],
                card_ids=["card_shared.jpg"],
            )
        ]
        base_bytes = create_recipeboxer_archive(sess_base, store=store, manager=manager, cache=cache)

        # Incoming card (DIFFERENT image with SAME card_id) and modified recipe (SAME recipe_id and title)
        card_b_file = scans_dir / "card_incoming.jpg"
        card_b_bytes = generate_test_image("red")  # Different image bytes!
        card_b_file.write_bytes(card_b_bytes)
        card_b_sha = hashlib.sha256(card_b_bytes).hexdigest()

        sess_incoming = PipelineSession(session_id="sess_incoming_conflict", cache=cache)
        sess_incoming.cards = [ScannedCard(card_id="card_shared.jpg", file_path=card_b_file, filename="card_shared.jpg", sha256_hash=card_b_sha)]
        sess_incoming.recipes = [
            RecipeSchema(
                recipe_id="rec_conflict_01",
                title="Chocolate Chip Cookies",
                description="Incoming recipe: 1 cup brown sugar, dark chocolate chunks",
                ingredients=["2 cups flour", "1 cup dark brown sugar", "1 cup dark chocolate chunks"],
                directions=["1. Cream butter and dark brown sugar.", "2. Fold in dark chocolate.", "3. Bake at 350F."],
                card_ids=["card_shared.jpg"],
            ),
            RecipeSchema(
                recipe_id="rec_new_02",
                title="Fudge Brownies",
                description="New unique recipe",
                ingredients=["Cocoa", "Butter", "Eggs"],
                directions=["1. Mix.", "2. Bake."],
                card_ids=["card_shared.jpg"],
            ),
        ]
        incoming_bytes = create_recipeboxer_archive(sess_incoming, store=store, manager=manager, cache=cache)

        return {
            "base_bytes": base_bytes,
            "incoming_bytes": incoming_bytes,
            "card_a_sha": card_a_sha,
            "card_b_sha": card_b_sha,
            "store": store,
            "manager": manager,
            "cache": cache,
        }

    def test_conflict_strategy_keep_both(self, setup_merge_conflict_archives):
        ctx = setup_merge_conflict_archives
        manifest, merged_bytes = merge_recipeboxer_archives(
            base_archive=ctx["base_bytes"],
            incoming_archive_or_session=ctx["incoming_bytes"],
            conflict_strategy="keep_both",
            store=ctx["store"],
            manager=ctx["manager"],
            cache=ctx["cache"],
        )

        # Both assets should be kept (different SHA)
        assert len(manifest.assets) == 2

        # 3 recipes total: original base, incoming renamed duplicate, and incoming new recipe
        assert len(manifest.recipes) == 3
        recipe_ids = {r.recipe_id for r in manifest.recipes}
        assert len(recipe_ids) == 3  # All unique IDs
        assert "rec_conflict_01" in recipe_ids
        assert "rec_new_02" in recipe_ids

        # Descriptions show both variants are present
        descriptions = [r.description for r in manifest.recipes]
        assert any("Base recipe" in d for d in descriptions)
        assert any("Incoming recipe" in d for d in descriptions)
        assert any("New unique recipe" in d for d in descriptions)

        # Verify card remapping on incoming card collision
        card_ids = [c.card_id for c in manifest.cards]
        assert "card_shared.jpg" in card_ids
        assert any(c.startswith("card_shared.jpg_") for c in card_ids)

    def test_conflict_strategy_prefer_incoming(self, setup_merge_conflict_archives):
        ctx = setup_merge_conflict_archives
        manifest, merged_bytes = merge_recipeboxer_archives(
            base_archive=ctx["base_bytes"],
            incoming_archive_or_session=ctx["incoming_bytes"],
            conflict_strategy="prefer_incoming",
            store=ctx["store"],
            manager=ctx["manager"],
            cache=ctx["cache"],
        )

        # 2 recipes total: incoming replaced base, plus the new recipe
        assert len(manifest.recipes) == 2
        titles = {r.title for r in manifest.recipes}
        assert "Chocolate Chip Cookies" in titles
        assert "Fudge Brownies" in titles

        cookie_recipe = next(r for r in manifest.recipes if r.title == "Chocolate Chip Cookies")
        assert "Incoming recipe: 1 cup brown sugar" in cookie_recipe.description

    def test_conflict_strategy_prefer_base(self, setup_merge_conflict_archives):
        ctx = setup_merge_conflict_archives
        manifest, merged_bytes = merge_recipeboxer_archives(
            base_archive=ctx["base_bytes"],
            incoming_archive_or_session=ctx["incoming_bytes"],
            conflict_strategy="prefer_base",
            store=ctx["store"],
            manager=ctx["manager"],
            cache=ctx["cache"],
        )

        # 2 recipes total: base preserved unchanged, plus the new recipe
        assert len(manifest.recipes) == 2
        cookie_recipe = next(r for r in manifest.recipes if r.title == "Chocolate Chip Cookies")
        assert "Base recipe: 1 cup sugar" in cookie_recipe.description


# ============================================================================
# 7. REST API ENDPOINTS INTEGRATION & ERROR HANDLING SUITE
# ============================================================================

class TestArchiveEndpointsAdversarialAndEdgeCases:
    """Tests REST endpoints against edge cases, invalid payloads, and security inputs."""

    def test_export_endpoint_sanitizes_dangerous_filenames(self, api_test_client):
        client, ws = api_test_client
        store = ws["store"]
        manager = ws["manager"]

        sid = "sess_export_sec_01"
        sess = PipelineSession(session_id=sid, cache=ws["cache"])
        sess.state = PipelineState.COMPLETED
        sess.recipes = [
            RecipeSchema(
                recipe_id="rec_01",
                title="Apple Crisp",
                ingredients=["Apples"],
                directions=["Bake."],
            )
        ]
        manager.register_session(sess)
        store.save_pipeline_session(sess)

        # Inject dangerous filename parameters
        evil_name = "../../etc/passwd;malicious<script>"
        resp = client.get(f"/api/project/export?session_id={sid}&filename={evil_name}")
        assert resp.status_code == 200
        cd_header = resp.headers.get("content-disposition", "")
        assert ".." not in cd_header
        assert "<script>" not in cd_header
        assert ";" not in cd_header or "filename=" in cd_header

    def test_import_endpoint_rejects_corrupted_zip_payloads(self, api_test_client):
        client, _ = api_test_client

        files = {
            "file": ("corrupt.recipeboxer", b"\x00\x01\x02\x03\x04NOT_A_ZIP", "application/x-recipeboxer")
        }
        resp = client.post("/api/project/import", files=files)
        assert resp.status_code in (400, 422)

    def test_import_endpoint_rejects_zipslip_payload(self, api_test_client):
        client, _ = api_test_client

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"format_version": "1.0.0", "project_id": "p1"}))
            zf.writestr("../../evil_import.txt", b"evil exploit")

        files = {
            "file": ("evil_zipslip.recipeboxer", buf.getvalue(), "application/x-recipeboxer")
        }
        resp = client.post("/api/project/import", files=files)
        assert resp.status_code in (400, 422)

    def test_merge_endpoint_rejects_non_zip_base_archive(self, api_test_client):
        client, _ = api_test_client

        files = {
            "base_archive": ("base.recipeboxer", b"NOT_ZIP", "application/x-recipeboxer"),
            "incoming_archive": ("inc.recipeboxer", b"NOT_ZIP", "application/x-recipeboxer"),
        }
        resp = client.post("/api/project/merge", files=files)
        assert resp.status_code in (400, 422)


# ============================================================================
# 8. EXTENDED DEEP EDGE CASES & ERROR PATH STRESS
# ============================================================================

class TestExtendedAdversarialEdgeCases:
    """Stress tests deep edge cases: missing assets in restore, invalid session IDs in merge, and large scale collisions."""

    def test_merge_with_nonexistent_session_id_raises_valueerror(self, workspace_env):
        store: SessionStore = workspace_env["store"]
        manager: SessionManager = workspace_env["manager"]
        cache: FingerprintCache = workspace_env["cache"]

        # Valid base archive
        sess_base = PipelineSession(session_id="sess_base_valid", cache=cache)
        base_bytes = create_recipeboxer_archive(sess_base, store=store, manager=manager, cache=cache)

        # Merge with non-existent incoming session_id
        with pytest.raises(ValueError, match="Session 'sess_does_not_exist' not found"):
            merge_recipeboxer_archives(
                base_archive=base_bytes,
                incoming_archive_or_session="sess_does_not_exist",
                store=store,
                manager=manager,
                cache=cache,
            )

    def test_restore_archive_with_card_referencing_unlisted_asset(self, workspace_env):
        store: SessionStore = workspace_env["store"]
        manager: SessionManager = workspace_env["manager"]
        cache: FingerprintCache = workspace_env["cache"]
        tmp_path = workspace_env["tmp_path"]

        # Card record has SHA that is NOT in manifest.assets
        manifest = {
            "format_version": "1.0.0",
            "project_id": "proj_unlisted_asset",
            "project_name": "Unlisted Asset Test",
            "assets": [],
            "cards": [
                {
                    "card_id": "orphan_card.jpg",
                    "sha256_hash": "deadbeef" * 8,
                    "filename": "orphan_card.jpg",
                    "role": "STANDALONE",
                }
            ],
            "proposals": [],
            "recipes": [],
        }

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))

        # Restore should gracefully handle unlisted asset without failing or crashing
        restored = restore_archive_to_session(
            archive_file_or_bytes=buf.getvalue(),
            store=store,
            manager=manager,
            cache=cache,
            target_session_id="sess_orphan_test",
        )
        assert restored.session_id == "sess_orphan_test"
        assert restored.card_count == 0

    def test_large_scale_card_id_collision_remapping(self, workspace_env):
        store: SessionStore = workspace_env["store"]
        manager: SessionManager = workspace_env["manager"]
        cache: FingerprintCache = workspace_env["cache"]
        scans_dir = workspace_env["scans_dir"]

        # Create base archive with 10 cards named card_01.jpg .. card_10.jpg
        base_cards = []
        for i in range(1, 11):
            fn = f"card_{i:02d}.jpg"
            fp_p = scans_dir / f"base_{fn}"
            b = generate_test_image(color=(i * 10, 0, 0))
            fp_p.write_bytes(b)
            base_cards.append(ScannedCard(card_id=fn, file_path=fp_p, filename=fn, sha256_hash=hashlib.sha256(b).hexdigest()))

        sess_base = PipelineSession(session_id="sess_col_base", cache=cache)
        sess_base.cards = base_cards
        base_bytes = create_recipeboxer_archive(sess_base, store=store, manager=manager, cache=cache)

        # Create incoming archive with SAME 10 card names but DIFFERENT image bytes
        inc_cards = []
        for i in range(1, 11):
            fn = f"card_{i:02d}.jpg"
            fp_p = scans_dir / f"inc_{fn}"
            b = generate_test_image(color=(0, i * 10, 0))
            fp_p.write_bytes(b)
            inc_cards.append(ScannedCard(card_id=fn, file_path=fp_p, filename=fn, sha256_hash=hashlib.sha256(b).hexdigest()))

        sess_inc = PipelineSession(session_id="sess_col_inc", cache=cache)
        sess_inc.cards = inc_cards
        sess_inc.recipes = [
            RecipeSchema(
                recipe_id=f"rec_col_{i}",
                title=f"Recipe Collided {i}",
                ingredients=["Ingredient"],
                directions=["Step 1"],
                card_ids=[f"card_{i:02d}.jpg"],
            )
            for i in range(1, 11)
        ]
        inc_bytes = create_recipeboxer_archive(sess_inc, store=store, manager=manager, cache=cache)

        # Merge
        merged_manifest, _ = merge_recipeboxer_archives(
            base_archive=base_bytes,
            incoming_archive_or_session=inc_bytes,
            conflict_strategy="keep_both",
            store=store,
            manager=manager,
            cache=cache,
        )

        # All 20 assets should be preserved (10 base + 10 incoming)
        assert len(merged_manifest.assets) == 20
        assert len(merged_manifest.cards) == 20
        # Card IDs for incoming should all be uniquely renamed
        card_id_set = {c.card_id for c in merged_manifest.cards}
        assert len(card_id_set) == 20
        # Recipes should reflect remapped card IDs
        for r in merged_manifest.recipes:
            assert r.card_ids[0] in card_id_set
            assert r.card_ids[0].startswith("card_")
