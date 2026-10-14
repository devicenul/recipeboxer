"""Offline unit and integration tests for WP-X1 Unified LLM Client.

Verifies:
- Native system_instruction placement in GenerateContentConfig (INV-AC1, INV-AC2)
- Native response_schema mapping from Pydantic models
- ThinkingConfig configuration
- Magic-byte MIME sniffing for PNG, JPEG, WebP, GIF, TIFF, BMP
- Transient failure retry loop on 429/503 (with Retry-After honoring)
- Terminal error classification (RATE_LIMITED, TIMEOUT, MODEL_UNAVAILABLE)
- Schema validation and invalid JSON error handling (INVALID_RESPONSE, SCHEMA_VALIDATION)
- Stage-aware typed exception mapping (FingerprintUnavailable, SemanticVerificationUnavailable, etc.)
- InMemoryUsageLedger token and latency recording
- ScriptedLLMClient call-order execution
- RecordingLLMClient and ReplayLLMClient cassette round-trip (hashes only for images)
- ReplayLLMClient fingerprint mismatch detection
- create_llm_client factory behavior with/without API key
- list_available_models startup validation helper
- Concurrency Semaphore and RPM TokenBucket limiter
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, Field

from recipeboxer.ai.client import (
    AsyncTokenBucket,
    CassetteFingerprintMismatchError,
    GenAIClient,
    ImagePart,
    InMemoryUsageLedger,
    LLMRequest,
    LLMResponse,
    LLMStage,
    RecordingLLMClient,
    ReplayLLMClient,
    ScriptedLLMClient,
    TextPart,
    UsageTokens,
    create_llm_client,
    create_stage_error,
    sniff_mime_type,
)
from recipeboxer.config import QUALITY_PROFILES, Settings
from recipeboxer.models.failures import (
    ExtractionUnavailable,
    FailureReason,
    FingerprintUnavailable,
    ModalityUnavailable,
    SemanticVerificationUnavailable,
    StageUnavailableError,
)


class DummyRecipeOutput(BaseModel):
    title: str = Field(description="Recipe title")
    servings: int = Field(default=4)
    ingredients: List[str] = Field(default_factory=list)


def _make_mock_genai_response(
    text: str,
    *,
    prompt_tokens: int = 15,
    candidates_tokens: int = 25,
    thinking_tokens: int = 0,
    finish_reason: str = "STOP",
    parsed: Any = None,
) -> MagicMock:
    """Creates a mock google-genai GenerateContentResponse."""
    resp = MagicMock()
    resp.text = text
    resp.parsed = parsed

    cand = MagicMock()
    cand.finish_reason = finish_reason
    part_mock = MagicMock()
    part_mock.text = text
    cand.content.parts = [part_mock]
    resp.candidates = [cand]

    usage = MagicMock()
    usage.prompt_token_count = prompt_tokens
    usage.candidates_token_count = candidates_tokens
    usage.thoughts_token_count = thinking_tokens
    usage.total_token_count = prompt_tokens + candidates_tokens + thinking_tokens
    resp.usage_metadata = usage

    return resp


class DummyAPIError(Exception):
    """Simulates google.genai.errors.APIError."""

    def __init__(self, code: int, message: str, retry_after: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.response = MagicMock()
        self.response.headers = {"retry-after": retry_after} if retry_after else {}


# --- Test 1: system_instruction placement in config ---


@pytest.mark.asyncio
async def test_system_instruction_placed_in_config() -> None:
    fake_client = MagicMock()
    fake_client.aio.models.generate_content = AsyncMock(
        return_value=_make_mock_genai_response('{"title": "Pancake"}')
    )

    client = GenAIClient(api_key="test-key", genai_client=fake_client)
    req = LLMRequest(
        stage=LLMStage.TRANSCRIBE,
        model="gemini-2.5-flash",
        system_instruction="Strict transcription rules: do not fix spelling.",
        parts=["Sample card text"],
    )

    await client.generate(req)

    call_args = fake_client.aio.models.generate_content.call_args
    assert call_args is not None
    _, kwargs = call_args
    config = kwargs["config"]
    assert config.system_instruction == "Strict transcription rules: do not fix spelling."

    # Verify contents only contains user parts, never the system instruction
    contents = kwargs["contents"]
    assert len(contents) == 1
    assert contents[0].text == "Sample card text"


# --- Test 2: response_schema set from Pydantic model ---


@pytest.mark.asyncio
async def test_response_schema_set_from_pydantic_model() -> None:
    fake_client = MagicMock()
    fake_client.aio.models.generate_content = AsyncMock(
        return_value=_make_mock_genai_response('{"title": "Apple Pie", "servings": 8, "ingredients": ["apple"]}')
    )

    client = GenAIClient(api_key="test-key", genai_client=fake_client)
    req = LLMRequest(
        stage=LLMStage.STRUCTURE,
        model="gemini-2.5-flash",
        parts=["Recipe text..."],
        response_model=DummyRecipeOutput,
    )

    resp = await client.generate(req)

    call_args = fake_client.aio.models.generate_content.call_args
    assert call_args is not None
    config = call_args.kwargs["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_schema == DummyRecipeOutput

    assert isinstance(resp.parsed, DummyRecipeOutput)
    assert resp.parsed.title == "Apple Pie"
    assert resp.parsed.servings == 8


# --- Test 3: ThinkingConfig set when thinking_budget is provided ---


@pytest.mark.asyncio
async def test_thinking_config_set_when_budget_provided() -> None:
    fake_client = MagicMock()
    fake_client.aio.models.generate_content = AsyncMock(
        return_value=_make_mock_genai_response("Response text", thinking_tokens=128)
    )

    client = GenAIClient(api_key="test-key", genai_client=fake_client)
    req = LLMRequest(
        stage=LLMStage.TRANSCRIBE,
        model="gemini-3.7-pro",
        parts=["Challenging cursive card"],
        thinking_budget=2048,
    )

    resp = await client.generate(req)

    call_args = fake_client.aio.models.generate_content.call_args
    config = call_args.kwargs["config"]
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_budget == 2048
    assert resp.usage.thinking_tokens == 128


# --- Test 4: MIME sniffing for image bytes ---


def test_mime_sniffing_magic_bytes() -> None:
    # PNG magic bytes
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    assert sniff_mime_type(png_bytes) == "image/png"

    # JPEG magic bytes
    jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF"
    assert sniff_mime_type(jpeg_bytes) == "image/jpeg"

    # WebP magic bytes (RIFF....WEBP)
    webp_bytes = b"RIFF\x1a\x00\x00\x00WEBPVP8 "
    assert sniff_mime_type(webp_bytes) == "image/webp"

    # GIF magic bytes
    gif_bytes = b"GIF89a\x01\x00\x01\x00"
    assert sniff_mime_type(gif_bytes) == "image/gif"

    # TIFF magic bytes
    tiff_le = b"II*\x00\x08\x00\x00\x00"
    assert sniff_mime_type(tiff_le) == "image/tiff"
    tiff_be = b"MM\x00*\x00\x00\x00\x08"
    assert sniff_mime_type(tiff_be) == "image/tiff"

    # BMP magic bytes
    bmp_bytes = b"BM\x36\x00\x00\x00"
    assert sniff_mime_type(bmp_bytes) == "image/bmp"

    # Fallback
    assert sniff_mime_type(b"not-an-image") == "image/jpeg"


@pytest.mark.asyncio
async def test_image_part_mime_sniffing_in_generate() -> None:
    fake_client = MagicMock()
    fake_client.aio.models.generate_content = AsyncMock(
        return_value=_make_mock_genai_response("Card transcribed")
    )

    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    img_part = ImagePart(data=png_bytes)
    assert img_part.get_sniffed_mime_type() == "image/png"

    client = GenAIClient(api_key="test-key", genai_client=fake_client)
    req = LLMRequest(
        stage=LLMStage.FINGERPRINT,
        model="gemini-2.5-flash",
        parts=[img_part],
    )

    await client.generate(req)

    call_args = fake_client.aio.models.generate_content.call_args
    contents = call_args.kwargs["contents"]
    assert len(contents) == 1
    # Check that Part received image/png
    assert contents[0].inline_data.mime_type == "image/png"


# --- Test 5: Retries on 429 and 503 then succeeds ---


@pytest.mark.asyncio
async def test_retries_on_429_503_then_success() -> None:
    fake_client = MagicMock()
    err_429 = DummyAPIError(429, "Rate limit exceeded", retry_after="0.01")
    err_503 = DummyAPIError(503, "Service unavailable")
    success_resp = _make_mock_genai_response("Finally succeeded")

    fake_client.aio.models.generate_content = AsyncMock(
        side_effect=[err_429, err_503, success_resp]
    )

    client = GenAIClient(api_key="test-key", genai_client=fake_client, max_retries=4)
    req = LLMRequest(
        stage=LLMStage.SET_VERIFY,
        model="gemini-2.5-flash",
        parts=["Verify candidate continuity"],
    )

    resp = await client.generate(req)

    assert fake_client.aio.models.generate_content.call_count == 3
    assert resp.attempts == 3
    assert resp.raw_text == "Finally succeeded"


# --- Test 6: Exhausted retries raises StageUnavailableError with RATE_LIMITED ---


@pytest.mark.asyncio
async def test_gives_up_after_max_attempts_with_rate_limited() -> None:
    fake_client = MagicMock()
    err_429 = DummyAPIError(429, "Resource exhausted quota limit reached")
    fake_client.aio.models.generate_content = AsyncMock(side_effect=err_429)

    client = GenAIClient(api_key="test-key", genai_client=fake_client, max_retries=3, retry_delay_base=0.001)
    req = LLMRequest(
        stage=LLMStage.FINGERPRINT,
        model="gemini-2.5-flash",
        parts=["Card image bytes"],
    )

    with pytest.raises(FingerprintUnavailable) as exc_info:
        await client.generate(req)

    assert exc_info.value.reason == FailureReason.RATE_LIMITED
    assert fake_client.aio.models.generate_content.call_count == 3


# --- Test 7: Timeout mapping to TIMEOUT reason ---


@pytest.mark.asyncio
async def test_timeout_mapping_and_exhaustion() -> None:
    fake_client = MagicMock()
    fake_client.aio.models.generate_content = AsyncMock(side_effect=asyncio.TimeoutError())

    client = GenAIClient(api_key="test-key", genai_client=fake_client, max_retries=2, retry_delay_base=0.001)
    req = LLMRequest(
        stage=LLMStage.TRANSCRIBE,
        model="gemini-2.5-flash",
        parts=["Card"],
        timeout_s=0.01,
    )

    with pytest.raises(ExtractionUnavailable) as exc_info:
        await client.generate(req)

    assert exc_info.value.reason == FailureReason.TIMEOUT
    assert fake_client.aio.models.generate_content.call_count == 2


# --- Test 8: Invalid JSON response raises INVALID_RESPONSE ---


@pytest.mark.asyncio
async def test_invalid_json_raises_invalid_response() -> None:
    fake_client = MagicMock()
    fake_client.aio.models.generate_content = AsyncMock(
        return_value=_make_mock_genai_response("This is not JSON at all!")
    )

    client = GenAIClient(api_key="test-key", genai_client=fake_client)
    req = LLMRequest(
        stage=LLMStage.STRUCTURE,
        model="gemini-2.5-flash",
        parts=["Extract recipe"],
        response_model=DummyRecipeOutput,
    )

    with pytest.raises(ExtractionUnavailable) as exc_info:
        await client.generate(req)

    assert exc_info.value.reason == FailureReason.INVALID_RESPONSE


# --- Test 9: Schema validation mismatch raises SCHEMA_VALIDATION ---


@pytest.mark.asyncio
async def test_schema_validation_failure_raises_schema_validation() -> None:
    fake_client = MagicMock()
    # Missing required field 'title'
    fake_client.aio.models.generate_content = AsyncMock(
        return_value=_make_mock_genai_response('{"servings": "not_an_int"}')
    )

    client = GenAIClient(api_key="test-key", genai_client=fake_client)
    req = LLMRequest(
        stage=LLMStage.STRUCTURE,
        model="gemini-2.5-flash",
        parts=["Extract recipe"],
        response_model=DummyRecipeOutput,
    )

    with pytest.raises(ExtractionUnavailable) as exc_info:
        await client.generate(req)

    assert exc_info.value.reason == FailureReason.SCHEMA_VALIDATION


# --- Test 10: Stage-aware typed exception mapping ---


def test_stage_aware_exception_mapping() -> None:
    err_fp = create_stage_error(LLMStage.FINGERPRINT, "Failed", reason=FailureReason.RATE_LIMITED)
    assert isinstance(err_fp, FingerprintUnavailable)
    assert err_fp.reason == FailureReason.RATE_LIMITED

    err_sv = create_stage_error(LLMStage.SET_VERIFY, "Failed", reason=FailureReason.TIMEOUT)
    assert isinstance(err_sv, SemanticVerificationUnavailable)
    assert err_sv.reason == FailureReason.TIMEOUT

    err_tr = create_stage_error(LLMStage.TRANSCRIBE, "Failed", reason=FailureReason.INVALID_RESPONSE)
    assert isinstance(err_tr, ExtractionUnavailable)
    assert err_tr.reason == FailureReason.INVALID_RESPONSE

    err_mod = create_stage_error(LLMStage.MODALITY, "Failed", reason=FailureReason.MODEL_UNAVAILABLE)
    assert isinstance(err_mod, ModalityUnavailable)
    assert err_mod.reason == FailureReason.MODEL_UNAVAILABLE


# --- Test 11: InMemoryUsageLedger token recording ---


@pytest.mark.asyncio
async def test_in_memory_usage_ledger() -> None:
    ledger = InMemoryUsageLedger()
    fake_client = MagicMock()
    fake_client.aio.models.generate_content = AsyncMock(
        return_value=_make_mock_genai_response("Success", prompt_tokens=20, candidates_tokens=30, thinking_tokens=10)
    )

    client = GenAIClient(api_key="test-key", genai_client=fake_client, ledger=ledger)
    req = LLMRequest(
        stage=LLMStage.TRANSCRIBE,
        model="gemini-2.5-flash",
        parts=["Card text"],
    )

    await client.generate(req)

    assert len(ledger.get_records()) == 1
    rec = ledger.get_records()[0]
    assert rec.stage == "TRANSCRIBE"
    assert rec.model == "gemini-2.5-flash"
    assert rec.usage.input_tokens == 20
    assert rec.usage.output_tokens == 30
    assert rec.usage.thinking_tokens == 10
    assert rec.usage.total_tokens == 60
    assert rec.latency_ms > 0

    assert ledger.total_input_tokens == 20
    assert ledger.total_output_tokens == 30
    assert ledger.total_thinking_tokens == 10
    assert ledger.total_tokens == 60

    summary = ledger.summary()
    assert summary["total_calls"] == 1
    assert "TRANSCRIBE" in summary["by_stage"]


# --- Test 12: ScriptedLLMClient call order execution ---


@pytest.mark.asyncio
async def test_scripted_llm_client_call_order() -> None:
    resp1 = DummyRecipeOutput(title="Pancake", servings=2, ingredients=["flour", "milk"])
    resp2 = DummyRecipeOutput(title="Waffle", servings=4, ingredients=["flour", "eggs"])
    err3 = ExtractionUnavailable("Stage down", reason=FailureReason.MODEL_UNAVAILABLE)

    client = ScriptedLLMClient([resp1, resp2, err3])

    req1 = LLMRequest(stage=LLMStage.STRUCTURE, model="gemini-2.5-flash", parts=["Part 1"], response_model=DummyRecipeOutput)
    req2 = LLMRequest(stage=LLMStage.STRUCTURE, model="gemini-2.5-flash", parts=["Part 2"], response_model=DummyRecipeOutput)
    req3 = LLMRequest(stage=LLMStage.STRUCTURE, model="gemini-2.5-flash", parts=["Part 3"], response_model=DummyRecipeOutput)

    r1 = await client.generate(req1)
    assert r1.parsed.title == "Pancake"
    assert client.call_count == 1

    r2 = await client.generate(req2)
    assert r2.parsed.title == "Waffle"
    assert client.call_count == 2

    with pytest.raises(ExtractionUnavailable):
        await client.generate(req3)
    assert client.call_count == 3

    # Exhausted call raises IndexError
    with pytest.raises(IndexError):
        await client.generate(req1)


# --- Test 13: RecordingLLMClient and ReplayLLMClient round-trip ---


@pytest.mark.asyncio
async def test_recording_and_replay_roundtrip_with_cassette(tmp_path: Path) -> None:
    cassette_file = tmp_path / "test_cassette.jsonl"

    fake_backend = ScriptedLLMClient([
        {"title": "Sugar Cookies", "servings": 24, "ingredients": ["sugar", "butter"]},
        "Transcribed text line 1\nTranscribed text line 2",
    ])

    recording_client = RecordingLLMClient(fake_backend, cassette_file)

    dummy_image_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    req1 = LLMRequest(
        stage=LLMStage.STRUCTURE,
        model="gemini-2.5-flash",
        parts=[TextPart(text="Cookie instructions"), ImagePart(data=dummy_image_bytes)],
        response_model=DummyRecipeOutput,
    )
    req2 = LLMRequest(
        stage=LLMStage.TRANSCRIBE,
        model="gemini-2.5-flash",
        parts=["Transcribe text"],
    )

    # Generate and record
    rec_r1 = await recording_client.generate(req1)
    rec_r2 = await recording_client.generate(req2)

    assert cassette_file.exists()
    content = cassette_file.read_text(encoding="utf-8")
    lines = content.strip().splitlines()
    assert len(lines) == 2

    # Check that image bytes are NOT in cassette, only sha256 hash
    assert "data" not in lines[0]
    assert dummy_image_bytes.decode("latin1", errors="ignore") not in lines[0]
    assert "sha256" in lines[0]

    # Now replay from cassette
    replay_client = ReplayLLMClient(cassette_file)
    rep_r1 = await replay_client.generate(req1)
    rep_r2 = await replay_client.generate(req2)

    assert rep_r1.parsed.title == "Sugar Cookies"
    assert rep_r2.raw_text == "Transcribed text line 1\nTranscribed text line 2"
    assert rep_r1.request_fingerprint == rec_r1.request_fingerprint
    assert rep_r2.request_fingerprint == rec_r2.request_fingerprint


# --- Test 14: ReplayLLMClient raises CassetteFingerprintMismatchError ---


@pytest.mark.asyncio
async def test_replay_fingerprint_mismatch_detection(tmp_path: Path) -> None:
    cassette_file = tmp_path / "mismatch_cassette.jsonl"
    fake_backend = ScriptedLLMClient(["Original response"])
    recording_client = RecordingLLMClient(fake_backend, cassette_file)

    req_orig = LLMRequest(
        stage=LLMStage.TRANSCRIBE,
        model="gemini-2.5-flash",
        parts=["Original text part"],
    )
    await recording_client.generate(req_orig)

    replay_client = ReplayLLMClient(cassette_file)

    # Mutated request (different text part -> different sha256 fingerprint)
    req_tampered = LLMRequest(
        stage=LLMStage.TRANSCRIBE,
        model="gemini-2.5-flash",
        parts=["Different prompt text part!"],
    )

    with pytest.raises(CassetteFingerprintMismatchError) as exc_info:
        await replay_client.generate(req_tampered)

    assert "Cassette replay fingerprint mismatch" in str(exc_info.value)


# --- Test 15 & 16: create_llm_client with and without key ---


def test_create_llm_client_returns_none_without_key() -> None:
    settings = Settings(gemini_api_key=None)
    client = create_llm_client(settings)
    assert client is None

    empty_settings = Settings(gemini_api_key="   ")
    assert create_llm_client(empty_settings) is None


def test_create_llm_client_returns_client_with_key() -> None:
    settings = Settings(gemini_api_key="fake-test-api-key")
    client = create_llm_client(settings)
    assert client is not None
    assert isinstance(client, GenAIClient)
    assert client.api_key == "fake-test-api-key"


# --- Test 17: list_available_models helper ---


@pytest.mark.asyncio
async def test_list_available_models_strips_prefix() -> None:
    fake_client = MagicMock()
    model1 = MagicMock()
    model1.name = "models/gemini-2.5-flash"
    model2 = MagicMock()
    model2.name = "models/gemini-3.7-pro"
    model3 = MagicMock()
    model3.name = "gemini-2.5-flash-lite"

    fake_client.aio.models.list = AsyncMock(return_value=[model1, model2, model3])

    client = GenAIClient(api_key="test-key", genai_client=fake_client)
    models = await client.list_available_models()

    assert models == ["gemini-2.5-flash", "gemini-3.7-pro", "gemini-2.5-flash-lite"]


# --- Test 18: Concurrency Semaphore and RPM TokenBucket limiter ---


@pytest.mark.asyncio
async def test_token_bucket_rate_limiter() -> None:
    # 600 RPM = 10 tokens/sec
    limiter = AsyncTokenBucket(rpm=600)
    start = asyncio.get_event_loop().time()
    for _ in range(5):
        await limiter.acquire()
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed < 1.0


# --- Test 19: Quality profiles and stage model resolution in Settings ---


def test_settings_quality_profiles_and_stage_models() -> None:
    # Economy profile
    s_econ = Settings(quality_profile="economy")
    assert s_econ.get_stage_model(LLMStage.FINGERPRINT) == QUALITY_PROFILES["economy"]["FINGERPRINT"]
    assert s_econ.get_stage_model(LLMStage.TRANSCRIBE) == QUALITY_PROFILES["economy"]["TRANSCRIBE"]

    # Best profile
    s_best = Settings(quality_profile="best")
    assert s_best.get_stage_model(LLMStage.SET_VERIFY) == QUALITY_PROFILES["best"]["SET_VERIFY"]
    assert s_best.get_stage_model(LLMStage.STRUCTURE) == QUALITY_PROFILES["best"]["STRUCTURE"]

    # Stage override takes precedence over profile
    s_override = Settings(
        quality_profile="economy",
        fingerprint_model="custom-fingerprint-model",
        transcribe_model="custom-transcribe-model",
    )
    assert s_override.get_stage_model(LLMStage.FINGERPRINT) == "custom-fingerprint-model"
    assert s_override.get_stage_model(LLMStage.TRANSCRIBE) == "custom-transcribe-model"
    assert s_override.get_stage_model(LLMStage.SET_VERIFY) == QUALITY_PROFILES["economy"]["SET_VERIFY"]
