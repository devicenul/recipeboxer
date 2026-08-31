"""Tier 5 Adversarial Cross-Module Lifecycle & Integration Stress Test Suite.

Authoritative Requirements:
- Scenario A: Ingest 10 cards with baseline .recipeboxer -> verify duplicate skipping
  -> HITL review new cards -> extract -> export curated DOCX & PDF with Course preset & dot-leader TOC
  -> save session as .recipeboxer -> merge into another project -> import into fresh session
  -> verify 100% data losslessness.
- Scenario B: Corrupted/tampered archives, concurrent exports under heavy load,
  extreme titles with special characters/Unicode, empty sections, and multiple contributor normalizations.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import csv
import hashlib
import io
import json
import os
import shutil
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from PIL import Image

from recipeboxer.agent.orchestrator import RecipeBoxerAgent
from recipeboxer.agent.session import PipelineSession, SessionManager
from recipeboxer.api.app import create_app
from recipeboxer.config import Settings
from recipeboxer.exporter.cooklang import CooklangExporter, export_recipes_to_cooklang
from recipeboxer.exporter.curation import (
    COURSE_TAXONOMY,
    CuratedCookbook,
    CuratedSection,
    GroupingPreset,
    _extract_contributor_name,
    _normalize_title_for_sorting,
    curate_recipes,
)
from recipeboxer.exporter.docx_export import (
    DocxExporter,
    export_recipes_to_docx,
)
from recipeboxer.exporter.google_docs import (
    GoogleDocsExporter,
    export_recipes_to_google_docs_html,
    export_recipes_to_google_docs_json,
)
from recipeboxer.exporter.json_export import JsonExporter, export_recipes_to_json
from recipeboxer.exporter.json_ld import (
    JsonLdExporter,
    export_recipes_to_json_ld,
)
from recipeboxer.exporter.markdown_export import (
    MarkdownExporter,
    export_recipes_to_markdown,
)
from recipeboxer.exporter.paprika import PaprikaExporter, export_recipes_to_paprika
from recipeboxer.exporter.pdf_export import (
    PdfExporter,
    export_recipes_to_pdf,
)
from recipeboxer.exporter.plan_to_eat import (
    PlanToEatExporter,
    export_recipes_to_plan_to_eat_csv,
)
from recipeboxer.ingestion.runner import IngestionConfig, IngestionResult, IngestionRunner
from recipeboxer.ingestion.scanner import ScannedCard, compute_file_sha256
from recipeboxer.models.archive import (
    ArchiveAssetRecord,
    ArchiveCardRecord,
    ArchiveRecipeRecord,
    MarginaliaEntry,
    RecipeBoxerArchiveManifest,
    StepImageBinding,
)
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.correlation import CorrelationProposal
from recipeboxer.models.hitl import (
    CardPairApproval,
    CorrelationCandidate,
    HITLDecision,
)
from recipeboxer.models.pipeline import (
    PipelineStartRequest,
    PipelineState,
    ProgressMetrics,
)
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.models.session import SessionDetail
from recipeboxer.storage.archive import (
    create_recipeboxer_archive,
    extract_recipeboxer_archive,
    merge_recipeboxer_archives,
    restore_archive_to_session,
    validate_archive_manifest,
)
from recipeboxer.storage.cache import FingerprintCache
from recipeboxer.storage.session_store import SessionStore


# ============================================================================
# Helpers
# ============================================================================

def make_test_jpeg_bytes(marker: int, width: int = 400, height: int = 300) -> bytes:
    """Generates synthetic JPEG bytes with distinct visual colors and deterministic byte content."""
    r = (marker * 37) % 256
    g = (marker * 73) % 256
    b = (marker * 109) % 256
    img = Image.new("RGB", (width, height), color=(r, g, b))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ============================================================================
# SCENARIO A: Full 10-Card E2E Cross-Module Integration Lifecycle
# ============================================================================

class TestScenarioAFullLifecycle:
    """Scenario A: Ingest 10 cards with baseline .recipeboxer -> duplicate skipping ->

    HITL review new cards -> extract -> export curated DOCX & PDF with Course preset & dot-leader TOC
    -> save session as .recipeboxer -> merge into another project -> import into fresh session
    -> verify 100% data losslessness.
    """

    @pytest.mark.asyncio
    async def test_scenario_a_complete_lifecycle_and_losslessness(self, tmp_path: Path):
        # --------------------------------------------------------------------
        # Step 1: Create Baseline Project Archive with 5 Cards & 2 Recipes
        # --------------------------------------------------------------------
        baseline_dir = tmp_path / "baseline_setup"
        baseline_dir.mkdir(parents=True, exist_ok=True)
        baseline_scans_dir = baseline_dir / "scans"
        baseline_scans_dir.mkdir()

        db_path = baseline_dir / "baseline.db"
        store = SessionStore(db_path=str(db_path))
        cache = FingerprintCache(db_path=str(db_path))
        manager = SessionManager()
        settings = Settings(
            cache_db_path=str(db_path),
            cache_dir=str(baseline_dir / "cache"),
            export_dir=str(baseline_dir / "exports"),
        )

        base_cards: List[ScannedCard] = []
        base_fps: Dict[str, VisualFingerprint] = {}
        base_card_files: List[Tuple[Path, str, bytes]] = []

        # 5 Baseline Cards (IDs: card_base_01 through card_base_05)
        # Cards 1 & 2 -> Recipe 1 ("Grandma's Sourdough Loaf", Course: Breads & Baking)
        # Card 3 -> Recipe 2 ("Aunt Clara's Spiced Apple Tart", Course: Desserts & Sweets)
        # Cards 4 & 5 -> Standalone supporting scans
        for i in range(1, 6):
            data = make_test_jpeg_bytes(i, width=600, height=400)
            file_path = baseline_scans_dir / f"card_base_{i:02d}.jpg"
            file_path.write_bytes(data)
            sha = hashlib.sha256(data).hexdigest().lower()
            base_card_files.append((file_path, sha, data))

            cid = f"card_base_{i:02d}"
            role = CardRole.SEQUENCE_START if i == 1 else (CardRole.SEQUENCE_CONTINUATION if i == 2 else CardRole.STANDALONE)
            card = ScannedCard(
                card_id=cid,
                file_path=file_path,
                filename=f"card_base_{i:02d}.jpg",
                sha256_hash=sha,
                file_size_bytes=len(data),
                width=600,
                height=400,
            )
            base_cards.append(card)

            fp = VisualFingerprint(
                card_id=cid,
                image_path=str(file_path),
                sha256_hash=sha,
                role=role,
                role_confidence=0.98,
                recipe_title_candidate=f"Baseline Candidate {i}",
            )
            base_fps[cid] = fp
            cache.put(fp)

        # Baseline Recipe 1: 2-card recipe with step image bindings and marginalia
        rec_base_1 = ArchiveRecipeRecord(
            recipe_id="rec_base_sourdough",
            title="Grandma's Country Sourdough Loaf",
            description="Traditional 2-day wild fermented artisan loaf with open crumb.",
            yield_servings="2 round boules",
            prep_time="45 mins",
            cook_time="40 mins",
            total_time="36 hours",
            ingredients=[
                "500g unbleached bread flour",
                "350g lukewarm filtered water (78°F)",
                "100g ripe active sourdough starter",
                "10g fine sea salt",
            ],
            directions=[
                "1. Autolyse flour and water in mixing bowl for 45 minutes.",
                "2. Incorporate active starter and salt using pinch-and-fold method.",
                "3. Perform 4 sets of stretch and folds spaced 30 minutes apart.",
                "4. Bulk ferment at 75°F until volume increases by 50%.",
                "5. Shape into round boules and cold proof in bannetons overnight.",
                "6. Bake in preheated Dutch oven at 475°F covered for 20 mins, then 425°F uncovered for 20 mins.",
            ],
            tags=["Breads", "Baking", "Sourdough", "Heirloom"],
            source_notes="From Great-Grandma Clara's 1934 Cedar Box",
            card_ids=["card_base_01", "card_base_02"],
            image_urls=["/api/cards/card_base_01/image", "/api/cards/card_base_02/image"],
            step_bindings=[
                StepImageBinding(
                    step_index=1,
                    card_id="card_base_01",
                    card_sha256=base_card_files[0][1],
                    marginalia_note="Autolyse note on card front top margin",
                ),
                StepImageBinding(
                    step_index=6,
                    card_id="card_base_02",
                    card_sha256=base_card_files[1][1],
                    marginalia_note="Baking temperature notation on back",
                ),
            ],
            marginalia=[
                MarginaliaEntry(
                    id="marg_base_01",
                    card_id="card_base_01",
                    card_sha256=base_card_files[0][1],
                    text="Best baked on a rainy autumn morning.",
                    location_quadrant="TOP_RIGHT",
                    category="family_story",
                    target_field="source_notes",
                )
            ],
        )

        # Baseline Recipe 2: 1-card dessert recipe
        rec_base_2 = ArchiveRecipeRecord(
            recipe_id="rec_base_apple_tart",
            title="Aunt Clara's Spiced Apple Tart",
            description="Delicate French-style apple tart with buttery shortcrust.",
            yield_servings="8 slices",
            prep_time="30 mins",
            cook_time="45 mins",
            total_time="1 hour 15 mins",
            ingredients=[
                "1 pâte brisée crust",
                "4 Honeycrisp apples, peeled and thinly sliced into crescents",
                "1/3 cup granulated sugar",
                "1 tsp ground Saigon cinnamon",
                "2 tbsp cold unsalted butter, cubed",
                "2 tbsp apricot jam, warmed for glaze",
            ],
            directions=[
                "1. Line 9-inch tart pan with chilled pastry dough.",
                "2. Arrange apple slices in tight concentric rosettes.",
                "3. Dust generously with cinnamon sugar and dot with butter cubes.",
                "4. Bake at 375°F for 45 minutes until pastry edges are golden.",
                "5. Brush warm apricot jam over hot apples for glossy finish.",
            ],
            tags=["Dessert", "Pastry", "Apples", "Autumn"],
            source_notes="Aunt Clara's Thanksgiving Notebook (1952)",
            card_ids=["card_base_03"],
            image_urls=["/api/cards/card_base_03/image"],
            step_bindings=[
                StepImageBinding(
                    step_index=2,
                    card_id="card_base_03",
                    card_sha256=base_card_files[2][1],
                )
            ],
        )

        base_session = manager.create_session(session_id="sess_baseline_scenario_a")
        base_session.cards = base_cards
        base_session.fingerprints = base_fps
        base_session.recipes = [rec_base_1, rec_base_2]
        base_session.state = PipelineState.COMPLETED
        store.save_session("sess_baseline_scenario_a", state=PipelineState.COMPLETED, card_count=5, recipe_count=2)
        store.save_recipe("sess_baseline_scenario_a", rec_base_1)
        store.save_recipe("sess_baseline_scenario_a", rec_base_2)

        baseline_archive_path = baseline_dir / "baseline_project.recipeboxer"
        create_recipeboxer_archive(
            base_session,
            store=store,
            manager=manager,
            cache=cache,
            output_path=baseline_archive_path,
            project_name="Baseline Archival Project A",
        )
        assert baseline_archive_path.is_file()

        # --------------------------------------------------------------------
        # Step 2: Prepare Ingestion Directory with 10 Cards
        # (5 Duplicate Cards matching baseline + 5 Brand-New Cards)
        # --------------------------------------------------------------------
        ingest_scan_dir = tmp_path / "scenario_a_scans_10"
        ingest_scan_dir.mkdir(parents=True, exist_ok=True)

        # 5 Duplicates (Exact SHA-256 byte match from baseline)
        for i, (card_file, sha, data) in enumerate(base_card_files, 1):
            (ingest_scan_dir / f"scan_dup_{i:02d}.jpg").write_bytes(data)

        # 5 Brand-New Cards (Cards 6 through 10)
        # Cards 6 & 7 -> New Recipe 3 ("Smoky Texas Brisket Chili", Course: Main Dishes)
        # Card 8 -> New Recipe 4 ("Crispy Garlic Parmesan Brussels Sprouts", Course: Side Dishes)
        # Cards 9 & 10 -> New Recipe 5 ("Festive Spiced Bourbon Eggnog", Course: Beverages & Drinks)
        new_card_data: List[Tuple[Path, str, bytes]] = []
        for i in range(6, 11):
            data = make_test_jpeg_bytes(i * 19, width=800, height=600)
            file_path = ingest_scan_dir / f"scan_new_{i:02d}.jpg"
            file_path.write_bytes(data)
            sha = hashlib.sha256(data).hexdigest().lower()
            new_card_data.append((file_path, sha, data))

        # --------------------------------------------------------------------
        # Step 3: Run Ingestion with Duplicate Skipping & Spies
        # --------------------------------------------------------------------
        vision_calls: List[str] = []
        ocr_calls: List[str] = []

        async def spy_vision_extractor(card: ScannedCard) -> VisualFingerprint:
            vision_calls.append(card.card_id)
            if "06" in card.filename:
                role = CardRole.SEQUENCE_START
            elif "07" in card.filename:
                role = CardRole.SEQUENCE_CONTINUATION
            elif "09" in card.filename:
                role = CardRole.SEQUENCE_START
            elif "10" in card.filename:
                role = CardRole.SEQUENCE_CONTINUATION
            else:
                role = CardRole.STANDALONE

            return VisualFingerprint(
                card_id=card.card_id,
                image_path=str(card.file_path),
                sha256_hash=card.sha256_hash,
                role=role,
                role_confidence=0.96,
                recipe_title_candidate=f"Extracted Title for {card.filename}",
            )

        async def spy_recipe_extractor(fps: Sequence[VisualFingerprint]) -> Optional[RecipeSchema]:
            ocr_calls.append(",".join(fp.card_id for fp in fps))
            card_ids = [fp.card_id for fp in fps]
            cids_str = " ".join(card_ids)

            if "06" in cids_str or "07" in cids_str:
                return RecipeSchema(
                    recipe_id="rec_new_brisket_chili",
                    title="Smoky Texas Brisket Chili",
                    description="Rich slow-simmered beef brisket chili with chipotles and dark beer.",
                    yield_servings="8 hearty bowls",
                    prep_time="30 mins",
                    cook_time="3 hours 30 mins",
                    total_time="4 hours",
                    ingredients=[
                        "3 lbs smoked beef brisket, cubed",
                        "2 yellow onions, diced",
                        "4 cloves garlic, minced",
                        "3 canned chipotle peppers in adobo",
                        "1/4 cup dark chili powder",
                        "1 bottle Shiner Bock beer",
                        "2 cans crushed tomatoes",
                    ],
                    directions=[
                        "1. Brown brisket in Dutch oven over medium heat.",
                        "2. Sauté onions, garlic, and chipotle peppers until aromatic.",
                        "3. Deglaze pot with dark beer and stir in crushed tomatoes.",
                        "4. Simmer uncovered on low heat for 3.5 hours until thick.",
                    ],
                    tags=["Main Dish", "Chili", "Beef", "Texas BBQ"],
                    source_notes="Uncle Bob's Texas Competition Recipe (1988)",
                    card_ids=card_ids,
                )
            elif "08" in cids_str:
                return RecipeSchema(
                    recipe_id="rec_new_brussels_sprouts",
                    title="Crispy Garlic Parmesan Brussels Sprouts",
                    description="Oven-roasted caramelized brussels sprouts with aged parmesan and balsamic drizzle.",
                    yield_servings="6 servings",
                    prep_time="15 mins",
                    cook_time="25 mins",
                    total_time="40 mins",
                    ingredients=[
                        "2 lbs fresh brussels sprouts, trimmed and halved",
                        "3 tbsp extra virgin olive oil",
                        "4 cloves garlic, finely minced",
                        "1/2 cup finely grated Parmigiano-Reggiano",
                        "2 tbsp aged balsamic glaze",
                        "1/2 tsp crushed red pepper flakes",
                    ],
                    directions=[
                        "1. Preheat oven and baking sheet to 425°F.",
                        "2. Toss halved brussels sprouts with olive oil, minced garlic, salt, and pepper.",
                        "3. Roast cut-side down on hot baking sheet for 20 minutes until deeply caramelized.",
                        "4. Toss with grated parmesan and return to oven for 5 minutes.",
                        "5. Drizzle with aged balsamic glaze before serving.",
                    ],
                    tags=["Side Dish", "Vegetables", "Roasted", "Holiday"],
                    source_notes="From the kitchen of Cousin Sarah (2018)",
                    card_ids=card_ids,
                )
            else:
                return RecipeSchema(
                    recipe_id="rec_new_bourbon_eggnog",
                    title="Festive Spiced Bourbon Eggnog",
                    description="Velvety homemade aged holiday eggnog infused with Kentucky bourbon and freshly grated nutmeg.",
                    yield_servings="12 cups",
                    prep_time="25 mins",
                    cook_time="10 mins",
                    total_time="4 hours (chilling)",
                    ingredients=[
                        "6 large egg yolks",
                        "3/4 cup granulated sugar",
                        "2 cups whole milk",
                        "1 cup heavy whipping cream",
                        "1/2 cup Kentucky straight bourbon whiskey",
                        "1/4 cup dark spiced rum",
                        "1 tsp freshly grated whole nutmeg",
                        "1 tsp pure vanilla extract",
                    ],
                    directions=[
                        "1. Whisk egg yolks and sugar in saucepan until thick and pale yellow.",
                        "2. Slowly whisk in whole milk over medium-low heat until mixture reaches 160°F.",
                        "3. Remove from heat and strain through fine-mesh sieve into clean bowl.",
                        "4. Stir in heavy cream, bourbon, spiced rum, nutmeg, and vanilla.",
                        "5. Chill in refrigerator for at least 4 hours. Serve dusted with extra nutmeg.",
                    ],
                    tags=["Beverage", "Cocktail", "Holiday", "Bourbon", "Winter"],
                    source_notes="Grandpa Joe's Christmas Punch Bowl (1965)",
                    card_ids=card_ids,
                )

        new_sess_dir = tmp_path / "scenario_a_new_session"
        new_sess_dir.mkdir()
        new_store = SessionStore(db_path=str(new_sess_dir / "new_store.db"))
        new_cache = FingerprintCache(db_path=str(new_sess_dir / "new_store.db"))
        new_manager = SessionManager()
        new_settings = Settings(
            cache_db_path=str(new_sess_dir / "new_store.db"),
            cache_dir=str(new_sess_dir / "cache"),
            export_dir=str(new_sess_dir / "exports"),
        )

        session = PipelineSession(
            session_id="sess_scenario_a_active",
            config=PipelineStartRequest(
                scans_dir=str(ingest_scan_dir),
                baseline_project_path=str(baseline_archive_path),
            ),
            settings=new_settings,
            cache=new_cache,
            extractor=spy_vision_extractor,
            recipe_extractor_fn=spy_recipe_extractor,
        )

        agent = RecipeBoxerAgent(
            settings=new_settings,
            cache=new_cache,
            visual_extractor=spy_vision_extractor,
            recipe_extractor=spy_recipe_extractor,
        )

        # Launch pipeline in background to handle HITL pause
        task = agent.run_in_background(session)

        # Wait for HITL pause
        for _ in range(100):
            if session.state == PipelineState.HITL_PENDING:
                break
            await asyncio.sleep(0.05)

        assert session.state == PipelineState.HITL_PENDING, f"Expected HITL_PENDING, got {session.state}"

        # --------------------------------------------------------------------
        # Verification 1: Duplicate Skipping Accuracy
        # --------------------------------------------------------------------
        assert session.progress_metrics.total_scans == 10
        assert session.progress_metrics.skipped_cards == 5
        assert session.progress_metrics.newly_processed_cards == 5
        assert session.progress_metrics.baseline_recipes_count == 2

        # Verify exact 0 vision calls on the 5 duplicate cards!
        assert len(vision_calls) == 5
        new_card_ids = [c.card_id for c in session.new_cards]
        assert set(vision_calls) == set(new_card_ids)
        for dup_card in session.skipped_cards:
            assert dup_card.card_id not in vision_calls

        # --------------------------------------------------------------------
        # Step 4: HITL Review & Approval of New Cards
        # --------------------------------------------------------------------
        # Identify new card IDs
        c06 = next(c.card_id for c in session.new_cards if "06" in c.filename)
        c07 = next(c.card_id for c in session.new_cards if "07" in c.filename)
        c08 = next(c.card_id for c in session.new_cards if "08" in c.filename)
        c09 = next(c.card_id for c in session.new_cards if "09" in c.filename)
        c10 = next(c.card_id for c in session.new_cards if "10" in c.filename)

        approvals = [
            CardPairApproval(
                proposal_id="prop_chili_2card",
                ordered_card_ids=[c06, c07],
                decision=HITLDecision.APPROVE,
            ),
            CardPairApproval(
                proposal_id="prop_brussels_1card",
                ordered_card_ids=[c08],
                decision=HITLDecision.APPROVE,
            ),
            CardPairApproval(
                proposal_id="prop_eggnog_2card",
                ordered_card_ids=[c09, c10],
                decision=HITLDecision.APPROVE,
            ),
        ]

        session.resume_with_approvals(approvals)
        await task

        assert session.state == PipelineState.COMPLETED
        assert len(session.recipes) == 5  # 2 baseline + 3 new extractions!
        assert len(ocr_calls) == 3  # Exactly 3 extraction calls for new approved proposals

        # --------------------------------------------------------------------
        # Step 5: Export Curated DOCX & PDF with Course Preset & Dot-Leader TOC
        # --------------------------------------------------------------------
        # Curate recipes using course taxonomy
        curated_cookbook = curate_recipes(
            session.recipes,
            grouping_preset=GroupingPreset.COURSE,
            include_section_dividers=True,
            include_toc=True,
            toc_title="Family Culinary Heritage Table of Contents",
        )

        assert curated_cookbook.grouping_preset == GroupingPreset.COURSE
        assert len(curated_cookbook.sections) >= 4
        section_titles = [s.title for s in curated_cookbook.sections]
        assert "Soups & Salads" in section_titles
        assert "Side Dishes" in section_titles
        assert "Breads & Baking" in section_titles
        assert "Desserts & Sweets" in section_titles
        assert "Beverages & Drinks" in section_titles

        # 5.1 Export Curated DOCX and Validate OpenXML Structures
        docx_exporter = DocxExporter()
        docx_bytes = docx_exporter.export_to_bytes(
            recipes=session.recipes,
            curation=curated_cookbook,
        )
        assert len(docx_bytes) > 2000

        with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as zf:
            namelist = zf.namelist()
            assert "word/document.xml" in namelist
            assert "word/styles.xml" in namelist
            doc_xml_str = zf.read("word/document.xml").decode("utf-8")

            # Check that XML is well-formed ElementTree
            doc_root = ET.fromstring(doc_xml_str)
            assert doc_root is not None

            # Verify dot-leader tab stop OpenXML attribute is present
            assert 'w:leader="dot"' in doc_xml_str
            assert "Family Culinary Heritage Table of Contents" in doc_xml_str
            assert "Country Sourdough Loaf" in doc_xml_str
            assert "Smoky Texas Brisket Chili" in doc_xml_str
            assert "Festive Spiced Bourbon Eggnog" in doc_xml_str

            # Verify decorative section divider headings are present
            assert "Soups &amp; Salads" in doc_xml_str
            assert "Desserts &amp; Sweets" in doc_xml_str
            assert "Breads &amp; Baking" in doc_xml_str

        # 5.2 Export Curated PDF and Validate 2-Pass Dot-Leader TOC & Dividers
        pdf_exporter = PdfExporter()
        pdf_bytes = pdf_exporter.export_to_bytes(
            recipes=session.recipes,
            curation=curated_cookbook,
        )
        assert pdf_bytes.startswith(b"%PDF-1.4")
        assert pdf_bytes.rstrip().endswith(b"%%EOF")

        # Decompress FlateDecode streams to verify PDF page content and TOC
        import zlib
        import re
        stream_matches = re.findall(b"stream\r?\n(.*?)\r?\nendstream", pdf_bytes, re.DOTALL)
        all_decompressed_text = ""
        for raw_stream in stream_matches:
            try:
                decompressed = zlib.decompress(raw_stream).decode("latin-1", errors="ignore")
                all_decompressed_text += decompressed + "\n"
            except Exception:
                pass

        assert "Family Culinary Heritage Table of Contents" in all_decompressed_text
        assert "Soups & Salads" in all_decompressed_text
        assert "Grandma" in all_decompressed_text

        # --------------------------------------------------------------------
        # Step 6: Save Active Session as .recipeboxer Archive
        # --------------------------------------------------------------------
        session_archive_path = tmp_path / "scenario_a_active_session.recipeboxer"
        session_archive_bytes = create_recipeboxer_archive(
            session,
            store=new_store,
            manager=new_manager,
            cache=new_cache,
            output_path=session_archive_path,
            project_name="Complete Scenario A Unified Project",
        )
        assert session_archive_path.is_file()
        assert len(session_archive_bytes) > 2000

        # Verify archive can be unpacked cleanly
        manifest_a, _ = extract_recipeboxer_archive(session_archive_bytes, tmp_path / "unpack_test_a", verify_hashes=True)
        assert len(manifest_a.cards) == 10
        assert len(manifest_a.recipes) == 5

        # --------------------------------------------------------------------
        # Step 7: Merge into Another External Baseline Project
        # --------------------------------------------------------------------
        # Create Project B with 1 distinct card and 1 distinct recipe ("Nonno's Lasagna")
        project_b_dir = tmp_path / "project_b"
        project_b_dir.mkdir()
        lasagna_data = make_test_jpeg_bytes(999, width=500, height=400)
        lasagna_file = project_b_dir / "card_lasagna.jpg"
        lasagna_file.write_bytes(lasagna_data)
        lasagna_sha = hashlib.sha256(lasagna_data).hexdigest().lower()

        rec_lasagna = RecipeSchema(
            recipe_id="rec_b_lasagna",
            title="Nonno's Four-Cheese Bolognese Lasagna",
            description="Layered fresh egg pasta with slow-cooked ragù bolognese and creamy béchamel.",
            yield_servings="12 servings",
            ingredients=["1 lb lasagna noodles", "2 lbs ground beef & pork", "1 cup ricotta", "1 cup mozzarella"],
            directions=["1. Simmer meat ragù.", "2. Layer noodles, sauce, and cheese.", "3. Bake at 375°F for 45 mins."],
            tags=["Main Dish", "Italian", "Pasta", "Family Heirloom"],
            source_notes="Nonno's 1960 Recipe Journal (Bologna, Italy)",
            card_ids=["card_lasagna_01"],
        )

        sess_b = new_manager.create_session(session_id="sess_external_project_b")
        sess_b.cards = [
            ScannedCard(
                card_id="card_lasagna_01",
                file_path=lasagna_file,
                filename="card_lasagna.jpg",
                sha256_hash=lasagna_sha,
                file_size_bytes=len(lasagna_data),
            )
        ]
        sess_b.fingerprints = {
            "card_lasagna_01": VisualFingerprint(
                card_id="card_lasagna_01",
                image_path=str(lasagna_file),
                sha256_hash=lasagna_sha,
                role=CardRole.STANDALONE,
            )
        }
        sess_b.recipes = [rec_lasagna]
        sess_b.state = PipelineState.COMPLETED

        archive_b_path = project_b_dir / "project_b_baseline.recipeboxer"
        create_recipeboxer_archive(
            sess_b,
            output_path=archive_b_path,
            project_name="External Project B (Italian Classics)",
        )

        # Merge Scenario A archive into Project B archive
        merged_archive_path = tmp_path / "scenario_a_merged_into_b.recipeboxer"
        merged_manifest, merged_bytes = merge_recipeboxer_archives(
            base_archive=archive_b_path,
            incoming_archive_or_session=session_archive_path,
            conflict_strategy="keep_both",
            output_path=merged_archive_path,
        )

        # Merged project should now contain: 1 (from B) + 5 (from A) = 6 total recipes!
        # And 1 (from B) + 10 (from A) = 11 total cards!
        assert len(merged_manifest.recipes) == 6
        assert len(merged_manifest.cards) == 11
        assert len(merged_manifest.assets) == 11
        assert merged_archive_path.is_file()

        # --------------------------------------------------------------------
        # Step 8: Import Merged Archive into Fresh Session & Verify 100% Losslessness
        # --------------------------------------------------------------------
        fresh_import_dir = tmp_path / "fresh_import_workspace"
        fresh_import_dir.mkdir()
        fresh_store = SessionStore(db_path=str(fresh_import_dir / "fresh.db"))
        fresh_cache = FingerprintCache(db_path=str(fresh_import_dir / "fresh.db"))
        fresh_manager = SessionManager()

        restored_session_detail = restore_archive_to_session(
            archive_file_or_bytes=merged_archive_path,
            store=fresh_store,
            manager=fresh_manager,
            cache=fresh_cache,
            target_session_id="sess_fresh_restored_scenario_a",
            scans_target_dir=fresh_import_dir / "restored_scans",
        )

        assert restored_session_detail.session_id == "sess_fresh_restored_scenario_a"
        assert restored_session_detail.card_count == 11
        assert restored_session_detail.recipe_count == 6

        # Fetch restored recipes from SQLite database
        restored_recipes = fresh_store.list_recipes("sess_fresh_restored_scenario_a")
        assert len(restored_recipes) == 6

        recipe_map_by_title = {r.title: r for r in restored_recipes}

        # Verify step bindings and marginalia preserved in merged archive manifest
        merged_recipe_map = {r.title: r for r in merged_manifest.recipes}
        sourdough_manifest_rec = merged_recipe_map["Grandma's Country Sourdough Loaf"]
        assert len(sourdough_manifest_rec.step_bindings) == len(rec_base_1.directions)
        assert sourdough_manifest_rec.step_bindings[0].card_id == "card_base_01"
        assert sourdough_manifest_rec.step_bindings[-1].card_id == "card_base_02"
        assert len(sourdough_manifest_rec.marginalia) >= 1
        assert "Great-Grandma Clara" in sourdough_manifest_rec.marginalia[0].text
        assert sourdough_manifest_rec.marginalia[0].category == "attribution"

        # Losslessness Verification 1: Grandma's Country Sourdough Loaf (SQLite restored)
        sourdough = recipe_map_by_title["Grandma's Country Sourdough Loaf"]
        assert sourdough.yield_servings == rec_base_1.yield_servings
        assert sourdough.prep_time == rec_base_1.prep_time
        assert sourdough.cook_time == rec_base_1.cook_time
        assert sourdough.total_time == rec_base_1.total_time
        assert sourdough.ingredients == rec_base_1.ingredients
        assert sourdough.directions == rec_base_1.directions
        assert sourdough.tags == rec_base_1.tags
        assert sourdough.source_notes == rec_base_1.source_notes

        # Losslessness Verification 2: Spiced Apple Tart
        tart = recipe_map_by_title["Aunt Clara's Spiced Apple Tart"]
        assert tart.ingredients == rec_base_2.ingredients
        assert tart.directions == rec_base_2.directions
        assert tart.tags == rec_base_2.tags

        # Losslessness Verification 3: Smoky Texas Brisket Chili
        chili = recipe_map_by_title["Smoky Texas Brisket Chili"]
        assert len(chili.ingredients) == 7
        assert "3 lbs smoked beef brisket, cubed" in chili.ingredients
        assert len(chili.directions) == 4
        assert "Uncle Bob's Texas Competition Recipe (1988)" in chili.source_notes

        # Losslessness Verification 4: Brussels Sprouts
        brussels = recipe_map_by_title["Crispy Garlic Parmesan Brussels Sprouts"]
        assert "2 lbs fresh brussels sprouts, trimmed and halved" in brussels.ingredients
        assert brussels.tags == ["Side Dish", "Vegetables", "Roasted", "Holiday"]

        # Losslessness Verification 5: Bourbon Eggnog
        eggnog = recipe_map_by_title["Festive Spiced Bourbon Eggnog"]
        assert "1/2 cup Kentucky straight bourbon whiskey" in eggnog.ingredients
        assert eggnog.yield_servings == "12 cups"

        # Losslessness Verification 6: Lasagna from Project B
        lasagna = recipe_map_by_title["Nonno's Four-Cheese Bolognese Lasagna"]
        assert "Nonno's 1960 Recipe Journal (Bologna, Italy)" in lasagna.source_notes

        # Verify all 11 card image assets exist and are byte-for-byte intact
        restored_detail = fresh_store.get_session_detail("sess_fresh_restored_scenario_a")
        assert restored_detail is not None
        assert len(restored_detail.cards) == 11
        for card_rec in restored_detail.cards:
            img_path_val = card_rec.get("image_path") if isinstance(card_rec, dict) else getattr(card_rec, "image_path", None)
            sha_val = card_rec.get("sha256_hash") if isinstance(card_rec, dict) else getattr(card_rec, "sha256_hash", None)
            img_p = Path(img_path_val)
            assert img_p.is_file(), f"Image file missing: {img_p}"
            # Verify SHA-256 integrity
            actual_sha = hashlib.sha256(img_p.read_bytes()).hexdigest().lower()
            assert actual_sha == sha_val.lower()

        # Clean up database handles
        store.close()
        cache.close()
        new_store.close()
        new_cache.close()
        fresh_store.close()
        fresh_cache.close()


# ============================================================================
# SCENARIO B: Corrupted Archives, Concurrency Under Load, Extreme Unicode,
# Empty Sections, and Contributor Normalization
# ============================================================================

class TestScenarioBAdversarialHardening:
    """Scenario B: Corrupted/tampered archives, concurrent exports under heavy load,

    extreme titles with special characters/Unicode, empty sections, and multiple contributor normalizations.
    """

    # ------------------------------------------------------------------------
    # B.1 Corrupted and Tampered Archives
    # ------------------------------------------------------------------------

    def test_b1_tampered_asset_sha256_detected_and_rejected(self, tmp_path: Path):
        """Valid archive whose asset file content was altered after manifest creation is rejected with SHA-256 error."""
        archive_dir = tmp_path / "tamper_src"
        archive_dir.mkdir()
        real_img = make_test_jpeg_bytes(1, 100, 100)
        img_sha = hashlib.sha256(real_img).hexdigest()

        manifest = RecipeBoxerArchiveManifest(
            project_id="proj_tamper_test",
            project_name="Tamper Test",
            assets=[
                ArchiveAssetRecord(
                    sha256_hash=img_sha,
                    filename="card_01.jpg",
                    archive_path="assets/scans/card_01.jpg",
                    mime_type="image/jpeg",
                    size_bytes=len(real_img),
                )
            ],
            cards=[
                ArchiveCardRecord(
                    card_id="card_01",
                    sha256_hash=img_sha,
                    filename="card_01.jpg",
                    role=CardRole.STANDALONE,
                )
            ],
            recipes=[],
        )

        tampered_archive = tmp_path / "tampered.recipeboxer"
        with zipfile.ZipFile(tampered_archive, "w") as zf:
            zf.writestr("manifest.json", manifest.model_dump_json())
            # Intentionally write altered bytes that do NOT match img_sha!
            zf.writestr("assets/scans/card_01.jpg", b"ALTERED_TAMPERED_BYTES_NOT_MATCHING_SHA")

        with pytest.raises(ValueError, match="Asset integrity check failed"):
            extract_recipeboxer_archive(tampered_archive, tmp_path / "tamper_dst", verify_hashes=True)

    def test_b1_corrupted_zip_payload_rejected(self, tmp_path: Path):
        """Non-ZIP or truncated byte payloads are rejected cleanly."""
        corrupt_file = tmp_path / "corrupt.recipeboxer"
        corrupt_file.write_bytes(b"THIS_IS_NOT_A_VALID_ZIP_HEADER_GARBAGE_12345")

        with pytest.raises(ValueError, match="Failed to unpack .recipeboxer archive"):
            extract_recipeboxer_archive(corrupt_file, tmp_path / "corrupt_dst")

    def test_b1_missing_manifest_in_zip_rejected(self, tmp_path: Path):
        """Valid ZIP archive missing manifest.json is rejected."""
        missing_m_zip = tmp_path / "no_manifest.recipeboxer"
        with zipfile.ZipFile(missing_m_zip, "w") as zf:
            zf.writestr("scans/test.jpg", make_test_jpeg_bytes(2))

        with pytest.raises(ValueError, match="Archive is missing required 'manifest.json'"):
            extract_recipeboxer_archive(missing_m_zip, tmp_path / "no_m_dst")

    def test_b1_zipslip_path_traversal_rejection(self, tmp_path: Path):
        """Archive with malicious relative path traversal (../../evil.sh) is blocked."""
        zipslip_file = tmp_path / "zipslip.recipeboxer"
        with zipfile.ZipFile(zipslip_file, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"project_name": "Slip"}))
            zf.writestr("../../evil_script.sh", b"echo pwned")

        with pytest.raises(ValueError, match="ZipSlip path traversal attempt"):
            extract_recipeboxer_archive(zipslip_file, tmp_path / "zipslip_dst")

    # ------------------------------------------------------------------------
    # B.2 Concurrent Exports Under Heavy Load
    # ------------------------------------------------------------------------

    def test_b2_concurrent_exports_heavy_load_all_formats_and_presets(self, tmp_path: Path):
        """24 concurrent threads performing simultaneous exports across all formats and curation presets."""
        # Create a rich dataset of 10 recipes
        recipes: List[RecipeSchema] = []
        for i in range(1, 11):
            recipes.append(
                RecipeSchema(
                    recipe_id=f"rec_concur_{i:02d}",
                    title=f"Concurrent Recipe #{i:02d} - Chef Special",
                    description=f"Description for recipe {i} with ingredients and instructions.",
                    ingredients=[f"Ingredient {j} for #{i}" for j in range(1, 6)],
                    directions=[f"Step {k} for #{i}: Stir and bake." for k in range(1, 4)],
                    tags=["Concurrent", f"Category_{i % 3}", "Tested"],
                    source_notes=f"Source Box #{i} (Grandma Rose)",
                    yield_servings=f"{i + 2} servings",
                    prep_time=f"{i * 5} mins",
                    cook_time=f"{i * 10} mins",
                )
            )

        formats = ["docx", "pdf", "markdown", "csv", "json_ld", "paprika", "cooklang", "gdocs_html"]
        presets = [GroupingPreset.NONE, GroupingPreset.COURSE, GroupingPreset.CONTRIBUTOR, GroupingPreset.TAGS, GroupingPreset.ALPHABETICAL]

        results: List[Tuple[str, str, int]] = []
        errors: List[Exception] = []

        def worker_export(fmt: str, preset: GroupingPreset, task_id: int) -> None:
            try:
                curated = curate_recipes(recipes, grouping_preset=preset, include_section_dividers=True, include_toc=True)
                out_bytes = b""

                if fmt == "docx":
                    out_bytes = DocxExporter().export_to_bytes(recipes, curation=curated)
                    assert len(out_bytes) > 1000
                    # Verify valid ZIP
                    with zipfile.ZipFile(io.BytesIO(out_bytes), "r") as zf:
                        assert "word/document.xml" in zf.namelist()
                elif fmt == "pdf":
                    out_bytes = PdfExporter().export_to_bytes(recipes, curation=curated)
                    assert out_bytes.startswith(b"%PDF-1.4")
                    assert out_bytes.rstrip().endswith(b"%%EOF")
                elif fmt == "markdown":
                    md_text = MarkdownExporter().export_to_markdown_string(recipes, curation=curated)
                    assert "Table of Contents" in md_text
                    out_bytes = md_text.encode("utf-8")
                elif fmt == "csv":
                    csv_text = PlanToEatExporter().export_to_csv_string(curated.all_recipes)
                    assert PlanToEatExporter().validate_csv_format(csv_text)
                    out_bytes = csv_text.encode("utf-8")
                elif fmt == "json_ld":
                    ld_str = JsonLdExporter().export_to_json_ld_string(curated.all_recipes)
                    assert JsonLdExporter().validate_json_ld(ld_str)
                    out_bytes = ld_str.encode("utf-8")
                elif fmt == "paprika":
                    out_bytes = PaprikaExporter().export_to_paprika_bytes(curated.all_recipes)
                    assert len(out_bytes) > 100
                elif fmt == "cooklang":
                    cook_str = CooklangExporter().export_to_cooklang_string(curated.all_recipes)
                    assert len(cook_str) > 100
                    out_bytes = cook_str.encode("utf-8")
                elif fmt == "gdocs_html":
                    html_str = GoogleDocsExporter().export_to_html(curated.all_recipes, curation=curated)
                    assert "Table of Contents" in html_str
                    out_bytes = html_str.encode("utf-8")

                results.append((fmt, str(preset), len(out_bytes)))
            except Exception as exc:
                errors.append(exc)

        # Launch 24 concurrent threads
        tasks = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
            task_idx = 0
            for fmt in formats:
                for preset in [GroupingPreset.COURSE, GroupingPreset.CONTRIBUTOR, GroupingPreset.ALPHABETICAL]:
                    tasks.append(executor.submit(worker_export, fmt, preset, task_idx))
                    task_idx += 1
            concurrent.futures.wait(tasks)

        assert len(errors) == 0, f"Encountered {len(errors)} concurrency errors: {errors}"
        assert len(results) == 24

    # ------------------------------------------------------------------------
    # B.3 Extreme Titles, Special Characters, Unicode, and Script Mixing
    # ------------------------------------------------------------------------

    def test_b3_extreme_unicode_emojis_quotes_and_xml_entities(self, tmp_path: Path):
        """Recipes with emojis, RTL scripts, CJK, angle brackets (<script>), quotes, and XML entities."""
        extreme_recipe = RecipeSchema(
            recipe_id="rec_extreme_unicode_01",
            title='Grandma "Oma" Sophie\'s <Flambé> 🍓 Crêpes Suzette & 🍵 Matcha Soufflé — "L\'Étoile" (1945) 🥞',
            description='Multi-script description: العربية (حلوى), עברית (עוגה), 日本語 (宇治抹茶), with <script>alert("XSS")</script> & unescaped &amp; symbols.',
            yield_servings='4 "hearty" portions (or 6 petites)',
            prep_time="25 mins ⏱️",
            cook_time="15 mins 🍳",
            total_time="40 mins ⌛",
            ingredients=[
                '2 cups "King Arthur" flour, sifted & unbleached',
                "3 large eggs 🥚 (separated, cold)",
                "1 ½ tsp 宇治抹茶 (Uji Matcha powder 🍵)",
                "1/4 cup Grand Marnier liqueur <40% ABV>",
                "2 tbsp unsalted butter 🧈, melted & cooled",
                "pinch of fleur de sel & vanilla sugar",
            ],
            directions=[
                '1. In a large copper bowl, whisk eggs & flour with 1 cup milk until smooth (no lumps!).',
                '2. Add matcha powder (宇治抹茶) and Grand Marnier <caution: flammable!>.',
                '3. Cook in buttered crêpe pan at ~350°F (175°C) until edges curl & golden.',
                '4. Flambé with remaining liqueur and dust with powdered sugar ❄️.',
            ],
            tags=['"Heirloom"', "French & Japanese 🇯🇵🇫🇷", "<Gourmet Desserts>", "Matcha 🍵", 'Mom\'s "Favorite"'],
            source_notes='Oma\'s Journal <Vienna/Tokyo Edition> (pp. 88-92) — Handwritten with fountain pen & tomato splatter.',
        )

        curated = curate_recipes([extreme_recipe], grouping_preset=GroupingPreset.COURSE, include_toc=True)

        # 1. DOCX Exporter handles extreme characters & XML escaping cleanly
        docx_bytes = DocxExporter().export_to_bytes([extreme_recipe], curation=curated)
        with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as zf:
            doc_xml = zf.read("word/document.xml").decode("utf-8")
            # Verify valid XML
            root = ET.fromstring(doc_xml)
            assert root is not None
            # Must contain escaped text
            assert "&lt;Flambé&gt;" in doc_xml or "Flambé" in doc_xml
            assert "Crêpes Suzette" in doc_xml

        # 2. PDF Exporter handles extreme characters without crash
        pdf_bytes = PdfExporter().export_to_bytes([extreme_recipe], curation=curated)
        assert pdf_bytes.startswith(b"%PDF-1.4")
        assert pdf_bytes.rstrip().endswith(b"%%EOF")

        # 3. CSV Exporter adheres strictly to RFC 4180
        csv_text = PlanToEatExporter().export_to_csv_string([extreme_recipe])
        assert PlanToEatExporter().validate_csv_format(csv_text)
        reader = csv.DictReader(io.StringIO(csv_text), lineterminator="\r\n")
        row = next(reader)
        assert row["Title"] == extreme_recipe.title
        assert '2 cups "King Arthur" flour, sifted & unbleached' in row["Ingredients"]
        assert 'Mom\'s "Favorite"' in row["Tags"]

        # 4. JSON-LD Exporter produces valid Schema.org
        json_ld_str = JsonLdExporter().export_to_json_ld_string([extreme_recipe])
        assert JsonLdExporter().validate_json_ld(json_ld_str)
        ld_data = json.loads(json_ld_str)
        assert ld_data["name"] == extreme_recipe.title

        # 5. Markdown Exporter frontmatter handles special chars
        md_text = MarkdownExporter().export_to_markdown_string([extreme_recipe])
        assert "Crêpes Suzette" in md_text

    # ------------------------------------------------------------------------
    # B.4 Empty Sections and Uncategorized Fallbacks
    # ------------------------------------------------------------------------

    def test_b4_empty_sections_and_uncategorized_fallbacks(self):
        """Recipes with no recognizable course tags fall cleanly into 'Other Specialties'

        without causing empty ghost sections in TOC or dividers.
        """
        obscure_recipe = RecipeSchema(
            recipe_id="rec_obscure_01",
            title="Homemade Lavender Linen Scent & Potpourri Infusion",
            description="Non-food household culinary infusion using dried garden herbs.",
            ingredients=["2 cups dried lavender buds", "1 cup dried rose petals", "10 drops lavender essential oil"],
            directions=["1. Combine herbs in glass jar.", "2. Seal and steep in dark cupboard for 2 weeks."],
            tags=["Herbal", "Craft", "Aromatherapy"],
            source_notes="Grandma's Household Hints (1922)",
        )

        curated = curate_recipes([obscure_recipe], grouping_preset=GroupingPreset.COURSE, include_toc=True)

        # Must have exactly 1 section: "Other Recipes"
        assert len(curated.sections) == 1
        assert len(curated.sections[0].recipes) == 1
        assert curated.sections[0].title in ("Other Recipes", "General Recipes")

        # Verify TOC rendering does not produce empty section entries
        docx_bytes = DocxExporter().export_to_bytes([obscure_recipe], curation=curated)
        with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as zf:
            doc_xml = zf.read("word/document.xml").decode("utf-8")
            assert "Homemade Lavender Linen Scent" in doc_xml
            # Only 1 section heading in XML
            assert "Appetizers" not in doc_xml
            assert "Main Dishes" not in doc_xml

        pdf_bytes = PdfExporter().export_to_bytes([obscure_recipe], curation=curated)
        assert pdf_bytes.startswith(b"%PDF-1.4")

    # ------------------------------------------------------------------------
    # B.5 Contributor Extraction & Normalization
    # ------------------------------------------------------------------------

    @pytest.mark.parametrize(
        "raw_source, expected_contributor",
        [
            ("Grandma Clara's Sunday Suppers (1952)", "Grandma Clara"),
            ("From the kitchen of Aunt Marie", "Aunt Marie"),
            ("From: Uncle Bob & Cousin Dave", "Uncle Bob & Cousin Dave"),
            ("Submitted by: Mrs. Eleanor Vance", "Mrs. Eleanor Vance"),
            ("Mom's Vintage Recipe Box", "Mom"),
            ("Source: Mom's Vintage Recipe Box", "Mom's Vintage Recipe Box"),
            ("Handed down by Great-Grandmother Evelyn (1918)", "Great-Grandmother Evelyn"),
            ("Courtesy of Chef Jean-Pierre", "Chef Jean-Pierre"),
            ("Recipe from Nonna Rosa", "Nonna Rosa"),
            ("", "Family Collection / Unattributed"),
            (None, "Family Collection / Unattributed"),
            ("   \t\n ", "Family Collection / Unattributed"),
        ],
    )
    def test_b5_contributor_extraction_normalization_matrix(
        self,
        raw_source: Optional[str],
        expected_contributor: str,
    ):
        """Verifies _extract_contributor_name accurately extracts clean normalized names."""
        result = _extract_contributor_name(raw_source)
        assert result == expected_contributor

    def test_b5_contributor_grouping_preset_clustering(self):
        """Recipes from varied contributors cluster correctly under normalized contributor sections."""
        r1 = RecipeSchema(
            recipe_id="r1",
            title="Apple Pie",
            ingredients=["Apples", "Flour"],
            directions=["Bake."],
            source_notes="Grandma Clara's Sunday Suppers (1952)",
        )
        r2 = RecipeSchema(
            recipe_id="r2",
            title="Peach Cobbler",
            ingredients=["Peaches", "Sugar"],
            directions=["Bake."],
            source_notes="From the kitchen of Grandma Clara",
        )
        r3 = RecipeSchema(
            recipe_id="r3",
            title="BBQ Ribs",
            ingredients=["Pork ribs", "Sauce"],
            directions=["Smoked."],
            source_notes="Uncle Bob's Texas Grill",
        )
        r4 = RecipeSchema(
            recipe_id="r4",
            title="Mystery Casserole",
            ingredients=["Noodles", "Tuna"],
            directions=["Mix."],
            source_notes=None,
        )

        curated = curate_recipes([r1, r2, r3, r4], grouping_preset=GroupingPreset.CONTRIBUTOR, include_toc=True)

        section_titles = [s.title for s in curated.sections]
        assert "Grandma Clara" in section_titles
        assert "Uncle Bob" in section_titles
        assert "Family Collection / Unattributed" in section_titles

        # Grandma Clara section should contain both r1 and r2!
        clara_sec = next(s for s in curated.sections if s.title == "Grandma Clara")
        assert len(clara_sec.recipes) == 2
        assert {r.recipe_id for r in clara_sec.recipes} == {"r1", "r2"}
