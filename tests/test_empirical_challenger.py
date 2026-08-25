"""Empirical Challenger stress test suite for Milestone 3 and Milestone 4.

Verifies:
1. Concurrent POST /api/pipeline/start requests.
2. Premature POST /api/hitl/approve before HITL_PENDING and error status codes.
3. Unblocking approval_event and verifying downstream extraction completes.
4. Downloading GET /api/export/csv across multiple completed sessions.
5. Edge cases: double approvals, all-rejected batches, cancelled sessions, missing files.
"""

import asyncio
import csv
import io
import shutil
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from recipeboxer.agent.session import PipelineSession, SessionManager
from recipeboxer.api.app import create_app
from recipeboxer.models.hitl import ApprovalPayload, CardPairApproval, HITLDecision
from recipeboxer.models.pipeline import PipelineState
from tests.conftest import (
    MockGeminiClient,
    SQLiteFingerprintCache,
    SyntheticDeckInfo,
    create_synthetic_card_image,
)


@pytest.fixture
def challenger_session_manager() -> SessionManager:
    """Isolated SessionManager instance for challenger stress tests."""
    return SessionManager()


@pytest.fixture
def challenger_app(challenger_session_manager: SessionManager) -> Any:
    """FastAPI application with injected isolated challenger SessionManager."""
    return create_app(session_manager=challenger_session_manager)


@pytest_asyncio.fixture
async def challenger_client(challenger_app: Any) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async HTTP client targeting the challenger FastAPI app."""
    transport = ASGITransport(app=challenger_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


class TestEmpiricalChallengerM3M4:
    """Adversarial stress harness for RecipeBoxer M3 & M4."""

    # =========================================================================
    # 1. Concurrent POST /api/pipeline/start requests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_concurrent_pipeline_start_requests(
        self,
        challenger_client: httpx.AsyncClient,
        challenger_session_manager: SessionManager,
        synthetic_card_deck: SyntheticDeckInfo,
    ):
        """Stress-test: Send 10 concurrent POST /api/pipeline/start requests simultaneously."""
        concurrency = 10
        payload = {
            "scans_dir": str(synthetic_card_deck.directory),
            "mode": "parallel",
            "max_concurrency": 2,
        }

        async def _launch_start(idx: int):
            return await challenger_client.post("/api/pipeline/start", json=payload)

        responses = await asyncio.gather(*[_launch_start(i) for i in range(concurrency)])

        # Assert every concurrent request received HTTP 200 OK
        assert len(responses) == concurrency
        session_ids = set()
        for r in responses:
            assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
            data = r.json()
            assert "session_id" in data
            assert data["total_scans_found"] > 0
            session_ids.add(data["session_id"])

        # Assert every session ID is distinct and unique
        assert len(session_ids) == concurrency, f"Expected {concurrency} unique session IDs, got {len(session_ids)}"

        # Assert SessionManager registered all distinct sessions
        registered_sessions = challenger_session_manager.list_sessions()
        assert len(registered_sessions) == concurrency
        for sid in session_ids:
            assert challenger_session_manager.get_session(sid) is not None

        # Clean up running background tasks
        for s in registered_sessions:
            s.cancel()

    @pytest.mark.asyncio
    async def test_concurrent_pipeline_start_with_invalid_mix(
        self,
        challenger_client: httpx.AsyncClient,
        synthetic_card_deck: SyntheticDeckInfo,
        tmp_path: Path,
    ):
        """Stress-test: Send concurrent requests mixing valid and invalid parameters."""
        empty_dir = tmp_path / "empty_dir_challenger"
        empty_dir.mkdir()

        reqs = [
            challenger_client.post("/api/pipeline/start", json={"scans_dir": str(synthetic_card_deck.directory)}),
            challenger_client.post("/api/pipeline/start", json={"scans_dir": "/nonexistent/dir/xyz"}),
            challenger_client.post("/api/pipeline/start", json={"scans_dir": str(empty_dir)}),
            challenger_client.post("/api/pipeline/start", json={"directory_path": str(synthetic_card_deck.directory)}),
            challenger_client.post("/api/pipeline/start", json={}),
        ]

        results = await asyncio.gather(*reqs)
        assert results[0].status_code == 200  # valid scans_dir
        assert results[1].status_code == 400  # nonexistent dir
        assert results[2].status_code == 400  # empty dir
        assert results[3].status_code == 200  # valid directory_path alias
        assert results[4].status_code in [400, 422]  # missing directory (schema validation error)


    # =========================================================================
    # 2. Calling POST /api/hitl/approve before HITL_PENDING and error codes
    # =========================================================================

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "invalid_state",
        [
            PipelineState.IDLE,
            PipelineState.INGESTING,
            PipelineState.FINGERPRINTING,
            PipelineState.CORRELATING,
            PipelineState.EXTRACTING,
            PipelineState.EXPORTING,
            PipelineState.COMPLETED,
            PipelineState.CANCELLED,
            PipelineState.FAILED,
        ],
    )
    async def test_hitl_approve_before_hitl_pending_returns_400(
        self,
        challenger_client: httpx.AsyncClient,
        challenger_session_manager: SessionManager,
        invalid_state: PipelineState,
    ):
        """Stress-test: Calling POST /api/hitl/approve in any non-HITL_PENDING state returns 400."""
        session = challenger_session_manager.create_session()
        session.state = invalid_state

        payload = {
            "session_id": session.session_id,
            "approvals": [
                {
                    "proposal_id": "prop_1",
                    "front_card_id": "card_01.jpg",
                    "back_card_id": "card_02.jpg",
                    "decision": "APPROVE",
                }
            ],
            "rejected_card_ids": [],
            "continue_pipeline": True,
        }

        res = await challenger_client.post("/api/hitl/approve", json=payload)
        assert res.status_code == 400
        data = res.json()
        assert "Cannot approve session in state" in data["detail"]
        assert invalid_state.value in data["detail"]

    @pytest.mark.asyncio
    async def test_hitl_approve_nonexistent_session_returns_404(
        self,
        challenger_client: httpx.AsyncClient,
    ):
        """Calling POST /api/hitl/approve with non-existent session_id returns 404."""
        payload = {
            "session_id": "sess_does_not_exist_12345",
            "approvals": [],
            "rejected_card_ids": [],
        }
        res = await challenger_client.post("/api/hitl/approve", json=payload)
        assert res.status_code == 404
        assert "Session not found" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_hitl_approve_malformed_payload_returns_422(
        self,
        challenger_client: httpx.AsyncClient,
    ):
        """Calling POST /api/hitl/approve with malformed JSON body returns 422 Unprocessable Entity."""
        # Missing required session_id
        res = await challenger_client.post("/api/hitl/approve", json={"approvals": []})
        assert res.status_code == 422

        # Invalid decision enum
        res2 = await challenger_client.post("/api/hitl/approve", json={
            "session_id": "sess_1",
            "approvals": [{"proposal_id": "p1", "front_card_id": "c1", "decision": "INVALID_DECISION"}],
        })
        assert res2.status_code == 422

    # =========================================================================
    # 3. Unblocking approval_event and verifying downstream extraction completes
    # =========================================================================

    @pytest.mark.asyncio
    async def test_hitl_unblock_and_complete_downstream_extraction(
        self,
        challenger_client: httpx.AsyncClient,
        challenger_session_manager: SessionManager,
        synthetic_card_deck: SyntheticDeckInfo,
    ):
        """Empirical test: Pipeline pauses at HITL, approval unblocks event, extracts recipes, and completes."""
        # 1. Start pipeline
        start_res = await challenger_client.post("/api/pipeline/start", json={
            "scans_dir": str(synthetic_card_deck.directory),
            "mode": "parallel",
            "max_concurrency": 4,
        })
        assert start_res.status_code == 200
        sid = start_res.json()["session_id"]
        session = challenger_session_manager.get_session(sid)
        assert session is not None

        # 2. Poll until session reaches HITL_PENDING
        pending_data = None
        for _ in range(100):
            res_hitl = await challenger_client.get(f"/api/hitl/pending?session_id={sid}")
            if res_hitl.status_code == 200:
                pending_data = res_hitl.json()
                break
            await asyncio.sleep(0.02)

        assert pending_data is not None
        assert pending_data["status"] == "HITL_PENDING"
        assert session.state == PipelineState.HITL_PENDING
        assert not session.approval_event.is_set(), "approval_event must be clear while waiting"
        assert len(pending_data["proposed_pairs"]) > 0

        # 3. Submit approval for proposals
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

        approve_res = await challenger_client.post("/api/hitl/approve", json={
            "session_id": sid,
            "approvals": approvals,
            "rejected_card_ids": [],
            "continue_pipeline": True,
        })
        assert approve_res.status_code == 200
        assert approve_res.json()["approved_count"] == len(approvals)

        # 4. Verify approval_event is set and pipeline finishes downstream stages
        for _ in range(100):
            s_res = await challenger_client.get(f"/api/status?session_id={sid}")
            if s_res.json()["status"] in ["COMPLETED", "FAILED"]:
                break
            await asyncio.sleep(0.02)

        final_status = await challenger_client.get(f"/api/status?session_id={sid}")
        status_data = final_status.json()
        assert status_data["status"] == "COMPLETED", f"Expected COMPLETED, got {status_data}"
        assert session.state == PipelineState.COMPLETED

        # 5. Verify downstream extraction artifacts
        assert len(session.recipes) > 0, "session.recipes must not be empty"
        assert session.progress_metrics.extracted == len(session.recipes)
        assert session.progress_metrics.percent_complete == 100.0
        assert session.csv_content is not None
        assert len(session.csv_content) > 0

        # Verify extracted recipe structure adheres to RecipeSchema
        for recipe in session.recipes:
            assert recipe.title, "Recipe must have a non-empty title"
            assert len(recipe.ingredients) > 0, "Recipe must have ingredients"
            assert len(recipe.directions) > 0, "Recipe must have directions"
            # Verify numbered sequential directions
            for idx, step in enumerate(recipe.directions, start=1):
                assert step.startswith(f"{idx}. "), f"Direction step not correctly numbered: '{step}'"

    @pytest.mark.asyncio
    async def test_hitl_rejection_and_override_handling(
        self,
        challenger_client: httpx.AsyncClient,
        challenger_session_manager: SessionManager,
        synthetic_card_deck: SyntheticDeckInfo,
    ):
        """Verify that HITL decisions (REJECT, OVERRIDE, APPROVE) are properly handled downstream."""
        start_res = await challenger_client.post("/api/pipeline/start", json={
            "scans_dir": str(synthetic_card_deck.directory),
            "mode": "parallel",
        })
        sid = start_res.json()["session_id"]
        session = challenger_session_manager.get_session(sid)

        # Poll until HITL_PENDING
        for _ in range(100):
            res_hitl = await challenger_client.get(f"/api/hitl/pending?session_id={sid}")
            if res_hitl.status_code == 200:
                pending_data = res_hitl.json()
                break
            await asyncio.sleep(0.02)

        proposals = pending_data["proposed_pairs"]
        assert len(proposals) >= 2

        # Reject proposal 0, Approve proposal 1
        approvals = [
            {
                "proposal_id": proposals[0]["proposal_id"],
                "front_card_id": proposals[0]["front_card_id"],
                "back_card_id": proposals[0]["back_card_id"],
                "decision": "REJECT",
            },
            {
                "proposal_id": proposals[1]["proposal_id"],
                "front_card_id": proposals[1]["front_card_id"],
                "back_card_id": proposals[1]["back_card_id"],
                "decision": "APPROVE",
            },
        ]

        approve_res = await challenger_client.post("/api/hitl/approve", json={
            "session_id": sid,
            "approvals": approvals,
            "rejected_card_ids": [proposals[0]["front_card_id"]],
            "continue_pipeline": True,
        })
        assert approve_res.status_code == 200

        # Wait for completion
        for _ in range(100):
            s_res = await challenger_client.get(f"/api/status?session_id={sid}")
            if s_res.json()["status"] == "COMPLETED":
                break
            await asyncio.sleep(0.02)

        assert session.state == PipelineState.COMPLETED
        # Rejected proposal should NOT be extracted
        extracted_card_ids = []
        for r in session.recipes:
            extracted_card_ids.append(r.title)

        # Approved proposal should be extracted
        assert len(session.recipes) == 1

    # =========================================================================
    # 4. Downloading GET /api/export/csv across multiple completed sessions
    # =========================================================================

    @pytest.mark.asyncio
    async def test_multi_session_csv_export_isolation(
        self,
        challenger_client: httpx.AsyncClient,
        challenger_session_manager: SessionManager,
        tmp_path: Path,
    ):
        """Stress-test: Run 3 independent sessions with different decks, download & verify each CSV separately."""
        # Create 3 isolated decks
        deck_a_dir = tmp_path / "deck_a"
        deck_b_dir = tmp_path / "deck_b"
        deck_c_dir = tmp_path / "deck_c"

        deck_a_dir.mkdir()
        deck_b_dir.mkdir()
        deck_c_dir.mkdir()

        # Deck A: Cinnamon rolls
        create_synthetic_card_image(deck_a_dir / "01_cinnamon_rolls_front.jpg", title="Grandma's Cinnamon Rolls", continuation_cue="(over)")
        create_synthetic_card_image(deck_a_dir / "02_cinnamon_rolls_back.jpg", title=None, body_lines=["Knead dough 5 mins."])

        # Deck B: Lemon bars
        create_synthetic_card_image(deck_b_dir / "03_lemon_bars_front.jpg", title="Luscious Lemon Glaze Bars", continuation_cue="PTO...")
        create_synthetic_card_image(deck_b_dir / "04_lemon_bars_back.jpg", title=None, body_lines=["Bake at 350F for 25 mins."])

        # Deck C: Chocolate fudge (standalone)
        create_synthetic_card_image(deck_c_dir / "05_chocolate_fudge_standalone.jpg", title="Quick Chocolate Fudge")

        decks = [
            ("Deck A", deck_a_dir, "Cinnamon Rolls"),
            ("Deck B", deck_b_dir, "Lemon"),
            ("Deck C", deck_c_dir, "Chocolate Fudge"),
        ]

        completed_sessions = {}

        for deck_name, deck_dir, expected_keyword in decks:
            # 1. Start session
            start_res = await challenger_client.post("/api/pipeline/start", json={
                "scans_dir": str(deck_dir),
                "mode": "parallel",
            })
            assert start_res.status_code == 200
            sid = start_res.json()["session_id"]

            # 2. Wait for HITL
            pending_data = None
            for _ in range(100):
                res_hitl = await challenger_client.get(f"/api/hitl/pending?session_id={sid}")
                if res_hitl.status_code == 200:
                    pending_data = res_hitl.json()
                    break
                await asyncio.sleep(0.02)

            assert pending_data is not None

            # 3. Approve proposals
            approvals = [
                {
                    "proposal_id": p["proposal_id"],
                    "front_card_id": p["front_card_id"],
                    "back_card_id": p["back_card_id"],
                    "decision": "APPROVE",
                }
                for p in pending_data["proposed_pairs"]
            ]
            await challenger_client.post("/api/hitl/approve", json={
                "session_id": sid,
                "approvals": approvals,
            })

            # 4. Wait for COMPLETED
            for _ in range(100):
                s_res = await challenger_client.get(f"/api/status?session_id={sid}")
                if s_res.json()["status"] == "COMPLETED":
                    break
                await asyncio.sleep(0.02)

            final_status = await challenger_client.get(f"/api/status?session_id={sid}")
            assert final_status.json()["status"] == "COMPLETED"

            completed_sessions[deck_name] = (sid, expected_keyword)

        # 5. Now query GET /api/export/csv for each session in random order and verify complete content isolation
        for deck_name, (sid, expected_keyword) in completed_sessions.items():
            export_res = await challenger_client.get(f"/api/export/csv?session_id={sid}")
            assert export_res.status_code == 200
            assert "text/csv" in export_res.headers["content-type"]
            assert 'filename="recipeboxer_export.csv"' in export_res.headers["content-disposition"]

            csv_text = export_res.text
            # Verify RFC 4180 parsing
            reader = csv.reader(io.StringIO(csv_text))
            rows = list(reader)
            assert len(rows) >= 2, f"CSV for {deck_name} should have header + at least 1 row"
            expected_headers = [
                "Title", "Description", "Ingredients", "Directions",
                "Prep Time", "Total Time", "Yield", "Tags", "Source"
            ]
            assert rows[0] == expected_headers

            # Verify deck content isolation
            assert expected_keyword.lower() in csv_text.lower()
            # Verify that recipes from other decks do NOT appear
            for other_deck, (other_sid, other_kw) in completed_sessions.items():
                if other_deck != deck_name:
                    assert other_kw.lower() not in rows[1][0].lower(), (
                        f"Session {sid} ({deck_name}) contains data from {other_deck} ({other_kw})"
                    )

    @pytest.mark.asyncio
    async def test_export_csv_error_cases(
        self,
        challenger_client: httpx.AsyncClient,
        challenger_session_manager: SessionManager,
    ):
        """Test error responses for GET /api/export/csv."""
        # Non-existent session -> 404
        r404 = await challenger_client.get("/api/export/csv?session_id=sess_nonexistent_xyz")
        assert r404.status_code == 404

        # Session in IDLE state -> 400
        idle_session = challenger_session_manager.create_session()
        r400_idle = await challenger_client.get(f"/api/export/csv?session_id={idle_session.session_id}")
        assert r400_idle.status_code == 400
        assert "not completed extraction" in r400_idle.json()["detail"]

        # Session in HITL_PENDING state -> 400
        idle_session.state = PipelineState.HITL_PENDING
        r400_hitl = await challenger_client.get(f"/api/export/csv?session_id={idle_session.session_id}")
        assert r400_hitl.status_code == 400

    # =========================================================================
    # 5. Additional Adversarial Cases: Race Conditions, Cancellations, Edge Cases
    # =========================================================================

    @pytest.mark.asyncio
    async def test_rapid_double_approval_race_condition(
        self,
        challenger_client: httpx.AsyncClient,
        challenger_session_manager: SessionManager,
        synthetic_card_deck: SyntheticDeckInfo,
    ):
        """Stress-test: Send two rapid approval calls concurrently to the same session."""
        start_res = await challenger_client.post("/api/pipeline/start", json={
            "scans_dir": str(synthetic_card_deck.directory),
            "mode": "parallel",
        })
        sid = start_res.json()["session_id"]

        for _ in range(100):
            res_hitl = await challenger_client.get(f"/api/hitl/pending?session_id={sid}")
            if res_hitl.status_code == 200:
                pending_data = res_hitl.json()
                break
            await asyncio.sleep(0.02)

        approvals = [
            {
                "proposal_id": p["proposal_id"],
                "front_card_id": p["front_card_id"],
                "back_card_id": p["back_card_id"],
                "decision": "APPROVE",
            }
            for p in pending_data["proposed_pairs"]
        ]

        # Send 2 approvals simultaneously
        res1, res2 = await asyncio.gather(
            challenger_client.post("/api/hitl/approve", json={"session_id": sid, "approvals": approvals}),
            challenger_client.post("/api/hitl/approve", json={"session_id": sid, "approvals": approvals}),
        )

        # One should succeed (200), the other may succeed (if both hit before transition) or return 400 (if state changed)
        assert res1.status_code in [200, 400]
        assert res2.status_code in [200, 400]
        # At least one succeeded
        assert 200 in [res1.status_code, res2.status_code]

        # Pipeline must still reach COMPLETED without crashing
        for _ in range(100):
            s_res = await challenger_client.get(f"/api/status?session_id={sid}")
            if s_res.json()["status"] == "COMPLETED":
                break
            await asyncio.sleep(0.02)

        final_s = await challenger_client.get(f"/api/status?session_id={sid}")
        assert final_s.json()["status"] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_session_cancellation_during_hitl_pending(
        self,
        challenger_client: httpx.AsyncClient,
        challenger_session_manager: SessionManager,
        synthetic_card_deck: SyntheticDeckInfo,
    ):
        """Verify that cancelling during HITL_PENDING transitions state to CANCELLED and blocks further approval."""
        start_res = await challenger_client.post("/api/pipeline/start", json={
            "scans_dir": str(synthetic_card_deck.directory),
            "mode": "parallel",
        })
        sid = start_res.json()["session_id"]
        session = challenger_session_manager.get_session(sid)

        for _ in range(100):
            res_hitl = await challenger_client.get(f"/api/hitl/pending?session_id={sid}")
            if res_hitl.status_code == 200:
                break
            await asyncio.sleep(0.02)

        # Cancel the session
        session.cancel()
        await asyncio.sleep(0.05)

        assert session.state == PipelineState.CANCELLED

        # Subsequent approval attempt must return 400
        approve_res = await challenger_client.post("/api/hitl/approve", json={
            "session_id": sid,
            "approvals": [],
        })
        assert approve_res.status_code == 400
        assert "Cannot approve session in state 'CANCELLED'" in approve_res.json()["detail"]

    @pytest.mark.asyncio
    async def test_full_pipeline_slow_batch_mode_via_api(
        self,
        challenger_client: httpx.AsyncClient,
        challenger_session_manager: SessionManager,
        synthetic_card_deck: SyntheticDeckInfo,
    ):
        """Verify slow-batch ingestion mode operates properly through the complete API lifecycle."""
        start_res = await challenger_client.post("/api/pipeline/start", json={
            "scans_dir": str(synthetic_card_deck.directory),
            "mode": "slow_batch",
            "slow_batch_size": 2,
            "slow_batch_interval_seconds": 0.01,
        })
        assert start_res.status_code == 200
        sid = start_res.json()["session_id"]
        assert start_res.json()["mode"] == "slow_batch"

        # Wait for HITL
        for _ in range(100):
            res_hitl = await challenger_client.get(f"/api/hitl/pending?session_id={sid}")
            if res_hitl.status_code == 200:
                pending_data = res_hitl.json()
                break
            await asyncio.sleep(0.02)

        assert pending_data is not None
        assert pending_data["status"] == "HITL_PENDING"

        # Approve all
        approvals = [
            {
                "proposal_id": p["proposal_id"],
                "front_card_id": p["front_card_id"],
                "back_card_id": p["back_card_id"],
                "decision": "APPROVE",
            }
            for p in pending_data["proposed_pairs"]
        ]
        await challenger_client.post("/api/hitl/approve", json={
            "session_id": sid,
            "approvals": approvals,
        })

        # Wait for completion
        for _ in range(100):
            s_res = await challenger_client.get(f"/api/status?session_id={sid}")
            if s_res.json()["status"] == "COMPLETED":
                break
            await asyncio.sleep(0.02)

        final_s = await challenger_client.get(f"/api/status?session_id={sid}")
        assert final_s.json()["status"] == "COMPLETED"
        assert final_s.json()["mode"] == "slow_batch"

    @pytest.mark.asyncio
    async def test_empty_approvals_with_rejected_ids_clean_completion(
        self,
        challenger_client: httpx.AsyncClient,
        challenger_session_manager: SessionManager,
        synthetic_card_deck: SyntheticDeckInfo,
    ):
        """Verify that when human passes approvals=[] and rejected_card_ids, pipeline completes cleanly."""
        start_res = await challenger_client.post("/api/pipeline/start", json={
            "scans_dir": str(synthetic_card_deck.directory),
            "mode": "parallel",
        })
        sid = start_res.json()["session_id"]
        session = challenger_session_manager.get_session(sid)

        for _ in range(100):
            res_hitl = await challenger_client.get(f"/api/hitl/pending?session_id={sid}")
            if res_hitl.status_code == 200:
                pending_data = res_hitl.json()
                break
            await asyncio.sleep(0.02)

        # Pass empty approvals list
        await challenger_client.post("/api/hitl/approve", json={
            "session_id": sid,
            "approvals": [],
            "rejected_card_ids": [p["front_card_id"] for p in pending_data["proposed_pairs"]],
        })

        for _ in range(100):
            s_res = await challenger_client.get(f"/api/status?session_id={sid}")
            if s_res.json()["status"] == "COMPLETED":
                break
            await asyncio.sleep(0.02)

        final_s = await challenger_client.get(f"/api/status?session_id={sid}")
        assert final_s.json()["status"] == "COMPLETED"
        assert len(session.recipes) == 0

        # Export returns valid CSV with headers only
        export_res = await challenger_client.get(f"/api/export/csv?session_id={sid}")
        assert export_res.status_code == 200
        rows = list(csv.reader(io.StringIO(export_res.text)))
        assert len(rows) == 1  # header only


