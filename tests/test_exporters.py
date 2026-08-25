"""Unit and validation tests for Multi-Format Exporters (Plan To Eat CSV, Schema.org JSON-LD, Raw JSON)."""

import json
from pathlib import Path
import pytest

from recipeboxer.exporter.json_export import (
    JsonExporter,
    export_recipes_to_json,
)
from recipeboxer.exporter.json_ld import (
    JsonLdExporter,
    export_recipes_to_json_ld,
    human_duration_to_iso8601,
)
from recipeboxer.exporter.plan_to_eat import (
    PLAN_TO_EAT_COLUMNS,
    PlanToEatExporter,
    export_recipes_to_plan_to_eat_csv,
)
from recipeboxer.models.recipe import RecipeSchema


@pytest.fixture
def sample_recipes() -> list[RecipeSchema]:
    """Provides sample recipes covering diverse attributes and characters."""
    r1 = RecipeSchema(
        title="Grandma's Cinnamon Rolls",
        description="Traditional holiday morning cinnamon rolls with cream cheese icing.",
        yield_servings="12 rolls",
        prep_time="30 mins",
        cook_time="25 mins",
        total_time="1 hr 15 mins",
        ingredients=[
            "4 cups all-purpose flour",
            "1 packet active dry yeast",
            "1/3 cup granulated sugar",
            "1 cup warm milk",
            "1/3 cup melted butter",
            "2 large eggs",
        ],
        directions=[
            "1. Dissolve yeast in warm milk.",
            "2. Mix in sugar, butter, eggs, and flour to form soft dough.",
            "3. Knead and let rise for 1 hour until doubled.",
            "4. Roll out, spread cinnamon sugar filling, slice, and bake at 350°F for 25 minutes.",
        ],
        tags=["Baking", "Breakfast", "Holiday"],
        source_notes="Grandma's handwritten index card (1974)",
    )

    r2 = RecipeSchema(
        title="Classic Caesar Salad",
        description="Crisp romaine lettuce with homemade garlic croutons and parmesan.",
        yield_servings="4 servings",
        prep_time="15 mins",
        cook_time="0 mins",
        total_time="15 mins",
        ingredients=[
            "1 large head romaine lettuce, chopped",
            "1/2 cup grated Parmesan cheese",
            "1 cup garlic croutons",
            "1/3 cup Caesar dressing",
        ],
        directions=[
            "1. Wash and thoroughly dry romaine lettuce.",
            "2. Toss with dressing until lightly coated.",
            "3. Top with grated Parmesan and crunchy croutons.",
        ],
        tags=["Salad", "Appetizer", "Quick"],
        source_notes="Vintage diner recipe",
    )
    return [r1, r2]


class TestPlanToEatExporter:
    """Tests for Plan To Eat RFC 4180 CSV export engine."""

    def test_csv_columns_and_rfc4180_format(self, sample_recipes: list[RecipeSchema]):
        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string(sample_recipes)

        assert "\r\n" in csv_str
        assert exporter.validate_csv_format(csv_str) is True

        lines = csv_str.split("\r\n")
        header = lines[0].split(",")
        assert header == PLAN_TO_EAT_COLUMNS

        # Check content
        assert "Grandma's Cinnamon Rolls" in csv_str
        assert "Classic Caesar Salad" in csv_str

    def test_csv_file_export(self, sample_recipes: list[RecipeSchema], tmp_path: Path):
        exporter = PlanToEatExporter()
        out_file = tmp_path / "export.csv"
        path = exporter.export_to_file(sample_recipes, out_file)

        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "Grandma's Cinnamon Rolls" in content


class TestJsonLdExporter:
    """Tests for Schema.org Recipe JSON-LD exporter."""

    def test_duration_parsing(self):
        """Test conversion of human-readable duration strings to ISO 8601 durations."""
        assert human_duration_to_iso8601("20 mins") == "PT20M"
        assert human_duration_to_iso8601("20 minutes") == "PT20M"
        assert human_duration_to_iso8601("1 hr") == "PT1H"
        assert human_duration_to_iso8601("1 hour 15 mins") == "PT1H15M"
        assert human_duration_to_iso8601("1 hr 15 mins") == "PT1H15M"
        assert human_duration_to_iso8601("2 hours 30 mins") == "PT2H30M"
        assert human_duration_to_iso8601("45 seconds") == "PT45S"
        assert human_duration_to_iso8601("1 day 2 hours") == "P1DT2H"
        assert human_duration_to_iso8601("PT30M") == "PT30M"
        assert human_duration_to_iso8601("45") == "PT45M"
        assert human_duration_to_iso8601("") is None
        assert human_duration_to_iso8601(None) is None

    def test_single_recipe_json_ld(self, sample_recipes: list[RecipeSchema]):
        exporter = JsonLdExporter()
        recipe = sample_recipes[0]
        data = exporter.export_to_json_ld_dict(recipe)

        assert data["@context"] == "https://schema.org"
        assert data["@type"] == "Recipe"
        assert data["name"] == "Grandma's Cinnamon Rolls"
        assert data["prepTime"] == "PT30M"
        assert data["cookTime"] == "PT25M"
        assert data["totalTime"] == "PT1H15M"
        assert data["recipeYield"] == "12 rolls"
        assert len(data["recipeIngredient"]) == 6
        assert len(data["recipeInstructions"]) == 4
        assert data["recipeInstructions"][0]["@type"] == "HowToStep"
        assert data["recipeInstructions"][0]["position"] == 1
        assert "Dissolve yeast" in data["recipeInstructions"][0]["text"]

    def test_multiple_recipes_json_ld_graph(self, sample_recipes: list[RecipeSchema]):
        exporter = JsonLdExporter()
        data = exporter.export_to_json_ld_dict(sample_recipes)

        assert data["@context"] == "https://schema.org"
        assert "@graph" in data
        assert len(data["@graph"]) == 2
        assert data["@graph"][0]["@type"] == "Recipe"
        assert data["@graph"][1]["@type"] == "Recipe"

    def test_json_ld_string_and_validation(self, sample_recipes: list[RecipeSchema]):
        json_ld_str = export_recipes_to_json_ld(sample_recipes)
        exporter = JsonLdExporter()
        assert exporter.validate_json_ld(json_ld_str) is True

        parsed = json.loads(json_ld_str)
        assert parsed["@context"] == "https://schema.org"

    def test_json_ld_file_export(self, sample_recipes: list[RecipeSchema], tmp_path: Path):
        exporter = JsonLdExporter()
        out_file = tmp_path / "recipes.jsonld"
        path = exporter.export_to_file(sample_recipes, out_file)

        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "https://schema.org" in content


class TestJsonExporter:
    """Tests for Standard Raw Recipe JSON exporter."""

    def test_raw_json_export(self, sample_recipes: list[RecipeSchema]):
        exporter = JsonExporter()
        json_str = exporter.export_to_json_string(sample_recipes)

        assert exporter.validate_json_export(json_str) is True
        data = json.loads(json_str)
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["title"] == "Grandma's Cinnamon Rolls"

    def test_single_recipe_json(self, sample_recipes: list[RecipeSchema]):
        exporter = JsonExporter()
        json_str = exporter.export_to_json_string(sample_recipes[0])

        assert exporter.validate_json_export(json_str) is True
        data = json.loads(json_str)
        assert isinstance(data, dict)
        assert data["title"] == "Grandma's Cinnamon Rolls"

    def test_json_file_export(self, sample_recipes: list[RecipeSchema], tmp_path: Path):
        exporter = JsonExporter()
        out_file = tmp_path / "recipes.json"
        path = exporter.export_to_file(sample_recipes, out_file)

        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "Grandma's Cinnamon Rolls" in content
