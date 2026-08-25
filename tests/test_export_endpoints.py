"""Integration tests for all FastAPI export REST endpoints (recipeboxer/api/routes.py)."""

import gzip
import json
import zipfile
import io
from pathlib import Path
from typing import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from recipeboxer.agent.session import SessionManager
from recipeboxer.api.app import create_app
from recipeboxer.exporter.paprika import PaprikaExporter
from recipeboxer.models.pipeline import PipelineState
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.storage.session_store import SessionStore
from tests.conftest import MockGeminiClient, SQLiteFingerprintCache


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
def populated_session(test_session_manager: SessionManager, app) -> str:
    """Populates an active completed session with sample recipes."""
    sid = "sess_export_integration_test"
    session = test_session_manager.create_session(session_id=sid)
    session.state = PipelineState.COMPLETED

    r1 = RecipeSchema(
        recipe_id="rec_api_01",
        title="Spiced Apple Crisp",
        description="Warm baked apples under oat streusel.",
        yield_servings="6 servings",
        prep_time="15 mins",
        cook_time="35 mins",
        total_time="50 mins",
        ingredients=[
            "5 honeycrisp apples, sliced",
            "1 cup rolled oats",
            "1/2 cup brown sugar",
            "1/2 cup cold butter",
            "1 tsp ground cinnamon",
        ],
        directions=[
            "1. Arrange sliced apples in a buttered baking dish.",
            "2. Combine oats, brown sugar, butter, and cinnamon into crumbs.",
            "3. Spread topping over apples and bake at 350°F for 35 minutes.",
        ],
        tags=["Dessert", "Autumn", "Fruit"],
        source_notes="Grandma's recipe box",
        card_ids=["card_apple_crisp_01"],
    )
    session.recipes = [r1]

    # Save to SQLite store as well
    store: SessionStore = getattr(app.state, "session_store", None)
    if store:
        store.save_session(session_id=sid, state=PipelineState.COMPLETED, recipe_count=1)
        store.save_recipes(session_id=sid, recipes=[r1])

    return sid


class TestExportEndpoints:
    """Comprehensive test suite for all 9 export format API endpoints."""

    @pytest.mark.asyncio
    async def test_export_docx_endpoint(self, client: httpx.AsyncClient, populated_session: str):
        res = await client.get(f"/api/export/docx?session_id={populated_session}")
        assert res.status_code == 200
        assert "application/vnd.openxmlformats-officedocument.wordprocessingml.document" in res.headers["content-type"]
        assert 'filename="recipeboxer_cookbook.docx"' in res.headers["content-disposition"]
        assert len(res.content) > 0

        # Verify docx is valid zip
        zip_buf = io.BytesIO(res.content)
        with zipfile.ZipFile(zip_buf, "r") as zf:
            assert "word/document.xml" in zf.namelist()

    @pytest.mark.asyncio
    async def test_export_pdf_endpoint(self, client: httpx.AsyncClient, populated_session: str):
        res = await client.get(f"/api/export/pdf?session_id={populated_session}")
        assert res.status_code == 200
        assert "application/pdf" in res.headers["content-type"]
        assert 'filename="recipeboxer_cookbook.pdf"' in res.headers["content-disposition"]
        assert res.content.startswith(b"%PDF-1.4")

    @pytest.mark.asyncio
    async def test_export_markdown_endpoints(self, client: httpx.AsyncClient, populated_session: str):
        # Primary endpoint
        res = await client.get(f"/api/export/markdown?session_id={populated_session}")
        assert res.status_code == 200
        assert "text/markdown" in res.headers["content-type"]
        assert 'filename="recipeboxer_recipes.md"' in res.headers["content-disposition"]
        assert "Spiced Apple Crisp" in res.text
        assert "title:" in res.text

        # Alias endpoint
        res_alias = await client.get(f"/api/export/md?session_id={populated_session}")
        assert res_alias.status_code == 200
        assert "Spiced Apple Crisp" in res_alias.text

    @pytest.mark.asyncio
    async def test_export_cooklang_endpoints(self, client: httpx.AsyncClient, populated_session: str):
        # Primary endpoint
        res = await client.get(f"/api/export/cooklang?session_id={populated_session}")
        assert res.status_code == 200
        assert "text/plain" in res.headers["content-type"]
        assert 'filename="recipeboxer_recipes.cook"' in res.headers["content-disposition"]
        assert ">> title: Spiced Apple Crisp" in res.text

        # Alias endpoint
        res_alias = await client.get(f"/api/export/cook?session_id={populated_session}")
        assert res_alias.status_code == 200
        assert ">> title: Spiced Apple Crisp" in res_alias.text

    @pytest.mark.asyncio
    async def test_export_paprika_endpoint(self, client: httpx.AsyncClient, populated_session: str):
        res = await client.get(f"/api/export/paprika?session_id={populated_session}")
        assert res.status_code == 200
        assert "application/x-paprikarecipes" in res.headers["content-type"]
        assert 'filename="recipeboxer_export.paprikarecipes"' in res.headers["content-disposition"]

        # Decompress gzip
        decompressed = gzip.decompress(res.content)
        parsed = json.loads(decompressed.decode("utf-8"))
        assert parsed["name"] == "Spiced Apple Crisp"

    @pytest.mark.asyncio
    async def test_export_google_docs_endpoints(self, client: httpx.AsyncClient, populated_session: str):
        # POST batch requests (default)
        res = await client.post(
            "/api/export/google-docs",
            json={"session_id": populated_session, "format": "batch_requests", "title": "Test Docs"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["title"] == "Test Docs"
        assert "requests" in data
        assert len(data["requests"]) > 0

        # POST HTML format (case-insensitive)
        res_html = await client.post(
            "/api/export/google-docs",
            json={"session_id": populated_session, "format": "HTML"},
        )
        assert res_html.status_code == 200
        assert "text/html" in res_html.headers["content-type"]
        assert "<!DOCTYPE html>" in res_html.text

        # GET alias (case-insensitive)
        res_get = await client.get(f"/api/export/google-docs?session_id={populated_session}&format=JSON")
        assert res_get.status_code == 200
        assert "application/json" in res_get.headers["content-type"]

    @pytest.mark.asyncio
    async def test_export_nonexistent_session_404(self, client: httpx.AsyncClient):
        res = await client.get("/api/export/docx?session_id=sess_nonexistent_12345")
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_export_session_isolation_no_leakage(
        self,
        client: httpx.AsyncClient,
        test_session_manager: SessionManager,
        populated_session: str,
        app,
    ):
        """Adversarial check: Empty completed session MUST return 400 and NOT leak recipes from populated_session."""
        empty_sid = "sess_empty_completed"
        sess = test_session_manager.create_session(session_id=empty_sid)
        sess.state = PipelineState.COMPLETED
        sess.recipes = []

        # Request export for empty session
        res = await client.get(f"/api/export/docx?session_id={empty_sid}")
        assert res.status_code == 400
        assert "No recipes available" in res.json()["detail"]

        res_pdf = await client.get(f"/api/export/pdf?session_id={empty_sid}")
        assert res_pdf.status_code == 400

        res_md = await client.get(f"/api/export/markdown?session_id={empty_sid}")
        assert res_md.status_code == 400

    @pytest.mark.asyncio
    async def test_export_paprika_as_zip_endpoint(
        self,
        client: httpx.AsyncClient,
        populated_session: str,
    ):
        """Verify as_zip query param on paprika endpoint returns zip archive."""
        res = await client.get(f"/api/export/paprika?session_id={populated_session}&as_zip=true")
        assert res.status_code == 200
        # Check that it's a valid archive
        records = PaprikaExporter.decompress_paprika_archive(res.content)
        assert len(records) >= 1
        assert records[0]["name"] == "Spiced Apple Crisp"

    @pytest.mark.asyncio
    async def test_export_primary_csv_and_json_ld(self, client: httpx.AsyncClient, populated_session: str):
        # R0: Plan To Eat CSV
        res_csv = await client.get(f"/api/export/csv?session_id={populated_session}")
        assert res_csv.status_code == 200
        assert "text/csv" in res_csv.headers["content-type"]
        assert "Title,Description,Ingredients" in res_csv.text

        # R0: Schema.org JSON-LD
        res_ld = await client.get(f"/api/export/json-ld?session_id={populated_session}")
        assert res_ld.status_code == 200
        assert "application/ld+json" in res_ld.headers["content-type"]
        data = res_ld.json()
        assert data["@context"] == "https://schema.org"
        assert data["@type"] == "Recipe"
