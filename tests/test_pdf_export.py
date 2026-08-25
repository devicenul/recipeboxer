"""Tests for Printable Archival PDF Exporter (recipeboxer/exporter/pdf_export.py)."""

import io
from pathlib import Path
import re
import zlib
from PIL import Image
import pytest

from recipeboxer.exporter.pdf_export import (
    PdfExporter,
    export_recipes_to_pdf,
)
from recipeboxer.models.recipe import RecipeSchema


@pytest.fixture
def sample_recipes() -> list[RecipeSchema]:
    """Sample recipes for PDF export tests."""
    r1 = RecipeSchema(
        recipe_id="rec_001",
        title="Heirloom Sourdough Bread",
        description="Crusty artisan loaf fermented with 50-year-old starter.",
        yield_servings="1 loaf",
        prep_time="20 mins",
        cook_time="45 mins",
        total_time="24 hrs",
        ingredients=[
            "500g bread flour",
            "350g water",
            "100g active sourdough starter",
            "10g fine sea salt",
        ],
        directions=[
            "1. Mix flour, water, and starter; rest for 30 minutes autolyse.",
            "2. Add salt and perform 4 stretch-and-folds over 2 hours.",
            "3. Bulk ferment at room temperature until doubled in volume.",
            "4. Shape into a round boule and cold retard overnight in banneton.",
            "5. Bake in a preheated Dutch oven at 450°F for 45 minutes.",
        ],
        tags=["Baking", "Bread", "Artisan"],
        source_notes="San Francisco bakery notebook (1976)",
        card_ids=["card_sourdough_front", "card_sourdough_back"],
    )
    return [r1]


@pytest.fixture
def mock_card_image(tmp_path: Path) -> Path:
    """Creates a temporary synthetic image file."""
    img_path = tmp_path / "card_sourdough_front.jpg"
    img = Image.new("RGB", (400, 300), color=(250, 245, 235))
    img.save(img_path, format="JPEG")
    return img_path


class TestPdfExporter:
    """Test suite verifying PDF generation, typography, and image embedding."""

    def test_pdf_header_and_structure(self, sample_recipes: list[RecipeSchema]):
        exporter = PdfExporter()
        pdf_bytes = exporter.export_to_bytes(sample_recipes)

        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 0

        # Validate PDF header and footer
        assert pdf_bytes.startswith(b"%PDF-1.4")
        assert b"%%EOF" in pdf_bytes

        # Validate PDF structural elements
        assert b"/Type /Catalog" in pdf_bytes
        assert b"/Type /Pages" in pdf_bytes
        assert b"/Type /Page" in pdf_bytes
        assert b"/Type /Font" in pdf_bytes
        assert b"xref" in pdf_bytes
        assert b"trailer" in pdf_bytes
        assert b"startxref" in pdf_bytes

    def test_pdf_image_embedding(
        self,
        sample_recipes: list[RecipeSchema],
        mock_card_image: Path,
    ):
        def resolver(card_id: str):
            if card_id == "card_sourdough_front":
                return mock_card_image
            return None

        exporter = PdfExporter()
        pdf_bytes = exporter.export_to_bytes(sample_recipes, image_resolver=resolver)

        # Validate Image XObject embedded
        assert b"/Type /XObject" in pdf_bytes
        assert b"/Subtype /Image" in pdf_bytes
        assert b"/Filter /DCTDecode" in pdf_bytes

    def test_pdf_long_recipe_multi_page_pagination(self):
        """Adversarial check: Long recipe with 25 ingredients and 10 directions paginates gracefully."""
        long_recipe = RecipeSchema(
            recipe_id="rec_long",
            title="Extremely Comprehensive Wedding Cake Recipe with Multi-Tier Assembly and Multiple Frostings",
            description="A detailed step-by-step heirloom wedding cake manual spanning multiple pages.",
            yield_servings="100 servings",
            prep_time="4 hrs",
            cook_time="2 hrs",
            total_time="6 hrs",
            ingredients=[f"Ingredient #{i}: {i * 50}g special baking component" for i in range(1, 26)],
            directions=[
                f"Step {i}: Carefully process component #{i} in mixer, blend thoroughly at medium speed for 5 minutes, scrape down the sides of the bowl, and incorporate evenly."
                for i in range(1, 13)
            ],
            tags=["Baking", "Wedding", "Celebration", "Advanced"],
            source_notes="Vintage pastry chef master ledger",
        )
        exporter = PdfExporter()
        pdf_bytes = exporter.export_to_bytes([long_recipe])

        assert pdf_bytes.startswith(b"%PDF-1.4")
        assert b"%%EOF" in pdf_bytes
        # Verify multiple pages were created in the Pages tree (/Count >= 2)
        assert b"/Count 2" in pdf_bytes or b"/Count 3" in pdf_bytes

        # Decompress streams using length headers to verify continuation header in decompressed content
        decompressed_texts = []
        for match in re.finditer(rb"<< /Length (\d+) /Filter /FlateDecode >>\nstream\n", pdf_bytes):
            length = int(match.group(1))
            stream_start = match.end()
            stream_data = pdf_bytes[stream_start:stream_start + length]
            dec = zlib.decompress(stream_data).decode("latin-1")
            decompressed_texts.append(dec)

        combined_decompressed = "\n".join(decompressed_texts)
        assert "CONT" in combined_decompressed

    def test_pdf_special_characters_and_fractions(self):
        """Adversarial check: Typographic quotes, fractions, degree symbols, and em-dashes."""
        recipe = RecipeSchema(
            recipe_id="rec_special_chars",
            title='“Grandma’s” Famous 450°F Scones — 1½ Cups Flour',
            ingredients=["½ cup sugar", "¼ tsp nutmeg", "¾ cup milk"],
            directions=["1. Bake at 400°F… test with toothpick ± 2 minutes."],
        )
        exporter = PdfExporter()
        pdf_bytes = exporter.export_to_bytes(recipe)
        assert pdf_bytes.startswith(b"%PDF-1.4")
        assert b"startxref" in pdf_bytes

    def test_pdf_file_export(self, sample_recipes: list[RecipeSchema], tmp_path: Path):
        exporter = PdfExporter()
        out_file = tmp_path / "cookbook.pdf"
        path = exporter.export_to_file(sample_recipes, out_file)

        assert path.exists()
        assert path.stat().st_size > 0

    def test_convenience_function(self, sample_recipes: list[RecipeSchema]):
        data = export_recipes_to_pdf(sample_recipes)
        assert len(data) > 0
        assert data.startswith(b"%PDF-1.4")
