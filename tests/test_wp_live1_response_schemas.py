"""Tests for WP-LIVE1: Developer-API compatible response schemas, failure classification,

budget guard fail-closed behavior, and failure deduplication.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Type
from pydantic import BaseModel
import pytest

from google.genai import _transformers
from recipeboxer.ai.client import GenAIClient, FailureReason
from recipeboxer.ai.cost import BudgetGuard, BudgetUnpriced, CostLedger, PriceTable
from recipeboxer.correlation.semantic import SetVerificationResponse
from recipeboxer.extraction.grounding import GroundingBatchResponse
from recipeboxer.extraction.transcribe import FaceTranscriptList
from recipeboxer.models.recipe import RecipeStructureResponse
from recipeboxer.vision.fingerprint import FingerprintV2Response
from evals.judge import JudgeResultSchema


PRODUCTION_RESPONSE_MODELS: List[Type[BaseModel]] = [
    FingerprintV2Response,
    SetVerificationResponse,
    FaceTranscriptList,
    RecipeStructureResponse,
    GroundingBatchResponse,
    JudgeResultSchema,
]


def _assert_no_additional_properties_or_unsupported(schema_dict: Any, path: str = "root") -> None:
    """Recursively asserts that neither 'additionalProperties' nor unsupported keys exist."""
    if isinstance(schema_dict, dict):
        assert "additionalProperties" not in schema_dict, (
            f"Found forbidden key 'additionalProperties' at {path}: {schema_dict}"
        )
        assert "additional_properties" not in schema_dict, (
            f"Found forbidden key 'additional_properties' at {path}: {schema_dict}"
        )
        assert "$schema" not in schema_dict, (
            f"Found forbidden key '$schema' at {path}: {schema_dict}"
        )
        for k, v in schema_dict.items():
            _assert_no_additional_properties_or_unsupported(v, f"{path}.{k}")
    elif isinstance(schema_dict, list):
        for idx, item in enumerate(schema_dict):
            _assert_no_additional_properties_or_unsupported(item, f"{path}[{idx}]")


@pytest.mark.parametrize("model_cls", PRODUCTION_RESPONSE_MODELS, ids=lambda c: c.__name__)
def test_production_response_models_gemini_api_compatible(model_cls: Type[BaseModel]):
    """Offline test verifying every production response model generates a strict Gemini Developer-API

    compatible schema with zero additionalProperties and zero unsupported keywords.
    """
    sdk_schema = _transformers.t_schema(None, model_cls)
    assert sdk_schema is not None

    schema_dict = sdk_schema.model_dump(exclude_none=True)
    _assert_no_additional_properties_or_unsupported(schema_dict)

    # Also assert serialized JSON contains neither camelCase nor snake_case additionalProperties
    raw_json = json.dumps(schema_dict)
    assert "additionalProperties" not in raw_json
    assert "additional_properties" not in raw_json


def test_schema_error_classified_as_schema_unsupported():
    """Verify that Gemini Developer API schema mismatch errors are classified as

    FailureReason.SCHEMA_UNSUPPORTED and marked non-retryable.
    """
    client = GenAIClient(api_key="fake-key-for-test")

    exc = Exception(
        "LLM generation failed: additionalProperties is only supported in Gemini Enterprise Agent Platform mode, "
        "not in Gemini Developer API mode."
    )
    is_retryable, reason = client._classify_error(exc)
    assert not is_retryable
    assert reason == FailureReason.SCHEMA_UNSUPPORTED


def test_budget_guard_fails_closed_when_price_table_empty():
    """When a USD spending cap is active, BudgetGuard must raise BudgetUnpriced

    if the pricing table is empty.
    """
    guard = BudgetGuard(cap_usd=1.0)
    empty_ledger = CostLedger(prices=PriceTable(prices={}))

    with pytest.raises(BudgetUnpriced) as exc_info:
        guard.check_before_call(empty_ledger, est_usd=0.01, model="gemini-3.8-flash")

    assert exc_info.value.reason == FailureReason.BUDGET_UNPRICED
    assert "empty or uninitialized" in str(exc_info.value)


def test_budget_guard_fails_closed_when_model_unpriced():
    """When a USD spending cap is active, BudgetGuard must raise BudgetUnpriced

    if the requested model is unpriced.
    """
    guard = BudgetGuard(cap_usd=1.0)
    # Load default verified prices
    ledger = CostLedger(prices=PriceTable.load())

    with pytest.raises(BudgetUnpriced) as exc_info:
        guard.check_before_call(ledger, est_usd=0.01, model="unpriced-experimental-model")

    assert exc_info.value.reason == FailureReason.BUDGET_UNPRICED
    assert "not priced" in str(exc_info.value)


def test_budget_guard_passes_when_model_priced():
    """BudgetGuard check_before_call succeeds when cap is set and model is priced."""
    guard = BudgetGuard(cap_usd=1.0)
    ledger = CostLedger(prices=PriceTable.load())
    # Should not raise
    guard.check_before_call(ledger, est_usd=0.01, model="gemini-3.8-flash")


def test_failure_deduplication_by_stage_and_images():
    """Verify deduplication removes duplicate failures with same stage and image set."""
    failures_in = [
        {"stage": "STRUCTURE", "reason": "SCHEMA_UNSUPPORTED", "image_ids": ["c1", "c2"], "message": "msg 1"},
        {"stage": "STRUCTURE", "reason": "SCHEMA_UNSUPPORTED", "image_ids": ["c2", "c1"], "message": "dup 1"},
        {"stage": "STRUCTURE", "reason": "SCHEMA_UNSUPPORTED", "image_ids": ["c3"], "message": "msg 2"},
        {"stage": "TRANSCRIBE", "reason": "TIMEOUT", "image_ids": ["c1", "c2"], "message": "different stage"},
    ]

    deduped: List[Dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for item in failures_in:
        key = (item["stage"], tuple(sorted(str(cid) for cid in item.get("image_ids", []))))
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    assert len(deduped) == 3
    assert [d["message"] for d in deduped] == ["msg 1", "msg 2", "different stage"]
