"""Comprehensive tests for WP-XC: Domain Normalizers (X7), No-Key State (X12), and SSE Spend Event.

Authoritative contracts: WAVE5_CONTRACTS.md §2 (Extraction interfaces), §3 (WP-XC).
Rules: WAVE3_CONTRACTS.md §0:
- Fully offline (no Gemini API calls, no external networks).
- Never assert accuracy offline.
- Uses hand-written synthetic test cases, NEVER corpus recipes.
- Wraps any async wait in asyncio.wait_for(..., timeout=...).
"""

import asyncio
import io
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import httpx
from httpx import ASGITransport
from PIL import Image
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from recipeboxer.agent.session import PipelineSession, SessionManager
from recipeboxer.ai.cost import CostLedger, PriceTable, UsageTokens
from recipeboxer.api.app import create_app
from recipeboxer.config import Settings
from recipeboxer.extraction.normalize import (
    normalize_ingredient_line,
    normalize_recipe,
    normalize_step,
    parse_unambiguous_quantity,
    parse_unambiguous_unit,
)
from recipeboxer.ingestion.capture import read_capture_meta
from recipeboxer.ingestion.scanner import ScannedCard, validate_and_load_card
from recipeboxer.models.failures import FailureReason, StageFailure
from recipeboxer.models.pipeline import IngestionMode, PipelineState, PipelineStatus
from recipeboxer.models.recipe import (
    IngredientLine,
    Legibility,
    Recipe,
    RecipeSection,
    RecipeWarning,
    Step,
    TextSpan,
    TimeValue,
    YieldValue,
)
from recipeboxer.sources.local import LocalSourceProvider
from recipeboxer.vision.pixel_features import compute_pixel_features


# ============================================================================
# PART 1: Domain Normalizers (X7) Hand-written Unit Tests
# ============================================================================

class TestWPXCDomainNormalizers:
    """Test suite verifying fraction, archaic unit, quantity parsing, and noise filtering."""

    def test_unambiguous_quantity_parsing(self):
        """Verifies unambiguous numeric quantity parsing vs ambiguous/range values."""
        # Unambiguous fractions and mixed numbers
        assert parse_unambiguous_quantity("1½") == 1.5
        assert parse_unambiguous_quantity("1 1/2") == 1.5
        assert parse_unambiguous_quantity("1-1/2") == 1.5
        assert parse_unambiguous_quantity("1/4") == 0.25
        assert parse_unambiguous_quantity("3/4") == 0.75
        assert parse_unambiguous_quantity("2") == 2.0
        assert parse_unambiguous_quantity("0.5") == 0.5
        assert parse_unambiguous_quantity(3) == 3.0
        assert parse_unambiguous_quantity(2.25) == 2.25

        # Ambiguous / ranges / descriptive quantities should return None
        assert parse_unambiguous_quantity("to taste") is None
        assert parse_unambiguous_quantity("a pinch") is None
        assert parse_unambiguous_quantity("pinch") is None
        assert parse_unambiguous_quantity("1 to 2") is None
        assert parse_unambiguous_quantity("1 or 2") is None
        assert parse_unambiguous_quantity("1-2") is None
        assert parse_unambiguous_quantity("1/4 - 1/2") is None
        assert parse_unambiguous_quantity("few drops") is None
        assert parse_unambiguous_quantity("") is None
        assert parse_unambiguous_quantity(None) is None

    def test_unambiguous_unit_parsing(self):
        """Verifies canonical unit resolution."""
        assert parse_unambiguous_unit("c.") == "cup"
        assert parse_unambiguous_unit("cups") == "cup"
        assert parse_unambiguous_unit("T.") == "tbsp"
        assert parse_unambiguous_unit("tbsp.") == "tbsp"
        assert parse_unambiguous_unit("t.") == "tsp"
        assert parse_unambiguous_unit("tsp") == "tsp"
        assert parse_unambiguous_unit("oz.") == "oz"
        assert parse_unambiguous_unit("lbs") == "lb"
        assert parse_unambiguous_unit("gill") == "gill"
        assert parse_unambiguous_unit("teacup") == "teacup"

    def test_normalize_handwritten_fractions_and_archaic_measurements(self):
        """Test with hand-written synthetic cases ('1½', '1 1/2', 'gill', 'teacup', 'butter the size of an egg')."""
        recipe = Recipe(
            title="Aunt Clara's Heirloom Tea Cake",
            sections=[
                RecipeSection(
                    name="Cake",
                    ingredients=[
                        IngredientLine(
                            original_text="1½ cups sifted flour",
                            quantity="1½",
                            unit="cups",
                            item="sifted flour",
                        ),
                        IngredientLine(
                            original_text="1 1/2 tsp baking powder",
                            quantity="1 1/2",
                            unit="tsp",
                            item="baking powder",
                        ),
                        IngredientLine(
                            original_text="1 gill sweet milk",
                            quantity="1",
                            unit="gill",
                            item="sweet milk",
                        ),
                        IngredientLine(
                            original_text="1 teacup cane sugar",
                            quantity="1",
                            unit="teacup",
                            item="cane sugar",
                        ),
                        IngredientLine(
                            original_text="butter the size of an egg",
                            item="butter",
                        ),
                        IngredientLine(
                            original_text="a pinch of nutmeg",
                            item="nutmeg",
                        ),
                    ],
                    steps=[
                        Step(
                            original_text="Cut butter the size of an egg into the flour.",
                        ),
                        Step(
                            original_text="Stir in 1 gill sweet milk and bake 45 mins at 350°.",
                        ),
                    ],
                )
            ],
            notes=[
                TextSpan(text="Passed down on index card from 1902."),
            ],
            prep_time=TimeValue(original_text="15 mins"),
            cook_time=TimeValue(original_text="45 mins"),
            yield_value=YieldValue(original_text="8 servings"),
        )

        normalized = normalize_recipe(recipe)

        ings = normalized.sections[0].ingredients

        # 1. '1½' test
        assert ings[0].original_text == "1½ cups sifted flour"  # Original NEVER altered
        assert "1 1/2" in ings[0].normalized_text
        assert ings[0].normalized_quantity == 1.5
        assert ings[0].normalized_unit == "cup"

        # 2. '1 1/2' test
        assert ings[1].original_text == "1 1/2 tsp baking powder"  # Original NEVER altered
        assert ings[1].normalized_quantity == 1.5
        assert ings[1].normalized_unit == "tsp"

        # 3. 'gill' test -> original kept, normalized note (1/2 cup)
        assert ings[2].original_text == "1 gill sweet milk"  # Original NEVER altered
        assert "1 gill (1/2 cup)" in ings[2].normalized_text
        assert "whole milk (sweet milk)" in ings[2].normalized_text  # saleratus/sweet milk archaic expansion
        assert ings[2].normalized_unit == "gill"

        # 4. 'teacup' test -> original kept, normalized note (3/4 cup)
        assert ings[3].original_text == "1 teacup cane sugar"  # Original NEVER altered
        assert "1 teacup (3/4 cup)" in ings[3].normalized_text
        assert ings[3].normalized_unit == "teacup"

        # 5. 'butter the size of an egg' test -> original kept, normalized note (size of an egg)
        assert ings[4].original_text == "butter the size of an egg"  # Original NEVER altered
        assert "1/4 cup butter (size of an egg)" in ings[4].normalized_text
        assert ings[4].normalized_quantity == 0.25
        assert ings[4].normalized_unit == "cup"

        # 6. Ambiguous quantity: 'a pinch of nutmeg'
        assert ings[5].original_text == "a pinch of nutmeg"
        assert ings[5].normalized_quantity is None  # Ambiguous remains None

        # Verify step normalization
        steps = normalized.sections[0].steps
        assert steps[0].original_text == "Cut butter the size of an egg into the flour."
        assert "1/4 cup butter (size of an egg)" in steps[0].normalized_text

        assert steps[1].original_text == "Stir in 1 gill sweet milk and bake 45 mins at 350°."
        assert "1 gill (1/2 cup)" in steps[1].normalized_text
        assert "whole milk (sweet milk)" in steps[1].normalized_text

        # Verify time and yield normalization
        assert normalized.prep_time.normalized_minutes == 15
        assert normalized.prep_time.normalized_iso == "PT15M"
        assert normalized.cook_time.normalized_minutes == 45
        assert normalized.cook_time.normalized_iso == "PT45M"
        assert normalized.yield_value.normalized_servings == 8.0

    def test_filter_advertisement_noise_moved_to_notes_with_warning(self):
        """Verifies promotional clipping noise is moved to notes with ADVERTISEMENT_NOISE warning and not deleted."""
        recipe = Recipe(
            title="Vintage Newspaper Prune Pudding",
            sections=[
                RecipeSection(
                    name="Pudding",
                    ingredients=[
                        IngredientLine(original_text="2 cups cooked prunes, pitted"),
                        IngredientLine(original_text="CLIP AND SAVE THIS VALUABLE RECIPE"),
                        IngredientLine(original_text="1/2 cup chopped walnuts"),
                        IngredientLine(original_text="Save 25¢ with coupon on back page"),
                    ],
                    steps=[
                        Step(original_text="Mash prunes with walnuts."),
                        Step(original_text="Coupon expires November 30, 1948."),
                        Step(original_text="Bake in buttered dish 30 minutes."),
                    ],
                )
            ],
            notes=[
                TextSpan(text="From Morning Herald 1948."),
            ],
        )

        normalized = normalize_recipe(recipe)

        sec = normalized.sections[0]
        # Only culinary ingredients should remain in the section
        assert len(sec.ingredients) == 2
        assert [i.original_text for i in sec.ingredients] == [
            "2 cups cooked prunes, pitted",
            "1/2 cup chopped walnuts",
        ]

        # Only culinary steps should remain in the section
        assert len(sec.steps) == 2
        assert [s.original_text for s in sec.steps] == [
            "Mash prunes with walnuts.",
            "Bake in buttered dish 30 minutes.",
        ]

        # Promotional lines must NOT be deleted: moved to notes
        note_texts = [n.text for n in normalized.notes]
        assert "CLIP AND SAVE THIS VALUABLE RECIPE" in note_texts
        assert "Save 25¢ with coupon on back page" in note_texts
        assert "Coupon expires November 30, 1948." in note_texts
        assert "From Morning Herald 1948." in note_texts

        # Warnings must record each moved promotional element
        ad_warnings = [w for w in normalized.warnings if w.code == "ADVERTISEMENT_NOISE"]
        assert len(ad_warnings) >= 3
        assert any("CLIP AND SAVE" in w.message for w in ad_warnings)
        assert any("Save 25¢" in w.message for w in ad_warnings)
        assert any("Coupon expires" in w.message for w in ad_warnings)


# ============================================================================
# PART 2: X12 No-Key State Tests
# ============================================================================

class TestWPXCX12NoKeyState:
    """Verifies that scanning, thumbnails, pixel features, and metadata work without API key,

    while analysis/extraction is blocked with clear status ('analysis_blocked_reason: NO_API_KEY').
    """

    @pytest.fixture
    def test_image_card(self, tmp_path: Path) -> Path:
        """Creates a synthetic recipe card image file with known EXIF metadata."""
        img_path = tmp_path / "card_face_01.jpg"
        im = Image.new("RGB", (400, 300), color=(245, 240, 225))  # Ruled aged substrate
        # Draw some dark simulated ink pixels
        for x in range(50, 350, 10):
            for y in range(80, 250, 20):
                im.putpixel((x, y), (30, 40, 70))
        im.save(img_path, format="JPEG")
        return img_path

    def test_offline_scanning_works_without_api_key(self, test_image_card: Path):
        """Verifies card scanning, hashing, and loading succeed completely offline with no key."""
        with patch.dict(os.environ, {}, clear=True):
            card = validate_and_load_card(test_image_card, natural_sort_index=0)
            assert isinstance(card, ScannedCard)
            assert card.filename == "card_face_01.jpg"
            assert card.sha256_hash is not None
            assert len(card.sha256_hash) == 64
            assert card.width == 400
            assert card.height == 300

    @pytest.mark.asyncio
    async def test_offline_thumbnails_work_without_api_key(self, test_image_card: Path):
        """Verifies thumbnail generation succeeds with zero LLM or network dependency."""
        with patch.dict(os.environ, {}, clear=True):
            provider = LocalSourceProvider()
            thumb_bytes = await provider.fetch_thumbnail(str(test_image_card), max_size=128)
            assert thumb_bytes is not None
            assert len(thumb_bytes) > 0
            # Confirm it's a valid JPEG thumbnail
            thumb_im = Image.open(io.BytesIO(thumb_bytes))
            assert max(thumb_im.size) <= 128

    def test_offline_pixel_features_work_without_api_key(self, test_image_card: Path):
        """Verifies deterministic pixel feature extraction functions without a Gemini key."""
        with patch.dict(os.environ, {}, clear=True):
            feats = compute_pixel_features(test_image_card)
            assert feats.paper_lab[0] > 0.0
            assert feats.aspect_ratio > 0.0
            assert isinstance(feats.ink_hues, list)

    def test_offline_capture_metadata_works_without_api_key(self, test_image_card: Path):
        """Verifies capture metadata (EXIF/mtime) functions offline with no key."""
        with patch.dict(os.environ, {}, clear=True):
            meta = read_capture_meta(test_image_card, natural_sort_index=5)
            assert meta.natural_sort_index == 5
            assert meta.taken_at is not None
            assert meta.source in ("EXIF", "mtime", "FILE_MTIME")

    @pytest.mark.asyncio
    async def test_status_endpoint_reports_no_api_key_and_onboarding_hint(self):
        """Tests that GET /api/status returns analysis_blocked_reason='NO_API_KEY'

        and an onboarding hint pointing to /api/settings/api-key when no key is configured.
        """
        # Ensure environment has no API key
        with patch.dict(os.environ, {"GEMINI_API_KEY": "", "RECIPEBOXER_GEMINI_API_KEY": ""}, clear=True):
            manager = SessionManager()
            app = create_app(session_manager=manager)
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.get("/api/status")
                assert res.status_code == 200
                data = res.json()
                assert data["status"] == "IDLE"
                assert data.get("analysis_blocked_reason") == "NO_API_KEY"
                assert "/api/settings/api-key" in (data.get("onboarding_hint") or "")

    @pytest.mark.asyncio
    async def test_session_status_with_no_key_failure_reports_clear_block(self):
        """Tests that a session that failed due to NO_API_KEY returns clear blocked status."""
        manager = SessionManager()
        session = manager.create_session(config={"mode": "parallel"})
        no_key_fail = StageFailure(
            stage="FINGERPRINT",
            reason=FailureReason.NO_API_KEY,
            message="No API key configured for Gemini analysis.",
            retryable=False,
        )
        session.add_failure(no_key_fail)
        session.state = PipelineState.FAILED

        app = create_app(session_manager=manager)
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get(f"/api/status?session_id={session.session_id}")
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "FAILED"
            assert data.get("analysis_blocked_reason") == "NO_API_KEY"
            assert "/api/settings/api-key" in (data.get("onboarding_hint") or "")


# ============================================================================
# PART 3: SSE 'spend' Event Tests (using TestClient)
# ============================================================================

class TestWPXCSSESpendEvent:
    """Verifies that the /api/events SSE stream emits 'spend' events when CostLedger snapshot changes."""

    def test_sse_stream_emits_spend_event_on_cost_change(self, tmp_path: Path):
        """Tests that SSE stream emits 'spend' event with CostLedger snapshot using TestClient."""
        pricing_toml = tmp_path / "pricing.toml"
        pricing_toml.write_text(
            """
["gemini-3.7-flash"]
input_per_mtok = 0.075
output_per_mtok = 0.30
thinking_per_mtok = 0.30
verified_on = "2026-03-01"
source_url = "https://ai.google.dev/pricing"
"""
        )
        prices = PriceTable.load(pricing_toml)
        ledger = CostLedger(prices=prices)
        ledger.record(
            stage="FINGERPRINT",
            model="gemini-3.7-flash",
            usage=UsageTokens(input_tokens=10_000, output_tokens=5_000, thinking_tokens=0),
            latency_ms=250.0,
        )
        assert ledger.total_usd > 0.0

        manager = SessionManager()
        session = manager.create_session(config={"mode": "parallel"})
        session.ledger = ledger
        session.state = PipelineState.COMPLETED

        app = create_app(session_manager=manager)
        client = TestClient(app)

        lines: List[str] = []
        with client.stream("GET", f"/api/events?session_id={session.session_id}") as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                lines.append(line)

        stream_content = "\n".join(lines)
        assert "event: spend" in stream_content

        spend_data = None
        for i, line in enumerate(lines):
            if line.strip() == "event: spend" and i + 1 < len(lines):
                data_line = lines[i + 1]
                if data_line.startswith("data:"):
                    spend_data = json.loads(data_line[len("data:"):].strip())
                    break

        assert spend_data is not None
        assert spend_data["session_id"] == session.session_id
        assert spend_data["spend_usd"] == pytest.approx(ledger.total_usd, rel=1e-4)
        assert "snapshot" in spend_data
        assert spend_data["snapshot"]["total_calls"] == 1
        assert "by_stage" in spend_data["snapshot"]

    def test_sse_stream_fallback_to_session_spend_usd_field(self):
        """Tests that SSE stream also emits 'spend' event when session.spend_usd is set."""
        manager = SessionManager()
        session = manager.create_session(config={"mode": "parallel"})
        session.spend_usd = 0.045
        session.state = PipelineState.COMPLETED

        app = create_app(session_manager=manager)
        client = TestClient(app)

        lines: List[str] = []
        with client.stream("GET", f"/api/events?session_id={session.session_id}") as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                lines.append(line)

        stream_content = "\n".join(lines)
        assert "event: spend" in stream_content

        spend_data = None
        for i, line in enumerate(lines):
            if line.strip() == "event: spend" and i + 1 < len(lines):
                data_line = lines[i + 1]
                if data_line.startswith("data:"):
                    spend_data = json.loads(data_line[len("data:"):].strip())
                    break

        assert spend_data is not None
        assert spend_data["session_id"] == session.session_id
        assert spend_data["spend_usd"] == 0.045
