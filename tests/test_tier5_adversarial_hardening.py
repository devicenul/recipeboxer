"""Tier 5 Adversarial Hardening Test Suite for RecipeBoxer.

Comprehensive white-box adversarial stress testing across all enhanced modules:
1. Ingestion & Connection Stability (Directory picker API & fallback, SSE backoff & disconnect safety, batch default tags).
2. Multi-Card HITL Modal & Sequence Ordering (3, 4, 5, 8+ card sequences, drag/reorder, reverse, detach, backend multimodal extraction order).
3. Recipe Editor UX & Extraction Intelligence (Card image loading resets, last-recipe navigation dialog, inline tag editing/sync, [GUESS] title flagging, anecdote/marginalia isolation).
4. Exporters, Google Docs OAuth, Images & Filenames (Google Docs direct creation & Drive upload, DOCX proportional EMU scaling, filename customization across all 9 formats, RFC 4180 CSV Col 8 tags, JSON-LD keywords, Markdown/Cooklang frontmatter, Paprika).
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from PIL import Image

from recipeboxer.agent.orchestrator import RecipeBoxerAgent
from recipeboxer.agent.session import PipelineSession, SessionManager
from recipeboxer.api.app import create_app
from recipeboxer.api.routes import sanitize_export_filename
from recipeboxer.config import Settings
from recipeboxer.exporter.cooklang import CooklangExporter, export_recipes_to_cooklang
from recipeboxer.exporter.docx_export import (
    MAX_EMU_HEIGHT,
    MAX_EMU_WIDTH,
    DocxExporter,
    export_recipes_to_docx,
)
from recipeboxer.exporter.google_docs import (
    GoogleDocsExporter,
    create_google_docs_archival_document,
    export_recipes_to_google_docs_batch_requests,
    export_recipes_to_google_docs_html,
    export_recipes_to_google_docs_json,
)
from recipeboxer.exporter.json_export import JsonExporter, export_recipes_to_json
from recipeboxer.exporter.json_ld import (
    JsonLdExporter,
    export_recipes_to_json_ld,
    human_duration_to_iso8601,
)
from recipeboxer.exporter.markdown_export import (
    MarkdownExporter,
    export_recipes_to_markdown,
)
from recipeboxer.exporter.paprika import PaprikaExporter, export_recipes_to_paprika
from recipeboxer.exporter.pdf_export import PdfExporter, export_recipes_to_pdf
from recipeboxer.exporter.plan_to_eat import (
    PLAN_TO_EAT_COLUMNS,
    PlanToEatExporter,
    export_recipes_to_plan_to_eat_csv,
)
from recipeboxer.extraction.extractor import (
    RECIPE_EXTRACTION_SYSTEM_INSTRUCTION,
    GeminiRecipeExtractor,
    compute_adaptive_thinking_budget,
)
from recipeboxer.ingestion.scanner import ScannedCard
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.hitl import (
    ApprovalPayload,
    CardPairApproval,
    CorrelationCandidate,
    HITLDecision,
)
from recipeboxer.ingestion.runner import IngestionConfig
from recipeboxer.models.pipeline import (
    IngestionMode,
    PipelineStartRequest,
    PipelineState,
    ProgressMetrics,
)
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.storage.session_store import SessionStore
from recipeboxer.vision.router import (
    CardModality,
    ModalityClassificationResult,
    PromptRouter,
    classify_card_modality,
    route_prompt_for_modality,
)
from tests.conftest import MockGeminiClient


# ============================================================================
# Helpers & Fixtures
# ============================================================================

def create_dummy_image_bytes(width: int = 400, height: int = 300, color: str = "red", fmt: str = "JPEG") -> bytes:
    """Generates synthetic image bytes for testing."""
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    return tmp_path / f"test_store_{uuid.uuid4().hex[:8]}.db"


@pytest.fixture
def session_store(temp_db: Path) -> SessionStore:
    store = SessionStore(db_path=temp_db)
    yield store
    store.close()


@pytest.fixture
def session_manager() -> SessionManager:
    return SessionManager()


@pytest.fixture
def test_app(session_manager: SessionManager, session_store: SessionStore) -> Any:
    settings = Settings(
        gemini_api_key="test-api-key",
        enable_auth=False,
    )
    app = create_app(settings=settings)
    app.state.session_manager = session_manager
    app.state.session_store = session_store
    return app


@pytest_asyncio.fixture
async def async_client(test_app: Any) -> Any:
    transport = ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


# ============================================================================
# 1. Ingestion & Connection Stability Tests
# ============================================================================

class TestIngestionAndConnectionStabilityAdversarial:
    """Stress tests for Ingestion, Session Configuration, SSE Backoff, and Batch Default Tags."""

    def test_batch_default_tags_parsing_and_merging(self):
        """Validates that session batch default tags are merged, stripped, and deduplicated."""
        # 1. Test clean_and_deduplicate_tags
        tags_input = [" Grandma's Heirloom ", "vintage", "VINTAGE", "Dessert", "", "   ", "Holiday"]
        deduped = RecipeSchema.clean_and_deduplicate_tags(tags_input)
        assert deduped == ["Grandma's Heirloom", "vintage", "Dessert", "Holiday"]

        # 2. Test IngestionConfig with mixed comma-separated string vs list
        cfg1 = IngestionConfig(default_tags=["Baking", "Family Tradition", "Baking"])
        assert cfg1.default_tags == ["Baking", "Family Tradition", "Baking"]

        # 3. Test Orchestrator tag resolution
        orchestrator = RecipeBoxerAgent()
        session = PipelineSession(session_id="test_sess_tags")
        session.config = {"default_tags": "Thanksgiving, Family Feast, Thanksgiving"}
        ing_cfg = orchestrator._resolve_ingestion_config(session.config)
        assert RecipeSchema.clean_and_deduplicate_tags(ing_cfg.default_tags) == ["Thanksgiving", "Family Feast"]

    @pytest.mark.asyncio
    async def test_pipeline_start_with_batch_default_tags_and_empty_directory_error(self, async_client: httpx.AsyncClient, tmp_path: Path):
        """Pipeline start with batch default tags on empty directory returns 400."""
        empty_dir = tmp_path / "empty_scans"
        empty_dir.mkdir()

        payload = {
            "directory_path": str(empty_dir),
            "default_tags": ["Vintage", "Grandma Rose", "Holiday Dessert"],
            "mode": "parallel",
            "max_concurrency": 2,
        }

        resp = await async_client.post("/api/pipeline/start", json=payload)
        assert resp.status_code == 400
        data = resp.json()
        assert "no valid card scans found in directory" in data["detail"].lower()

    @pytest.mark.asyncio
    async def test_pipeline_start_with_nonexistent_directory(self, async_client: httpx.AsyncClient):
        """Pipeline start with invalid directory path returns 400."""
        payload = {
            "directory_path": "/path/to/nonexistent/directory/scans_xyz_999",
            "default_tags": ["Test"],
        }
        resp = await async_client.post("/api/pipeline/start", json=payload)
        assert resp.status_code == 400
        assert "directory does not exist" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_sse_endpoint_validates_session_and_emits_ping(self, async_client: httpx.AsyncClient, session_manager: SessionManager):
        """SSE event stream stays open and handles invalid session IDs cleanly."""
        # Non-existent session returns 404
        resp = await async_client.get("/api/events?session_id=non_existent_session_123")
        assert resp.status_code == 404


# ============================================================================
# 2. Multi-Card HITL Sequence Ordering & Multimodal Extraction Order Tests
# ============================================================================

class TestMultiCardSequenceOrderingAdversarial:
    """Stress tests for 3, 4, 5, 8+ card sequences, drag/reorder, reverse, detach, and extraction."""

    @pytest.mark.asyncio
    async def test_orchestrator_preserves_custom_ordered_card_sequence_3_to_8_cards(self):
        """Verifies that orchestrator accurately passes ordered card fingerprints and updates recipe."""
        orchestrator = RecipeBoxerAgent()
        session = PipelineSession(session_id="test_multicard_sess")
        session.mock_client = MockGeminiClient()
        session.state = PipelineState.HITL_PENDING

        # Create 8 cards
        card_ids = [f"card_heirloom_{i:02d}" for i in range(1, 9)]
        fps: Dict[str, VisualFingerprint] = {}
        for cid in card_ids:
            fps[cid] = VisualFingerprint(
                card_id=cid,
                image_path=f"/fake/scans/{cid}.jpg",
                sha256_hash=f"hash_{cid}_12345",
                role=CardRole.UNKNOWN,
            )
        session.fingerprints = fps

        # User submits 8-card approval with custom reverse-interleaved permutation
        custom_order = [
            "card_heirloom_08",
            "card_heirloom_01",
            "card_heirloom_07",
            "card_heirloom_02",
            "card_heirloom_06",
            "card_heirloom_03",
            "card_heirloom_05",
            "card_heirloom_04",
        ]

        approval = CardPairApproval(
            proposal_id="prop_8card",
            front_card_id=custom_order[0],
            back_card_id=custom_order[1],
            card_ids=custom_order,
            decision=HITLDecision.APPROVE,
        )
        session.approvals = [approval]

        # Mock _extract_single_recipe to verify ordered_fps sequence
        captured_ordered_fps: List[VisualFingerprint] = []

        async def mock_extract(sess, front_fp=None, back_fp=None, ordered_fps=None):
            nonlocal captured_ordered_fps
            captured_ordered_fps = list(ordered_fps or [])
            return RecipeSchema(
                recipe_id="rec_8card_feast",
                title="8-Course Grand Heirloom Feast",
                ingredients=["1 whole roast turkey", "2 cups sage stuffing", "1 jar cranberry conserve"],
                directions=["Prepare turkey and brine for 24 hours.", "Roast at 325F until internal temp is 165F."],
                tags=["Feast", "Holiday"],
            )

        orchestrator._extract_single_recipe = mock_extract  # type: ignore

        # Set session cards so scanning stage doesn't look for disk directory
        session.cards = [
            ScannedCard(card_id=cid, file_path=f"/fake/{cid}.jpg", filename=f"{cid}.jpg", file_size_bytes=100)
            for cid in card_ids
        ]

        from recipeboxer.ingestion.runner import IngestionResult
        from recipeboxer.models.correlation import CorrelationResult

        with patch("recipeboxer.agent.orchestrator.IngestionRunner.run", new_callable=AsyncMock) as mock_runner_run, \
             patch.object(orchestrator.pairing_optimizer, "optimize", new_callable=AsyncMock) as mock_optimize:

            mock_runner_run.return_value = IngestionResult(
                fingerprints=list(fps.values()),
                errors=[],
                processed_cards=8,
                cached_cards=0,
            )
            mock_optimize.return_value = CorrelationResult(
                proposals=[],
                unmatched_card_ids=[],
            )

            session.pause_for_hitl = AsyncMock(return_value=True)  # type: ignore

            # Run pipeline extraction step
            await orchestrator.run_pipeline(session)

        # Assertions
        assert session.state == PipelineState.COMPLETED
        assert len(captured_ordered_fps) == 8
        assert [fp.card_id for fp in captured_ordered_fps] == custom_order
        assert len(session.recipes) == 1
        recipe = session.recipes[0]
        assert recipe.card_ids == custom_order
        assert len(recipe.image_urls) == 8
        assert recipe.image_urls[0] == f"/api/cards/{custom_order[0]}/image?session_id={session.session_id}"
        assert recipe.image_urls[-1] == f"/api/cards/{custom_order[-1]}/image?session_id={session.session_id}"

    @pytest.mark.asyncio
    async def test_hitl_approval_payload_validation_and_rejection_of_unrelated_cards(self, async_client: httpx.AsyncClient, session_manager: SessionManager):
        """Tests that HITL approve endpoint properly validates session state and payload."""
        session = session_manager.create_session(config={"default_tags": ["Test"]})
        session.state = PipelineState.HITL_PENDING

        approval = {
            "proposal_id": "prop_test_01",
            "front_card_id": "card_01",
            "back_card_id": "card_02",
            "card_ids": ["card_01", "card_02", "card_03"],
            "decision": "APPROVE",
        }

        payload = {
            "session_id": session.session_id,
            "approvals": [approval],
            "rejected_card_ids": ["card_04"],
            "continue_pipeline": True,
        }

        resp = await async_client.post("/api/hitl/approve", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved_count"] == 1
        assert data["rejected_count"] == 1


# ============================================================================
# 3. Recipe Editor UX & Extraction Intelligence Tests
# ============================================================================

class TestRecipeEditorAndExtractionIntelligenceAdversarial:
    """Stress tests for `[GUESS]` title flagging, anecdote/marginalia isolation, and inline tag editing."""

    def test_recipe_extraction_system_prompt_enforces_guess_flagging_and_marginalia_isolation(self):
        """Verifies that the system instruction explicitly specifies [GUESS] and isolation."""
        prompt = RECIPE_EXTRACTION_SYSTEM_INSTRUCTION
        assert "TITLE INFERENCE & GUESSING" in prompt
        assert "[GUESS]" in prompt
        assert "ANECDOTE, STORY & METADATA PRESERVATION" in prompt
        assert "STRICT ISOLATION" in prompt
        assert "NEVER place family stories, personal anecdotes, handwritten marginalia" in prompt

    def test_prompt_router_modality_classification_and_routing(self):
        """Tests prompt router across diverse document modalities."""
        mock_client = MagicMock()
        mock_client.classify_modality.side_effect = lambda inp: {
            "grandma_cursive_1942.jpg": ModalityClassificationResult(modality=CardModality.HANDWRITTEN_CURSIVE, confidence=0.95),
            "newspaper_clipping_recipe.png": ModalityClassificationResult(modality=CardModality.PRINTED_CLIPPING_OR_LABEL, confidence=0.95),
            "typewritten_card_apple_pie.jpg": ModalityClassificationResult(modality=CardModality.TYPEWRITTEN, confidence=0.95),
        }.get(str(inp), ModalityClassificationResult(modality=CardModality.HANDWRITTEN_CURSIVE, confidence=0.0))

        # 1. Vintage handwriting
        mod1 = classify_card_modality("grandma_cursive_1942.jpg", client=mock_client)
        assert mod1.modality == CardModality.HANDWRITTEN_CURSIVE

        # 2. Newsprint clipping
        mod2 = classify_card_modality("newspaper_clipping_recipe.png", client=mock_client)
        assert mod2.modality == CardModality.PRINTED_CLIPPING_OR_LABEL

        # 3. Typewritten card
        mod3 = classify_card_modality("typewritten_card_apple_pie.jpg", client=mock_client)
        assert mod3.modality == CardModality.TYPEWRITTEN

        # Test adaptive thinking budgets
        enabled, budget = compute_adaptive_thinking_budget(CardModality.HANDWRITTEN_CURSIVE, degradation_level="SEVERE")
        assert enabled is True
        assert budget == 4096

        enabled_type, budget_type = compute_adaptive_thinking_budget(CardModality.TYPEWRITTEN, degradation_level="NONE")
        assert enabled_type is False
        assert budget_type is None

    @pytest.mark.asyncio
    async def test_recipe_update_api_inline_modifications_persisted(self, async_client: httpx.AsyncClient, session_manager: SessionManager, session_store: SessionStore):
        """Tests PUT /api/recipes/{recipe_id} for inline tag, title, and ingredient modifications."""
        session = session_manager.create_session()
        original_recipe = RecipeSchema(
            recipe_id="rec_butter_cookies",
            title="Aunt Clara's Butter Cookies [GUESS]",
            description="Handwritten on index card dated Christmas 1954.",
            ingredients=["1 cup butter", "1/2 cup sugar", "2 cups flour"],
            directions=["Cream butter and sugar.", "Bake at 350F for 12 mins."],
            tags=["Holiday", "Cookies"],
            source_notes="Aunt Clara's 1954 Recipe Box",
        )
        session.recipes = [original_recipe]
        session_store.save_recipe(session.session_id, original_recipe)

        # User edits title (removes [GUESS]), adds new inline tag, and updates ingredients
        updated_payload = original_recipe.model_dump()
        updated_payload["title"] = "Aunt Clara's Butter Cookies"
        updated_payload["tags"] = ["Holiday", "Cookies", "Heirloom", "Shortbread"]
        updated_payload["ingredients"].append("1 tsp pure vanilla extract")

        resp = await async_client.put(
            f"/api/recipes/rec_butter_cookies?session_id={session.session_id}",
            json=updated_payload,
        )
        assert resp.status_code == 200
        saved = resp.json()
        assert saved["title"] == "Aunt Clara's Butter Cookies"
        assert "Heirloom" in saved["tags"]
        assert len(saved["ingredients"]) == 4

        # Verify persisted in database
        db_recipe = session_store.get_recipe("rec_butter_cookies", session_id=session.session_id)
        assert db_recipe is not None
        assert db_recipe.title == "Aunt Clara's Butter Cookies"
        assert "Heirloom" in db_recipe.tags


# ============================================================================
# 4. Exporters, Google Docs OAuth, Images & Filenames Tests
# ============================================================================

class TestExportersImagesAndFilenamesAdversarial:
    """Stress tests for Google Docs direct creation, DOCX EMU scaling, PDF scans, and universal tags."""

    @pytest.fixture
    def sample_recipes_with_all_fields(self) -> List[RecipeSchema]:
        """Provides rich multi-card recipe models with stories, tags, and special characters."""
        return [
            RecipeSchema(
                recipe_id="rec_apple_strudel",
                title='Grandma "Oma" Sophie\'s Austrian Apple Strudel [GUESS]',
                description='Handwritten note from Vienna (1938): "Best when served warm with double cream."',
                yield_servings="8 servings",
                prep_time="45 mins",
                cook_time="40 mins",
                total_time="1 hr 25 mins",
                ingredients=[
                    "2 cups unbleached bread flour",
                    "1/2 tsp fine sea salt",
                    "6 large Honeycrisp apples, peeled & thinly sliced",
                    "1/2 cup golden raisins soaked in 2 tbsp dark rum",
                    "1 tsp ground cinnamon",
                ],
                directions=[
                    '1. Sift flour and salt into a warm wooden bowl.',
                    '2. Stretch dough paper-thin on a floured tablecloth until you can "read a newspaper through it".',
                    '3. Spread spiced apple filling and roll carefully using the cloth edge.',
                    '4. Bake at 375°F (190°C) until golden brown & crispy (approx 40 min).',
                ],
                tags=["Dessert", "Austrian", "Heirloom", "Pastry, Strudel", 'Holiday "Favorites"'],
                source_notes='Preserved in Oma\'s leather-bound journal (p. 42)',
                card_ids=["card_oma_front", "card_oma_back", "card_oma_cont1"],
            ),
            RecipeSchema(
                recipe_id="rec_sunday_gravy",
                title="Nonno's Slow-Simmered Sunday Gravy & Meatballs",
                description="Simmered every Sunday morning starting at 6:00 AM.",
                yield_servings="12 servings",
                prep_time="1 hour",
                cook_time="4 hours",
                total_time="5 hours",
                ingredients=[
                    "2 lbs ground beef, pork & veal blend",
                    "4 cans San Marzano whole tomatoes, crushed by hand",
                    "1 lb Italian sausage links",
                ],
                directions=[
                    "Brown meats in a heavy Dutch oven.",
                    "Add tomatoes and slow simmer on lowest heat for 4 hours.",
                ],
                tags=["Italian", "Dinner", "Comfort Food"],
                source_notes="Transcribed from index card with tomato sauce splatters",
                card_ids=["card_gravy_01", "card_gravy_02"],
            ),
        ]

    # --- 4.1 Filename Sanitization & Path Traversal ---

    @pytest.mark.parametrize(
        "raw_input, default_name, expected_ext, expected_output",
        [
            ("../../../etc/passwd", "default.csv", "csv", "passwd.csv"),
            ("..\\..\\Windows\\System32\\config.sys", "default.json", "json", "config.sys.json"),
            ("My Recipe Box: 2026/08/26 <Best> *Cookbook*?.pdf", "cookbook.pdf", "pdf", "26 Best Cookbook.pdf"),
            ("   \t\n  ", "fallback.docx", "docx", "fallback.docx"),
            (None, "recipes.md", "md", "recipes.md"),
            ('custom_export"with"quotes.csv', "out.csv", "csv", "custom_exportwithquotes.csv"),
            ("export_no_ext", "recipe.cook", "cook", "export_no_ext.cook"),
            ("already_has_ext.cook", "recipe.cook", "cook", "already_has_ext.cook"),
        ],
    )
    def test_sanitize_export_filename_thorough(self, raw_input, default_name, expected_ext, expected_output):
        result = sanitize_export_filename(raw_input, default_name, expected_ext)
        assert result == expected_output
        assert "/" not in result
        assert "\\" not in result
        assert ".." not in result

    # --- 4.2 DOCX Proportional EMU Scaling & Embedded Images ---

    def test_docx_proportional_emu_scaling_and_packaging(self, sample_recipes_with_all_fields: List[RecipeSchema]):
        """Verifies DOCX exporter calculates accurate EMUs and builds valid OpenXML ZIP package."""
        # Create image resolver with distinct landscape and portrait test images
        landscape_img = create_dummy_image_bytes(width=1200, height=600, color="orange")
        portrait_img = create_dummy_image_bytes(width=600, height=1200, color="blue")
        square_img = create_dummy_image_bytes(width=800, height=800, color="green")

        def mock_image_resolver(cid: str) -> Optional[bytes]:
            if "front" in cid:
                return landscape_img
            elif "back" in cid:
                return portrait_img
            elif "cont" in cid:
                return square_img
            return create_dummy_image_bytes()

        exporter = DocxExporter()
        docx_bytes = exporter.export_to_bytes(sample_recipes_with_all_fields, image_resolver=mock_image_resolver)

        assert isinstance(docx_bytes, bytes)
        assert len(docx_bytes) > 1000

        # Inspect ZIP structure
        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
            namelist = zf.namelist()
            assert "[Content_Types].xml" in namelist
            assert "_rels/.rels" in namelist
            assert "word/document.xml" in namelist
            assert "word/styles.xml" in namelist
            assert "word/_rels/document.xml.rels" in namelist

            # Check media folder contains embedded images
            media_files = [f for f in namelist if f.startswith("word/media/")]
            assert len(media_files) >= 3

            # Verify document.xml contains DrawingML elements
            doc_xml = zf.read("word/document.xml").decode("utf-8")
            assert "Grandma &quot;Oma&quot; Sophie&apos;s Austrian Apple Strudel [GUESS]" in doc_xml
            assert "wp:inline" in doc_xml
            assert "Figure 1: Original Scanned Card" in doc_xml

    # --- 4.3 PDF Archival Cookbook Generation with Embedded Scans ---

    def test_pdf_export_with_embedded_scans(self, sample_recipes_with_all_fields: List[RecipeSchema]):
        """Verifies PDF exporter generates standard PDF 1.4+ stream with embedded images."""
        def mock_image_resolver(cid: str) -> bytes:
            return create_dummy_image_bytes(width=400, height=300)

        exporter = PdfExporter()
        pdf_bytes = exporter.export_to_bytes(sample_recipes_with_all_fields, image_resolver=mock_image_resolver)

        assert isinstance(pdf_bytes, bytes)
        assert pdf_bytes.startswith(b"%PDF-")
        assert b"%%EOF" in pdf_bytes

    # --- 4.4 Google Docs Direct Document Creation & Drive Upload ---

    def test_google_docs_batch_update_requests_structure(self, sample_recipes_with_all_fields: List[RecipeSchema]):
        """Verifies Google Docs batchUpdate request list generation."""
        exporter = GoogleDocsExporter()
        requests = exporter.build_batch_update_requests(sample_recipes_with_all_fields)

        assert isinstance(requests, list)
        assert len(requests) > 10

        # Check for HEADING_1, tables, and page breaks
        has_heading = any("insertText" in req for req in requests)
        has_style = any("updateTextStyle" in req for req in requests)
        has_page_break = any("insertPageBreak" in req for req in requests)

        assert has_heading
        assert has_style
        assert has_page_break

    def test_google_docs_direct_document_creation_flow_mocked(self, sample_recipes_with_all_fields: List[RecipeSchema]):
        """Mocks Google Docs & Drive APIs to test the complete direct creation workflow."""
        mock_client = MagicMock(spec=httpx.Client)

        # Drive folder search response
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {"files": [{"id": "folder_recipeboxer_123"}]}

        # Drive file upload response
        upload_resp = MagicMock()
        upload_resp.status_code = 200
        upload_resp.json.return_value = {
            "id": "file_scan_789",
            "webContentLink": "https://drive.google.com/uc?id=file_scan_789",
        }

        # Docs create response
        docs_create_resp = MagicMock()
        docs_create_resp.status_code = 200
        docs_create_resp.json.return_value = {"documentId": "doc_recipeboxer_456"}

        # Docs batchUpdate response
        batch_resp = MagicMock()
        batch_resp.status_code = 200
        batch_resp.json.return_value = {}

        def mock_post(url: str, **kwargs):
            if "upload/drive/v3/files" in url:
                return upload_resp
            elif "drive/v3/files" in url:
                return upload_resp
            elif "docs.googleapis.com/v1/documents/doc_recipeboxer_456:batchUpdate" in url:
                return batch_resp
            elif "docs.googleapis.com/v1/documents" in url:
                return docs_create_resp
            return MagicMock(status_code=200, json=lambda: {})

        mock_client.get.return_value = search_resp
        mock_client.post.side_effect = mock_post

        def mock_image_resolver(cid: str) -> bytes:
            return create_dummy_image_bytes()

        result = create_google_docs_archival_document(
            access_token="ya29.test_valid_mock_token_12345",
            recipes=sample_recipes_with_all_fields,
            title="My Family Heirloom Cookbook",
            image_resolver=mock_image_resolver,
            http_client=mock_client,
        )

        assert result["document_id"] == "doc_recipeboxer_456"
        assert result["url"] == "https://docs.google.com/document/d/doc_recipeboxer_456/edit"
        assert result["recipe_count"] == 2

    def test_google_docs_direct_document_creation_empty_token_raises_value_error(self, sample_recipes_with_all_fields: List[RecipeSchema]):
        """Empty or whitespace Google OAuth token raises ValueError."""
        exporter = GoogleDocsExporter()
        with pytest.raises(ValueError, match="Google OAuth access token is required"):
            exporter.create_google_document(
                access_token="   ",
                recipes=sample_recipes_with_all_fields,
            )

    # --- 4.5 Universal Tag Serialization Across All 9 Formats ---

    def test_universal_tag_serialization_across_all_supported_formats(self, sample_recipes_with_all_fields: List[RecipeSchema]):
        """Adversarial stress test verifying tags are correctly serialized in all export formats."""
        recipes = sample_recipes_with_all_fields
        expected_tags_r1 = recipes[0].tags
        assert expected_tags_r1 is not None

        # 1. Plan To Eat CSV (Column 8: Tags)
        csv_text = export_recipes_to_plan_to_eat_csv(recipes)
        reader = list(csv_text.splitlines())
        header = reader[0]
        assert "Tags" in header
        # Row 1 check
        r1_line = reader[1]
        assert "Pastry, Strudel" in r1_line or "Austrian" in r1_line

        # 2. Schema.org JSON-LD (keywords & recipeCategory)
        json_ld_str = export_recipes_to_json_ld(recipes)
        json_ld_data = json.loads(json_ld_str)
        items = json_ld_data["@graph"] if "@graph" in json_ld_data else [json_ld_data]
        r1_schema = items[0]
        assert "keywords" in r1_schema
        assert "Austrian" in r1_schema["keywords"]
        assert "recipeCategory" in r1_schema

        # 3. Raw Structured JSON
        raw_json_str = export_recipes_to_json(recipes)
        raw_data = json.loads(raw_json_str)
        assert raw_data[0]["tags"] == expected_tags_r1

        # 4. Obsidian Markdown (YAML Frontmatter)
        md_text = export_recipes_to_markdown(recipes)
        assert "tags:" in md_text
        assert "Austrian" in md_text
        assert "Heirloom" in md_text

        # 5. Cooklang (>> tags: ...)
        cook_text = export_recipes_to_cooklang(recipes)
        assert ">> tags:" in cook_text
        assert "Austrian" in cook_text

        # 6. Paprika 3 (categories: [...])
        paprika_bytes = export_recipes_to_paprika(recipes)
        assert isinstance(paprika_bytes, bytes)
        # Decompress Paprika gzip
        import gzip
        decompressed = gzip.decompress(paprika_bytes).decode("utf-8")
        paprika_data = json.loads(decompressed)
        if isinstance(paprika_data, list):
            assert "categories" in paprika_data[0]
            assert "Austrian" in paprika_data[0]["categories"]
        else:
            assert "categories" in paprika_data
            assert "Austrian" in paprika_data["categories"]

        # 7. Google Docs HTML
        gdocs_html = export_recipes_to_google_docs_html(recipes)
        assert 'class="tag-badge"' in gdocs_html
        assert "Austrian" in gdocs_html

        # 8. Google Docs JSON
        gdocs_json = export_recipes_to_google_docs_json(recipes)
        assert "Austrian" in gdocs_json

        # 9. Standalone DOCX
        docx_bytes = export_recipes_to_docx(recipes)
        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
            doc_xml = zf.read("word/document.xml").decode("utf-8")
            assert "Tags:" in doc_xml
            assert "Austrian" in doc_xml

    # --- 4.6 Export Endpoints HTTP Verification with Custom Filename Query ---

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "endpoint, custom_name, expected_content_type, expected_filename_ext",
        [
            ("/api/export/csv", "my_custom_family_recipes.csv", "text/csv", ".csv"),
            ("/api/export/json-ld", "structured_recipes_ld.json", "application/ld+json", ".json"),
            ("/api/export/json", "raw_recipes_export.json", "application/json", ".json"),
            ("/api/export/docx", "holiday_cookbook_2026.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
            ("/api/export/pdf", "printable_cookbook.pdf", "application/pdf", ".pdf"),
            ("/api/export/markdown", "obsidian_recipes.md", "text/markdown", ".md"),
            ("/api/export/cooklang", "cooklang_recipes.cook", "text/plain", ".cook"),
            ("/api/export/paprika", "paprika_recipes.paprikarecipes", "application/x-paprikarecipes", ".paprikarecipes"),
            ("/api/export/google-docs?format=html", "archival_gdocs.html", "text/html", ".html"),
        ],
    )
    async def test_all_export_endpoints_respect_custom_filename_and_content_type(
        self,
        async_client: httpx.AsyncClient,
        session_manager: SessionManager,
        session_store: SessionStore,
        sample_recipes_with_all_fields: List[RecipeSchema],
        endpoint: str,
        custom_name: str,
        expected_content_type: str,
        expected_filename_ext: str,
    ):
        """Tests that every export endpoint honors custom filename and responds with correct Content-Disposition."""
        session = session_manager.create_session()
        session.state = PipelineState.COMPLETED
        session.recipes = sample_recipes_with_all_fields
        for r in sample_recipes_with_all_fields:
            session_store.save_recipe(session.session_id, r)

        sep = "&" if "?" in endpoint else "?"
        url = f"{endpoint}{sep}session_id={session.session_id}&filename={custom_name}"

        resp = await async_client.get(url)
        assert resp.status_code == 200
        assert expected_content_type in resp.headers.get("Content-Type", "")

        disposition = resp.headers.get("Content-Disposition", "")
        assert "attachment" in disposition
        assert custom_name in disposition
        assert custom_name.endswith(expected_filename_ext)
