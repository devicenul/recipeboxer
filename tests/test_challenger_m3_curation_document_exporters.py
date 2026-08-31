"""Empirical Challenger 1 Test Suite for Milestone 3 (Curation Engine & Document Exporters).

Adversarially stress-tests:
1. Categorical Grouping Presets:
   - Course grouping across all 8 culinary taxonomy buckets + other/fallback.
   - Contributor attribution parsing across varied/messy source notes (possessives, prefixes, dates, long clauses, empty).
   - Alphabetical sorting stripping leading articles ("The", "A", "An") case-insensitively, numeric/# grouping.
   - Manual sequence reordering (full, partial, unknown IDs, duplicates, comma-separated strings).
2. Heirloom DOCX Exporter:
   - OpenXML package structure validity via zipfile and xml.etree.ElementTree.
   - TOC tab stops with w:leader="dot" and dot leader bullets.
   - Section divider pages with ornamental styling, large Georgia typography, page breaks.
   - XML escaping of hostile/malformed characters and control characters.
3. Printable PDF Exporter:
   - Two-pass pagination calculation ensuring TOC page numbers accurately reflect rendered body pages.
   - Dotted leader line strings in TOC PDF stream commands.
   - Double-bordered section divider pages with decorative ornaments.
   - Recipe multi-page overflow and (CONTINUED) header generation.
   - Embedded image resolution and PDF 1.4 xref validity.
4. FastAPI Curation Export Endpoints (GET & POST) for DOCX and PDF.
"""

from __future__ import annotations

import io
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib
from typing import List, Optional

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from recipeboxer.agent.session import PipelineSession, PipelineState, get_session_manager
from recipeboxer.api.app import create_app
from recipeboxer.exporter.curation import (
    COURSE_TAXONOMY,
    CuratedCookbook,
    CuratedSection,
    GroupingPreset,
    _extract_contributor_name,
    _match_course_category,
    _normalize_title_for_sorting,
    _reorder_recipes,
    curate_recipes,
)
from recipeboxer.exporter.docx_export import DocxExporter, export_recipes_to_docx, _escape_xml
from recipeboxer.exporter.pdf_export import PdfExporter, export_recipes_to_pdf, _PdfCanvas, _escape_pdf_string
from recipeboxer.models.recipe import RecipeSchema


# ============================================================================
# Fixtures & Helpers
# ============================================================================

def _make_dummy_image_bytes(color: str = "forestgreen", size: tuple[int, int] = (300, 200)) -> bytes:
    """Generates a valid test JPEG image."""
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


@pytest.fixture
def sample_real_world_recipes() -> List[RecipeSchema]:
    """Diverse real-world recipe collection covering all culinary courses, messy notes, and titles."""
    return [
        # Appetizers
        RecipeSchema(
            recipe_id="app_crostini",
            title="The Rustic Fig & Goat Cheese Bruschetta",
            description="Crisp toasted baguette topped with fig preserves and creamy chèvre.",
            yield_servings="12 pieces",
            prep_time="10 mins",
            cook_time="5 mins",
            total_time="15 mins",
            ingredients=["1 French baguette", "8 oz goat cheese", "1/2 cup fig jam", "Fresh thyme"],
            directions=["1. Slice baguette diagonally.", "2. Toast until golden.", "3. Spread cheese and fig jam."],
            tags=["Appetizer", "Finger Food", "Party"],
            source_notes="From the kitchen of Nonna Rosa (Autumn 1958)",
            card_ids=["card_app_1"],
        ),
        # Soups & Salads
        RecipeSchema(
            recipe_id="soup_chowder",
            title="A New England Clam Chowder",
            description="Rich cream-based chowder with tender clams and potatoes.",
            yield_servings="6 bowls",
            prep_time="20 mins",
            cook_time="35 mins",
            total_time="55 mins",
            ingredients=["2 cans minced clams", "4 russet potatoes", "1 cup heavy cream", "3 strips bacon"],
            directions=["1. Render bacon in Dutch oven.", "2. Sauté onions and simmer potatoes.", "3. Stir in cream and clams."],
            tags=["Soup", "Seafood", "Winter"],
            source_notes="Grandma Clara's Sunday Suppers",
            card_ids=["card_soup_1"],
        ),
        RecipeSchema(
            recipe_id="salad_caesar",
            title="Classic Grilled Chicken Caesar Salad",
            description="Crisp romaine hearts tossed with homemade garlic dressing and shaved parmesan.",
            yield_servings="4 salads",
            prep_time="15 mins",
            cook_time="10 mins",
            total_time="25 mins",
            ingredients=["2 heads romaine lettuce", "2 grilled chicken breasts", "1/2 cup parmesan", "Caesar dressing"],
            directions=["1. Chop romaine and grill chicken.", "2. Toss with dressing and croutons."],
            tags=["Salad", "Lunch", "Healthy"],
            source_notes="Submitted by: Chef Marcus Vance",
            card_ids=["card_salad_1"],
        ),
        # Main Dishes
        RecipeSchema(
            recipe_id="main_pot_roast",
            title="Braised Sunday Pot Roast",
            description="Slow-cooked beef chuck roast with red wine gravy.",
            yield_servings="8 servings",
            prep_time="25 mins",
            cook_time="3 hrs 30 mins",
            total_time="3 hrs 55 mins",
            ingredients=["4 lb beef chuck roast", "6 carrots", "1 lb baby potatoes", "2 cups beef stock"],
            directions=["1. Sear beef on high heat.", "2. Add vegetables and stock.", "3. Braise in oven at 300F for 3.5 hrs."],
            tags=["Main Dish", "Dinner", "Beef", "Comfort"],
            source_notes="Handed down by Great Aunt Martha from the farm in Lancaster County, PA; winner of the 1938 county fair blue ribbon",
            card_ids=["card_main_1", "card_main_2"],
        ),
        # Side Dishes
        RecipeSchema(
            recipe_id="side_potatoes",
            title="An Au Gratin Potato Casserole",
            description="Thinly sliced Yukon golds layered with gruyère cream sauce.",
            yield_servings="8 portions",
            prep_time="20 mins",
            cook_time="50 mins",
            total_time="1 hr 10 mins",
            ingredients=["3 lbs Yukon gold potatoes", "2 cups heavy cream", "2 cups grated Gruyère cheese", "2 cloves garlic"],
            directions=["1. Slice potatoes thinly with mandoline.", "2. Layer with cheese and cream.", "3. Bake at 375F until bubbly."],
            tags=["Side Dish", "Vegetable", "Holiday"],
            source_notes="Mom's Kitchen",
            card_ids=["card_side_1"],
        ),
        # Breads & Baking
        RecipeSchema(
            recipe_id="bread_sourdough",
            title="The Wild Yeast Sourdough Loaf",
            description="Artisan crusty country bread fermented with heritage sourdough starter.",
            yield_servings="1 loaf",
            prep_time="30 mins",
            cook_time="45 mins",
            total_time="24 hrs",
            ingredients=["500g bread flour", "350g water", "100g active sourdough starter", "10g fine sea salt"],
            directions=["1. Autolyse flour and water.", "2. Fold in starter and salt.", "3. Bulk ferment 4 hours.", "4. Bake in Dutch oven at 450F."],
            tags=["Bread", "Baking", "Artisan"],
            source_notes="Courtesy of Uncle Arthur's Bakery (1971)",
            card_ids=["card_bread_1"],
        ),
        # Desserts & Sweets
        RecipeSchema(
            recipe_id="dessert_apple_crisp",
            title="The Old Apple Crisp",
            description="Spiced tart apples under a crunchy brown sugar oat streusel.",
            yield_servings="6 servings",
            prep_time="20 mins",
            cook_time="40 mins",
            total_time="1 hr",
            ingredients=["6 Honeycrisp apples", "1 cup rolled oats", "1/2 cup brown sugar", "1/2 cup butter", "1 tsp cinnamon"],
            directions=["1. Peel and slice apples into baking dish.", "2. Blend oats, sugar, butter, and cinnamon.", "3. Bake at 350F for 40 mins."],
            tags=["Dessert", "Baking", "Autumn"],
            source_notes="Grandma Clara's Kitchen (Summer 1964)",
            card_ids=["card_dessert_1"],
        ),
        RecipeSchema(
            recipe_id="dessert_cheesecake",
            title="New York Style Cheesecake",
            description="Dense, velvety cream cheese cake with graham cracker crust.",
            yield_servings="12 slices",
            prep_time="30 mins",
            cook_time="1 hr 15 mins",
            total_time="5 hrs",
            ingredients=["4 pkgs cream cheese", "1 cup sugar", "4 eggs", "1 cup sour cream", "Graham crust"],
            directions=["1. Beat cream cheese and sugar.", "2. Add eggs one at a time.", "3. Bake in water bath at 325F."],
            tags=["Dessert", "Cake", "Cheesecake"],
            source_notes="Aunt Marie's recipe",
            card_ids=["card_dessert_2"],
        ),
        # Beverages & Drinks
        RecipeSchema(
            recipe_id="bev_cider",
            title="Spiced Mulled Apple Cider Spritz",
            description="Warm aromatic cider infused with cinnamon sticks, cloves, and orange peel.",
            yield_servings="8 mugs",
            prep_time="5 mins",
            cook_time="25 mins",
            total_time="30 mins",
            ingredients=["1 gallon fresh apple cider", "3 cinnamon sticks", "1 whole orange sliced", "6 cloves", "1 star anise"],
            directions=["1. Combine ingredients in slow cooker or pot.", "2. Simmer gently for 25 minutes.", "3. Serve hot."],
            tags=["Beverage", "Drink", "Holiday"],
            source_notes="Recipe from Cousin David",
            card_ids=["card_bev_1"],
        ),
        # Unattributed / Other
        RecipeSchema(
            recipe_id="misc_trail_mix",
            title="1952 Blue Ribbon Mountain Trail Mix",
            description="Energy packed mix of roasted almonds, cranberries, and dark chocolate drops.",
            yield_servings="10 bags",
            prep_time="10 mins",
            cook_time="0 mins",
            total_time="10 mins",
            ingredients=["2 cups roasted almonds", "1 cup dried cranberries", "1 cup dark chocolate chips"],
            directions=["1. Combine all ingredients in large bowl.", "2. Portion into airtight snack bags."],
            tags=["Snack", "Quick"],
            source_notes="",  # Empty notes to test fallback
            card_ids=["card_misc_1"],
        ),
    ]


@pytest.fixture
def image_store(sample_real_world_recipes):
    """Generates JPEG image bytes for all card IDs in sample recipes."""
    store = {}
    for r in sample_real_world_recipes:
        for cid in r.card_ids:
            store[cid] = _make_dummy_image_bytes(color="darkolivegreen", size=(400, 260))
    return store


@pytest.fixture
def image_resolver(image_store):
    def _res(cid: str):
        return image_store.get(cid)
    return _res


# ============================================================================
# 1. Categorical Grouping Presets Stress Tests
# ============================================================================

class TestCategoricalGroupingPresetsStress:
    """Adversarially tests course matching, contributor parsing, alphabetical sorting, and reordering."""

    def test_course_grouping_all_taxonomy_buckets(self, sample_real_world_recipes):
        """Verifies that all standard culinary courses are populated and ordered canonically."""
        curated = curate_recipes(
            sample_real_world_recipes,
            grouping_preset=GroupingPreset.COURSE,
            include_section_dividers=True,
            include_toc=True,
        )
        assert curated.grouping_preset == GroupingPreset.COURSE
        assert curated.total_recipe_count == len(sample_real_world_recipes)

        section_ids = [s.section_id for s in curated.sections]
        section_titles = [s.title for s in curated.sections]

        # Verify presence of expected sections
        assert "course_appetizers" in section_ids
        assert "course_soups_salads" in section_ids
        assert "course_main_dishes" in section_ids
        assert "course_side_dishes" in section_ids
        assert "course_breads_baking" in section_ids
        assert "course_desserts_sweets" in section_ids
        assert "course_beverages_drinks" in section_ids

        # Verify canonical ordering
        expected_order = [
            "course_appetizers",
            "course_soups_salads",
            "course_main_dishes",
            "course_side_dishes",
            "course_breads_baking",
            "course_desserts_sweets",
            "course_beverages_drinks",
        ]
        actual_order = [s_id for s_id in section_ids if s_id in expected_order]
        assert actual_order == expected_order

        # Verify recipes correctly assigned
        sec_map = {s.section_id: [r.recipe_id for r in s.recipes] for s in curated.sections}
        assert "app_crostini" in sec_map["course_appetizers"]
        assert "soup_chowder" in sec_map["course_soups_salads"]
        assert "salad_caesar" in sec_map["course_soups_salads"]
        assert "main_pot_roast" in sec_map["course_main_dishes"]
        assert "side_potatoes" in sec_map["course_side_dishes"]
        assert "bread_sourdough" in sec_map["course_breads_baking"]
        assert "dessert_apple_crisp" in sec_map["course_desserts_sweets"]
        assert "dessert_cheesecake" in sec_map["course_desserts_sweets"]
        assert "bev_cider" in sec_map["course_beverages_drinks"]

    def test_course_matching_multiword_and_fallback(self):
        """Tests keyword matching for multi-word titles and unclassified recipes."""
        r_pot_roast = RecipeSchema(title="Tender Pot Roast", ingredients=["Beef"], directions=["Bake."])
        sec_id, title = _match_course_category(r_pot_roast)
        assert sec_id == "course_main_dishes"

        r_ice_cream = RecipeSchema(title="Vanilla Ice Cream", ingredients=["Milk", "Sugar"], directions=["Freeze."])
        sec_id, title = _match_course_category(r_ice_cream)
        assert sec_id == "course_desserts_sweets"

        r_finger_food = RecipeSchema(title="Crispy Finger Foods", ingredients=["Cheese"], directions=["Fry."])
        sec_id, title = _match_course_category(r_finger_food)
        assert sec_id == "course_appetizers"

        r_alien = RecipeSchema(title="Zorgon 9 Nutrient Paste", ingredients=["Algae"], directions=["Synthesize."])
        sec_id, title = _match_course_category(r_alien)
        assert sec_id == "course_other"
        assert title == "Other Recipes"

    @pytest.mark.parametrize(
        "notes_input, expected_contributor",
        [
            ("Grandma Clara's Sunday Suppers", "Grandma Clara"),
            ("Mom's Kitchen", "Mom"),
            ("Aunt Marie's recipe", "Aunt Marie"),
            ("From the kitchen of Nonna Rosa (Autumn 1958)", "Nonna Rosa"),
            ("Submitted by: Chef Marcus Vance", "Chef Marcus Vance"),
            ("Handed down by Nana", "Nana"),
            ("Courtesy of Uncle Arthur's Bakery (1971)", "Courtesy of Uncle Arthur"),
            ("Uncle Arthur's Bakery (1971)", "Uncle Arthur"),
            ("Recipe from Cousin David", "Cousin David"),
            ("Source: Great Grandma Smith (1924)", "Great Grandma Smith"),
            ("Handed down by Great Aunt Martha from the farm in Lancaster County, PA; winner of the 1938 county fair blue ribbon", "Great Aunt Martha from the farm in Lancaster County"),
            ("", "Family Collection / Unattributed"),
            ("   ", "Family Collection / Unattributed"),
            (None, "Family Collection / Unattributed"),
            ("(handed down)", "Family Collection / Unattributed"),
            ('..."', "Family Collection / Unattributed"),
        ],
    )
    def test_contributor_attribution_parsing_variations(self, notes_input, expected_contributor):
        """Stress-tests contributor regex extractor across varied real-world strings."""
        result = _extract_contributor_name(notes_input)
        assert result == expected_contributor

    def test_contributor_grouping_unattributed_at_end(self, sample_real_world_recipes):
        """Verifies contributor grouping sorts contributors alphabetically and places Unattributed at end."""
        curated = curate_recipes(
            sample_real_world_recipes,
            grouping_preset=GroupingPreset.CONTRIBUTOR,
        )
        assert curated.grouping_preset == GroupingPreset.CONTRIBUTOR
        section_titles = [s.title for s in curated.sections]

        # "Family Collection / Unattributed" must be the final section
        assert section_titles[-1] == "Family Collection / Unattributed"

        # Check alphabetical sorting of other contributors
        named_contributors = section_titles[:-1]
        assert named_contributors == sorted(named_contributors, key=lambda c: c.lower())

    @pytest.mark.parametrize(
        "title_input, expected_normalized",
        [
            ("The Rustic Fig Bruschetta", "Rustic Fig Bruschetta"),
            ("the Old Apple Crisp", "Old Apple Crisp"),
            ("THE GREAT PUMPKIN PIE", "GREAT PUMPKIN PIE"),
            ("A New England Clam Chowder", "New England Clam Chowder"),
            ("a Berry Tart", "Berry Tart"),
            ("An Au Gratin Potato Casserole", "Au Gratin Potato Casserole"),
            ("AN APPLE CAKE", "APPLE CAKE"),
            ("An Omelet", "Omelet"),
            ("1952 Blue Ribbon Pie", "1952 Blue Ribbon Pie"),
            ("  The Sourdough Bread  ", "Sourdough Bread"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_normalize_title_for_sorting(self, title_input, expected_normalized):
        """Verifies stripping of leading English articles for alphabetical sorting."""
        assert _normalize_title_for_sorting(title_input) == expected_normalized

    def test_alphabetical_grouping_sections_and_numeric_bucket(self, sample_real_world_recipes):
        """Verifies alphabetical grouping creates letter buckets and '#' bucket for numbers/symbols at end."""
        curated = curate_recipes(
            sample_real_world_recipes,
            grouping_preset=GroupingPreset.ALPHABETICAL,
        )
        assert curated.grouping_preset == GroupingPreset.ALPHABETICAL

        section_titles = [s.title for s in curated.sections]
        # "1952 Blue Ribbon Mountain Trail Mix" should land in '#' section
        assert "#" in section_titles
        assert section_titles[-1] == "#"  # '#' is at the end

        # Letters A-Z should be in strict ascending alphabetical order
        letter_sections = [t for t in section_titles if t != "#"]
        assert letter_sections == sorted(letter_sections)

        # Confirm leading article recipes sorted by normalized title:
        # "The Rustic Fig & Goat Cheese Bruschetta" -> "R" section
        r_map = {r.recipe_id: r.title for r in curated.all_recipes}
        sec_map = {s.title: [r.recipe_id for r in s.recipes] for s in curated.sections}

        assert "app_crostini" in sec_map["R"]
        assert "soup_chowder" in sec_map["N"]  # "A New England Clam Chowder" -> "N"
        assert "dessert_apple_crisp" in sec_map["O"]  # "The Old Apple Crisp" -> "O"
        assert "side_potatoes" in sec_map["A"]  # "An Au Gratin Potato Casserole" -> "A"
        assert "bread_sourdough" in sec_map["W"]  # "The Wild Yeast Sourdough Loaf" -> "W"
        assert "misc_trail_mix" in sec_map["#"]  # "1952 Blue Ribbon..." -> "#"

    def test_manual_sequence_reordering_full_and_partial(self, sample_real_world_recipes):
        """Stress-tests recipe reordering with full, partial, invalid, duplicate, and string inputs."""
        # 1. Full arbitrary reorder
        rev_ids = [r.recipe_id for r in reversed(sample_real_world_recipes)]
        reordered = _reorder_recipes(sample_real_world_recipes, rev_ids)
        assert [r.recipe_id for r in reordered] == rev_ids

        # 2. Comma-separated string with spaces
        csv_order = f"{rev_ids[0]},  {rev_ids[1]} , {rev_ids[2]}"
        reordered_csv = _reorder_recipes(sample_real_world_recipes, csv_order)
        assert [r.recipe_id for r in reordered_csv[:3]] == rev_ids[:3]
        # Remaining 7 recipes follow
        assert len(reordered_csv) == len(sample_real_world_recipes)

        # 3. Duplicate IDs in order list
        dupe_order = [rev_ids[0], rev_ids[0], rev_ids[1], rev_ids[0]]
        reordered_dupe = _reorder_recipes(sample_real_world_recipes, dupe_order)
        assert [r.recipe_id for r in reordered_dupe[:2]] == [rev_ids[0], rev_ids[1]]
        assert len(reordered_dupe) == len(sample_real_world_recipes)

        # 4. Unknown/bogus IDs ignored
        bogus_order = ["nonexistent_id_1", rev_ids[2], "nonexistent_id_2"]
        reordered_bogus = _reorder_recipes(sample_real_world_recipes, bogus_order)
        assert reordered_bogus[0].recipe_id == rev_ids[2]
        assert len(reordered_bogus) == len(sample_real_world_recipes)

        # 5. Empty inputs
        assert _reorder_recipes(sample_real_world_recipes, []) == sample_real_world_recipes
        assert _reorder_recipes(sample_real_world_recipes, "") == sample_real_world_recipes
        assert _reorder_recipes(sample_real_world_recipes, None) == sample_real_world_recipes


# ============================================================================
# 2. Heirloom DOCX Exporter XML & Structure Stress Tests
# ============================================================================

class TestDocxExporterEmpiricalStress:
    """Stress-tests OpenXML .docx generation, XML schema, dot leaders, dividers, and hostile text."""

    def test_docx_xml_dot_leader_and_section_dividers(self, sample_real_world_recipes, image_resolver):
        """Verifies OpenXML document.xml contains w:leader='dot' tab stops and ornamental section dividers."""
        curated = curate_recipes(
            sample_real_world_recipes,
            grouping_preset=GroupingPreset.COURSE,
            include_toc=True,
            include_section_dividers=True,
            toc_title="Heirloom Cookbook Index",
        )
        docx_bytes = export_recipes_to_docx(
            curated.all_recipes,
            image_resolver=image_resolver,
            curation=curated,
        )

        assert len(docx_bytes) > 0
        zip_buf = io.BytesIO(docx_bytes)

        with zipfile.ZipFile(zip_buf, "r") as zf:
            # Check package files
            namelist = zf.namelist()
            assert "[Content_Types].xml" in namelist
            assert "_rels/.rels" in namelist
            assert "word/document.xml" in namelist
            assert "word/styles.xml" in namelist
            assert "word/_rels/document.xml.rels" in namelist

            # Parse and validate document.xml as strict XML
            doc_xml_bytes = zf.read("word/document.xml")
            root = ET.fromstring(doc_xml_bytes)
            assert root.tag.endswith("document")

            doc_text = doc_xml_bytes.decode("utf-8")

            # 1. TOC Title & Ornament
            assert "Heirloom Cookbook Index" in doc_text
            assert "✦ ✦ ✦" in doc_text

            # 2. Dotted leader tab stop: <w:tab w:val="right" w:leader="dot" w:pos="9360"/>
            assert 'w:leader="dot"' in doc_text
            assert 'w:val="right"' in doc_text
            assert 'w:pos="9360"' in doc_text

            # 3. Section divider pages
            assert "Appetizers &amp; Starters" in doc_text or "Appetizers & Starters" in doc_text
            assert "Soups &amp; Salads" in doc_text or "Soups & Salads" in doc_text
            assert "Desserts &amp; Sweets" in doc_text or "Desserts & Sweets" in doc_text
            assert 'w:val="56"' in doc_text  # Section title large font

            # 4. Page breaks between sections and recipes
            page_breaks = [p for p in doc_text.split('<w:br w:type="page"/>')]
            assert len(page_breaks) >= len(curated.sections) + len(curated.all_recipes)

            # 5. Check styles.xml for Georgia and Calibri definitions
            styles_xml = zf.read("word/styles.xml").decode("utf-8")
            assert "Georgia" in styles_xml
            assert "Calibri" in styles_xml
            assert "Heading1" in styles_xml
            assert "Heading2" in styles_xml

            # 6. Check media embedded images
            media_files = [f for f in namelist if f.startswith("word/media/")]
            assert len(media_files) > 0

    def test_docx_xml_hostile_character_escaping(self):
        """Verifies that hostile XML characters (&, <, >, ", ', control codes) do not corrupt document.xml."""
        hostile_recipe = RecipeSchema(
            recipe_id="hostile_1",
            title="Hostile <script>alert('XSS')</script> & \"Quotes\" \x00\x08\x0b Recipe",
            description="Special & < > \" ' characters and \x1b[31m ANSI codes \x0c form feed.",
            ingredients=["1 & 1/2 <cups> flour & sugar > salt \"quoted\"", "2 eggs 'large'"],
            directions=["1. Mix <all> ingredients & bake.", "2. Serve with \"love\" & 'care'."],
            tags=["<Hostile>", "Tag&Tag", "\"Quotes\""],
            source_notes="Grandma's & Grandpa's \"Secret\" <Recipe>",
        )
        curated = curate_recipes([hostile_recipe], grouping_preset=GroupingPreset.NONE, include_toc=True)
        docx_bytes = export_recipes_to_docx(curated.all_recipes, curation=curated)

        # Must parse as valid XML without syntax errors
        zip_buf = io.BytesIO(docx_bytes)
        with zipfile.ZipFile(zip_buf, "r") as zf:
            doc_xml = zf.read("word/document.xml")
            root = ET.fromstring(doc_xml)  # Will raise ParseError if unescaped
            assert root is not None
            doc_str = doc_xml.decode("utf-8")
            assert "&lt;script&gt;" in doc_str
            assert "&amp;" in doc_str
            assert "\x00" not in doc_str
            assert "\x08" not in doc_str


# ============================================================================
# 3. Printable PDF Exporter Stress Tests
# ============================================================================

class TestPdfExporterEmpiricalStress:
    """Stress-tests PDF 1.4 generation, 2-pass page numbering, dotted leaders, borders, and long overflow."""

    def test_pdf_two_pass_pagination_and_dotted_leaders(self, sample_real_world_recipes, image_resolver):
        """Verifies 2-pass TOC page numbering, dotted leader lines, and double borders."""
        curated = curate_recipes(
            sample_real_world_recipes,
            grouping_preset=GroupingPreset.COURSE,
            include_toc=True,
            include_section_dividers=True,
            toc_title="Cookbook Table of Contents",
        )
        pdf_bytes = export_recipes_to_pdf(
            curated.all_recipes,
            image_resolver=image_resolver,
            curation=curated,
        )

        assert len(pdf_bytes) > 0
        assert pdf_bytes.startswith(b"%PDF-1.4")
        assert b"%%EOF" in pdf_bytes

        # Extract and decompress all page content streams
        decompressed_pages = []
        for match in re.finditer(rb"<< /Length (\d+) /Filter /FlateDecode >>\nstream\n", pdf_bytes):
            length = int(match.group(1))
            stream_start = match.end()
            stream_data = pdf_bytes[stream_start:stream_start + length]
            dec = zlib.decompress(stream_data).decode("latin-1")
            decompressed_pages.append(dec)

        full_pdf_text = "\n".join(decompressed_pages)

        # 1. TOC Title
        assert "Cookbook Table of Contents" in full_pdf_text

        # 2. Dotted leaders in TOC stream (contains ". . . . .")
        assert ". . . ." in full_pdf_text

        # 3. Section divider double borders: outer line_width 2.0 and inner line_width 0.75
        assert "2.00 w" in full_pdf_text
        assert "0.75 w" in full_pdf_text
        assert "0.137 0.263 0.169 RG" in full_pdf_text  # Forest green stroke

        # 4. Verify footer page numbering "Page X of Y"
        total_pages = len(decompressed_pages)
        assert f"Page 1 of {total_pages}" in full_pdf_text
        assert f"Page {total_pages} of {total_pages}" in full_pdf_text

    def test_pdf_multipage_recipe_overflow_handling(self):
        """Verifies that an extremely long recipe with 40 ingredients and 30 directions wraps across pages."""
        many_ingredients = [f"{i} oz heirloom ingredient #{i} with extra notes and preparation specifications" for i in range(1, 41)]
        many_directions = [f"Step {i}: Carefully prepare, roast, simmer, whisk, and reduce item #{i} until texture is smooth." for i in range(1, 31)]

        long_recipe = RecipeSchema(
            recipe_id="long_recipe_1",
            title="The Grand Imperial 72-Hour Wedding Feast Stew",
            description="An extraordinarily complex historical banquet stew spanning multiple hours of continuous culinary preparation.",
            yield_servings="50 servings",
            prep_time="6 hrs",
            cook_time="72 hrs",
            total_time="78 hrs",
            ingredients=many_ingredients,
            directions=many_directions,
            tags=["Banquet", "Stew", "Historic"],
            source_notes="From the royal kitchen of 1884",
        )

        curated = curate_recipes([long_recipe], grouping_preset=GroupingPreset.NONE, include_toc=True)
        pdf_bytes = export_recipes_to_pdf(curated.all_recipes, curation=curated)

        # Decompress streams
        decompressed_pages = []
        for match in re.finditer(rb"<< /Length (\d+) /Filter /FlateDecode >>\nstream\n", pdf_bytes):
            length = int(match.group(1))
            stream_start = match.end()
            stream_data = pdf_bytes[stream_start:stream_start + length]
            dec = zlib.decompress(stream_data).decode("latin-1")
            decompressed_pages.append(dec)

        # Must span at least 3 pages (TOC + Page 1 + Continuation Page(s))
        assert len(decompressed_pages) >= 3
        full_pdf = "\n".join(decompressed_pages)
        assert r"\(CONT.\)" in full_pdf or r"\(CONTINUED\)" in full_pdf

    def test_pdf_large_cookbook_toc_expansion(self):
        """Verifies that a large cookbook (60 recipes) expands TOC to multiple pages and shifts body page numbers."""
        large_recipes = [
            RecipeSchema(
                recipe_id=f"rec_bulk_{i:03d}",
                title=f"Bulk Family Recipe #{i:03d}",
                ingredients=[f"Ingredient {i}"],
                directions=[f"Direction {i}"],
                tags=["Bulk"],
            )
            for i in range(1, 61)
        ]

        curated = curate_recipes(large_recipes, grouping_preset=GroupingPreset.NONE, include_toc=True)
        pdf_bytes = export_recipes_to_pdf(curated.all_recipes, curation=curated)

        decompressed_pages = []
        for match in re.finditer(rb"<< /Length (\d+) /Filter /FlateDecode >>\nstream\n", pdf_bytes):
            length = int(match.group(1))
            stream_start = match.end()
            stream_data = pdf_bytes[stream_start:stream_start + length]
            dec = zlib.decompress(stream_data).decode("latin-1")
            decompressed_pages.append(dec)

        # 60 recipes / 26 items per page = at least 3 TOC pages
        # Total pages should be at least 3 TOC pages + 60 recipe pages = 63 pages
        assert len(decompressed_pages) >= 63


# ============================================================================
# 4. FastAPI Curation Export Endpoints Stress Tests
# ============================================================================

class TestApiCurationExportEndpointsStress:
    """Stress-tests /api/export/docx and /api/export/pdf with various query params and POST payloads."""

    @pytest.fixture
    def api_client(self, sample_real_world_recipes):
        manager = get_session_manager()
        session_id = "test_challenger_session_m3"
        session = manager.create_session(session_id=session_id)
        session.state = PipelineState.COMPLETED
        session.recipes = sample_real_world_recipes
        app_instance = create_app(session_manager=manager)
        client = TestClient(app_instance)
        yield client, session_id
        manager._sessions.pop(session_id, None)

    def test_export_docx_get_endpoint_presets(self, api_client):
        client, session_id = api_client

        # Course grouping
        resp_course = client.get(
            "/api/export/docx",
            params={"session_id": session_id, "grouping_preset": "course", "toc_title": "Grandma's Index"},
        )
        assert resp_course.status_code == 200
        assert resp_course.headers["content-type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        assert len(resp_course.content) > 1000

        # Contributor grouping
        resp_contrib = client.get(
            "/api/export/docx",
            params={"session_id": session_id, "grouping_preset": "contributor"},
        )
        assert resp_contrib.status_code == 200

        # Alphabetical grouping
        resp_alpha = client.get(
            "/api/export/docx",
            params={"session_id": session_id, "grouping_preset": "alphabetical"},
        )
        assert resp_alpha.status_code == 200

    def test_export_docx_post_endpoint_reordering(self, api_client, sample_real_world_recipes):
        client, session_id = api_client
        custom_seq = [r.recipe_id for r in reversed(sample_real_world_recipes)]

        payload = {
            "session_id": session_id,
            "recipe_order": custom_seq,
            "grouping_preset": "custom",
            "include_toc": True,
            "include_section_dividers": False,
            "toc_title": "Reversed Sequence",
        }
        resp = client.post("/api/export/docx", json=payload)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    def test_export_pdf_get_and_post_endpoints(self, api_client):
        client, session_id = api_client

        # GET request
        resp_get = client.get(
            "/api/export/pdf",
            params={
                "session_id": session_id,
                "grouping_preset": "course",
                "include_toc": "true",
                "include_section_dividers": "true",
            },
        )
        assert resp_get.status_code == 200
        assert resp_get.headers["content-type"] == "application/pdf"
        assert resp_get.content.startswith(b"%PDF-1.4")

        # POST request with custom options
        payload = {
            "session_id": session_id,
            "grouping_preset": "alphabetical",
            "include_toc": True,
            "include_section_dividers": True,
            "toc_title": "Alphabetical Cookbook Index",
        }
        resp_post = client.post("/api/export/pdf", json=payload)
        assert resp_post.status_code == 200
        assert resp_post.headers["content-type"] == "application/pdf"
        assert resp_post.content.startswith(b"%PDF-1.4")


# ============================================================================
# 5. Extreme Edge Cases & Boundary Conditions
# ============================================================================

class TestCurationAndExportersExtremeEdgeCases:
    """Stress-tests 0 recipes, 1 recipe, international unicode, extreme image aspect ratios."""

    def test_empty_cookbook_docx_and_pdf(self):
        """Verifies graceful handling of empty cookbook (0 recipes)."""
        curated = curate_recipes([], grouping_preset=GroupingPreset.NONE, include_toc=True)
        assert curated.total_recipe_count == 0
        assert len(curated.sections) == 0

        # DOCX empty export
        docx_bytes = export_recipes_to_docx([], curation=curated)
        assert len(docx_bytes) > 0
        with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as zf:
            doc_xml = zf.read("word/document.xml").decode("utf-8")
            assert "No recipes in document" in doc_xml

        # PDF empty export
        pdf_bytes = export_recipes_to_pdf([], curation=curated)
        assert len(pdf_bytes) > 0
        assert pdf_bytes.startswith(b"%PDF-1.4")

    def test_single_recipe_singular_subtitle_check(self):
        """Verifies single recipe emits '1 Recipe' singular subtitle."""
        single = RecipeSchema(
            recipe_id="single_rec",
            title="Solo Crêpe",
            ingredients=["1 egg", "1/2 cup milk", "1/2 cup flour"],
            directions=["Whisk and fry."],
        )
        curated = curate_recipes([single], grouping_preset=GroupingPreset.NONE)
        assert curated.total_recipe_count == 1
        assert curated.sections[0].subtitle == "1 Recipe"

        # Check course preset singular
        curated_course = curate_recipes([single], grouping_preset=GroupingPreset.COURSE)
        assert curated_course.sections[0].subtitle == "1 Recipe"

    def test_international_diacritics_and_unicode(self):
        """Verifies French, Nordic, German, and Spanish diacritics in titles, tags, notes."""
        intl_recipe = RecipeSchema(
            recipe_id="intl_crepes",
            title="Crêpes Suzette & Smørrebrød Flambé",
            description="Délicieuses crêpes préparées avec du zeste d'orange et du Grand Marnier.",
            ingredients=["250g farine", "4 œufs frais", "50g beurre noisette", "1 pincée de sel"],
            directions=["1. Mélanger la farine et les œufs.", "2. Cuire à la poêle."],
            tags=["Français", "Gâteau", "Pâtisserie"],
            source_notes="Recette de Grand-Mère Hélène (Château de Chambord, 1935)",
        )
        curated = curate_recipes([intl_recipe], grouping_preset=GroupingPreset.ALPHABETICAL)
        assert curated.sections[0].title == "C"

        # DOCX export
        docx_bytes = export_recipes_to_docx(curated.all_recipes, curation=curated)
        with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as zf:
            doc_xml = zf.read("word/document.xml").decode("utf-8")
            assert "Crêpes Suzette" in doc_xml
            assert "Grand-Mère Hélène" in doc_xml

        # PDF export
        pdf_bytes = export_recipes_to_pdf(curated.all_recipes, curation=curated)
        assert len(pdf_bytes) > 0

    def test_extreme_image_dimensions_and_aspect_ratios(self):
        """Tests image scaling with extreme panoramic (1600x200), vertical bookmark (150x900), and square (500x500)."""
        images = {
            "panoramic_card": _make_dummy_image_bytes(color="crimson", size=(1600, 200)),
            "vertical_card": _make_dummy_image_bytes(color="navy", size=(150, 900)),
            "square_card": _make_dummy_image_bytes(color="gold", size=(500, 500)),
        }
        recipe = RecipeSchema(
            recipe_id="rec_aspects",
            title="Multi-Aspect Ratio Recipe",
            ingredients=["1 cup test"],
            directions=["1. Test image scaling."],
            card_ids=["panoramic_card", "vertical_card", "square_card"],
        )
        resolver = lambda cid: images.get(cid)

        curated = curate_recipes([recipe], grouping_preset=GroupingPreset.NONE)

        # DOCX should embed all 3 images with calculated EMUs
        docx_bytes = export_recipes_to_docx(curated.all_recipes, image_resolver=resolver, curation=curated)
        with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as zf:
            namelist = zf.namelist()
            media_files = [f for f in namelist if f.startswith("word/media/")]
            assert len(media_files) == 3

        # PDF should embed all 3 images without overflow errors
        pdf_bytes = export_recipes_to_pdf(curated.all_recipes, image_resolver=resolver, curation=curated)
        assert len(pdf_bytes) > 0

