"""Empirical Challenger 2 Test Suite: Recipe Extraction (M3) and Plan To Eat CSV Exporter (M4).

Stress tests edge cases, massive payloads, Unicode/fractions/emojis, punctuation/quotes,
CSV RFC 4180 parsing fidelity with csv.reader and csv.DictReader, and FastAPI export endpoints.
"""

from __future__ import annotations

import asyncio
import csv
import io
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from recipeboxer.agent.orchestrator import RecipeBoxerAgent
from recipeboxer.agent.session import PipelineSession, SessionManager
from recipeboxer.ai import ScriptedLLMClient
from recipeboxer.api.app import create_app
from recipeboxer.exporter.plan_to_eat import (
    PLAN_TO_EAT_COLUMNS,
    PlanToEatExporter,
    export_recipes_to_plan_to_eat_csv,
)

TINY_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c"
    b"\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c"
    b"\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01"
    b"\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01"
    b"\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06"
    b"\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
)
from recipeboxer.extraction.extractor import GeminiRecipeExtractor
from recipeboxer.models.domain import (
    BoundaryText,
    CardRole,
    InkInfo,
    PaperColorInfo,
    PaperRulingInfo,
    VisualFingerprint,
)
from recipeboxer.models.hitl import CardPairApproval, HITLDecision
from recipeboxer.models.pipeline import PipelineState
from recipeboxer.models.recipe import RecipeSchema


# ============================================================================
# 1. Recipe Extraction & Schema Stress Tests
# ============================================================================


class TestRecipeExtractionEdgeCases:
    """Stress-tests RecipeSchema validation and GeminiRecipeExtractor edge cases."""

    def test_recipe_schema_minimal_fields(self):
        """Tests that RecipeSchema accepts minimal valid payload with defaults."""
        recipe = RecipeSchema(
            title="Minimalist Sourdough",
            ingredients=["500g flour", "350g water", "10g salt"],
            directions=["Mix and bake."],
        )
        assert recipe.title == "Minimalist Sourdough"
        assert recipe.description == ""
        assert recipe.yield_servings == ""
        assert recipe.prep_time == ""
        assert recipe.cook_time == ""
        assert recipe.total_time == ""
        assert recipe.tags == []
        assert recipe.source_notes == ""
        assert recipe.directions == ["1. Mix and bake."]

    def test_recipe_schema_empty_title_rejection(self):
        """Tests that empty or whitespace-only title raises validation error."""
        with pytest.raises(ValueError, match="Recipe title cannot be empty"):
            RecipeSchema(title="   ", ingredients=["1 apple"], directions=["Eat apple."])

    def test_recipe_schema_direction_numbering_normalization_permutations(self):
        """Tests direction auto-numbering with various messy real-world prefixes."""
        raw_directions = [
            "Step 1: Preheat the oven to 400°F.",
            "2) In a large mixing bowl, combine dry ingredients.",
            "(3) Cut in cold butter with pastry blender.",
            "4 - Slowly pour in buttermilk while stirring.",
            "5. Form dough into a rough ball.",
            "Bake for 15-18 minutes until golden brown.",  # unnumbered
            "   ",  # whitespace line to be filtered
            "7. Brush tops with melted honey butter.",
        ]
        recipe = RecipeSchema(
            title="Buttermilk Biscuits",
            ingredients=["2 cups flour", "1 tbsp baking powder", "1/2 cup butter"],
            directions=raw_directions,
        )

        assert len(recipe.directions) == 7
        assert recipe.directions[0] == "1. Preheat the oven to 400°F."
        assert recipe.directions[1] == "2. In a large mixing bowl, combine dry ingredients."
        assert recipe.directions[2] == "3. Cut in cold butter with pastry blender."
        assert recipe.directions[3] == "4. Slowly pour in buttermilk while stirring."
        assert recipe.directions[4] == "5. Form dough into a rough ball."
        assert recipe.directions[5] == "6. Bake for 15-18 minutes until golden brown."
        assert recipe.directions[6] == "7. Brush tops with melted honey butter."

    def test_recipe_schema_tag_cleaning_and_deduplication(self):
        """Tests tag whitespace trimming, case-insensitive deduplication, and order preservation."""
        raw_tags = ["Dessert", "  baking ", "DESSERT", "Cookies", "Baking", "quick", "QUICK", ""]
        recipe = RecipeSchema(
            title="Sugar Cookies",
            ingredients=["1 cup sugar", "1 cup butter"],
            directions=["Mix and bake."],
            tags=raw_tags,
        )
        assert recipe.tags == ["Dessert", "baking", "Cookies", "quick"]

    def test_recipe_schema_clean_ingredients_list(self):
        """Tests that whitespace, blank lines, and empty strings are stripped from ingredients."""
        raw_ingredients = [
            "  2 cups all-purpose flour  ",
            "",
            "   ",
            "1 tsp pure vanilla extract",
            "   1/2 tsp kosher salt   ",
        ]
        recipe = RecipeSchema(
            title="Shortbread",
            ingredients=raw_ingredients,
            directions=["Mix, press into pan, bake."],
        )
        assert recipe.ingredients == [
            "2 cups all-purpose flour",
            "1 tsp pure vanilla extract",
            "1/2 tsp kosher salt",
        ]

    @pytest.mark.asyncio
    async def test_extractor_offline_boundary_text_fallback_handling(self, tmp_path):
        """Tests boundary text title sanitization with injected mock client."""
        recipe_data = RecipeSchema(
            title="Grandma's Famous Molasses Ginger Cookies (1942)",
            ingredients=["1/2 cup molasses", "2 cups flour"],
            directions=["Mix and bake."],
        )
        client = ScriptedLLMClient([recipe_data])
        extractor = GeminiRecipeExtractor(llm_client=client)
        img_p = tmp_path / "noisy_boundary_card.jpg"
        img_p.write_bytes(TINY_JPEG)
        fp = VisualFingerprint(
            card_id="noisy_boundary_card.jpg",
            image_path=str(img_p),
            sha256_hash="abcdef0123456789",
            role=CardRole.STANDALONE,
            boundary_text=BoundaryText(
                first_line="1. Grandma's Famous Molasses Ginger Cookies (1942)",
                last_line="Yield: 3 dozen cookies. Enjoy with cold milk!",
            ),
        )
        recipe = await extractor.extract(fp)
        assert isinstance(recipe, RecipeSchema)
        assert "Molasses Ginger Cookies" in recipe.title
        assert not recipe.title.startswith("1.")

    @pytest.mark.asyncio
    async def test_extractor_batch_concurrency_stress(self, tmp_path):
        """Tests batch extraction under high concurrency load across multiple pairs."""
        cards = []
        for i in range(6):
            p = tmp_path / f"card_{i:02d}.jpg"
            p.write_bytes(TINY_JPEG)
            cards.append(p)

        # Construct 20 extraction tasks
        pairs = [
            (cards[0], cards[1]),
            (cards[2], cards[3]),
            (cards[4], None),
            (cards[5], None),
        ] * 5

        batch_recipe = RecipeSchema(
            title="Batch Recipe",
            ingredients=["1 cup sugar", "2 cups flour"],
            directions=["Mix and bake at 350F."],
        )
        client = ScriptedLLMClient([batch_recipe] * 20)
        extractor = GeminiRecipeExtractor(llm_client=client)

        recipes = await extractor.extract_batch(pairs, concurrency=8)
        assert len(recipes) == 20
        for r in recipes:
            assert isinstance(r, RecipeSchema)
            assert len(r.title) > 0
            assert len(r.ingredients) > 0
            assert len(r.directions) > 0


# ============================================================================
# 2. Plan To Eat CSV Serialization & RFC 4180 Parsing Fidelity Stress Tests
# ============================================================================


class TestPlanToEatCSVStress:
    """Stress-tests Plan To Eat CSV serialization, quoting, Unicode, scale, and parser fidelity."""

    def test_csv_exact_nine_columns_and_header_format(self):
        """Verifies exact 9 column headers, casing, order, and CRLF line terminators."""
        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string([])

        # Header line inspection
        lines = csv_str.split("\r\n")
        assert lines[0] == "Title,Description,Ingredients,Directions,Prep Time,Total Time,Yield,Tags,Source"
        assert len(PLAN_TO_EAT_COLUMNS) == 9

        # csv.reader header validation
        reader = csv.reader(io.StringIO(csv_str))
        headers = next(reader)
        assert headers == [
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

    def test_csv_massive_ingredient_and_direction_counts(self):
        """Stress test with 120+ ingredients and 100+ directions; verifies no cell/row truncation."""
        ingredients = [f"{i}g ingredient number {i}, finely diced & sifted" for i in range(1, 125)]
        directions = [f"Step detail {i}: Mix thoroughly and verify consistency at {i * 2}°F." for i in range(1, 105)]

        recipe = RecipeSchema(
            title="Massive Banquet Feast Recipe",
            description="Stress testing 120+ ingredients and 100+ direction steps in a single card.",
            yield_servings="500 servings",
            prep_time="4 hours",
            cook_time="6 hours",
            total_time="10 hours",
            ingredients=ingredients,
            directions=directions,
            tags=["Banquet", "Scale Test", "Catering"],
            source_notes="Grand Master Chef Banquet Archives",
        )

        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string([recipe])

        # Validate with PlanToEatExporter validator
        assert exporter.validate_csv(csv_str) is True

        # Parse with standard csv.reader
        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 2  # Exactly 1 header + 1 data row! No row splitting!
        data_row = rows[1]
        assert len(data_row) == 9  # Exactly 9 columns!

        # Parse with standard csv.DictReader
        dict_reader = csv.DictReader(io.StringIO(csv_str))
        dict_rows = list(dict_reader)
        assert len(dict_rows) == 1
        r = dict_rows[0]

        parsed_ingredients = r["Ingredients"].split("\n")
        assert len(parsed_ingredients) == 124
        assert parsed_ingredients[0] == "1g ingredient number 1, finely diced & sifted"
        assert parsed_ingredients[-1] == "124g ingredient number 124, finely diced & sifted"

        parsed_directions = r["Directions"].split("\n")
        assert len(parsed_directions) == 104
        assert parsed_directions[0].startswith("1. Step detail 1:")
        assert parsed_directions[-1].startswith("104. Step detail 104:")

    def test_csv_heavy_punctuation_quotes_and_commas(self):
        """Tests heavy punctuation, nested double quotes, single quotes, commas, colons, semicolons."""
        recipe = RecipeSchema(
            title='Chef\'s "Magnifique" Dark Chocolate, Hazelnut & "Espresso" Torte, 9"',
            description='Note: Use a 9" springform pan; grease with 1 tbsp "softened" butter. Do not substitute 8" pan!',
            ingredients=[
                '8 oz "Valrhona" 70% dark chocolate, chopped',
                '1/2 cup "pure" unsalted butter, cut into 1/2" cubes',
                '1 tsp espresso powder, dissolved in 1 tbsp "hot" water',
                '4 large eggs, separated, room temperature (68°-70°F)',
            ],
            directions=[
                '1. Melt chocolate, butter, and espresso in heatproof bowl set over simmering water (do not let bowl touch water!).',
                '2. Whisk yolks with 1/2 cup sugar until pale, thick, and ribbon-like.',
                '3. Gently fold chocolate mixture into yolks; then fold in egg whites in 3 additions (do not overmix!).',
                '4. Bake at 350°F for 35-40 mins until edges are set, but center slightly "wobbly".',
            ],
            tags=['"Gourmet"', 'Dessert, Fine Dining', 'Chocolate & Espresso', 'Gluten-Free, Naturally'],
            source_notes='From "The French Culinary Masterclass", Vol. 2, pp. 104-106',
        )

        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string([recipe])

        # Raw string check: internal quotes are doubled
        assert '""Valrhona"" 70% dark chocolate' in csv_str
        assert '9"" springform pan' in csv_str

        # Parse with standard csv.reader: exactly 1 data row, exactly 9 columns
        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 2
        assert len(rows[1]) == 9

        # Parse with standard csv.DictReader
        dict_reader = csv.DictReader(io.StringIO(csv_str))
        row = next(dict_reader)

        assert row["Title"] == 'Chef\'s "Magnifique" Dark Chocolate, Hazelnut & "Espresso" Torte, 9"'
        assert row["Description"] == 'Note: Use a 9" springform pan; grease with 1 tbsp "softened" butter. Do not substitute 8" pan!'
        assert '8 oz "Valrhona" 70% dark chocolate, chopped' in row["Ingredients"]
        assert '4. Bake at 350°F for 35-40 mins until edges are set, but center slightly "wobbly".' in row["Directions"]
        assert row["Tags"] == '"Gourmet", Dessert, Fine Dining, Chocolate & Espresso, Gluten-Free, Naturally'
        assert row["Source"] == 'From "The French Culinary Masterclass", Vol. 2, pp. 104-106'

    def test_csv_unicode_fractions_emojis_and_multilingual_scripts(self):
        """Tests vulgar fractions, temperature degrees, math symbols, Asian scripts, and emojis."""
        recipe = RecipeSchema(
            title="Japanese 抹茶 (Matcha) Soufflé Pancake 🥞 & Crème 🍨",
            description="Ultra-fluffy soufflé pancakes with 2½ tsp Uji matcha (宇治抹茶) & organic honey 🍯. Serve with crème fraîche.",
            yield_servings="4 pancakes 🥞",
            prep_time="20 mins ⏱️",
            cook_time="15 mins 🍳",
            total_time="35 mins ⌛",
            ingredients=[
                "2 large eggs 🥚 (yolks & whites separated)",
                "2½ tbsp whole milk 🥛",
                "½ tsp pure vanilla extract",
                "⅓ cup cake flour (薄力粉)",
                "1½ tsp organic Uji matcha powder 🍵",
                "2¼ tbsp granulated sugar",
                "⅛ tsp cream of tartar",
                "1 tbsp unsalted butter 🧈 for pan",
            ],
            directions=[
                "1. Whisk egg yolks, milk, and vanilla until pale. Sift in cake flour and matcha powder (抹茶).",
                "2. In clean metal bowl, beat egg whites with cream of tartar; gradually add sugar until stiff, glossy peaks form (meringue ☁️).",
                "3. Gently fold ⅓ of meringue into matcha batter, then fold remaining meringue in 2 batches (preserve aeration!).",
                "4. Cook in covered non-stick pan on lowest heat at ~300°F (150°C) with 1 tsp water for steam, 5-6 mins each side.",
                "5. Stack high, dust with powdered sugar ❄️, and top with butter 🧈 & honey 🍯.",
            ],
            tags=["Breakfast 🥞", "Japanese 🇯🇵", "Matcha 🍵", "Soufflé ☁️", "Dessert 🍰"],
            source_notes="Tokyo Café Recipe (東京カフェノート) — Chef Kenji 👨‍🍳 (2024)",
        )

        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string([recipe])
        csv_bytes = exporter.export_to_bytes([recipe])

        # Verify bytes decode cleanly as UTF-8
        decoded = csv_bytes.decode("utf-8")
        assert decoded == csv_str
        assert "🥞" in decoded
        assert "宇治抹茶" in decoded
        assert "2½ tsp" in decoded
        assert "300°F (150°C)" in decoded

        # DictReader verification
        dict_reader = csv.DictReader(io.StringIO(csv_str))
        row = next(dict_reader)

        assert row["Title"] == "Japanese 抹茶 (Matcha) Soufflé Pancake 🥞 & Crème 🍨"
        assert "2½ tsp Uji matcha (宇治抹茶)" in row["Description"]
        assert "2 large eggs 🥚 (yolks & whites separated)" in row["Ingredients"]
        assert "5. Stack high, dust with powdered sugar ❄️" in row["Directions"]
        assert row["Tags"] == "Breakfast 🥞, Japanese 🇯🇵, Matcha 🍵, Soufflé ☁️, Dessert 🍰"
        assert row["Source"] == "Tokyo Café Recipe (東京カフェノート) — Chef Kenji 👨‍🍳 (2024)"

    def test_csv_embedded_linebreaks_in_description_and_source(self):
        """Tests embedded multi-line paragraphs in Description and Source notes."""
        desc = "Paragraph 1: Historical background of this vintage 1920s dessert.\n\nParagraph 2: Requires slow baking and thorough chilling overnight.\n\nParagraph 3: Best enjoyed during the winter holidays."
        source = "Grandma's Leather Notebook\nPage 42, Entry #3\nHandwritten in Blue Ink"

        recipe = RecipeSchema(
            title="Vintage Victorian Fruitcake",
            description=desc,
            ingredients=["1 lb dried mixed fruits", "2 cups flour", "1 cup dark rum"],
            directions=["1. Soak fruits in rum overnight.", "2. Mix batter and bake 2 hours at 300°F."],
            source_notes=source,
        )

        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string([recipe])

        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 2
        assert len(rows[1]) == 9

        dict_reader = csv.DictReader(io.StringIO(csv_str))
        row = next(dict_reader)
        assert row["Description"] == desc
        assert row["Source"] == source

    def test_csv_delimiter_and_formula_injection_defense(self):
        """Tests characters like '=', '+', '-', '@', and commas inside strings do not alter CSV structure."""
        recipe = RecipeSchema(
            title="=SUM(1+1) Risky Formula Recipe, Deluxe",
            description="Testing formula injection prefix @SUM and +cmd|' /C calc'!A0",
            ingredients=["-1 cup sugar", "+2 tbsp cinnamon", "=3 eggs", "@pinch of salt"],
            directions=["1. Execute steps cleanly without formula interpretation.", "2. Cool down."],
            tags=["=Test", "+Tag", "-MinusTag"],
            source_notes="@DangerousNotes",
        )

        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string([recipe])

        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 2
        assert len(rows[1]) == 9
        assert rows[1][0] == "=SUM(1+1) Risky Formula Recipe, Deluxe"

        dict_reader = csv.DictReader(io.StringIO(csv_str))
        row = next(dict_reader)
        assert row["Title"] == "=SUM(1+1) Risky Formula Recipe, Deluxe"
        assert "-1 cup sugar\n+2 tbsp cinnamon\n=3 eggs\n@pinch of salt" == row["Ingredients"]

    def test_csv_multi_recipe_batch_alignment(self):
        """Tests serializing 20 distinct edge-case recipes into a single CSV; verifies 100% row alignment."""
        recipes = []
        for i in range(20):
            recipes.append(
                RecipeSchema(
                    title=f"Recipe #{i:02d}, Title with 'Quotes' & Commas",
                    description=f"Description for recipe {i} with\nline break and special characters: ½, ¼, °F, 🍋",
                    ingredients=[f"Ingredient {j} for #{i}" for j in range(1, 6)],
                    directions=[f"Step {j} for #{i}" for j in range(1, 4)],
                    prep_time=f"{i * 5} mins",
                    cook_time=f"{i * 10} mins",
                    total_time=f"{i * 15} mins",
                    yield_servings=f"{i + 2} servings",
                    tags=[f"Tag{i}A", f"Tag{i}B, Subtag"],
                    source_notes=f"Source Box #{i}",
                )
            )

        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string(recipes)

        # Validate with PlanToEatExporter validator
        assert exporter.validate_csv(csv_str) is True

        # Parse with standard csv.reader
        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 21  # 1 header + 20 data rows
        for idx, row in enumerate(rows[1:], start=0):
            assert len(row) == 9, f"Row {idx} did not have 9 columns!"
            assert row[0] == f"Recipe #{idx:02d}, Title with 'Quotes' & Commas"

        # Parse with standard csv.DictReader
        dict_reader = csv.DictReader(io.StringIO(csv_str))
        dict_rows = list(dict_reader)
        assert len(dict_rows) == 20
        for idx, r in enumerate(dict_rows):
            assert r["Title"] == f"Recipe #{idx:02d}, Title with 'Quotes' & Commas"
            assert r["Prep Time"] == f"{idx * 5} mins"
            assert len(r["Ingredients"].split("\n")) == 5
            assert len(r["Directions"].split("\n")) == 3

    def test_csv_extreme_multilingual_rtl_and_zero_width_characters(self):
        """Tests Right-to-Left (Arabic/Hebrew), Cyrillic, Greek, Chinese, and zero-width characters."""
        recipe = RecipeSchema(
            title="Международный Десерт / حلوى عالمية / 经典甜点 / Επιδόρπιο 🍰",
            description="Multi-script description: العربية, Русский, 中文, Ελληνικά, with zero-width space \u200b & LTR/RTL marks \u200e\u200f.",
            ingredients=[
                "1 cup دقيق القمح (wheat flour)",
                "200g свежая сметана (sour cream)",
                "100g 细砂糖 (fine granulated sugar)",
                "½ tsp κρόκος Κοζάνης (Greek saffron)",
            ],
            directions=[
                "1. Смешать все ингредиенты в глубокой миске.",
                "2. اخلط المكونات جيداً حتى تصبح ناعمة.",
                "3. 将面糊倒入烤盘并在 180°C 下烘烤 25 分钟。",
                "4. Ψήστε μέχρι να ροδίσει ελαφρά.",
            ],
            tags=["International 🌐", "العربية", "Русский", "中文", "Ελληνικά"],
            source_notes="Global Culinary Archive \u200b (المطبخ العالمي)",
        )

        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string([recipe])
        assert exporter.validate_csv(csv_str) is True

        dict_reader = csv.DictReader(io.StringIO(csv_str))
        row = next(dict_reader)

        assert row["Title"] == "Международный Десерт / حلوى عالمية / 经典甜点 / Επιδόρπιο 🍰"
        assert "\u200b" in row["Description"]
        assert "دقيق القمح" in row["Ingredients"]
        assert "Смешать все" in row["Directions"]
        assert "العربية" in row["Tags"]

    def test_csv_mixed_line_endings_in_cells(self):
        """Tests cells with internal \\r, \\n, and \\r\\n; verifies row structure is strictly preserved."""
        desc_mixed = "Line 1 with LF\nLine 2 with CRLF\r\nLine 3 with CR\rLine 4 final."
        recipe = RecipeSchema(
            title="Mixed Line Endings Recipe",
            description=desc_mixed,
            ingredients=["1 cup milk", "2 cups sugar"],
            directions=["1. Step one with internal quote \"mix\".", "2. Step two."],
        )

        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string([recipe])

        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 2
        assert len(rows[1]) == 9

    def test_csv_empty_ingredients_and_directions_lists(self):
        """Tests recipe with empty ingredients and directions lists produces valid 9-column CSV row."""
        recipe = RecipeSchema(
            title="Title Only Recipe",
            ingredients=[],
            directions=[],
        )

        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string([recipe])

        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 2
        assert len(rows[1]) == 9
        assert rows[1][0] == "Title Only Recipe"
        assert rows[1][2] == ""  # Ingredients
        assert rows[1][3] == ""  # Directions

    def test_csv_export_to_path_with_spaces_and_unicode(self, tmp_path):
        """Tests exporting to a nested destination path with spaces and Unicode directory names."""
        recipe = RecipeSchema(
            title="Exported to Unicode Path",
            ingredients=["1 tbsp olive oil", "1 clove garlic"],
            directions=["1. Sauté garlic in oil."],
        )

        exporter = PlanToEatExporter()
        out_path = tmp_path / "📁 Recettes 2026" / "Édition Française" / "mes_recettes.csv"

        resolved = exporter.export_to_file([recipe], out_path)
        assert resolved.exists()
        assert resolved.is_file()

        content = resolved.read_text(encoding="utf-8")
        assert exporter.validate_csv(content) is True

    def test_csv_large_text_fields_no_buffer_overflow(self):
        """Tests massive text blocks (50KB+) in description and source notes."""
        large_desc = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 500  # ~28KB
        large_notes = "Special vintage archiving notes and historical lineage details. " * 500

        recipe = RecipeSchema(
            title="Large Payload Recipe",
            description=large_desc,
            ingredients=["1 cup flour"] * 50,
            directions=[f"Step {i} with large content" for i in range(1, 51)],
            source_notes=large_notes,
        )

        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string([recipe])

        assert len(csv_str) > 50000
        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 2
        assert len(rows[1]) == 9

        dict_reader = csv.DictReader(io.StringIO(csv_str))
        row = next(dict_reader)
        assert row["Description"] == large_desc
        assert row["Source"] == large_notes


# ============================================================================
# 3. End-to-End Orchestrator, Session & FastAPI REST Endpoint Integration
# ============================================================================


class CallOrderMockClient:
    """Mock client keyed strictly by call order."""

    def __init__(self, responses: List[RecipeSchema]):
        self._responses = list(responses)
        self._index = 0

    def __call__(self, *args, **kwargs) -> RecipeSchema:
        resp = self._responses[self._index]
        self._index += 1
        return resp


class TestMilestone3Milestone4Integration:
    """Tests the interaction between PipelineSession, Recipe Extraction, and CSV Export."""

    @pytest.mark.asyncio
    async def test_session_lifecycle_extraction_and_csv_generation(self, tmp_path):
        """Simulates full session lifecycle through HITL approval to CSV export."""
        export_dir = tmp_path / "exports"
        settings = MagicMock()
        settings.export_dir = export_dir
        settings.ensure_directories = MagicMock()

        fudge_img = tmp_path / "card_fudge.jpg"
        pecan_img = tmp_path / "card_pecan.jpg"
        fudge_img.write_bytes(TINY_JPEG)
        pecan_img.write_bytes(TINY_JPEG)

        session = PipelineSession(session_id="sess_test_m3_m4")
        session.fingerprints = {
            "card_fudge.jpg": VisualFingerprint(
                card_id="card_fudge.jpg",
                image_path=str(fudge_img),
                sha256_hash="hash1",
                role=CardRole.STANDALONE,
            ),
            "card_pecan.jpg": VisualFingerprint(
                card_id="card_pecan.jpg",
                image_path=str(pecan_img),
                sha256_hash="hash2",
                role=CardRole.STANDALONE,
            ),
        }

        # Simulate approvals
        session.approvals = [
            CardPairApproval(
                proposal_id="prop_1",
                front_card_id="card_fudge.jpg",
                back_card_id=None,
                decision=HITLDecision.APPROVE,
            ),
            CardPairApproval(
                proposal_id="prop_2",
                front_card_id="card_pecan.jpg",
                back_card_id=None,
                decision=HITLDecision.APPROVE,
            ),
        ]

        recipe_fudge = RecipeSchema(
            title="10-Minute Quick Chocolate Fudge",
            ingredients=["2 cups sugar", "1/2 cup cocoa"],
            directions=["Cook and cool."],
        )
        recipe_pecan = RecipeSchema(
            title="Southern Pecan Pie",
            ingredients=["1 cup pecans", "1 cup corn syrup"],
            directions=["Bake in pie shell."],
        )
        client = ScriptedLLMClient([recipe_fudge, recipe_pecan])
        extractor = GeminiRecipeExtractor(llm_client=client)
        exporter = PlanToEatExporter()

        # Extract recipes
        recipes = []
        for approval in session.approvals:
            front_fp = session.fingerprints[approval.front_card_id]
            recipe = await extractor.extract(front_fp)
            recipes.append(recipe)

        session.recipes = recipes
        assert len(session.recipes) == 2

        # Serialize CSV
        csv_text = exporter.export_to_csv_string(session.recipes)
        session.csv_content = csv_text
        session.state = PipelineState.COMPLETED

        assert exporter.validate_csv(session.csv_content) is True

        # Test DictReader on session CSV
        reader = csv.DictReader(io.StringIO(session.csv_content))
        rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["Title"] == "10-Minute Quick Chocolate Fudge"
        assert rows[1]["Title"] == "Southern Pecan Pie"

    def test_fastapi_export_csv_endpoint(self):
        """Tests FastAPI GET /api/export/csv endpoint on completed session with complex recipes."""
        app = create_app()
        manager = SessionManager()
        app.state.session_manager = manager

        session = manager.create_session(session_id="sess_export_test")
        recipe1 = RecipeSchema(
            title="Tested Endpoint Cookie, Deluxe",
            description="Crisp & chewy ½ tsp vanilla 🍪",
            ingredients=["2 cups flour", "1 cup chocolate chips 🍫"],
            directions=["1. Preheat oven to 350°F.", "2. Bake 10 mins."],
            tags=["Cookies", "Dessert 🍪"],
            source_notes="Aunt Clara's Notes",
        )
        recipe2 = RecipeSchema(
            title='Baker\'s "Special" Brownie',
            description='Baked in 8" x 8" square pan.',
            ingredients=['1 cup "Ghirardelli" cocoa', "2 eggs"],
            directions=['1. Mix and bake at 325°F.'],
            tags=['"Brownie"', "Chocolate"],
            source_notes="Cookbook Page 12",
        )

        exporter = PlanToEatExporter()
        csv_text = exporter.export_to_csv_string([recipe1, recipe2])
        session.recipes = [recipe1, recipe2]
        session.csv_content = csv_text
        session.state = PipelineState.COMPLETED

        client = TestClient(app)

        # Query GET /api/export/csv
        resp = client.get("/api/export/csv?session_id=sess_export_test")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        assert 'attachment; filename="recipeboxer_export.csv"' in resp.headers["content-disposition"]

        # Parse returned CSV content with DictReader
        csv_content = resp.text
        assert exporter.validate_csv(csv_content) is True

        dict_reader = csv.DictReader(io.StringIO(csv_content))
        rows = list(dict_reader)
        assert len(rows) == 2
        assert rows[0]["Title"] == "Tested Endpoint Cookie, Deluxe"
        assert "½ tsp vanilla 🍪" in rows[0]["Description"]
        assert rows[1]["Title"] == 'Baker\'s "Special" Brownie'
        assert 'Baked in 8" x 8" square pan.' in rows[1]["Description"]

    def test_fastapi_export_csv_endpoint_incomplete_pipeline_returns_400(self):
        """Tests that GET /api/export/csv returns HTTP 400 when session is in progress / not completed."""
        app = create_app()
        manager = SessionManager()
        app.state.session_manager = manager

        session = manager.create_session(session_id="sess_incomplete")
        session.state = PipelineState.EXTRACTING

        client = TestClient(app)
        resp = client.get("/api/export/csv?session_id=sess_incomplete")
        assert resp.status_code == 400
        assert "CSV export not ready" in resp.json()["detail"]

    def test_fastapi_export_csv_endpoint_invalid_session_returns_404(self):
        """Tests that GET /api/export/csv returns HTTP 404 for unknown session ID."""
        app = create_app()
        manager = SessionManager()
        app.state.session_manager = manager

        client = TestClient(app)
        resp = client.get("/api/export/csv?session_id=nonexistent_session")
        assert resp.status_code == 404
        assert "Session not found" in resp.json()["detail"]
