"""Tests for Google Docs Archival Exporter (recipeboxer/exporter/google_docs.py)."""

import json
from pathlib import Path
import httpx
import pytest

from recipeboxer.exporter.google_docs import (
    GoogleDocsExporter,
    export_recipes_to_google_docs_batch_requests,
    export_recipes_to_google_docs_html,
    export_recipes_to_google_docs_json,
)
from recipeboxer.models.recipe import RecipeSchema


@pytest.fixture
def sample_recipes() -> list[RecipeSchema]:
    """Sample recipes with rich attributes, card IDs, and notes."""
    r1 = RecipeSchema(
        recipe_id="rec_001",
        title="Grandma's Apple Pie",
        description="Traditional heirloom cinnamon apple pie with flaky crust.",
        yield_servings="8 servings",
        prep_time="30 mins",
        cook_time="45 mins",
        total_time="1 hr 15 mins",
        ingredients=[
            "6 large apples, peeled and sliced",
            "3/4 cup granulated sugar",
            "1 tbsp ground cinnamon",
            "2 rolled pastry crusts",
        ],
        directions=[
            "1. Toss sliced apples with sugar and cinnamon in a large bowl.",
            "2. Fit pastry crust into 9-inch pie dish.",
            "3. Fill with spiced apples and top with second crust.",
            "4. Bake at 375°F for 45 minutes until golden brown.",
        ],
        tags=["Dessert", "Baking", "Holiday"],
        source_notes="Grandma's recipe box (1952)",
        card_ids=["card_front_01", "card_back_01"],
    )

    r2 = RecipeSchema(
        recipe_id="rec_002",
        title="Sunday Pot Roast",
        description="Slow-braised beef roast with root vegetables.",
        yield_servings="6 servings",
        prep_time="20 mins",
        cook_time="3 hrs",
        total_time="3 hrs 20 mins",
        ingredients=[
            "3 lbs beef chuck roast",
            "4 carrots, cut into chunks",
            "4 russet potatoes, quartered",
            "1 yellow onion, sliced",
            "2 cups beef broth",
        ],
        directions=[
            "1. Season beef generously with salt and pepper.",
            "2. Sear on all sides in a Dutch oven.",
            "3. Add vegetables and broth, cover and braise at 300°F for 3 hours.",
        ],
        tags=["Main Course", "Comfort Food"],
        source_notes="Sunday family dinner notes",
        card_ids=["card_front_02"],
    )
    return [r1, r2]


class TestGoogleDocsExporter:
    """Test suite for GoogleDocsExporter batch update requests and JSON/HTML archives."""

    def test_batch_update_requests_structure(self, sample_recipes: list[RecipeSchema]):
        exporter = GoogleDocsExporter()
        requests = exporter.build_batch_update_requests(sample_recipes)

        assert isinstance(requests, list)
        assert len(requests) > 0

        # Check for title insertion and styles
        insert_texts = [r["insertText"]["text"] for r in requests if "insertText" in r]
        assert any("Grandma's Apple Pie" in t for t in insert_texts)
        assert any("Sunday Pot Roast" in t for t in insert_texts)
        assert any("Ingredients" in t for t in insert_texts)
        assert any("Directions" in t for t in insert_texts)

        # Check for page break between recipes
        page_breaks = [r for r in requests if "insertPageBreak" in r]
        assert len(page_breaks) == 1  # 1 break between 2 recipes

        # Check for paragraph styles (HEADING_1, HEADING_2)
        p_styles = [
            r["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"]
            for r in requests
            if "updateParagraphStyle" in r
        ]
        assert "HEADING_1" in p_styles
        assert "HEADING_2" in p_styles

        # Check for bullet presets
        bullets = [
            r["createParagraphBullets"]["bulletPreset"]
            for r in requests
            if "createParagraphBullets" in r
        ]
        assert "BULLET_DISC_CIRCLE_SQUARE" in bullets
        assert "NUMBERED_DECIMAL_ALPHA_ROMAN" in bullets

    def test_embedded_card_scans_in_batch_requests(self, sample_recipes: list[RecipeSchema]):
        def mock_resolver(card_id: str) -> str:
            return f"https://recipeboxer.local/scans/{card_id}.jpg"

        exporter = GoogleDocsExporter()
        requests = exporter.build_batch_update_requests(
            sample_recipes,
            image_resolver=mock_resolver,
        )

        inline_images = [r["insertInlineImage"] for r in requests if "insertInlineImage" in r]
        assert len(inline_images) == 3  # 2 for rec1, 1 for rec2
        assert inline_images[0]["uri"] == "https://recipeboxer.local/scans/card_front_01.jpg"
        assert inline_images[1]["uri"] == "https://recipeboxer.local/scans/card_back_01.jpg"

    def test_embedded_card_scans_fallback_to_image_urls(self):
        """Adversarial check: When resolver returns None, fall back to recipe.image_urls."""
        recipe = RecipeSchema(
            recipe_id="rec_url_test",
            title="Online Recipe",
            ingredients=["1 apple"],
            directions=["Eat apple."],
            image_urls=["https://example.com/apple.jpg"],
        )
        exporter = GoogleDocsExporter()
        requests = exporter.build_batch_update_requests(recipe, image_resolver=lambda cid: None)
        inline_images = [r["insertInlineImage"] for r in requests if "insertInlineImage" in r]
        assert len(inline_images) == 1
        assert inline_images[0]["uri"] == "https://example.com/apple.jpg"

        html_out = exporter.export_to_html(recipe, image_resolver=lambda cid: None)
        assert "https://example.com/apple.jpg" in html_out

    def test_json_archive_export(self, sample_recipes: list[RecipeSchema]):
        exporter = GoogleDocsExporter()
        doc_dict = exporter.export_to_json_dict(sample_recipes, title="Family Recipes")

        assert doc_dict["title"] == "Family Recipes"
        assert doc_dict["recipe_count"] == 2
        assert "requests" in doc_dict
        assert len(doc_dict["requests"]) > 0
        assert len(doc_dict["recipes"]) == 2

        # Verify JSON serialization
        json_str = exporter.export_to_json_string(sample_recipes)
        parsed = json.loads(json_str)
        assert parsed["recipe_count"] == 2
        assert "documentId" in parsed

    def test_html_archive_export(self, sample_recipes: list[RecipeSchema], tmp_path: Path):
        exporter = GoogleDocsExporter()
        html_content = exporter.export_to_html(
            sample_recipes,
            title="Archival Cookbook",
            embed_images_base64=False,
        )

        assert "<!DOCTYPE html>" in html_content
        assert "Archival Cookbook" in html_content
        assert "Grandma&#x27;s Apple Pie" in html_content or "Grandma&#39;s Apple Pie" in html_content or "Grandma's Apple Pie" in html_content
        assert "Sunday Pot Roast" in html_content
        assert "meta-table" in html_content
        assert "recipe-page-break" in html_content

        # Write to file
        out_html = tmp_path / "cookbook.html"
        exporter.export_to_file(sample_recipes, out_html, as_html=True)
        assert out_html.exists()
        assert out_html.stat().st_size > 0

    def test_multi_sentence_direction_cleaning(self):
        """Adversarial check: Multi-sentence steps without leading numbers must NOT lose first sentence."""
        recipe = RecipeSchema(
            recipe_id="rec_sentences",
            title="Shortbread Cookies",
            directions=[
                "Cream butter and sugar until fluffy. Add vanilla extract.",
                "Step 2: Sift in flour. Mix until soft dough forms.",
                "3. Roll dough into log. Chill in refrigerator for 1 hour.",
            ],
            ingredients=["1 cup butter", "1/2 cup sugar", "2 cups flour"],
        )
        exporter = GoogleDocsExporter()
        html_out = exporter.export_to_html(recipe)

        # Ensure first sentence is fully preserved
        assert "Cream butter and sugar until fluffy. Add vanilla extract." in html_out
        assert "Sift in flour. Mix until soft dough forms." in html_out
        assert "Roll dough into log. Chill in refrigerator for 1 hour." in html_out

        # Verify batch requests clean step numbers without deleting sentences
        reqs = exporter.build_batch_update_requests(recipe)
        dir_texts = [r["insertText"]["text"] for r in reqs if "insertText" in r]
        combined_text = "".join(dir_texts)
        assert "Cream butter and sugar until fluffy." in combined_text
        assert "Sift in flour." in combined_text
        assert "Roll dough into log." in combined_text

    def test_convenience_functions(self, sample_recipes: list[RecipeSchema]):
        reqs = export_recipes_to_google_docs_batch_requests(sample_recipes)
        assert isinstance(reqs, list)

        json_out = export_recipes_to_google_docs_json(sample_recipes)
        assert "documentId" in json_out

        html_out = export_recipes_to_google_docs_html(sample_recipes)
        assert "<!DOCTYPE html>" in html_out

    def test_create_google_document_success(self, sample_recipes: list[RecipeSchema], tmp_path: Path):
        """GAP-01: Verify authentic Google Docs creation and Drive image upload flow."""
        img_path = tmp_path / "card_front_01.jpg"
        img_path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)  # fake JPEG header

        def handler(request: httpx.Request) -> httpx.Response:
            url_str = str(request.url)
            # 1. Drive search folder
            if "drive/v3/files" in url_str and request.method == "GET":
                return httpx.Response(200, json={"files": [{"id": "folder_123", "name": "RecipeBoxer Scans"}]})
            # 2. Drive upload file
            if "upload/drive/v3/files" in url_str and request.method == "POST":
                return httpx.Response(200, json={"id": "file_scan_001", "webContentLink": "https://drive.google.com/uc?id=file_scan_001"})
            # 3. Drive permission
            if "permissions" in url_str and request.method == "POST":
                return httpx.Response(200, json={"id": "perm_001"})
            # 4. Docs create document
            if "docs.googleapis.com/v1/documents" in url_str and request.method == "POST" and not url_str.endswith(":batchUpdate"):
                return httpx.Response(200, json={"documentId": "doc_recipeboxer_999"})
            # 5. Docs batch update
            if ":batchUpdate" in url_str and request.method == "POST":
                return httpx.Response(200, json={"replies": []})

            return httpx.Response(404, json={"error": "not found"})

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)

        exporter = GoogleDocsExporter()
        result = exporter.create_google_document(
            access_token="ya29.mock_valid_token_12345",
            recipes=sample_recipes,
            title="Grandma's Heirloom Cookbook",
            image_resolver=lambda cid: img_path,
            http_client=client,
        )

        assert result["document_id"] == "doc_recipeboxer_999"
        assert result["url"] == "https://docs.google.com/document/d/doc_recipeboxer_999/edit"
        assert result["title"] == "Grandma's Heirloom Cookbook"
        assert result["recipe_count"] == 2

    def test_create_google_document_unauthorized_token_401(self, sample_recipes: list[RecipeSchema]):
        """Verify 401 unauthorized token raises clear ValueError."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"code": 401, "message": "Invalid Credentials"}})

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)

        exporter = GoogleDocsExporter()
        with pytest.raises(ValueError) as exc_info:
            exporter.create_google_document(
                access_token="ya29.expired_token",
                recipes=sample_recipes,
                http_client=client,
            )
        assert "401" in str(exc_info.value) or "unauthorized" in str(exc_info.value).lower() or "invalid" in str(exc_info.value).lower()

    def test_create_google_document_validation(self, sample_recipes: list[RecipeSchema]):
        """Verify missing token or empty recipe list raises ValueError."""
        exporter = GoogleDocsExporter()
        with pytest.raises(ValueError, match="access token is required"):
            exporter.create_google_document(access_token="", recipes=sample_recipes)

        with pytest.raises(ValueError, match="No recipes provided"):
            exporter.create_google_document(access_token="valid_token", recipes=[])

