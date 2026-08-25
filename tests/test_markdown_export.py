"""Tests for Obsidian Markdown Exporter (recipeboxer/exporter/markdown_export.py)."""

from pathlib import Path
import pytest
import yaml

from recipeboxer.exporter.markdown_export import (
    MarkdownExporter,
    export_recipes_to_markdown,
)
from recipeboxer.models.recipe import RecipeSchema


@pytest.fixture
def sample_recipes() -> list[RecipeSchema]:
    """Sample recipes for Markdown export tests."""
    r1 = RecipeSchema(
        recipe_id="rec_001",
        title="Aunt Clara's Peach Cobbler",
        description="Fresh summer peaches baked under a sweet buttery biscuit crust.",
        yield_servings="8 servings",
        prep_time="20 mins",
        cook_time="40 mins",
        total_time="1 hr",
        ingredients=[
            "6 cups fresh sliced peaches",
            "1 cup granulated sugar",
            "1 cup all-purpose flour",
            "1 tsp baking powder",
            "1/2 cup cold butter",
            "1 large egg",
        ],
        directions=[
            "1. Preheat oven to 375°F.",
            "2. Spread sliced peaches in a greased baking dish.",
            "3. Mix flour, sugar, baking powder, egg, and butter into coarse crumbs.",
            "4. Scatter topping over peaches and bake for 40 minutes.",
        ],
        tags=["Dessert", "Southern", "Fruit"],
        source_notes="Aunt Clara's handwritten recipe card (1962)",
        card_ids=["card_peach_front", "card_peach_back"],
    )

    r2 = RecipeSchema(
        recipe_id="rec_002",
        title="Quick Guacamole",
        description="Classic fresh avocado dip.",
        yield_servings="4 servings",
        prep_time="10 mins",
        cook_time="0 mins",
        total_time="10 mins",
        ingredients=[
            "3 ripe avocados, pitted",
            "1 lime, juiced",
            "1/2 tsp kosher salt",
            "1/4 cup diced red onion",
            "2 tbsp chopped cilantro",
        ],
        directions=[
            "1. Mash avocados coarsely with lime juice and salt.",
            "2. Fold in red onion and fresh cilantro.",
        ],
        tags=["Appetizer", "Mexican", "Quick"],
        source_notes="Recipe clipping",
        card_ids=["card_guac_front"],
    )
    return [r1, r2]


class TestMarkdownExporter:
    """Test suite for Markdown & YAML frontmatter exporter."""

    def test_yaml_frontmatter_parsing(self, sample_recipes: list[RecipeSchema]):
        exporter = MarkdownExporter()
        md_str = exporter.export_single_recipe(sample_recipes[0])

        assert md_str.startswith("---")
        parts = md_str.split("---", 2)
        assert len(parts) >= 3

        yaml_content = parts[1]
        fm = yaml.safe_load(yaml_content)

        assert fm["title"] == "Aunt Clara's Peach Cobbler"
        assert fm["yield"] == "8 servings"
        assert fm["prep_time"] == "20 mins"
        assert fm["cook_time"] == "40 mins"
        assert fm["total_time"] == "1 hr"
        assert fm["tags"] == ["Dessert", "Southern", "Fruit"]
        assert fm["card_ids"] == ["card_peach_front", "card_peach_back"]

    def test_markdown_content_and_headings(self, sample_recipes: list[RecipeSchema]):
        exporter = MarkdownExporter()
        md_str = exporter.export_single_recipe(sample_recipes[0], use_wikilinks=True)

        assert "# Aunt Clara's Peach Cobbler" in md_str
        assert "## Ingredients" in md_str
        assert "- 6 cups fresh sliced peaches" in md_str
        assert "## Directions" in md_str
        assert "1. Preheat oven to 375°F." in md_str
        assert "## Notes & Source" in md_str
        assert "Aunt Clara's handwritten recipe card" in md_str
        assert "## Archival Card Scans" in md_str
        assert "![[card_peach_front.jpg]]" in md_str
        assert "![[card_peach_back.jpg]]" in md_str

    def test_standard_markdown_image_links(self, sample_recipes: list[RecipeSchema]):
        exporter = MarkdownExporter()
        md_str = exporter.export_single_recipe(sample_recipes[0], use_wikilinks=False)

        assert "![Archival Card Scan](card_peach_front)" in md_str

    def test_multiple_recipes_export(self, sample_recipes: list[RecipeSchema]):
        exporter = MarkdownExporter()
        multi_md = exporter.export_to_markdown_string(sample_recipes)

        assert "Aunt Clara's Peach Cobbler" in multi_md
        assert "Quick Guacamole" in multi_md
        assert "\n\n---\n\n" in multi_md

    def test_markdown_file_export(self, sample_recipes: list[RecipeSchema], tmp_path: Path):
        exporter = MarkdownExporter()
        out_file = tmp_path / "recipes.md"
        path = exporter.export_to_file(sample_recipes, out_file)

        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "Aunt Clara's Peach Cobbler" in content

    def test_markdown_card_id_fallback_when_resolver_returns_none(self, sample_recipes: list[RecipeSchema]):
        """Adversarial check: If image_resolver returns None, falls back to raw card_ids."""
        exporter = MarkdownExporter()
        md_str = exporter.export_single_recipe(sample_recipes[0], image_resolver=lambda cid: None)
        assert "## Archival Card Scans" in md_str
        assert "![[card_peach_front.jpg]]" in md_str

    def test_markdown_empty_recipes_list(self):
        """Adversarial check: Empty list returns empty string."""
        exporter = MarkdownExporter()
        assert exporter.export_to_markdown_string([]) == ""

    def test_convenience_function(self, sample_recipes: list[RecipeSchema]):
        md_text = export_recipes_to_markdown(sample_recipes)
        assert "Aunt Clara's Peach Cobbler" in md_text
