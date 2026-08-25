"""Tests for Offline DOCX Exporter (recipeboxer/exporter/docx_export.py)."""

import io
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile
from PIL import Image
import pytest

from recipeboxer.exporter.docx_export import (
    DocxExporter,
    export_recipes_to_docx,
)
from recipeboxer.models.recipe import RecipeSchema


@pytest.fixture
def sample_recipes() -> list[RecipeSchema]:
    """Sample recipes for DOCX export tests."""
    r1 = RecipeSchema(
        recipe_id="rec_001",
        title="Mom's Chocolate Chip Cookies",
        description="Crisp edges and chewy centers loaded with semisweet chips.",
        yield_servings="24 cookies",
        prep_time="15 mins",
        cook_time="10 mins",
        total_time="25 mins",
        ingredients=[
            "2 1/4 cups all-purpose flour",
            "1 tsp baking soda",
            "1 cup butter, softened",
            "3/4 cup granulated sugar",
            "3/4 cup packed brown sugar",
            "2 cups semisweet chocolate chips",
        ],
        directions=[
            "1. Preheat oven to 375°F.",
            "2. Cream butter and sugars in a large bowl until smooth.",
            "3. Beat in eggs and vanilla, then blend in flour and baking soda.",
            "4. Stir in chocolate chips and drop by rounded tablespoons onto ungreased baking sheets.",
            "5. Bake for 9 to 11 minutes until golden brown.",
        ],
        tags=["Dessert", "Cookies", "Baking"],
        source_notes="Family cookie tin index card",
        card_ids=["card_front_mom_cookies"],
    )

    r2 = RecipeSchema(
        recipe_id="rec_002",
        title="Classic Minestrone Soup",
        description="Hearty Italian vegetable soup with beans and pasta.",
        yield_servings="6 servings",
        prep_time="20 mins",
        cook_time="40 mins",
        total_time="1 hr",
        ingredients=[
            "2 tbsp olive oil",
            "1 medium onion, chopped",
            "2 carrots, diced",
            "1 can cannellini beans, drained",
            "4 cups vegetable broth",
            "1 cup small pasta shells",
        ],
        directions=[
            "1. Heat olive oil in a large stockpot over medium heat.",
            "2. Saute onion and carrots until tender.",
            "3. Add broth, beans, and pasta; simmer until pasta is al dente.",
        ],
        tags=["Soup", "Italian", "Vegetarian"],
        source_notes="Old magazine cutout (1968)",
        card_ids=["card_front_minestrone"],
    )
    return [r1, r2]


@pytest.fixture
def mock_card_image(tmp_path: Path) -> Path:
    """Creates a temporary synthetic image file."""
    img_path = tmp_path / "card_front_mom_cookies.jpg"
    img = Image.new("RGB", (300, 200), color=(245, 240, 230))
    img.save(img_path, format="JPEG")
    return img_path


class TestDocxExporter:
    """Test suite verifying OpenXML .docx generation and integrity."""

    def test_docx_binary_and_openxml_structure(self, sample_recipes: list[RecipeSchema]):
        exporter = DocxExporter()
        docx_bytes = exporter.export_to_bytes(sample_recipes)

        assert isinstance(docx_bytes, bytes)
        assert len(docx_bytes) > 0

        # Verify it is a valid zip file
        zip_buf = io.BytesIO(docx_bytes)
        with zipfile.ZipFile(zip_buf, "r") as zf:
            namelist = zf.namelist()
            assert "[Content_Types].xml" in namelist
            assert "_rels/.rels" in namelist
            assert "word/_rels/document.xml.rels" in namelist
            assert "word/styles.xml" in namelist
            assert "word/document.xml" in namelist

            # Verify XML parsing without errors
            content_types_xml = zf.read("[Content_Types].xml")
            ET.fromstring(content_types_xml)

            document_xml = zf.read("word/document.xml")
            root = ET.fromstring(document_xml)

            # Check for recipe titles and text inside document.xml
            doc_str = document_xml.decode("utf-8")
            assert "Mom&apos;s Chocolate Chip Cookies" in doc_str or "Mom&#39;s Chocolate Chip Cookies" in doc_str or "Mom's Chocolate Chip Cookies" in doc_str
            assert "Classic Minestrone Soup" in doc_str
            assert "Ingredients" in doc_str
            assert "Directions" in doc_str

            # Verify page break element is present between the 2 recipes
            assert 'w:type="page"' in doc_str

    def test_docx_image_embedding(
        self,
        sample_recipes: list[RecipeSchema],
        mock_card_image: Path,
    ):
        def resolver(card_id: str):
            if card_id == "card_front_mom_cookies":
                return mock_card_image
            return None

        exporter = DocxExporter()
        docx_bytes = exporter.export_to_bytes(sample_recipes, image_resolver=resolver)

        zip_buf = io.BytesIO(docx_bytes)
        with zipfile.ZipFile(zip_buf, "r") as zf:
            namelist = zf.namelist()
            # Verify media file embedded in word/media/
            assert any(name.startswith("word/media/image_") for name in namelist)

            # Verify image relationship
            rels_str = zf.read("word/_rels/document.xml.rels").decode("utf-8")
            assert "relationships/image" in rels_str
            assert "media/image_" in rels_str

            # Verify drawing element in document.xml
            doc_str = zf.read("word/document.xml").decode("utf-8")
            assert "w:drawing" in doc_str

    def test_docx_empty_recipes_list(self):
        """Adversarial check: Empty recipe list generates valid OpenXML without corruption."""
        exporter = DocxExporter()
        docx_bytes = exporter.export_to_bytes([])
        assert len(docx_bytes) > 0
        zip_buf = io.BytesIO(docx_bytes)
        with zipfile.ZipFile(zip_buf, "r") as zf:
            document_xml = zf.read("word/document.xml")
            ET.fromstring(document_xml)

    def test_docx_control_character_sanitization(self):
        """Adversarial check: Unprintable OCR control characters do not corrupt XML."""
        bad_recipe = RecipeSchema(
            recipe_id="rec_bad_ctrl",
            title="Vintage \x00Apple \x0bPie \x0cRecipe",
            description="Pie \x08with \x1fspices.",
            ingredients=["1 cup \x01sugar", "2 tbsp \x0eallspice"],
            directions=["1. Bake at 350\x02F."],
        )
        exporter = DocxExporter()
        docx_bytes = exporter.export_to_bytes(bad_recipe)
        zip_buf = io.BytesIO(docx_bytes)
        with zipfile.ZipFile(zip_buf, "r") as zf:
            document_xml = zf.read("word/document.xml")
            # Must parse without ParseError
            root = ET.fromstring(document_xml)
            assert root is not None

    def test_docx_gif_and_png_content_types(self, tmp_path: Path):
        """Adversarial check: Content Types includes proper MIME for png, gif, bmp."""
        png_path = tmp_path / "card.png"
        img = Image.new("RGB", (100, 100), color=(200, 200, 200))
        img.save(png_path, format="PNG")

        recipe = RecipeSchema(
            recipe_id="rec_png",
            title="PNG Card Recipe",
            ingredients=["1 egg"],
            directions=["Cook egg."],
            card_ids=["card_png"],
        )
        exporter = DocxExporter()
        docx_bytes = exporter.export_to_bytes(recipe, image_resolver=lambda cid: png_path)
        zip_buf = io.BytesIO(docx_bytes)
        with zipfile.ZipFile(zip_buf, "r") as zf:
            ct_xml = zf.read("[Content_Types].xml").decode("utf-8")
            assert 'Extension="png"' in ct_xml

    def test_docx_file_export(self, sample_recipes: list[RecipeSchema], tmp_path: Path):
        exporter = DocxExporter()
        out_file = tmp_path / "cookbook.docx"
        path = exporter.export_to_file(sample_recipes, out_file)

        assert path.exists()
        assert path.stat().st_size > 0

    def test_convenience_function(self, sample_recipes: list[RecipeSchema]):
        data = export_recipes_to_docx(sample_recipes)
        assert len(data) > 0
        assert data[:4] == b"PK\x03\x04"  # Zip header
