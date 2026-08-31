"""Comprehensive tests for Milestone 3 (R3): Configurable Multi-Format Recipe Ordering & Grouping."""

import io
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib
import pytest
from fastapi.testclient import TestClient

from recipeboxer.api.app import create_app
from recipeboxer.agent.session import PipelineSession, PipelineState, get_session_manager
from recipeboxer.exporter.curation import (
    CuratedCookbook,
    CuratedSection,
    GroupingPreset,
    curate_recipes,
)
from recipeboxer.exporter.docx_export import DocxExporter, export_recipes_to_docx
from recipeboxer.exporter.google_docs import GoogleDocsExporter, export_recipes_to_google_docs_html
from recipeboxer.exporter.markdown_export import MarkdownExporter, export_recipes_to_markdown
from recipeboxer.exporter.pdf_export import PdfExporter, export_recipes_to_pdf
from recipeboxer.exporter.plan_to_eat import export_recipes_to_plan_to_eat_csv
from recipeboxer.models.recipe import RecipeSchema


@pytest.fixture
def test_recipes() -> list[RecipeSchema]:
    return [
        RecipeSchema(
            recipe_id="rec_soup",
            title="The Heirloom Tomato Soup",
            description="A comforting roasted tomato soup with fresh basil.",
            yield_servings="4 bowls",
            prep_time="15 mins",
            cook_time="30 mins",
            total_time="45 mins",
            ingredients=["2 lbs heirloom tomatoes", "1 cup vegetable broth", "Fresh basil leaves"],
            directions=["1. Roast tomatoes in oven.", "2. Simmer with broth and puree until smooth."],
            tags=["Appetizer", "Soup", "Vegetarian"],
            source_notes="Grandma Clara's Kitchen (Summer 1964)",
            card_ids=["card_soup_front"],
        ),
        RecipeSchema(
            recipe_id="rec_pie",
            title="Apple Cinnamon Pie",
            description="Crisp spiced apples in a flaky butter crust.",
            yield_servings="8 slices",
            prep_time="25 mins",
            cook_time="50 mins",
            total_time="1 hr 15 mins",
            ingredients=["6 apples, peeled and sliced", "1 cup sugar", "2 rolled pie crusts"],
            directions=["1. Toss apples with sugar and cinnamon.", "2. Bake at 375F until golden."],
            tags=["Dessert", "Baking", "Holiday"],
            source_notes="Aunt Mabel's Recipe Box",
            card_ids=["card_pie_front"],
        ),
        RecipeSchema(
            recipe_id="rec_roast",
            title="Braised Pot Roast",
            description="Tender beef chuck roast with carrots and potatoes.",
            yield_servings="6 servings",
            prep_time="20 mins",
            cook_time="3 hrs",
            total_time="3 hrs 20 mins",
            ingredients=["3 lb chuck roast", "4 carrots", "4 russet potatoes", "2 cups beef broth"],
            directions=["1. Sear beef on all sides.", "2. Braise on low heat for 3 hours."],
            tags=["Dinner", "Main Course", "Comfort Food"],
            source_notes="Grandma Clara's Sunday Suppers",
            card_ids=["card_roast_front"],
        ),
        RecipeSchema(
            recipe_id="rec_cookies",
            title="Chocolate Chip Cookies",
            description="Soft-baked cookies with semi-sweet chocolate chunks.",
            yield_servings="24 cookies",
            prep_time="15 mins",
            cook_time="10 mins",
            total_time="25 mins",
            ingredients=["2 cups flour", "1 cup chocolate chips", "1 cup butter", "3/4 cup sugar"],
            directions=["1. Cream butter and sugar.", "2. Mix in chips and bake at 350F for 10 minutes."],
            tags=["Dessert", "Cookies", "Quick"],
            source_notes="Aunt Mabel's Favorites",
            card_ids=["card_cookie_front"],
        ),
    ]


class TestCurationEngine:
    def test_preset_none_maintains_input_order(self, test_recipes):
        curated = curate_recipes(test_recipes, grouping_preset=GroupingPreset.NONE)
        assert curated.grouping_preset == GroupingPreset.NONE
        assert curated.total_recipe_count == 4
        assert [r.recipe_id for r in curated.all_recipes] == ["rec_soup", "rec_pie", "rec_roast", "rec_cookies"]
        assert len(curated.sections) == 1
        assert curated.sections[0].section_id == "section_all"

    def test_custom_recipe_order_reordering(self, test_recipes):
        custom_order = ["rec_cookies", "rec_roast", "rec_soup", "rec_pie"]
        curated = curate_recipes(
            test_recipes,
            recipe_order=custom_order,
            grouping_preset=GroupingPreset.CUSTOM,
        )
        assert curated.grouping_preset == GroupingPreset.CUSTOM
        assert [r.recipe_id for r in curated.all_recipes] == custom_order

    def test_custom_recipe_order_with_unlisted_appended(self, test_recipes):
        # Only list 2 recipes; remaining 2 should follow deterministically
        custom_order = ["rec_cookies", "rec_pie"]
        curated = curate_recipes(
            test_recipes,
            recipe_order=custom_order,
            grouping_preset=GroupingPreset.CUSTOM,
        )
        ordered_ids = [r.recipe_id for r in curated.all_recipes]
        assert ordered_ids[:2] == ["rec_cookies", "rec_pie"]
        assert "rec_soup" in ordered_ids[2:]
        assert "rec_roast" in ordered_ids[2:]
        assert len(ordered_ids) == 4

    def test_course_grouping_preset(self, test_recipes):
        curated = curate_recipes(test_recipes, grouping_preset=GroupingPreset.COURSE)
        assert curated.grouping_preset == GroupingPreset.COURSE
        section_titles = [s.title for s in curated.sections]
        
        # Appetizers / Soups, Main Courses, Desserts should be organized in culinary order
        assert any("Appetizer" in t or "Soup" in t for t in section_titles)
        assert any("Main" in t or "Entrée" in t for t in section_titles)
        assert any("Dessert" in t for t in section_titles)

        # Confirm all 4 recipes are distributed across sections
        assert curated.total_recipe_count == 4
        all_ids = [r.recipe_id for r in curated.all_recipes]
        assert set(all_ids) == {"rec_soup", "rec_pie", "rec_roast", "rec_cookies"}

    def test_contributor_grouping_preset(self, test_recipes):
        curated = curate_recipes(test_recipes, grouping_preset=GroupingPreset.CONTRIBUTOR)
        assert curated.grouping_preset == GroupingPreset.CONTRIBUTOR
        section_titles = [s.title for s in curated.sections]

        # Should group into Aunt Mabel and Grandma Clara
        assert any("Aunt Mabel" in t for t in section_titles)
        assert any("Grandma Clara" in t for t in section_titles)

        # Check that recipes are correctly grouped
        for sec in curated.sections:
            if "Aunt Mabel" in sec.title:
                mabel_ids = [r.recipe_id for r in sec.recipes]
                assert "rec_pie" in mabel_ids
                assert "rec_cookies" in mabel_ids
            elif "Grandma Clara" in sec.title:
                clara_ids = [r.recipe_id for r in sec.recipes]
                assert "rec_soup" in clara_ids
                assert "rec_roast" in clara_ids

    def test_tags_grouping_preset(self, test_recipes):
        curated = curate_recipes(test_recipes, grouping_preset=GroupingPreset.TAGS)
        assert curated.grouping_preset == GroupingPreset.TAGS
        assert len(curated.sections) > 0
        assert curated.total_recipe_count == 4

    def test_alphabetical_preset_strips_leading_articles(self, test_recipes):
        curated = curate_recipes(test_recipes, grouping_preset=GroupingPreset.ALPHABETICAL)
        assert curated.grouping_preset == GroupingPreset.ALPHABETICAL
        recipe_titles = [r.title for r in curated.all_recipes]
        assert recipe_titles == [
            "Apple Cinnamon Pie",
            "Braised Pot Roast",
            "Chocolate Chip Cookies",
            "The Heirloom Tomato Soup",
        ]


class TestDocxExporterCuration:
    def test_docx_export_with_toc_and_sections(self, test_recipes):
        curated = curate_recipes(
            test_recipes,
            grouping_preset=GroupingPreset.COURSE,
            include_toc=True,
            include_section_dividers=True,
            toc_title="Family Recipe Index",
        )
        docx_bytes = export_recipes_to_docx(curated.all_recipes, curation=curated)
        assert len(docx_bytes) > 0

        # Inspect generated DOCX OpenXML
        zip_buf = io.BytesIO(docx_bytes)
        with zipfile.ZipFile(zip_buf, "r") as zf:
            doc_str = zf.read("word/document.xml").decode("utf-8")
            # TOC Title present
            assert "Family Recipe Index" in doc_str
            # Dot leader tab stops present
            assert 'w:leader="dot"' in doc_str
            # Section dividers present
            assert "Dessert" in doc_str or "Main" in doc_str
            # All recipe titles present
            assert "The Heirloom Tomato Soup" in doc_str
            assert "Apple Cinnamon Pie" in doc_str


class TestPdfExporterCuration:
    def test_pdf_export_with_toc_and_sections(self, test_recipes):
        curated = curate_recipes(
            test_recipes,
            grouping_preset=GroupingPreset.CONTRIBUTOR,
            include_toc=True,
            include_section_dividers=True,
            toc_title="Cookbook Contents",
        )
        pdf_bytes = export_recipes_to_pdf(curated.all_recipes, curation=curated)
        assert len(pdf_bytes) > 0
        assert pdf_bytes.startswith(b"%PDF-1.4")
        assert b"%%EOF" in pdf_bytes

        # Decompress streams using length headers to verify TOC, Section Divider, and Recipes
        decompressed_texts = []
        for match in re.finditer(rb"<< /Length (\d+) /Filter /FlateDecode >>\nstream\n", pdf_bytes):
            length = int(match.group(1))
            stream_start = match.end()
            stream_data = pdf_bytes[stream_start:stream_start + length]
            dec = zlib.decompress(stream_data).decode("latin-1")
            decompressed_texts.append(dec)

        full_pdf_text = "\n".join(decompressed_texts)
        assert "Cookbook Contents" in full_pdf_text
        assert "Aunt Mabel" in full_pdf_text
        assert "Grandma Clara" in full_pdf_text
        assert "The Heirloom Tomato Soup" in full_pdf_text


class TestGoogleDocsExporterCuration:
    def test_google_docs_html_toc_anchor_links(self, test_recipes):
        curated = curate_recipes(
            test_recipes,
            grouping_preset=GroupingPreset.COURSE,
            include_toc=True,
            include_section_dividers=True,
            toc_title="Table of Contents",
        )
        html_out = export_recipes_to_google_docs_html(curated.all_recipes, curation=curated)
        assert '<div class="table-of-contents' in html_out
        assert '<h1 class="toc-title">Table of Contents</h1>' in html_out
        # Confirm anchor links matching recipe IDs
        assert '<a href="#recipe-rec_soup">' in html_out
        assert '<a href="#recipe-rec_pie">' in html_out
        assert 'id="recipe-rec_soup"' in html_out
        assert 'id="recipe-rec_pie"' in html_out
        # Confirm section divider
        assert '<div class="section-divider' in html_out

    def test_google_docs_batch_update_curation(self, test_recipes):
        curated = curate_recipes(
            test_recipes,
            grouping_preset=GroupingPreset.ALPHABETICAL,
            include_toc=True,
            include_section_dividers=True,
        )
        exporter = GoogleDocsExporter()
        requests = exporter.build_batch_update_requests(curated.all_recipes, curation=curated)
        assert len(requests) > 0
        insert_texts = [r["insertText"]["text"] for r in requests if "insertText" in r]
        assert any("Table of Contents" in t for t in insert_texts)


class TestMarkdownExporterCuration:
    def test_markdown_export_with_toc_and_sections(self, test_recipes):
        curated = curate_recipes(
            test_recipes,
            grouping_preset=GroupingPreset.COURSE,
            include_toc=True,
            include_section_dividers=True,
            toc_title="Cookbook Index",
        )
        md_str = export_recipes_to_markdown(curated.all_recipes, curation=curated, use_wikilinks=True)
        assert "# Cookbook Index" in md_str
        assert "[[#The Heirloom Tomato Soup]]" in md_str
        assert "[[#Apple Cinnamon Pie]]" in md_str


class TestApiExportEndpointsCuration:
    @pytest.fixture
    def client_with_session(self, test_recipes):
        manager = get_session_manager()
        session_id = "test_curation_session"
        session = manager.create_session(session_id=session_id)
        session.state = PipelineState.COMPLETED
        session.recipes = test_recipes
        app_instance = create_app(session_manager=manager)
        client = TestClient(app_instance)
        yield client, session_id
        manager._sessions.pop(session_id, None)

    def test_export_docx_with_curation_query_params(self, client_with_session):
        client, session_id = client_with_session
        resp = client.get(
            "/api/export/docx",
            params={
                "session_id": session_id,
                "grouping_preset": "course",
                "include_toc": "true",
                "include_section_dividers": "true",
                "toc_title": "Family Dishes",
            },
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        zip_buf = io.BytesIO(resp.content)
        with zipfile.ZipFile(zip_buf, "r") as zf:
            doc_str = zf.read("word/document.xml").decode("utf-8")
            assert "Family Dishes" in doc_str

    def test_export_pdf_with_curation_post_body(self, client_with_session):
        client, session_id = client_with_session
        payload = {
            "session_id": session_id,
            "recipe_order": ["rec_cookies", "rec_roast", "rec_soup", "rec_pie"],
            "grouping_preset": "custom",
            "include_toc": True,
            "include_section_dividers": False,
            "toc_title": "Custom Order",
        }
        resp = client.post("/api/export/pdf", json=payload)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content.startswith(b"%PDF-1.4")
        assert b"/Count" in resp.content

    def test_export_markdown_with_curation_post_body(self, client_with_session):
        client, session_id = client_with_session
        payload = {
            "session_id": session_id,
            "grouping_preset": "alphabetical",
            "include_toc": True,
            "toc_title": "Alphabetical Recipes",
            "use_wikilinks": True,
        }
        resp = client.post("/api/export/markdown", json=payload)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/markdown")
        assert "# Alphabetical Recipes" in resp.text
        assert "[[#Apple Cinnamon Pie]]" in resp.text

    def test_export_csv_with_custom_order(self, client_with_session):
        client, session_id = client_with_session
        resp = client.get(
            "/api/export/csv",
            params={
                "session_id": session_id,
                "recipe_order": "rec_cookies,rec_roast,rec_soup,rec_pie",
                "grouping_preset": "custom",
            },
        )
        assert resp.status_code == 200
        lines = resp.text.strip().split("\n")
        assert "Chocolate Chip Cookies" in lines[1]
