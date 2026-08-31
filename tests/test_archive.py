"""Comprehensive unit, integration, and security test suite for RecipeBoxer portable archive (.recipeboxer)."""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

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
from recipeboxer.models.domain import CardRole, VisualFingerprint, PaperColorInfo, PaperRulingInfo, InkInfo, BoundaryText
from recipeboxer.models.pipeline import PipelineState
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


@pytest.fixture
def sample_jpeg_bytes() -> bytes:
    # Standard 1x1 valid JPEG
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c"
        b"\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c"
        b"\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01"
        b"\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01"
        b"\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06"
        b"\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
    )


@pytest.fixture
def temp_workspace(tmp_path: Path, sample_jpeg_bytes: bytes):
    scans_dir = tmp_path / "scans"
    scans_dir.mkdir(parents=True, exist_ok=True)
    card1_path = scans_dir / "card_001.jpg"
    card2_path = scans_dir / "card_002.jpg"
    card1_path.write_bytes(sample_jpeg_bytes)
    # Alter second card byte slightly to generate distinct hash
    card2_bytes = sample_jpeg_bytes[:-2] + b"\x01\xff\xd9"
    card2_path.write_bytes(card2_bytes)

    db_path = tmp_path / "cache" / "test.db"
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
        "card1_path": card1_path,
        "card2_path": card2_path,
        "settings": settings,
        "store": store,
        "cache": cache,
        "manager": manager,
    }

    store.close()
    cache.close()


class TestArchiveModels:
    def test_manifest_schema_serialization_round_trip(self):
        asset = ArchiveAssetRecord(
            sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            filename="front.jpg",
            archive_path="assets/scans/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855.jpg",
            size_bytes=1024,
            width=800,
            height=600,
        )
        card = ArchiveCardRecord(
            card_id="front.jpg",
            sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            filename="front.jpg",
            role="SEQUENCE_START",
            role_confidence=0.95,
        )
        proposal = ArchiveSequenceProposal(
            proposal_id="prop_01",
            ordered_card_ids=["front.jpg"],
            confidence=0.92,
            continuity_explanation="Single standalone card",
        )
        recipe = ArchiveRecipeRecord(
            recipe_id="rec_01",
            title="Old Fashioned Sugar Cookies",
            description="Classic family recipe",
            ingredients=["2 cups flour", "1 cup sugar", "1/2 cup butter"],
            directions=["1. Cream butter and sugar.", "2. Mix in flour.", "3. Bake at 350F for 10 mins."],
            card_ids=["front.jpg"],
            step_bindings=[
                StepImageBinding(
                    step_index=1,
                    card_id="front.jpg",
                    card_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                )
            ],
            marginalia=[
                MarginaliaEntry(
                    id="marg_01",
                    card_id="front.jpg",
                    card_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                    text="Use extra vanilla!",
                    target_field="source_notes",
                )
            ],
        )

        manifest = RecipeBoxerArchiveManifest(
            project_id="proj_sugar_cookies",
            project_name="Sugar Cookies Project",
            assets=[asset],
            cards=[card],
            proposals=[proposal],
            recipes=[recipe],
        )

        json_str = manifest.model_dump_json(indent=2)
        assert "proj_sugar_cookies" in json_str
        assert "Old Fashioned Sugar Cookies" in json_str

        # Validate deserialization
        validated = validate_archive_manifest(json_str)
        assert validated.project_id == "proj_sugar_cookies"
        assert len(validated.assets) == 1
        assert len(validated.cards) == 1
        assert len(validated.recipes) == 1
        assert validated.recipes[0].marginalia[0].text == "Use extra vanilla!"


class TestArchiveStorageEngine:
    def test_create_and_extract_archive(self, temp_workspace):
        store: SessionStore = temp_workspace["store"]
        manager: SessionManager = temp_workspace["manager"]
        cache: FingerprintCache = temp_workspace["cache"]
        card1: Path = temp_workspace["card1_path"]
        card2: Path = temp_workspace["card2_path"]

        sid = "sess_test_archive_01"
        session = PipelineSession(session_id=sid, cache=cache)
        session.state = PipelineState.COMPLETED
        sc1 = ScannedCard(card_id="card_001.jpg", file_path=card1, filename="card_001.jpg")
        sc2 = ScannedCard(card_id="card_002.jpg", file_path=card2, filename="card_002.jpg")
        session.cards = [sc1, sc2]

        fp1 = VisualFingerprint(
            card_id="card_001.jpg",
            image_path=str(card1),
            sha256_hash=cache.compute_hash(card1),
            role=CardRole.SEQUENCE_START,
        )
        session.fingerprints["card_001.jpg"] = fp1
        cache.put(fp1)

        recipe = RecipeSchema(
            recipe_id="rec_pie_01",
            title="Rustic Apple Pie",
            description="Heirloom apple pie",
            ingredients=["6 apples", "1 cup sugar", "2 pie crusts"],
            directions=["1. Peel apples.", "2. Place in crust and bake."],
            card_ids=["card_001.jpg", "card_002.jpg"],
            source_notes="Grandma's secret recipe",
        )
        session.recipes = [recipe]
        manager.register_session(session)
        store.save_pipeline_session(session)

        # Create archive
        archive_bytes = create_recipeboxer_archive(
            session_or_id=session,
            store=store,
            manager=manager,
            cache=cache,
            project_name="Apple Pie Project",
        )

        assert len(archive_bytes) > 0

        # Verify ZIP internal layout
        with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as zf:
            names = zf.namelist()
            assert "manifest.json" in names
            assert any(n.startswith("assets/scans/") for n in names)

        # Extract archive safely
        extract_dir = temp_workspace["tmp_path"] / "extracted"
        manifest, out_path = extract_recipeboxer_archive(
            archive_file_or_bytes=archive_bytes,
            target_dir=extract_dir,
            verify_hashes=True,
        )

        assert manifest.project_name == "Apple Pie Project"
        assert len(manifest.assets) == 2
        assert len(manifest.recipes) == 1
        assert manifest.recipes[0].title == "Rustic Apple Pie"

    def test_extract_archive_detects_corrupt_asset_hash(self, temp_workspace):
        tmp_path = temp_workspace["tmp_path"]
        bad_zip_path = tmp_path / "corrupted.recipeboxer"

        manifest_data = {
            "format_version": "1.0.0",
            "app_version": "1.0.0",
            "schema_version": "1.0.0",
            "project_id": "proj_corrupt",
            "project_name": "Corrupt Project",
            "assets": [
                {
                    "sha256_hash": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
                    "filename": "fake.jpg",
                    "archive_path": "assets/scans/fake.jpg",
                    "mime_type": "image/jpeg",
                    "size_bytes": 12,
                }
            ],
            "cards": [],
            "proposals": [],
            "recipes": [],
        }

        with zipfile.ZipFile(bad_zip_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest_data))
            zf.writestr("assets/scans/fake.jpg", b"corrupted bytes")

        target_dir = tmp_path / "bad_extract"
        with pytest.raises(ValueError, match="Asset integrity check failed"):
            extract_recipeboxer_archive(bad_zip_path, target_dir, verify_hashes=True)

    def test_safe_extract_zipslip_protection(self, temp_workspace):
        tmp_path = temp_workspace["tmp_path"]
        zipslip_path = tmp_path / "zipslip.recipeboxer"

        manifest_data = {
            "format_version": "1.0.0",
            "project_id": "proj_slip",
            "project_name": "ZipSlip Attack",
        }

        with zipfile.ZipFile(zipslip_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest_data))
            zf.writestr("../../../evil.txt", b"malicious content")

        target_dir = tmp_path / "safe_target"
        with pytest.raises(ValueError, match="ZipSlip path traversal attempt"):
            extract_recipeboxer_archive(zipslip_path, target_dir, verify_hashes=False)

    def test_merge_recipeboxer_archives_deduplication(self, temp_workspace):
        store: SessionStore = temp_workspace["store"]
        manager: SessionManager = temp_workspace["manager"]
        cache: FingerprintCache = temp_workspace["cache"]
        card1: Path = temp_workspace["card1_path"]
        card2: Path = temp_workspace["card2_path"]

        # Base Session with Card 1 and Recipe A
        sess1 = PipelineSession(session_id="sess_base", cache=cache)
        sess1.cards = [ScannedCard(card_id="card_001.jpg", file_path=card1, filename="card_001.jpg")]
        sess1.recipes = [
            RecipeSchema(
                recipe_id="rec_01",
                title="Apple Crisp",
                ingredients=["Apples", "Oats"],
                directions=["1. Mix.", "2. Bake."],
                card_ids=["card_001.jpg"],
            )
        ]
        base_bytes = create_recipeboxer_archive(sess1, store=store, manager=manager, cache=cache)

        # Incoming Session with Card 1 (duplicate) + Card 2 (new) and Recipe B
        sess2 = PipelineSession(session_id="sess_incoming", cache=cache)
        sess2.cards = [
            ScannedCard(card_id="card_001.jpg", file_path=card1, filename="card_001.jpg"),
            ScannedCard(card_id="card_002.jpg", file_path=card2, filename="card_002.jpg"),
        ]
        sess2.recipes = [
            RecipeSchema(
                recipe_id="rec_02",
                title="Berry Tart",
                ingredients=["Berries", "Flour"],
                directions=["1. Bake tart."],
                card_ids=["card_002.jpg"],
            )
        ]
        incoming_bytes = create_recipeboxer_archive(sess2, store=store, manager=manager, cache=cache)

        # Merge archives
        merged_manifest, merged_bytes = merge_recipeboxer_archives(
            base_archive=base_bytes,
            incoming_archive_or_session=incoming_bytes,
            conflict_strategy="keep_both",
            store=store,
            manager=manager,
            cache=cache,
        )

        # Assets should be deduplicated: card1 + card2 = 2 assets total
        assert len(merged_manifest.assets) == 2
        # Both distinct recipes should be present
        assert len(merged_manifest.recipes) == 2
        recipe_titles = {r.title for r in merged_manifest.recipes}
        assert "Apple Crisp" in recipe_titles
        assert "Berry Tart" in recipe_titles

    def test_restore_archive_to_session_fidelity(self, temp_workspace):
        store: SessionStore = temp_workspace["store"]
        manager: SessionManager = temp_workspace["manager"]
        cache: FingerprintCache = temp_workspace["cache"]
        card1: Path = temp_workspace["card1_path"]

        orig_sid = "sess_original"
        sess = PipelineSession(session_id=orig_sid, cache=cache)
        sess.state = PipelineState.COMPLETED
        sc = ScannedCard(card_id="card_001.jpg", file_path=card1, filename="card_001.jpg")
        sess.cards = [sc]
        fp = VisualFingerprint(
            card_id="card_001.jpg",
            image_path=str(card1),
            sha256_hash=cache.compute_hash(card1),
            role=CardRole.STANDALONE,
        )
        sess.fingerprints["card_001.jpg"] = fp
        cache.put(fp)
        sess.recipes = [
            RecipeSchema(
                recipe_id="rec_pecan_pie",
                title="Grandma's Pecan Pie",
                ingredients=["Pecans", "Corn Syrup", "Eggs"],
                directions=["1. Mix ingredients.", "2. Pour into crust.", "3. Bake at 350F."],
                card_ids=["card_001.jpg"],
                source_notes="Holiday 1974",
            )
        ]

        archive_bytes = create_recipeboxer_archive(sess, store=store, manager=manager, cache=cache)

        # Restore into new target session
        restored_detail = restore_archive_to_session(
            archive_file_or_bytes=archive_bytes,
            store=store,
            manager=manager,
            cache=cache,
            target_session_id="sess_restored_01",
        )

        assert restored_detail.session_id == "sess_restored_01"
        assert restored_detail.card_count == 1
        assert restored_detail.recipe_count == 1
        assert restored_detail.recipes[0].title == "Grandma's Pecan Pie"
        assert restored_detail.recipes[0].source_notes == "Holiday 1974"

        # Verify in-memory session manager registration
        in_mem_sess = manager.get_session("sess_restored_01")
        assert in_mem_sess is not None
        assert in_mem_sess.recipes[0].title == "Grandma's Pecan Pie"


class TestArchiveEndpoints:
    @pytest.fixture
    def api_client(self, temp_workspace):
        settings: Settings = temp_workspace["settings"]
        store: SessionStore = temp_workspace["store"]
        manager: SessionManager = temp_workspace["manager"]

        app = create_app(
            settings=settings,
            session_manager=manager,
            session_store=store,
        )
        client = TestClient(app)
        return client, store, manager, temp_workspace

    def test_export_project_endpoint_and_alias(self, api_client):
        client, store, manager, ws = api_client
        cache: FingerprintCache = ws["cache"]
        card1: Path = ws["card1_path"]

        sid = "sess_api_export_01"
        sess = PipelineSession(session_id=sid, cache=cache)
        sess.state = PipelineState.COMPLETED
        sess.cards = [ScannedCard(card_id="card_001.jpg", file_path=card1, filename="card_001.jpg")]
        sess.recipes = [
            RecipeSchema(
                recipe_id="rec_shortbread",
                title="Butter Shortbread",
                ingredients=["Butter", "Sugar", "Flour"],
                directions=["1. Beat butter.", "2. Mix in flour.", "3. Bake."],
                card_ids=["card_001.jpg"],
            )
        ]
        manager.register_session(sess)
        store.save_pipeline_session(sess)

        # 1. GET /api/project/export?session_id=...
        res1 = client.get(f"/api/project/export?session_id={sid}")
        assert res1.status_code == 200
        assert res1.headers["content-type"] == "application/x-recipeboxer"
        assert f"{sid}.recipeboxer" in res1.headers.get("content-disposition", "")
        assert len(res1.content) > 0

        # 2. GET /api/sessions/{session_id}/export/project (Alias)
        res2 = client.get(f"/api/sessions/{sid}/export/project")
        assert res2.status_code == 200
        assert res2.headers["content-type"] == "application/x-recipeboxer"

        # 3. Unknown session returns 404
        res3 = client.get("/api/project/export?session_id=nonexistent_sid")
        assert res3.status_code == 404

    def test_import_and_merge_project_endpoints(self, api_client):
        client, store, manager, ws = api_client
        cache: FingerprintCache = ws["cache"]
        card1: Path = ws["card1_path"]
        card2: Path = ws["card2_path"]

        # Prepare archive bytes
        sess = PipelineSession(session_id="sess_src_export", cache=cache)
        sess.state = PipelineState.COMPLETED
        sess.cards = [ScannedCard(card_id="card_001.jpg", file_path=card1, filename="card_001.jpg")]
        sess.recipes = [
            RecipeSchema(
                recipe_id="rec_cookies",
                title="Molasses Cookies",
                ingredients=["Molasses", "Ginger", "Flour"],
                directions=["1. Roll in sugar.", "2. Bake."],
                card_ids=["card_001.jpg"],
            )
        ]
        archive_bytes = create_recipeboxer_archive(sess, store=store, manager=manager, cache=cache)

        # 1. POST /api/project/import
        files = {"file": ("test_archive.recipeboxer", archive_bytes, "application/x-recipeboxer")}
        data = {"target_session_id": "sess_api_imported_01"}
        res_import = client.post("/api/project/import", files=files, data=data)
        assert res_import.status_code == 200
        imported_json = res_import.json()
        assert imported_json["session_id"] == "sess_api_imported_01"
        assert imported_json["recipe_count"] == 1
        assert imported_json["recipes"][0]["title"] == "Molasses Cookies"

        # 2. POST /api/project/merge with session
        sess_base = PipelineSession(session_id="sess_base_for_merge", cache=cache)
        sess_base.cards = [ScannedCard(card_id="card_002.jpg", file_path=card2, filename="card_002.jpg")]
        sess_base.recipes = [
            RecipeSchema(
                recipe_id="rec_pie",
                title="Lemon Meringue Pie",
                ingredients=["Lemons", "Eggs", "Sugar"],
                directions=["1. Cook curd.", "2. Top with meringue."],
                card_ids=["card_002.jpg"],
            )
        ]
        manager.register_session(sess_base)
        store.save_pipeline_session(sess_base)

        merge_files = {"base_archive": ("base.recipeboxer", archive_bytes, "application/x-recipeboxer")}
        merge_data = {"session_id": "sess_base_for_merge", "conflict_strategy": "keep_both"}

        res_merge = client.post("/api/project/merge", files=merge_files, data=merge_data)
        assert res_merge.status_code == 200
        assert res_merge.headers["content-type"] == "application/x-recipeboxer"

        # Verify merged content has both Molasses Cookies and Lemon Meringue Pie
        with zipfile.ZipFile(io.BytesIO(res_merge.content), "r") as zf:
            manifest_dict = json.loads(zf.read("manifest.json").decode("utf-8"))
            titles = {r["title"] for r in manifest_dict["recipes"]}
            assert "Molasses Cookies" in titles
            assert "Lemon Meringue Pie" in titles
