"""
Integration tests for Project Archive REST Endpoints (recipeboxer/api/routes.py).

Covers:
- GET /api/project/export (Session export to .recipeboxer binary stream)
- POST /api/project/import (Multipart upload restoring session to SessionStore & SessionManager)
- POST /api/project/merge (Merging archive containers or session into baseline archive)
"""

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from PIL import Image

from recipeboxer.agent.session import SessionManager
from recipeboxer.api.app import create_app
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.hitl import HITLDecision, CardSequenceApproval
from recipeboxer.models.pipeline import PipelineState
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.storage.session_store import SessionStore
from tests.conftest import MockGeminiClient, SQLiteFingerprintCache


# ============================================================================
# FIXTURES
# ============================================================================

def make_test_card_image(color: str = "blue") -> bytes:
    img = Image.new("RGB", (200, 200), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def test_session_manager(test_cache: SQLiteFingerprintCache, mock_gemini_client: MockGeminiClient) -> SessionManager:
    return SessionManager()


@pytest.fixture
def app(test_session_manager: SessionManager) -> Any:
    return create_app(session_manager=test_session_manager)


@pytest_asyncio.fixture
async def client(app) -> AsyncGenerator[httpx.AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def active_session_with_recipes(test_session_manager: SessionManager) -> str:
    """Pre-populates an active completed session with sample cards and recipes."""
    sid = "sess_archive_endpoint_test"
    session = test_session_manager.create_session(session_id=sid)
    session.state = PipelineState.COMPLETED

    img1 = make_test_card_image("green")
    sha1 = hashlib.sha256(img1).hexdigest()

    card_id = "card_herb_bread_01"
    session.card_scans = [
        {
            "card_id": card_id,
            "filename": "herb_bread.jpg",
            "path": f"/tmp/scans/{card_id}.jpg",
            "sha256": sha1,
            "raw_bytes": img1,
            "role": CardRole.STANDALONE,
        }
    ]
    session.fingerprints = {
        card_id: VisualFingerprint(
            card_id=card_id,
            image_path=f"/tmp/scans/{card_id}.jpg",
            sha256_hash=sha1,
            role=CardRole.STANDALONE,
            role_confidence=0.95,
        )
    }
    session.recipes = [
        RecipeSchema(
            recipe_id="rec_herb_bread_01",
            title="Rustic Herb Bread",
            description="Crusty artisan loaf with rosemary and thyme.",
            yield_servings="1 loaf",
            prep_time="20 mins",
            cook_time="35 mins",
            total_time="3 hrs",
            ingredients=["3 cups bread flour", "1 tsp salt", "1 packet yeast", "1 tbsp chopped fresh rosemary"],
            directions=[
                "1. Mix flour, salt, yeast, and water to form shaggy dough.",
                "2. Let rise for 2 hours.",
                "3. Shape into loaf, top with rosemary, and bake at 425°F for 35 minutes.",
            ],
            tags=["Baking", "Bread", "Herbs"],
            source_notes="Aunt Clara's handwritten recipe.",
            card_ids=[card_id],
        )
    ]
    return sid


def build_minimal_archive_bytes(project_name: str = "Test Archive") -> bytes:
    """Constructs a valid in-memory .recipeboxer zip archive."""
    buf = io.BytesIO()
    img_bytes = make_test_card_image("purple")
    sha = hashlib.sha256(img_bytes).hexdigest()

    manifest = {
        "format_version": "1.0.0",
        "app_version": "1.0.0",
        "schema_version": "1.0.0",
        "project_id": "proj_mem_01",
        "project_name": project_name,
        "assets": [
            {
                "sha256_hash": sha,
                "filename": "card_purple.jpg",
                "archive_path": f"assets/scans/{sha}.jpg",
                "mime_type": "image/jpeg",
                "size_bytes": len(img_bytes),
            }
        ],
        "cards": [
            {
                "card_id": "card_purple_01",
                "sha256_hash": sha,
                "filename": "card_purple.jpg",
                "role": "STANDALONE",
                "role_confidence": 0.99,
            }
        ],
        "proposals": [],
        "recipes": [
            {
                "recipe_id": "rec_purple_cake",
                "title": "Blueberry Lemon Cake",
                "description": "Zesty summer cake.",
                "yield_servings": "8 servings",
                "ingredients": ["2 cups flour", "1 cup sugar", "1 cup blueberries"],
                "directions": ["1. Fold blueberries into batter.", "2. Bake at 350F for 40 mins."],
                "tags": ["Dessert", "Cake"],
                "source_notes": "Summer baking collection.",
                "card_ids": ["card_purple_01"],
            }
        ],
    }

    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr(f"assets/scans/{sha}.jpg", img_bytes)

    return buf.getvalue()


# ============================================================================
# TIER 1: FEATURE COVERAGE (Nominal Happy Path)
# ============================================================================

@pytest.mark.asyncio
class TestProjectEndpointsTier1:
    """Tier 1: Happy path export, import, and merge endpoint execution."""

    async def test_export_project_archive_success(self, client: httpx.AsyncClient, active_session_with_recipes: str):
        """F3.1: GET /api/project/export returns a valid .recipeboxer binary stream."""
        resp = await client.get(f"/api/project/export?session_id={active_session_with_recipes}&filename=herb_bread_project")
        assert resp.status_code == 200
        assert "application/x-recipeboxer" in resp.headers.get("content-type", "") or "application/octet-stream" in resp.headers.get("content-type", "") or "application/zip" in resp.headers.get("content-type", "")
        
        # Verify content disposition header
        cd = resp.headers.get("content-disposition", "")
        assert "herb_bread_project" in cd or "recipeboxer" in cd

        # Verify zip structure
        zip_bytes = resp.content
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            assert "manifest.json" in zf.namelist()
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            assert len(manifest["recipes"]) == 1
            assert manifest["recipes"][0]["title"] == "Rustic Herb Bread"

    async def test_import_project_archive_success(self, client: httpx.AsyncClient):
        """F3.2: POST /api/project/import uploads an archive and restores session state."""
        archive_bytes = build_minimal_archive_bytes("Imported Blueberry Cake")
        files = {
            "file": ("imported_project.recipeboxer", archive_bytes, "application/x-recipeboxer")
        }

        resp = await client.post("/api/project/import", files=files)
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert "session_id" in data
        restored_sid = data["session_id"]

        # Fetch recipes from the restored session
        recipes_resp = await client.get(f"/api/recipes?session_id={restored_sid}")
        assert recipes_resp.status_code == 200
        recipes = recipes_resp.json()
        assert len(recipes) == 1
        assert recipes[0]["title"] == "Blueberry Lemon Cake"

    async def test_merge_project_archives_success(self, client: httpx.AsyncClient):
        """F3.3: POST /api/project/merge merges base archive and incoming archive into unified stream."""
        base_bytes = build_minimal_archive_bytes("Base Cake Project")
        incoming_bytes = build_minimal_archive_bytes("Incoming Pie Project")

        files = {
            "base_archive": ("base.recipeboxer", base_bytes, "application/x-recipeboxer"),
            "incoming_archive": ("incoming.recipeboxer", incoming_bytes, "application/x-recipeboxer"),
        }

        resp = await client.post("/api/project/merge", files=files)
        assert resp.status_code == 200
        
        # Verify returned zip
        merged_zip = resp.content
        with zipfile.ZipFile(io.BytesIO(merged_zip), "r") as zf:
            assert "manifest.json" in zf.namelist()
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            assert len(manifest["recipes"]) >= 1


# ============================================================================
# TIER 2: BOUNDARY & CORNER CASES (Validation & Error Handling)
# ============================================================================

@pytest.mark.asyncio
class TestProjectEndpointsTier2Boundary:
    """Tier 2: Error handling, missing sessions, bad zip uploads, and ZipSlip attacks."""

    async def test_export_nonexistent_session_404(self, client: httpx.AsyncClient):
        """F3.4: GET /api/project/export with invalid session ID returns 404."""
        resp = await client.get("/api/project/export?session_id=nonexistent_session_999")
        assert resp.status_code == 404

    async def test_import_corrupted_archive_400(self, client: httpx.AsyncClient):
        """F3.5: POST /api/project/import with corrupt non-zip file returns 400 / 422."""
        files = {
            "file": ("corrupt.recipeboxer", b"THIS_IS_NOT_A_ZIP_FILE", "application/x-recipeboxer")
        }
        resp = await client.post("/api/project/import", files=files)
        assert resp.status_code in (400, 422)

    async def test_import_zipslip_rejection_400(self, client: httpx.AsyncClient):
        """F3.6: POST /api/project/import with ZipSlip path traversal payload returns 400."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"format_version": "1.0.0", "recipes": []}))
            zf.writestr("../../etc/passwd", "root:x:0:0:root")
        
        files = {
            "file": ("exploit.recipeboxer", buf.getvalue(), "application/x-recipeboxer")
        }
        resp = await client.post("/api/project/import", files=files)
        assert resp.status_code in (400, 422)

    async def test_export_filename_sanitization(self, client: httpx.AsyncClient, active_session_with_recipes: str):
        """F3.7: Dangerous characters in suggested filename query parameter are sanitized."""
        resp = await client.get(
            f"/api/project/export?session_id={active_session_with_recipes}&filename=../../evil;name<script>"
        )
        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        assert ".." not in cd
        assert "<script>" not in cd


# ============================================================================
# TIER 3 & 4: CROSS-FEATURE & REAL-WORLD SCENARIOS
# ============================================================================

@pytest.mark.asyncio
class TestProjectEndpointsTier3And4Scenarios:
    """Tier 3 & 4: Multi-session roundtrip workflows and collaborative archive merging."""

    async def test_export_import_full_fidelity_workflow(self, client: httpx.AsyncClient, active_session_with_recipes: str):
        """T3.1: Export session -> Import to new session -> Verify all recipe details match."""
        # 1. Export active session
        export_resp = await client.get(f"/api/project/export?session_id={active_session_with_recipes}")
        assert export_resp.status_code == 200
        archive_bytes = export_resp.content

        # 2. Import into a new session
        files = {"file": ("exported.recipeboxer", archive_bytes, "application/x-recipeboxer")}
        import_resp = await client.post("/api/project/import", files=files)
        assert import_resp.status_code in (200, 201)
        new_sid = import_resp.json()["session_id"]

        # 3. Query recipes from the new session
        recipes_resp = await client.get(f"/api/recipes?session_id={new_sid}")
        assert recipes_resp.status_code == 200
        recipes = recipes_resp.json()
        assert len(recipes) == 1
        assert recipes[0]["title"] == "Rustic Herb Bread"
        assert "rosemary" in " ".join(recipes[0]["ingredients"])

    async def test_multi_branch_family_archive_merge_scenario(self, client: httpx.AsyncClient):
        """Scenario 3: Two family branches export archives, merged into unified cookbook."""
        branch_a_bytes = build_minimal_archive_bytes("Grandma Desserts")
        branch_b_bytes = build_minimal_archive_bytes("Grandpa Roasts")

        files = {
            "base_archive": ("desserts.recipeboxer", branch_a_bytes, "application/x-recipeboxer"),
            "incoming_archive": ("roasts.recipeboxer", branch_b_bytes, "application/x-recipeboxer"),
        }

        merge_resp = await client.post("/api/project/merge", files=files)
        assert merge_resp.status_code == 200
        merged_zip = merge_resp.content

        # Import the merged archive to verify session works
        import_files = {"file": ("merged.recipeboxer", merged_zip, "application/x-recipeboxer")}
        import_resp = await client.post("/api/project/import", files=import_files)
        assert import_resp.status_code in (200, 201)
        merged_sid = import_resp.json()["session_id"]

        recipes_resp = await client.get(f"/api/recipes?session_id={merged_sid}")
        assert recipes_resp.status_code == 200
        recipes = recipes_resp.json()
        assert len(recipes) >= 1
