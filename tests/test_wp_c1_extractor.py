"""Focused unit tests for WP-C1: honest recipe extractor without answer keys or offline cheats."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pydantic
import pytest

import recipeboxer.config as cfg
if not hasattr(cfg, "DEFAULT_STAGE_TIMEOUTS"):
    cfg.DEFAULT_STAGE_TIMEOUTS = {
        "fingerprint": 30.0,
        "modality": 15.0,
        "correlation": 45.0,
        "extraction": 60.0,
    }

from recipeboxer.extraction.extractor import GeminiRecipeExtractor, RecipeExtractor
from recipeboxer.models.domain import VisualFingerprint, CardRole
from recipeboxer.models.failures import (
    ExtractionUnavailable,
    FailureReason,
    PipelineStage,
    StageFailure,
)
from recipeboxer.models.recipe import RecipeSchema


class SimpleMockClient:
    """Explicit injected mock client with programmed response."""

    def __init__(self, response=None, should_raise=None):
        self.response = response
        self.should_raise = should_raise
        self.calls = []

    async def extract_recipe(self, front, back=None):
        self.calls.append((front, back))
        if self.should_raise:
            raise self.should_raise
        return self.response


@pytest.mark.asyncio
async def test_no_key_raises_no_api_key():
    """Missing or dummy mock API key without client raises ExtractionUnavailable(NO_API_KEY)."""
    # 1. api_key=None
    extractor = GeminiRecipeExtractor(api_key=None)
    with pytest.raises(ExtractionUnavailable) as exc_info:
        await extractor.extract(b"opaque_image_bytes")
    assert exc_info.value.reason == FailureReason.NO_API_KEY
    assert exc_info.value.retryable is False

    # 2. api_key starting with 'mock'
    extractor_mock_key = GeminiRecipeExtractor(api_key="mock-api-key-12345")
    with pytest.raises(ExtractionUnavailable) as exc_info2:
        await extractor_mock_key.extract(b"opaque_image_bytes")
    assert exc_info2.value.reason == FailureReason.NO_API_KEY
    assert exc_info2.value.retryable is False

    # 3. Empty string api_key
    extractor_empty = GeminiRecipeExtractor(api_key="")
    with pytest.raises(ExtractionUnavailable) as exc_info3:
        await extractor_empty.extract(b"opaque_image_bytes")
    assert exc_info3.value.reason == FailureReason.NO_API_KEY
    assert exc_info3.value.retryable is False


def test_extract_sync_no_key_raises():
    """extract_sync without an API key or client raises ExtractionUnavailable(NO_API_KEY)."""
    extractor = GeminiRecipeExtractor(api_key=None)
    with pytest.raises(ExtractionUnavailable) as exc_info:
        extractor.extract_sync(b"opaque_card_bytes")
    assert exc_info.value.reason == FailureReason.NO_API_KEY
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_injected_client_result_passes_through():
    """Injected mock client producing RecipeSchema passes through cleanly."""
    mock_recipe = RecipeSchema(
        title="Heirloom Sourdough Bread",
        ingredients=["3 cups flour", "1 cup starter", "1 tsp salt"],
        directions=["1. Mix ingredients.", "2. Ferment overnight.", "3. Bake at 450F."],
    )
    client = SimpleMockClient(response=mock_recipe)
    extractor = GeminiRecipeExtractor(client=client)

    result = await extractor.extract(b"opaque_bytes_123")
    assert isinstance(result, RecipeSchema)
    assert result.title == "Heirloom Sourdough Bread"
    assert len(result.ingredients) == 3
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_injected_client_dict_passes_through():
    """Injected client returning a dictionary is validated into RecipeSchema."""
    dict_response = {
        "title": "Rustic Blueberry Scones",
        "ingredients": ["2 cups flour", "1/2 cup butter", "1 cup blueberries"],
        "directions": ["1. Cut butter into flour.", "2. Fold in berries.", "3. Bake at 400F."],
    }
    client = SimpleMockClient(response=dict_response)
    extractor = GeminiRecipeExtractor(client=client)

    result = await extractor.extract(b"opaque_bytes_456")
    assert isinstance(result, RecipeSchema)
    assert result.title == "Rustic Blueberry Scones"
    assert result.ingredients[0] == "2 cups flour"


@pytest.mark.asyncio
async def test_injected_client_yielding_nothing_raises_invalid_response():
    """Injected client returning None raises ExtractionUnavailable(INVALID_RESPONSE)."""
    client = SimpleMockClient(response=None)
    extractor = GeminiRecipeExtractor(client=client)

    with pytest.raises(ExtractionUnavailable) as exc_info:
        await extractor.extract(b"opaque_bytes_789")
    assert exc_info.value.reason == FailureReason.INVALID_RESPONSE


@pytest.mark.asyncio
async def test_injected_client_invalid_schema_raises_schema_validation():
    """Injected client returning malformed dict raises ExtractionUnavailable(SCHEMA_VALIDATION)."""
    # Missing required directions or malformed structure
    client = SimpleMockClient(response={"title": 12345, "ingredients": "not a list"})
    extractor = GeminiRecipeExtractor(client=client)

    with pytest.raises(ExtractionUnavailable) as exc_info:
        await extractor.extract(b"opaque_bytes_000")
    assert exc_info.value.reason == FailureReason.SCHEMA_VALIDATION


def test_extract_sync_with_injected_client():
    """extract_sync works seamlessly with an injected client."""
    mock_recipe = RecipeSchema(
        title="Sync Test Recipe",
        ingredients=["1 cup oats", "1 cup milk"],
        directions=["1. Soak oats overnight."],
    )
    client = SimpleMockClient(response=mock_recipe)
    extractor = GeminiRecipeExtractor(client=client)

    result = extractor.extract_sync(b"opaque_bytes_sync")
    assert isinstance(result, RecipeSchema)
    assert result.title == "Sync Test Recipe"


@pytest.mark.asyncio
async def test_api_exception_mapping_timeout(monkeypatch):
    """httpx.TimeoutException maps to FailureReason.TIMEOUT with card_ids."""
    async def mock_timeout(*args, **kwargs):
        raise httpx.ReadTimeout("Read operation timed out")

    extractor = GeminiRecipeExtractor(api_key="real-gemini-key-xyz")
    monkeypatch.setattr(extractor, "_call_gemini_extraction_api", mock_timeout)

    card = VisualFingerprint(
        card_id="card_alpha_01",
        image_path="/memory/card_alpha_01.jpg",
        sha256_hash="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        role=CardRole.STANDALONE,
    )

    with pytest.raises(ExtractionUnavailable) as exc_info:
        await extractor.extract(card)
    assert exc_info.value.reason == FailureReason.TIMEOUT
    assert "card_alpha_01" in exc_info.value.card_ids


@pytest.mark.asyncio
async def test_api_exception_mapping_rate_limited(monkeypatch):
    """HTTP 429 maps to FailureReason.RATE_LIMITED."""
    async def mock_rate_limit(*args, **kwargs):
        req = httpx.Request("POST", "https://generativelanguage.googleapis.com/")
        resp = httpx.Response(status_code=429, request=req)
        raise httpx.HTTPStatusError("Rate limit exceeded", request=req, response=resp)

    extractor = GeminiRecipeExtractor(api_key="real-gemini-key-xyz")
    monkeypatch.setattr(extractor, "_call_gemini_extraction_api", mock_rate_limit)

    card = VisualFingerprint(
        card_id="card_beta_02",
        image_path="/memory/card_beta_02.jpg",
        sha256_hash="abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
        role=CardRole.STANDALONE,
    )

    with pytest.raises(ExtractionUnavailable) as exc_info:
        await extractor.extract(card)
    assert exc_info.value.reason == FailureReason.RATE_LIMITED
    assert "card_beta_02" in exc_info.value.card_ids


@pytest.mark.asyncio
async def test_api_exception_mapping_model_unavailable(monkeypatch):
    """HTTP 503 or network error maps to FailureReason.MODEL_UNAVAILABLE."""
    async def mock_service_error(*args, **kwargs):
        req = httpx.Request("POST", "https://generativelanguage.googleapis.com/")
        resp = httpx.Response(status_code=503, request=req)
        raise httpx.HTTPStatusError("Service Unavailable", request=req, response=resp)

    extractor = GeminiRecipeExtractor(api_key="real-gemini-key-xyz")
    monkeypatch.setattr(extractor, "_call_gemini_extraction_api", mock_service_error)

    with pytest.raises(ExtractionUnavailable) as exc_info:
        await extractor.extract(b"opaque_bytes")
    assert exc_info.value.reason == FailureReason.MODEL_UNAVAILABLE


@pytest.mark.asyncio
async def test_api_exception_mapping_json_error(monkeypatch):
    """json.JSONDecodeError maps to FailureReason.INVALID_RESPONSE."""
    async def mock_json_err(*args, **kwargs):
        raise json.JSONDecodeError("Expecting value", "invalid json response", 0)

    extractor = GeminiRecipeExtractor(api_key="real-gemini-key-xyz")
    monkeypatch.setattr(extractor, "_call_gemini_extraction_api", mock_json_err)

    with pytest.raises(ExtractionUnavailable) as exc_info:
        await extractor.extract(b"opaque_bytes")
    assert exc_info.value.reason == FailureReason.INVALID_RESPONSE


@pytest.mark.asyncio
async def test_api_exception_mapping_validation_error(monkeypatch):
    """pydantic.ValidationError maps to FailureReason.SCHEMA_VALIDATION."""
    async def mock_validation_err(*args, **kwargs):
        RecipeSchema.model_validate({"invalid": "missing fields"})

    extractor = GeminiRecipeExtractor(api_key="real-gemini-key-xyz")
    monkeypatch.setattr(extractor, "_call_gemini_extraction_api", mock_validation_err)

    with pytest.raises(ExtractionUnavailable) as exc_info:
        await extractor.extract(b"opaque_bytes")
    assert exc_info.value.reason == FailureReason.SCHEMA_VALIDATION


def test_module_has_no_manifest_or_offline_cheats():
    """Verify extractor module contains no answer-key variables, functions, or fixture imports."""
    import recipeboxer.extraction.extractor as ext_mod

    # 1. Deleted symbols must not exist on the module
    assert not hasattr(ext_mod, "_MANIFEST_CACHE"), "_MANIFEST_CACHE must be deleted"
    assert not hasattr(ext_mod, "_MANIFEST_BY_CARD_ID"), "_MANIFEST_BY_CARD_ID must be deleted"
    assert not hasattr(ext_mod, "_MANIFEST_BY_SHA256"), "_MANIFEST_BY_SHA256 must be deleted"
    assert not hasattr(ext_mod, "_get_manifest_lookup_tables"), "_get_manifest_lookup_tables must be deleted"
    assert not hasattr(ext_mod, "_extract_offline_recipe"), "_extract_offline_recipe must be deleted"

    # 2. Source code text must not reference manifests or canned recipe titles
    source_path = Path(ext_mod.__file__)
    source_text = source_path.read_text(encoding="utf-8").lower()

    assert "manifest.json" not in source_text
    assert "sample_cards" not in source_text
    assert "tests/fixtures" not in source_text
    assert "grandma's cinnamon rolls" not in source_text
    assert "luscious lemon glaze bars" not in source_text
    assert "10-minute quick chocolate fudge" not in source_text
    assert "southern pecan pie" not in source_text


@pytest.mark.asyncio
async def test_corpus_image_path_with_no_client_raises():
    """A corpus fixture image path with no client raises ExtractionUnavailable instead of canned recipe."""
    extractor = GeminiRecipeExtractor(api_key=None)
    fixture_path = Path("tests/fixtures/sample_cards/05_chocolate_fudge_standalone.jpg")

    # Must raise ExtractionUnavailable(NO_API_KEY) and NEVER return any RecipeSchema
    with pytest.raises(ExtractionUnavailable) as exc_info:
        await extractor.extract(fixture_path)
    assert exc_info.value.reason == FailureReason.NO_API_KEY
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_extract_batch_returns_stage_failures():
    """extract_batch returns StageFailure objects on failure rather than placeholder recipes."""
    extractor = GeminiRecipeExtractor(api_key=None)
    pairs = [
        (b"card_1_bytes", None),
        (b"card_2_bytes", None),
    ]

    results = await extractor.extract_batch(pairs, concurrency=2)
    assert len(results) == 2
    for r in results:
        assert isinstance(r, StageFailure)
        assert r.stage == PipelineStage.EXTRACTION
        assert r.reason == FailureReason.NO_API_KEY
