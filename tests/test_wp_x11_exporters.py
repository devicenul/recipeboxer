"""Unit tests for WP-X11 Exporters to Recipe Schema v2."""

from __future__ import annotations

import csv
import io
import json
import zipfile
import pytest

from recipeboxer.models.recipe import (
    IllegibleSpan,
    IngredientLine,
    Legibility,
    Provenance,
    Recipe,
    RecipeSection,
    RecipeWarning,
    Step,
    TextSpan,
    TimeValue,
    YieldValue,
)
from recipeboxer.exporter.markdown_export import (
    MarkdownExporter,
    export_recipes_to_markdown,
)
from recipeboxer.exporter.docx_export import (
    DocxExporter,
    export_recipes_to_docx,
)
from recipeboxer.exporter.pdf_export import (
    PdfExporter,
    export_recipes_to_pdf,
)
from recipeboxer.exporter.google_docs import (
    GoogleDocsExporter,
    export_recipes_to_google_docs_batch_requests,
    export_recipes_to_google_docs_html,
    export_recipes_to_google_docs_json,
)
from recipeboxer.exporter.plan_to_eat import (
    PlanToEatExporter,
    export_recipes_to_plan_to_eat_csv,
)
from recipeboxer.exporter.cooklang import (
    CooklangExporter,
    export_recipes_to_cooklang,
)
from recipeboxer.exporter.json_ld import (
    JsonLdExporter,
    export_recipes_to_json_ld,
)
from recipeboxer.exporter.json_export import (
    JsonExporter,
    export_recipes_to_json,
)


@pytest.fixture
def fictitious_pie() -> Recipe:
    """Returns canonical hand-written Grandma's Strawberry Rhubarb Pie per task specification."""
    return Recipe(
        recipe_id="rec_pie_canonical",
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
                        original_text="1 cup cold butter, cubed",
                        quantity="1",
                        unit="cup",
                        item="cold butter",
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


def test_markdown_exporter_v2(fictitious_pie: Recipe):
    """Verifies Markdown export retains sections, text, times, and transcription notes when enabled."""
    # Standard export (notes disabled)
    md_clean = export_recipes_to_markdown([fictitious_pie], include_transcription_notes=False)
    assert "# Grandma's Strawberry Rhubarb Pie" in md_clean
    assert "### Crust" in md_clean
    assert "### Filling" in md_clean
    assert "2 1/2 cups all-purpose flour" in md_clean
    assert "1/2 tsp sea salt" in md_clean  # uses display_text (normalized_text)
    assert "## Transcription Notes" not in md_clean

    # Export with transcription notes enabled
    md_with_notes = export_recipes_to_markdown([fictitious_pie], include_transcription_notes=True)
    assert "## Transcription Notes & Verification" in md_with_notes
    assert "Salt type obscured by water damage" in md_with_notes
    assert "UNCERTAIN" in md_with_notes or "uncertain" in md_with_notes.lower()


def test_docx_exporter_v2(fictitious_pie: Recipe):
    """Verifies DOCX export produces valid OpenXML, section titles, and transcription notes."""
    docx_bytes = export_recipes_to_docx(fictitious_pie, include_transcription_notes=True)
    assert len(docx_bytes) > 0

    with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as zf:
        doc_xml = zf.read("word/document.xml").decode("utf-8")
        assert "Grandma&apos;s Strawberry Rhubarb Pie" in doc_xml or "Strawberry Rhubarb Pie" in doc_xml
        assert "Crust" in doc_xml
        assert "Filling" in doc_xml
        assert "all-purpose flour" in doc_xml
        assert "Transcription Notes" in doc_xml
        assert "Salt type obscured by water damage" in doc_xml


def test_pdf_exporter_v2(fictitious_pie: Recipe):
    """Verifies PDF export renders multi-section recipe without error and includes transcription notes."""
    exporter = PdfExporter(include_transcription_notes=True)
    pdf_bytes = exporter.export_to_bytes(fictitious_pie)
    assert pdf_bytes.startswith(b"%PDF-1.4")
    assert b"%%EOF" in pdf_bytes
    assert len(pdf_bytes) > 1000

    # Also test with include_transcription_notes=False
    pdf_clean = PdfExporter(include_transcription_notes=False).export_to_bytes(fictitious_pie)
    assert pdf_clean.startswith(b"%PDF-1.4")


def test_google_docs_exporter_v2(fictitious_pie: Recipe):
    """Verifies Google Docs batchUpdate, HTML, and JSON representations retain v2 metadata and sections."""
    gdocs = GoogleDocsExporter(include_transcription_notes=True)

    # 1. Batch update requests
    requests = gdocs.build_batch_update_requests(fictitious_pie)
    assert len(requests) > 0
    all_inserted_text = "".join(
        req["insertText"]["text"] for req in requests if "insertText" in req
    )
    assert "Grandma's Strawberry Rhubarb Pie" in all_inserted_text
    assert "Crust" in all_inserted_text
    assert "Filling" in all_inserted_text
    assert "Transcription Notes" in all_inserted_text
    assert "Salt type obscured by water damage" in all_inserted_text

    # 2. HTML archive export
    html_out = gdocs.export_to_html(fictitious_pie)
    assert "Grandma&#x27;s Strawberry Rhubarb Pie" in html_out or "Grandma's Strawberry Rhubarb Pie" in html_out
    assert "Crust" in html_out
    assert "Filling" in html_out
    assert "Transcription Notes &amp; Verification" in html_out

    # 3. JSON interchange export
    json_str = gdocs.export_to_json_string(fictitious_pie)
    data = json.loads(json_str)
    assert data["recipe_count"] == 1
    assert data["recipes"][0]["title"] == fictitious_pie.title


def test_plan_to_eat_exporter_v2(fictitious_pie: Recipe):
    """Verifies Plan to Eat CSV formats nested sections, times, and display text properly."""
    csv_str = export_recipes_to_plan_to_eat_csv([fictitious_pie], include_transcription_notes=True)
    reader = csv.DictReader(io.StringIO(csv_str))
    rows = list(reader)
    assert len(rows) == 1
    row = rows[0]

    assert row["Title"] == "Grandma's Strawberry Rhubarb Pie"
    assert "2 1/2 cups all-purpose flour" in row["Ingredients"]
    assert "1/2 tsp sea salt" in row["Ingredients"]
    assert "Cut butter into flour" in row["Directions"]
    assert row["Prep Time"] == "30 mins"
    assert row["Total Time"] == "1 hr 20 mins"
    assert "Salt type obscured" in row["Description"]


def test_cooklang_exporter_v2(fictitious_pie: Recipe):
    """Verifies Cooklang exporter produces valid ingredient markup, directions, and warning metadata."""
    cook_text = export_recipes_to_cooklang(fictitious_pie, include_transcription_notes=True)
    assert ">> title: Grandma's Strawberry Rhubarb Pie" in cook_text
    assert ">> prep time: 30 mins" in cook_text
    assert ">> cook time: 50 mins" in cook_text
    assert "Salt type obscured" in cook_text
    assert "flour" in cook_text
    assert "Cut butter into flour" in cook_text


def test_json_ld_exporter_v2(fictitious_pie: Recipe):
    """Verifies JSON-LD export emits Schema.org structure with ISO 8601 durations and transcription notes."""
    ld_str = export_recipes_to_json_ld([fictitious_pie], include_transcription_notes=True)
    data = json.loads(ld_str)
    recipe_ld = data[0] if isinstance(data, list) else data

    assert recipe_ld["@type"] == "Recipe"
    assert recipe_ld["name"] == "Grandma's Strawberry Rhubarb Pie"
    assert recipe_ld["prepTime"] == "PT30M"
    assert recipe_ld["cookTime"] == "PT50M"
    assert recipe_ld["totalTime"] == "PT1H20M"
    assert len(recipe_ld["recipeIngredient"]) == 6
    assert any("sea salt" in ing for ing in recipe_ld["recipeIngredient"])
    assert "Salt type obscured by water damage" in recipe_ld["comment"]


def test_json_exporter_v2(fictitious_pie: Recipe):
    """Verifies lossless round-trip JSON export for Schema v2."""
    exporter = JsonExporter()
    exported = exporter.export_to_json_string(fictitious_pie)
    assert exporter.validate_json_export(exported) is True

    parsed = json.loads(exported)
    reconstructed = Recipe.model_validate(parsed)
    assert reconstructed.title == fictitious_pie.title
    assert len(reconstructed.sections) == 2
    assert reconstructed.confidence == 0.88
