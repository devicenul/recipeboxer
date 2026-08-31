"""Integration tests for all FastAPI export REST endpoints (recipeboxer/api/routes.py)."""

import gzip
import json
import zipfile
import io
from pathlib import Path
from typing import Any, AsyncGenerator

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

    @pytest.mark.asyncio
    async def test_export_endpoints_custom_filename(self, client: httpx.AsyncClient, populated_session: str):
        """GAP-02: Verify custom filename query parameter is reflected in Content-Disposition across all formats."""
        # DOCX
        res_docx = await client.get(f"/api/export/docx?session_id={populated_session}&filename=grandma_cookbook.docx")
        assert res_docx.status_code == 200
        assert 'filename="grandma_cookbook.docx"' in res_docx.headers["content-disposition"]

        # PDF
        res_pdf = await client.get(f"/api/export/pdf?session_id={populated_session}&filename=my_holiday_pie.pdf")
        assert res_pdf.status_code == 200
        assert 'filename="my_holiday_pie.pdf"' in res_pdf.headers["content-disposition"]

        # CSV
        res_csv = await client.get(f"/api/export/csv?session_id={populated_session}&filename=custom_plan_to_eat.csv")
        assert res_csv.status_code == 200
        assert 'filename="custom_plan_to_eat.csv"' in res_csv.headers["content-disposition"]

        # Markdown
        res_md = await client.get(f"/api/export/markdown?session_id={populated_session}&filename=obsidian_notes.md")
        assert res_md.status_code == 200
        assert 'filename="obsidian_notes.md"' in res_md.headers["content-disposition"]

        # Cooklang
        res_cook = await client.get(f"/api/export/cooklang?session_id={populated_session}&filename=apple_crisp.cook")
        assert res_cook.status_code == 200
        assert 'filename="apple_crisp.cook"' in res_cook.headers["content-disposition"]

        # Paprika
        res_paprika = await client.get(f"/api/export/paprika?session_id={populated_session}&filename=paprika_recipes.paprikarecipes")
        assert res_paprika.status_code == 200
        assert 'filename="paprika_recipes.paprikarecipes"' in res_paprika.headers["content-disposition"]

        # Google Docs HTML
        res_gdocs = await client.get(f"/api/export/google-docs?session_id={populated_session}&format=html&filename=shared_recipes.html")
        assert res_gdocs.status_code == 200
        assert 'filename="shared_recipes.html"' in res_gdocs.headers["content-disposition"]

    @pytest.mark.asyncio
    async def test_export_filename_sanitization_path_traversal(self, client: httpx.AsyncClient, populated_session: str):
        """Verify filenames are sanitized against path traversal (../) and illegal characters."""
        res = await client.get(f"/api/export/pdf?session_id={populated_session}&filename=../../../../etc/malicious.pdf")
        assert res.status_code == 200
        assert 'filename="malicious.pdf"' in res.headers["content-disposition"]

        res_illegal = await client.get(f"/api/export/docx?session_id={populated_session}&filename=bad:name*?<>|.docx")
        assert res_illegal.status_code == 200
        assert 'filename="badname.docx"' in res_illegal.headers["content-disposition"]

    @pytest.mark.asyncio
    async def test_google_docs_create_document_endpoint(self, client: httpx.AsyncClient, populated_session: str, monkeypatch):
        """GAP-01: Test POST /api/export/google-docs/create-document endpoint."""
        # 1. Missing token -> 400
        res_missing = await client.post(
            "/api/export/google-docs/create-document",
            json={"session_id": populated_session, "access_token": ""},
        )
        assert res_missing.status_code == 400

        # 2. Mocked successful creation
        def mock_create(self, access_token, recipes, title="RecipeBoxer Archival Cookbook", **kwargs):
            return {
                "document_id": "doc_abc_12345",
                "url": "https://docs.google.com/document/d/doc_abc_12345/edit",
                "title": title,
                "recipe_count": len(recipes),
            }

        monkeypatch.setattr(
            "recipeboxer.exporter.google_docs.GoogleDocsExporter.create_google_document",
            mock_create,
        )

        res_success = await client.post(
            "/api/export/google-docs/create-document",
            json={
                "session_id": populated_session,
                "access_token": "ya29.valid_mock_token",
                "document_title": "Family Heirloom Collection",
            },
        )
        assert res_success.status_code == 200
        data = res_success.json()
        assert data["document_id"] == "doc_abc_12345"
        assert "https://docs.google.com/document/d/doc_abc_12345/edit" in data["url"]
        assert data["title"] == "Family Heirloom Collection"
        assert data["recipe_count"] == 1

        # 3. 401 Unauthorized token
        def mock_create_401(self, access_token, recipes, **kwargs):
            raise ValueError("Invalid or expired Google OAuth access token (401 Unauthorized)")

        monkeypatch.setattr(
            "recipeboxer.exporter.google_docs.GoogleDocsExporter.create_google_document",
            mock_create_401,
        )

        res_401 = await client.post(
            "/api/export/google-docs/create-document",
            json={"session_id": populated_session, "access_token": "ya29.expired_token"},
        )
        assert res_401.status_code == 401
        assert "authorization failed" in res_401.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_tag_serialization_across_all_formats(self, client: httpx.AsyncClient, populated_session: str):
        """F5.4 & GAP-05: Enforce tag serialization compliance across Plan to Eat, JSON-LD, Markdown, Cooklang, Paprika."""
        # 1. Plan to Eat CSV: Col 8 "Tags" contains "Dessert, Autumn, Fruit"
        res_csv = await client.get(f"/api/export/csv?session_id={populated_session}")
        assert res_csv.status_code == 200
        assert '"Dessert, Autumn, Fruit"' in res_csv.text or "Dessert, Autumn, Fruit" in res_csv.text

        # 2. Schema.org JSON-LD: keywords & recipeCategory
        res_ld = await client.get(f"/api/export/json-ld?session_id={populated_session}")
        assert res_ld.status_code == 200
        data_ld = res_ld.json()
        assert "Dessert" in data_ld["keywords"]
        assert "Autumn" in data_ld["keywords"]
        assert "Fruit" in data_ld["keywords"]
        assert data_ld["recipeCategory"] == "Dessert"

        # 3. Obsidian Markdown: YAML frontmatter tags
        res_md = await client.get(f"/api/export/markdown?session_id={populated_session}")
        assert res_md.status_code == 200
        assert "tags:" in res_md.text
        assert "- Dessert" in res_md.text
        assert "- Autumn" in res_md.text
        assert "- Fruit" in res_md.text

        # 4. Cooklang: >> tags:
        res_cook = await client.get(f"/api/export/cooklang?session_id={populated_session}")
        assert res_cook.status_code == 200
        assert ">> tags: Dessert, Autumn, Fruit" in res_cook.text

        # 5. Paprika: categories list
        res_paprika = await client.get(f"/api/export/paprika?session_id={populated_session}")
        assert res_paprika.status_code == 200
        decomp = gzip.decompress(res_paprika.content)
        paprika_data = json.loads(decomp.decode("utf-8"))
        assert paprika_data["categories"] == ["Dessert", "Autumn", "Fruit"]

    @pytest.mark.asyncio
    async def test_export_project_archive_endpoint(self, client: httpx.AsyncClient, populated_session: str):
        res = await client.get(f"/api/project/export?session_id={populated_session}")
        assert res.status_code == 200
        assert "application/x-recipeboxer" in res.headers["content-type"]
        assert f"{populated_session}.recipeboxer" in res.headers["content-disposition"]
        assert len(res.content) > 0

        # Verify zip structure
        zip_buf = io.BytesIO(res.content)
        with zipfile.ZipFile(zip_buf, "r") as zf:
            names = zf.namelist()
            assert "manifest.json" in names
            manifest_dict = json.loads(zf.read("manifest.json").decode("utf-8"))
            assert manifest_dict["format_version"] == "1.0.0"
            assert len(manifest_dict["recipes"]) == 1
            assert manifest_dict["recipes"][0]["title"] == "Spiced Apple Crisp"

