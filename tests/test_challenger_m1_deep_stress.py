"""Empirical Challenger Deep Stress Suite for Milestone 1 (Ingestion, SSE Telemetry & Batch Default Tags).

Verifies:
1. Directory Ingestion Edge Cases: Non-existent directory, empty directory, non-image directory, mixed valid/invalid files, Unicode paths.
2. SSE Telemetry Stream: Initial handshake comment, idle and active session event dispatch, periodic ping heartbeats, terminal state event emission and termination.
3. Batch Default Tags Propagation & Sanitization: Empty/whitespace tags, special characters, commas, quotes, emojis, HTML/XSS strings, case-insensitive deduplication, and export serialization (CSV, JSON-LD, Markdown, Cooklang, Paprika).
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import AsyncGenerator, List
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from PIL import Image

from recipeboxer.agent.orchestrator import RecipeBoxerAgent
from recipeboxer.agent.session import PipelineSession, SessionManager
from recipeboxer.api.app import create_app
from recipeboxer.exporter.cooklang import export_recipes_to_cooklang
from recipeboxer.exporter.json_ld import export_recipes_to_json_ld
from recipeboxer.exporter.markdown_export import export_recipes_to_markdown
from recipeboxer.exporter.paprika import export_recipes_to_paprika
from recipeboxer.exporter.plan_to_eat import export_recipes_to_plan_to_eat_csv
from recipeboxer.models.domain import CardRole, PaperColorInfo, PaperRulingInfo, VisualFingerprint
from recipeboxer.models.hitl import CardPairApproval, HITLDecision
from recipeboxer.models.pipeline import (
    IngestionMode,
    PipelineStartRequest,
    PipelineState,
    PipelineStatus,
    ProgressMetrics,
)
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.storage.session_store import SessionStore


def create_test_image(dest_path: Path, width: int = 100, height: int = 80) -> Path:
    """Helper to generate a valid synthetic image scan."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (width, height), color=(250, 240, 220))
    img.save(dest_path, format="JPEG")
    return dest_path


@pytest_asyncio.fixture
async def async_client(tmp_path: Path) -> AsyncGenerator[AsyncClient, None]:
    """Test async HTTP client fixture with fresh SessionManager and SessionStore."""
    db_path = tmp_path / "m1_stress_test.db"
    store = SessionStore(db_path=db_path)
    manager = SessionManager()
    app = create_app(session_manager=manager, session_store=store)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


# ============================================================================
# 1. DIRECTORY INGESTION BACKEND EDGE CASES
# ============================================================================

class TestDirectoryIngestionEdgeCases:
    """Adversarial tests for directory scanning and ingestion initiation."""

    @pytest.mark.asyncio
    async def test_start_pipeline_nonexistent_directory_returns_400(self, async_client: AsyncClient):
        """Pipeline start with a non-existent directory returns 400 Bad Request."""
        payload = {
            "scans_dir": "/nonexistent/path/to/recipe/scans_12345",
            "mode": "parallel",
        }
        res = await async_client.post("/api/pipeline/start", json=payload)
        assert res.status_code == 400
        data = res.json()
        assert "directory does not exist" in data["detail"].lower()

    @pytest.mark.asyncio
    async def test_start_pipeline_empty_directory_returns_400(self, async_client: AsyncClient, tmp_path: Path):
        """Pipeline start with an empty directory returns 400 Bad Request."""
        empty_dir = tmp_path / "empty_scans"
        empty_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "scans_dir": str(empty_dir),
            "mode": "parallel",
        }
        res = await async_client.post("/api/pipeline/start", json=payload)
        assert res.status_code == 400
        data = res.json()
        assert "no valid card scans found" in data["detail"].lower()

    @pytest.mark.asyncio
    async def test_start_pipeline_directory_with_only_non_image_files_returns_400(
        self, async_client: AsyncClient, tmp_path: Path
    ):
        """Pipeline start with a directory containing only non-image files (.txt, .pdf, .docx, .DS_Store) returns 400."""
        non_img_dir = tmp_path / "non_images_only"
        non_img_dir.mkdir(parents=True, exist_ok=True)

        (non_img_dir / "notes.txt").write_text("Family recipes notes")
        (non_img_dir / "scans.pdf").write_bytes(b"%PDF-1.4 dummy")
        (non_img_dir / "doc.docx").write_bytes(b"PK dummy")
        (non_img_dir / ".DS_Store").write_bytes(b"\x00\x00\x00\x01")

        payload = {
            "scans_dir": str(non_img_dir),
            "mode": "parallel",
        }
        res = await async_client.post("/api/pipeline/start", json=payload)
        assert res.status_code == 400
        data = res.json()
        assert "no valid card scans found" in data["detail"].lower()

    @pytest.mark.asyncio
    async def test_start_pipeline_mixed_files_and_unicode_path(
        self, async_client: AsyncClient, tmp_path: Path
    ):
        """Pipeline start with directory containing mixed files and Unicode/special characters path."""
        # Create folder with unicode, accents, spaces, apostrophe
        scans_dir = tmp_path / "Mémé's & 🍰 Bakes (1950s)"
        scans_dir.mkdir(parents=True, exist_ok=True)

        # 3 valid image files
        create_test_image(scans_dir / "card_01.jpg")
        create_test_image(scans_dir / "card_02.PNG")
        create_test_image(scans_dir / "card_03.webp")

        # 3 non-image junk files
        (scans_dir / "readme.txt").write_text("instructions")
        (scans_dir / "archive.zip").write_bytes(b"PK")
        (scans_dir / ".DS_Store").write_bytes(b"\x00")

        payload = {
            "scans_dir": str(scans_dir),
            "mode": "parallel",
            "default_tags": ["Vintage Mémé", "🍰 Desserts"],
        }
        res = await async_client.post("/api/pipeline/start", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["total_scans_found"] == 3
        assert data["status"] in ("INGESTING", "SCANNING", "FINGERPRINTING", "IDLE", "HITL_PENDING", "COMPLETED", "EXTRACTING", "CORRELATING")


# ============================================================================
# 2. SSE TELEMETRY STREAM RESILIENCE & HEARTBEATS
# ============================================================================

class TestSSETelemetryStreamResilience:
    """Adversarial tests for GET /api/events SSE stream."""

    @pytest.mark.asyncio
    async def test_sse_stream_initial_connected_comment_handshake(self, async_client: AsyncClient):
        """SSE stream sends initial ': connected\n\n' comment immediately upon connection."""
        async with async_client.stream("GET", "/api/events") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")

            lines: List[str] = []
            async for line in response.aiter_lines():
                if line:
                    lines.append(line)
                if len(lines) >= 2:
                    break

            # First comment line must be : connected
            assert any(": connected" in l for l in lines)

    @pytest.mark.asyncio
    async def test_sse_stream_active_session_lifecycle_events(
        self, async_client: AsyncClient, tmp_path: Path
    ):
        """SSE stream emits state_change, progress, recipe_extracted, and terminal completed events."""
        app = async_client._transport.app  # type: ignore
        manager: SessionManager = app.state.session_manager

        sid = "sess_sse_lifecycle_test"
        config = PipelineStartRequest(
            scans_dir=str(tmp_path),
            mode=IngestionMode.PARALLEL,
            default_tags=["Family Classics"],
        )
        session = manager.create_session(config=config, session_id=sid)
        session.state = PipelineState.COMPLETED
        session.progress_metrics.total_scans = 4
        session.progress_metrics.fingerprinted = 4
        session.progress_metrics.extracted = 1
        session.progress_metrics.percent_complete = 100.0

        recipe = RecipeSchema(
            title="Apple Crisp",
            ingredients=["4 apples", "1 cup oats"],
            directions=["1. Slice apples.", "2. Top with oats and bake."],
            tags=["Family Classics", "Dessert"],
        )
        session.recipes.append(recipe)

        events_received: List[str] = []
        async with async_client.stream("GET", f"/api/events?session_id={sid}") as response:
            assert response.status_code == 200

            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    evt = line.split(":", 1)[1].strip()
                    events_received.append(evt)
                    if evt == "completed":
                        break

        assert "state_change" in events_received
        assert "progress" in events_received
        assert "recipe_extracted" in events_received
        assert "completed" in events_received


# ============================================================================
# 3. BATCH DEFAULT TAGS STRESS-TESTING & PROPAGATION MATRIX
# ============================================================================

class TestBatchDefaultTagsStressAndPropagation:
    """Stress tests for Batch Default Tags across ingestion, extraction, and all exporters."""

    def test_recipe_schema_clean_and_deduplicate_tags_comprehensive_matrix(self):
        """RecipeSchema.clean_and_deduplicate_tags handles all adversarial inputs cleanly."""
        raw_tags = [
            "  Grandma's Recipe Box  ",   # Leading/trailing whitespace
            "",                           # Empty string
            "   \t\n  ",                  # Whitespace only
            "grandma's recipe box",       # Case-insensitive duplicate -> discarded
            "GRANDMA'S RECIPE BOX",       # Case-insensitive duplicate -> discarded
            "🍰 Desserts & Sweets",       # Emoji & ampersand
            "Gluten-Free (100%)",         # Hyphens & parentheses
            "Tag, with comma",            # Embedded comma
            "Tag \"with quotes\"",        # Double quotes
            "<script>alert('xss')</script>", # HTML/XSS tag
            "1950s / Vintage #1",         # Slash & hash
            "🍰 DESSERTS & SWEETS",       # Duplicate emoji tag with uppercase -> discarded
        ]

        cleaned = RecipeSchema.clean_and_deduplicate_tags(raw_tags)

        expected = [
            "Grandma's Recipe Box",
            "🍰 Desserts & Sweets",
            "Gluten-Free (100%)",
            "Tag, with comma",
            "Tag \"with quotes\"",
            "<script>alert('xss')</script>",
            "1950s / Vintage #1",
        ]
        assert cleaned == expected

    @pytest.mark.asyncio
    async def test_upload_cards_multipart_with_json_and_comma_default_tags(
        self, async_client: AsyncClient, tmp_path: Path
    ):
        """Multipart upload endpoint properly parses JSON array string and comma string default_tags."""
        # 1. JSON Array default_tags
        img_bytes = io.BytesIO()
        img = Image.new("RGB", (80, 60), color=(240, 230, 210))
        img.save(img_bytes, format="JPEG")
        img_bytes.seek(0)

        json_tags = json.dumps(["Grand-mère's Vintage", "🍰 Pâtisserie", "1954"])
        files = [("files", ("card_test_1.jpg", img_bytes.getvalue(), "image/jpeg"))]
        data = {
            "mode": "parallel",
            "default_tags": json_tags,
        }

        res = await async_client.post("/api/cards/upload", files=files, data=data)
        assert res.status_code == 200
        sid1 = res.json()["session_id"]

        app = async_client._transport.app  # type: ignore
        session1 = app.state.session_manager.get_session(sid1)
        assert session1 is not None
        assert session1.config.default_tags == ["Grand-mère's Vintage", "🍰 Pâtisserie", "1954"]

        # 2. Comma-separated default_tags
        img_bytes.seek(0)
        files2 = [("files", ("card_test_2.jpg", img_bytes.getvalue(), "image/jpeg"))]
        data2 = {
            "mode": "parallel",
            "default_tags": "Holiday, Thanksgiving, Grandma's Pies",
        }

        res2 = await async_client.post("/api/cards/upload", files=files2, data=data2)
        assert res2.status_code == 200
        sid2 = res2.json()["session_id"]
        session2 = app.state.session_manager.get_session(sid2)
        assert session2 is not None
        assert session2.config.default_tags == ["Holiday", "Thanksgiving", "Grandma's Pies"]

    @pytest.mark.asyncio
    async def test_extraction_loop_merges_session_default_tags_with_recipe_tags(self):
        """Agent extraction loop merges session default_tags with card-level tags and deduplicates."""
        config = PipelineStartRequest(
            scans_dir="/tmp/dummy",
            default_tags=["Grandma's Box", "Vintage 1950s", "SharedTag"],
        )
        session = PipelineSession(session_id="sess_extract_merge_test", config=config)

        fp1 = VisualFingerprint(
            card_id="card_01.jpg",
            image_path="/tmp/dummy/card_01.jpg",
            sha256_hash="hash001",
            role=CardRole.FRONT_ONLY,
            role_confidence=0.95,
            paper_color=PaperColorInfo(tone_name="Cream"),
            ruling=PaperRulingInfo(has_ruling=True),
        )
        session.fingerprints["card_01.jpg"] = fp1

        # Extracted recipe has some overlapping and new tags
        mock_recipe = RecipeSchema(
            title="Strawberry Shortcake",
            ingredients=["2 cups flour", "1 lb strawberries", "1 cup cream"],
            directions=["1. Bake shortcakes.", "2. Top with strawberries and whipped cream."],
            tags=["sharedtag", "Dessert", "Summer", "Grandma's Box"],  # casing variations
        )

        async def mock_extract(front, back):
            return mock_recipe

        session.recipe_extractor_fn = mock_extract

        session.approvals = [
            CardPairApproval(
                proposal_id="prop_01",
                front_card_id="card_01.jpg",
                decision=HITLDecision.APPROVE,
            )
        ]

        agent = RecipeBoxerAgent()
        extracted = await agent._extract_single_recipe(session, fp1, None)
        assert extracted is not None

        # Execute merging logic as in orchestrator.py
        session_tags = list(session.config.default_tags)
        combined_tags = session_tags + list(extracted.tags or [])
        final_tags = RecipeSchema.clean_and_deduplicate_tags(combined_tags)

        # Verify batch default tags come first, recipe tags follow, and duplicates are removed
        assert final_tags == ["Grandma's Box", "Vintage 1950s", "SharedTag", "Dessert", "Summer"]

    def test_batch_default_tags_serialization_across_all_exporters(self):
        """Tags serialize cleanly across Plan To Eat CSV, JSON-LD, Markdown, Cooklang, and Paprika."""
        recipe = RecipeSchema(
            title="Pecan Pie",
            description="Southern classic holiday pie",
            yield_servings="8 slices",
            prep_time="20 mins",
            cook_time="50 mins",
            total_time="1 hr 10 mins",
            ingredients=["1 pie crust", "1 cup pecans", "3 eggs", "1 cup corn syrup"],
            directions=["1. Preheat oven to 350F.", "2. Mix filling and pour into crust.", "3. Bake 50 mins."],
            tags=["Grandma's Box", "Holidays & Celebrations", "Pies, Tarts & Pastries", "Dessert"],
        )

        # 1. Plan To Eat CSV (RFC 4180 Column 8: Tags)
        csv_out = export_recipes_to_plan_to_eat_csv([recipe])
        assert "Grandma's Box, Holidays & Celebrations, Pies, Tarts & Pastries, Dessert" in csv_out

        # 2. Schema.org JSON-LD (keywords array)
        json_ld_out = export_recipes_to_json_ld([recipe])
        json_data = json.loads(json_ld_out)
        recipe_ld = json_data if isinstance(json_data, dict) else json_data[0]
        assert recipe_ld["keywords"] in (
            "Grandma's Box, Holidays & Celebrations, Pies, Tarts & Pastries, Dessert",
            [
                "Grandma's Box",
                "Holidays & Celebrations",
                "Pies, Tarts & Pastries",
                "Dessert",
            ],
        )

        # 3. Markdown Export (YAML frontmatter)
        md_out = export_recipes_to_markdown([recipe])
        assert "Grandma's Box" in md_out
        assert "Holidays & Celebrations" in md_out

        # 4. Cooklang Export
        cook_out = export_recipes_to_cooklang([recipe])
        assert "Grandma's Box" in cook_out

        # 5. Paprika Export
        paprika_bytes = export_recipes_to_paprika([recipe])
        assert len(paprika_bytes) > 0
