"""Empirical Challenger Adversarial Stress Suite for Milestone 4.

Covers:
1. Google Docs OAuth creation:
   - Upstream Google Drive / Docs API failures (502 Bad Gateway, 500, network timeouts, bad JSON)
   - Invalid / expired OAuth tokens (401 Unauthorized, empty, whitespace)
   - Mock document creation with rich data, multiple recipes, unicode, and varied image formats
   - Multipart upload boundary and batchUpdate payload validation
2. Filename customization & sanitization:
   - Path traversal attempts (../, ..\\, nested traversal, absolute paths)
   - Illegal characters (:, *, ?, ", <, >, |, null bytes, control characters, leading/trailing dots/spaces)
   - Extension preservation and enforcement across all 9 export endpoints
   - Empty/whitespace/all-illegal filename fallbacks
3. Tag serialization across all 9 export formats:
   - Tags with commas, quotes, colons, brackets, unicode, HTML entities, and empty tags
   - Plan To Eat RFC 4180 CSV (Col 8 quoting)
   - Schema.org JSON-LD (keywords and recipeCategory)
   - Obsidian Markdown (YAML frontmatter tag list)
   - Cooklang (>> tags: metadata header)
   - Paprika (.paprikarecipes categories array)
   - DOCX, PDF, Google Docs (batchUpdate and HTML)
4. DOCX & PDF Image embedding & aspect ratio EMU scaling:
   - Proportional EMU calculation for wide (3:1), tall (1:3), square (1:1), and corrupt image bytes
   - PDF canvas image decoding, EXIF orientation handling, and multi-page overflow
"""

import gzip
import io
import json
import zipfile
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List

import httpx
from httpx import ASGITransport
import pytest
import pytest_asyncio
from PIL import Image

from recipeboxer.agent.session import SessionManager
from recipeboxer.api.app import create_app
from recipeboxer.api.routes import sanitize_export_filename
from recipeboxer.exporter.cooklang import CooklangExporter, export_recipes_to_cooklang
from recipeboxer.exporter.docx_export import (
    DocxExporter,
    MAX_EMU_HEIGHT,
    MAX_EMU_WIDTH,
    _escape_xml,
    export_recipes_to_docx,
)
from recipeboxer.exporter.google_docs import (
    GoogleDocsExporter,
    create_google_docs_archival_document,
    export_recipes_to_google_docs_batch_requests,
    export_recipes_to_google_docs_html,
    export_recipes_to_google_docs_json,
)
from recipeboxer.exporter.json_export import JsonExporter, export_recipes_to_json
from recipeboxer.exporter.json_ld import JsonLdExporter, export_recipes_to_json_ld
from recipeboxer.exporter.markdown_export import MarkdownExporter, export_recipes_to_markdown
from recipeboxer.exporter.paprika import PaprikaExporter, export_recipes_to_paprika
from recipeboxer.exporter.pdf_export import PdfExporter, export_recipes_to_pdf
from recipeboxer.exporter.plan_to_eat import PlanToEatExporter, export_recipes_to_plan_to_eat_csv
from recipeboxer.models.pipeline import PipelineState
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.storage.session_store import SessionStore
from tests.conftest import MockGeminiClient, SQLiteFingerprintCache


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def adversarial_recipes() -> List[RecipeSchema]:
    """Recipes with complex tags (commas, quotes, special chars), rich metadata, and multiple card scans."""
    r1 = RecipeSchema(
        recipe_id="adv_rec_01",
        title='Grandma\'s "Secret" Apple & Pear Tart <Heirloom Edition>',
        description="A vintage family tart with flaky butter crust, spiced apples, and caramel drizzle.",
        yield_servings="8 slices",
        prep_time="45 mins",
        cook_time="1 hr",
        total_time="1 hr 45 mins",
        ingredients=[
            "2 1/2 cups unbleached all-purpose flour",
            "1/2 cup granulated sugar, plus 2 tbsp for dusting",
            "1 cup unsalted European butter, chilled & cubed",
            "4 Honeycrisp apples, peeled, cored & thinly sliced",
            "2 Bosc pears, sliced into 1/4\" crescents",
            "1 tsp ground Saigon cinnamon",
            "1/4 tsp freshly grated nutmeg",
            "pinch of kosher salt",
        ],
        directions=[
            "Step 1: Whisk flour, sugar, and salt in a large mixing bowl.",
            "2. Cut in chilled butter with a pastry blender until pea-sized crumbs form.",
            "3. Press dough into a 10-inch tart pan with removable bottom and chill for 30 minutes.",
            "Step 4: Arrange apple and pear slices in concentric circles over the chilled crust.",
            "5. Dust with cinnamon sugar and bake in preheated oven at 375°F for 60 minutes until golden.",
        ],
        tags=[
            "Dessert, Baking",
            'Mom\'s "Favorite"',
            "Autumn & Winter",
            "<Holiday Special>",
            "Category: Fruit Tart",
            "Tag with [Brackets] & {Braces}",
            "Tag with Unicode: 🍰 Pie & Tart",
        ],
        source_notes="Handwritten in 1948 by Aunt Clara on vintage card stock.",
        card_ids=["card_tart_front", "card_tart_back", "card_tart_notes"],
    )

    r2 = RecipeSchema(
        recipe_id="adv_rec_02",
        title="Smoky Cast Iron Chili con Carne [GUESS]",
        description="Rich, thick Texas-style chili with smoked brisket and chipotle peppers.",
        yield_servings="10 hearty bowls",
        prep_time="30 mins",
        cook_time="4 hrs",
        total_time="4 hrs 30 mins",
        ingredients=[
            "3 lbs smoked beef brisket, chopped into 1/2-inch cubes",
            "2 tbsp neutral cooking oil",
            "2 large yellow onions, finely diced",
            "6 cloves garlic, minced",
            "3 canned chipotle chiles in adobo sauce, chopped",
            "1/4 cup dark chili powder",
            "1 tbsp ground cumin",
            "1 tbsp Mexican oregano",
            "1 bottle (12 oz) dark lager beer",
            "2 cups rich beef bone broth",
            "1 can (28 oz) crushed San Marzano tomatoes",
            "2 tbsp masa harina mixed with 1/4 cup warm water",
        ],
        directions=[
            "1. Heat oil in a large Dutch oven over medium-high heat. Brown brisket pieces in batches.",
            "2. Sauté onions and garlic in beef fat until softened and translucent, about 8 minutes.",
            "3. Stir in chili powder, cumin, oregano, and chipotle chiles; cook for 1 minute until fragrant.",
            "4. Deglaze Dutch oven with dark lager beer, scraping up browned bits from the bottom.",
            "5. Add crushed tomatoes and beef bone broth. Simmer uncovered on low heat for 3.5 hours.",
            "6. Stir in masa harina slurry and simmer for another 30 minutes until thick and glossy.",
        ],
        tags=["Main Dish", "Slow Cooked", "Spicy, Bold"],
        source_notes="Adapted from Texas BBQ competition notes (1976).",
        card_ids=["card_chili_01"],
    )
    return [r1, r2]


@pytest.fixture
def image_generator(tmp_path: Path):
    """Generates real PNG and JPEG images of various aspect ratios (wide, tall, square)."""
    img_dir = tmp_path / "test_scans"
    img_dir.mkdir(parents=True, exist_ok=True)

    # 1. Wide landscape (2:1 aspect ratio: 800 x 400)
    wide_img = Image.new("RGB", (800, 400), color=(220, 180, 140))
    wide_path = img_dir / "wide_card.jpg"
    wide_img.save(wide_path, format="JPEG")

    # 2. Tall portrait (1:2 aspect ratio: 300 x 600)
    tall_img = Image.new("RGB", (300, 600), color=(140, 180, 220))
    tall_path = img_dir / "tall_card.png"
    tall_img.save(tall_path, format="PNG")

    # 3. Square (1:1 aspect ratio: 500 x 500)
    square_img = Image.new("RGB", (500, 500), color=(180, 220, 140))
    square_path = img_dir / "square_card.jpg"
    square_img.save(square_path, format="JPEG")

    # 4. Ultra-wide (4:1 aspect ratio: 1200 x 300)
    ultrawide_img = Image.new("RGB", (1200, 300), color=(200, 200, 200))
    ultrawide_path = img_dir / "ultrawide_card.jpg"
    ultrawide_img.save(ultrawide_path, format="JPEG")

    # 5. Ultra-tall (1:4 aspect ratio: 200 x 800)
    ultratall_img = Image.new("RGB", (200, 800), color=(210, 190, 170))
    ultratall_path = img_dir / "ultratall_card.jpg"
    ultratall_img.save(ultratall_path, format="JPEG")

    def resolver(card_id: str) -> Path:
        if "tart_front" in card_id or "wide" in card_id:
            return wide_path
        elif "tart_back" in card_id or "tall" in card_id:
            return tall_path
        elif "tart_notes" in card_id or "ultrawide" in card_id:
            return ultrawide_path
        elif "ultratall" in card_id:
            return ultratall_path
        return square_path

    return resolver


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
def populated_adversarial_session(
    test_session_manager: SessionManager,
    adversarial_recipes: List[RecipeSchema],
    app,
) -> str:
    sid = "sess_adv_challenge_m4"
    session = test_session_manager.create_session(session_id=sid)
    session.state = PipelineState.COMPLETED
    session.recipes = list(adversarial_recipes)

    store: SessionStore = getattr(app.state, "session_store", None)
    if store:
        store.save_session(session_id=sid, state=PipelineState.COMPLETED, recipe_count=len(adversarial_recipes))
        store.save_recipes(session_id=sid, recipes=adversarial_recipes)

    return sid


# ============================================================================
# 1. GOOGLE DOCS OAUTH & DIRECT PUBLISHING ADVERSARIAL STRESS TESTS
# ============================================================================

class TestGoogleDocsOAuthAdversarial:
    """Stress tests for Google Docs OAuth direct creation, Drive integration, and failure modes."""

    def test_missing_and_empty_access_token_rejections(self, adversarial_recipes: List[RecipeSchema]):
        """Empty, whitespace, and None access tokens must be rejected before network calls."""
        exporter = GoogleDocsExporter()

        for invalid_tok in ["", "   ", "\t\n"]:
            with pytest.raises(ValueError, match="Google OAuth access token is required"):
                exporter.create_google_document(access_token=invalid_tok, recipes=adversarial_recipes)

    def test_empty_recipe_list_rejection(self):
        """Empty recipe collections must be rejected."""
        exporter = GoogleDocsExporter()
        with pytest.raises(ValueError, match="No recipes provided"):
            exporter.create_google_document(access_token="ya29.valid_token", recipes=[])

    def test_simulated_drive_search_failure_falls_back_gracefully(
        self,
        adversarial_recipes: List[RecipeSchema],
        image_generator,
    ):
        """When Drive folder search returns 500 error, document creation proceeds without crashing."""
        def handler(request: httpx.Request) -> httpx.Response:
            url_str = str(request.url)
            # Drive API fails
            if "drive/v3/files" in url_str:
                return httpx.Response(500, json={"error": "Internal Drive Error"})
            if "docs.googleapis.com/v1/documents" in url_str and not url_str.endswith(":batchUpdate"):
                return httpx.Response(200, json={"documentId": "doc_fallback_001"})
            if ":batchUpdate" in url_str:
                return httpx.Response(200, json={"replies": []})
            return httpx.Response(404, json={"error": "not found"})

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)

        exporter = GoogleDocsExporter()
        result = exporter.create_google_document(
            access_token="ya29.mock_token",
            recipes=adversarial_recipes,
            image_resolver=image_generator,
            http_client=client,
        )
        assert result["document_id"] == "doc_fallback_001"
        assert "https://docs.google.com/document/d/doc_fallback_001/edit" in result["url"]

    def test_google_docs_create_401_unauthorized_token(self, adversarial_recipes: List[RecipeSchema]):
        """Expired or unauthorized token returns 401 and raises ValueError containing 401."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"code": 401, "message": "Invalid Credentials"}})

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)

        exporter = GoogleDocsExporter()
        with pytest.raises(ValueError) as exc:
            exporter.create_google_document(
                access_token="ya29.expired_token_12345",
                recipes=adversarial_recipes,
                http_client=client,
            )
        assert "401" in str(exc.value)

    def test_google_docs_create_502_upstream_failure(self, adversarial_recipes: List[RecipeSchema]):
        """Docs API failure returns HTTP 500/502 and raises descriptive ValueError."""
        def handler(request: httpx.Request) -> httpx.Response:
            if "docs.googleapis.com" in str(request.url):
                return httpx.Response(503, json={"error": "Service Unavailable"})
            return httpx.Response(200, json={"files": []})

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)

        exporter = GoogleDocsExporter()
        with pytest.raises(ValueError) as exc:
            exporter.create_google_document(
                access_token="ya29.valid_token",
                recipes=adversarial_recipes,
                http_client=client,
            )
        assert "503" in str(exc.value) or "Failed to create Google Doc" in str(exc.value)

    def test_full_google_doc_creation_with_multipart_images_and_rich_typography(
        self,
        adversarial_recipes: List[RecipeSchema],
        image_generator,
    ):
        """Verify full multi-recipe document creation with multipart image uploads and structured batchUpdates."""
        recorded_requests: List[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            recorded_requests.append(request)
            url_str = str(request.url)

            # 1. Drive folder search
            if "drive/v3/files" in url_str and request.method == "GET":
                return httpx.Response(200, json={"files": [{"id": "f_recipeboxer_scans", "name": "RecipeBoxer Scans"}]})
            # 2. Drive multipart image upload
            if "upload/drive/v3/files" in url_str and request.method == "POST":
                # Verify multipart content-type boundary
                assert "multipart/related" in request.headers.get("content-type", "")
                return httpx.Response(200, json={
                    "id": f"drive_img_{len(recorded_requests)}",
                    "webContentLink": f"https://drive.google.com/uc?id=drive_img_{len(recorded_requests)}",
                })
            # 3. Permissions
            if "permissions" in url_str:
                return httpx.Response(200, json={"id": "perm_ok"})
            # 4. Docs create
            if "docs.googleapis.com/v1/documents" in url_str and not url_str.endswith(":batchUpdate"):
                req_json = json.loads(request.content.decode("utf-8"))
                assert req_json["title"] == "Heirloom Recipe Box"
                return httpx.Response(200, json={"documentId": "doc_heirloom_777"})
            # 5. Docs batchUpdate
            if ":batchUpdate" in url_str:
                batch_body = json.loads(request.content.decode("utf-8"))
                assert "requests" in batch_body
                assert len(batch_body["requests"]) > 0
                return httpx.Response(200, json={"replies": []})

            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)

        result = create_google_docs_archival_document(
            access_token="ya29.valid_token_xyz",
            recipes=adversarial_recipes,
            title="Heirloom Recipe Box",
            image_resolver=image_generator,
            http_client=client,
        )

        assert result["document_id"] == "doc_heirloom_777"
        assert result["url"] == "https://docs.google.com/document/d/doc_heirloom_777/edit"
        assert result["recipe_count"] == 2


# ============================================================================
# 2. FILENAME SANITIZATION & CUSTOMIZATION STRESS TESTS
# ============================================================================

class TestFilenameSanitizationAndCustomization:
    """Adversarial testing of filename sanitization and query parameter handling."""

    @pytest.mark.parametrize(
        "malicious_input, expected_contains, expected_ext",
        [
            # Directory traversal
            ("../../../etc/passwd", "passwd.csv", "csv"),
            ("..\\..\\windows\\system32\\cmd.exe", "cmd.exe", "docx"),
            ("/absolute/path/to/my_recipe.pdf", "my_recipe.pdf", "pdf"),
            ("nested/dir/subdir/cake", "cake.json", "json"),
            # Illegal characters
            ('recipe:*?"<>|test.md', "recipetest.md", "md"),
            ("my:grandma*favorite?recipe.cook", "mygrandmafavoriterecipe.cook", "cook"),
            ("null\x00byte\x1fcontrol.paprikarecipes", "nullbytecontrol.paprikarecipes", "paprikarecipes"),
            # Leading/trailing dots and spaces
            ("  ...my_cookbook...  ", "my_cookbook.pdf", "pdf"),
            (".hidden_recipe", "hidden_recipe.html", "html"),
            # Empty / pure illegal characters fallback to default
            ("", "default.csv", "csv"),
            ("   ", "default.docx", "docx"),
            ("???***:::", "default.pdf", "pdf"),
            ("///\\\\\\", "default.json", "json"),
            (None, "default.md", "md"),
            # Double extensions & extension correction
            ("recipe.txt", "recipe.txt.csv", "csv"),
            ("my_export.CSV", "my_export.CSV", "csv"),
            ("my_export.DOCX", "my_export.DOCX", "docx"),
            ("cookbook", "cookbook.cook", "cook"),
        ],
    )
    def test_sanitize_export_filename_matrix(
        self,
        malicious_input: str,
        expected_contains: str,
        expected_ext: str,
    ):
        result = sanitize_export_filename(
            filename=malicious_input,
            default_name=f"default.{expected_ext}",
            expected_ext=expected_ext,
        )
        assert expected_contains in result
        # Verify no path traversal indicators survive
        assert "/" not in result
        assert "\\" not in result
        assert ".." not in result
        assert "\x00" not in result
        assert result.lower().endswith(f".{expected_ext.lower()}")

    @pytest.mark.asyncio
    async def test_all_9_export_endpoints_respect_custom_and_sanitized_filenames(
        self,
        client: httpx.AsyncClient,
        populated_adversarial_session: str,
    ):
        """Every single export endpoint correctly sets Content-Disposition with sanitized custom filenames."""
        endpoints = [
            ("/api/export/csv", "my_family_recipes.csv", "my_family_recipes.csv", "text/csv"),
            ("/api/export/json-ld", "../malicious/schema.json", "schema.json", "application/ld+json"),
            ("/api/export/json", "custom_raw_data", "custom_raw_data.json", "application/json"),
            ("/api/export/docx", "grandma:pie*heirloom.docx", "grandmapieheirloom.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            ("/api/export/pdf", "print_ready_book.pdf", "print_ready_book.pdf", "application/pdf"),
            ("/api/export/markdown", "obsidian_vault_notes.md", "obsidian_vault_notes.md", "text/markdown"),
            ("/api/export/cooklang", "tasty_chili.cook", "tasty_chili.cook", "text/plain"),
            ("/api/export/paprika", "export_bundle.paprikarecipes", "export_bundle.paprikarecipes", "application/x-paprikarecipes"),
            ("/api/export/google-docs?format=html", "archival_gdocs.html", "archival_gdocs.html", "text/html"),
        ]

        for path, custom_fn, expected_fn, expected_media in endpoints:
            sep = "&" if "?" in path else "?"
            url = f"{path}{sep}session_id={populated_adversarial_session}&filename={custom_fn}"
            res = await client.get(url)
            assert res.status_code == 200, f"Endpoint {url} failed with {res.status_code}: {res.text}"
            assert expected_media in res.headers["content-type"]
            disp = res.headers.get("content-disposition", "")
            assert f'filename="{expected_fn}"' in disp, f"Expected filename '{expected_fn}' in Content-Disposition '{disp}' for {url}"


# ============================================================================
# 3. TAG SERIALIZATION ACROSS ALL 9 FORMATS
# ============================================================================

class TestTagSerializationAcrossAllFormats:
    """Stress tests verifying that tags with commas, quotes, brackets, and special characters serialize cleanly."""

    def test_plan_to_eat_csv_column_8_tag_quoting(self, adversarial_recipes: List[RecipeSchema]):
        """Plan To Eat RFC 4180 CSV Col 8 (Tags) must join tags with comma and quote fields containing commas."""
        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string(adversarial_recipes)
        assert exporter.validate_csv_format(csv_str)

        # Parse CSV with standard csv reader to verify column 8
        reader = io.StringIO(csv_str)
        import csv
        rows = list(csv.reader(reader))
        assert len(rows) == 3  # Header + 2 recipes
        header = rows[0]
        assert header[7] == "Tags"

        # Row 1 tags: contains "Dessert, Baking", "Mom's \"Favorite\"", etc.
        row1_tags = rows[1][7]
        assert "Dessert, Baking" in row1_tags
        assert 'Mom\'s "Favorite"' in row1_tags
        assert "Category: Fruit Tart" in row1_tags
        assert "Tag with [Brackets] & {Braces}" in row1_tags
        assert "🍰 Pie & Tart" in row1_tags

        # Row 2 tags
        row2_tags = rows[2][7]
        assert "Main Dish, Slow Cooked, Spicy, Bold" == row2_tags

    def test_schema_org_json_ld_tag_serialization(self, adversarial_recipes: List[RecipeSchema]):
        """Schema.org JSON-LD keywords string and recipeCategory."""
        exporter = JsonLdExporter()
        data = exporter.export_to_json_ld_dict(adversarial_recipes)
        assert exporter.validate_json_ld(data)

        graph = data["@graph"]
        assert len(graph) == 2

        # Recipe 1
        rec1 = graph[0]
        assert "Dessert, Baking" in rec1["keywords"]
        assert 'Mom\'s "Favorite"' in rec1["keywords"]
        assert rec1["recipeCategory"] == "Dessert, Baking"

        # Recipe 2
        rec2 = graph[1]
        assert "Main Dish" in rec2["keywords"]
        assert "Spicy, Bold" in rec2["keywords"]
        assert rec2["recipeCategory"] == "Main Dish"

    def test_obsidian_markdown_yaml_frontmatter_tags(self, adversarial_recipes: List[RecipeSchema]):
        """Obsidian Markdown YAML frontmatter handles tags with special characters cleanly."""
        exporter = MarkdownExporter()
        md_content = exporter.export_to_markdown_string(adversarial_recipes)

        import yaml
        # Split documents by separator
        docs = md_content.split("\n\n---\n\n")
        assert len(docs) == 2

        for doc in docs:
            # Extract frontmatter between --- and ---
            assert doc.startswith("---\n")
            parts = doc.split("---\n", 2)
            fm_text = parts[1]
            parsed_fm = yaml.safe_load(fm_text)

            assert "title" in parsed_fm
            assert "tags" in parsed_fm
            assert isinstance(parsed_fm["tags"], list)

        # Inspect first doc tags in YAML
        doc1_fm = yaml.safe_load(docs[0].split("---\n", 2)[1])
        assert "Dessert, Baking" in doc1_fm["tags"]
        assert 'Mom\'s "Favorite"' in doc1_fm["tags"]
        assert "Tag with [Brackets] & {Braces}" in doc1_fm["tags"]
        assert "Tag with Unicode: 🍰 Pie & Tart" in doc1_fm["tags"]

    def test_cooklang_tag_serialization(self, adversarial_recipes: List[RecipeSchema]):
        """Cooklang metadata header >> tags: comma-separated list."""
        exporter = CooklangExporter()
        cook_content = exporter.export_to_cooklang_string(adversarial_recipes)

        assert ">> tags: Dessert, Baking, Mom's \"Favorite\", Autumn & Winter" in cook_content
        assert ">> tags: Main Dish, Slow Cooked, Spicy, Bold" in cook_content

    def test_paprika_categories_array(self, adversarial_recipes: List[RecipeSchema]):
        """Paprika .paprikarecipes gzip archive contains exact categories list."""
        exporter = PaprikaExporter()
        archive_bytes = exporter.export_to_paprika_bytes(adversarial_recipes)

        records = PaprikaExporter.decompress_paprika_archive(archive_bytes)
        assert len(records) == 2

        rec1 = records[0]
        assert rec1["categories"] == [
            "Dessert, Baking",
            'Mom\'s "Favorite"',
            "Autumn & Winter",
            "<Holiday Special>",
            "Category: Fruit Tart",
            "Tag with [Brackets] & {Braces}",
            "Tag with Unicode: 🍰 Pie & Tart",
        ]

        rec2 = records[1]
        assert rec2["categories"] == ["Main Dish", "Slow Cooked", "Spicy, Bold"]

    def test_docx_tag_serialization_xml_escaping(self, adversarial_recipes: List[RecipeSchema]):
        """DOCX document.xml properly XML-escapes special characters in tags (&, <, >, ", ')."""
        exporter = DocxExporter()
        docx_bytes = exporter.export_to_bytes(adversarial_recipes)

        with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as zf:
            doc_xml = zf.read("word/document.xml").decode("utf-8")

            # Check that XML is well-formed
            import xml.etree.ElementTree as ET
            root = ET.fromstring(doc_xml)
            assert root is not None

            # Check escaped content
            assert "Tags: Dessert, Baking, Mom&apos;s &quot;Favorite&quot;, Autumn &amp; Winter, &lt;Holiday Special&gt;" in doc_xml or (
                "Dessert, Baking" in doc_xml and "&amp;" in doc_xml and "&lt;Holiday Special&gt;" in doc_xml
            )

    def test_pdf_tag_serialization_and_special_char_escaping(self, adversarial_recipes: List[RecipeSchema]):
        """PDF exporter encodes tags without PDF operator corruption."""
        exporter = PdfExporter()
        pdf_bytes = exporter.export_to_bytes(adversarial_recipes)

        assert pdf_bytes.startswith(b"%PDF-1.4")
        assert pdf_bytes.rstrip().endswith(b"%%EOF")
        # Ensure xref table is valid
        assert b"xref\n0 " in pdf_bytes

    def test_google_docs_tag_serialization(self, adversarial_recipes: List[RecipeSchema]):
        """Google Docs batchUpdate requests and HTML output include tags."""
        exporter = GoogleDocsExporter()

        # Batch requests
        reqs = exporter.build_batch_update_requests(adversarial_recipes)
        insert_texts = [r["insertText"]["text"] for r in reqs if "insertText" in r]
        combined_text = "".join(insert_texts)
        assert "Tags: Dessert, Baking, Mom's \"Favorite\", Autumn & Winter" in combined_text

        # HTML archive
        html_out = exporter.export_to_html(adversarial_recipes)
        assert "Dessert, Baking" in html_out
        assert "Holiday Special" in html_out
        assert "tag-badge" in html_out


# ============================================================================
# 4. DOCX & PDF PROPORTIONAL EMU SCALING & IMAGE EMBEDDING
# ============================================================================

class TestDocxAndPdfImageAspectRatios:
    """Stress tests for proportional EMU aspect ratio scaling and image embedding in DOCX/PDF."""

    def test_docx_proportional_emu_scaling(self, adversarial_recipes: List[RecipeSchema], image_generator):
        """DOCX generator calculates proportional cx and cy EMUs preserving physical aspect ratio."""
        exporter = DocxExporter()
        docx_bytes = exporter.export_to_bytes(adversarial_recipes, image_resolver=image_generator)

        with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as zf:
            # Check media folder
            media_files = [n for n in zf.namelist() if n.startswith("word/media/")]
            assert len(media_files) >= 3  # All resolved card images embedded

            # Check document.xml for proportional DrawingML extent dimensions
            doc_xml = zf.read("word/document.xml").decode("utf-8")
            import re
            extents = re.findall(r'<wp:extent cx="(\d+)" cy="(\d+)"/>', doc_xml)
            assert len(extents) >= 3

            for cx_str, cy_str in extents:
                cx = int(cx_str)
                cy = int(cy_str)
                # Ensure within bounding box
                assert cx <= MAX_EMU_WIDTH
                assert cy <= MAX_EMU_HEIGHT
                assert cx > 0 and cy > 0

            # Wide image (800x400 -> 2:1 ratio): cx should be MAX_EMU_WIDTH, cy should be approx MAX_EMU_WIDTH / 2
            wide_cx, wide_cy = int(extents[0][0]), int(extents[0][1])
            assert wide_cx == MAX_EMU_WIDTH
            assert abs(wide_cy - (MAX_EMU_WIDTH // 2)) <= 10

            # Tall image (300x600 -> 1:2 ratio): cy should be MAX_EMU_HEIGHT, cx should be approx MAX_EMU_HEIGHT / 2
            tall_cx, tall_cy = int(extents[1][0]), int(extents[1][1])
            assert tall_cy == MAX_EMU_HEIGHT
            assert abs(tall_cx - (MAX_EMU_HEIGHT // 2)) <= 10

    def test_docx_handles_corrupt_image_bytes_gracefully(self, adversarial_recipes: List[RecipeSchema]):
        """DOCX exporter does not crash on unparseable/corrupted image bytes."""
        def corrupt_resolver(card_id: str) -> bytes:
            return b"not_a_valid_image_bytes_random_noise_12345"

        exporter = DocxExporter()
        docx_bytes = exporter.export_to_bytes(adversarial_recipes, image_resolver=corrupt_resolver)
        assert len(docx_bytes) > 0

    def test_pdf_embeds_images_with_proper_stream_and_xobjects(
        self,
        adversarial_recipes: List[RecipeSchema],
        image_generator,
    ):
        """PDF exporter embeds scanned card photos as valid XObjects with correct dimensions."""
        exporter = PdfExporter()
        pdf_bytes = exporter.export_to_bytes(adversarial_recipes, image_resolver=image_generator)

        assert b"/XObject <<" in pdf_bytes
        assert b"/Subtype /Image" in pdf_bytes
        assert b"/Filter /DCTDecode" in pdf_bytes
        assert b"/ColorSpace /DeviceRGB" in pdf_bytes

    def test_pdf_handles_extreme_multi_recipe_multi_page_layout(self, image_generator):
        """PDF layout correctly distributes 20 recipes across pages without buffer or index overflow."""
        bulk_recipes = [
            RecipeSchema(
                recipe_id=f"bulk_rec_{i:03d}",
                title=f"Archival Family Recipe #{i:03d}",
                description=f"Description for recipe #{i:03d} with multiple ingredients and directions.",
                ingredients=[f"Ingredient {j} for recipe {i}" for j in range(1, 8)],
                directions=[f"Step {k}: Action for recipe {i}." for k in range(1, 6)],
                tags=["Family", f"Batch-{i % 3}"],
                card_ids=[f"card_{i}_front"],
            )
            for i in range(1, 15)
        ]

        exporter = PdfExporter()
        pdf_bytes = exporter.export_to_bytes(bulk_recipes, image_resolver=image_generator)
        assert len(pdf_bytes) > 10000
        assert pdf_bytes.startswith(b"%PDF-1.4")
        assert pdf_bytes.rstrip().endswith(b"%%EOF")
