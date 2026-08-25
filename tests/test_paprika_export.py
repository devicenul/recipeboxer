"""Tests for Paprika Recipe Manager (.paprikarecipes) Exporter (recipeboxer/exporter/paprika.py)."""

import base64
import gzip
import io
import json
from pathlib import Path
from PIL import Image
import pytest

from recipeboxer.exporter.paprika import (
    PaprikaExporter,
    export_recipes_to_paprika,
)
from recipeboxer.models.recipe import RecipeSchema


@pytest.fixture
def sample_recipes() -> list[RecipeSchema]:
    """Sample recipes for Paprika export tests."""
    r1 = RecipeSchema(
        recipe_id="rec_001",
        title="Lemon Blueberry Scones",
        description="Tender bakery-style scones with fresh lemon zest and blueberries.",
        yield_servings="8 scones",
        prep_time="15 mins",
        cook_time="18 mins",
        total_time="33 mins",
        ingredients=[
            "2 cups all-purpose flour",
            "1/3 cup sugar",
            "1 tbsp baking powder",
            "1/2 cup cold unsalted butter",
            "1 cup fresh blueberries",
            "1 tbsp fresh lemon zest",
            "1/2 cup heavy cream",
        ],
        directions=[
            "1. Preheat oven to 400°F and line a baking sheet with parchment.",
            "2. Whisk flour, sugar, baking powder, and lemon zest.",
            "3. Cut in cold butter until pea-sized crumbs form.",
            "4. Fold in blueberries and cream; shape into circle and slice into 8 wedges.",
            "5. Bake for 18 minutes until lightly browned.",
        ],
        tags=["Breakfast", "Baking", "Pastry"],
        source_notes="Bakery handwritten card",
        card_ids=["card_scones_front"],
    )

    r2 = RecipeSchema(
        recipe_id="rec_002",
        title="Iced Chai Latte",
        description="Spiced black tea latte over ice.",
        yield_servings="2 glasses",
        prep_time="5 mins",
        cook_time="5 mins",
        total_time="10 mins",
        ingredients=[
            "2 chai tea bags",
            "1 cup boiling water",
            "2 tbsp honey",
            "1 cup oat milk",
            "Ice cubes",
        ],
        directions=[
            "1. Steep tea bags in boiling water for 5 minutes.",
            "2. Stir in honey and let cool slightly.",
            "3. Pour over glasses filled with ice and top with oat milk.",
        ],
        tags=["Beverage", "Quick"],
        source_notes="Coffee shop notes",
    )
    return [r1, r2]


@pytest.fixture
def mock_card_image(tmp_path: Path) -> Path:
    """Creates a temporary synthetic image file."""
    img_path = tmp_path / "card_scones_front.jpg"
    img = Image.new("RGB", (320, 240), color=(255, 250, 240))
    img.save(img_path, format="JPEG")
    return img_path


class TestPaprikaExporter:
    """Test suite for Paprika (.paprikarecipes) archive exporter."""

    def test_single_recipe_paprika_dict(self, sample_recipes: list[RecipeSchema]):
        exporter = PaprikaExporter()
        p_dict = exporter.recipe_to_paprika_dict(sample_recipes[0])

        assert p_dict["name"] == "Lemon Blueberry Scones"
        assert "2 cups all-purpose flour" in p_dict["ingredients"]
        assert "\n" in p_dict["ingredients"]
        assert "1. Preheat oven to 400°F" in p_dict["directions"]
        assert p_dict["prep_time"] == "15 mins"
        assert p_dict["cook_time"] == "18 mins"
        assert p_dict["servings"] == "8 scones"
        assert p_dict["categories"] == ["Breakfast", "Baking", "Pastry"]
        assert p_dict["notes"] == "Bakery handwritten card"
        assert p_dict["uid"] is not None
        assert p_dict["hash"] is not None

    def test_paprika_photo_embedding(
        self,
        sample_recipes: list[RecipeSchema],
        mock_card_image: Path,
    ):
        def resolver(card_id: str):
            if card_id == "card_scones_front":
                return mock_card_image
            return None

        exporter = PaprikaExporter()
        p_dict = exporter.recipe_to_paprika_dict(sample_recipes[0], image_resolver=resolver)

        assert p_dict["photo"] == "card_scones_front.jpg"
        assert p_dict["photo_data"] is not None
        assert p_dict["photo_hash"] is not None

        # Verify photo_data is valid base64
        decoded = base64.b64decode(p_dict["photo_data"])
        assert len(decoded) > 0

    def test_gzip_archive_export_and_decompression(self, sample_recipes: list[RecipeSchema]):
        exporter = PaprikaExporter()
        archive_bytes = exporter.export_to_paprika_bytes(sample_recipes)

        # Decompress using standard gzip
        decompressed = gzip.decompress(archive_bytes)
        parsed = json.loads(decompressed.decode("utf-8"))

        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert parsed[0]["name"] == "Lemon Blueberry Scones"
        assert parsed[1]["name"] == "Iced Chai Latte"

        # Verify using decompress_paprika_archive helper
        records = PaprikaExporter.decompress_paprika_archive(archive_bytes)
        assert len(records) == 2
        assert records[0]["name"] == "Lemon Blueberry Scones"

    def test_zip_multi_recipe_archive(self, sample_recipes: list[RecipeSchema]):
        exporter = PaprikaExporter()
        zip_bytes = exporter.export_to_paprika_bytes(sample_recipes, as_zip_archive=True)

        records = PaprikaExporter.decompress_paprika_archive(zip_bytes)
        assert len(records) == 2
        assert records[0]["name"] in ["Lemon Blueberry Scones", "Iced Chai Latte"]

    def test_zip_single_recipe_archive(self, sample_recipes: list[RecipeSchema]):
        """Adversarial check: Single recipe exported with as_zip_archive=True returns valid ZIP containing .paprikarecipe."""
        exporter = PaprikaExporter()
        zip_bytes = exporter.export_to_paprika_bytes(sample_recipes[0], as_zip_archive=True)

        import zipfile
        assert zipfile.is_zipfile(io.BytesIO(zip_bytes))
        records = PaprikaExporter.decompress_paprika_archive(zip_bytes)
        assert len(records) == 1
        assert records[0]["name"] == "Lemon Blueberry Scones"


    def test_paprika_file_export(self, sample_recipes: list[RecipeSchema], tmp_path: Path):
        exporter = PaprikaExporter()
        out_file = tmp_path / "recipes.paprikarecipes"
        path = exporter.export_to_file(sample_recipes, out_file)

        assert path.exists()
        assert path.stat().st_size > 0

    def test_convenience_function(self, sample_recipes: list[RecipeSchema]):
        archive_data = export_recipes_to_paprika(sample_recipes)
        assert len(archive_data) > 0
        records = PaprikaExporter.decompress_paprika_archive(archive_data)
        assert len(records) == 2
