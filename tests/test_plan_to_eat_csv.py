"""Unit and integration tests for Plan To Eat RFC 4180 CSV export engine.

Tests header exactness, multi-line intra-cell linebreaks, quote escaping (""),
CRLF terminators, culinary Unicode characters, file creation, and CSV roundtrip validation.
"""

import csv
import io
from pathlib import Path

import pytest

from recipeboxer.exporter.plan_to_eat import (
    PLAN_TO_EAT_COLUMNS,
    PlanToEatExporter,
    export_recipes_to_plan_to_eat_csv,
)
from recipeboxer.models.recipe import RecipeSchema


@pytest.fixture
def sample_recipe() -> RecipeSchema:
    """Fixture providing a standard recipe."""
    return RecipeSchema(
        recipe_id="rec_001",
        title="Classic Chocolate Chip Cookies",
        description="Crisp edges and chewy centers.",
        yield_servings="24 cookies",
        prep_time="15 mins",
        cook_time="10 mins",
        total_time="25 mins",
        ingredients=[
            "2 1/4 cups all-purpose flour",
            "1 tsp baking soda",
            "1 tsp salt",
            "1 cup butter, softened",
            "3/4 cup granulated sugar",
            "3/4 cup packed brown sugar",
            "2 large eggs",
            "2 cups semi-sweet chocolate morsels",
        ],
        directions=[
            "1. Preheat oven to 375°F.",
            "2. Combine flour, baking soda and salt in small bowl.",
            "3. Beat butter, granulated sugar, brown sugar and vanilla in large bowl.",
            "4. Add eggs one at a time, beating well.",
            "5. Drop by rounded tablespoon onto ungreased baking sheets.",
            "6. Bake for 9 to 11 minutes or until golden brown.",
        ],
        tags=["Dessert", "Cookies", "Baking"],
        source_notes="Nestle Toll House Card",
    )


def test_csv_exact_nine_columns_header(sample_recipe):
    """Verifies header row contains exact 9 columns in exact order."""
    exporter = PlanToEatExporter()
    csv_str = exporter.export_to_csv_string([sample_recipe])

    reader = csv.reader(io.StringIO(csv_str))
    header = next(reader)
    expected_header = [
        "Title",
        "Description",
        "Ingredients",
        "Directions",
        "Prep Time",
        "Total Time",
        "Yield",
        "Tags",
        "Source",
    ]
    assert header == expected_header
    assert header == PLAN_TO_EAT_COLUMNS


def test_csv_multiline_ingredients_cell(sample_recipe):
    """Tests multi-line ingredients are joined by newline inside quoted cell."""
    exporter = PlanToEatExporter()
    csv_str = exporter.export_to_csv_string([sample_recipe])

    reader = csv.DictReader(io.StringIO(csv_str))
    row = next(reader)

    ingredients = row["Ingredients"].split("\n")
    assert len(ingredients) == len(sample_recipe.ingredients)
    assert ingredients[0] == "2 1/4 cups all-purpose flour"
    assert ingredients[-1] == "2 cups semi-sweet chocolate morsels"


def test_csv_multiline_directions_cell(sample_recipe):
    """Tests numbered directions (1., 2.) joined by newline inside quoted cell."""
    exporter = PlanToEatExporter()
    csv_str = exporter.export_to_csv_string([sample_recipe])

    reader = csv.DictReader(io.StringIO(csv_str))
    row = next(reader)

    directions = row["Directions"].split("\n")
    assert len(directions) == len(sample_recipe.directions)
    assert directions[0] == "1. Preheat oven to 375°F."
    assert directions[-1] == "6. Bake for 9 to 11 minutes or until golden brown."


def test_csv_crlf_record_terminators(sample_recipe):
    """Verifies record terminators between rows are CRLF (\\r\\n)."""
    exporter = PlanToEatExporter()
    csv_str = exporter.export_to_csv_string([sample_recipe])

    assert "\r\n" in csv_str
    # The header line must end with \r\n
    header_end = csv_str.index("\r\n")
    assert header_end > 0
    header_line = csv_str[:header_end]
    assert header_line == ",".join(PLAN_TO_EAT_COLUMNS)


def test_csv_double_quote_escaping():
    """Tests description or directions containing double quotes escaped as double double-quotes ("")."""
    recipe = RecipeSchema(
        recipe_id="rec_quotes",
        title='Baker\'s "Secret" Brownies',
        description='Note: Use an 8" x 8" metal pan. Do not use 9" glass pan.',
        ingredients=['1 cup "Ghirardelli" cocoa powder', "2 cups sugar"],
        directions=[
            '1. Grease an 8" square baking pan.',
            '2. Bake until tester says "clean".',
        ],
        tags=['"Chocolate"', "Bar"],
        source_notes='From "The Joy of Cooking"',
    )

    exporter = PlanToEatExporter()
    csv_str = exporter.export_to_csv_string([recipe])

    # In raw CSV string, internal quotes should be doubled
    assert '8"" x 8"" metal pan' in csv_str
    assert '""Ghirardelli"" cocoa powder' in csv_str
    assert 'tester says ""clean""' in csv_str

    # When parsed back with csv reader, quotes are cleanly restored
    reader = csv.DictReader(io.StringIO(csv_str))
    row = next(reader)
    assert row["Title"] == 'Baker\'s "Secret" Brownies'
    assert row["Description"] == 'Note: Use an 8" x 8" metal pan. Do not use 9" glass pan.'
    assert '1 cup "Ghirardelli" cocoa powder' in row["Ingredients"]
    assert 'tester says "clean"' in row["Directions"]
    assert '"Chocolate", Bar' == row["Tags"]
    assert row["Source"] == 'From "The Joy of Cooking"'


def test_csv_comma_inside_fields():
    """Tests commas in Title, ingredients, and tags do not corrupt column counts."""
    recipe = RecipeSchema(
        recipe_id="rec_commas",
        title="Cookies, Chocolate Chip, Deluxe",
        description="Rich, buttery, and decadent.",
        ingredients=["1 cup butter, softened", "2 tsp vanilla, pure Madagascar"],
        directions=["1. Mix flour, sugar, and salt.", "2. Bake, cool, and serve."],
        tags=["Dessert", "Cookies, Drop", "Holiday, Christmas"],
        source_notes="Recipe Box, Grandma",
    )

    exporter = PlanToEatExporter()
    csv_str = exporter.export_to_csv_string([recipe])

    reader = csv.reader(io.StringIO(csv_str))
    header = next(reader)
    row = next(reader)

    assert len(header) == 9
    assert len(row) == 9
    assert row[0] == "Cookies, Chocolate Chip, Deluxe"
    assert row[1] == "Rich, buttery, and decadent."


def test_csv_newline_in_description():
    """Tests multi-line descriptions preserved within a single cell."""
    desc = "Line 1: Special holiday recipe.\nLine 2: Handed down for 3 generations.\nLine 3: Best served hot."
    recipe = RecipeSchema(
        recipe_id="rec_multiline_desc",
        title="Holiday Pot Roast",
        description=desc,
        ingredients=["3 lbs chuck roast", "1 onion"],
        directions=["1. Sear roast on all sides.", "2. Slow cook 8 hours."],
    )

    exporter = PlanToEatExporter()
    csv_str = exporter.export_to_csv_string([recipe])

    reader = csv.DictReader(io.StringIO(csv_str))
    row = next(reader)
    assert row["Description"] == desc


def test_csv_culinary_unicode_characters():
    """Tests vulgar fractions (½, ¼, ¾), degree symbol °F, and accented characters."""
    recipe = RecipeSchema(
        recipe_id="rec_unicode",
        title="Crème Brûlée with Jalapeño Infusion",
        description="Rich French custard with a spicy twist: ½ tsp cayenne & 2½ cups crème fraîche.",
        yield_servings="6 ramekins",
        prep_time="20 mins",
        cook_time="40 mins",
        total_time="1 hr 30 mins",
        ingredients=[
            "2½ cups heavy cream",
            "1 vanilla bean, split & scraped",
            "5 egg yolks",
            "⅓ cup sugar + 6 tbsp for brûlée",
            "¼ tsp jalapeño zest",
            "⅛ tsp sea salt",
        ],
        directions=[
            "1. Preheat oven to 325°F (165°C).",
            "2. Scald cream with vanilla bean.",
            "3. Whisk yolks and sugar until pale.",
            "4. Bake in water bath for 40–45 mins.",
        ],
        tags=["Dessert", "French", "Flambé"],
        source_notes="Café de Paris — Chef’s Notes",
    )

    exporter = PlanToEatExporter()
    csv_str = exporter.export_to_csv_string([recipe])
    csv_bytes = exporter.export_to_bytes([recipe])

    # Bytes roundtrip
    decoded_str = csv_bytes.decode("utf-8")
    assert decoded_str == csv_str

    reader = csv.DictReader(io.StringIO(csv_str))
    row = next(reader)
    assert row["Title"] == "Crème Brûlée with Jalapeño Infusion"
    assert "½ tsp" in row["Description"]
    assert "2½ cups" in row["Ingredients"]
    assert "325°F" in row["Directions"]
    assert "Café de Paris — Chef’s Notes" == row["Source"]


def test_csv_empty_optional_fields():
    """Tests recipes with None/empty optional fields produce valid empty cells without shifting columns."""
    recipe = RecipeSchema(
        recipe_id="rec_minimal",
        title="Plain Toast",
        description="",
        yield_servings="",
        prep_time="",
        cook_time="",
        total_time="",
        ingredients=["2 slices bread", "1 tbsp butter"],
        directions=["1. Toast bread in toaster.", "2. Spread butter evenly."],
        tags=[],
        source_notes="",
    )

    exporter = PlanToEatExporter()
    csv_str = exporter.export_to_csv_string([recipe])

    reader = csv.reader(io.StringIO(csv_str))
    header = next(reader)
    row = next(reader)

    assert len(header) == 9
    assert len(row) == 9
    assert row[0] == "Plain Toast"
    assert row[1] == ""
    assert row[4] == ""  # Prep Time
    assert row[5] == ""  # Total Time
    assert row[6] == ""  # Yield
    assert row[7] == ""  # Tags
    assert row[8] == ""  # Source


def test_csv_export_to_file_creates_directory(tmp_path, sample_recipe):
    """Calls export_to_file with nested non-existent directory and writes file."""
    exporter = PlanToEatExporter()
    out_file = tmp_path / "deep" / "nested" / "exports" / "plan_to_eat.csv"

    res_path = exporter.export_to_file([sample_recipe], out_file)
    assert res_path.exists()
    assert res_path.is_file()

    content = res_path.read_text(encoding="utf-8")
    assert exporter.validate_csv(content) is True


def test_csv_export_to_bytes(sample_recipe):
    """Calls export_to_bytes and verifies valid UTF-8 bytes."""
    exporter = PlanToEatExporter()
    data = exporter.export_to_bytes([sample_recipe])
    assert isinstance(data, bytes)
    assert len(data) > 0
    text = data.decode("utf-8")
    assert "Classic Chocolate Chip Cookies" in text


def test_csv_roundtrip_dict_reader(sample_recipe):
    """Parses serialized CSV with csv.DictReader; ensures 100% data fidelity."""
    exporter = PlanToEatExporter()
    csv_str = exporter.export_to_csv_string([sample_recipe])

    reader = csv.DictReader(io.StringIO(csv_str))
    rows = list(reader)
    assert len(rows) == 1
    r = rows[0]

    assert r["Title"] == sample_recipe.title
    assert r["Description"] == sample_recipe.description
    assert r["Ingredients"].split("\n") == sample_recipe.ingredients
    assert r["Directions"].split("\n") == sample_recipe.directions
    assert r["Prep Time"] == sample_recipe.prep_time
    assert r["Total Time"] == sample_recipe.total_time
    assert r["Yield"] == sample_recipe.yield_servings
    assert r["Tags"] == ", ".join(sample_recipe.tags)
    assert r["Source"] == sample_recipe.source_notes


def test_csv_empty_recipe_list():
    """Exports empty list of recipes; generates valid CSV with header row only."""
    exporter = PlanToEatExporter()
    csv_str = exporter.export_to_csv_string([])

    reader = csv.reader(io.StringIO(csv_str))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0] == PLAN_TO_EAT_COLUMNS


def test_csv_validate_helper_method():
    """Tests validate_csv_format and validate_csv on valid and malformed CSV strings."""
    exporter = PlanToEatExporter()

    # Valid CSV
    valid_csv = "Title,Description,Ingredients,Directions,Prep Time,Total Time,Yield,Tags,Source\r\n"
    assert exporter.validate_csv_format(valid_csv) is True
    assert exporter.validate_csv(valid_csv) is True

    # Empty string raises ValueError
    with pytest.raises(ValueError, match="CSV content is empty"):
        exporter.validate_csv("")

    with pytest.raises(ValueError, match="CSV content is empty"):
        exporter.validate_csv("   \n  ")

    # Mismatched header
    bad_header_csv = "Title,Description,Ingredients\r\n"
    with pytest.raises(ValueError, match="Header mismatch"):
        exporter.validate_csv(bad_header_csv)

    # Row length mismatch
    bad_row_csv = (
        "Title,Description,Ingredients,Directions,Prep Time,Total Time,Yield,Tags,Source\r\n"
        "Cookie,Desc,Flour,Bake,10 mins\r\n"
    )
    with pytest.raises(ValueError, match="column count mismatch"):
        exporter.validate_csv(bad_row_csv)


def test_csv_synthetic_deck_export(synthetic_card_deck):
    """Exports all 4 synthetic deck recipes, producing 5 rows total."""
    recipes = [RecipeSchema.model_validate(r) for r in synthetic_card_deck.expected_recipes]
    exporter = PlanToEatExporter()
    csv_str = exporter.export_to_csv_string(recipes)

    assert exporter.validate_csv(csv_str) is True

    reader = csv.reader(io.StringIO(csv_str))
    rows = list(reader)
    assert len(rows) == 5  # 1 header + 4 recipes

    titles = [row[0] for row in rows[1:]]
    assert titles == [
        "Grandma's Cinnamon Rolls",
        "Luscious Lemon Glaze Bars",
        "10-Minute Quick Chocolate Fudge",
        "Southern Pecan Pie",
    ]


def test_functional_export_wrapper(sample_recipe):
    """Tests export_recipes_to_plan_to_eat_csv functional wrapper."""
    csv_str = export_recipes_to_plan_to_eat_csv([sample_recipe])
    assert "Classic Chocolate Chip Cookies" in csv_str
    assert "Title,Description,Ingredients,Directions" in csv_str


def test_csv_emoji_and_symbols_preservation():
    """Tests that emojis and special culinary symbols survive serialization and roundtrip intact."""
    recipe = RecipeSchema(
        title="Grandma's 🍋 Lemon 🥧 Pie & 🍪 Cookies",
        description="Fresh zest 🍋 + flaky crust 🥧 ❤️",
        ingredients=["3 🍋 fresh lemons", "1 cup 🧈 butter", "2 cups 🌾 flour"],
        directions=["1. Zest 🍋.", "2. Mix with 🧈 butter.", "3. Bake at 350°F 🔥."],
        tags=["Dessert 🍰", "Summer ☀️"],
        source_notes="Family 📖 Notebook",
    )

    exporter = PlanToEatExporter()
    csv_str = exporter.export_to_csv_string([recipe])
    assert "🍋" in csv_str
    assert "🥧" in csv_str
    assert "🍪" in csv_str
    assert "🧈" in csv_str

    reader = csv.DictReader(io.StringIO(csv_str))
    row = next(reader)
    assert row["Title"] == "Grandma's 🍋 Lemon 🥧 Pie & 🍪 Cookies"
    assert "3 🍋 fresh lemons" in row["Ingredients"]
    assert "1. Zest 🍋." in row["Directions"]


def test_csv_validate_empty_rows():
    """Tests validate_csv when given header with trailing whitespace or no data rows."""
    exporter = PlanToEatExporter()
    content = "Title,Description,Ingredients,Directions,Prep Time,Total Time,Yield,Tags,Source\r\n"
    assert exporter.validate_csv_format(content) is True

