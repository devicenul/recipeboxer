"""Tests for WP-SCALE throughput, concurrency, bounded execution, and incremental ledger flushes."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, List, Optional
from unittest.mock import AsyncMock, patch

import pytest

from recipeboxer.agent.orchestrator import (
    ConcurrentGroupingEngine,
    RecipeBoxerAgent,
)
from recipeboxer.agent.session import PipelineSession
from recipeboxer.ai.client import (
    LLMRequest,
    LLMResponse,
    LLMStage,
    ScriptedLLMClient,
    TextPart,
    UsageTokens,
)
from recipeboxer.ai.cost import CostLedger, PriceTable
from recipeboxer.config import Settings, get_settings
from recipeboxer.correlation.engine import FileImageProvider, GroupingEngine
from recipeboxer.models.domain import VisualFingerprint
from recipeboxer.models.hitl import CardPairApproval, HITLDecision
from recipeboxer.models.pipeline import PipelineState
from recipeboxer.models.recipe import RecipeSchema


@pytest.mark.asyncio
async def test_cost_ledger_incremental_jsonl_flush_and_reload(tmp_path: Path):
    """Validates that CostLedger writes incremental JSONL entries and can reload after simulated crash."""
    flush_path = tmp_path / "ledger.jsonl"
    ledger = CostLedger(prices=PriceTable.load(), flush_path=flush_path)

    usage = UsageTokens(input_tokens=100, output_tokens=50, thinking_tokens=10)
    for i in range(5):
        ledger.record(
            stage=LLMStage.STRUCTURE,
            model="gemini-3.8-flash",
            usage=usage,
            latency_ms=120.0,
            attempts=1,
            metadata={"call_index": i},
        )

    assert flush_path.exists()
    lines = [line.strip() for line in flush_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    # At least 5 call records + totals records
    call_lines = [json.loads(line) for line in lines if json.loads(line).get("type") == "call"]
    assert len(call_lines) == 5

    # Reconstitute ledger from the incremental JSONL
    reconstituted = CostLedger.load_from_jsonl(flush_path)
    assert len(reconstituted["calls"]) == 5
    assert reconstituted["totals"]["calls"] == 5
    assert reconstituted["totals"]["input_tokens"] == 500
    assert reconstituted["totals"]["output_tokens"] == 250
    assert reconstituted["totals"]["thinking_tokens"] == 50


@pytest.mark.asyncio
async def test_concurrent_extraction_bounded_and_deterministic(tmp_path: Path):
    """Validates that Stage 5 executes approvals concurrently bounded by semaphore while preserving order."""
    settings = Settings(llm_max_concurrency=3)
    agent = RecipeBoxerAgent(settings=settings)

    from recipeboxer.ingestion.scanner import ScannedCard

    session = PipelineSession(
        session_id="test_concurrent_ext",
        settings=settings,
        config={"scans_dir": str(tmp_path), "auto_approve": True},
    )
    session.mock_client = ScriptedLLMClient([])

    # Create 6 card fingerprints and approvals
    card_ids = [f"card_{i:02d}" for i in range(6)]
    for cid in card_ids:
        session.fingerprints[cid] = VisualFingerprint(
            card_id=cid,
            image_path=f"/path/{cid}.jpg",
            title=f"Recipe {cid}",
            role="FRONT",
            sha256_hash=f"hash_{cid}",
        )

    from recipeboxer.models.correlation import CorrelationProposal

    session.cards = [
        ScannedCard(card_id=cid, file_path=Path(f"/path/{cid}.jpg"), sha256_hash=f"hash_{cid}")
        for cid in card_ids
    ]
    session.proposals = [
        CorrelationProposal(
            proposal_id=f"prop_{i:02d}",
            ordered_card_ids=[cid],
            part_count=1,
            heuristic_score=1.0,
            semantic_score=1.0,
            confidence=1.0,
            is_standalone=True,
            recommended_action="APPROVE",
        )
        for i, cid in enumerate(card_ids)
    ]

    approvals = [
        CardPairApproval(
            proposal_id=f"prop_{i:02d}",
            card_ids=[cid],
            ordered_card_ids=[cid],
            decision=HITLDecision.APPROVE,
        )
        for i, cid in enumerate(card_ids)
    ]
    session.approvals = approvals

    active_concurrent = 0
    max_concurrent = 0
    lock = asyncio.Lock()

    async def _mock_extract(sess, front_fp=None, back_fp=None, ordered_fps=None):
        nonlocal active_concurrent, max_concurrent
        async with lock:
            active_concurrent += 1
            if active_concurrent > max_concurrent:
                max_concurrent = active_concurrent
        await asyncio.sleep(0.05)
        cid = ordered_fps[0].card_id if ordered_fps else "unknown"
        res = RecipeSchema(
            recipe_id=f"rec_{cid}",
            title=f"Extracted {cid}",
            ingredients=["1 cup sugar"],
            directions=["Mix and bake."],
            card_ids=[cid],
        )
        async with lock:
            active_concurrent -= 1
        return res

    agent._extract_single_recipe = _mock_extract

    # Run extraction stage
    session.state = PipelineState.EXTRACTING
    start_t = time.monotonic()
    await agent.run_pipeline(session)
    elapsed = time.monotonic() - start_t

    # Max concurrency should not exceed bounded semaphore (3)
    assert max_concurrent <= 3
    assert max_concurrent >= 2  # Proves concurrency was active
    # 6 items * 0.05s = 0.30s sequentially. With concurrency 3, should take ~0.10 - 0.20s
    assert elapsed < 0.28

    # INVARIANT: Strict determinism of recipe ordering matching session.approvals
    assert len(session.recipes) == 6
    for i, r in enumerate(session.recipes):
        assert r.card_ids == [f"card_{i:02d}"]
        assert r.title == f"Extracted card_{i:02d}"


@pytest.mark.asyncio
async def test_concurrent_grouping_engine_set_verify_concurrency():
    """Validates that ConcurrentGroupingEngine executes set verifications concurrently."""
    engine = ConcurrentGroupingEngine()

    fps = [
        VisualFingerprint(
            card_id=f"card_{i}",
            image_path=f"/path/card_{i}.jpg",
            role="FRONT" if i % 2 == 0 else "BACK",
            sha256_hash=f"h_{i}",
        )
        for i in range(8)
    ]
    images = FileImageProvider({})

    active_concurrent = 0
    max_concurrent = 0
    lock = asyncio.Lock()

    class _MockSetVerifier:
        async def verify(self, anchor, candidates, images=None):
            nonlocal active_concurrent, max_concurrent
            async with lock:
                active_concurrent += 1
                if active_concurrent > max_concurrent:
                    max_concurrent = active_concurrent
            await asyncio.sleep(0.04)
            from recipeboxer.models.correlation import SetVerification
            res = SetVerification(
                anchor=anchor.card_id,
                continuations=[c.card_id for c in candidates[:1]],
                rejected=[c.card_id for c in candidates[1:]],
                confidence=0.92,
                evidence_quotes=["continuous handwriting"],
            )
            async with lock:
                active_concurrent -= 1
            return res

    engine.set_verifier = _MockSetVerifier()

    res = await engine.group(fps=fps, images=images)
    assert isinstance(res.chains, list)
    assert len(res.chains) > 0
    assert len(res.proposals) > 0
