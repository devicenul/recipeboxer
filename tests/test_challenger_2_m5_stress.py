"""Milestone 5 Empirical Verification & Stress Test Suite (Challenger 2).

Verifies Plan To Eat CSV formatting, RFC 4180 compliance, newline encoding,
cross-platform path sanitization, and edge case resilience.
"""

from __future__ import annotations

import csv
import io
import os
import tempfile
from pathlib import Path
import pytest

from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.exporter.plan_to_eat import (
    PlanToEatExporter,
    PLAN_TO_EAT_COLUMNS,
    export_recipes_to_plan_to_eat_csv,
)
from recipeboxer.api.routes import sanitize_export_filename


class TestPlanToEatCSVStress:
    """Stress tests for Plan To Eat CSV formatting and RFC 4180 compliance."""

    def test_rfc4180_crlf_and_quotes_handling(self) -> None:
        """Verify RFC 4180 compliant CRLF line terminators and quote escaping."""
        exporter = PlanToEatExporter()
        recipes = [
            RecipeSchema(
                title='Grandma\'s "Secret" Apple Pie',
                description='A decadent recipe with "quotes", commas, and\nnewlines across fields.',
                ingredients=[
                    '2 ½ cups "organic" flour',
                    '1 cup sugar, granulated',
                    '½ tsp cinnamon & nutmeg',
                ],
                directions=[
                    '1. Preheat oven to 350°F (175°C).\nPrepare baking dish.',
                    '2. Mix ingredients in a "large" bowl.',
                    '3. Bake for 45-50 mins until "golden brown".',
                ],
                prep_time="20 mins",
                total_time="1 hour 15 mins",
                yield_servings="8 slices",
                tags=['Dessert, Holiday', 'Family "Heirloom"', 'Baking'],
                source_notes='Card #101 "Vintage 1968"',
            ),
            RecipeSchema(
                title="Japanese Matcha Roll Cake 抹茶ロールケーキ",
                description="Light sponge with green tea cream 🍵.",
                ingredients=["3 eggs", "50g sugar", "15g 抹茶"],
                directions=["1. Beat egg whites.", "2. Roll carefully with cream."],
                prep_time="30m",
                total_time="45m",
                yield_servings="6 slices",
                tags=["Japanese", "Dessert", "Matcha"],
                source_notes="Tokyo scans 2024",
            ),
            RecipeSchema(
                title="Untitled Minimal Recipe",
                description=None,
                ingredients=[],
                directions=[],
                prep_time=None,
                total_time=None,
                yield_servings=None,
                tags=[],
                source_notes=None,
            ),
        ]

        csv_str = exporter.export_to_csv_string(recipes)
        assert exporter.validate_csv_format(csv_str) is True

        # Parse with standard RFC 4180 CSV parser
        rows = list(csv.reader(io.StringIO(csv_str)))
        assert len(rows) == 4
        assert rows[0] == PLAN_TO_EAT_COLUMNS

        # Row 1 check
        r1 = rows[1]
        assert r1[0] == 'Grandma\'s "Secret" Apple Pie'
        assert '"quotes"' in r1[1]
        assert '2 ½ cups "organic" flour' in r1[2]
        assert '1 cup sugar, granulated' in r1[2]
        assert '1. Preheat oven to 350°F (175°C).\nPrepare baking dish.' in r1[3]
        assert r1[7] == 'Dessert, Holiday, Family "Heirloom", Baking'

        # Row 2 check (Unicode)
        r2 = rows[2]
        assert r2[0] == "Japanese Matcha Roll Cake 抹茶ロールケーキ"
        assert "🍵" in r2[1]
        assert "15g 抹茶" in r2[2]

        # Row 3 check (Minimal recipe)
        r3 = rows[3]
        assert r3 == ["Untitled Minimal Recipe", "", "", "", "", "", "", "", ""]

    def test_file_export_no_duplicate_crlf_on_windows(self) -> None:
        """Verify export_to_file on disk produces pure CRLF without duplicate CR (\\r\\r\\n)."""
        exporter = PlanToEatExporter()
        recipes = [
            RecipeSchema(
                title="CRLF Test",
                description="Line 1\r\nLine 2\nLine 3",
                ingredients=["Item 1", "Item 2"],
                directions=["Step 1", "Step 2"],
            )
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "output.csv"
            exporter.export_to_file(recipes, out_path)

            raw_bytes = out_path.read_bytes()
            assert b"\r\r\n" not in raw_bytes, "Duplicate carriage return detected in exported file!"
            assert b"\r\n" in raw_bytes, "Expected RFC 4180 CRLF line terminators"

            text_content = out_path.read_text(encoding="utf-8")
            assert exporter.validate_csv_format(text_content) is True

    def test_large_batch_export_performance(self) -> None:
        """Verify exporter handles large batches (500 recipes) without errors."""
        exporter = PlanToEatExporter()
        recipes = [
            RecipeSchema(
                title=f"Recipe #{i}",
                description=f"Description for recipe #{i} with multiple lines.\nSecond line.",
                ingredients=[f"Ingredient {j} for #{i}" for j in range(10)],
                directions=[f"Direction step {k} for #{i}" for k in range(5)],
                tags=[f"tag_{i}", "bulk_export"],
            )
            for i in range(500)
        ]

        csv_str = exporter.export_to_csv_string(recipes)
        assert exporter.validate_csv_format(csv_str) is True

        rows = list(csv.reader(io.StringIO(csv_str)))
        assert len(rows) == 501


class TestAllExportersStress:
    """Stress tests across all 9 export format engines with multi-part recipes."""

    @pytest.fixture
    def multipart_recipes(self) -> list[RecipeSchema]:
        return [
            RecipeSchema(
                title="Grandma's 3-Part Thanksgiving Turkey",
                description="Roast turkey spanning 3 scanned index cards.",
                yield_servings="12-14 servings",
                prep_time="45 mins",
                cook_time="3 hours 30 mins",
                total_time="4 hours 15 mins",
                ingredients=[
                    "1 whole turkey (14 lbs)",
                    "½ cup unsalted butter",
                    "2 tbsp fresh rosemary, minced",
                    "2 tbsp fresh thyme, minced",
                    "1 tbsp coarse sea salt",
                    "1 tsp freshly ground black pepper",
                    "1 yellow onion, quartered",
                    "1 lemon, halved",
                ],
                directions=[
                    "1. Thaw turkey completely and pat dry.",
                    "2. Season cavity with salt, pepper, onion, and lemon.",
                    "3. Rub butter and herb mixture under the breast skin.",
                    "4. Roast at 325°F until thigh registers 165°F.",
                    "5. Rest 20 minutes before carving.",
                ],
                tags=["Holiday", "Thanksgiving", "Poultry", "Heirloom"],
                source_notes="Cards #10, #11, #12 (Box 1)",
                card_ids=["card_10", "card_11", "card_12"],
            ),
            RecipeSchema(
                title="Dark Wedding Fruitcake (4 Cards)",
                description="Traditional 4-card sequence vintage fruitcake.",
                yield_servings="24 slices",
                prep_time="2 hours",
                cook_time="3 hours",
                total_time="5 hours",
                ingredients=[
                    "2 cups dark raisins",
                    "1 cup dried currants",
                    "1 cup chopped candied peel",
                    "½ cup dark rum or brandy",
                    "3 cups all-purpose flour",
                    "1 tsp baking powder",
                    "1 tsp ground cinnamon",
                    "½ tsp ground cloves",
                    "1 cup brown sugar",
                    "4 large eggs",
                ],
                directions=[
                    "1. Soak dried fruit in rum overnight.",
                    "2. Sift flour with baking powder and spices.",
                    "3. Cream butter and sugar, beat in eggs one at a time.",
                    "4. Fold in flour mixture and soaked fruit.",
                    "5. Pour into prepared loaf pans and bake at 300°F for 3 hours.",
                    "6. Cool completely and wrap in cheesecloth soaked in rum.",
                ],
                tags=["Vintage", "Holiday", "Baking", "Fruitcake"],
                source_notes="Cards #20, #21, #22, #23 (Box 2)",
                card_ids=["card_20", "card_21", "card_22", "card_23"],
            ),
        ]

    def test_all_9_exporters_export_successfully(
        self,
        multipart_recipes: list[RecipeSchema],
    ) -> None:
        """Verify all 9 export formats serialize multi-part recipes to disk and bytes without errors."""
        from recipeboxer.exporter.plan_to_eat import PlanToEatExporter
        from recipeboxer.exporter.json_ld import JsonLdExporter
        from recipeboxer.exporter.json_export import JsonExporter
        from recipeboxer.exporter.google_docs import GoogleDocsExporter
        from recipeboxer.exporter.docx_export import DocxExporter
        from recipeboxer.exporter.pdf_export import PdfExporter
        from recipeboxer.exporter.markdown_export import MarkdownExporter
        from recipeboxer.exporter.cooklang import CooklangExporter
        from recipeboxer.exporter.paprika import PaprikaExporter

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # 1. Plan To Eat CSV
            csv_path = PlanToEatExporter().export_to_file(multipart_recipes, tmp_path / "test.csv")
            assert csv_path.exists() and csv_path.stat().st_size > 0

            # 2. Schema.org JSON-LD
            json_ld_path = JsonLdExporter().export_to_file(multipart_recipes, tmp_path / "test_ld.json")
            assert json_ld_path.exists() and json_ld_path.stat().st_size > 0

            # 3. Raw JSON
            raw_json_path = JsonExporter().export_to_file(multipart_recipes, tmp_path / "test_raw.json")
            assert raw_json_path.exists() and raw_json_path.stat().st_size > 0

            # 4. Google Docs Archival HTML & JSON
            gdocs_html_path = GoogleDocsExporter().export_to_file(multipart_recipes, tmp_path / "test_gdocs.html")
            assert gdocs_html_path.exists() and gdocs_html_path.stat().st_size > 0

            # 5. Heirloom DOCX
            docx_path = DocxExporter().export_to_file(multipart_recipes, tmp_path / "test.docx")
            assert docx_path.exists() and docx_path.stat().st_size > 0

            # 6. Archival PDF
            pdf_path = PdfExporter().export_to_file(multipart_recipes, tmp_path / "test.pdf")
            assert pdf_path.exists() and pdf_path.stat().st_size > 0

            # 7. Obsidian Markdown
            md_path = MarkdownExporter().export_to_file(multipart_recipes, tmp_path / "test.md")
            assert md_path.exists() and md_path.stat().st_size > 0

            # 8. Cooklang (.cook)
            cook_path = CooklangExporter().export_to_file(multipart_recipes, tmp_path / "test.cook")
            assert cook_path.exists() and cook_path.stat().st_size > 0

            # 9. Paprika (.paprikarecipes)
            paprika_path = PaprikaExporter().export_to_file(multipart_recipes, tmp_path / "test.paprikarecipes")
            assert paprika_path.exists() and paprika_path.stat().st_size > 0


class TestSanitizeExportFilenameStress:
    """Stress tests for cross-platform export filename sanitization."""

    @pytest.mark.parametrize(
        "filename,default_name,expected_ext,expected_result",
        [
            ("../../etc/passwd", "default.csv", "csv", "passwd.csv"),
            ("..\\..\\Windows\\System32\\cmd.exe", "default.csv", "csv", "cmd.exe.csv"),
            ("/var/log/app.json", "default.json", "json", "app.json"),
            ("C:\\nested\\folder\\recipes.cook", "default.cook", "cook", "recipes.cook"),
            ("test<illegal>:chars*in?name|here.docx", "default.docx", "docx", "testillegalcharsinnamehere.docx"),
            ("null\x00ctrl\x1f\x7fchars.pdf", "default.pdf", "pdf", "nullctrlchars.pdf"),
            ("   ...spaced_name...   ", "default.csv", "csv", "spaced_name.csv"),
            ("", "default.csv", "csv", "default.csv"),
            (None, "default.csv", "csv", "default.csv"),
            ("   ", "default.csv", "csv", "default.csv"),
            ("...", "default.csv", "csv", "default.csv"),
            ("my_export", "default.csv", "csv", "my_export.csv"),
            ("my_export.CSV", "default.csv", "csv", "my_export.CSV"),
            ("archive.tar.gz", "default.csv", "csv", "archive.tar.gz.csv"),
            ("existing.json", "default.json", "json", "existing.json"),
            ("UPPERCASE.MD", "default.md", "md", "UPPERCASE.MD"),
        ],
    )
    def test_sanitize_filename_variations(
        self,
        filename: str | None,
        default_name: str,
        expected_ext: str,
        expected_result: str,
    ) -> None:
        """Verify cross-platform filename sanitization against path traversal, illegal chars, and extensions."""
        result = sanitize_export_filename(filename, default_name, expected_ext)
        assert result == expected_result


@pytest.fixture
def test_session_manager(test_cache, mock_gemini_client):
    from recipeboxer.agent.session import SessionManager
    return SessionManager()


@pytest.fixture
def app(test_session_manager):
    from recipeboxer.api.app import create_app
    return create_app(session_manager=test_session_manager)


class TestExportEndpointsFilenameSanitization:
    """Verify all FastAPI export endpoints sanitize query parameter filenames."""

    @pytest.mark.asyncio
    async def test_endpoints_sanitize_traversal_filenames(
        self,
        test_session_manager,
        app,
    ) -> None:
        from httpx import ASGITransport, AsyncClient
        from recipeboxer.models.pipeline import PipelineState

        sid = "sess_challenger2_m5_test"
        session = test_session_manager.create_session(session_id=sid)
        session.state = PipelineState.COMPLETED

        r = RecipeSchema(
            recipe_id="rec_01",
            title="Test Recipe",
            ingredients=["1 egg"],
            directions=["1. Cook egg."],
        )
        session.recipes = [r]
        store = getattr(app.state, "session_store", None)
        if store:
            store.save_recipe(sid, r)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. CSV
            res = await client.get(f"/api/export/csv?session_id={sid}&filename=../../secret.csv")
            assert res.status_code == 200
            assert 'filename="secret.csv"' in res.headers.get("content-disposition", "")

            # 2. JSON-LD
            res = await client.get(f"/api/export/json-ld?session_id={sid}&filename=..\\..\\secret.json")
            assert res.status_code == 200
            assert 'filename="secret.json"' in res.headers.get("content-disposition", "")

            # 3. Raw JSON
            res = await client.get(f"/api/export/json?session_id={sid}&filename=nested/folder/secret.json")
            assert res.status_code == 200
            assert 'filename="secret.json"' in res.headers.get("content-disposition", "")

            # 4. DOCX
            res = await client.get(f"/api/export/docx?session_id={sid}&filename=../../secret.docx")
            assert res.status_code == 200
            assert 'filename="secret.docx"' in res.headers.get("content-disposition", "")

            # 5. PDF
            res = await client.get(f"/api/export/pdf?session_id={sid}&filename=../../secret.pdf")
            assert res.status_code == 200
            assert 'filename="secret.pdf"' in res.headers.get("content-disposition", "")

            # 6. Markdown
            res = await client.get(f"/api/export/markdown?session_id={sid}&filename=../../secret.md")
            assert res.status_code == 200
            assert 'filename="secret.md"' in res.headers.get("content-disposition", "")

            # 7. Cooklang
            res = await client.get(f"/api/export/cooklang?session_id={sid}&filename=../../secret.cook")
            assert res.status_code == 200
            assert 'filename="secret.cook"' in res.headers.get("content-disposition", "")

            # 8. Paprika
            res = await client.get(f"/api/export/paprika?session_id={sid}&filename=../../secret.paprikarecipes")
            assert res.status_code == 200
            assert 'filename="secret.paprikarecipes"' in res.headers.get("content-disposition", "")

