"""Tests for PipelineSession state machine, asyncio.Event HITL bridge, and SessionManager."""

import asyncio
import concurrent.futures
import time
from pathlib import Path
from typing import List

import pytest

from recipeboxer.agent.orchestrator import RecipeBoxerAgent
from recipeboxer.agent.session import PipelineSession, SessionManager, get_session_manager
from recipeboxer.ingestion.runner import IngestionMode
from recipeboxer.models.hitl import CardPairApproval, HITLDecision
from recipeboxer.models.pipeline import PipelineStartRequest, PipelineState
from tests.conftest import (
    MockGeminiClient,
    SQLiteFingerprintCache,
    SyntheticDeckInfo,
    create_synthetic_card_image,
)


class TestHITLBridge:
    """Test suite for PipelineSession lifecycle and HITL pause/resume mechanics."""

    def test_session_initial_state(self):
        """Test initial PipelineSession attributes and defaults."""
        session = PipelineSession(session_id="sess_init_test")
        assert session.session_id == "sess_init_test"
        assert session.state == PipelineState.IDLE
        assert session.cards == []
        assert session.fingerprints == {}
        assert session.proposals == []
        assert session.approvals == []
        assert session.recipes == []
        assert session.csv_content is None
        assert session.csv_output_path is None
        assert session.approval_event.is_set() is False
        assert session.cancellation_event.is_set() is False
        assert session.is_cancelled is False

    @pytest.mark.asyncio
    async def test_session_state_transitions_pause_and_resume(
        self,
        synthetic_card_deck: SyntheticDeckInfo,
        mock_gemini_client: MockGeminiClient,
        test_cache: SQLiteFingerprintCache,
    ):
        """Test clean transition from IDLE -> INGESTING -> FINGERPRINTING -> CORRELATING -> HITL_PENDING -> EXTRACTING -> EXPORTING -> COMPLETED."""
        session = PipelineSession(
            session_id="sess_test_bridge",
            config=PipelineStartRequest(
                scans_dir=str(synthetic_card_deck.directory),
                mode=IngestionMode.PARALLEL,
                max_concurrency=4,
            ),
            cache=test_cache,
            extractor=mock_gemini_client,
            mock_client=mock_gemini_client,
        )

        task = asyncio.create_task(session.run())

        # Poll until paused at HITL_PENDING
        for _ in range(100):
            if session.state == PipelineState.HITL_PENDING:
                break
            await asyncio.sleep(0.02)

        assert session.state == PipelineState.HITL_PENDING
        assert len(session.proposals) > 0
        assert session.approval_event.is_set() is False
        assert session.progress_metrics.total_scans > 0
        assert session.progress_metrics.fingerprinted > 0

        # Submit approvals to unblock
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
        assert session.approval_event.is_set() is True

        await task
        assert session.state == PipelineState.COMPLETED
        assert len(session.extracted_recipes) > 0
        assert session.csv_content is not None
        assert "Title,Description,Ingredients,Directions" in session.csv_content
        assert session.progress_metrics.percent_complete == 100.0

    @pytest.mark.asyncio
    async def test_session_cancellation_at_hitl_pending(
        self,
        synthetic_card_deck: SyntheticDeckInfo,
        mock_gemini_client: MockGeminiClient,
        test_cache: SQLiteFingerprintCache,
    ):
        """Test cancelling session while paused at HITL_PENDING cleanly transitions state to CANCELLED."""
        session = PipelineSession(
            session_id="sess_cancel",
            config=PipelineStartRequest(
                scans_dir=str(synthetic_card_deck.directory),
                mode=IngestionMode.PARALLEL,
            ),
            cache=test_cache,
            extractor=mock_gemini_client,
            mock_client=mock_gemini_client,
        )
        task = asyncio.create_task(session.run())

        for _ in range(100):
            if session.state == PipelineState.HITL_PENDING:
                break
            await asyncio.sleep(0.02)

        assert session.state == PipelineState.HITL_PENDING
        session.cancel()
        assert session.is_cancelled is True

        await task
        assert session.state == PipelineState.CANCELLED
        assert session.end_time is not None

    @pytest.mark.asyncio
    async def test_session_cancellation_during_ingestion(
        self,
        tmp_path: Path,
        mock_gemini_client: MockGeminiClient,
        test_cache: SQLiteFingerprintCache,
    ):
        """Test cooperative cancellation during ingestion pauses."""
        cards_dir = tmp_path / "cards_cancel_ingestion"
        cards_dir.mkdir()
        for i in range(10):
            p = cards_dir / f"card_{i:02d}.jpg"
            create_synthetic_card_image(p, title=f"Recipe {i}")

        session = PipelineSession(
            session_id="sess_cancel_ingestion",
            config=PipelineStartRequest(
                scans_dir=str(cards_dir),
                mode=IngestionMode.SLOW_BATCH,
                slow_batch_size=1,
                slow_batch_interval_seconds=1.0,
            ),
            cache=test_cache,
            extractor=mock_gemini_client,
            mock_client=mock_gemini_client,
        )

        task = asyncio.create_task(session.run())
        await asyncio.sleep(0.05)
        session.cancel()

        await task
        assert session.state == PipelineState.CANCELLED

    def test_resume_with_approvals_invalid_state_raises_error(self):
        """Test that resume_with_approvals raises ValueError if session is not in HITL_PENDING."""
        session = PipelineSession(session_id="sess_invalid_state")
        assert session.state == PipelineState.IDLE

        with pytest.raises(ValueError) as exc_info:
            session.resume_with_approvals(approvals=[])
        assert "Expected 'HITL_PENDING'" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_hitl_rejections_and_overrides(
        self,
        synthetic_card_deck: SyntheticDeckInfo,
        mock_gemini_client: MockGeminiClient,
        test_cache: SQLiteFingerprintCache,
    ):
        """Test that rejected proposals are omitted and overridden proposals extract correctly."""
        session = PipelineSession(
            session_id="sess_reject_override",
            config=PipelineStartRequest(
                scans_dir=str(synthetic_card_deck.directory),
                mode=IngestionMode.PARALLEL,
            ),
            cache=test_cache,
            extractor=mock_gemini_client,
            mock_client=mock_gemini_client,
        )
        task = asyncio.create_task(session.run())

        for _ in range(100):
            if session.state == PipelineState.HITL_PENDING:
                break
            await asyncio.sleep(0.02)

        assert session.state == PipelineState.HITL_PENDING
        assert len(session.proposals) >= 2

        # Approve first proposal, reject second proposal
        p1 = session.proposals[0]
        p2 = session.proposals[1]

        approvals = [
            CardPairApproval(
                proposal_id=p1.proposal_id,
                front_card_id=p1.front_card_id,
                back_card_id=None,  # Override to standalone
                decision=HITLDecision.OVERRIDE,
            ),
            CardPairApproval(
                proposal_id=p2.proposal_id,
                front_card_id=p2.front_card_id,
                back_card_id=p2.back_card_id,
                decision=HITLDecision.REJECT,
            ),
        ]

        session.submit_approval(approvals, rejected_card_ids=[p2.front_card_id])
        await task

        assert session.state == PipelineState.COMPLETED
        # Exactly 1 recipe extracted (since 1 was rejected)
        assert len(session.extracted_recipes) == 1
        assert session.progress_metrics.extracted == 1

    def test_token_usage_telemetry_accounting(self):
        """Test token accumulation in session telemetry."""
        session = PipelineSession(session_id="sess_tokens")
        assert session.token_usage.total_tokens == 0

        session.record_token_usage(prompt_tokens=100, candidates_tokens=50, thoughts_tokens=25)
        assert session.token_usage.prompt_tokens == 100
        assert session.token_usage.candidates_tokens == 50
        assert session.token_usage.thoughts_tokens == 25
        assert session.token_usage.total_tokens == 175

        session.record_tokens(prompt_tokens=200, candidates_tokens=100, thoughts_tokens=50)
        assert session.token_usage.prompt_tokens == 300
        assert session.token_usage.candidates_tokens == 150
        assert session.token_usage.thoughts_tokens == 75
        assert session.token_usage.total_tokens == 525

    def test_progress_metrics_and_status_model(self):
        """Test stage-weighted percent_complete calculations."""
        session = PipelineSession(session_id="sess_status")
        status_idle = session.get_status()
        assert status_idle.status == PipelineState.IDLE
        assert status_idle.progress.percent_complete == 0.0

        session.state = PipelineState.INGESTING
        assert session.get_status().progress.percent_complete == 5.0

        session.state = PipelineState.FINGERPRINTING
        session.progress_metrics.total_scans = 10
        session.progress_metrics.fingerprinted = 5
        # 5% + 35% * (5/10) = 22.5%
        assert session.get_status().progress.percent_complete == 22.5

        session.state = PipelineState.CORRELATING
        assert session.get_status().progress.percent_complete == 45.0

        session.state = PipelineState.HITL_PENDING
        assert session.get_status().progress.percent_complete == 55.0

        session.state = PipelineState.EXTRACTING
        session.progress_metrics.approved = 4
        session.progress_metrics.extracted = 2
        # 55% + 35% * (2/4) = 72.5%
        assert session.get_status().progress.percent_complete == 72.5

        session.state = PipelineState.EXPORTING
        assert session.get_status().progress.percent_complete == 95.0

        session.state = PipelineState.COMPLETED
        assert session.get_status().progress.percent_complete == 100.0


class TestSessionManager:
    """Test suite for thread-safe SessionManager registry."""

    def test_session_manager_crud_and_active_tracking(self):
        """Test SessionManager creation, active tracking, and lookup."""
        manager = SessionManager()
        req = PipelineStartRequest(scans_dir="/tmp/test", mode=IngestionMode.PARALLEL)

        s1 = manager.create_session(req)
        assert manager.get_active_session_id() == s1.session_id
        assert manager.get_session(s1.session_id) == s1
        assert manager.get_session() == s1
        assert manager.get_active_session() == s1

        s2 = manager.create_session(req)
        assert manager.get_active_session_id() == s2.session_id
        assert manager.get_session(s1.session_id) == s1
        assert manager.get_session(s2.session_id) == s2
        assert manager.get_session() == s2
        assert len(manager.list_sessions()) == 2

        manager.clear()
        assert len(manager.list_sessions()) == 0
        assert manager.get_active_session_id() is None
        assert manager.get_session() is None

    def test_session_manager_cleanup_stale_sessions(self):
        """Test cleanup of expired sessions."""
        manager = SessionManager()
        s1 = manager.create_session()
        s1.end_time = time.monotonic() - 100.0  # Ended 100 seconds ago

        s2 = manager.create_session()
        s2.end_time = time.monotonic() - 2.0    # Ended 2 seconds ago

        s3 = manager.create_session()
        s3.end_time = None                     # Still running

        cleaned = manager.cleanup_stale_sessions(max_age_seconds=50.0)
        assert cleaned == 1
        assert manager.get_session(s1.session_id) is None
        assert manager.get_session(s2.session_id) is not None
        assert manager.get_session(s3.session_id) is not None

    def test_session_manager_thread_safety(self):
        """Test concurrent session creation across multiple threads."""
        manager = SessionManager()

        def _create_session(idx: int):
            return manager.create_session(session_id=f"thread_sess_{idx}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(_create_session, i) for i in range(50)]
            for f in concurrent.futures.as_completed(futures):
                f.result()

        assert len(manager.list_sessions()) == 50
