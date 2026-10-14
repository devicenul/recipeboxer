"""Replay, scripted, and recording clients for offline testing (WP-X1).

Enforces strict call-order playback and request-fingerprint verification (INV-AC1, INV-AC2).
Images are hashed by their SHA-256 byte digest and never stored as raw bytes or referenced
by filename.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from pydantic import BaseModel

from recipeboxer.ai.client import (
    ImagePart,
    LLMClient,
    LLMRequest,
    LLMResponse,
    TextPart,
    UsageTokens,
)


class CassetteFingerprintMismatchError(RuntimeError):
    """Raised when a replay request fingerprint does not match the cassette record in call order."""

    pass


class ScriptedLLMClient(LLMClient):
    """Offline test client that serves responses strictly by CALL ORDER.

    Enforces INV-AC1/INV-AC2: It never keys by filename, image content, or card ID.
    Records all received requests for inspection.
    """

    def __init__(self, responses: Optional[Sequence[Any]] = None) -> None:
        self._responses: List[Any] = list(responses or [])
        self._call_count: int = 0
        self.requests: List[LLMRequest] = []

    def set_responses(self, responses: Sequence[Any]) -> None:
        """Sets the sequence of scripted responses or exceptions to return."""
        self._responses = list(responses)
        self._call_count = 0

    def add_response(self, response: Any) -> None:
        """Appends a response or exception to the scripted sequence."""
        self._responses.append(response)

    @property
    def call_count(self) -> int:
        """Total number of generate() calls made to this scripted client."""
        return self._call_count

    def reset(self) -> None:
        """Resets call counter and recorded requests."""
        self._call_count = 0
        self.requests.clear()

    async def generate(self, request: LLMRequest) -> LLMResponse[Any]:
        """Returns the next scripted response or raises the next scripted exception."""
        self.requests.append(request)
        idx = self._call_count
        if idx >= len(self._responses):
            raise IndexError(
                f"ScriptedLLMClient exhausted: received call #{idx + 1}, but only {len(self._responses)} scripted responses configured."
            )
        self._call_count += 1
        item = self._responses[idx]

        if isinstance(item, Exception):
            raise item

        req_fp = request.compute_fingerprint()

        if isinstance(item, LLMResponse):
            return LLMResponse(
                parsed=item.parsed,
                raw_text=item.raw_text,
                usage=item.usage,
                model=item.model or request.model,
                latency_ms=item.latency_ms,
                attempts=item.attempts,
                finish_reason=item.finish_reason,
                request_fingerprint=req_fp,
            )

        if isinstance(item, BaseModel):
            return LLMResponse(
                parsed=item,
                raw_text=item.model_dump_json(),
                usage=UsageTokens(input_tokens=10, output_tokens=10, total_tokens=20),
                model=request.model,
                latency_ms=5.0,
                attempts=1,
                finish_reason="STOP",
                request_fingerprint=req_fp,
            )

        if isinstance(item, (dict, list)):
            raw_text = json.dumps(item)
            parsed = None
            if request.response_model and isinstance(item, dict):
                parsed = request.response_model.model_validate(item)
            return LLMResponse(
                parsed=parsed,
                raw_text=raw_text,
                usage=UsageTokens(input_tokens=10, output_tokens=10, total_tokens=20),
                model=request.model,
                latency_ms=5.0,
                attempts=1,
                finish_reason="STOP",
                request_fingerprint=req_fp,
            )

        if isinstance(item, str):
            raw_text = item
            parsed = None
            if request.response_model:
                parsed = request.response_model.model_validate_json(raw_text)
            return LLMResponse(
                parsed=parsed,
                raw_text=raw_text,
                usage=UsageTokens(input_tokens=10, output_tokens=10, total_tokens=20),
                model=request.model,
                latency_ms=5.0,
                attempts=1,
                finish_reason="STOP",
                request_fingerprint=req_fp,
            )

        return LLMResponse(
            parsed=item,
            raw_text=str(item),
            usage=UsageTokens(),
            model=request.model,
            request_fingerprint=req_fp,
        )


class RecordingLLMClient(LLMClient):
    """Wraps an active LLMClient and records interactions into a JSONL cassette.

    Images are stored ONLY as sha256 hashes, preserving opacity and compact storage.
    """

    def __init__(self, inner_client: LLMClient, cassette_path: Union[str, Path]) -> None:
        self._inner_client = inner_client
        self.cassette_path = Path(cassette_path)
        self.cassette_path.parent.mkdir(parents=True, exist_ok=True)
        self._call_index = 0
        self.requests: List[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse[Any]:
        self.requests.append(request)
        response = await self._inner_client.generate(request)

        # Build serializable cassette record
        req_fp = request.compute_fingerprint()
        parts_repr = []
        for p in request.parts:
            if isinstance(p, TextPart):
                parts_repr.append({"type": "text", "text": p.text, "label": p.label})
            elif isinstance(p, ImagePart):
                parts_repr.append({
                    "type": "image",
                    "sha256": p.sha256(),
                    "mime_type": p.get_sniffed_mime_type(),
                    "label": p.label,
                })

        parsed_data = None
        if response.parsed is not None:
            if isinstance(response.parsed, BaseModel):
                parsed_data = response.parsed.model_dump()
            elif isinstance(response.parsed, (dict, list, str, int, float, bool)):
                parsed_data = response.parsed

        record = {
            "call_index": self._call_index,
            "request_fingerprint": req_fp,
            "stage": request.stage.value if hasattr(request.stage, "value") else str(request.stage),
            "model": request.model,
            "system_instruction": request.system_instruction or "",
            "parts": parts_repr,
            "response": {
                "raw_text": response.raw_text,
                "parsed": parsed_data,
                "usage": response.usage.model_dump(),
                "model": response.model,
                "latency_ms": response.latency_ms,
                "attempts": response.attempts,
                "finish_reason": response.finish_reason,
            },
        }

        with open(self.cassette_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        self._call_index += 1
        return response


class ReplayLLMClient(LLMClient):
    """Replays recorded cassette responses strictly in call order.

    Verifies that each incoming request matches the recorded request fingerprint,
    raising CassetteFingerprintMismatchError if any stage, model, instruction, text,
    or image byte hash differs.
    """

    def __init__(
        self,
        cassette_path: Optional[Union[str, Path]] = None,
        *,
        records: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> None:
        self.cassette_path = Path(cassette_path) if cassette_path else None
        self._records: List[Dict[str, Any]] = []
        if records is not None:
            self._records = list(records)
        elif self.cassette_path and self.cassette_path.exists():
            with open(self.cassette_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self._records.append(json.loads(line))
        self._call_index: int = 0
        self.requests: List[LLMRequest] = []

    @property
    def call_index(self) -> int:
        return self._call_index

    @property
    def remaining_records(self) -> int:
        return max(0, len(self._records) - self._call_index)

    def reset(self) -> None:
        self._call_index = 0
        self.requests.clear()

    async def generate(self, request: LLMRequest) -> LLMResponse[Any]:
        self.requests.append(request)
        if self._call_index >= len(self._records):
            raise RuntimeError(
                f"ReplayLLMClient exhausted: received call #{self._call_index + 1}, but cassette only contains {len(self._records)} interactions."
            )

        expected = self._records[self._call_index]
        actual_fp = request.compute_fingerprint()
        expected_fp = expected.get("request_fingerprint", "")

        if actual_fp != expected_fp:
            raise CassetteFingerprintMismatchError(
                f"Cassette replay fingerprint mismatch at call order {self._call_index}:\n"
                f"  Expected fingerprint: {expected_fp}\n"
                f"  Actual fingerprint:   {actual_fp}\n"
                f"  Stage: expected {expected.get('stage')}, got {request.stage}\n"
                f"  Model: expected {expected.get('model')}, got {request.model}"
            )

        resp_data = expected.get("response", {})
        raw_text = resp_data.get("raw_text", "")
        parsed = None

        if request.response_model is not None:
            if resp_data.get("parsed") is not None:
                parsed_raw = resp_data["parsed"]
                if isinstance(parsed_raw, dict):
                    parsed = request.response_model.model_validate(parsed_raw)
                else:
                    parsed = parsed_raw
            elif raw_text:
                parsed = request.response_model.model_validate_json(raw_text)

        usage_data = resp_data.get("usage", {})
        usage = UsageTokens(**usage_data) if isinstance(usage_data, dict) else UsageTokens()

        resp = LLMResponse(
            parsed=parsed,
            raw_text=raw_text,
            usage=usage,
            model=resp_data.get("model", request.model),
            latency_ms=float(resp_data.get("latency_ms", 0.0)),
            attempts=int(resp_data.get("attempts", 1)),
            finish_reason=resp_data.get("finish_reason", "STOP"),
            request_fingerprint=actual_fp,
        )
        self._call_index += 1
        return resp
