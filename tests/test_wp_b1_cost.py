"""Tests for WP-B1 Cost Engine, Pricing Table, CostLedger, BudgetGuard, and Pre-run Cost Estimation."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict

import pytest

from recipeboxer.agent.orchestrator import RecipeBoxerAgent
from recipeboxer.agent.session import PipelineSession
from recipeboxer.ai.client import (
    GenAIClient,
    LLMRequest,
    LLMResponse,
    LLMStage,
    ScriptedLLMClient,
    TextPart,
    UsageTokens,
)
from recipeboxer.ai.cost import (
    BudgetExceeded,
    BudgetGuard,
    CostLedger,
    ModelPrice,
    PriceTable,
    estimate_run_cost,
)
from recipeboxer.config import Settings, get_settings
from recipeboxer.models.failures import FailureReason, StageUnavailableError
from recipeboxer.models.hitl import CardPairApproval, HITLDecision, HitlItemType
from recipeboxer.models.pipeline import PipelineState
from recipeboxer.storage.cache import FingerprintCache


def test_price_table_load_default_and_schema():
    """Validates that PriceTable loads config/pricing.toml with final schema fields."""
    table = PriceTable.load()
    assert isinstance(table, PriceTable)
    assert table.meta.get("verified_on") not in (None, "", "UNVERIFIED")
    assert "gemini-3.8-flash" in table.prices
    assert "gemini-3.1-pro-preview" in table.prices
    # Models without a published price must stay unpriced, never "free".
    assert "gemini-3.7-pro" not in table.prices

    flash = table.prices["gemini-3.8-flash"]
    assert isinstance(flash, ModelPrice)
    assert flash.model == "gemini-3.8-flash"
    assert flash.input_per_mtok > 0 and flash.output_per_mtok > 0
    assert hasattr(flash, "input_per_mtok")
    assert hasattr(flash, "output_per_mtok")
    assert hasattr(flash, "thinking_per_mtok")
    assert hasattr(flash, "context_tier_threshold_tokens")
    assert hasattr(flash, "verified_on")
    assert hasattr(flash, "source_url")


def test_price_table_unpriced_model_returns_none_and_warns(caplog):
    """Verifies that an unpriced model returns None and logs a warning, never silently defaulting to 0."""
    table = PriceTable.load()
    with caplog.at_level(logging.WARNING):
        cost = table.cost_usd("unpriced-exotic-model-v99", input_tokens=1000, output_tokens=500)
    assert cost is None
    assert any("Unpriced model 'unpriced-exotic-model-v99'" in r.message for r in caplog.records)


def test_price_table_calculation_math_and_context_tier_threshold(tmp_path: Path):
    """Verifies pricing math including thinking tokens and context-tier threshold shifts."""
    custom_toml = tmp_path / "custom_pricing.toml"
    custom_toml.write_text(
        """
[meta]
table_version = "1.0.0"
verified_on = "2026-10-14"
source_url = "https://ai.google.dev/pricing"

["gemini-test-tiered"]
input_per_mtok = 2.0
output_per_mtok = 8.0
thinking_per_mtok = 8.0
context_tier_threshold_tokens = 100000
input_per_mtok_high_tier = 4.0
output_per_mtok_high_tier = 16.0
verified_on = "2026-10-14"
source_url = "https://ai.google.dev/pricing"
""",
        encoding="utf-8",
    )
    table = PriceTable.load(custom_toml)
    assert table.is_priced("gemini-test-tiered")
    assert table.is_priced("models/gemini-test-tiered")  # Normalization check

    # Standard tier (<= 100,000 input tokens)
    # 50,000 input @ $2/MTok = $0.10
    # 10,000 output @ $8/MTok = $0.08
    # 5,000 thinking @ $8/MTok = $0.04
    # Total = 0.10 + 0.08 + 0.04 = 0.22
    cost_std = table.cost_usd(
        "gemini-test-tiered",
        input_tokens=50_000,
        output_tokens=10_000,
        thinking_tokens=5_000,
    )
    assert cost_std == pytest.approx(0.22, rel=1e-5)

    # High tier (> 100,000 input tokens)
    # 200,000 input @ $4/MTok = $0.80
    # 10,000 output @ $16/MTok = $0.16
    # 5,000 thinking @ $8/MTok = $0.04
    # Total = 0.80 + 0.16 + 0.04 = 1.00
    cost_high = table.cost_usd(
        "gemini-test-tiered",
        input_tokens=200_000,
        output_tokens=10_000,
        thinking_tokens=5_000,
    )
    assert cost_high == pytest.approx(1.00, rel=1e-5)


def test_cost_ledger_records_calls_and_generates_snapshot(tmp_path: Path):
    """Tests CostLedger accumulation, per-call USD tracking, and snapshot structure."""
    pricing_file = tmp_path / "pricing.toml"
    pricing_file.write_text(
        """
["gemini-flash"]
input_per_mtok = 1.0
output_per_mtok = 4.0
thinking_per_mtok = 4.0

["gemini-pro"]
input_per_mtok = 5.0
output_per_mtok = 20.0
thinking_per_mtok = 20.0
""",
        encoding="utf-8",
    )
    prices = PriceTable.load(pricing_file)
    ledger = CostLedger(prices=prices)

    # Record Call 1: flash stage=FINGERPRINT
    # 500,000 input ($0.50), 250,000 output ($1.00) = $1.50
    ledger.record(
        stage=LLMStage.FINGERPRINT,
        model="gemini-flash",
        usage=UsageTokens(input_tokens=500_000, output_tokens=250_000, total_tokens=750_000),
        latency_ms=120.0,
    )

    # Record Call 2: pro stage=STRUCTURE
    # 100,000 input ($0.50), 50,000 output ($1.00) = $1.50
    ledger.record(
        stage=LLMStage.STRUCTURE,
        model="gemini-pro",
        usage=UsageTokens(input_tokens=100_000, output_tokens=50_000, total_tokens=150_000),
        latency_ms=250.0,
    )

    # Record Call 3: unpriced model
    ledger.record(
        stage=LLMStage.JUDGE,
        model="unpriced-judge-v1",
        usage=UsageTokens(input_tokens=10_000, output_tokens=5_000, total_tokens=15_000),
        latency_ms=80.0,
    )

    assert ledger.calls == 3
    assert ledger.unpriced_calls == 1
    assert ledger.total_usd == pytest.approx(3.00, rel=1e-5)
    assert ledger.total_input_tokens == 610_000
    assert ledger.total_output_tokens == 305_000
    assert ledger.total_latency_ms == pytest.approx(450.0)

    snap = ledger.snapshot()
    assert snap["total_calls"] == 3
    assert snap["total_usd"] == pytest.approx(3.00)
    assert snap["unpriced_calls"] == 1
    assert "FINGERPRINT" in snap["by_stage"]
    assert snap["by_stage"]["FINGERPRINT"]["usd"] == pytest.approx(1.50)
    assert "STRUCTURE" in snap["by_stage"]
    assert snap["by_stage"]["STRUCTURE"]["usd"] == pytest.approx(1.50)
    assert snap["by_stage"]["JUDGE"]["usd"] == 0.0
    assert snap["by_stage"]["JUDGE"]["unpriced_calls"] == 1


def test_budget_guard_trips_on_max_calls():
    """Validates that BudgetGuard trips and raises BudgetExceeded when max_calls limit is hit."""
    guard = BudgetGuard(max_calls=2)
    ledger = CostLedger(prices=PriceTable())

    # Call 0 (calls=0 < 2) -> passes
    guard.check_before_call(ledger)

    ledger.record(LLMStage.FINGERPRINT, "test-m", UsageTokens(), 10.0)
    # Call 1 (calls=1 < 2) -> passes
    guard.check_before_call(ledger)

    ledger.record(LLMStage.FINGERPRINT, "test-m", UsageTokens(), 10.0)
    # Call 2 (calls=2 >= 2) -> trips!
    with pytest.raises(BudgetExceeded) as exc_info:
        guard.check_before_call(ledger)

    exc = exc_info.value
    assert isinstance(exc, StageUnavailableError)
    assert exc.reason == FailureReason.BUDGET_EXCEEDED
    assert exc.retryable is False
    assert exc.details["breach"] == "max_calls"


def test_budget_guard_trips_on_cap_usd(tmp_path: Path):
    """Validates that BudgetGuard trips when total_usd spend reaches or exceeds cap_usd."""
    pricing_file = tmp_path / "pricing.toml"
    pricing_file.write_text(
        """
["gemini-fixed"]
input_per_mtok = 10.0
output_per_mtok = 10.0
""",
        encoding="utf-8",
    )
    prices = PriceTable.load(pricing_file)
    guard = BudgetGuard(cap_usd=0.05)
    ledger = CostLedger(prices=prices, guard=guard)

    # 1000 input + 1000 output @ $10/MTok = $0.00002
    ledger.record(LLMStage.FINGERPRINT, "gemini-fixed", UsageTokens(input_tokens=1000, output_tokens=1000), 5.0)
    # Spend = 0.00002 < 0.05 -> passes
    guard.check_before_call(ledger)

    # Record big call: 3,000,000 tokens @ $10/MTok = $0.06
    ledger.record(LLMStage.STRUCTURE, "gemini-fixed", UsageTokens(input_tokens=3_000_000, output_tokens=3_000_000), 50.0)
    # Spend = $0.06002 > 0.05 -> trips!
    with pytest.raises(BudgetExceeded) as exc_info:
        guard.check_before_call(ledger)

    exc = exc_info.value
    assert exc.reason == FailureReason.BUDGET_EXCEEDED
    assert exc.retryable is False
    assert exc.details["breach"] == "spend_cap_usd"


def test_estimate_run_cost_formula_and_calibration():
    """Validates pre-run cost estimation per REMEDIATION_PLAN.md §7.1 and calibration overrides."""
    prices = PriceTable(
        prices={
            "gemini-3.8-flash": ModelPrice(
                model="gemini-3.8-flash",
                input_per_mtok=0.50,
                output_per_mtok=2.00,
                thinking_per_mtok=2.00,
            ),
            "gemini-3.1-pro-preview": ModelPrice(
                model="gemini-3.1-pro-preview",
                input_per_mtok=2.00,
                output_per_mtok=8.00,
                thinking_per_mtok=8.00,
            ),
        }
    )

    est = estimate_run_cost(n_cards=10, profile="balanced", prices=prices)
    assert "low" in est and "expected" in est and "high" in est and "assumptions" in est
    assert est["low"] is not None and est["low"] > 0
    assert est["expected"] is not None and est["expected"] >= est["low"]
    assert est["high"] is not None and est["high"] >= est["expected"]

    # Verify calibration override alters expected spend
    custom_calibration = {
        "stages": {
            "TRANSCRIBE": {"input_tokens": 5000, "output_tokens": 2000, "thinking_tokens": 500}
        }
    }
    est_calibrated = estimate_run_cost(
        n_cards=10,
        profile="balanced",
        prices=prices,
        calibration=custom_calibration,
    )
    assert est_calibrated["expected"] > est["expected"]
    assert est_calibrated["assumptions"]["calibration_applied"] is True


def test_settings_budget_fields_and_profile_params():
    """Validates Settings budget configuration fields and profile_params() values per §1.2."""
    s = Settings(
        spend_cap_usd=5.0,
        monthly_cap_usd=50.0,
        max_calls=100,
        quality_profile="economy",
    )
    assert s.spend_cap_usd == 5.0
    assert s.monthly_cap_usd == 50.0
    assert s.max_calls == 100

    params_econ = s.profile_params()
    assert params_econ["image_long_edge"] == 1280
    assert params_econ["thinking_budget_transcribe"] == 0
    assert params_econ["self_consistency"] == "off"
    assert params_econ["grounding"] is False

    s_balanced = Settings(quality_profile="balanced")
    params_bal = s_balanced.profile_params()
    assert params_bal["image_long_edge"] == 1600
    assert params_bal["thinking_budget_transcribe"] == -1  # adaptive=-1
    assert params_bal["self_consistency"] == "flagged"
    assert params_bal["grounding"] is True

    s_best = Settings(quality_profile="best")
    params_best = s_best.profile_params()
    assert params_best["image_long_edge"] == 2000
    assert params_best["thinking_budget_transcribe"] == 2048
    assert params_best["self_consistency"] == "flagged_other_model"
    assert params_best["grounding"] is True


@pytest.mark.asyncio
async def test_genai_client_guard_hook_trips_before_call():
    """Validates that GenAIClient checks the budget guard BEFORE executing any call."""
    guard = BudgetGuard(max_calls=1)
    ledger = CostLedger(prices=PriceTable(), guard=guard)

    class DummySDKModels:
        async def generate_content(self, **kwargs):
            raise AssertionError("generate_content must not be reached when guard trips!")

    class DummySDKClient:
        class aio:
            models = DummySDKModels()

    client = GenAIClient(
        api_key="dummy-key-for-test",
        genai_client=DummySDKClient(),
        ledger=ledger,
        guard=guard,
    )

    # 1 call already recorded in ledger -> reaches max_calls limit (1)
    ledger.record(LLMStage.FINGERPRINT, "gemini-3.7-flash", UsageTokens(), 10.0)

    req = LLMRequest(
        stage=LLMStage.FINGERPRINT,
        model="gemini-3.7-flash",
        parts=[TextPart(text="test")],
    )

    with pytest.raises(BudgetExceeded) as exc_info:
        await client.generate(req)

    assert exc_info.value.reason == FailureReason.BUDGET_EXCEEDED


@pytest.mark.asyncio
async def test_orchestrator_pauses_with_budget_hitl_item_on_budget_exceeded(tmp_path: Path):
    """Validates that on BudgetExceeded, the Orchestrator pauses with a HITL item of kind budget and never degrades."""
    from PIL import Image
    cache = FingerprintCache(":memory:")
    img_file = tmp_path / "card_01.jpg"
    Image.new("RGB", (50, 50), color=(200, 200, 200)).save(img_file, format="JPEG")

    from recipeboxer.ingestion.scanner import ScannedCard
    from recipeboxer.models.domain import CardRole, VisualFingerprint

    card = ScannedCard(card_id="card_01", file_path=img_file, sha256_hash="0123456789abcdef")

    session = PipelineSession(
        session_id="test_budget_pause",
        cache=cache,
        config={"scans_dir": str(tmp_path), "auto_approve": True},
    )
    session.cards = [card]

    class ScriptedBudgetExtractionClient:
        async def extract(self, *args, **kwargs):
            raise BudgetExceeded("Budget cap of $0.10 exceeded")

    agent = RecipeBoxerAgent(
        cache=cache,
        recipe_extractor=ScriptedBudgetExtractionClient(),
    )

    # Seed an approval to trigger extraction stage
    session.approvals = [
        CardPairApproval(
            proposal_id="prop_01",
            ordered_card_ids=["card_01"],
            decision=HITLDecision.APPROVE,
        )
    ]
    session.fingerprints["card_01"] = VisualFingerprint(
        card_id="card_01",
        image_path=str(img_file),
        sha256_hash="0123456789abcdef",
        role=CardRole.STANDALONE,
        role_confidence=0.9,
    )
    session.recipe_extractor_fn = ScriptedBudgetExtractionClient().extract

    # Run pipeline in a background task
    pipe_task = asyncio.create_task(agent.run_pipeline(session))

    # Wait for session to reach Stage 4 HITL_PENDING or Stage 5
    for _ in range(50):
        if session.state == PipelineState.HITL_PENDING:
            break
        await asyncio.sleep(0.01)

    # If paused on correlation proposal, approve to enter extraction stage
    if session.state == PipelineState.HITL_PENDING and session.proposals:
        approvals = [
            CardPairApproval(
                proposal_id=p.proposal_id,
                card_ids=p.ordered_card_ids,
                decision=HITLDecision.APPROVE,
            )
            for p in session.proposals
        ]
        session.resume_with_approvals(approvals)

    # Wait for orchestrator to hit the extraction stage and pause on budget exceeded
    for _ in range(50):
        has_budget = any(
            it.item_type == HitlItemType.BUDGET or getattr(it, "kind", "") == "budget"
            for it in session.hitl_queue
        )
        if has_budget:
            break
        await asyncio.sleep(0.01)

    # Verify session is in HITL_PENDING
    assert session.state == PipelineState.HITL_PENDING

    # Verify a HITL item of kind "budget" was enqueued
    budget_items = [
        it for it in session.hitl_queue
        if it.item_type == HitlItemType.BUDGET or getattr(it, "kind", "") == "budget"
    ]
    assert len(budget_items) == 1
    assert "Budget cap of $0.10 exceeded" in budget_items[0].message

    # Verify pipeline has NOT degraded to fake or failed recipes
    assert len(session.recipes) == 0

    # User cancels/stops session
    session.cancel()
    await asyncio.wait_for(pipe_task, timeout=2.0)
    assert session.state == PipelineState.CANCELLED

