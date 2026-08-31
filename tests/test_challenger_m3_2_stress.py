"""Empirical Challenger 2 Stress Test Suite for Milestone 3 (RecipeBoxer Phase 9).

Validates:
1. All 9 /api/export/* endpoints (docx, pdf, google-docs, markdown, csv, json-ld, cooklang, paprika, json)
   via both GET query parameters and POST JSON request bodies with curation parameters:
   recipe_order, grouping_preset, include_section_dividers, include_toc, toc_title.
2. Google Docs API batchUpdate section breaks & HTML export anchors (<div class="table-of-contents">,
   <a href="#recipe-{id}">, <div class="section-divider">).
3. Markdown export # Table of Contents, wikilinks/GFM anchors, and section divider headers.
4. Curated recipe order preservation across CSV, JSON-LD, Cooklang, Paprika, and JSON formats.
5. Adversarial edge cases: special characters/XML escaping, invalid/partial recipe_order,
   Unicode/HTML injection, empty recipe lists, and alias endpoints.
"""

import gzip
import html
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import Any, AsyncGenerator, Dict, List

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from recipeboxer.agent.session import SessionManager
from recipeboxer.api.app import create_app
from recipeboxer.exporter.cooklang import export_recipes_to_cooklang
from recipeboxer.exporter.curation import (
    GroupingPreset,
    curate_recipes,
)
from recipeboxer.exporter.docx_export import DocxExporter, export_recipes_to_docx
from recipeboxer.exporter.google_docs import (
    GoogleDocsExporter,
    export_recipes_to_google_docs_batch_requests,
    export_recipes_to_google_docs_html,
    export_recipes_to_google_docs_json,
)
from recipeboxer.exporter.json_export import export_recipes_to_json
from recipeboxer.exporter.json_ld import export_recipes_to_json_ld
from recipeboxer.exporter.markdown_export import (
    MarkdownExporter,
    export_recipes_to_markdown,
)
from recipeboxer.exporter.paprika import PaprikaExporter, export_recipes_to_paprika
from recipeboxer.exporter.pdf_export import PdfExporter, export_recipes_to_pdf
from recipeboxer.exporter.plan_to_eat import export_recipes_to_plan_to_eat_csv
from recipeboxer.models.pipeline import PipelineState
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.storage.session_store import SessionStore
from tests.conftest import MockGeminiClient, SQLiteFingerprintCache


def create_sample_recipe_collection() -> List[RecipeSchema]:
    """Builds a curated 4-recipe multi-category collection for stress-testing."""
    r1 = RecipeSchema(
        recipe_id="rec_app_01",
        title="Artichoke Dip & Bruschetta <Special>",
        description="Creamy warm dip served with toasted garlic baguettes.",
        yield_servings="8 servings",
        prep_time="10 mins",
        cook_time="20 mins",
        total_time="30 mins",
        ingredients=[
            "1 can artichoke hearts, drained & chopped",
            "1/2 cup mayonnaise",
            "1/2 cup grated parmesan cheese",
            "1 clove garlic, minced",
        ],
        directions=[
            "1. Mix artichoke hearts, mayonnaise, parmesan, and garlic in a bowl.",
            "2. Transfer to baking dish and bake at 375°F for 20 minutes until bubbly.",
        ],
        tags=["Appetizer", "Party", "Dip"],
        source_notes="Aunt Marie's Holiday Party (1984)",
        card_ids=["card_artichoke_01"],
    )

    r2 = RecipeSchema(
        recipe_id="rec_main_02",
        title="Braised Beef Short Ribs",
        description="Slow-cooked beef ribs in rich red wine reduction.",
        yield_servings="4 servings",
        prep_time="25 mins",
        cook_time="3 hrs",
        total_time="3 hrs 25 mins",
        ingredients=[
            "4 lbs beef short ribs",
            "2 cups dry red wine",
            "2 cups beef broth",
            "2 carrots, chopped",
            "2 sprigs fresh rosemary",
        ],
        directions=[
            "1. Brown the ribs in a dutch oven on all sides.",
            "2. Add wine, broth, carrots, and rosemary.",
            "3. Cover and simmer gently for 3 hours until fork-tender.",
        ],
        tags=["Main Dish", "Dinner", "Beef"],
        source_notes="Grandma Clara's Sunday Suppers",
        card_ids=["card_ribs_01", "card_ribs_02"],
    )

    r3 = RecipeSchema(
        recipe_id="rec_dessert_03",
        title="Spiced Apple Crisp",
        description="Warm spiced honeycrisp apples topped with golden oat streusel.",
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
        source_notes="From the kitchen of Mom",
        card_ids=["card_apple_crisp_01"],
    )

    r4 = RecipeSchema(
        recipe_id="rec_beverage_04",
        title="Spiced Mulled Cider",
        description="Simmered fresh apple cider with citrus and aromatic whole spices.",
        yield_servings="10 cups",
        prep_time="5 mins",
        cook_time="30 mins",
        total_time="35 mins",
        ingredients=[
            "1 gallon fresh apple cider",
            "2 oranges, sliced",
            "4 cinnamon sticks",
            "1 tsp whole cloves",
            "2 star anise",
        ],
        directions=[
            "1. Combine cider, orange slices, cinnamon sticks, cloves, and star anise in a stock pot.",
            "2. Bring to a gentle simmer for 30 minutes. Serve warm in mugs.",
        ],
        tags=["Beverage", "Autumn", "Drink"],
        source_notes="Family Collection / Unattributed",
        card_ids=["card_cider_01"],
    )

    return [r1, r2, r3, r4]


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
def stress_session(test_session_manager: SessionManager, app) -> str:
    """Populates an active completed session with the 4-recipe multi-category collection."""
    sid = "sess_m3_stress_verification"
    session = test_session_manager.create_session(session_id=sid)
    session.state = PipelineState.COMPLETED
    recipes = create_sample_recipe_collection()
    session.recipes = recipes

    store: SessionStore = getattr(app.state, "session_store", None)
    if store:
        store.save_session(session_id=sid, state=PipelineState.COMPLETED, recipe_count=len(recipes))
        store.save_recipes(session_id=sid, recipes=recipes)

    return sid


class TestMilestone3EmpiricalChallenge:
    """Rigorous empirical validation tests for Milestone 3."""

    # =========================================================================
    # 1. All 9 Export Endpoints: GET query params and POST bodies
    # =========================================================================

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "endpoint_path,expected_mime,expected_ext",
        [
            ("/api/export/docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
            ("/api/export/pdf", "application/pdf", "pdf"),
            ("/api/export/markdown", "text/markdown", "md"),
            ("/api/export/md", "text/markdown", "md"),
            ("/api/export/csv", "text/csv", "csv"),
            ("/api/export/json-ld", "application/ld+json", "json"),
            ("/api/export/jsonld", "application/ld+json", "json"),
            ("/api/export/cooklang", "text/plain", "cook"),
            ("/api/export/cook", "text/plain", "cook"),
            ("/api/export/paprika", "application/x-paprikarecipes", "paprikarecipes"),
            ("/api/export/json", "application/json", "json"),
        ],
    )
    async def test_all_endpoints_get_with_curation_query_params(
        self,
        client: httpx.AsyncClient,
        stress_session: str,
        endpoint_path: str,
        expected_mime: str,
        expected_ext: str,
    ):
        """Verify GET request with full set of curation query params across all endpoints."""
        url = (
            f"{endpoint_path}?"
            f"session_id={stress_session}&"
            f"recipe_order=rec_dessert_03,rec_app_01,rec_beverage_04,rec_main_02&"
            f"grouping_preset=course&"
            f"include_section_dividers=true&"
            f"include_toc=true&"
            f"toc_title=Grandma%27s%20Recipe%20Index"
        )
        res = await client.get(url)
        assert res.status_code == 200, f"Failed GET on {endpoint_path}: {res.text}"
        assert expected_mime in res.headers["content-type"], f"MIME mismatch on {endpoint_path}"
        assert "content-disposition" in res.headers
        assert f".{expected_ext}" in res.headers["content-disposition"]
        assert len(res.content) > 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "endpoint_path,expected_mime,expected_ext",
        [
            ("/api/export/docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
            ("/api/export/pdf", "application/pdf", "pdf"),
            ("/api/export/markdown", "text/markdown", "md"),
            ("/api/export/md", "text/markdown", "md"),
            ("/api/export/csv", "text/csv", "csv"),
            ("/api/export/json-ld", "application/ld+json", "json"),
            ("/api/export/jsonld", "application/ld+json", "json"),
            ("/api/export/cooklang", "text/plain", "cook"),
            ("/api/export/cook", "text/plain", "cook"),
            ("/api/export/paprika", "application/x-paprikarecipes", "paprikarecipes"),
            ("/api/export/json", "application/json", "json"),
        ],
    )
    async def test_all_endpoints_post_with_curation_body(
        self,
        client: httpx.AsyncClient,
        stress_session: str,
        endpoint_path: str,
        expected_mime: str,
        expected_ext: str,
    ):
        """Verify POST request with JSON body containing curation configuration across all endpoints."""
        payload = {
            "session_id": stress_session,
            "recipe_order": ["rec_dessert_03", "rec_app_01", "rec_beverage_04", "rec_main_02"],
            "grouping_preset": "contributor",
            "include_section_dividers": True,
            "include_toc": True,
            "toc_title": "Heirloom Contributor Index",
            "filename": f"custom_heirloom.{expected_ext}",
        }
        res = await client.post(endpoint_path, json=payload)
        assert res.status_code == 200, f"Failed POST on {endpoint_path}: {res.text}"
        assert expected_mime in res.headers["content-type"], f"MIME mismatch on {endpoint_path}"
        assert f'filename="custom_heirloom.{expected_ext}"' in res.headers["content-disposition"]
        assert len(res.content) > 0

    @pytest.mark.asyncio
    async def test_google_docs_endpoint_all_formats_get_and_post(
        self,
        client: httpx.AsyncClient,
        stress_session: str,
    ):
        """Test /api/export/google-docs with batch_requests, json, and html formats via GET & POST."""
        curation_params = {
            "recipe_order": ["rec_dessert_03", "rec_main_02", "rec_app_01", "rec_beverage_04"],
            "grouping_preset": "course",
            "include_section_dividers": True,
            "include_toc": True,
            "toc_title": "Google Docs Archival Index",
        }

        # 1. POST default (batch_requests JSON object)
        res_post_batch = await client.post(
            "/api/export/google-docs",
            json={"session_id": stress_session, "format": "batch_requests", **curation_params},
        )
        assert res_post_batch.status_code == 200
        data_batch = res_post_batch.json()
        assert "requests" in data_batch
        assert data_batch["recipe_count"] == 4

        # 2. POST html format
        res_post_html = await client.post(
            "/api/export/google-docs",
            json={"session_id": stress_session, "format": "html", **curation_params},
        )
        assert res_post_html.status_code == 200
        assert "text/html" in res_post_html.headers["content-type"]
        assert '<div class="table-of-contents recipe-page-break">' in res_post_html.text
        assert "Google Docs Archival Index" in res_post_html.text

        # 3. GET html format
        res_get_html = await client.get(
            f"/api/export/google-docs?session_id={stress_session}&format=html&grouping_preset=course&include_toc=true"
        )
        assert res_get_html.status_code == 200
        assert "text/html" in res_get_html.headers["content-type"]
        assert '<div class="table-of-contents' in res_get_html.text

    @pytest.mark.asyncio
    async def test_markdown_endpoint_use_wikilinks_false(
        self,
        client: httpx.AsyncClient,
        stress_session: str,
    ):
        """Verify GET and POST /api/export/markdown with use_wikilinks=false generates GFM slugs and does not crash."""
        # 1. GET with use_wikilinks=false and include_toc=true
        res_get = await client.get(
            f"/api/export/markdown?session_id={stress_session}&use_wikilinks=false&include_toc=true&grouping_preset=course"
        )
        assert res_get.status_code == 200
        assert "text/markdown" in res_get.headers["content-type"]
        assert "[Spiced Apple Crisp](#spiced-apple-crisp)" in res_get.text

        # 2. POST with use_wikilinks=false and include_toc=true
        res_post = await client.post(
            "/api/export/markdown",
            json={
                "session_id": stress_session,
                "use_wikilinks": False,
                "include_toc": True,
                "grouping_preset": "course",
            },
        )
        assert res_post.status_code == 200
        assert "text/markdown" in res_post.headers["content-type"]
        assert "[Spiced Apple Crisp](#spiced-apple-crisp)" in res_post.text

    # =========================================================================
    # 2. Google Docs batchUpdate Section Breaks & HTML Anchors / Dividers
    # =========================================================================

    def test_google_docs_batch_update_section_breaks_and_toc(self):
        """Verify Google Docs batchUpdate contains TOC, section headers, section divider pages, and page breaks."""
        recipes = create_sample_recipe_collection()
        curation = curate_recipes(
            recipes=recipes,
            grouping_preset=GroupingPreset.COURSE,
            include_section_dividers=True,
            include_toc=True,
            toc_title="Custom Culinary Index",
        )

        exporter = GoogleDocsExporter()
        requests = exporter.build_batch_update_requests(recipes, curation=curation, include_page_breaks=True)

        # 1. TOC verification
        inserted_texts = [r["insertText"]["text"] for r in requests if "insertText" in r]
        assert any("Custom Culinary Index\n" in t for t in inserted_texts), "TOC heading missing from batchUpdate"
        assert any("✦  ✦  ✦\n\n" in t for t in inserted_texts), "TOC ornament missing"
        assert any("Appetizers & Starters\n" in t for t in inserted_texts), "TOC section header missing"
        assert any("•  Spiced Apple Crisp\n" in t for t in inserted_texts), "TOC recipe bullet missing"

        # 2. Section divider verification
        assert any("Desserts & Sweets\n" in t for t in inserted_texts), "Section divider title missing"
        assert any("Main Dishes\n" in t for t in inserted_texts), "Section divider title missing"

        # 3. Page break verification
        page_breaks = [r for r in requests if "insertPageBreak" in r]
        # Should have at least 1 page break after TOC + section dividers + between recipes
        assert len(page_breaks) >= 4, f"Expected multiple page breaks, found {len(page_breaks)}"

    def test_google_docs_html_export_anchors_and_dividers(self):
        """Verify HTML export has <div class="table-of-contents">, <a href="#recipe-{id}">, and .section-divider."""
        recipes = create_sample_recipe_collection()
        curation = curate_recipes(
            recipes=recipes,
            grouping_preset=GroupingPreset.COURSE,
            include_section_dividers=True,
            include_toc=True,
            toc_title="Grandma's Table of Contents",
        )

        exporter = GoogleDocsExporter()
        html_out = exporter.export_to_html(recipes, curation=curation)

        # 1. TOC presence and styling
        assert '<div class="table-of-contents recipe-page-break">' in html_out
        assert '<h1 class="toc-title">Grandma&#x27;s Table of Contents</h1>' in html_out or '<h1 class="toc-title">Grandma\'s Table of Contents</h1>' in html_out

        # 2. Hyperlinked anchors for all 4 recipes
        for r in recipes:
            expected_href = f'href="#recipe-{html.escape(r.recipe_id)}"'
            expected_target_id = f'id="recipe-{html.escape(r.recipe_id)}"'
            assert expected_href in html_out, f"Missing TOC hyperlink {expected_href}"
            assert expected_target_id in html_out, f"Missing target anchor {expected_target_id}"

        # 3. Section dividers
        assert '<div class="section-divider recipe-page-break">' in html_out
        assert '<h1 class="section-divider-title">Appetizers &amp; Starters</h1>' in html_out or "Appetizers & Starters" in html_out
        assert '<h1 class="section-divider-title">Desserts &amp; Sweets</h1>' in html_out or "Desserts & Sweets" in html_out
        assert '<h1 class="section-divider-title">Main Dishes</h1>' in html_out or "Main Dishes" in html_out
        assert '<h1 class="section-divider-title">Beverages &amp; Drinks</h1>' in html_out or "Beverages & Drinks" in html_out

    def test_google_docs_html_disabled_toc_and_dividers(self):
        """Verify HTML export omits TOC and section dividers when disabled."""
        recipes = create_sample_recipe_collection()
        curation = curate_recipes(
            recipes=recipes,
            grouping_preset=GroupingPreset.NONE,
            include_section_dividers=False,
            include_toc=False,
        )

        exporter = GoogleDocsExporter()
        html_out = exporter.export_to_html(recipes, curation=curation)

        assert '<div class="table-of-contents' not in html_out
        assert '<div class="section-divider' not in html_out
        # Recipes themselves are still present
        for r in recipes:
            assert r.title in html_out or html.escape(r.title) in html_out

    # =========================================================================
    # 3. Markdown Export TOC, Anchors, and Section Dividers
    # =========================================================================

    def test_markdown_export_toc_wikilinks_and_dividers(self):
        """Verify Markdown export contains # Table of Contents, wikilinks, and section headers."""
        recipes = create_sample_recipe_collection()
        curation = curate_recipes(
            recipes=recipes,
            grouping_preset=GroupingPreset.COURSE,
            include_section_dividers=True,
            include_toc=True,
            toc_title="Cookbook Index",
        )

        exporter = MarkdownExporter()
        # Test with use_wikilinks=True
        md_wikilinks = exporter.export_to_markdown_string(recipes, use_wikilinks=True, curation=curation)

        assert "# Cookbook Index" in md_wikilinks
        assert "## Appetizers & Starters" in md_wikilinks
        assert "## Main Dishes" in md_wikilinks
        assert "## Desserts & Sweets" in md_wikilinks
        assert "## Beverages & Drinks" in md_wikilinks

        # Wikilinks anchors
        assert "- [[#Artichoke Dip & Bruschetta <Special>]]" in md_wikilinks
        assert "- [[#Braised Beef Short Ribs]]" in md_wikilinks
        assert "- [[#Spiced Apple Crisp]]" in md_wikilinks
        assert "- [[#Spiced Mulled Cider]]" in md_wikilinks

        # Section dividers
        assert "# Appetizers & Starters\n\n*1 Recipe*" in md_wikilinks
        assert "# Main Dishes\n\n*1 Recipe*" in md_wikilinks

    def test_markdown_export_gfm_slug_anchors(self):
        """Verify Markdown export contains standard GFM slug links when use_wikilinks=False."""
        recipes = create_sample_recipe_collection()
        curation = curate_recipes(
            recipes=recipes,
            grouping_preset=GroupingPreset.COURSE,
            include_section_dividers=True,
            include_toc=True,
            toc_title="Standard TOC",
        )

        exporter = MarkdownExporter()
        md_gfm = exporter.export_to_markdown_string(recipes, use_wikilinks=False, curation=curation)

        assert "# Standard TOC" in md_gfm
        # GFM slug links: [Title](#slug)
        assert "[Spiced Apple Crisp](#spiced-apple-crisp)" in md_gfm
        assert "[Braised Beef Short Ribs](#braised-beef-short-ribs)" in md_gfm

    def test_markdown_disabled_toc_and_dividers(self):
        """Verify Markdown export omits TOC and divider headers when disabled."""
        recipes = create_sample_recipe_collection()
        curation = curate_recipes(
            recipes=recipes,
            grouping_preset=GroupingPreset.NONE,
            include_section_dividers=False,
            include_toc=False,
        )

        exporter = MarkdownExporter()
        md_plain = exporter.export_to_markdown_string(recipes, curation=curation)

        assert "# Table of Contents" not in md_plain
        assert "# All Recipes" not in md_plain
        assert "# Spiced Apple Crisp" in md_plain

    # =========================================================================
    # 4. Preservation of Curated Ordering Across Data and Tabular Formats
    # =========================================================================

    def test_csv_exact_curated_recipe_ordering(self):
        """Verify Plan To Eat CSV preserves exact curated custom recipe order."""
        recipes = create_sample_recipe_collection()
        # Custom order: rec_beverage_04, rec_dessert_03, rec_main_02, rec_app_01
        custom_order = ["rec_beverage_04", "rec_dessert_03", "rec_main_02", "rec_app_01"]
        curation = curate_recipes(recipes=recipes, recipe_order=custom_order, grouping_preset=GroupingPreset.NONE)

        csv_str = export_recipes_to_plan_to_eat_csv(curation.all_recipes)
        lines = [line.strip() for line in csv_str.splitlines() if line.strip()]

        # Header is line 0
        assert lines[0].startswith("Title,Description,Ingredients")

        # Find row positions for each title
        pos_cider = next(i for i, line in enumerate(lines) if "Spiced Mulled Cider" in line)
        pos_crisp = next(i for i, line in enumerate(lines) if "Spiced Apple Crisp" in line)
        pos_ribs = next(i for i, line in enumerate(lines) if "Braised Beef Short Ribs" in line)
        pos_dip = next(i for i, line in enumerate(lines) if "Artichoke Dip & Bruschetta" in line)

        assert pos_cider < pos_crisp < pos_ribs < pos_dip, "CSV did not preserve custom recipe order!"

    def test_json_ld_exact_curated_recipe_ordering(self):
        """Verify Schema.org JSON-LD @graph array preserves exact curated order."""
        recipes = create_sample_recipe_collection()
        custom_order = ["rec_dessert_03", "rec_beverage_04", "rec_app_01", "rec_main_02"]
        curation = curate_recipes(recipes=recipes, recipe_order=custom_order, grouping_preset=GroupingPreset.NONE)

        json_ld_str = export_recipes_to_json_ld(curation.all_recipes)
        data = json.loads(json_ld_str)

        assert "@graph" in data
        graph = data["@graph"]
        assert len(graph) == 4
        assert graph[0]["name"] == "Spiced Apple Crisp"
        assert graph[1]["name"] == "Spiced Mulled Cider"
        assert "Artichoke Dip" in graph[2]["name"]
        assert graph[3]["name"] == "Braised Beef Short Ribs"

    def test_cooklang_exact_curated_recipe_ordering(self):
        """Verify Cooklang multi-recipe serialization preserves exact curated order."""
        recipes = create_sample_recipe_collection()
        custom_order = ["rec_main_02", "rec_dessert_03", "rec_app_01", "rec_beverage_04"]
        curation = curate_recipes(recipes=recipes, recipe_order=custom_order, grouping_preset=GroupingPreset.NONE)

        cook_str = export_recipes_to_cooklang(curation.all_recipes)
        sections = cook_str.split("\n\n---\n\n")
        assert len(sections) == 4

        assert ">> title: Braised Beef Short Ribs" in sections[0]
        assert ">> title: Spiced Apple Crisp" in sections[1]
        assert ">> title: Artichoke Dip" in sections[2]
        assert ">> title: Spiced Mulled Cider" in sections[3]

    def test_paprika_exact_curated_recipe_ordering(self):
        """Verify Paprika archive preserves exact curated order in gzipped list."""
        recipes = create_sample_recipe_collection()
        custom_order = ["rec_beverage_04", "rec_app_01", "rec_main_02", "rec_dessert_03"]
        curation = curate_recipes(recipes=recipes, recipe_order=custom_order, grouping_preset=GroupingPreset.NONE)

        paprika_bytes = export_recipes_to_paprika(curation.all_recipes, as_zip_archive=False)
        decomp = gzip.decompress(paprika_bytes)
        parsed = json.loads(decomp.decode("utf-8"))

        assert isinstance(parsed, list)
        assert len(parsed) == 4
        assert parsed[0]["name"] == "Spiced Mulled Cider"
        assert "Artichoke Dip" in parsed[1]["name"]
        assert parsed[2]["name"] == "Braised Beef Short Ribs"
        assert parsed[3]["name"] == "Spiced Apple Crisp"

    def test_raw_json_exact_curated_recipe_ordering(self):
        """Verify Raw JSON export preserves exact curated order."""
        recipes = create_sample_recipe_collection()
        custom_order = ["rec_app_01", "rec_dessert_03", "rec_beverage_04", "rec_main_02"]
        curation = curate_recipes(recipes=recipes, recipe_order=custom_order, grouping_preset=GroupingPreset.NONE)

        raw_json_str = export_recipes_to_json(curation.all_recipes)
        parsed = json.loads(raw_json_str)

        assert isinstance(parsed, list)
        assert [r["recipe_id"] for r in parsed] == custom_order

    # =========================================================================
    # 5. DOCX & PDF Exporter Invariant & XML Integrity
    # =========================================================================

    def test_docx_xml_escaping_and_dot_leaders(self):
        """Verify DOCX OpenXML contains dot leader tab stops and escapes special XML characters."""
        recipes = create_sample_recipe_collection()  # Has <Special> in title
        curation = curate_recipes(
            recipes=recipes,
            grouping_preset=GroupingPreset.COURSE,
            include_section_dividers=True,
            include_toc=True,
            toc_title="Heirloom & Heritage Index <TOC>",
        )

        exporter = DocxExporter()
        docx_bytes = exporter.export_to_bytes(recipes, curation=curation)
        assert len(docx_bytes) > 0

        # Unpack docx zip and inspect word/document.xml
        zip_buf = io.BytesIO(docx_bytes)
        with zipfile.ZipFile(zip_buf, "r") as zf:
            doc_xml = zf.read("word/document.xml").decode("utf-8")

        # 1. XML validity check
        ET.fromstring(doc_xml)

        # 2. Check dot leader tabs in TOC
        assert 'w:leader="dot"' in doc_xml

        # 3. Check XML escaping for special characters (<, >, &)
        assert "Artichoke Dip &amp; Bruschetta &lt;Special&gt;" in doc_xml
        assert "Heirloom &amp; Heritage Index &lt;TOC&gt;" in doc_xml

        # 4. Check section dividers
        assert "Appetizers &amp; Starters" in doc_xml
        assert "✦ ✦ ✦" in doc_xml

    def test_pdf_rendering_with_two_pass_page_numbers(self):
        """Verify PDF export generates valid PDF binary with TOC dot leaders and divider pages."""
        recipes = create_sample_recipe_collection()
        curation = curate_recipes(
            recipes=recipes,
            grouping_preset=GroupingPreset.COURSE,
            include_section_dividers=True,
            include_toc=True,
            toc_title="Table of Contents",
        )

        exporter = PdfExporter()
        pdf_bytes = exporter.export_to_bytes(recipes, curation=curation)
        assert pdf_bytes.startswith(b"%PDF-1.4")
        assert len(pdf_bytes) > 1000

    @pytest.mark.asyncio
    async def test_invalid_grouping_preset_handling(
        self,
        client: httpx.AsyncClient,
        stress_session: str,
    ):
        """Test API behavior when an invalid grouping preset string is provided."""
        # Unhandled ValueError inside FastAPI handler
        with pytest.raises(Exception):
            await client.get(
                f"/api/export/docx?session_id={stress_session}&grouping_preset=invalid_preset_xyz"
            )

    def test_grouping_preset_case_sensitivity(self):
        """Verify behavior when grouping_preset string has uppercase or mixed casing (e.g. 'Course', 'CONTRIBUTOR')."""
        recipes = create_sample_recipe_collection()
        # Test whether 'Course' or 'COURSE' works or raises ValueError
        try:
            c_upper = curate_recipes(recipes=recipes, grouping_preset="Course")
            assert c_upper.grouping_preset == GroupingPreset.COURSE
        except ValueError as e:
            # Documented bug / limitation: GroupingPreset enum lookup is strictly case-sensitive without normalization
            pytest.fail(f"curate_recipes failed on mixed-case grouping_preset 'Course': {e}")

    # =========================================================================
    # 6. Adversarial Robustness & Edge Cases
    # =========================================================================

    def test_curate_recipes_adversarial_recipe_order_inputs(self):
        """Test partial, duplicate, non-existent, and empty recipe_order sequences."""
        recipes = create_sample_recipe_collection()

        # 1. Partial order: specify 2 IDs out of 4 -> specified IDs first, remaining appended
        c1 = curate_recipes(recipes=recipes, recipe_order=["rec_dessert_03", "rec_beverage_04"])
        all_ids_1 = [r.recipe_id for r in c1.all_recipes]
        assert all_ids_1 == ["rec_dessert_03", "rec_beverage_04", "rec_app_01", "rec_main_02"]

        # 2. Duplicated IDs in recipe_order -> deduplicated without corruption
        c2 = curate_recipes(recipes=recipes, recipe_order="rec_dessert_03, rec_dessert_03, rec_main_02")
        all_ids_2 = [r.recipe_id for r in c2.all_recipes]
        assert all_ids_2 == ["rec_dessert_03", "rec_main_02", "rec_app_01", "rec_beverage_04"]

        # 3. Non-existent IDs in recipe_order -> ignored cleanly
        c3 = curate_recipes(recipes=recipes, recipe_order="rec_ghost_999, rec_app_01, rec_alien_000")
        all_ids_3 = [r.recipe_id for r in c3.all_recipes]
        assert all_ids_3 == ["rec_app_01", "rec_main_02", "rec_dessert_03", "rec_beverage_04"]

        # 4. Empty recipe list -> returns empty CuratedCookbook
        c_empty = curate_recipes(recipes=[])
        assert c_empty.total_recipe_count == 0
        assert len(c_empty.sections) == 0

    def test_grouping_presets_taxonomy_and_normalization(self):
        """Stress-test all 5 grouping preset algorithms."""
        recipes = create_sample_recipe_collection()

        # 1. Contributor grouping
        c_contrib = curate_recipes(recipes=recipes, grouping_preset=GroupingPreset.CONTRIBUTOR)
        section_titles = [s.title for s in c_contrib.sections]
        # "Aunt Marie", "Mom", "Grandma Clara", and "Family Collection / Unattributed" at the end
        assert "Family Collection / Unattributed" == section_titles[-1]
        assert any("Aunt Marie" in t for t in section_titles)
        assert any("Mom" in t for t in section_titles)
        assert any("Grandma Clara" in t for t in section_titles)

        # 2. Alphabetical grouping
        c_alpha = curate_recipes(recipes=recipes, grouping_preset=GroupingPreset.ALPHABETICAL)
        alpha_titles = [s.title for s in c_alpha.sections]
        # 'A' (Artichoke Dip), 'B' (Braised Beef), 'S' (Spiced Apple Crisp, Spiced Mulled Cider)
        assert "A" in alpha_titles
        assert "B" in alpha_titles
        assert "S" in alpha_titles

        # 3. Tags grouping
        c_tags = curate_recipes(recipes=recipes, grouping_preset=GroupingPreset.TAGS)
        tag_titles = [s.title for s in c_tags.sections]
        assert "Appetizer" in tag_titles
        assert "Main Dish" in tag_titles
        assert "Dessert" in tag_titles
        assert "Beverage" in tag_titles
