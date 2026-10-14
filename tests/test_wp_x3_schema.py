"""Unit tests for WP-X3 Recipe Schema v2."""

from __future__ import annotations

import json
import pytest
from pydantic import ValidationError

from recipeboxer.models.recipe import (
    IllegibleSpan,
    IngredientLine,
    Legibility,
    Provenance,
    Recipe,
    RecipeSection,
    RecipeWarning,
    RecipeSchema,
    Step,
    TextSpan,
    TimeValue,
    YieldValue,
)
from recipeboxer.exporter.json_export import JsonExporter


def test_schema_v2_fictitious_pie_construction():
    """Constructs the canonical fictitious Grandma's Strawberry Rhubarb Pie with 2 sections."""
    pie = Recipe(
        recipe_id="rec_pie_001",
        title="Grandma's Strawberry Rhubarb Pie",
        description="A traditional double-crust summer fruit pie with tender lattice top.",
        confidence=0.88,
        prep_time=TimeValue(original_text="30 mins", minutes=30, normalized_iso="PT30M"),
        cook_time=TimeValue(original_text="50 mins", minutes=50, normalized_iso="PT50M"),
        total_time=TimeValue(original_text="1 hr 20 mins", minutes=80, normalized_iso="PT1H20M"),
        yield_value=YieldValue(original_text="1 9-inch pie (8 slices)", normalized_servings=8.0, unit="slices"),
        sections=[
            RecipeSection(
                name="Crust",
                ingredients=[
                    IngredientLine(
                        original_text="2 1/2 cups all-purpose flour",
                        quantity="2 1/2",
                        unit="cups",
                        item="all-purpose flour",
                        confidence=0.98,
                        source_face=0,
                        legibility=Legibility.CLEAR,
                    ),
                    IngredientLine(
                        original_text="1 cup cold unsalted butter, cubed",
                        quantity="1",
                        unit="cup",
                        item="cold unsalted butter",
                        preparation="cubed",
                        confidence=0.95,
                        source_face=0,
                        legibility=Legibility.CLEAR,
                    ),
                    IngredientLine(
                        original_text="1/2 tsp [illegible] salt",
                        normalized_text="1/2 tsp sea salt",
                        quantity="1/2",
                        unit="tsp",
                        item="sea salt",
                        confidence=0.72,
                        source_face=0,
                        legibility=Legibility.UNCERTAIN,
                        illegible_spans=[IllegibleSpan(start=8, end=19, reason="water stain on card")],
                    ),
                ],
                steps=[
                    Step(
                        original_text="Cut butter into flour and salt until pea-sized crumbles form.",
                        text="1. Cut butter into flour and salt until pea-sized crumbles form.",
                        confidence=0.96,
                        source_face=0,
                        legibility=Legibility.CLEAR,
                    ),
                    Step(
                        original_text="Add ice water gradually and chill dough for 1 hour.",
                        text="2. Add ice water gradually and chill dough for 1 hour.",
                        confidence=0.94,
                        source_face=0,
                        legibility=Legibility.CLEAR,
                    ),
                ],
            ),
            RecipeSection(
                name="Filling",
                ingredients=[
                    IngredientLine(
                        original_text="3 cups fresh strawberries, hulled and halved",
                        quantity="3",
                        unit="cups",
                        item="fresh strawberries",
                        preparation="hulled and halved",
                        confidence=0.95,
                        source_face=1,
                        legibility=Legibility.CLEAR,
                    ),
                    IngredientLine(
                        original_text="3 cups fresh rhubarb, sliced 1/2-inch thick",
                        quantity="3",
                        unit="cups",
                        item="fresh rhubarb",
                        preparation="sliced 1/2-inch thick",
                        confidence=0.92,
                        source_face=1,
                        legibility=Legibility.CLEAR,
                    ),
                    IngredientLine(
                        original_text="1 1/4 cups granulated sugar",
                        quantity="1 1/4",
                        unit="cups",
                        item="granulated sugar",
                        confidence=0.99,
                        source_face=1,
                        legibility=Legibility.CLEAR,
                    ),
                ],
                steps=[
                    Step(
                        original_text="Toss fruit with sugar and cornstarch in a large mixing bowl.",
                        text="3. Toss fruit with sugar and cornstarch in a large mixing bowl.",
                        confidence=0.95,
                        source_face=1,
                        legibility=Legibility.CLEAR,
                    ),
                    Step(
                        original_text="Fill pastry crust, top with lattice, and bake at 400F for 50 mins.",
                        text="4. Fill pastry crust, top with lattice, and bake at 400F for 50 mins.",
                        confidence=0.91,
                        source_face=1,
                        legibility=Legibility.CLEAR,
                    ),
                ],
            ),
        ],
        tags=["Dessert", "Pie", "Summer", "Strawberry", "Rhubarb"],
        source_notes="Grandma Clara's 1948 recipe box",
        warnings=[
            RecipeWarning(
                code="GROUNDING_UNCERTAIN",
                message="Salt type obscured by water damage; inferred as sea salt from family notes",
                field_path="sections[0].ingredients[2]",
            )
        ],
        provenance=Provenance(
            faces=["card_pie_front", "card_pie_back"],
            models=["gemini-2.5-flash"],
            cost=0.0035,
        ),
    )

    assert pie.title == "Grandma's Strawberry Rhubarb Pie"
    assert len(pie.sections) == 2
    assert len(pie.all_ingredients()) == 6
    assert len(pie.all_steps()) == 4
    assert pie.confidence == 0.88
    assert pie.flagged_count() > 0
    assert pie.needs_review() is True

    # Check display text fallback
    unc_ing = pie.sections[0].ingredients[2]
    assert unc_ing.display_text == "1/2 tsp sea salt"
    assert unc_ing.original_text == "1/2 tsp [illegible] salt"


def test_confidence_validation():
    """Confidence must strictly lie within [0.0, 1.0]."""
    # Valid bounds
    r0 = Recipe(title="Valid Low", confidence=0.0)
    assert r0.confidence == 0.0

    r1 = Recipe(title="Valid High", confidence=1.0)
    assert r1.confidence == 1.0

    # Invalid below 0.0
    with pytest.raises(ValidationError):
        Recipe(title="Negative Conf", confidence=-0.05)

    # Invalid above 1.0
    with pytest.raises(ValidationError):
        Recipe(title="Too High Conf", confidence=1.05)

    # In IngredientLine
    with pytest.raises(ValidationError):
        IngredientLine(original_text="Salt", confidence=-0.1)

    with pytest.raises(ValidationError):
        IngredientLine(original_text="Salt", confidence=1.1)

    # In Step
    with pytest.raises(ValidationError):
        Step(original_text="Mix", confidence=1.5)


def test_source_face_validation():
    """source_face must be >= 0."""
    ing = IngredientLine(original_text="Flour", source_face=0)
    assert ing.source_face == 0

    with pytest.raises(ValidationError):
        IngredientLine(original_text="Flour", source_face=-1)

    step = Step(original_text="Bake", source_face=2)
    assert step.source_face == 2

    with pytest.raises(ValidationError):
        Step(original_text="Bake", source_face=-3)


def test_title_validation():
    """Title must be a non-empty string."""
    with pytest.raises(ValidationError):
        Recipe(title="")

    with pytest.raises(ValidationError):
        Recipe(title="    ")

    with pytest.raises(ValidationError):
        Recipe(title="\n\t")

    r = Recipe(title="  Apple Pie  ")
    assert r.title == "Apple Pie"


def test_tag_cleaning_and_deduplication():
    """Tags are trimmed and deduplicated case-insensitively."""
    r = Recipe(
        title="Tag Test",
        tags=["Dessert", "  dessert ", "baking", "BAKING", "pie", ""],
    )
    assert r.tags == ["Dessert", "baking", "pie"]


def test_to_scoring_dict_shape():
    """Verify to_scoring_dict returns the exact shape required by evals/scoring."""
    recipe = Recipe(
        title="Test Scoring Recipe",
        yield_value=YieldValue(original_text="4 servings"),
        sections=[
            RecipeSection(
                name="Main",
                ingredients=[
                    IngredientLine(
                        original_text="2 cups flour",
                        quantity="2",
                        unit="cups",
                        item="flour",
                    ),
                    IngredientLine(
                        original_text="1 tsp salt",
                        quantity="1",
                        unit="tsp",
                        item="salt",
                    ),
                ],
                steps=[
                    Step(original_text="Mix ingredients.", text="1. Mix ingredients."),
                    Step(original_text="Bake at 350F.", text="2. Bake at 350F."),
                ],
            )
        ],
        notes=[TextSpan(text="Best served warm.")],
    )

    scoring_dict = recipe.to_scoring_dict()
    assert isinstance(scoring_dict, dict)
    assert scoring_dict["title"] == "Test Scoring Recipe"
    assert scoring_dict["yield"] == "4 servings"
    assert len(scoring_dict["ingredients"]) == 2
    assert scoring_dict["ingredients"][0] == {
        "raw": "2 cups flour",
        "quantity": "2",
        "unit": "cups",
        "item": "flour",
        "section": "Main",
    }
    assert scoring_dict["directions"] == ["1. Mix ingredients.", "2. Bake at 350F."]
    assert scoring_dict["notes"] == ["Best served warm."]


def test_round_trip_json_serialization():
    """Tests round-trip JSON serialization and validation."""
    pie = Recipe(
        recipe_id="rec_json_001",
        title="Roundtrip Strawberry Pie",
        confidence=0.92,
        prep_time=TimeValue(original_text="25 mins", minutes=25, normalized_iso="PT25M"),
        yield_value=YieldValue(original_text="6 servings", normalized_servings=6.0),
        sections=[
            RecipeSection(
                name="Crust",
                ingredients=[
                    IngredientLine(original_text="2 cups flour", item="flour", quantity="2", unit="cups")
                ],
                steps=[
                    Step(original_text="Roll dough.", text="1. Roll dough.")
                ],
            )
        ],
        tags=["Dessert", "Summer"],
        warnings=[RecipeWarning(code="NOTE", message="Family heirloom")],
    )

    # 1. Via Pydantic JSON dump/validate
    json_bytes = pie.model_dump_json()
    reloaded = Recipe.model_validate_json(json_bytes)
    assert reloaded.recipe_id == pie.recipe_id
    assert reloaded.title == pie.title
    assert reloaded.confidence == pie.confidence
    assert len(reloaded.sections) == 1
    assert reloaded.sections[0].ingredients[0].display_text == "2 cups flour"

    # 2. Via JsonExporter
    exporter = JsonExporter()
    exported_str = exporter.export_to_json_string(pie)
    assert exporter.validate_json_export(exported_str) is True


def test_backward_compatibility_v1_coercion():
    """Tests that v1 legacy flat strings for ingredients, directions, and times coerce gracefully."""
    v1_dict = {
        "recipe_id": "rec_v1",
        "title": "Legacy Coercion Cake",
        "ingredients": ["1 cup sugar", "2 cups flour", "1/2 cup butter"],
        "directions": ["Preheat oven to 350F.", "Mix all ingredients.", "Bake for 30 mins."],
        "prep_time": "15 mins",
        "cook_time": "30 mins",
        "total_time": "45 mins",
        "yield_servings": "8 servings",
        "tags": ["Dessert", "Cake"],
    }

    recipe = Recipe.model_validate(v1_dict)
    assert recipe.title == "Legacy Coercion Cake"
    assert len(recipe.sections) == 1
    assert len(recipe.all_ingredients()) == 3
    assert len(recipe.all_steps()) == 3
    assert recipe.ingredients == ["1 cup sugar", "2 cups flour", "1/2 cup butter"]
    assert recipe.directions == ["1. Preheat oven to 350F.", "2. Mix all ingredients.", "3. Bake for 30 mins."]
    assert recipe.prep_time == "15 mins"
    assert recipe.yield_servings == "8 servings"
