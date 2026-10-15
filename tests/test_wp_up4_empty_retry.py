"""WP-UP4: empty / blank model responses are retried (client + extraction stage) before INVALID_RESPONSE.

Eval symptom: 'Model response is not valid JSON: Expecting value: line 1 column 1 (char 0)' (an empty
body) surfaced as a terminal INVALID_RESPONSE without any retry. Offline only: stubbed SDK / scripted
clients keyed by call order.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from recipeboxer.ai.client import GenAIClient, LLMRequest, LLMResponse, LLMStage
from recipeboxer.ai.replay import ScriptedLLMClient
from recipeboxer.config import Settings
from recipeboxer.extraction import repair as repair_mod
from recipeboxer.extraction.image_prep import PreparedFace
from recipeboxer.extraction.repair import generate_with_empty_retry, is_empty_response_error
from recipeboxer.extraction.transcribe import FaceTranscript, FaceTranscriptList, transcribe
from recipeboxer.models.failures import BudgetExceeded, ExtractionUnavailable, FailureReason, StageUnavailableError


class _Out(BaseModel):
    value: int


def _sdk_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    resp.parsed = None
    part = MagicMock()
    part.text = text
    part.thought = False
    cand = MagicMock()
    cand.finish_reason = "STOP"
    cand.content.parts = [part] if text else []
    resp.candidates = [cand]
    usage = MagicMock()
    usage.prompt_token_count = 1
    usage.candidates_token_count = 1
    usage.thoughts_token_count = 0
    usage.total_token_count = 2
    resp.usage_metadata = usage
    return resp


def _client(side_effect, max_retries: int) -> tuple[GenAIClient, MagicMock]:
    fake = MagicMock()
    fake.aio.models.generate_content = AsyncMock(side_effect=side_effect)
    return GenAIClient(api_key="test-key", genai_client=fake, max_retries=max_retries, retry_delay_base=0.001), fake


def _req(**kw) -> LLMRequest:
    return LLMRequest(stage=LLMStage.STRUCTURE, model="m", parts=["x"], response_model=_Out, **kw)


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=20))


# ---------------------------------------------------------------------------------------------
# Client level
# ---------------------------------------------------------------------------------------------
def test_client_retries_empty_body_even_with_single_attempt_cap():
    client, fake = _client([_sdk_response(""), _sdk_response('{"value": 3}')], max_retries=1)
    resp = _run(client.generate(_req()))
    assert resp.parsed.value == 3
    assert fake.aio.models.generate_content.await_count == 2
    assert resp.attempts == 2


def test_client_whitespace_body_counts_as_empty():
    client, fake = _client([_sdk_response("   \n"), _sdk_response('{"value": 1}')], max_retries=1)
    assert _run(client.generate(_req())).parsed.value == 1
    assert fake.aio.models.generate_content.await_count == 2


def test_client_persistent_empty_raises_distinct_retryable_invalid_response():
    client, fake = _client([_sdk_response(""), _sdk_response(""), _sdk_response("")], max_retries=1)
    with pytest.raises(StageUnavailableError) as ei:
        _run(client.generate(_req()))
    exc = ei.value
    assert exc.reason == FailureReason.INVALID_RESPONSE
    assert exc.retryable is True
    assert exc.details.get("empty_response") is True
    assert "empty response" in str(exc) and "2 attempts" in str(exc)
    assert fake.aio.models.generate_content.await_count == 2
    assert is_empty_response_error(exc)


def test_client_respects_larger_cap_for_empty_bodies():
    client, fake = _client([_sdk_response("")] * 3 + [_sdk_response('{"value": 9}')], max_retries=4)
    assert _run(client.generate(_req())).parsed.value == 9
    assert fake.aio.models.generate_content.await_count == 4


def test_client_invalid_nonempty_json_message_keeps_prefix_and_attempts():
    client, _ = _client([_sdk_response("not json")], max_retries=1)
    with pytest.raises(StageUnavailableError) as ei:
        _run(client.generate(_req()))
    assert str(ei.value).startswith("Model response is not valid JSON:")
    assert "after 1 attempts" in str(ei.value)
    assert not is_empty_response_error(ei.value)


# ---------------------------------------------------------------------------------------------
# Stage level helper
# ---------------------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(repair_mod, "EMPTY_RETRY_BACKOFF_S", 0.0)


def _empty_exc() -> StageUnavailableError:
    return StageUnavailableError("Model response is not valid JSON: Expecting value: line 1 column 1 (char 0)",
                                 reason=FailureReason.INVALID_RESPONSE, retryable=False, details={"raw_text": ""})


def test_stage_helper_retries_once_with_larger_output_budget():
    sc = ScriptedLLMClient([_empty_exc(), _Out(value=5)])
    req = _req(max_output_tokens=1000, metadata={"k": 1})
    resp = _run(generate_with_empty_retry(sc, req))
    assert resp.parsed.value == 5
    assert sc.call_count == 2
    assert sc.requests[1].max_output_tokens == 2000
    assert sc.requests[1].metadata.get("empty_retry") is True and sc.requests[1].metadata.get("k") == 1


def test_stage_helper_retries_on_blank_unparsed_response():
    blank = LLMResponse(parsed=None, raw_text="", model="m")
    sc = ScriptedLLMClient([blank, _Out(value=2)])
    assert _run(generate_with_empty_retry(sc, _req())).parsed.value == 2
    assert sc.call_count == 2


def test_stage_helper_does_not_retry_real_invalid_json():
    bad = StageUnavailableError("Model response is not valid JSON: Expecting ',' delimiter",
                                reason=FailureReason.INVALID_RESPONSE, details={"raw_text": '{"a": 1 "b"'})
    sc = ScriptedLLMClient([bad, _Out(value=1)])
    with pytest.raises(StageUnavailableError):
        _run(generate_with_empty_retry(sc, _req()))
    assert sc.call_count == 1


def test_stage_helper_budget_exceeded_never_retried():
    sc = ScriptedLLMClient([BudgetExceeded("cap"), _Out(value=1)])
    with pytest.raises(BudgetExceeded):
        _run(generate_with_empty_retry(sc, _req()))
    assert sc.call_count == 1


def test_stage_helper_gives_up_after_one_retry():
    sc = ScriptedLLMClient([_empty_exc(), _empty_exc(), _Out(value=1)])
    with pytest.raises(StageUnavailableError) as ei:
        _run(generate_with_empty_retry(sc, _req()))
    assert ei.value.reason == FailureReason.INVALID_RESPONSE
    assert sc.call_count == 2


# ---------------------------------------------------------------------------------------------
# Wired into the extraction stages
# ---------------------------------------------------------------------------------------------
_JPEG = (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xd9")


def _faces(n: int = 1):
    return [PreparedFace(face_index=i, n_faces=n, data=_JPEG, mime_type="image/jpeg", variant="standard",
                         long_edge_px=1600, label=f"Face {i + 1} of {n}") for i in range(n)]


def test_transcribe_survives_one_empty_response():
    good = FaceTranscriptList(faces=[FaceTranscript(face_index=0, text="Take a pound of flour.")])
    sc = ScriptedLLMClient([_empty_exc(), good])
    tr = _run(transcribe(_faces(), sc, Settings(quality_profile="balanced")))
    assert tr.faces[0].text == "Take a pound of flour."
    assert sc.call_count == 2


def test_transcribe_two_empty_responses_is_invalid_response():
    sc = ScriptedLLMClient([_empty_exc(), _empty_exc()])
    with pytest.raises(ExtractionUnavailable) as ei:
        _run(transcribe(_faces(), sc, Settings(quality_profile="balanced")))
    assert ei.value.reason == FailureReason.INVALID_RESPONSE
    assert sc.call_count == 2


def test_structure_and_repair_use_the_empty_retry_helper():
    import importlib
    import inspect

    structure_mod = importlib.import_module("recipeboxer.extraction.structure")

    assert "generate_with_empty_retry" in inspect.getsource(structure_mod.structure)
    assert "generate_with_empty_retry" in inspect.getsource(repair_mod.validate_and_repair)
