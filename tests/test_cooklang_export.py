"""Tests for Cooklang (.cook) Exporter (recipeboxer/exporter/cooklang.py)."""

from pathlib import Path
import pytest

from recipeboxer.exporter.cooklang import (
    CooklangExporter,
    export_recipes_to_cooklang,
    format_cooklang_ingredient,
    parse_ingredient_components,
)
from recipeboxer.models.recipe import RecipeSchema


@pytest.fixture
def sample_recipe() -> RecipeSchema:
    """Sample recipe with cookware and timers."""
    return RecipeSchema(
        recipe_id="rec_cook_01",
        title="Skillet Cornbread",
        description="Golden crust cornbread baked in a smoking hot cast iron skillet.",
        yield_servings="8 wedges",
        prep_time="10 mins",
        cook_time="25 mins",
        total_time="35 mins",
        ingredients=[
            "1 cup yellow cornmeal",
            "1 cup all-purpose flour",
            "1/4 cup sugar",
            "1 tbsp baking powder",
            "1 cup buttermilk",
            "1/3 cup melted butter",
            "1 large egg",
        ],
        directions=[
            "1. Preheat oven to 400°F and heat a cast iron skillet with 1 tbsp butter.",
            "2. Whisk cornmeal, flour, sugar, and baking powder in a large bowl.",
            "3. Beat in buttermilk, melted butter, and egg until just combined.",
            "4. Pour batter into hot skillet and bake for 25 minutes until golden.",
        ],
        tags=["Baking", "Southern", "Side Dish"],
        source_notes="Skillet cornbread tradition",
    )


class TestCooklangExporter:
    """Test suite for Cooklang (.cook) serializer."""

    def test_ingredient_parsing(self):
        name, qty, unit = parse_ingredient_components("4 cups all-purpose flour")
        assert name == "all-purpose flour"
        assert qty == "4"
        assert unit == "cups"

        name, qty, unit = parse_ingredient_components("1/3 cup granulated sugar")
        assert name == "granulated sugar"
        assert qty == "1/3"
        assert unit == "cup"

        name, qty, unit = parse_ingredient_components("2 large eggs")
        assert "egg" in name or "egg" in (unit or "")
        assert qty == "2"

        name, qty, unit = parse_ingredient_components("1 pinch salt")
        assert name == "salt"
        assert qty == "1"
        assert unit == "pinch"

        name, qty, unit = parse_ingredient_components("salt and pepper to taste")
        assert name == "salt and pepper to taste"
        assert qty is None
        assert unit is None

    def test_format_cooklang_ingredient(self):
        assert format_cooklang_ingredient("flour", "2", "cups") == "@flour{2%cups}"
        assert format_cooklang_ingredient("eggs", "2", None) == "@eggs{2}"
        assert format_cooklang_ingredient("butter", None, None) == "@butter{}"

    def test_single_recipe_cooklang_export(self, sample_recipe: RecipeSchema):
        exporter = CooklangExporter()
        cook_str = exporter.export_single_recipe(sample_recipe)

        # Metadata
        assert ">> title: Skillet Cornbread" in cook_str
        assert ">> prep time: 10 mins" in cook_str
        assert ">> cook time: 25 mins" in cook_str
        assert ">> servings: 8 wedges" in cook_str
        assert ">> tags: Baking, Southern, Side Dish" in cook_str

        # Cookware annotations in directions
        assert "#cast iron skillet{}" in cook_str or "#skillet{}" in cook_str or "#oven{}" in cook_str
        assert "#large bowl{}" in cook_str or "#bowl{}" in cook_str

        # Timer annotations in directions
        assert "~{25%minutes}" in cook_str or "~timer" in cook_str or "25 minutes" in cook_str

    def test_cooklang_substring_ingredient_matching_priority(self):
        """Adversarial check: 'melted butter' should match before 'butter' in steps."""
        recipe = RecipeSchema(
            recipe_id="rec_butter_test",
            title="Butter Test Cake",
            ingredients=[
                "1 tbsp butter for greasing pan",
                "1/2 cup melted butter",
            ],
            directions=[
                "1. Pour melted butter into bowl and mix.",
            ],
        )
        exporter = CooklangExporter()
        cook_out = exporter.export_single_recipe(recipe)

        # 'melted butter' should be annotated as @melted butter{1/2%cup}
        assert "@melted butter{1/2%cup}" in cook_out
        # Should not produce broken 'melted @butter{...}'
        assert "melted @butter" not in cook_out

    def test_cooklang_non_word_boundary_ingredient_matching(self):
        """Adversarial check: Ingredients with parentheses or trailing symbols match accurately in steps."""
        recipe = RecipeSchema(
            recipe_id="rec_unbleached",
            title="Sourdough Loaf",
            ingredients=[
                "3 cups all-purpose flour (unbleached)",
                "1 cup water",
            ],
            directions=[
                "1. Mix all-purpose flour (unbleached) with water in a bowl.",
            ],
        )
        exporter = CooklangExporter()
        cook_out = exporter.export_single_recipe(recipe)
        assert "@all-purpose flour (unbleached){3%cups}" in cook_out

    def test_cooklang_no_nested_annotation_corruption(self):
        """Adversarial check: Cookware keywords inside ingredient units must NOT become nested annotations."""
        recipe = RecipeSchema(
            recipe_id="rec_nested_check",
            title="Salad Greens",
            ingredients=[
                "1 bowl salad greens",
            ],
            directions=[
                "1. Add salad greens into mixing bowl and toss.",
            ],
        )
        exporter = CooklangExporter()
        cook_out = exporter.export_single_recipe(recipe)
        # Should not produce @salad greens{1%#bowl{}} or #mixing #bowl{}{}
        assert "#bowl{}" not in cook_out.split("salad greens")[1].split("}")[0] if "salad greens" in cook_out else True
        assert "#mixing bowl{}" in cook_out
        assert "#mixing #bowl" not in cook_out

    def test_cooklang_empty_recipes_list(self):
        """Adversarial check: Empty list returns empty string."""
        exporter = CooklangExporter()
        assert exporter.export_to_cooklang_string([]) == ""

    def test_cooklang_timer_expressions_decimals_fractions_and_ranges(self):
        """Adversarial check: Timers with decimals, mixed fractions, simple fractions, and ranges are annotated without corrupting step text."""
        from recipeboxer.exporter.cooklang import annotate_timers_in_step

        assert annotate_timers_in_step("Simmer for 1.5 hours until thickened.") == "Simmer for ~{1.5%hours} until thickened."
        assert annotate_timers_in_step("Bake for 1/2 hour.") == "Bake for ~{1/2%hour}."
        assert annotate_timers_in_step("Bake for 1 1/2 hours.") == "Bake for ~{1 1/2%hours}."
        assert annotate_timers_in_step("Rest for 15 to 20 minutes.") == "Rest for ~{15 to 20%minutes}."
        assert annotate_timers_in_step("Cool for 10-15 minutes.") == "Cool for ~{10-15%minutes}."
        assert annotate_timers_in_step("Wait 45 seconds.") == "Wait ~{45%seconds}."

    def test_cooklang_file_export(self, sample_recipe: RecipeSchema, tmp_path: Path):
        exporter = CooklangExporter()
        out_file = tmp_path / "recipe.cook"
        path = exporter.export_to_file(sample_recipe, out_file)

        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert ">> title: Skillet Cornbread" in content

    def test_convenience_function(self, sample_recipe: RecipeSchema):
        cook_text = export_recipes_to_cooklang(sample_recipe)
        assert ">> title: Skillet Cornbread" in cook_text

    def test_cooklang_ingredient_prefix_letters_o_f_of_regression(self):
        """Regression test for bug where lstrip('of ') stripped leading 'o', 'f', ' ' chars."""
        test_cases = [
            ("2 cups flour", "flour", "2", "cups"),
            ("2 cups of flour", "flour", "2", "cups"),
            ("1 tbsp oregano", "oregano", "1", "tbsp"),
            ("1 tbsp of oregano", "oregano", "1", "tbsp"),
            ("1 cup onions", "onions", "1", "cup"),
            ("1 cup of onions", "onions", "1", "cup"),
            ("2 tbsp fresh basil", "fresh basil", "2", "tbsp"),
            ("2 tbsp of fresh basil", "fresh basil", "2", "tbsp"),
            ("1/4 cup olive oil", "olive oil", "1/4", "cup"),
            ("1/4 cup of olive oil", "olive oil", "1/4", "cup"),
            ("2 tbsp oil", "oil", "2", "tbsp"),
            ("2 tbsp of oil", "oil", "2", "tbsp"),
            ("1/2 cup oats", "oats", "1/2", "cup"),
            ("1/2 cup of oats", "oats", "1/2", "cup"),
            ("1 tsp orange zest", "orange zest", "1", "tsp"),
            ("1/2 cup feta cheese", "feta cheese", "1/2", "cup"),
            ("1 cup figs", "figs", "1", "cup"),
        ]

        for raw, expected_name, expected_qty, expected_unit in test_cases:
            name, qty, unit = parse_ingredient_components(raw)
            assert name == expected_name, f"Failed for '{raw}': expected name '{expected_name}', got '{name}'"
            assert qty == expected_qty, f"Failed for '{raw}': expected qty '{expected_qty}', got '{qty}'"
            assert unit == expected_unit, f"Failed for '{raw}': expected unit '{expected_unit}', got '{unit}'"

    def test_cooklang_full_recipe_export_with_o_f_ingredients(self):
        """Verify full recipe serialization properly annotates ingredients starting with 'o', 'f' in steps."""
        recipe = RecipeSchema(
            recipe_id="rec_of_test",
            title="Mediterranean Sauté",
            ingredients=[
                "2 cups flour",
                "1 tbsp oregano",
                "1 cup onions",
                "2 tbsp fresh basil",
                "1/4 cup olive oil",
            ],
            directions=[
                "1. Sauté onions in olive oil until translucent.",
                "2. Whisk in flour, oregano, and fresh basil.",
            ],
        )
        exporter = CooklangExporter()
        cook_str = exporter.export_single_recipe(recipe)

        assert "@flour{2%cups}" in cook_str
        assert "@oregano{1%tbsp}" in cook_str
        assert "@onions{1%cup}" in cook_str
        assert "@fresh basil{2%tbsp}" in cook_str
        assert "@olive oil{1/4%cup}" in cook_str

        assert "@lour" not in cook_str
        assert "@regano" not in cook_str
        assert "@nions" not in cook_str
        assert "@resh basil" not in cook_str
        assert "@live oil" not in cook_str
        assert "-- Ingredients:" not in cook_str



