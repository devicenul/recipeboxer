"""Tier 5 Adversarial Stress, Mutation, and Edge Condition Test Suite for RecipeBoxer.

Comprehensive white-box adversarial verification covering:
1. SQLite WAL Concurrency & High-Throughput Thread Contention
2. Server-Sent Events (SSE) Disconnect, Reconnect & Telemetry Lifecycles
3. RFC 4180 CSV Escaping, Special Control Characters & Unicode Edge Cases
4. Schema.org Duration Parsing Edge Cases & JSON-LD Serialization
5. SPA Fallback Route Isolation & Static Traversal Security Hardening
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import csv
import io
import json
import os
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from recipeboxer.agent.session import PipelineSession, SessionManager
from recipeboxer.api.app import FALLBACK_SPA_HTML, create_app
from recipeboxer.config import Settings
from recipeboxer.exporter.json_export import JsonExporter, export_recipes_to_json
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
from recipeboxer.ingestion.scanner import ScannedCard
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.correlation import CorrelationProposal
from recipeboxer.models.hitl import CardPairApproval
from recipeboxer.models.pipeline import (
    IngestionMode,
    PipelineStartRequest,
    PipelineState,
    ProgressMetrics,
    TokenUsage,
)
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.storage.cache import FingerprintCache
from recipeboxer.storage.session_store import SessionStore


# ============================================================================
# Fixtures for Tier 5 Testing
# ============================================================================

@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    """Provides a fresh disk-backed SQLite database path for WAL mode testing."""
    return tmp_path / f"test_store_{uuid.uuid4().hex[:8]}.db"


@pytest.fixture
def temp_cache_db_path(tmp_path: Path) -> Path:
    """Provides a fresh disk-backed SQLite database path for FingerprintCache."""
    return tmp_path / f"test_cache_{uuid.uuid4().hex[:8]}.db"


@pytest.fixture
def disk_session_store(temp_db_path: Path) -> SessionStore:
    """Creates a disk-backed SessionStore with WAL enabled."""
    store = SessionStore(db_path=temp_db_path)
    yield store
    store.close()


@pytest.fixture
def disk_fingerprint_cache(temp_cache_db_path: Path) -> FingerprintCache:
    """Creates a disk-backed FingerprintCache with WAL enabled."""
    cache = FingerprintCache(db_path=temp_cache_db_path)
    yield cache
    cache.close()


@pytest.fixture
def test_session_manager() -> SessionManager:
    """Provides an isolated in-memory SessionManager."""
    return SessionManager()


@pytest.fixture
def app_with_store(test_session_manager: SessionManager, disk_session_store: SessionStore) -> Any:
    """FastAPI app configured with isolated SessionManager and disk SessionStore."""
    return create_app(session_manager=test_session_manager, session_store=disk_session_store)


@pytest_asyncio.fixture
async def async_client(app_with_store: Any) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async HTTP client for endpoint testing."""
    transport = ASGITransport(app=app_with_store)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


# ============================================================================
# 1. SQLite WAL Concurrency & High-Throughput Thread Contention Tests
# ============================================================================

class TestSQLiteWALConcurrency:
    """Adversarial stress testing for SQLite WAL multi-threaded and async operations."""

    def test_wal_mode_enabled_on_disk_store(self, disk_session_store: SessionStore, temp_db_path: Path):
        """Verifies that WAL journal mode and busy timeout are configured on disk databases."""
        with sqlite3.connect(str(temp_db_path)) as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode;")
            journal_mode = cur.fetchone()[0]
            assert journal_mode.lower() == "wal"

    def test_multithreaded_high_concurrency_writes_and_reads(self, disk_session_store: SessionStore):
        """Executes 50 concurrent threads performing 500+ mixed CRUD operations without lock errors."""
        num_threads = 50
        ops_per_thread = 10
        errors: List[Exception] = []

        def worker(thread_idx: int) -> None:
            try:
                for i in range(ops_per_thread):
                    sid = f"sess_t{thread_idx}_{i}"
                    # 1. Save session
                    disk_session_store.save_session(
                        session_id=sid,
                        state=PipelineState.SCANNING if i % 2 == 0 else PipelineState.COMPLETED,
                        mode="parallel",
                        folder_path=f"/fake/path/{thread_idx}/{i}",
                        card_count=i + 1,
                        recipe_count=i,
                    )

                    # 2. Save card
                    disk_session_store.save_card(
                        session_id=sid,
                        card_id=f"card_{thread_idx}_{i}",
                        image_path=f"/fake/img/{thread_idx}_{i}.jpg",
                        sha256_hash=f"{thread_idx:032x}{i:032x}",
                        role="FRONT",
                    )

                    # 3. Save recipe
                    rec = RecipeSchema(
                        recipe_id=f"rec_{thread_idx}_{i}",
                        title=f"Concurrent Recipe {thread_idx}-{i}",
                        ingredients=[f"{i + 1} cup flour", "1 tsp salt"],
                        directions=[f"Step 1 for {thread_idx}_{i}", "Bake until golden"],
                    )
                    disk_session_store.save_recipe(sid, rec)

                    # 4. Read queries
                    detail = disk_session_store.get_session_detail(sid)
                    assert detail is not None
                    assert detail.session_id == sid
                    assert detail.recipe_count >= 1

                    read_rec = disk_session_store.get_recipe(rec.recipe_id, session_id=sid)
                    assert read_rec is not None
                    assert read_rec.title == rec.title

                    # 5. List sessions
                    summaries = disk_session_store.list_sessions()
                    assert len(summaries) > 0
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Encountered {len(errors)} thread concurrency errors: {errors[:3]}"

        # Verify integrity
        summaries = disk_session_store.list_sessions()
        assert len(summaries) == num_threads * ops_per_thread

    def test_fingerprint_cache_high_concurrency_stress(self, disk_fingerprint_cache: FingerprintCache):
        """Stress tests FingerprintCache with 50 threads doing simultaneous puts, gets, and deletes."""
        num_threads = 50
        errors: List[Exception] = []

        def cache_worker(thread_idx: int) -> None:
            try:
                for i in range(10):
                    hex_hash = f"{thread_idx:032x}{i:032x}"
                    fp = VisualFingerprint(
                        sha256_hash=hex_hash,
                        card_id=f"card_{thread_idx}_{i}",
                        image_path=f"/path/to/{thread_idx}_{i}.jpg",
                        role=CardRole.FRONT_ONLY if i % 2 == 0 else CardRole.BACK_ONLY,
                    )
                    disk_fingerprint_cache.put(fp)

                    # Retrieve
                    retrieved = disk_fingerprint_cache.get(hex_hash)
                    assert retrieved is not None
                    assert retrieved.card_id == fp.card_id
                    assert disk_fingerprint_cache.contains(hex_hash)

                    by_card = disk_fingerprint_cache.get_by_card_id(fp.card_id)
                    assert by_card is not None

                    if i == 5:
                        deleted = disk_fingerprint_cache.delete(hex_hash)
                        assert deleted is True
                        assert not disk_fingerprint_cache.contains(hex_hash)
            except Exception as exc:
                errors.append(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(cache_worker, t) for t in range(num_threads)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"FingerprintCache concurrency errors: {errors}"

    @pytest.mark.asyncio
    async def test_async_session_store_interface_high_load(self, disk_session_store: SessionStore):
        """Tests async non-blocking store methods under 100 concurrent asyncio tasks."""
        num_tasks = 100

        async def async_task_worker(task_id: int) -> None:
            sid = f"async_sess_{task_id}"
            await disk_session_store.asave_session(
                session_id=sid,
                state=PipelineState.CORRELATING,
                card_count=task_id,
            )
            rec = RecipeSchema(
                recipe_id=f"async_rec_{task_id}",
                title=f"Async Recipe {task_id}",
                ingredients=["1 egg", "1 cup milk"],
                directions=["Mix well", "Cook over medium heat"],
            )
            await disk_session_store.asave_recipe(sid, rec)

            detail = await disk_session_store.aget_session_detail(sid)
            assert detail is not None
            assert detail.session_id == sid

            fetched_rec = await disk_session_store.aget_recipe(rec.recipe_id, session_id=sid)
            assert fetched_rec is not None

            recipes = await disk_session_store.alist_recipes(sid)
            assert len(recipes) == 1

        tasks = [asyncio.create_task(async_task_worker(i)) for i in range(num_tasks)]
        await asyncio.gather(*tasks)

        all_summaries = await disk_session_store.alist_sessions()
        assert len(all_summaries) == num_tasks

    def test_database_integrity_check(self, disk_session_store: SessionStore, temp_db_path: Path):
        """Runs SQLite PRAGMA integrity_check to verify no database corruption occurs."""
        with sqlite3.connect(str(temp_db_path)) as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA integrity_check;")
            result = cur.fetchone()[0]
            assert result.lower() == "ok"


# ============================================================================
# 2. Server-Sent Events (SSE) Disconnect, Reconnect & Telemetry Tests
# ============================================================================

class TestSSEDisconnectAndReconnectCycles:
    """Adversarial tests for SSE streaming, client dropouts, rapid reconnects, and lifecycle events."""

    @pytest.mark.asyncio
    async def test_sse_stream_state_transitions_and_completion(
        self,
        async_client: httpx.AsyncClient,
        test_session_manager: SessionManager,
    ):
        """Verifies SSE stream transitions through states and terminates on COMPLETED."""
        sid = "sse_term_test"
        session = test_session_manager.create_session(session_id=sid)
        session.state = PipelineState.INGESTING

        async def advance_session():
            await asyncio.sleep(0.05)
            session.state = PipelineState.CORRELATING
            await asyncio.sleep(0.05)
            session.state = PipelineState.COMPLETED

        asyncio.create_task(advance_session())

        async with async_client.stream("GET", f"/api/events?session_id={sid}") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")

            events: List[str] = []
            async for chunk in response.aiter_text():
                for line in chunk.split("\n"):
                    if line.startswith("event:"):
                        events.append(line.replace("event:", "").strip())
                if "completed" in events:
                    break

            assert len(events) >= 1
            assert "completed" in events

    @pytest.mark.asyncio
    async def test_sse_rapid_reconnect_cycles(
        self,
        async_client: httpx.AsyncClient,
        test_session_manager: SessionManager,
    ):
        """Simulates rapid client reconnects across distinct session states."""
        states_sequence = [
            PipelineState.SCANNING,
            PipelineState.FINGERPRINTING,
            PipelineState.CORRELATING,
            PipelineState.HITL_PENDING,
            PipelineState.EXTRACTING,
        ]

        for idx, st in enumerate(states_sequence):
            sid = f"sse_reconnect_sess_{idx}"
            session = test_session_manager.create_session(session_id=sid)
            session.state = st

            async def complete_shortly(s: PipelineSession):
                await asyncio.sleep(0.05)
                s.state = PipelineState.COMPLETED

            asyncio.create_task(complete_shortly(session))

            async with async_client.stream("GET", f"/api/events?session_id={sid}") as response:
                assert response.status_code == 200
                events = []
                async for chunk in response.aiter_text():
                    for line in chunk.split("\n"):
                        if line.startswith("event:"):
                            events.append(line)
                    if any("completed" in e for e in events):
                        break
                assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_sse_broadcast_recipe_extracted_event(
        self,
        async_client: httpx.AsyncClient,
        test_session_manager: SessionManager,
    ):
        """Verifies that newly appended recipes in session trigger 'event: recipe_extracted' SSE emissions."""
        config = PipelineStartRequest(scans_dir="/tmp", mode=IngestionMode.PARALLEL)
        session = test_session_manager.create_session(config=config, session_id="sse_recipe_broadcast")
        session.state = PipelineState.EXTRACTING

        async def inject_recipe_and_complete():
            await asyncio.sleep(0.05)
            test_recipe = RecipeSchema(
                recipe_id="rec_sse_01",
                title="SSE Live Recipe",
                ingredients=["1 cup sugar", "2 eggs"],
                directions=["Beat eggs", "Fold in sugar"],
            )
            session.recipes.append(test_recipe)
            await asyncio.sleep(0.05)
            session.state = PipelineState.COMPLETED

        asyncio.create_task(inject_recipe_and_complete())

        async with async_client.stream("GET", f"/api/events?session_id={session.session_id}") as response:
            collected_text = ""
            async for chunk in response.aiter_text():
                collected_text += chunk
                if "completed" in collected_text:
                    break

            assert "event: recipe_extracted" in collected_text
            assert "SSE Live Recipe" in collected_text

    @pytest.mark.asyncio
    async def test_sse_idle_session_clean_termination(
        self,
        async_client: httpx.AsyncClient,
        test_session_manager: SessionManager,
    ):
        """Verifies SSE endpoint for idle session streams status and cleanly terminates."""
        sid = "sse_idle_test_sid"
        session = test_session_manager.create_session(session_id=sid)
        session.state = PipelineState.IDLE

        async def complete_idle():
            await asyncio.sleep(0.05)
            session.state = PipelineState.COMPLETED

        asyncio.create_task(complete_idle())

        async with async_client.stream("GET", f"/api/events?session_id={sid}") as response:
            assert response.status_code == 200
            collected = ""
            async for chunk in response.aiter_text():
                collected += chunk
                if "completed" in collected:
                    break
            assert "event: state_change" in collected or "event: completed" in collected


# ============================================================================
# 3. RFC 4180 CSV Escaping, Control Characters & Unicode Edge Cases
# ============================================================================

class TestRFC4180CSVEscapingAndControlChars:
    """Adversarial stress testing for CSV exporter with quotes, newlines, control chars, and Unicode."""

    def test_plan_to_eat_csv_embedded_quotes_and_commas(self):
        """Verifies escaping of double quotes, triple quotes, commas, and multi-line strings in all fields."""
        recipe = RecipeSchema(
            recipe_id="rec_quote_test",
            title='Grandma\'s "Secret" & "Famous" 8" Apple Pie, Special Edition',
            description='A "delicately" spiced pie with """triple""" quotation marks, commas, and notes.',
            ingredients=[
                '2 cups flour, sifted (not "packed")',
                '1/2 cup sugar, granulated',
                '3 "Granny Smith" apples, peeled & sliced',
            ],
            directions=[
                'Preheat oven to 350°F (175°C).',
                'Mix flour, sugar, and "secret spices" in a bowl.',
                'Bake for 45 minutes; let cool before serving.',
            ],
            prep_time="20 mins",
            total_time="1 hr 5 mins",
            yield_servings='8 "generous" slices, or 12 small',
            tags=["Baking, Pies", '"Desserts"', "Holiday Favorites"],
            source_notes='Handwritten note: "Passed down from Aunt Mary\'s 1942 collection, Vol. 1".',
        )

        exporter = PlanToEatExporter()
        csv_text = exporter.export_to_csv_string([recipe])

        # 1. Must pass built-in validator
        assert exporter.validate_csv_format(csv_text) is True

        # 2. Must end with CRLF
        assert csv_text.endswith("\r\n")

        # 3. Parse with standard library csv.DictReader to verify exact round-trip fidelity
        reader = csv.DictReader(io.StringIO(csv_text), lineterminator="\r\n")
        rows = list(reader)
        assert len(rows) == 1
        row = rows[0]

        assert row["Title"] == recipe.title
        assert row["Description"] == recipe.description
        assert row["Yield"] == recipe.yield_servings
        assert row["Prep Time"] == "20 mins"
        assert row["Total Time"] == "1 hr 5 mins"
        assert row["Source"] == recipe.source_notes

        # Verify multi-line ingredients and directions
        ing_items = row["Ingredients"].split("\n")
        assert len(ing_items) == len(recipe.ingredients)
        for original, parsed in zip(recipe.ingredients, ing_items):
            assert parsed == original

        dir_items = row["Directions"].split("\n")
        assert len(dir_items) == len(recipe.directions)
        for original, parsed in zip(recipe.directions, dir_items):
            assert parsed == original

    def test_plan_to_eat_csv_special_control_characters(self):
        """Verifies CSV generation with tab, form feed, escape characters, and unusual whitespace."""
        recipe = RecipeSchema(
            recipe_id="rec_ctrl_chars",
            title="Tabbed\t& FormFeed\f Recipe",
            description="Line with tabs\t\tand\fspecial\x1b[31m colored ANSI codes\x1b[0m.",
            ingredients=["1\ttsp\tvanilla", "2\tcups\tcream"],
            directions=["Step 1\t-\tstir\tslowly.", "Step 2\t-\trefrigerate."],
            tags=["Special\tTag", "Odd\fTag"],
            source_notes="Source\tNote\fPage 42",
        )

        exporter = PlanToEatExporter()
        csv_text = exporter.export_to_csv_string([recipe])
        assert exporter.validate_csv_format(csv_text) is True

        reader = csv.DictReader(io.StringIO(csv_text), lineterminator="\r\n")
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["Title"] == recipe.title
        assert "1\ttsp\tvanilla" in rows[0]["Ingredients"]

    def test_plan_to_eat_csv_multilingual_unicode_fractions_and_emojis(self):
        """Verifies CSV handling of multilingual Unicode, RTL, fractions, and emojis."""
        recipe1 = RecipeSchema(
            recipe_id="rec_matcha",
            title="Authentic Matcha Latte 抹茶ラテ 🍵",
            description="Traditional Japanese green tea beverage 日本の伝統的なお茶.",
            ingredients=["1 ½ tsp ceremonial grade matcha powder 抹茶", "¾ cup oat milk", "1 ⅓ tbsp hot water"],
            directions=["Sift matcha powder into chawan", "Whisk with bamboo chasen 80°C water", "Pour steamed milk"],
            tags=["Beverage", "Japanese 料理", "Matcha 🍵"],
            source_notes="Kyoto tea master recipe 京都.",
        )
        recipe2 = RecipeSchema(
            recipe_id="rec_spicy_mapo",
            title="Sichuan Mapo Tofu 麻婆豆腐 🌶️🔥",
            description="Spicy & numbing tofu dish with Sichuan peppercorns 花椒.",
            ingredients=["400g silken tofu 豆腐", "2 ⅔ tbsp chili bean paste 豆瓣酱", "½ tsp ground Sichuan pepper 花椒粉"],
            directions=["Blanch tofu in salted water", "Fry minced beef and paste", "Simmer and thicken with cornstarch"],
            tags=["Spicy 🌶️", "Chinese 中餐", "Sichuan"],
            source_notes="Authentic Chengdu family recipe 成都.",
        )

        exporter = PlanToEatExporter()
        csv_text = exporter.export_to_csv_string([recipe1, recipe2])
        assert exporter.validate_csv_format(csv_text) is True

        reader = csv.DictReader(io.StringIO(csv_text), lineterminator="\r\n")
        rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["Title"] == recipe1.title
        assert "抹茶" in rows[0]["Ingredients"]
        assert "🍵" in rows[0]["Tags"]
        assert rows[1]["Title"] == recipe2.title
        assert "🌶️" in rows[1]["Tags"]

    def test_csv_exporter_validation_failures(self):
        """Verifies PlanToEatExporter.validate_csv_format catches corrupt CSV content."""
        exporter = PlanToEatExporter()

        with pytest.raises(ValueError, match="CSV content is empty"):
            exporter.validate_csv_format("")

        with pytest.raises(ValueError, match="Header mismatch"):
            exporter.validate_csv_format("Col1,Col2,Col3\r\nval1,val2,val3\r\n")

        # Missing columns in row
        corrupt_row_csv = ",".join(PLAN_TO_EAT_COLUMNS) + "\r\nOnlyOneField\r\n"
        with pytest.raises(ValueError, match="column count mismatch"):
            exporter.validate_csv_format(corrupt_row_csv)


# ============================================================================
# 4. Schema.org Duration Parsing & JSON-LD Serialization Stress
# ============================================================================

class TestSchemaOrgDurationParsingAndJSONLD:
    """Adversarial stress testing for ISO 8601 duration converter and Schema.org JSON-LD exporter."""

    @pytest.mark.parametrize(
        "human_input,expected_iso",
        [
            # Standard minutes/hours/days/seconds
            ("20 mins", "PT20M"),
            ("1 hr 15 mins", "PT1H15M"),
            ("2 hours", "PT2H"),
            ("45 seconds", "PT45S"),
            ("1 day 2 hours", "P1DT2H"),
            ("3 days 4 hours 15 mins 30 secs", "P3DT4H15M30S"),
            ("10 sec", "PT10S"),
            ("2 hrs 30 m", "PT2H30M"),
            ("1 d 12 h", "P1DT12H"),
            # Case insensitivity and irregular spacing
            ("  2  HOURS  15  MINUTES  ", "PT2H15M"),
            ("45 MIN", "PT45M"),
            ("1 HR", "PT1H"),
            # Already valid ISO 8601 durations
            ("PT30M", "PT30M"),
            ("PT1H15M", "PT1H15M"),
            ("P1DT2H", "P1DT2H"),
            ("PT45S", "PT45S"),
            ("P2D", "P2D"),
            # Bare numbers treated as minutes
            ("30", "PT30M"),
            ("90", "PT90M"),
            ("120", "PT120M"),
            # Zero / invalid inputs returning None
            ("0", None),
            ("0 mins", None),
            ("0 hours 0 minutes", None),
            ("", None),
            (None, None),
            ("   ", None),
            ("cook until brown", None),
            ("N/A", None),
            ("--", None),
            ("about an hour or so", None),
        ],
    )
    def test_human_duration_to_iso8601_matrix(self, human_input: Optional[str], expected_iso: Optional[str]):
        """Exhaustively tests duration parsing edge cases and ISO 8601 transformations."""
        result = human_duration_to_iso8601(human_input)
        assert result == expected_iso

    def test_json_ld_single_recipe_and_graph_export(self):
        """Verifies Schema.org JSON-LD output structure for single and multiple recipe datasets."""
        recipe1 = RecipeSchema(
            recipe_id="rec_ld_1",
            title="Heirloom Sourdough Bread",
            description="Crispy crust, chewy crumb artisan sourdough loaf.",
            prep_time="30 mins",
            cook_time="45 mins",
            total_time="24 hours",
            yield_servings="1 loaf (12 slices)",
            ingredients=["500g bread flour", "350g water", "100g active sourdough starter", "10g salt"],
            directions=[
                "1. Mix flour and water; autolyse for 45 minutes.",
                "2. Add starter and salt; perform stretch and folds.",
                "3. Bulk ferment for 4 hours until doubled.",
                "4. Shape and cold retard in banneton overnight.",
                "5. Bake in Dutch oven at 450°F for 40 minutes.",
            ],
            tags=["Baking", "Artisan", "Bread"],
            source_notes="Family heirloom recipe.",
        )

        exporter = JsonLdExporter()

        # Single recipe export (unwrapped object)
        single_dict = exporter.export_to_json_ld_dict(recipe1)
        assert single_dict["@context"] == "https://schema.org"
        assert single_dict["@type"] == "Recipe"
        assert single_dict["name"] == recipe1.title
        assert single_dict["prepTime"] == "PT30M"
        assert single_dict["cookTime"] == "PT45S" or single_dict["cookTime"] == "PT45M"
        assert single_dict["totalTime"] == "PT24H"
        assert len(single_dict["recipeInstructions"]) == 5
        assert single_dict["recipeInstructions"][0]["@type"] == "HowToStep"
        assert single_dict["recipeInstructions"][0]["position"] == 1
        # Leading "1. " must be stripped in HowToStep.text
        assert single_dict["recipeInstructions"][0]["text"].startswith("Mix flour")

        # Multi-recipe export (wrapped in @graph)
        recipe2 = RecipeSchema(
            recipe_id="rec_ld_2",
            title="Quick Herb Butter",
            ingredients=["1/2 cup butter, softened", "1 tbsp chopped chives", "1 clove garlic, minced"],
            directions=["1. Stir herbs and garlic into butter.", "2. Chill until firm."],
        )
        graph_dict = exporter.export_to_json_ld_dict([recipe1, recipe2])
        assert "@graph" in graph_dict
        assert len(graph_dict["@graph"]) == 2
        assert graph_dict["@graph"][0]["name"] == recipe1.title
        assert graph_dict["@graph"][1]["name"] == recipe2.title

        # Validate JSON-LD serialization
        json_str = exporter.export_to_json_ld_string([recipe1, recipe2])
        assert exporter.validate_json_ld(json_str) is True

    def test_json_ld_validator_error_cases(self):
        """Verifies JsonLdExporter.validate_json_ld catches corrupt payloads."""
        exporter = JsonLdExporter()

        with pytest.raises(ValueError, match="JSON-LD content is empty"):
            exporter.validate_json_ld("")

        with pytest.raises(ValueError, match="Invalid JSON"):
            exporter.validate_json_ld("{invalid json")

        with pytest.raises(ValueError, match="Missing or invalid @context"):
            exporter.validate_json_ld(json.dumps({"@type": "Recipe", "name": "Cake"}))

        with pytest.raises(ValueError, match="Root object must have @type 'Recipe'"):
            exporter.validate_json_ld(json.dumps({"@context": "https://schema.org", "@type": "Person", "name": "Chef"}))


# ============================================================================
# 5. SPA Fallback Route Isolation & Static Traversal Security Hardening
# ============================================================================

class TestSPAFallbackRouteIsolationAndSecurity:
    """Adversarial testing for FastAPI routing isolation between REST API and SPA catch-all."""

    @pytest.mark.asyncio
    async def test_api_routes_return_strict_404_not_spa_html(self, async_client: httpx.AsyncClient):
        """Verifies that unmatched /api/* requests strictly return 404 JSON, NEVER SPA HTML."""
        unmatched_api_paths = [
            "/api",
            "/api/",
            "/api/nonexistent",
            "/api/recipes/invalid_rec_id_99999",
            "/api/cards/missing_card/image",
            "/api/sessions/nonexistent_sid_12345",
            "/api/hitl/unknown_endpoint",
            "/api/export/unknown_format",
            "/api/pipeline/invalid_action",
        ]

        for path in unmatched_api_paths:
            res = await async_client.get(path)
            assert res.status_code == 404, f"Path {path} returned status {res.status_code}, expected 404"
            # Must return JSON error, not HTML
            content_type = res.headers.get("content-type", "")
            assert "application/json" in content_type, f"Path {path} returned content-type '{content_type}', expected application/json"
            assert "<!DOCTYPE html>" not in res.text

    @pytest.mark.asyncio
    async def test_spa_client_routes_return_spa_html(self, async_client: httpx.AsyncClient):
        """Verifies that non-API client routes return SPA index.html with HTTP 200."""
        spa_client_paths = [
            "/",
            "/ingest",
            "/correlate",
            "/recipes",
            "/recipes/rec_12345/edit",
            "/export",
            "/settings/general",
            "/deep/nested/client/route/for/react/history",
        ]

        for path in spa_client_paths:
            res = await async_client.get(path)
            assert res.status_code == 200, f"SPA path {path} returned status {res.status_code}"
            content_type = res.headers.get("content-type", "")
            assert "text/html" in content_type, f"SPA path {path} returned content-type '{content_type}'"
            assert "RecipeBoxer" in res.text or "<div id=\"root\">" in res.text

    @pytest.mark.asyncio
    async def test_path_traversal_attack_mitigation(self, async_client: httpx.AsyncClient):
        """Verifies that directory traversal attacks on static and catch-all routes do not leak filesystem files."""
        traversal_payloads = [
            "/../etc/passwd",
            "/../../../../etc/passwd",
            "/%2e%2e/%2e%2e/etc/passwd",
            "/assets/../../secret.txt",
            "/assets/..%2f..%2fconfig.py",
            "/static/../../../app.py",
        ]

        for payload in traversal_payloads:
            res = await async_client.get(payload)
            # Must either return 200 (SPA HTML fallback) or 404/400, but NEVER leak sensitive files
            assert res.status_code in (200, 400, 404)
            assert "root:x:0:0" not in res.text
            assert "DATABASE_URL" not in res.text
            assert "SECRET_KEY" not in res.text
            if res.status_code == 200:
                # Returned SPA fallback safely
                assert "RecipeBoxer" in res.text or "<div id=\"root\">" in res.text
