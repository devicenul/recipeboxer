"""WP-UP3: LLM error mapping / retry policy (offline, stubbed SDK exceptions).

Root cause addressed: httpx ``RemoteProtocolError('')`` / ``ReadError('')`` have an empty ``str()``
and previously fell through ``_classify_error`` to a terminal MODEL_UNAVAILABLE after 1 attempt with
an empty message. Policy now: transport / 5xx / unknown-empty errors are retryable with backoff and
the terminal message always carries exception type (+ status code); only real 4xx are terminal.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from recipeboxer.ai.client import GenAIClient, LLMRequest, LLMStage, describe_exception
from recipeboxer.models.failures import FailureReason, StageUnavailableError


def _ok_response(text: str = "ok") -> MagicMock:
    resp = MagicMock()
    resp.text = text
    resp.parsed = None
    part = MagicMock()
    part.text = text
    part.thought = False
    cand = MagicMock()
    cand.finish_reason = "STOP"
    cand.content.parts = [part]
    resp.candidates = [cand]
    usage = MagicMock()
    usage.prompt_token_count = 1
    usage.candidates_token_count = 1
    usage.thoughts_token_count = 0
    usage.total_token_count = 2
    resp.usage_metadata = usage
    return resp


class _StatusError(Exception):
    """Mimics google.genai.errors.APIError (code attribute, optional status text)."""

    def __init__(self, code: int, message: str = "", status: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.response = MagicMock()
        self.response.headers = {}


def _client(side_effect, max_retries: int = 4) -> tuple[GenAIClient, MagicMock]:
    fake = MagicMock()
    fake.aio.models.generate_content = AsyncMock(side_effect=side_effect)
    return GenAIClient(api_key="test-key", genai_client=fake, max_retries=max_retries, retry_delay_base=0.001), fake


def _req(**kw) -> LLMRequest:
    return LLMRequest(stage=LLMStage.SET_VERIFY, model="m", parts=["x"], **kw)


@pytest.mark.parametrize(
    "exc",
    [
        httpx.RemoteProtocolError(""),
        httpx.ReadError(""),
        httpx.ConnectError(""),
        ConnectionResetError(),
        _StatusError(503, "", status="UNAVAILABLE"),
        _StatusError(500, "internal"),
        RuntimeError(""),  # unknown exception with empty message
    ],
    ids=["remote_protocol", "read_error", "connect", "conn_reset", "503", "500", "unknown_empty"],
)
def test_transient_errors_are_retryable(exc: BaseException) -> None:
    client, _ = _client([])
    retryable, reason = client._classify_error(exc)
    assert retryable is True
    assert reason in {FailureReason.MODEL_UNAVAILABLE, FailureReason.TIMEOUT}


@pytest.mark.parametrize(
    "code,expected",
    [
        (400, {FailureReason.INVALID_REQUEST, FailureReason.SCHEMA_UNSUPPORTED}),
        (401, {FailureReason.NO_API_KEY}),
        (403, {FailureReason.NO_API_KEY}),
        (404, {FailureReason.MODEL_UNAVAILABLE}),
        (422, {FailureReason.INVALID_REQUEST}),
    ],
)
def test_real_4xx_are_terminal(code: int, expected: set) -> None:
    client, _ = _client([])
    retryable, reason = client._classify_error(_StatusError(code, "bad"))
    assert retryable is False
    assert reason in expected


def test_429_and_408_retryable_with_specific_reasons() -> None:
    client, _ = _client([])
    assert client._classify_error(_StatusError(429, "")) == (True, FailureReason.RATE_LIMITED)
    assert client._classify_error(_StatusError(408, "")) == (True, FailureReason.TIMEOUT)
    assert client._classify_error(asyncio.TimeoutError()) == (True, FailureReason.TIMEOUT)


def test_programming_errors_are_terminal() -> None:
    client, _ = _client([])
    retryable, _ = client._classify_error(TypeError("bad arg"))
    assert retryable is False


def test_status_found_through_cause_chain() -> None:
    client, _ = _client([])
    try:
        try:
            raise _StatusError(503, "")
        except _StatusError as inner:
            raise RuntimeError("") from inner
    except RuntimeError as outer:
        retryable, reason = client._classify_error(outer)
    assert retryable is True and reason == FailureReason.MODEL_UNAVAILABLE


def test_describe_exception_never_empty_and_has_type_and_status() -> None:
    assert describe_exception(httpx.RemoteProtocolError("")) == "RemoteProtocolError: <empty message>"
    text = describe_exception(_StatusError(503, ""))
    assert "_StatusError" in text and "status=503" in text


@pytest.mark.asyncio
async def test_empty_transport_error_retried_then_succeeds() -> None:
    client, fake = _client([httpx.RemoteProtocolError(""), httpx.ReadError(""), _ok_response("done")])
    resp = await client.generate(_req())
    assert resp.raw_text == "done"
    assert fake.aio.models.generate_content.call_count == 3


@pytest.mark.asyncio
async def test_exhausted_transient_message_has_type_and_status() -> None:
    client, fake = _client(_StatusError(503, "", status="UNAVAILABLE"), max_retries=3)
    with pytest.raises(StageUnavailableError) as ei:
        await client.generate(_req())
    assert fake.aio.models.generate_content.call_count == 3
    msg = str(ei.value)
    assert "after 3 attempts" in msg
    assert "_StatusError" in msg and "status=503" in msg
    assert not msg.rstrip().endswith(":")
    assert ei.value.details.get("status_code") == 503
    assert ei.value.details.get("exception_type") == "_StatusError"


@pytest.mark.asyncio
async def test_real_4xx_not_retried() -> None:
    client, fake = _client(_StatusError(400, "invalid argument"))
    with pytest.raises(StageUnavailableError) as ei:
        await client.generate(_req())
    assert fake.aio.models.generate_content.call_count == 1
    assert "status=400" in str(ei.value)


@pytest.mark.asyncio
async def test_per_request_max_attempts_cap() -> None:
    client, fake = _client(httpx.ReadError(""), max_retries=4)
    with pytest.raises(StageUnavailableError):
        await client.generate(_req(metadata={"max_attempts": 2}))
    assert fake.aio.models.generate_content.call_count == 2


def test_max_attempts_metadata_not_in_fingerprint() -> None:
    a = _req()
    b = _req(metadata={"max_attempts": 2})
    assert a.compute_fingerprint() == b.compute_fingerprint()
