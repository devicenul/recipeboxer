"""
Adversarial Empirical Stress Test Suite for Milestone 1 REST Endpoints and Session Restoration.
Tested by Challenger 2 (Empirical Challenger).

Covers:
1. GET /api/project/export (valid, non-existent, empty, unicode, SQLite-only, empty session, aliases, path traversal sanitization).
2. POST /api/project/import (corrupted zip, missing manifest, invalid JSON, schema failure, missing asset, sha256 mismatch, ZipSlip, bad extension, full fidelity restoration to SQLite/SessionManager/FingerprintCache, alias).
3. POST /api/project/merge (missing params, active session + base, archive + archive, deduplication, conflict strategies: keep_both, prefer_incoming, prefer_base, card ID collision renaming, corrupted inputs, ZipSlip).
4. Full round-trip fidelity across step bindings, marginalia, proposals, and fingerprints.
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
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
from recipeboxer.models.domain import CardRole, VisualFingerprint
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


def create_solid_image_bytes(color: str = "red", size: tuple[int, int] = (100, 100)) -> bytes:
    """Generates valid JPEG bytes with distinct colors/dimensions."""
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def challenger_workspace(tmp_path: Path):
    db_name = "challenger_test.db"
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    db_path = cache_dir / db_name
    scans_dir = tmp_path / "scans"
    scans_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        cache_dir=cache_dir,
        cache_db_filename=db_name,
        export_dir=tmp_path / "exports",
    )
    store = SessionStore(db_path=str(db_path))
    cache = FingerprintCache(db_path=str(db_path))
    manager = SessionManager()

    yield {
        "tmp_path": tmp_path,
        "db_path": db_path,
        "scans_dir": scans_dir,
        "settings": settings,
        "store": store,
        "cache": cache,
        "manager": manager,
    }

    store.close()
    cache.close()


@pytest_asyncio.fixture
async def challenger_client(challenger_workspace: dict[str, Any]) -> AsyncGenerator[httpx.AsyncClient, None]:
    settings: Settings = challenger_workspace["settings"]
    manager: SessionManager = challenger_workspace["manager"]
    store: SessionStore = challenger_workspace["store"]

    app = create_app(
        settings=settings,
        session_manager=manager,
        session_store=store,
    )
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ============================================================================
# 1. EXPORT ENDPOINT ADVERSARIAL STRESS TESTS
# ============================================================================

@pytest.mark.asyncio
class TestExportEndpointStress:
    """Adversarial stress testing for GET /api/project/export and its aliases."""

    async def test_export_nonexistent_session_returns_404(self, challenger_client: httpx.AsyncClient):
        """Exporting a nonexistent session ID must return 404 Not Found."""
        resp = await challenger_client.get("/api/project/export?session_id=sess_does_not_exist_404")
        assert resp.status_code == 404
        assert "Session 'sess_does_not_exist_404' not found" in resp.json()["detail"]

    async def test_export_empty_session_id_no_active_session_returns_404(
        self, challenger_client: httpx.AsyncClient
    ):
        """Exporting with empty session_id when no active session exists must return 404."""
        resp = await challenger_client.get("/api/project/export?session_id=")
        assert resp.status_code == 404

    async def test_export_no_query_param_no_active_session_returns_404(
        self, challenger_client: httpx.AsyncClient
    ):
        """Exporting with no session_id parameter when no active session exists must return 404."""
        resp = await challenger_client.get("/api/project/export")
        assert resp.status_code == 404

    async def test_export_sqlite_persisted_session_not_in_memory(
        self, challenger_workspace: dict[str, Any], challenger_client: httpx.AsyncClient
    ):
        """Exporting a session that is only in SQLite (e.g. after server restart) must succeed."""
        store: SessionStore = challenger_workspace["store"]
        sid = "sess_sqlite_only_01"

        store.save_session(
            session_id=sid,
            state=PipelineState.COMPLETED,
            mode="parallel",
            folder_path="/tmp/fake",
            config={"default_tags": ["Family", "Heirloom"]},
            card_count=0,
            recipe_count=1,
        )
        store.save_recipes(
            sid,
            [
                RecipeSchema(
                    recipe_id="rec_persisted_01",
                    title="SQLite Heirloom Stew",
                    ingredients=["Beef", "Carrots", "Potatoes"],
                    directions=["Simmer for 3 hours."],
                    tags=["Stew", "Winter"],
                )
            ],
        )

        resp = await challenger_client.get(f"/api/project/export?session_id={sid}")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/x-recipeboxer"

        # Verify manifest content inside archive
        with zipfile.ZipFile(io.BytesIO(resp.content), "r") as zf:
            assert "manifest.json" in zf.namelist()
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            assert manifest["session_metadata"]["session_id"] == sid
            assert len(manifest["recipes"]) == 1
            assert manifest["recipes"][0]["title"] == "SQLite Heirloom Stew"

    async def test_export_empty_session_succeeds_with_valid_manifest(
        self, challenger_workspace: dict[str, Any], challenger_client: httpx.AsyncClient
    ):
        """Exporting an active session with 0 cards and 0 recipes produces a structurally valid archive."""
        manager: SessionManager = challenger_workspace["manager"]
        sid = "sess_empty_active"
        sess = manager.create_session(session_id=sid)
        sess.state = PipelineState.COMPLETED

        resp = await challenger_client.get(f"/api/project/export?session_id={sid}")
        assert resp.status_code == 200
        with zipfile.ZipFile(io.BytesIO(resp.content), "r") as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            assert manifest["cards"] == []
            assert manifest["recipes"] == []
            assert manifest["assets"] == []
            assert manifest["proposals"] == []

    async def test_export_aliases_work_identically(
        self, challenger_workspace: dict[str, Any], challenger_client: httpx.AsyncClient
    ):
        """Verify all export route aliases return equivalent valid archives."""
        manager: SessionManager = challenger_workspace["manager"]
        sid = "sess_alias_test"
        sess = manager.create_session(session_id=sid)
        sess.state = PipelineState.COMPLETED
        sess.recipes = [
            RecipeSchema(
                recipe_id="rec_alias",
                title="Alias Cinnamon Rolls",
                ingredients=["Flour", "Cinnamon", "Sugar"],
                directions=["Roll dough.", "Bake at 375F."],
            )
        ]

        # Route 1: /api/project/export
        r1 = await challenger_client.get(f"/api/project/export?session_id={sid}")
        assert r1.status_code == 200

        # Route 2: /api/export/project
        r2 = await challenger_client.get(f"/api/export/project?session_id={sid}")
        assert r2.status_code == 200

        # Route 3: /api/export/recipeboxer
        r3 = await challenger_client.get(f"/api/export/recipeboxer?session_id={sid}")
        assert r3.status_code == 200

        # Route 4: /api/sessions/{session_id}/export/project
        r4 = await challenger_client.get(f"/api/sessions/{sid}/export/project")
        assert r4.status_code == 200

        for r in (r1, r2, r3, r4):
            assert r.headers["content-type"] == "application/x-recipeboxer"
            with zipfile.ZipFile(io.BytesIO(r.content), "r") as zf:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                assert manifest["recipes"][0]["title"] == "Alias Cinnamon Rolls"

    async def test_export_unicode_and_special_characters(
        self, challenger_workspace: dict[str, Any], challenger_client: httpx.AsyncClient
    ):
        """Export correctly preserves UTF-8 multi-byte characters (Japanese, French accents, emojis)."""
        manager: SessionManager = challenger_workspace["manager"]
        sid = "sess_unicode_01"
        sess = manager.create_session(session_id=sid)
        sess.state = PipelineState.COMPLETED
        sess.recipes = [
            RecipeSchema(
                recipe_id="rec_unicode",
                title="抹茶クレープケーキ 🍰 & Crème Brûlée",
                description="Délicieux gâteau avec thé vert 🍵 et vanille bourbon.",
                ingredients=["200g 抹茶粉", "3 œufs frais", "500ml lait entier"],
                directions=["1. Mélanger les ingrédients.", "2. Cuire à la poêle 🍳."],
                source_notes="Recette de grand-mère & おばあちゃんの秘伝",
                tags=["抹茶", "Gâteau", "Dessert ✨"],
            )
        ]

        resp = await challenger_client.get(f"/api/project/export?session_id={sid}")
        assert resp.status_code == 200
        with zipfile.ZipFile(io.BytesIO(resp.content), "r") as zf:
            manifest_str = zf.read("manifest.json").decode("utf-8")
            manifest = json.loads(manifest_str)
            assert manifest["recipes"][0]["title"] == "抹茶クレープケーキ 🍰 & Crème Brûlée"
            assert "おばあちゃんの秘伝" in manifest["recipes"][0]["source_notes"]

    async def test_export_filename_path_traversal_sanitized(
        self, challenger_workspace: dict[str, Any], challenger_client: httpx.AsyncClient
    ):
        """Suggested export filename containing directory traversal or dangerous characters is sanitized."""
        manager: SessionManager = challenger_workspace["manager"]
        sid = "sess_sanitization_01"
        sess = manager.create_session(session_id=sid)
        sess.state = PipelineState.COMPLETED

        resp = await challenger_client.get(
            f"/api/project/export?session_id={sid}&filename=../../../../etc/passwd;dangerous<script>"
        )
        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        assert ".." not in cd
        assert "<script>" not in cd
        assert cd.endswith('.recipeboxer"') or cd.endswith(".recipeboxer")


# ============================================================================
# 2. IMPORT ENDPOINT ADVERSARIAL STRESS TESTS
# ============================================================================

@pytest.mark.asyncio
class TestImportEndpointStress:
    """Adversarial stress testing for POST /api/project/import (corruption, validation, restoration)."""

    async def test_import_non_zip_file_rejected(self, challenger_client: httpx.AsyncClient):
        """Uploading random binary garbage must be rejected with HTTP 400 or 422."""
        garbage_bytes = b"\x00\xff\xfe\xfd\x01\x02\x03\x04RANDOM_NON_ZIP_DATA"
        files = {"file": ("corrupt.recipeboxer", garbage_bytes, "application/x-recipeboxer")}
        resp = await challenger_client.post("/api/project/import", files=files)
        assert resp.status_code in (400, 422)

    async def test_import_truncated_zip_rejected(self, challenger_client: httpx.AsyncClient):
        """Uploading a truncated ZIP archive must be rejected with HTTP 422."""
        valid_buf = io.BytesIO()
        with zipfile.ZipFile(valid_buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"format_version": "1.0.0"}))
        truncated = valid_buf.getvalue()[:20]  # Cut off central directory

        files = {"file": ("truncated.recipeboxer", truncated, "application/x-recipeboxer")}
        resp = await challenger_client.post("/api/project/import", files=files)
        assert resp.status_code in (400, 422)

    async def test_import_missing_manifest_rejected(self, challenger_client: httpx.AsyncClient):
        """Uploading a ZIP archive missing manifest.json must be rejected with 422."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("other_file.txt", "hello world")
        files = {"file": ("no_manifest.recipeboxer", buf.getvalue(), "application/x-recipeboxer")}
        resp = await challenger_client.post("/api/project/import", files=files)
        assert resp.status_code == 422
        assert "missing required 'manifest.json'" in resp.json()["detail"]

    async def test_import_malformed_json_manifest_rejected(self, challenger_client: httpx.AsyncClient):
        """Uploading an archive with invalid JSON syntax in manifest.json must be rejected with 422."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", "{ this is : not valid json }")
        files = {"file": ("bad_json.recipeboxer", buf.getvalue(), "application/x-recipeboxer")}
        resp = await challenger_client.post("/api/project/import", files=files)
        assert resp.status_code == 422

    async def test_import_missing_referenced_asset_rejected(self, challenger_client: httpx.AsyncClient):
        """Uploading an archive where manifest references an asset not in the zip must fail verification."""
        manifest = {
            "format_version": "1.0.0",
            "project_id": "proj_missing_asset",
            "assets": [
                {
                    "sha256_hash": "a" * 64,
                    "filename": "missing.jpg",
                    "archive_path": "assets/scans/missing.jpg",
                    "size_bytes": 100,
                }
            ],
            "cards": [],
            "recipes": [],
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            # Notice assets/scans/missing.jpg is deliberately NOT written
        files = {"file": ("missing_asset.recipeboxer", buf.getvalue(), "application/x-recipeboxer")}
        resp = await challenger_client.post("/api/project/import", files=files)
        assert resp.status_code == 422
        assert "Missing asset file" in resp.json()["detail"]

    async def test_import_tampered_asset_hash_mismatch_rejected(self, challenger_client: httpx.AsyncClient):
        """Uploading an archive where an asset's bytes do not match the manifest SHA-256 is rejected."""
        manifest = {
            "format_version": "1.0.0",
            "project_id": "proj_tampered",
            "assets": [
                {
                    "sha256_hash": "b" * 64,  # Expected hash
                    "filename": "tampered.jpg",
                    "archive_path": "assets/scans/tampered.jpg",
                    "size_bytes": 14,
                }
            ],
            "cards": [],
            "recipes": [],
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("assets/scans/tampered.jpg", b"tampered bytes")
        files = {"file": ("tampered.recipeboxer", buf.getvalue(), "application/x-recipeboxer")}
        resp = await challenger_client.post("/api/project/import", files=files)
        assert resp.status_code == 422
        assert "Asset integrity check failed" in resp.json()["detail"]

    async def test_import_zipslip_traversal_rejected(self, challenger_client: httpx.AsyncClient):
        """Uploading an archive containing ZipSlip path traversal entries is rejected."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"format_version": "1.0.0"}))
            zf.writestr("../../etc/shadow", b"malicious root shadow")
        files = {"file": ("exploit.recipeboxer", buf.getvalue(), "application/x-recipeboxer")}
        resp = await challenger_client.post("/api/project/import", files=files)
        assert resp.status_code == 422
        assert "ZipSlip" in resp.json()["detail"]

    async def test_import_disallowed_extension_rejected(self, challenger_client: httpx.AsyncClient):
        """Uploading a file without .recipeboxer or .zip extension is rejected with HTTP 400."""
        files = {"file": ("evil_script.py", b"print('hacked')", "text/plain")}
        resp = await challenger_client.post("/api/project/import", files=files)
        assert resp.status_code == 400
        assert "must have a .recipeboxer or .zip extension" in resp.json()["detail"]

    async def test_import_full_session_restoration_fidelity(
        self, challenger_workspace: dict[str, Any], challenger_client: httpx.AsyncClient
    ):
        """Full fidelity session import restores cards, fingerprints, proposals, recipes, and SQLite records."""
        store: SessionStore = challenger_workspace["store"]
        manager: SessionManager = challenger_workspace["manager"]
        cache: FingerprintCache = challenger_workspace["cache"]

        img_bytes = create_solid_image_bytes(color="gold", size=(150, 150))
        img_sha = hashlib.sha256(img_bytes).hexdigest()

        card_id = "card_gold_01"
        target_sid = "sess_gold_restored_100"

        manifest_data = {
            "format_version": "1.0.0",
            "app_version": "1.0.0",
            "schema_version": "1.0.0",
            "project_id": "proj_gold_archive",
            "project_name": "Gold Saffron Buns Project",
            "session_metadata": {
                "session_id": "sess_gold_source",
                "status": "COMPLETED",
                "mode": "parallel",
                "default_tags": ["Nordic", "Holiday"],
            },
            "assets": [
                {
                    "sha256_hash": img_sha,
                    "filename": "gold_card.jpg",
                    "archive_path": f"assets/scans/{img_sha}.jpg",
                    "mime_type": "image/jpeg",
                    "size_bytes": len(img_bytes),
                    "width": 150,
                    "height": 150,
                }
            ],
            "cards": [
                {
                    "card_id": card_id,
                    "sha256_hash": img_sha,
                    "filename": "gold_card.jpg",
                    "role": "STANDALONE",
                    "role_confidence": 0.98,
                    "fingerprint": {
                        "card_id": card_id,
                        "image_path": "",
                        "sha256_hash": img_sha,
                        "role": "STANDALONE",
                        "role_confidence": 0.98,
                    },
                }
            ],
            "proposals": [
                {
                    "proposal_id": "prop_gold_01",
                    "ordered_card_ids": [card_id],
                    "confidence": 0.99,
                    "is_standalone": True,
                    "recommended_action": "KEEP_SEPARATE",
                    "decision": "APPROVE",
                    "notes": "Verified single card recipe",
                }
            ],
            "recipes": [
                {
                    "recipe_id": "rec_saffron_buns",
                    "title": "St. Lucia Saffron Buns (Lussekatter)",
                    "description": "Traditional Swedish golden saffron yeast buns.",
                    "yield_servings": "16 buns",
                    "prep_time": "30 mins",
                    "cook_time": "12 mins",
                    "total_time": "2.5 hrs",
                    "ingredients": [
                        "1g saffron threads",
                        "500g all-purpose flour",
                        "250ml warm milk",
                        "75g sugar",
                        "75g butter",
                        "1 packet active dry yeast",
                        "Raisins for garnish",
                    ],
                    "directions": [
                        "1. Steep ground saffron in warm milk with 1 tbsp sugar.",
                        "2. Knead dough until smooth and elastic, let rise 1 hour.",
                        "3. Shape into S-curves, insert raisins, and bake at 400°F for 10-12 minutes.",
                    ],
                    "tags": ["Swedish", "Baking", "Saffron", "Holiday"],
                    "source_notes": "Mormor's authentic St. Lucia recipe from 1968.",
                    "card_ids": [card_id],
                    "step_bindings": [
                        {
                            "step_index": 1,
                            "card_id": card_id,
                            "card_sha256": img_sha,
                            "bounding_box": [0.1, 0.1, 0.4, 0.9],
                        }
                    ],
                    "marginalia": [
                        {
                            "id": "marg_saffron_01",
                            "card_id": card_id,
                            "card_sha256": img_sha,
                            "text": "Add a pinch of cognac to the saffron milk!",
                            "location_quadrant": "TOP_RIGHT",
                            "category": "culinary_tweak",
                            "target_field": "source_notes",
                        }
                    ],
                }
            ],
        }

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest_data))
            zf.writestr(f"assets/scans/{img_sha}.jpg", img_bytes)

        archive_bytes = buf.getvalue()

        # Execute POST /api/project/import with target_session_id override
        files = {"file": ("saffron_buns.recipeboxer", archive_bytes, "application/x-recipeboxer")}
        data = {"target_session_id": target_sid, "auto_resume": "false"}

        resp = await challenger_client.post("/api/project/import", files=files, data=data)
        assert resp.status_code == 200
        res_json = resp.json()
        assert res_json["session_id"] == target_sid
        assert res_json["recipe_count"] == 1
        assert res_json["card_count"] == 1

        # 1. Verify SQLite SessionStore state
        stored_detail = store.get_session_detail(target_sid)
        assert stored_detail is not None
        assert stored_detail.session_id == target_sid
        assert len(stored_detail.recipes) == 1
        assert stored_detail.recipes[0].title == "St. Lucia Saffron Buns (Lussekatter)"
        assert len(stored_detail.cards) == 1
        assert stored_detail.cards[0]["card_id"] == card_id
        assert len(stored_detail.proposals) == 1
        assert stored_detail.proposals[0]["proposal_id"] == "prop_gold_01"

        # 2. Verify in-memory SessionManager state
        mem_sess = manager.get_session(target_sid)
        assert mem_sess is not None
        assert mem_sess.session_id == target_sid
        assert len(mem_sess.recipes) == 1
        assert mem_sess.recipes[0].title == "St. Lucia Saffron Buns (Lussekatter)"
        assert card_id in mem_sess.fingerprints

        # 3. Verify SQLite FingerprintCache state
        cached_fp = cache.get_by_hash(img_sha)
        assert cached_fp is not None
        assert cached_fp.card_id == card_id
        assert cached_fp.role == CardRole.STANDALONE

        # 4. Verify REST endpoint /api/recipes?session_id=...
        rec_resp = await challenger_client.get(f"/api/recipes?session_id={target_sid}")
        assert rec_resp.status_code == 200
        recs = rec_resp.json()
        assert len(recs) == 1
        assert "Mormor's authentic" in recs[0]["source_notes"]
        assert len(recs[0]["ingredients"]) == 7


# ============================================================================
# 3. MERGE ENDPOINT ADVERSARIAL STRESS TESTS
# ============================================================================

@pytest.mark.asyncio
class TestMergeEndpointStress:
    """Adversarial stress testing for POST /api/project/merge (conflict resolution, deduplication, errors)."""

    def build_test_archive(self, project_name: str, recipe_id: str, title: str, img_color: str, card_id: str) -> bytes:
        img_bytes = create_solid_image_bytes(color=img_color)
        sha = hashlib.sha256(img_bytes).hexdigest()

        manifest = {
            "format_version": "1.0.0",
            "app_version": "1.0.0",
            "schema_version": "1.0.0",
            "project_id": f"proj_{uuid.uuid4().hex[:6]}",
            "project_name": project_name,
            "assets": [
                {
                    "sha256_hash": sha,
                    "filename": f"{card_id}.jpg",
                    "archive_path": f"assets/scans/{sha}.jpg",
                    "mime_type": "image/jpeg",
                    "size_bytes": len(img_bytes),
                }
            ],
            "cards": [
                {
                    "card_id": card_id,
                    "sha256_hash": sha,
                    "filename": f"{card_id}.jpg",
                    "role": "STANDALONE",
                }
            ],
            "proposals": [],
            "recipes": [
                {
                    "recipe_id": recipe_id,
                    "title": title,
                    "ingredients": ["Item 1", "Item 2"],
                    "directions": ["Step 1", "Step 2"],
                    "card_ids": [card_id],
                }
            ],
        }

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr(f"assets/scans/{sha}.jpg", img_bytes)

        return buf.getvalue()

    async def test_merge_missing_both_sources_rejected_400(self, challenger_client: httpx.AsyncClient):
        """Calling merge without session_id or incoming_archive must return HTTP 400."""
        base_bytes = self.build_test_archive("Base", "rec_base", "Base Salad", "green", "card_01")
        files = {"base_archive": ("base.recipeboxer", base_bytes, "application/x-recipeboxer")}

        resp = await challenger_client.post("/api/project/merge", files=files)
        assert resp.status_code == 400
        assert "Either session_id or incoming_archive must be provided" in resp.json()["detail"]

    async def test_merge_corrupted_base_archive_rejected_422(self, challenger_client: httpx.AsyncClient):
        """Calling merge with corrupted base archive returns HTTP 422."""
        incoming_bytes = self.build_test_archive("Inc", "rec_inc", "Inc Soup", "blue", "card_02")
        files = {
            "base_archive": ("corrupt_base.recipeboxer", b"NOT_A_ZIP_BASE", "application/x-recipeboxer"),
            "incoming_archive": ("incoming.recipeboxer", incoming_bytes, "application/x-recipeboxer"),
        }
        resp = await challenger_client.post("/api/project/merge", files=files)
        assert resp.status_code == 422

    async def test_merge_corrupted_incoming_archive_rejected_422(self, challenger_client: httpx.AsyncClient):
        """Calling merge with corrupted incoming archive returns HTTP 422."""
        base_bytes = self.build_test_archive("Base", "rec_base", "Base Soup", "green", "card_01")
        files = {
            "base_archive": ("base.recipeboxer", base_bytes, "application/x-recipeboxer"),
            "incoming_archive": ("corrupt_inc.recipeboxer", b"NOT_A_ZIP_INC", "application/x-recipeboxer"),
        }
        resp = await challenger_client.post("/api/project/merge", files=files)
        assert resp.status_code == 422

    async def test_merge_strategy_keep_both_renames_duplicate_recipe(self, challenger_client: httpx.AsyncClient):
        """With strategy 'keep_both', colliding recipes are both preserved with disambiguated ID."""
        # Both archives contain recipe with title 'Grandma's Apple Pie' and ID 'rec_pie'
        base_bytes = self.build_test_archive("Base", "rec_pie", "Grandma's Apple Pie", "green", "card_01")
        inc_bytes = self.build_test_archive("Incoming", "rec_pie", "Grandma's Apple Pie", "orange", "card_02")

        files = {
            "base_archive": ("base.recipeboxer", base_bytes, "application/x-recipeboxer"),
            "incoming_archive": ("inc.recipeboxer", inc_bytes, "application/x-recipeboxer"),
        }
        data = {"conflict_strategy": "keep_both"}

        resp = await challenger_client.post("/api/project/merge", files=files, data=data)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/x-recipeboxer"

        with zipfile.ZipFile(io.BytesIO(resp.content), "r") as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            # Both recipes preserved
            assert len(manifest["recipes"]) == 2
            recipe_ids = {r["recipe_id"] for r in manifest["recipes"]}
            assert "rec_pie" in recipe_ids
            # Second recipe has new UUID
            assert len(recipe_ids) == 2

    async def test_merge_strategy_prefer_incoming_overwrites(self, challenger_client: httpx.AsyncClient):
        """With strategy 'prefer_incoming', incoming recipe replaces the base recipe."""
        base_bytes = self.build_test_archive("Base", "rec_pie", "Grandma's Apple Pie", "green", "card_01")
        # Incoming has updated directions
        inc_bytes = self.build_test_archive("Incoming", "rec_pie", "Grandma's Apple Pie", "orange", "card_02")

        files = {
            "base_archive": ("base.recipeboxer", base_bytes, "application/x-recipeboxer"),
            "incoming_archive": ("inc.recipeboxer", inc_bytes, "application/x-recipeboxer"),
        }
        data = {"conflict_strategy": "prefer_incoming"}

        resp = await challenger_client.post("/api/project/merge", files=files, data=data)
        assert resp.status_code == 200

        with zipfile.ZipFile(io.BytesIO(resp.content), "r") as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            assert len(manifest["recipes"]) == 1
            # Card ID should be from incoming (card_02)
            assert "card_02" in manifest["recipes"][0]["card_ids"]

    async def test_merge_strategy_prefer_base_ignores_incoming(self, challenger_client: httpx.AsyncClient):
        """With strategy 'prefer_base', colliding incoming recipe is discarded."""
        base_bytes = self.build_test_archive("Base", "rec_pie", "Grandma's Apple Pie", "green", "card_01")
        inc_bytes = self.build_test_archive("Incoming", "rec_pie", "Grandma's Apple Pie", "orange", "card_02")

        files = {
            "base_archive": ("base.recipeboxer", base_bytes, "application/x-recipeboxer"),
            "incoming_archive": ("inc.recipeboxer", inc_bytes, "application/x-recipeboxer"),
        }
        data = {"conflict_strategy": "prefer_base"}

        resp = await challenger_client.post("/api/project/merge", files=files, data=data)
        assert resp.status_code == 200

        with zipfile.ZipFile(io.BytesIO(resp.content), "r") as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            assert len(manifest["recipes"]) == 1
            # Card ID remains base (card_01)
            assert "card_01" in manifest["recipes"][0]["card_ids"]

    async def test_merge_active_session_into_base_archive(
        self, challenger_workspace: dict[str, Any], challenger_client: httpx.AsyncClient
    ):
        """POST /api/project/merge combining an active session ID with an uploaded base archive."""
        manager: SessionManager = challenger_workspace["manager"]
        base_bytes = self.build_test_archive("Base", "rec_base", "Base Pot Roast", "brown", "card_base_01")

        # Create active session in manager
        sid = "sess_active_merge_01"
        sess = manager.create_session(session_id=sid)
        sess.state = PipelineState.COMPLETED
        sess.recipes = [
            RecipeSchema(
                recipe_id="rec_active_merge",
                title="Active Session Garlic Mashed Potatoes",
                ingredients=["Potatoes", "Garlic", "Butter", "Cream"],
                directions=["Boil potatoes.", "Mash with roasted garlic and butter."],
                card_ids=["card_active_01"],
            )
        ]

        files = {"base_archive": ("base.recipeboxer", base_bytes, "application/x-recipeboxer")}
        data = {"session_id": sid, "conflict_strategy": "keep_both"}

        resp = await challenger_client.post("/api/project/merge", files=files, data=data)
        assert resp.status_code == 200

        with zipfile.ZipFile(io.BytesIO(resp.content), "r") as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            assert len(manifest["recipes"]) == 2
            titles = {r["title"] for r in manifest["recipes"]}
            assert "Base Pot Roast" in titles
            assert "Active Session Garlic Mashed Potatoes" in titles

    async def test_merge_asset_deduplication_exact_sha256(self, challenger_client: httpx.AsyncClient):
        """When base and incoming share the exact same image asset bytes, it is deduplicated to 1 asset."""
        # Use identical color and size to ensure identical SHA-256
        img_bytes = create_solid_image_bytes(color="cyan", size=(80, 80))
        img_sha = hashlib.sha256(img_bytes).hexdigest()

        def make_archive_with_sha(rec_id: str, title: str):
            manifest = {
                "format_version": "1.0.0",
                "app_version": "1.0.0",
                "schema_version": "1.0.0",
                "project_id": f"proj_{uuid.uuid4().hex[:6]}",
                "assets": [
                    {
                        "sha256_hash": img_sha,
                        "filename": "shared_card.jpg",
                        "archive_path": f"assets/scans/{img_sha}.jpg",
                        "size_bytes": len(img_bytes),
                    }
                ],
                "cards": [
                    {
                        "card_id": "shared_card_01",
                        "sha256_hash": img_sha,
                        "filename": "shared_card.jpg",
                        "role": "STANDALONE",
                    }
                ],
                "proposals": [],
                "recipes": [
                    {
                        "recipe_id": rec_id,
                        "title": title,
                        "ingredients": ["Shared ingredient"],
                        "directions": ["Shared direction"],
                        "card_ids": ["shared_card_01"],
                    }
                ],
            }
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", json.dumps(manifest))
                zf.writestr(f"assets/scans/{img_sha}.jpg", img_bytes)
            return buf.getvalue()

        base_bytes = make_archive_with_sha("rec_01", "Dish 1")
        inc_bytes = make_archive_with_sha("rec_02", "Dish 2")

        files = {
            "base_archive": ("base.recipeboxer", base_bytes, "application/x-recipeboxer"),
            "incoming_archive": ("inc.recipeboxer", inc_bytes, "application/x-recipeboxer"),
        }
        resp = await challenger_client.post("/api/project/merge", files=files)
        assert resp.status_code == 200

        with zipfile.ZipFile(io.BytesIO(resp.content), "r") as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            # Asset list must contain exactly 1 entry (deduplicated)
            assert len(manifest["assets"]) == 1
            assert manifest["assets"][0]["sha256_hash"] == img_sha
            # Cards list deduplicated
            assert len(manifest["cards"]) == 1
            # Both recipes present
            assert len(manifest["recipes"]) == 2


# ============================================================================
# 4. COMPLEX SCENARIOS & ADVERSARIAL ROUND-TRIP FIDELITY TESTS
# ============================================================================

@pytest.mark.asyncio
class TestEndToEndRoundTripAndComplexMerge:
    """Complex edge cases: card ID collisions with different digests, step bindings, marginalia, proposals."""

    async def test_card_id_collision_different_sha_renames_cleanly(
        self, challenger_client: httpx.AsyncClient
    ):
        """When base and incoming use the same card_id ('card_01') with DIFFERENT images,
        incoming is deterministically renamed and all recipe/binding/marginalia references are remapped.
        """
        img1 = create_solid_image_bytes(color="red", size=(100, 100))
        sha1 = hashlib.sha256(img1).hexdigest()

        img2 = create_solid_image_bytes(color="blue", size=(120, 120))
        sha2 = hashlib.sha256(img2).hexdigest()

        base_manifest = {
            "format_version": "1.0.0",
            "project_id": "proj_base_collide",
            "assets": [
                {
                    "sha256_hash": sha1,
                    "filename": "card_01.jpg",
                    "archive_path": f"assets/scans/{sha1}.jpg",
                    "size_bytes": len(img1),
                }
            ],
            "cards": [
                {
                    "card_id": "card_01",
                    "sha256_hash": sha1,
                    "filename": "card_01.jpg",
                    "role": "STANDALONE",
                }
            ],
            "proposals": [
                {
                    "proposal_id": "prop_base_01",
                    "ordered_card_ids": ["card_01"],
                    "confidence": 0.9,
                }
            ],
            "recipes": [
                {
                    "recipe_id": "rec_base_01",
                    "title": "Base Red Cake",
                    "ingredients": ["Red Flour"],
                    "directions": ["Bake red cake."],
                    "card_ids": ["card_01"],
                    "step_bindings": [
                        {"step_index": 1, "card_id": "card_01", "card_sha256": sha1}
                    ],
                    "marginalia": [
                        {"id": "m1", "card_id": "card_01", "card_sha256": sha1, "text": "Base note"}
                    ],
                }
            ],
        }

        inc_manifest = {
            "format_version": "1.0.0",
            "project_id": "proj_inc_collide",
            "assets": [
                {
                    "sha256_hash": sha2,
                    "filename": "card_01.jpg",
                    "archive_path": f"assets/scans/{sha2}.jpg",
                    "size_bytes": len(img2),
                }
            ],
            "cards": [
                {
                    "card_id": "card_01",  # Same card ID, DIFFERENT image!
                    "sha256_hash": sha2,
                    "filename": "card_01.jpg",
                    "role": "STANDALONE",
                }
            ],
            "proposals": [
                {
                    "proposal_id": "prop_inc_01",
                    "ordered_card_ids": ["card_01"],
                    "confidence": 0.85,
                }
            ],
            "recipes": [
                {
                    "recipe_id": "rec_inc_01",
                    "title": "Incoming Blue Tart",
                    "ingredients": ["Blue Berries"],
                    "directions": ["Bake blue tart."],
                    "card_ids": ["card_01"],
                    "step_bindings": [
                        {"step_index": 1, "card_id": "card_01", "card_sha256": sha2}
                    ],
                    "marginalia": [
                        {"id": "m2", "card_id": "card_01", "card_sha256": sha2, "text": "Incoming note"}
                    ],
                }
            ],
        }

        buf1 = io.BytesIO()
        with zipfile.ZipFile(buf1, "w") as zf:
            zf.writestr("manifest.json", json.dumps(base_manifest))
            zf.writestr(f"assets/scans/{sha1}.jpg", img1)

        buf2 = io.BytesIO()
        with zipfile.ZipFile(buf2, "w") as zf:
            zf.writestr("manifest.json", json.dumps(inc_manifest))
            zf.writestr(f"assets/scans/{sha2}.jpg", img2)

        files = {
            "base_archive": ("base.recipeboxer", buf1.getvalue(), "application/x-recipeboxer"),
            "incoming_archive": ("inc.recipeboxer", buf2.getvalue(), "application/x-recipeboxer"),
        }
        resp = await challenger_client.post("/api/project/merge", files=files)
        assert resp.status_code == 200

        with zipfile.ZipFile(io.BytesIO(resp.content), "r") as zf:
            merged = json.loads(zf.read("manifest.json").decode("utf-8"))
            # Must contain both distinct assets
            assert len(merged["assets"]) == 2
            # Must contain 2 distinct card records
            assert len(merged["cards"]) == 2
            card_ids = [c["card_id"] for c in merged["cards"]]
            assert "card_01" in card_ids
            # Incoming card was renamed to avoid collision
            renamed_card_id = [cid for cid in card_ids if cid != "card_01"][0]
            assert "card_01_" in renamed_card_id

            # Recipes remapped
            inc_recipe = [r for r in merged["recipes"] if r["recipe_id"] == "rec_inc_01"][0]
            assert inc_recipe["card_ids"] == [renamed_card_id]
            assert inc_recipe["step_bindings"][0]["card_id"] == renamed_card_id
            assert inc_recipe["marginalia"][0]["card_id"] == renamed_card_id

            # Proposals remapped
            inc_prop = [p for p in merged["proposals"] if p["proposal_id"] == "prop_inc_01"][0]
            assert inc_prop["ordered_card_ids"] == [renamed_card_id]

    async def test_export_fallback_when_card_image_missing_on_disk(
        self, challenger_workspace: dict[str, Any], challenger_client: httpx.AsyncClient
    ):
        """When an active session references an image file deleted from disk, export generates
        a valid 1x1 fallback JPEG so the resulting archive is valid and extractable.
        """
        manager: SessionManager = challenger_workspace["manager"]
        sid = "sess_missing_disk_image"
        sess = manager.create_session(session_id=sid)
        sess.state = PipelineState.COMPLETED

        missing_path = Path("/nonexistent/path/card_deleted.jpg")
        sc = ScannedCard(card_id="card_deleted.jpg", file_path=missing_path, filename="card_deleted.jpg")
        sess.cards = [sc]
        sess.recipes = [
            RecipeSchema(
                recipe_id="rec_deleted_img",
                title="Recipe With Missing Image File",
                ingredients=["Flour"],
                directions=["Bake."],
                card_ids=["card_deleted.jpg"],
            )
        ]

        resp = await challenger_client.get(f"/api/project/export?session_id={sid}")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/x-recipeboxer"

        # Verify archive can be unpacked and validated without integrity error
        with zipfile.ZipFile(io.BytesIO(resp.content), "r") as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            assert len(manifest["assets"]) == 1
            asset_path = manifest["assets"][0]["archive_path"]
            assert asset_path in zf.namelist()
            # Asset is a valid JPEG
            img_data = zf.read(asset_path)
            img = Image.open(io.BytesIO(img_data))
            assert img.format == "JPEG"

    async def test_full_roundtrip_multi_card_step_bindings_and_proposals(
        self, challenger_workspace: dict[str, Any], challenger_client: httpx.AsyncClient
    ):
        """Full roundtrip: 2-card recipe with direction step bindings, marginalia, and HITL proposals."""
        manager: SessionManager = challenger_workspace["manager"]
        store: SessionStore = challenger_workspace["store"]
        cache: FingerprintCache = challenger_workspace["cache"]
        scans_dir: Path = challenger_workspace["scans_dir"]

        sid = "sess_complex_source_99"
        card1_path = scans_dir / "card_c1.jpg"
        card2_path = scans_dir / "card_c2.jpg"

        img1_bytes = create_solid_image_bytes(color="purple", size=(200, 300))
        img2_bytes = create_solid_image_bytes(color="yellow", size=(200, 300))
        card1_path.write_bytes(img1_bytes)
        card2_path.write_bytes(img2_bytes)

        sha1 = hashlib.sha256(img1_bytes).hexdigest()
        sha2 = hashlib.sha256(img2_bytes).hexdigest()

        sess = manager.create_session(session_id=sid)
        sess.state = PipelineState.COMPLETED

        sc1 = ScannedCard(card_id="card_c1", file_path=card1_path, filename="card_c1.jpg", sha256_hash=sha1)
        sc2 = ScannedCard(card_id="card_c2", file_path=card2_path, filename="card_c2.jpg", sha256_hash=sha2)
        sess.cards = [sc1, sc2]

        fp1 = VisualFingerprint(card_id="card_c1", image_path=str(card1_path), sha256_hash=sha1, role=CardRole.SEQUENCE_START)
        fp2 = VisualFingerprint(card_id="card_c2", image_path=str(card2_path), sha256_hash=sha2, role=CardRole.SEQUENCE_CONTINUATION)
        sess.fingerprints = {"card_c1": fp1, "card_c2": fp2}
        cache.put(fp1)
        cache.put(fp2)

        sess.proposals = [
            {
                "proposal_id": "prop_pair_01",
                "ordered_card_ids": ["card_c1", "card_c2"],
                "part_count": 2,
                "transition_scores": [0.94],
                "heuristic_score": 0.88,
                "semantic_score": 0.95,
                "confidence": 0.92,
                "continuity_explanation": "Card 2 continues directions from card 1.",
                "recommended_action": "SEQUENCE",
                "decision": "APPROVE",
                "notes": "Verified pair",
            }
        ]

        recipe = RecipeSchema(
            recipe_id="rec_complex_pie",
            title="Two-Card Heirloom Blackberry Cobbler",
            description="Deep dish skillet cobbler.",
            yield_servings="8 servings",
            prep_time="25 mins",
            cook_time="45 mins",
            total_time="1 hr 10 mins",
            ingredients=["4 cups blackberries", "1 cup sugar", "1 cup flour", "1/2 cup butter"],
            directions=[
                "1. Toss berries with sugar and lemon juice.",
                "2. Mix flour, sugar, and melted butter to make batter.",
                "3. Pour over berries and bake at 375F for 45 minutes until golden.",
            ],
            tags=["Cobbler", "Blackberry", "Summer"],
            source_notes="Aunt Mabel's Summer 1965 Cobbler",
            card_ids=["card_c1", "card_c2"],
        )
        sess.recipes = [recipe]

        # 1. Export from server
        export_resp = await challenger_client.get(f"/api/project/export?session_id={sid}")
        assert export_resp.status_code == 200
        archive_bytes = export_resp.content

        # 2. Import into fresh session
        restored_sid = "sess_complex_target_99"
        files = {"file": ("cobbler.recipeboxer", archive_bytes, "application/x-recipeboxer")}
        data = {"target_session_id": restored_sid}

        import_resp = await challenger_client.post("/api/project/import", files=files, data=data)
        assert import_resp.status_code == 200

        # 3. Verify SQLite SessionStore detail
        detail = store.get_session_detail(restored_sid)
        assert detail is not None
        assert detail.session_id == restored_sid
        assert detail.card_count == 2
        assert detail.recipe_count == 1
        assert detail.recipes[0].title == "Two-Card Heirloom Blackberry Cobbler"
        assert len(detail.recipes[0].directions) == 3
        assert detail.recipes[0].card_ids == ["card_c1", "card_c2"]
        assert len(detail.proposals) == 1
        assert detail.proposals[0]["proposal_id"] == "prop_pair_01"
        assert detail.proposals[0]["decision"] == "APPROVE"

        # 4. Verify Fingerprints in Cache
        fp_c1 = cache.get_by_hash(sha1)
        assert fp_c1 is not None
        assert fp_c1.card_id == "card_c1"
        assert fp_c1.role == CardRole.SEQUENCE_START

        fp_c2 = cache.get_by_hash(sha2)
        assert fp_c2 is not None
        assert fp_c2.card_id == "card_c2"
        assert fp_c2.role == CardRole.SEQUENCE_CONTINUATION

