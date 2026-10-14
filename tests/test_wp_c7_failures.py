"""Unit and integration tests for WP-C7: Orchestrator StageFailure catch, No-Key fail fast, and degraded/unverified surfacing.

Enforces:
- INV-AC4: No silent fabrication or dropping of failed recipes; failures captured as StageFailure in HITL and session status.
- INV-AC6 & D5: Fail fast if no API key and no injected client; scanning still executes.
- Surfacing degraded fingerprints (degraded=True) and unverified proposals (semantic_verified=False) with needs_review=True.
- Additive API status and HITL queue exposure.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, List
import pytest
from fastapi.testclient import TestClient

from recipeboxer.agent.orchestrator import RecipeBoxerAgent
from recipeboxer.agent.session import PipelineSession, SessionManager
from recipeboxer.api.app import create_app
from recipeboxer.config import Settings
from recipeboxer.ingestion.scanner import ScannedCard
from recipeboxer.models.correlation import CorrelationProposal, CorrelationResult
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.failures import (
    ExtractionUnavailable,
    FailureReason,
    PipelineStage,
    StageFailure,
)
from recipeboxer.models.hitl import HitlItemType
from recipeboxer.models.pipeline import PipelineState
from recipeboxer.models.recipe import RecipeSchema


@pytest.fixture
def sample_jpeg_bytes() -> bytes:
    """Minimal valid JPEG byte sequence."""
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c"
        b"\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c"
        b"\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01"
        b"\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01"
        b"\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06"
        b"\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
    )


@pytest.fixture
def scans_directory(tmp_path: Path, sample_jpeg_bytes: bytes) -> Path:
    """Creates a temporary scan directory with two sample card images."""
    d = tmp_path / "scans"
    d.mkdir(parents=True, exist_ok=True)
    (d / "card_001.jpg").write_bytes(sample_jpeg_bytes)
    (d / "card_002.jpg").write_bytes(sample_jpeg_bytes)
    return d


class MockFailingExtractor:
    """Mock extractor that raises ExtractionUnavailable."""

    def __init__(self, failure_msg: str = "Vision model extraction offline"):
        self.failure_msg = failure_msg

    async def extract_cards(self, cards: List[Any]) -> RecipeSchema:
        raise ExtractionUnavailable(
            message=self.failure_msg,
            reason=FailureReason.MODEL_UNAVAILABLE,
            card_ids=[getattr(c, "card_id", "card") for c in cards],
            retryable=True,
        )


class MockMixedExtractor:
    """Mock extractor that succeeds on the first call and fails on subsequent calls."""

    def __init__(self):
        self.call_count = 0

    async def extract_cards(self, cards: List[Any]) -> RecipeSchema:
        self.call_count += 1
        if self.call_count == 1:
            return RecipeSchema(
                recipe_id="rec_success_1",
                title="Grandma's Cinnamon Rolls",
                description="A cherished Sunday baking tradition.",
                ingredients=["2 cups flour", "1 cup sugar", "1 tbsp cinnamon"],
                directions=["1. Mix ingredients.", "2. Bake at 350F for 25 mins."],
            )
        raise ExtractionUnavailable(
            message="Upstream vision service quota exceeded",
            reason=FailureReason.RATE_LIMITED,
            card_ids=[getattr(c, "card_id", "card") for c in cards],
            retryable=True,
        )


class MockPassingFingerprinter:
    """Mock visual fingerprinter returning valid fingerprints."""

    async def fingerprint_card(self, card: ScannedCard) -> VisualFingerprint:
        return VisualFingerprint(
            card_id=card.card_id,
            image_path=str(card.file_path),
            sha256_hash=card.sha256_hash,
            role=CardRole.STANDALONE,
            role_confidence=0.95,
            degraded=False,
        )

    extract_fingerprint = fingerprint_card
    extract = fingerprint_card


class MockDegradedFingerprinter:
    """Mock visual fingerprinter returning degraded fingerprints."""

    async def fingerprint_card(self, card: ScannedCard) -> VisualFingerprint:
        return VisualFingerprint(
            card_id=card.card_id,
            image_path=str(card.file_path),
            sha256_hash=card.sha256_hash,
            role=CardRole.UNKNOWN,
            role_confidence=0.30,
            degraded=True,
        )

    extract_fingerprint = fingerprint_card
    extract = fingerprint_card


@pytest.mark.asyncio
async def test_injected_extractor_unavailable_yields_failed_recipe_and_hitl_item(
    scans_directory: Path,
):
    """INV-AC4: Failing extractor must NOT drop recipe; it must yield a FAILED recipe entry and a HITL item."""
    session = PipelineSession(session_id="sess_c7_fail_1")
    session.config = {"scans_dir": str(scans_directory), "auto_approve": True}
    session.extractor = MockFailingExtractor()

    agent = RecipeBoxerAgent(
        visual_extractor=MockPassingFingerprinter(),
        recipe_extractor=session.extractor,
    )

    await asyncio.wait_for(agent.run_pipeline(session), timeout=30.0)

    # Invariants verification: must NOT drop recipe
    assert len(session.recipes) == 1, "Recipe must NOT be dropped silently!"
    failed_recipe = session.recipes[0]
    assert getattr(failed_recipe, "status", None) == "FAILED"
    assert hasattr(failed_recipe, "failure")
    assert failed_recipe.failure is not None
    assert failed_recipe.failure.stage == PipelineStage.EXTRACTION

    # HITL item verification
    assert len(session.hitl_queue) >= 1
    hitl_failures = [
        item for item in session.hitl_queue
        if getattr(item, "item_type", None) == HitlItemType.STAGE_FAILURE
        or getattr(item, "kind", None) == "STAGE_FAILURE"
    ]
    assert len(hitl_failures) >= 1
    assert hitl_failures[0].failure is not None
    assert hitl_failures[0].failure.stage == PipelineStage.EXTRACTION

    # Status and progress metrics
    status = session.get_status()
    assert len(status.failures) >= 1
    assert status.failed_extractions_count >= 1
    assert status.progress.failed_extractions >= 1


@pytest.mark.asyncio
async def test_mixed_batch_one_succeeds_one_fails_preserves_both(
    tmp_path: Path, sample_jpeg_bytes: bytes
):
    """Mixed batch with one successful recipe and one failing recipe must keep both visible."""
    scans_dir = tmp_path / "scans_mixed"
    scans_dir.mkdir(parents=True, exist_ok=True)
    p1 = scans_dir / "card_001.jpg"
    p2 = scans_dir / "card_002.jpg"
    p1.write_bytes(sample_jpeg_bytes)
    p2.write_bytes(sample_jpeg_bytes)

    session = PipelineSession(session_id="sess_c7_mixed")
    session.config = {"scans_dir": str(scans_dir), "auto_approve": True}
    session.extractor = MockMixedExtractor()

    # Pre-populate 2 standalone proposals
    session.cards = [
        ScannedCard(card_id="card_001", file_path=p1, sha256_hash="h1"),
        ScannedCard(card_id="card_002", file_path=p2, sha256_hash="h2"),
    ]
    session.fingerprints = {
        "card_001": VisualFingerprint(
            card_id="card_001", image_path=str(p1), sha256_hash="h1", role=CardRole.STANDALONE
        ),
        "card_002": VisualFingerprint(
            card_id="card_002", image_path=str(p2), sha256_hash="h2", role=CardRole.STANDALONE
        ),
    }
    session.proposals = [
        CorrelationProposal(proposal_id="prop_01", ordered_card_ids=["card_001"], is_standalone=True),
        CorrelationProposal(proposal_id="prop_02", ordered_card_ids=["card_002"], is_standalone=True),
    ]

    agent = RecipeBoxerAgent(
        visual_extractor=MockPassingFingerprinter(),
        recipe_extractor=session.extractor,
    )

    await asyncio.wait_for(agent.run_pipeline(session), timeout=30.0)

    # Both recipes must be present
    assert len(session.recipes) == 2, f"Expected 2 recipes, found {len(session.recipes)}"
    success_recipes = [r for r in session.recipes if getattr(r, "status", None) != "FAILED"]
    failed_recipes = [r for r in session.recipes if getattr(r, "status", None) == "FAILED"]

    assert len(success_recipes) == 1
    assert success_recipes[0].title == "Grandma's Cinnamon Rolls"
    assert len(failed_recipes) == 1
    assert failed_recipes[0].failure is not None
    assert failed_recipes[0].failure.stage == PipelineStage.EXTRACTION


@pytest.mark.asyncio
async def test_no_key_fail_fast_without_injected_client(
    scans_directory: Path, monkeypatch
):
    """INV-AC6, D5: Without injected client and without Gemini key, fail fast with NO_API_KEY.
    Scanning/ingestion still completes, but analysis stages are blocked.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("RECIPEBOXER_GEMINI_API_KEY", raising=False)

    clean_settings = Settings(gemini_api_key=None)

    session = PipelineSession(session_id="sess_c7_nokey", settings=clean_settings)
    session.config = {"scans_dir": str(scans_directory)}

    # Agent with no injected mock clients and empty settings
    agent = RecipeBoxerAgent(settings=clean_settings)

    await asyncio.wait_for(agent.run_pipeline(session), timeout=30.0)

    # Must fail fast before analysis
    assert session.state == PipelineState.FAILED
    assert session.failures, "Session must have recorded a StageFailure"
    nokey_fail = session.failures[0]
    assert nokey_fail.reason == FailureReason.NO_API_KEY
    assert nokey_fail.retryable is False

    # Scanning / Ingestion still completed
    assert len(session.cards) == 2, "Scanning must still list and hash files"
    assert session.progress_metrics.total_scans == 2

    # Fingerprinting & Correlation did NOT run
    assert len(session.fingerprints) == 0
    assert len(session.proposals) == 0
    assert len(session.recipes) == 0


@pytest.mark.asyncio
async def test_degraded_fingerprints_and_unverified_proposals_surfaced(
    scans_directory: Path,
):
    """Degraded fingerprints (degraded=True) and unverified proposals (semantic_verified=False)
    must be counted in status and marked with needs_review=True.
    """
    p1 = scans_directory / "card_001.jpg"
    p2 = scans_directory / "card_002.jpg"

    session = PipelineSession(session_id="sess_c7_degraded")
    session.config = {"scans_dir": str(scans_directory), "auto_approve": False}
    session.mock_client = True  # bypass no-key check

    class MockUnverifiedOptimizer:
        async def optimize(self, fps: List[VisualFingerprint]) -> CorrelationResult:
            card_ids = [fp.card_id for fp in fps]
            return CorrelationResult(
                proposals=[
                    CorrelationProposal(
                        proposal_id="prop_unverified_1",
                        ordered_card_ids=card_ids,
                        semantic_verified=False,
                        recommended_action="SEQUENCE",
                    )
                ],
                unmatched_card_ids=[],
            )

    session.optimizer = MockUnverifiedOptimizer()
    agent = RecipeBoxerAgent(visual_extractor=MockDegradedFingerprinter(), pairing_optimizer=session.optimizer)

    session.cards = [
        ScannedCard(card_id="card_001", file_path=p1, sha256_hash="h1"),
        ScannedCard(card_id="card_002", file_path=p2, sha256_hash="h2"),
    ]
    session.fingerprints = {
        "card_001": VisualFingerprint(
            card_id="card_001", image_path=str(p1), sha256_hash="h1", role=CardRole.SEQUENCE_START, degraded=True
        ),
        "card_002": VisualFingerprint(
            card_id="card_002", image_path=str(p2), sha256_hash="h2", role=CardRole.SEQUENCE_CONTINUATION, degraded=True
        ),
    }
    session.proposals = [
        CorrelationProposal(
            proposal_id="prop_unverified_1",
            ordered_card_ids=["card_001", "card_002"],
            semantic_verified=False,
            recommended_action="SEQUENCE",
        )
    ]

    # Pre-pipeline status check
    status = session.get_status()
    assert status.degraded_fingerprints_count == 2
    assert status.unverified_proposals_count == 1

    # Run pipeline in background to reach HITL boundary without blocking
    task = agent.run_in_background(session)
    for _ in range(100):
        if session.state == PipelineState.HITL_PENDING:
            break
        await asyncio.sleep(0.05)

    # Pipeline pauses at HITL_PENDING for review
    assert session.state == PipelineState.HITL_PENDING
    assert len(session.proposals) >= 1
    p = session.proposals[0]
    assert getattr(p, "needs_review", False) is True
    assert p.recommended_action == "NEEDS_REVIEW"

    # Final status verification
    final_status = session.get_status()
    assert final_status.degraded_fingerprints_count >= 1
    assert final_status.unverified_proposals_count >= 1

    # Cleanup background task
    session.cancel()
    try:
        await asyncio.wait_for(task, timeout=2.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass


def test_api_status_and_hitl_queue_endpoints_expose_failures():
    """Expose failures in existing status and session endpoints, and provide HITL queue with failure items."""
    app = create_app()
    client = TestClient(app)

    manager: SessionManager = app.state.session_manager
    session = manager.create_session(session_id="sess_api_test_failures")

    fail = StageFailure(
        stage=PipelineStage.EXTRACTION,
        reason=FailureReason.MODEL_UNAVAILABLE,
        message="Model unavailable for recipe extraction",
        card_ids=["card_001", "card_002"],
        retryable=True,
    )
    session.add_failure(fail)
    session.enqueue_hitl_failure(fail)
    session.degraded_fingerprints_count = 1
    session.unverified_proposals_count = 1

    # 1. GET /api/status includes failures
    resp = client.get(f"/api/status?session_id={session.session_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert "failures" in data
    assert len(data["failures"]) == 1
    assert data["failures"][0]["stage"] == "EXTRACTION"
    assert data["failures_count"] == 1
    assert data["degraded_fingerprints_count"] == 1
    assert data["unverified_proposals_count"] == 1

    # 2. GET /api/sessions/{session_id} includes failures and hitl_queue
    resp_sess = client.get(f"/api/sessions/{session.session_id}")
    assert resp_sess.status_code == 200
    sess_data = resp_sess.json()
    assert "failures" in sess_data
    assert len(sess_data["failures"]) == 1
    assert "hitl_queue" in sess_data
    assert len(sess_data["hitl_queue"]) >= 1

    # 3. GET /api/hitl/queue includes failure items
    resp_queue = client.get(f"/api/hitl/queue?session_id={session.session_id}")
    assert resp_queue.status_code == 200
    queue_data = resp_queue.json()
    assert "items" in queue_data
    assert "failures" in queue_data
    assert len(queue_data["failures"]) == 1
    assert len(queue_data["failure_items"]) == 1

    # 4. GET /api/sessions/{session_id}/hitl/queue includes failure items
    resp_sess_queue = client.get(f"/api/sessions/{session.session_id}/hitl/queue")
    assert resp_sess_queue.status_code == 200
    sess_queue_data = resp_sess_queue.json()
    assert len(sess_queue_data["failures"]) == 1
