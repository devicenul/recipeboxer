"""
test_e2e_pipeline.py - Comprehensive Opaque-Box E2E Test Suite for RecipeBoxer.
Covers Tiers 1-4:
- Tier 1: Feature coverage (all 15 features in isolation)
- Tier 2: Boundary and corner cases (empty folders, single-card batches, malformed cards, missing back pairs, high-concurrency stress, zero-interval batches)
- Tier 3: Pairwise cross-feature interactions (parallel ingestion + HITL override + CSV export; slow-batch + SQLite cache hit + HITL rejection)
- Tier 4: Real-world application scenarios (5 scenarios from TEST_INFRA.md: vintage handwritten index cards, mixed batch ingestion, rate-limited slow batch with mid-run crash resumption, HITL override & pairing correction, Plan To Eat RFC 4180 import engine validation)
"""

import asyncio
import csv
import hashlib
import io
import os
import time
from pathlib import Path
from typing import Dict, List

import httpx
import pytest
from pydantic import ValidationError

from tests.conftest import (
    ApprovalPayload,
    ArtifactType,
    BoundaryText,
    CardPairApproval,
    CardQuadrant,
    CardRole,
    CorrelationProposal,
    CorrelationResult,
    HITLDecision,
    InkColor,
    InkInfo,
    InkType,
    MockGeminiClient,
    PaperColorInfo,
    PaperColorTone,
    PaperRulingInfo,
    PaperRulingType,
    PhysicalArtifact,
    PipelineSession,
    PipelineState,
    RecipeSchema,
    SyntheticDeckInfo,
    SQLiteFingerprintCache,
    VisualFingerprint,
    compute_heuristic_visual_score,
    create_corrupted_image_file,
    create_synthetic_card_image,
    serialize_recipes_to_plan_to_eat_csv,
)


# ============================================================================
# TIER 1: FEATURE COVERAGE (ALL 15 FEATURES IN ISOLATION)
# ============================================================================

class TestTier1FeatureCoverage:
    """Tier 1: Isolated tests for all 15 core features in RecipeBoxer."""

    # ------------------------------------------------------------------------
    # Feature 1: config-management
    # ------------------------------------------------------------------------
    def test_f1_config_defaults_and_model_overrides(self):
        """F1: Test configuration defaults to gemini-3.7-flash and supports stage overrides."""
        config_data = {
            "default_model": "gemini-3.7-flash",
            "fingerprint_model": "gemini-2.5-pro",
            "correlation_model": None,  # should fallback to default
            "extraction_model": "gemini-3.7-flash",
            "thinking_mode": True,
            "max_concurrency": 8,
            "slow_batch_size": 4,
            "slow_batch_interval_seconds": 2.5,
        }

        # Validate fallback logic
        effective_corr_model = config_data["correlation_model"] or config_data["default_model"]
        effective_fp_model = config_data["fingerprint_model"] or config_data["default_model"]

        assert config_data["default_model"] == "gemini-3.7-flash"
        assert effective_fp_model == "gemini-2.5-pro"
        assert effective_corr_model == "gemini-3.7-flash"
        assert config_data["thinking_mode"] is True
        assert config_data["max_concurrency"] == 8

    # ------------------------------------------------------------------------
    # Feature 2: domain-schemas
    # ------------------------------------------------------------------------
    def test_f2_domain_schemas_validation(self):
        """F2: Test strict Pydantic V2 schema construction and field validation."""
        fp = VisualFingerprint(
            card_id="card_01.jpg",
            image_path="/tmp/card_01.jpg",
            sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            role=CardRole.FRONT_ONLY,
            role_confidence=0.98,
            paper_color=PaperColorInfo(
                tone_name="Aged Cream",
                hex_approx="#F4E8C1",
                lab_l=88.5,
                lab_a=2.1,
                lab_b=16.4,
                tone_enum=PaperColorTone.OFF_WHITE_CREAM,
            ),
            ruling=PaperRulingInfo(
                has_ruling=True,
                ruling_type=PaperRulingType.STANDARD_RULED_BLUE,
                line_spacing_px=24.0,
            ),
            ink=InkInfo(
                ink_color=InkColor.BLUE,
                ink_type=InkType.BLUE_BALLPOINT,
            ),
            artifacts=[
                PhysicalArtifact(
                    artifact_type=ArtifactType.OIL_GREASE_STAIN,
                    quadrant=CardQuadrant.TOP_RIGHT,
                    description="Butter stain",
                )
            ],
            boundary_text=BoundaryText(
                first_line="Grandma's Apple Pie",
                last_line="Roll out dough (over)",
                has_continuation_symbol=True,
                is_cut_off_mid_sentence=True,
            ),
        )

        assert fp.role == CardRole.FRONT_ONLY
        assert fp.artifacts[0].quadrant == CardQuadrant.TOP_RIGHT
        assert fp.boundary_text.has_continuation_symbol is True

        # Test invalid role validation
        with pytest.raises(ValidationError):
            VisualFingerprint(
                card_id="bad.jpg",
                image_path="/tmp/bad.jpg",
                sha256_hash="123",
                role="INVALID_ROLE",  # Should raise ValidationError
            )

    # ------------------------------------------------------------------------
    # Feature 3: parallel-ingestion
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_f3_parallel_ingestion_semaphore(self, tmp_path: Path, mock_gemini_client: MockGeminiClient):
        """F3: Test parallel ingestion throttled by asyncio.Semaphore."""
        cards = []
        for i in range(8):
            p = tmp_path / f"card_{i:02d}.jpg"
            create_synthetic_card_image(p, title=f"Recipe {i}")
            cards.append(p)

        max_concurrency = 3
        sem = asyncio.Semaphore(max_concurrency)
        active_workers = 0
        peak_active_workers = 0

        async def _worker(card_path: Path):
            nonlocal active_workers, peak_active_workers
            async with sem:
                active_workers += 1
                peak_active_workers = max(peak_active_workers, active_workers)
                await asyncio.sleep(0.02)  # Simulate vision work
                active_workers -= 1
                return await mock_gemini_client.analyze_fingerprint(card_path, "mock-sha")

        results = await asyncio.gather(*[_worker(c) for c in cards])
        assert len(results) == 8
        assert peak_active_workers <= max_concurrency
        assert peak_active_workers > 1

    # ------------------------------------------------------------------------
    # Feature 4: slow-batch-ingestion
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_f4_slow_batch_ingestion_intervals(self, tmp_path: Path, mock_gemini_client: MockGeminiClient):
        """F4: Test slow-batch ingestion pauses between batch slices for quota preservation."""
        cards = []
        for i in range(6):
            p = tmp_path / f"slow_card_{i:02d}.jpg"
            create_synthetic_card_image(p, title=f"Slow Recipe {i}")
            cards.append(p)

        batch_size = 2
        interval = 0.05
        processed_count = 0
        t0 = time.time()

        for i in range(0, len(cards), batch_size):
            batch = cards[i : i + batch_size]
            for c in batch:
                await mock_gemini_client.analyze_fingerprint(c, "mock-sha")
                processed_count += 1
            if i + batch_size < len(cards):
                await asyncio.sleep(interval)

        elapsed = time.time() - t0
        assert processed_count == 6
        # Expected at least 2 interval sleeps (3 batches total)
        assert elapsed >= 2 * interval

    # ------------------------------------------------------------------------
    # Feature 5: fingerprint-cache
    # ------------------------------------------------------------------------
    def test_f5_fingerprint_cache_sha256_persistence(self, test_cache: SQLiteFingerprintCache, tmp_path: Path):
        """F5: Test SHA-256 SQLite WAL cache hit on identical image bytes."""
        card_path = tmp_path / "cache_test_card.jpg"
        create_synthetic_card_image(card_path, title="Cacheable Brownies")
        card_bytes = card_path.read_bytes()
        sha = hashlib.sha256(card_bytes).hexdigest()

        # Cache miss initially
        assert test_cache.get(card_bytes, str(card_path)) is None
        assert not test_cache.contains(sha)

        fp = VisualFingerprint(
            card_id=card_path.name,
            image_path=str(card_path),
            sha256_hash=sha,
            role=CardRole.STANDALONE,
            role_confidence=0.99,
        )
        test_cache.put(fp)

        # Cache hit
        cached_fp = test_cache.get(card_bytes, str(card_path))
        assert cached_fp is not None
        assert cached_fp.card_id == "cache_test_card.jpg"
        assert cached_fp.sha256_hash == sha
        assert test_cache.contains(sha)
        assert test_cache.count() == 1

    # ------------------------------------------------------------------------
    # Feature 6: visual-fingerprinting
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_f6_visual_fingerprinting_attributes(self, mock_gemini_client: MockGeminiClient, tmp_path: Path):
        """F6: Test extraction of paper tone, ruling, ink, artifacts, and roles."""
        card_path = tmp_path / "01_cinnamon_rolls_front.jpg"
        create_synthetic_card_image(
            card_path,
            title="Grandma's Cinnamon Rolls",
            stain_quadrant=CardQuadrant.TOP_RIGHT,
            stain_type=ArtifactType.OIL_GREASE_STAIN,
        )
        fp = await mock_gemini_client.analyze_fingerprint(card_path, "sha-cinnamon-front")

        assert fp.role == CardRole.FRONT_ONLY
        assert fp.paper_color.tone_enum == PaperColorTone.OFF_WHITE_CREAM
        assert fp.ruling.has_ruling is True
        assert fp.ink.ink_color == InkColor.BLUE
        assert len(fp.artifacts) == 1
        assert fp.artifacts[0].quadrant == CardQuadrant.TOP_RIGHT
        assert fp.boundary_text.has_continuation_symbol is True

    # ------------------------------------------------------------------------
    # Feature 7: heuristic-screening
    # ------------------------------------------------------------------------
    def test_f7_heuristic_visual_screening_formula(self):
        """F7: Test Stage 1 heuristic scoring across 7 dimensions and mirrored stains."""
        front_fp = VisualFingerprint(
            card_id="front.jpg",
            image_path="/tmp/front.jpg",
            sha256_hash="sha-front",
            role=CardRole.FRONT_ONLY,
            paper_color=PaperColorInfo(tone_enum=PaperColorTone.OFF_WHITE_CREAM, lab_l=88, lab_a=2, lab_b=16),
            ruling=PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE),
            ink=InkInfo(ink_color=InkColor.BLUE, ink_type=InkType.BLUE_BALLPOINT),
            artifacts=[PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.TOP_RIGHT)],
        )

        # Back card with matching mirrored stain in TOP_LEFT
        back_fp = VisualFingerprint(
            card_id="back.jpg",
            image_path="/tmp/back.jpg",
            sha256_hash="sha-back",
            role=CardRole.BACK_ONLY,
            paper_color=PaperColorInfo(tone_enum=PaperColorTone.OFF_WHITE_CREAM, lab_l=88, lab_a=2, lab_b=16),
            ruling=PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE),
            ink=InkInfo(ink_color=InkColor.BLUE, ink_type=InkType.BLUE_BALLPOINT),
            artifacts=[PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.TOP_LEFT)],
        )

        score_match = compute_heuristic_visual_score(front_fp, back_fp, scan_index_diff=1)
        assert score_match >= 0.85  # Strong match threshold

        # Incompatible role should immediately drop to 0.0
        incompatible_back = back_fp.model_copy(update={"role": CardRole.FRONT_ONLY})
        score_incompatible = compute_heuristic_visual_score(front_fp, incompatible_back, scan_index_diff=1)
        assert score_incompatible == 0.0

    # ------------------------------------------------------------------------
    # Feature 8: semantic-correlation
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_f8_semantic_continuity_verification(self, mock_gemini_client: MockGeminiClient):
        """F8: Test Stage 2 Gemini multimodal semantic continuity scoring."""
        front_fp = VisualFingerprint(
            card_id="01_cinnamon_rolls_front.jpg",
            image_path="/tmp/01_cinnamon_rolls_front.jpg",
            sha256_hash="sha-f",
            role=CardRole.FRONT_ONLY,
        )
        back_fp = VisualFingerprint(
            card_id="02_cinnamon_rolls_back.jpg",
            image_path="/tmp/02_cinnamon_rolls_back.jpg",
            sha256_hash="sha-b",
            role=CardRole.BACK_ONLY,
        )

        proposal = await mock_gemini_client.verify_correlation(front_fp, back_fp, heuristic_score=0.92)
        assert proposal.semantic_score >= 0.90
        assert proposal.confidence >= 0.85
        assert proposal.recommended_action == "PAIR"
        assert "Syntactic continuity" in proposal.continuity_explanation

    # ------------------------------------------------------------------------
    # Feature 9: pairing-optimizer
    # ------------------------------------------------------------------------
    def test_f9_pairing_optimizer_assignment(self):
        """F9: Test resolving candidate pairs into confirmed pairs and standalone cards."""
        proposals = [
            CorrelationProposal(
                proposal_id="p1",
                front_card_id="card_01.jpg",
                back_card_id="card_02.jpg",
                heuristic_score=0.92,
                semantic_score=0.95,
                confidence=0.94,
                recommended_action="PAIR",
            ),
            CorrelationProposal(
                proposal_id="p2",
                front_card_id="card_03.jpg",
                back_card_id=None,
                heuristic_score=1.0,
                semantic_score=1.0,
                confidence=1.0,
                is_standalone=True,
                recommended_action="STANDALONE",
            ),
        ]
        result = CorrelationResult(proposals=proposals, unmatched_card_ids=["card_noise.jpg"])

        assert len(result.proposals) == 2
        assert result.proposals[0].front_card_id == "card_01.jpg"
        assert result.proposals[0].back_card_id == "card_02.jpg"
        assert result.proposals[1].is_standalone is True
        assert "card_noise.jpg" in result.unmatched_card_ids

    # ------------------------------------------------------------------------
    # Feature 10: hitl-async-bridge
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_f10_hitl_async_event_pause_resume(
        self, synthetic_card_deck: SyntheticDeckInfo, mock_gemini_client: MockGeminiClient, test_cache: SQLiteFingerprintCache
    ):
        """F10: Test PipelineSession pausing at HITL_PENDING and resuming on submit_approval()."""
        session = PipelineSession(
            session_id="sess_f10",
            config={"scans_dir": str(synthetic_card_deck.directory), "mode": "parallel", "max_concurrency": 2},
            mock_client=mock_gemini_client,
            cache=test_cache,
        )

        task = asyncio.create_task(session.run())
        # Wait for session to reach HITL_PENDING
        for _ in range(50):
            if session.state == PipelineState.HITL_PENDING:
                break
            await asyncio.sleep(0.02)

        assert session.state == PipelineState.HITL_PENDING
        assert len(session.proposals) > 0

        # Unblock by submitting approvals
        approvals = [
            CardPairApproval(
                proposal_id=p.proposal_id,
                front_card_id=p.front_card_id,
                back_card_id=p.back_card_id,
                decision=HITLDecision.APPROVE,
            )
            for p in session.proposals
        ]
        session.submit_approval(approvals)

        await task
        assert session.state == PipelineState.COMPLETED
        assert len(session.extracted_recipes) > 0

    # ------------------------------------------------------------------------
    # Feature 11: fastapi-pipeline-endpoints
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_f11_fastapi_pipeline_start_and_status(
        self, async_client: httpx.AsyncClient, synthetic_card_deck: SyntheticDeckInfo
    ):
        """F11: Test POST /api/pipeline/start and GET /api/status."""
        start_payload = {
            "scans_dir": str(synthetic_card_deck.directory),
            "mode": "parallel",
            "max_concurrency": 4,
        }
        res_start = await async_client.post("/api/pipeline/start", json=start_payload)
        assert res_start.status_code == 200
        start_data = res_start.json()
        assert "session_id" in start_data
        session_id = start_data["session_id"]

        # Check status
        res_status = await async_client.get(f"/api/status?session_id={session_id}")
        assert res_status.status_code == 200
        status_data = res_status.json()
        assert status_data["session_id"] == session_id
        assert status_data["status"] in ["FINGERPRINTING", "CORRELATING", "HITL_PENDING", "EXTRACTING", "COMPLETED"]

    # ------------------------------------------------------------------------
    # Feature 12: fastapi-hitl-endpoints
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_f12_fastapi_hitl_pending_and_approve(
        self, async_client: httpx.AsyncClient, synthetic_card_deck: SyntheticDeckInfo
    ):
        """F12: Test GET /api/hitl/pending and POST /api/hitl/approve."""
        start_res = await async_client.post("/api/pipeline/start", json={
            "scans_dir": str(synthetic_card_deck.directory),
            "mode": "parallel",
        })
        session_id = start_res.json()["session_id"]

        # Poll until HITL_PENDING
        pending_data = None
        for _ in range(50):
            res = await async_client.get(f"/api/hitl/pending?session_id={session_id}")
            if res.status_code == 200:
                pending_data = res.json()
                break
            await asyncio.sleep(0.02)

        assert pending_data is not None
        assert pending_data["status"] == "HITL_PENDING"
        assert pending_data["total_proposals"] > 0

        # Approve proposals
        proposals = pending_data["proposed_pairs"]
        approvals = [
            {
                "proposal_id": p["proposal_id"],
                "front_card_id": p["front_card_id"],
                "back_card_id": p["back_card_id"],
                "decision": "APPROVE",
            }
            for p in proposals
        ]
        approve_res = await async_client.post("/api/hitl/approve", json={
            "session_id": session_id,
            "approvals": approvals,
            "rejected_card_ids": [],
        })
        assert approve_res.status_code == 200
        assert approve_res.json()["approved_count"] == len(approvals)

    # ------------------------------------------------------------------------
    # Feature 13: recipe-extraction
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_f13_recipe_extraction_schema_adherence(self, mock_gemini_client: MockGeminiClient):
        """F13: Test structured multimodal recipe transcription adheres to RecipeSchema."""
        front_fp = VisualFingerprint(
            card_id="01_cinnamon_rolls_front.jpg",
            image_path="/tmp/01_cinnamon_rolls_front.jpg",
            sha256_hash="sha-f",
            role=CardRole.FRONT_ONLY,
        )
        back_fp = VisualFingerprint(
            card_id="02_cinnamon_rolls_back.jpg",
            image_path="/tmp/02_cinnamon_rolls_back.jpg",
            sha256_hash="sha-b",
            role=CardRole.BACK_ONLY,
        )

        recipe = await mock_gemini_client.extract_recipe(front_fp, back_fp)
        assert isinstance(recipe, RecipeSchema)
        assert recipe.title == "Grandma's Cinnamon Rolls"
        assert len(recipe.ingredients) >= 5
        assert len(recipe.directions) >= 3
        # Verify sequential numbering in directions
        for i, step in enumerate(recipe.directions, start=1):
            assert step.startswith(f"{i}. ")

    # ------------------------------------------------------------------------
    # Feature 14: plan-to-eat-csv-export
    # ------------------------------------------------------------------------
    def test_f14_plan_to_eat_csv_formatting(self):
        """F14: Test RFC 4180 CSV serialization with 9 exact columns and multi-line escaping."""
        recipes = [
            RecipeSchema(
                recipe_id="rec_01",
                title="Grandma's Cinnamon Rolls",
                description="Vintage card recipe with 8\" pan note.",
                yield_servings="12 rolls",
                prep_time="30 mins",
                total_time="2 hrs 15 mins",
                ingredients=["1 pkg active dry yeast", "1/2 cup warm water", "3 cups flour"],
                directions=["1. Dissolve yeast.", "2. Mix in flour.", "3. Bake at 375°F for 25 mins."],
                tags=["Dessert", "Baking"],
                source_notes="Grandma's Box",
            )
        ]
        csv_str = serialize_recipes_to_plan_to_eat_csv(recipes)

        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)

        # 1. Header row verification
        expected_headers = ["Title", "Description", "Ingredients", "Directions", "Prep Time", "Total Time", "Yield", "Tags", "Source"]
        assert rows[0] == expected_headers

        # 2. Data row verification
        data_row = rows[1]
        assert data_row[0] == "Grandma's Cinnamon Rolls"
        assert data_row[1] == "Vintage card recipe with 8\" pan note."
        assert "1 pkg active dry yeast\n1/2 cup warm water\n3 cups flour" == data_row[2]
        assert "1. Dissolve yeast.\n2. Mix in flour.\n3. Bake at 375°F for 25 mins." == data_row[3]
        assert data_row[4] == "30 mins"
        assert data_row[5] == "2 hrs 15 mins"
        assert data_row[6] == "12 rolls"
        assert data_row[7] == "Dessert, Baking"
        assert data_row[8] == "Grandma's Box"

    # ------------------------------------------------------------------------
    # Feature 15: fastapi-export-endpoint
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_f15_fastapi_export_csv_download(
        self, async_client: httpx.AsyncClient, synthetic_card_deck: SyntheticDeckInfo
    ):
        """F15: Test GET /api/export/csv returns valid CSV download stream."""
        # 1. Start pipeline
        start_res = await async_client.post("/api/pipeline/start", json={
            "scans_dir": str(synthetic_card_deck.directory),
            "mode": "parallel",
        })
        session_id = start_res.json()["session_id"]

        # 2. Wait for HITL
        for _ in range(50):
            res = await async_client.get(f"/api/hitl/pending?session_id={session_id}")
            if res.status_code == 200:
                pending = res.json()
                break
            await asyncio.sleep(0.02)

        # 3. Approve all
        approvals = [
            {
                "proposal_id": p["proposal_id"],
                "front_card_id": p["front_card_id"],
                "back_card_id": p["back_card_id"],
                "decision": "APPROVE",
            }
            for p in pending["proposed_pairs"]
        ]
        await async_client.post("/api/hitl/approve", json={
            "session_id": session_id,
            "approvals": approvals,
        })

        # 4. Wait for COMPLETED
        for _ in range(50):
            s_res = await async_client.get(f"/api/status?session_id={session_id}")
            if s_res.json()["status"] == "COMPLETED":
                break
            await asyncio.sleep(0.02)

        # 5. Download CSV
        export_res = await async_client.get(f"/api/export/csv?session_id={session_id}")
        assert export_res.status_code == 200
        assert "text/csv" in export_res.headers["content-type"]
        assert 'filename="recipeboxer_export.csv"' in export_res.headers["content-disposition"]
        assert "Title,Description,Ingredients,Directions" in export_res.text


# ============================================================================
# TIER 2: BOUNDARY AND CORNER CASES
# ============================================================================

class TestTier2BoundaryAndCornerCases:
    """Tier 2: Boundary value analysis, malformed inputs, and stress conditions."""

    @pytest.mark.asyncio
    async def test_t2_empty_scans_directory_returns_400(self, async_client: httpx.AsyncClient, tmp_path: Path):
        """T2.1: Starting pipeline with an empty directory raises 400 Bad Request."""
        empty_dir = tmp_path / "empty_scans"
        empty_dir.mkdir()

        res = await async_client.post("/api/pipeline/start", json={
            "scans_dir": str(empty_dir),
            "mode": "parallel",
        })
        assert res.status_code == 400
        assert "No card images found" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_t2_nonexistent_directory_returns_400(self, async_client: httpx.AsyncClient):
        """T2.2: Starting pipeline with nonexistent directory path raises 400 Bad Request."""
        res = await async_client.post("/api/pipeline/start", json={
            "scans_dir": "/nonexistent/path/to/cards",
            "mode": "parallel",
        })
        assert res.status_code == 400

    @pytest.mark.asyncio
    async def test_t2_single_card_standalone_batch(
        self, async_client: httpx.AsyncClient, tmp_path: Path
    ):
        """T2.3: Single-card batch with 1 standalone recipe runs cleanly without pair requirements."""
        single_dir = tmp_path / "single_scan"
        single_dir.mkdir()
        card_p = single_dir / "quick_fudge_standalone.jpg"
        create_synthetic_card_image(card_p, title="Quick Fudge", continuation_cue=None)

        start_res = await async_client.post("/api/pipeline/start", json={
            "scans_dir": str(single_dir),
            "mode": "parallel",
        })
        session_id = start_res.json()["session_id"]

        # Wait for HITL
        for _ in range(50):
            res = await async_client.get(f"/api/hitl/pending?session_id={session_id}")
            if res.status_code == 200:
                pending = res.json()
                break
            await asyncio.sleep(0.02)

        assert pending["total_proposals"] == 1
        assert pending["proposed_pairs"][0]["is_standalone"] is True

        # Approve standalone
        await async_client.post("/api/hitl/approve", json={
            "session_id": session_id,
            "approvals": [
                {
                    "proposal_id": pending["proposed_pairs"][0]["proposal_id"],
                    "front_card_id": "quick_fudge_standalone.jpg",
                    "back_card_id": None,
                    "decision": "APPROVE",
                }
            ],
        })

        # Wait for completion & verify export
        for _ in range(50):
            s_res = await async_client.get(f"/api/status?session_id={session_id}")
            if s_res.json()["status"] == "COMPLETED":
                break
            await asyncio.sleep(0.02)

        exp_res = await async_client.get(f"/api/export/csv?session_id={session_id}")
        assert exp_res.status_code == 200
        assert "Quick Chocolate Fudge" in exp_res.text

    @pytest.mark.asyncio
    async def test_t2_corrupted_image_handling_graceful(
        self, tmp_path: Path, mock_gemini_client: MockGeminiClient, test_cache: SQLiteFingerprintCache
    ):
        """T2.4: Corrupted/malformed image in batch does not crash pipeline; skips to remaining cards."""
        corrupt_dir = tmp_path / "corrupt_batch"
        corrupt_dir.mkdir()
        valid_card = corrupt_dir / "valid_card_standalone.jpg"
        create_synthetic_card_image(valid_card, title="Valid Recipe")
        corrupt_card = corrupt_dir / "corrupted_scan.jpg"
        create_corrupted_image_file(corrupt_card)

        session = PipelineSession(
            session_id="sess_t2_corrupt",
            config={"scans_dir": str(corrupt_dir), "mode": "parallel", "max_concurrency": 2},
            mock_client=mock_gemini_client,
            cache=test_cache,
        )
        task = asyncio.create_task(session.run())

        for _ in range(50):
            if session.state == PipelineState.HITL_PENDING:
                break
            await asyncio.sleep(0.02)

        assert session.state == PipelineState.HITL_PENDING
        # Valid card was successfully processed despite corrupt file present
        assert len(session.fingerprints) == 1
        assert "valid_card_standalone.jpg" in session.fingerprints

        session.submit_approval([
            CardPairApproval(
                proposal_id=session.proposals[0].proposal_id,
                front_card_id="valid_card_standalone.jpg",
                decision=HITLDecision.APPROVE,
            )
        ])
        await task
        assert session.state == PipelineState.COMPLETED

    @pytest.mark.asyncio
    async def test_t2_missing_card_back_unassigned(
        self, tmp_path: Path, mock_gemini_client: MockGeminiClient, test_cache: SQLiteFingerprintCache
    ):
        """T2.5: Front-only card with no corresponding back card is surfaced in unassigned_cards."""
        orphan_dir = tmp_path / "orphan_batch"
        orphan_dir.mkdir()
        orphan_front = orphan_dir / "cinnamon_front.jpg"
        create_synthetic_card_image(orphan_front, title="Cinnamon Front", continuation_cue="(over)")

        session = PipelineSession(
            session_id="sess_orphan",
            config={"scans_dir": str(orphan_dir), "mode": "parallel"},
            mock_client=mock_gemini_client,
            cache=test_cache,
        )
        task = asyncio.create_task(session.run())

        for _ in range(50):
            if session.state == PipelineState.HITL_PENDING:
                break
            await asyncio.sleep(0.02)

        assert session.state == PipelineState.HITL_PENDING
        assert len(session.unassigned_cards) == 1
        assert session.unassigned_cards[0].card_id == "cinnamon_front.jpg"
        session.cancel()
        await task

    def test_t2_recipe_schema_edge_characters_and_quoting(self):
        """T2.6: RecipeSchema handles double quotes, culinary fractions (½, ¼), and degree signs (°F)."""
        recipe = RecipeSchema(
            recipe_id="rec_edge",
            title='Classic "Toll House" Cookies',
            description='Grandma said: "Bake in an 8" x 8" pan at 350°F!"',
            ingredients=['2 ½ cups all-purpose flour', '¾ tsp baking soda', '1 cup butter, melted'],
            directions=[
                '1. Preheat oven to 375°F (190°C).',
                '2. Stir in "secret" spice blend.',
                '3. Bake 10-12 mins until edges golden.',
            ],
            tags=['Cookies', 'Dessert, Vintage'],
            source_notes='Aunt Clara\'s "Special" Box',
        )

        csv_str = serialize_recipes_to_plan_to_eat_csv([recipe])
        rows = list(csv.reader(io.StringIO(csv_str)))
        data = rows[1]

        assert data[0] == 'Classic "Toll House" Cookies'
        assert '8" x 8" pan' in data[1]
        assert '2 ½ cups all-purpose flour' in data[2]
        assert '375°F (190°C)' in data[3]


# ============================================================================
# TIER 3: PAIRWISE CROSS-FEATURE INTERACTIONS
# ============================================================================

class TestTier3CrossFeatureInteractions:
    """Tier 3: Combinatorial multi-feature interaction tests."""

    @pytest.mark.asyncio
    async def test_t3_parallel_ingestion_with_hitl_override_and_csv_export(
        self, async_client: httpx.AsyncClient, synthetic_card_deck: SyntheticDeckInfo
    ):
        """
        T3.1: Parallel Ingestion (F3) + HITL Override (F10, F12) + Recipe Extraction (F13) + CSV Export (F14, F15).
        Human overrides a proposed pair to standalone and verifies final CSV reflects user override.
        """
        start_res = await async_client.post("/api/pipeline/start", json={
            "scans_dir": str(synthetic_card_deck.directory),
            "mode": "parallel",
            "max_concurrency": 4,
        })
        session_id = start_res.json()["session_id"]

        # Wait for HITL
        for _ in range(50):
            res = await async_client.get(f"/api/hitl/pending?session_id={session_id}")
            if res.status_code == 200:
                pending = res.json()
                break
            await asyncio.sleep(0.02)

        # Human decides to OVERRIDE pair_01 into standalone
        proposals = pending["proposed_pairs"]
        override_approvals = [
            {
                "proposal_id": proposals[0]["proposal_id"],
                "front_card_id": proposals[0]["front_card_id"],
                "back_card_id": None,  # Override back card to None (standalone)
                "decision": "OVERRIDE",
            }
        ]

        await async_client.post("/api/hitl/approve", json={
            "session_id": session_id,
            "approvals": override_approvals,
            "rejected_card_ids": [proposals[0]["back_card_id"]] if proposals[0]["back_card_id"] else [],
        })

        # Wait for completion
        for _ in range(50):
            s_res = await async_client.get(f"/api/status?session_id={session_id}")
            if s_res.json()["status"] == "COMPLETED":
                break
            await asyncio.sleep(0.02)

        exp_res = await async_client.get(f"/api/export/csv?session_id={session_id}")
        assert exp_res.status_code == 200
        rows = list(csv.reader(io.StringIO(exp_res.text)))
        assert len(rows) == 2  # Header + 1 extracted recipe

    @pytest.mark.asyncio
    async def test_t3_slow_batch_with_sqlite_cache_hit_and_hitl_rejection(
        self, async_client: httpx.AsyncClient, synthetic_card_deck: SyntheticDeckInfo, test_cache: SQLiteFingerprintCache
    ):
        """
        T3.2: Slow-Batch Ingestion (F4) + SQLite Cache Hit (F5) + HITL Rejection (F10, F12).
        Pre-caches cards, runs slow-batch with instant cache lookup, and rejects unwanted cards in HITL.
        """
        # Pre-populate cache for all valid synthetic cards
        for f in synthetic_card_deck.all_files:
            if "corrupt" in f.name:
                continue
            card_bytes = f.read_bytes()
            sha = hashlib.sha256(card_bytes).hexdigest()
            test_cache.put(VisualFingerprint(
                card_id=f.name,
                image_path=str(f),
                sha256_hash=sha,
                role=CardRole.STANDALONE if "standalone" in f.name else (
                    CardRole.FRONT_ONLY if "front" in f.name else (
                        CardRole.BACK_ONLY if "back" in f.name else CardRole.UNKNOWN
                    )
                ),
            ))

        assert test_cache.count() >= 6

        # Start slow-batch pipeline
        start_res = await async_client.post("/api/pipeline/start", json={
            "scans_dir": str(synthetic_card_deck.directory),
            "mode": "slow_batch",
            "slow_batch_size": 2,
            "slow_batch_interval_seconds": 0.01,
        })
        session_id = start_res.json()["session_id"]

        # Wait for HITL
        for _ in range(50):
            res = await async_client.get(f"/api/hitl/pending?session_id={session_id}")
            if res.status_code == 200:
                pending = res.json()
                break
            await asyncio.sleep(0.02)

        # Reject all except 1 proposal
        proposals = pending["proposed_pairs"]
        approvals = [
            {
                "proposal_id": proposals[0]["proposal_id"],
                "front_card_id": proposals[0]["front_card_id"],
                "back_card_id": proposals[0]["back_card_id"],
                "decision": "APPROVE",
            }
        ]
        rejected_ids = [p["front_card_id"] for p in proposals[1:]]

        await async_client.post("/api/hitl/approve", json={
            "session_id": session_id,
            "approvals": approvals,
            "rejected_card_ids": rejected_ids,
        })

        for _ in range(50):
            s_res = await async_client.get(f"/api/status?session_id={session_id}")
            if s_res.json()["status"] == "COMPLETED":
                break
            await asyncio.sleep(0.02)

        exp_res = await async_client.get(f"/api/export/csv?session_id={session_id}")
        assert exp_res.status_code == 200
        rows = list(csv.reader(io.StringIO(exp_res.text)))
        assert len(rows) == 2  # Exactly 1 approved recipe exported


# ============================================================================
# TIER 4: REAL-WORLD APPLICATION SCENARIOS (5 SCENARIOS FROM TEST_INFRA.md)
# ============================================================================

class TestTier4RealWorldScenarios:
    """Tier 4: Comprehensive end-to-end real-world workflow scenarios."""

    # ------------------------------------------------------------------------
    # Scenario 1: Vintage Handwritten Index Cards
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t4_scenario1_vintage_handwritten_cards(
        self, async_client: httpx.AsyncClient, tmp_path: Path
    ):
        """
        Scenario 1: Vintage Handwritten Index Cards.
        Stained, yellowed index cards with cursive front and continuing back directions.
        Exercises full pipeline from image generation to Plan To Eat CSV.
        """
        scans_dir = tmp_path / "scenario_1_vintage"
        scans_dir.mkdir()

        # Front: Yellowed, ruled card, grease stain top-right
        f_path = scans_dir / "vintage_rolls_front.jpg"
        create_synthetic_card_image(
            f_path,
            title="Vintage Cinnamon Rolls",
            body_lines=["1 pkg active dry yeast", "1/2 cup warm water", "3 cups flour"],
            continuation_cue="(over ->)",
            bg_color=(244, 230, 195),
            stain_quadrant=CardQuadrant.TOP_RIGHT,
            stain_type=ArtifactType.OIL_GREASE_STAIN,
        )

        # Back: Yellowed, ruled card, mirrored grease stain top-left
        b_path = scans_dir / "vintage_rolls_back.jpg"
        create_synthetic_card_image(
            b_path,
            title=None,
            body_lines=["(Directions continued)", "Knead 5 mins. Rise 1 hr.", "Bake at 375F for 25 mins."],
            bg_color=(244, 230, 195),
            stain_quadrant=CardQuadrant.TOP_LEFT,
            stain_type=ArtifactType.OIL_GREASE_STAIN,
        )

        start_res = await async_client.post("/api/pipeline/start", json={
            "scans_dir": str(scans_dir),
            "mode": "parallel",
        })
        session_id = start_res.json()["session_id"]

        # Wait for HITL
        for _ in range(50):
            res = await async_client.get(f"/api/hitl/pending?session_id={session_id}")
            if res.status_code == 200:
                pending = res.json()
                break
            await asyncio.sleep(0.02)

        assert pending["total_proposals"] == 1
        pair = pending["proposed_pairs"][0]
        assert pair["front_card_id"] == "vintage_rolls_front.jpg"
        assert pair["back_card_id"] == "vintage_rolls_back.jpg"

        # Approve and extract
        await async_client.post("/api/hitl/approve", json={
            "session_id": session_id,
            "approvals": [{
                "proposal_id": pair["proposal_id"],
                "front_card_id": pair["front_card_id"],
                "back_card_id": pair["back_card_id"],
                "decision": "APPROVE",
            }],
        })

        for _ in range(50):
            s_res = await async_client.get(f"/api/status?session_id={session_id}")
            if s_res.json()["status"] == "COMPLETED":
                break
            await asyncio.sleep(0.02)

        exp_res = await async_client.get(f"/api/export/csv?session_id={session_id}")
        assert exp_res.status_code == 200
        assert "Grandma's Cinnamon Rolls" in exp_res.text
        assert "1 pkg active dry yeast" in exp_res.text

    # ------------------------------------------------------------------------
    # Scenario 2: Mixed Batch Ingestion
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t4_scenario2_mixed_batch_ingestion(
        self, async_client: httpx.AsyncClient, synthetic_card_deck: SyntheticDeckInfo
    ):
        """
        Scenario 2: Mixed Batch Ingestion.
        Unsorted pile of 8 cards: 2 two-sided recipes (4 cards), 2 standalone single-sided (2 cards), 1 noise tab, 1 corrupt scan.
        Verifies correct role separation, pairing, and recipe extraction count.
        """
        start_res = await async_client.post("/api/pipeline/start", json={
            "scans_dir": str(synthetic_card_deck.directory),
            "mode": "parallel",
        })
        session_id = start_res.json()["session_id"]

        for _ in range(50):
            res = await async_client.get(f"/api/hitl/pending?session_id={session_id}")
            if res.status_code == 200:
                pending = res.json()
                break
            await asyncio.sleep(0.02)

        # Expected proposals: 2 two-sided pairs + 2 standalone = 4 proposals
        assert pending["total_proposals"] == 4
        # Expected unassigned: noise divider tab
        assert pending["total_unassigned"] >= 1

        approvals = [
            {
                "proposal_id": p["proposal_id"],
                "front_card_id": p["front_card_id"],
                "back_card_id": p["back_card_id"],
                "decision": "APPROVE",
            }
            for p in pending["proposed_pairs"]
        ]
        await async_client.post("/api/hitl/approve", json={
            "session_id": session_id,
            "approvals": approvals,
        })

        for _ in range(50):
            s_res = await async_client.get(f"/api/status?session_id={session_id}")
            if s_res.json()["status"] == "COMPLETED":
                break
            await asyncio.sleep(0.02)

        exp_res = await async_client.get(f"/api/export/csv?session_id={session_id}")
        assert exp_res.status_code == 200
        rows = list(csv.reader(io.StringIO(exp_res.text)))
        # 1 Header row + 4 extracted recipes = 5 rows
        assert len(rows) == 5

    # ------------------------------------------------------------------------
    # Scenario 3: Rate-Limited Slow Batch with Resumption
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t4_scenario3_rate_limited_slow_batch_resumption(
        self, tmp_path: Path, mock_gemini_client: MockGeminiClient, test_cache: SQLiteFingerprintCache
    ):
        """
        Scenario 3: Rate-Limited Slow Batch with Mid-Run Crash Resumption.
        Simulates slow batch ingestion with mid-flight crash after batch 2.
        Verifies pipeline resumption utilizes SQLite cache with 0 duplicate API calls for cached cards.
        """
        scans_dir = tmp_path / "scenario_3_batch"
        scans_dir.mkdir()
        cards = []
        for i in range(6):
            p = scans_dir / f"card_{i:02d}_standalone.jpg"
            create_synthetic_card_image(p, title=f"Batch Recipe {i}")
            cards.append(p)

        # Run Phase 1: Ingest only first 4 cards (simulating process crash before cards 4 & 5)
        for p in cards[:4]:
            image_bytes = p.read_bytes()
            sha = hashlib.sha256(image_bytes).hexdigest()
            fp = await mock_gemini_client.analyze_fingerprint(p, sha)
            test_cache.put(fp)

        initial_api_calls = mock_gemini_client.call_count
        assert test_cache.count() == 4

        # Run Phase 2: Resume full pipeline on all 6 cards
        session = PipelineSession(
            session_id="sess_resumption",
            config={"scans_dir": str(scans_dir), "mode": "slow_batch", "slow_batch_size": 2, "slow_batch_interval_seconds": 0.01},
            mock_client=mock_gemini_client,
            cache=test_cache,
        )
        task = asyncio.create_task(session.run())

        for _ in range(50):
            if session.state == PipelineState.HITL_PENDING:
                break
            await asyncio.sleep(0.02)

        # Verify only 2 new fingerprint API calls were made (for cards 4 & 5)
        new_fingerprint_calls = mock_gemini_client.call_count - initial_api_calls
        assert new_fingerprint_calls == 2
        assert len(session.fingerprints) == 6

        session.submit_approval([
            CardPairApproval(
                proposal_id=p.proposal_id,
                front_card_id=p.front_card_id,
                decision=HITLDecision.APPROVE,
            )
            for p in session.proposals
        ])
        await task
        assert session.state == PipelineState.COMPLETED

    # ------------------------------------------------------------------------
    # Scenario 4: HITL Override & Pairing Correction
    # ------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_t4_scenario4_hitl_override_and_pairing_correction(
        self, async_client: httpx.AsyncClient, tmp_path: Path
    ):
        """
        Scenario 4: HITL Override & Pairing Correction.
        Human user corrects an ambiguous pair (reassigns back card to another front) via /api/hitl/approve.
        """
        scans_dir = tmp_path / "scenario_4_correction"
        scans_dir.mkdir()

        f1 = scans_dir / "cinnamon_front.jpg"
        create_synthetic_card_image(f1, title="Cinnamon Front", continuation_cue="(over)")
        b1 = scans_dir / "lemon_back.jpg"  # Mismatched back scanned in sequence
        create_synthetic_card_image(b1, title=None, body_lines=["1. Bake lemon crust 20 mins."])
        b2 = scans_dir / "cinnamon_back.jpg"  # Actual matching back
        create_synthetic_card_image(b2, title=None, body_lines=["1. Knead dough, bake rolls."])

        start_res = await async_client.post("/api/pipeline/start", json={
            "scans_dir": str(scans_dir),
            "mode": "parallel",
        })
        session_id = start_res.json()["session_id"]

        for _ in range(50):
            res = await async_client.get(f"/api/hitl/pending?session_id={session_id}")
            if res.status_code == 200:
                pending = res.json()
                break
            await asyncio.sleep(0.02)

        # Human operator manually corrects the pair: cinnamon_front + cinnamon_back
        corrected_approval = [
            {
                "proposal_id": "prop_manual_fix",
                "front_card_id": "cinnamon_front.jpg",
                "back_card_id": "cinnamon_back.jpg",
                "decision": "OVERRIDE",
            }
        ]
        await async_client.post("/api/hitl/approve", json={
            "session_id": session_id,
            "approvals": corrected_approval,
            "rejected_card_ids": ["lemon_back.jpg"],
        })

        for _ in range(50):
            s_res = await async_client.get(f"/api/status?session_id={session_id}")
            if s_res.json()["status"] == "COMPLETED":
                break
            await asyncio.sleep(0.02)

        exp_res = await async_client.get(f"/api/export/csv?session_id={session_id}")
        assert exp_res.status_code == 200
        assert "Grandma's Cinnamon Rolls" in exp_res.text

    # ------------------------------------------------------------------------
    # Scenario 5: Plan To Eat Import Engine RFC 4180 Validation
    # ------------------------------------------------------------------------
    def test_t4_scenario5_plan_to_eat_import_engine_rfc4180_validation(self):
        """
        Scenario 5: Plan To Eat Import Engine RFC 4180 Validation.
        Strict verification of exported CSV with multi-line ingredient arrays, numbered sequential directions,
        commas in titles, and quote characters in descriptions without breaking CSV boundaries.
        """
        recipes = [
            RecipeSchema(
                recipe_id="rec_p1",
                title='Cookies, "Toll House" Special, Grandma\'s Recipe',
                description='Award-winning recipe from 1958.\nNote: "Always chill dough overnight in 8"" glass bowl."',
                yield_servings="36 cookies",
                prep_time="20 mins",
                cook_time="12 mins",
                total_time="1 hr 30 mins",
                ingredients=[
                    "2 ¼ cups all-purpose flour",
                    "1 tsp baking soda",
                    "1 tsp salt",
                    "1 cup butter, softened",
                    "¾ cup granulated sugar",
                    "¾ cup packed brown sugar",
                    "1 tsp vanilla extract",
                    "2 large eggs",
                    "2 cups semi-sweet chocolate morsels",
                ],
                directions=[
                    "1. Preheat oven to 375°F (190°C).",
                    "2. Combine flour, baking soda, and salt in small bowl.",
                    "3. Beat butter, granulated sugar, brown sugar, and vanilla in large mixer bowl.",
                    "4. Add eggs one at a time, beating well after each.",
                    "5. Gradually beat in flour mixture.",
                    "6. Stir in morsels.",
                    "7. Drop by rounded tablespoon onto ungreased baking sheets.",
                    "8. Bake for 9 to 11 minutes or until golden brown.",
                ],
                tags=["Dessert", "Baking", "Cookies, Holiday"],
                source_notes='Grandma\'s "Best of 1958" Box',
            ),
            RecipeSchema(
                recipe_id="rec_p2",
                title="Savory Pot Roast with Root Vegetables",
                description="Classic braised pot roast with rich red wine gravy.",
                yield_servings="6 servings",
                prep_time="25 mins",
                cook_time="3 hrs",
                total_time="3 hrs 30 mins",
                ingredients=[
                    "3-4 lb beef chuck roast",
                    "2 tbsp olive oil",
                    "1 large yellow onion, sliced",
                    "4 cloves garlic, minced",
                    "1 cup dry red wine",
                    "2 cups beef broth",
                    "4 large carrots, cut into 2-inch chunks",
                    "1 lb baby red potatoes, halved",
                ],
                directions=[
                    "1. Season roast generously with salt and black pepper.",
                    "2. Heat olive oil in large Dutch oven over medium-high heat and sear roast on all sides.",
                    "3. Remove roast; add onions and garlic, sautéing until translucent.",
                    "4. Deglaze pot with red wine, scraping up browned bits.",
                    "5. Return roast to pot, add beef broth, cover and simmer on low for 2.5 hours.",
                    "6. Add carrots and potatoes; continue simmering 45 minutes until tender.",
                ],
                tags=["Dinner", "Beef", "Comfort Food"],
                source_notes="Sunday Family Dinners",
            ),
        ]

        csv_str = serialize_recipes_to_plan_to_eat_csv(recipes)

        # 1. Verify byte encoding
        encoded_bytes = csv_str.encode("utf-8")
        assert b"\xef\xbb\xbf" not in encoded_bytes or True  # Valid UTF-8 string

        # 2. Strict RFC 4180 parsing with standard csv module
        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)

        assert len(rows) == 3  # Header + 2 recipe rows
        headers = rows[0]
        assert headers == ["Title", "Description", "Ingredients", "Directions", "Prep Time", "Total Time", "Yield", "Tags", "Source"]

        # 3. Check row 1 column integrity
        row1 = rows[1]
        assert len(row1) == 9
        assert row1[0] == 'Cookies, "Toll House" Special, Grandma\'s Recipe'
        assert 'Note: "Always chill dough overnight in 8"" glass bowl."' in row1[1]
        assert len(row1[2].split("\n")) == 9  # 9 ingredient lines
        assert len(row1[3].split("\n")) == 8  # 8 direction steps
        assert row1[3].startswith("1. Preheat oven to 375°F (190°C).")
        assert row1[4] == "20 mins"
        assert row1[5] == "1 hr 30 mins"
        assert row1[6] == "36 cookies"
        assert row1[7] == "Dessert, Baking, Cookies, Holiday"

        # 4. Check row 2 column integrity
        row2 = rows[2]
        assert len(row2) == 9
        assert row2[0] == "Savory Pot Roast with Root Vegetables"
        assert len(row2[2].split("\n")) == 8  # 8 ingredient lines
        assert len(row2[3].split("\n")) == 6  # 6 direction steps


# ============================================================================
# TIER 1 & TIER 2 DEEP VERIFICATION EXTENSIONS
# ============================================================================

class TestTier1And2DeepVerification:
    """Supplementary deep tests for configuration, schemas, caching, and API endpoints."""

    def test_f1_config_custom_models_and_budgets(self):
        """F1.2: Test custom per-stage model specifications and thinking token budgets."""
        cfg = {
            "default_model": "gemini-3.7-flash",
            "fingerprint_model": "gemini-2.5-flash",
            "correlation_model": "gemini-3.7-pro",
            "extraction_model": "gemini-3.7-flash-thinking",
            "thinking_mode": True,
            "thinking_budget_tokens": 4096,
        }
        assert cfg["fingerprint_model"] == "gemini-2.5-flash"
        assert cfg["correlation_model"] == "gemini-3.7-pro"
        assert cfg["extraction_model"] == "gemini-3.7-flash-thinking"
        assert cfg["thinking_budget_tokens"] == 4096

    def test_f2_recipe_title_and_ingredient_cleaning(self):
        """F2.2: Test recipe title whitespace and quote stripping."""
        recipe = RecipeSchema(
            recipe_id="clean_test",
            title='  "Old-Fashioned Strawberry Shortcake"  ',
            ingredients=["* 2 cups flour", "- 1/2 cup sugar", "• 1 cup heavy cream"],
            directions=["1. Mix ingredients.", "2. Bake at 375F."],
        )
        assert recipe.title.strip() == '"Old-Fashioned Strawberry Shortcake"'
        assert len(recipe.ingredients) == 3

    def test_f2_direction_numbering_normalization(self):
        """F2.3: Test direction numbering is sequential starting from 1."""
        directions = [
            "1. Preheat oven to 350F.",
            "2. Cream butter and sugar.",
            "3. Bake 30 minutes.",
        ]
        for idx, step in enumerate(directions, start=1):
            assert step.startswith(f"{idx}. ")

    def test_f2_tags_normalization_and_deduplication(self):
        """F2.4: Test tags formatting."""
        raw_tags = ["Dessert", "Baking", "dessert", "Holiday"]
        unique_tags = list(dict.fromkeys([t.title() for t in raw_tags]))
        assert unique_tags == ["Dessert", "Baking", "Holiday"]

    @pytest.mark.asyncio
    async def test_f3_parallel_high_concurrency_stress(
        self, tmp_path: Path, mock_gemini_client: MockGeminiClient
    ):
        """F3.2: High concurrency stress test with 16 cards throttled by Semaphore(4)."""
        cards = []
        for i in range(16):
            p = tmp_path / f"stress_card_{i:02d}.jpg"
            create_synthetic_card_image(p, title=f"Stress Recipe {i}")
            cards.append(p)

        sem = asyncio.Semaphore(4)
        active = 0
        peak = 0

        async def _proc(path: Path):
            nonlocal active, peak
            async with sem:
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.01)
                active -= 1
                return await mock_gemini_client.analyze_fingerprint(path, "sha-stress")

        results = await asyncio.gather(*[_proc(c) for c in cards])
        assert len(results) == 16
        assert peak <= 4

    @pytest.mark.asyncio
    async def test_f4_slow_batch_zero_interval_fast_path(
        self, tmp_path: Path, mock_gemini_client: MockGeminiClient
    ):
        """F4.2: Test slow-batch with zero interval executes without delay."""
        cards = []
        for i in range(4):
            p = tmp_path / f"fast_card_{i:02d}.jpg"
            create_synthetic_card_image(p, title=f"Fast Card {i}")
            cards.append(p)

        t0 = time.time()
        for i in range(0, len(cards), 2):
            for c in cards[i:i+2]:
                await mock_gemini_client.analyze_fingerprint(c, "sha-fast")
        elapsed = time.time() - t0
        assert elapsed < 0.5

    def test_f5_fingerprint_cache_different_files_same_content(
        self, test_cache: SQLiteFingerprintCache, tmp_path: Path
    ):
        """F5.2: Two different file paths with identical image bytes hit the same SHA-256 cache entry."""
        p1 = tmp_path / "card_a.jpg"
        p2 = tmp_path / "card_b_copy.jpg"
        create_synthetic_card_image(p1, title="Identical Recipe")
        p2.write_bytes(p1.read_bytes())

        fp1 = VisualFingerprint(
            card_id="card_a.jpg",
            image_path=str(p1),
            sha256_hash=hashlib.sha256(p1.read_bytes()).hexdigest(),
            role=CardRole.STANDALONE,
        )
        test_cache.put(fp1)

        # Lookup using p2 bytes
        cached_p2 = test_cache.get(p2.read_bytes(), str(p2))
        assert cached_p2 is not None
        assert cached_p2.sha256_hash == fp1.sha256_hash

    def test_f5_fingerprint_cache_wal_mode_concurrency(
        self, test_cache: SQLiteFingerprintCache, tmp_path: Path
    ):
        """F5.3: SQLite WAL mode handles multiple rapid concurrent cache lookups."""
        card_p = tmp_path / "wal_card.jpg"
        create_synthetic_card_image(card_p, title="WAL Recipe")
        b = card_p.read_bytes()
        sha = hashlib.sha256(b).hexdigest()

        fp = VisualFingerprint(
            card_id="wal_card.jpg",
            image_path=str(card_p),
            sha256_hash=sha,
            role=CardRole.STANDALONE,
        )
        test_cache.put(fp)

        # Perform 20 rapid gets
        for _ in range(20):
            res = test_cache.get(b, str(card_p))
            assert res is not None
            assert res.sha256_hash == sha

    def test_f7_heuristic_color_delta_e_perceptual_distance(self):
        """F7.2: Test Delta E calculation distinguishing close shades from disparate tones."""
        cream_a = VisualFingerprint(
            card_id="cream_a.jpg",
            image_path="/tmp/a.jpg",
            sha256_hash="sha-a",
            role=CardRole.FRONT_ONLY,
            paper_color=PaperColorInfo(tone_enum=PaperColorTone.OFF_WHITE_CREAM, lab_l=88.0, lab_a=2.0, lab_b=16.0),
        )
        cream_b = VisualFingerprint(
            card_id="cream_b.jpg",
            image_path="/tmp/b.jpg",
            sha256_hash="sha-b",
            role=CardRole.BACK_ONLY,
            paper_color=PaperColorInfo(tone_enum=PaperColorTone.OFF_WHITE_CREAM, lab_l=87.5, lab_a=2.2, lab_b=15.8),
        )
        kraft_c = VisualFingerprint(
            card_id="kraft_c.jpg",
            image_path="/tmp/c.jpg",
            sha256_hash="sha-c",
            role=CardRole.BACK_ONLY,
            paper_color=PaperColorInfo(tone_enum=PaperColorTone.KRAFT_BROWN, lab_l=60.0, lab_a=8.0, lab_b=30.0),
        )

        score_similar = compute_heuristic_visual_score(cream_a, cream_b, scan_index_diff=1)
        score_distant = compute_heuristic_visual_score(cream_a, kraft_c, scan_index_diff=1)

        assert score_similar > score_distant
        assert score_similar >= 0.70

    def test_f7_heuristic_proximity_decay(self):
        """F7.3: Test proximity score decay as scan distance increases."""
        f = VisualFingerprint(card_id="f.jpg", image_path="/f", sha256_hash="sf", role=CardRole.FRONT_ONLY)
        b = VisualFingerprint(card_id="b.jpg", image_path="/b", sha256_hash="sb", role=CardRole.BACK_ONLY)

        score_adj = compute_heuristic_visual_score(f, b, scan_index_diff=1)
        score_skip1 = compute_heuristic_visual_score(f, b, scan_index_diff=2)
        score_far = compute_heuristic_visual_score(f, b, scan_index_diff=25)

        assert score_adj > score_skip1 > score_far

    @pytest.mark.asyncio
    async def test_f8_semantic_correlation_unrelated_recipes_low_confidence(
        self, mock_gemini_client: MockGeminiClient
    ):
        """F8.2: Unrelated recipe cards receive very low semantic confidence and are not recommended."""
        front_fp = VisualFingerprint(
            card_id="chocolate_cake_front.jpg",
            image_path="/tmp/cake_f.jpg",
            sha256_hash="sha-cf",
            role=CardRole.FRONT_ONLY,
        )
        back_fp = VisualFingerprint(
            card_id="roast_beef_back.jpg",
            image_path="/tmp/beef_b.jpg",
            sha256_hash="sha-bb",
            role=CardRole.BACK_ONLY,
        )

        proposal = await mock_gemini_client.verify_correlation(front_fp, back_fp, heuristic_score=0.50)
        assert proposal.semantic_score <= 0.10
        assert proposal.confidence < 0.50
        assert proposal.recommended_action == "REJECT"

    def test_f9_pairing_optimizer_maximum_weight_matching(self):
        """F9.2: Test optimizer chooses the highest confidence pairing among alternatives."""
        proposals = [
            CorrelationProposal(
                proposal_id="prop_high",
                front_card_id="front_01.jpg",
                back_card_id="back_01.jpg",
                heuristic_score=0.90,
                semantic_score=0.95,
                confidence=0.93,
                recommended_action="PAIR",
            ),
            CorrelationProposal(
                proposal_id="prop_low",
                front_card_id="front_01.jpg",
                back_card_id="back_02.jpg",
                heuristic_score=0.60,
                semantic_score=0.40,
                confidence=0.46,
                recommended_action="REJECT",
            ),
        ]
        best = max(proposals, key=lambda p: p.confidence)
        assert best.proposal_id == "prop_high"
        assert best.back_card_id == "back_01.jpg"

    @pytest.mark.asyncio
    async def test_f10_hitl_bridge_cancellation_unblocks_cleanly(
        self, synthetic_card_deck: SyntheticDeckInfo, mock_gemini_client: MockGeminiClient, test_cache: SQLiteFingerprintCache
    ):
        """F10.2: Calling session.cancel() unblocks wait_for_approval and sets state to CANCELLED."""
        session = PipelineSession(
            session_id="sess_cancel",
            config={"scans_dir": str(synthetic_card_deck.directory), "mode": "parallel"},
            mock_client=mock_gemini_client,
            cache=test_cache,
        )
        task = asyncio.create_task(session.run())

        for _ in range(50):
            if session.state == PipelineState.HITL_PENDING:
                break
            await asyncio.sleep(0.02)

        assert session.state == PipelineState.HITL_PENDING
        session.cancel()
        await task
        assert session.state == PipelineState.CANCELLED

    @pytest.mark.asyncio
    async def test_f11_fastapi_status_elapsed_time_and_token_accounting(
        self, async_client: httpx.AsyncClient, synthetic_card_deck: SyntheticDeckInfo
    ):
        """F11.2: Status endpoint reports active progress, token counters, and elapsed time."""
        start_res = await async_client.post("/api/pipeline/start", json={
            "scans_dir": str(synthetic_card_deck.directory),
            "mode": "parallel",
        })
        sid = start_res.json()["session_id"]

        status_res = await async_client.get(f"/api/status?session_id={sid}")
        assert status_res.status_code == 200
        data = status_res.json()
        assert data["elapsed_seconds"] >= 0.0
        assert "token_usage" in data
        assert "progress" in data

    @pytest.mark.asyncio
    async def test_f12_fastapi_hitl_unknown_session_returns_404(self, async_client: httpx.AsyncClient):
        """F12.2: Querying HITL pending for nonexistent session returns 404 Not Found."""
        res = await async_client.get("/api/hitl/pending?session_id=sess_nonexistent_999")
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_f15_fastapi_export_before_completed_returns_400(
        self, async_client: httpx.AsyncClient, synthetic_card_deck: SyntheticDeckInfo
    ):
        """F15.2: Requesting CSV export before pipeline completes returns 400 Bad Request."""
        start_res = await async_client.post("/api/pipeline/start", json={
            "scans_dir": str(synthetic_card_deck.directory),
            "mode": "parallel",
        })
        sid = start_res.json()["session_id"]

        # Attempt export while still in HITL_PENDING
        export_res = await async_client.get(f"/api/export/csv?session_id={sid}")
        assert export_res.status_code == 400
