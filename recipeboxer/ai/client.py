"""Unified, hardened LLM client for RecipeBoxer (WP-X1).

Defines the core LLMClient protocol and GenAIClient backed by official google-genai SDK.
Provides:
- Native structured output via Pydantic response_schema
- Native system_instruction configuration
- ThinkingConfig support for reasoning models
- Magic-byte MIME sniffing for multimodal parts (PNG/JPEG/WebP/GIF/TIFF/BMP)
- Jittered exponential retries on 429/5xx/timeouts (honors Retry-After headers)
- Per-stage default timeouts and stage-aware failure exceptions
- Concurrency Semaphore and RPM TokenBucket rate limiter
- UsageLedger protocol and InMemoryUsageLedger for token tracking
- Scripted, Recording, and Replay test clients (in recipeboxer.ai.replay)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Generic, List, Optional, Protocol, Sequence, Type, TypeVar, Union

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from recipeboxer.config import Settings, get_settings
from recipeboxer.models.failures import (
    ExtractionUnavailable,
    FailureReason,
    FingerprintUnavailable,
    ModalityUnavailable,
    SemanticVerificationUnavailable,
    StageUnavailableError,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# WP-UP4: an empty/blank structured response always gets at least this many attempts (one retry with
# backoff), regardless of a per-request ``max_attempts`` cap; see GenAIClient.generate.
MIN_ATTEMPTS_ON_EMPTY = 2


class LLMStage(str, Enum):
    """Pipeline stages invoking multimodal LLM generation."""

    FINGERPRINT = "FINGERPRINT"
    SET_VERIFY = "SET_VERIFY"
    TRANSCRIBE = "TRANSCRIBE"
    STRUCTURE = "STRUCTURE"
    GROUNDING = "GROUNDING"
    REPAIR = "REPAIR"
    JUDGE = "JUDGE"
    MODALITY = "MODALITY"


def sniff_mime_type(data: bytes, fallback: str = "image/jpeg") -> str:
    """Sniffs image MIME type from raw magic bytes (or PIL fallback) without relying on filenames."""
    if not data:
        return fallback
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"II*\x00") or data.startswith(b"MM\x00*"):
        return "image/tiff"
    if data.startswith(b"BM"):
        return "image/bmp"

    # PIL fallback if available
    try:
        import io
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            fmt = (img.format or "").upper()
            mapping = {
                "JPEG": "image/jpeg",
                "PNG": "image/png",
                "WEBP": "image/webp",
                "GIF": "image/gif",
                "TIFF": "image/tiff",
                "BMP": "image/bmp",
            }
            if fmt in mapping:
                return mapping[fmt]
    except Exception:
        pass

    return fallback


class TextPart(BaseModel):
    """Plain-text component of an LLM prompt."""

    text: str
    label: Optional[str] = None


class ImagePart(BaseModel):
    """Raw image component of an LLM prompt with automatic MIME sniffing."""

    data: bytes = Field(default=b"", validation_alias=AliasChoices("data", "bytes"))
    mime_type: Optional[str] = None
    label: Optional[str] = None

    def __getattr__(self, name: str) -> Any:
        if name == "bytes":
            return self.data
        return super().__getattr__(name)

    def get_sniffed_mime_type(self) -> str:
        """Returns explicitly provided MIME type or sniffs from bytes."""
        if self.mime_type and self.mime_type != "image/*":
            return self.mime_type
        return sniff_mime_type(self.data)

    def sha256(self) -> str:
        """Computes content-addressable lowercase SHA-256 hex digest of image bytes."""
        return hashlib.sha256(self.data).hexdigest()


class UsageTokens(BaseModel):
    """Token consumption statistics for an LLM interaction."""

    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    total_tokens: int = 0


class LLMRequest(BaseModel):
    """Standardized multimodal LLM generation request across all pipeline stages."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    stage: LLMStage
    model: str
    system_instruction: Optional[str] = None
    parts: List[Union[TextPart, ImagePart]] = Field(default_factory=list)
    response_model: Optional[Type[BaseModel]] = None
    temperature: Optional[float] = None
    max_output_tokens: Optional[int] = None
    thinking_budget: Optional[int] = None
    timeout_s: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("parts", mode="before")
    @classmethod
    def _coerce_parts(cls, raw_parts: Any) -> List[Union[TextPart, ImagePart]]:
        """Accepts strings, dicts, or part objects and normalizes to TextPart/ImagePart."""
        if not raw_parts:
            return []
        coerced: List[Union[TextPart, ImagePart]] = []
        for p in raw_parts:
            if isinstance(p, (TextPart, ImagePart)):
                coerced.append(p)
            elif isinstance(p, str):
                coerced.append(TextPart(text=p))
            elif isinstance(p, dict):
                if "bytes" in p or "data" in p:
                    data = p.get("bytes") or p.get("data")
                    if isinstance(data, str):
                        data = data.encode("utf-8")
                    coerced.append(
                        ImagePart(
                            data=data,
                            mime_type=p.get("mime_type"),
                            label=p.get("label"),
                        )
                    )
                elif "text" in p:
                    coerced.append(TextPart(text=p["text"], label=p.get("label")))
                else:
                    coerced.append(TextPart(text=str(p)))
            else:
                coerced.append(TextPart(text=str(p)))
        return coerced

    def compute_fingerprint(self) -> str:
        """Computes deterministic SHA-256 fingerprint for caching and cassette validation.

        Encodes stage, model, system instruction, text parts, and image SHA-256 byte hashes.
        Never incorporates filenames or filesystem paths (INV-AC1, INV-AC2).
        """
        parts_repr = []
        for p in self.parts:
            if isinstance(p, TextPart):
                parts_repr.append({"type": "text", "text": p.text, "label": p.label})
            elif isinstance(p, ImagePart):
                parts_repr.append({
                    "type": "image",
                    "sha256": p.sha256(),
                    "mime_type": p.get_sniffed_mime_type(),
                    "label": p.label,
                })

        payload = {
            "stage": self.stage.value if isinstance(self.stage, Enum) else str(self.stage),
            "model": self.model,
            "system_instruction": self.system_instruction or "",
            "parts": parts_repr,
            "response_model": self.response_model.__name__ if self.response_model else None,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
            "thinking_budget": self.thinking_budget,
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class LLMResponse(BaseModel, Generic[T]):
    """Standardized response container for all LLM calls."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    parsed: Optional[T] = None
    raw_text: str = ""
    usage: UsageTokens = Field(default_factory=UsageTokens)
    model: str
    latency_ms: float = 0.0
    attempts: int = 1
    finish_reason: Optional[str] = None
    request_fingerprint: str = ""


class LLMClient(ABC):
    """Abstract interface / Protocol for LLM generation clients."""

    @abstractmethod
    async def generate(self, request: LLMRequest) -> LLMResponse[Any]:
        """Executes an LLM request asynchronously and returns a typed response."""
        ...


class UsageRecord(BaseModel):
    """Recorded entry in the token ledger."""

    stage: str
    model: str
    usage: UsageTokens
    latency_ms: float
    request_fingerprint: str = ""
    attempts: int = 1
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)


class UsageLedger(Protocol):
    """Protocol for recording LLM usage and latency metrics."""

    def record(
        self,
        stage: Union[LLMStage, str],
        model: str,
        usage: UsageTokens,
        latency_ms: float,
        *,
        request_fingerprint: Optional[str] = None,
        attempts: int = 1,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        ...


class InMemoryUsageLedger:
    """Thread-safe in-memory ledger recording LLM call usage for cost accounting (WP-B1)."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._records: List[UsageRecord] = []

    def record(
        self,
        stage: Union[LLMStage, str],
        model: str,
        usage: UsageTokens,
        latency_ms: float,
        *,
        request_fingerprint: Optional[str] = None,
        attempts: int = 1,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        stage_str = stage.value if isinstance(stage, Enum) else str(stage)
        record = UsageRecord(
            stage=stage_str,
            model=model,
            usage=usage,
            latency_ms=latency_ms,
            request_fingerprint=request_fingerprint or "",
            attempts=attempts,
            metadata=metadata or {},
        )
        self._records.append(record)

    def get_records(self, stage: Optional[Union[LLMStage, str]] = None) -> List[UsageRecord]:
        """Returns a snapshot of recorded usage entries, optionally filtered by stage."""
        if stage is None:
            return list(self._records)
        stage_str = stage.value if isinstance(stage, Enum) else str(stage)
        return [r for r in self._records if r.stage == stage_str]

    @property
    def total_input_tokens(self) -> int:
        return sum(r.usage.input_tokens for r in self._records)

    @property
    def total_output_tokens(self) -> int:
        return sum(r.usage.output_tokens for r in self._records)

    @property
    def total_thinking_tokens(self) -> int:
        return sum(r.usage.thinking_tokens for r in self._records)

    @property
    def total_tokens(self) -> int:
        return sum(r.usage.total_tokens for r in self._records)

    @property
    def total_latency_ms(self) -> float:
        return sum(r.latency_ms for r in self._records)

    def clear(self) -> None:
        self._records.clear()

    def summary(self) -> Dict[str, Any]:
        """Aggregates usage records by stage and model."""
        by_stage: Dict[str, Dict[str, Any]] = {}
        for r in self._records:
            entry = by_stage.setdefault(
                r.stage,
                {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "thinking_tokens": 0,
                    "total_tokens": 0,
                    "latency_ms": 0.0,
                },
            )
            entry["calls"] += 1
            entry["input_tokens"] += r.usage.input_tokens
            entry["output_tokens"] += r.usage.output_tokens
            entry["thinking_tokens"] += r.usage.thinking_tokens
            entry["total_tokens"] += r.usage.total_tokens
            entry["latency_ms"] += r.latency_ms

        return {
            "total_calls": len(self._records),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_thinking_tokens": self.total_thinking_tokens,
            "total_tokens": self.total_tokens,
            "total_latency_ms": self.total_latency_ms,
            "by_stage": by_stage,
        }


class AsyncTokenBucket:
    """Async token bucket rate limiter for RPM throttling."""

    def __init__(self, rpm: int) -> None:
        self.rpm = max(1, rpm)
        self.capacity = float(self.rpm)
        self.tokens = float(self.rpm)
        self.rate = float(self.rpm) / 60.0  # tokens per second
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Acquires 1 token from the bucket, pausing asynchronously if rate limited."""
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self.last_update
                self.last_update = now
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                needed = (1.0 - self.tokens) / self.rate
                await asyncio.sleep(min(max(needed, 0.01), 2.0))


def create_stage_error(
    stage: Union[LLMStage, str],
    message: str,
    *,
    reason: FailureReason = FailureReason.UNKNOWN,
    card_ids: Optional[List[str]] = None,
    retryable: bool = False,
    details: Optional[Dict[str, Any]] = None,
) -> StageUnavailableError:
    """Instantiates a stage-aware typed StageUnavailableError subclass."""
    stage_str = stage.value if hasattr(stage, "value") else str(stage)
    stage_upper = stage_str.upper()

    if stage_upper == "FINGERPRINT":
        return FingerprintUnavailable(message, reason=reason, card_ids=card_ids, retryable=retryable, details=details)
    if stage_upper == "MODALITY":
        return ModalityUnavailable(message, reason=reason, card_ids=card_ids, retryable=retryable, details=details)
    if stage_upper in ("SET_VERIFY", "CORRELATION"):
        return SemanticVerificationUnavailable(
            message, reason=reason, card_ids=card_ids, retryable=retryable, details=details
        )
    if stage_upper in ("TRANSCRIBE", "STRUCTURE", "GROUNDING", "REPAIR", "EXTRACTION", "JUDGE"):
        return ExtractionUnavailable(message, reason=reason, card_ids=card_ids, retryable=retryable, details=details)

    return StageUnavailableError(message, reason=reason, card_ids=card_ids, retryable=retryable, details=details)


def _extract_json_text(text: str) -> str:
    """Strips Markdown code block wrappers or outer prose to extract raw JSON payload."""
    import re
    s = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", s, re.IGNORECASE)
    if m:
        candidate = m.group(1).strip()
        if candidate:
            return candidate
    first_brace = s.find("{")
    last_brace = s.rfind("}")
    first_bracket = s.find("[")
    last_bracket = s.rfind("]")
    if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
        if last_brace != -1 and last_brace > first_brace:
            return s[first_brace:last_brace + 1].strip()
    elif first_bracket != -1:
        if last_bracket != -1 and last_bracket > first_bracket:
            return s[first_bracket:last_bracket + 1].strip()
    return s


def _extract_retry_after(exc: Exception) -> Optional[float]:
    """Inspects exception attributes for a Retry-After header or delay."""
    resp = getattr(exc, "response", None)
    if resp is not None:
        headers = getattr(resp, "headers", None)
        if headers is not None:
            val = None
            if hasattr(headers, "get"):
                val = headers.get("retry-after") or headers.get("Retry-After")
            if val:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    pass
    return None


# WP-UP3: transient transport-layer exception class names (matched against the MRO by name so that
# no optional dependency has to be importable).
_TRANSIENT_EXC_NAMES = frozenset({
    "TransportError", "NetworkError", "ProtocolError", "RemoteProtocolError", "LocalProtocolError",
    "ReadError", "WriteError", "ConnectError", "CloseError", "ReadTimeout", "WriteTimeout",
    "ConnectTimeout", "PoolTimeout", "TimeoutException", "ServerDisconnectedError",
    "ClientConnectionError", "ClientPayloadError", "ClientOSError", "EndOfStream",
    "ClosedResourceError", "BrokenResourceError", "IncompleteRead", "ServerError",
    "ChunkedEncodingError", "SSLError", "ConnectionError",
})
_TRANSPORT_MODULES = frozenset({"httpx", "httpcore", "anyio", "aiohttp", "h11", "h2", "ssl", "websockets", "urllib3"})
_PROGRAMMING_ERRORS = (TypeError, AttributeError, KeyError, NameError, AssertionError, NotImplementedError, IndexError)


def _exception_chain(exc: BaseException, limit: int = 6) -> List[BaseException]:
    """Returns exc followed by its __cause__/__context__ chain (bounded, cycle-safe)."""
    out: List[BaseException] = []
    seen: set = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen and len(out) < limit:
        out.append(cur)
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    return out


def _status_code_of(exc: BaseException) -> Optional[int]:
    """Extracts an HTTP status code from SDK/HTTP exceptions (code, status_code, response.status_code)."""
    for attr in ("code", "status_code"):
        val = getattr(exc, attr, None)
        if isinstance(val, bool):
            continue
        if isinstance(val, int) and 100 <= val <= 599:
            return val
        if isinstance(val, str) and val.isdigit() and 100 <= int(val) <= 599:
            return int(val)
    resp = getattr(exc, "response", None)
    if resp is not None:
        val = getattr(resp, "status_code", None) or getattr(resp, "status", None)
        if isinstance(val, int) and not isinstance(val, bool) and 100 <= val <= 599:
            return val
    return None


def describe_exception(exc: BaseException) -> str:
    """Human-readable, never-empty description: 'Type[status=503]: message (cause: Type: msg)'."""
    def _one(e: BaseException) -> str:
        name = type(e).__name__
        code = _status_code_of(e)
        msg = str(e).strip() or "<empty message>"
        return f"{name}[status={code}]: {msg}" if code is not None else f"{name}: {msg}"

    chain = _exception_chain(exc, limit=3)
    text = _one(chain[0])
    if len(chain) > 1:
        text += " (cause: " + "; ".join(_one(c) for c in chain[1:]) + ")"
    return text[:600]

class GenAIClient(LLMClient):
    """Production LLM client utilizing the official google-genai SDK (client.aio.models).

    Features:
    - system_instruction passed in GenerateContentConfig (never as user part)
    - Pydantic response_model mapped to response_schema with application/json MIME type
    - ThinkingConfig for reasoning budget allocation
    - MIME sniffing for image parts
    - Jittered exponential retries on 429/5xx/timeouts
    - Per-stage timeout configuration
    - Concurrency Semaphore & RPM rate limiting
    - Optional UsageLedger integration
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        settings: Optional[Settings] = None,
        genai_client: Optional[Any] = None,
        ledger: Optional[UsageLedger] = None,
        guard: Optional[Any] = None,
        max_retries: Optional[int] = None,
        max_concurrency: Optional[int] = None,
        rpm_limit: Optional[int] = None,
        retry_delay_base: Optional[float] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.api_key = api_key or self.settings.gemini_api_key
        self.ledger = ledger
        self.guard = guard


        self.max_retries = max_retries if max_retries is not None else self.settings.llm_max_retries
        self.max_concurrency = max_concurrency if max_concurrency is not None else self.settings.llm_max_concurrency
        self.rpm_limit = rpm_limit if rpm_limit is not None else self.settings.llm_rpm_limit
        self.retry_delay_base = retry_delay_base if retry_delay_base is not None else 1.0

        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._rate_limiter = AsyncTokenBucket(self.rpm_limit)

        if genai_client is not None:
            self._client = genai_client
        else:
            from google import genai

            self._client = genai.Client(api_key=self.api_key)

    async def list_available_models(self) -> List[str]:
        """Lists available Gemini models for startup validation."""
        models: List[str] = []
        pager = await self._client.aio.models.list()
        if hasattr(pager, "__aiter__"):
            async for m in pager:
                name = getattr(m, "name", "")
                if name.startswith("models/"):
                    name = name[len("models/"):]
                models.append(name)
        elif hasattr(pager, "__iter__"):
            for m in pager:
                name = getattr(m, "name", "")
                if name.startswith("models/"):
                    name = name[len("models/"):]
                models.append(name)
        return models

    async def generate(self, request: LLMRequest) -> LLMResponse[Any]:
        """Executes a multimodal generation request with retries, timeout, and schema validation."""
        from google.genai import types

        # Determine effective timeout
        timeout_s = request.timeout_s
        if timeout_s is None:
            timeout_s = self.settings.get_stage_timeout(request.stage)

        # Build GenerateContentConfig
        config_kwargs: Dict[str, Any] = {}
        if request.system_instruction:
            config_kwargs["system_instruction"] = request.system_instruction
        if request.temperature is not None:
            config_kwargs["temperature"] = float(request.temperature)
        if request.max_output_tokens is not None:
            config_kwargs["max_output_tokens"] = int(request.max_output_tokens)
        if request.thinking_budget is not None:
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=int(request.thinking_budget)
            )

        if request.response_model is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = request.response_model

        config = types.GenerateContentConfig(**config_kwargs)

        # Convert parts to SDK Part representations
        sdk_contents: List[Any] = []
        for part in request.parts:
            if isinstance(part, TextPart):
                sdk_contents.append(types.Part.from_text(text=part.text))
            elif isinstance(part, ImagePart):
                sniffed_mime = part.get_sniffed_mime_type()
                sdk_contents.append(
                    types.Part.from_bytes(data=part.data, mime_type=sniffed_mime)
                )

        # Retry loop with jittered backoff
        attempts = 0
        max_attempts = max(1, self.max_retries)
        try:
            req_cap = int((request.metadata or {}).get("max_attempts") or 0)
        except (TypeError, ValueError):
            req_cap = 0
        if req_cap > 0:
            max_attempts = max(1, min(max_attempts, req_cap))
        last_error: Optional[Exception] = None
        base_delay = self.retry_delay_base

        req_fingerprint = request.compute_fingerprint()

        while attempts < max_attempts:
            # Check budget guard before initiating call attempt (WP-B1)
            eff_guard = self.guard
            if eff_guard is None and hasattr(self.ledger, "guard"):
                eff_guard = getattr(self.ledger, "guard", None)
            if eff_guard is not None and self.ledger is not None:
                est_usd = float(request.metadata.get("est_usd", 0.0)) if request.metadata else 0.0
                eff_guard.check_before_call(self.ledger, est_usd=est_usd, model=request.model)

            attempts += 1
            await self._rate_limiter.acquire()

            start_time = time.monotonic()

            try:
                eff_timeout_s = min(300.0, timeout_s * (1.0 + 0.25 * (attempts - 1)))

                async with self._semaphore:
                    raw_response = await asyncio.wait_for(
                        self._client.aio.models.generate_content(
                            model=request.model,
                            contents=sdk_contents,
                            config=config,
                        ),
                        timeout=eff_timeout_s,
                    )

                latency_ms = (time.monotonic() - start_time) * 1000.0

                # Extract token usage
                usage = UsageTokens()
                usage_meta = getattr(raw_response, "usage_metadata", None)
                if usage_meta is not None:
                    usage.input_tokens = getattr(usage_meta, "prompt_token_count", 0) or 0
                    usage.output_tokens = getattr(usage_meta, "candidates_token_count", 0) or 0
                    usage.thinking_tokens = getattr(usage_meta, "thoughts_token_count", 0) or 0
                    usage.total_tokens = getattr(usage_meta, "total_token_count", 0) or (
                        usage.input_tokens + usage.output_tokens + usage.thinking_tokens
                    )

                # Extract finish reason
                finish_reason = None
                if getattr(raw_response, "candidates", None):
                    cand = raw_response.candidates[0]
                    fr = getattr(cand, "finish_reason", None)
                    finish_reason = fr.value if hasattr(fr, "value") else str(fr) if fr else "STOP"

                # Extract raw response text (separating thoughts from output)
                raw_text = ""
                try:
                    raw_text = raw_response.text or ""
                except Exception:
                    pass

                if not raw_text and getattr(raw_response, "candidates", None):
                    c0 = raw_response.candidates[0]
                    if getattr(c0, "content", None) and getattr(c0.content, "parts", None):
                        non_thought_parts = [
                            p.text for p in c0.content.parts
                            if getattr(p, "text", None) and not getattr(p, "thought", False)
                        ]
                        if non_thought_parts:
                            raw_text = "".join(non_thought_parts)
                        else:
                            all_parts = [p.text for p in c0.content.parts if getattr(p, "text", None)]
                            raw_text = "".join(all_parts)

                # Structured response validation
                parsed = None
                if request.response_model is not None:
                    # Check if SDK already parsed it
                    sdk_parsed = getattr(raw_response, "parsed", None)
                    if sdk_parsed is not None and isinstance(sdk_parsed, request.response_model):
                        parsed = sdk_parsed
                    else:
                        # WP-UP4: an empty / whitespace-only body is a transient failure (dropped
                        # stream, output budget consumed by thinking, safety blank), not a model
                        # judgement. Always allow at least one backoff retry, even under a
                        # per-request cap of 1, and surface a distinct retryable error.
                        if not (raw_text or "").strip():
                            empty_cap = max(max_attempts, MIN_ATTEMPTS_ON_EMPTY)
                            if attempts < empty_cap:
                                max_attempts = empty_cap
                                sleep_s = min(
                                    60.0,
                                    base_delay * (2 ** (attempts - 1)) + random.uniform(0.0, 0.5 * base_delay),
                                )
                                logger.warning(
                                    "Attempt %d/%d returned an EMPTY response (finish_reason=%s); retrying in %.1fs",
                                    attempts, max_attempts, finish_reason, sleep_s,
                                )
                                await asyncio.sleep(sleep_s)
                                continue
                            raise create_stage_error(
                                request.stage,
                                f"Model returned an empty response after {attempts} attempts "
                                f"(finish_reason={finish_reason})",
                                reason=FailureReason.INVALID_RESPONSE,
                                retryable=True,
                                details={
                                    "raw_text": "",
                                    "empty_response": True,
                                    "finish_reason": finish_reason,
                                    "attempts": attempts,
                                    "model": request.model,
                                },
                            )
                        json_str = _extract_json_text(raw_text)
                        try:
                            json_obj = json.loads(json_str)
                        except json.JSONDecodeError as exc:
                            if attempts < max_attempts:
                                logger.warning(
                                    f"Attempt {attempts} returned invalid JSON: {exc}; retrying..."
                                )
                                sleep_s = min(
                                    60.0,
                                    base_delay * (2 ** (attempts - 1)) + random.uniform(0.0, 0.5 * base_delay),
                                )
                                await asyncio.sleep(sleep_s)
                                continue
                            raise create_stage_error(
                                request.stage,
                                f"Model response is not valid JSON: {exc} (after {attempts} attempts)",
                                reason=FailureReason.INVALID_RESPONSE,
                                details={"raw_text": raw_text[:500], "json_error": str(exc), "attempts": attempts},
                            ) from exc

                        try:
                            parsed = request.response_model.model_validate(json_obj)
                        except Exception as exc:
                            if attempts < max_attempts:
                                logger.warning(
                                    f"Attempt {attempts} failed schema validation: {exc}; retrying..."
                                )
                                sleep_s = min(
                                    60.0,
                                    base_delay * (2 ** (attempts - 1)) + random.uniform(0.0, 0.5 * base_delay),
                                )
                                await asyncio.sleep(sleep_s)
                                continue
                            raise create_stage_error(
                                request.stage,
                                f"Model response failed Pydantic schema validation: {exc}",
                                reason=FailureReason.SCHEMA_VALIDATION,
                                details={"raw_text": raw_text[:500], "validation_error": str(exc)},
                            ) from exc

                # Record usage in ledger if attached
                if self.ledger is not None:
                    self.ledger.record(
                        stage=request.stage,
                        model=request.model,
                        usage=usage,
                        latency_ms=latency_ms,
                        request_fingerprint=req_fingerprint,
                        attempts=attempts,
                        metadata=request.metadata,
                    )

                return LLMResponse(
                    parsed=parsed,
                    raw_text=raw_text,
                    usage=usage,
                    model=request.model,
                    latency_ms=latency_ms,
                    attempts=attempts,
                    finish_reason=finish_reason,
                    request_fingerprint=req_fingerprint,
                )

            except StageUnavailableError:
                # Schema validation or JSON decode failures are terminal (not transient retryable)
                raise

            except Exception as exc:
                last_error = exc
                is_retryable, reason = self._classify_error(exc)
                exc_desc = describe_exception(exc)

                if is_retryable and attempts < max_attempts:
                    retry_after = _extract_retry_after(exc)
                    if retry_after is not None:
                        sleep_s = min(max(retry_after, 0.5), 60.0)
                    else:
                        sleep_s = min(
                            60.0,
                            base_delay * (2 ** (attempts - 1)) + random.uniform(0.0, 0.5 * base_delay),
                        )
                    logger.warning(
                        "Attempt %d/%d transient %s (%s); retrying in %.1fs",
                        attempts, max_attempts, reason.value, exc_desc, sleep_s,
                    )
                    await asyncio.sleep(sleep_s)
                    continue

                # Final attempt failed or non-retryable error
                raise create_stage_error(
                    request.stage,
                    f"LLM generation failed ({reason.value}) after {attempts} attempts: {exc_desc}",
                    reason=reason,
                    retryable=is_retryable,
                    details={
                        "model": request.model,
                        "attempts": attempts,
                        "exception": exc_desc,
                        "exception_type": type(exc).__name__,
                        "status_code": _status_code_of(exc),
                    },
                ) from exc

        # Fallback if loop ends without response
        last_desc = describe_exception(last_error) if last_error is not None else "no response"
        raise create_stage_error(
            request.stage,
            f"LLM generation exhausted max attempts ({max_attempts}): {last_desc}",
            reason=FailureReason.MODEL_UNAVAILABLE,
            details={"attempts": attempts, "error": last_desc},
        )

    def _classify_error(self, exc: BaseException) -> tuple[bool, FailureReason]:
        """Determines if an exception is retryable and assigns the appropriate FailureReason.

        WP-UP3 policy:
        - Real client errors (HTTP 4xx other than 408/409/429) are terminal (non-retryable).
        - 429 -> RATE_LIMITED (retryable); 408/504/deadline/timeouts -> TIMEOUT (retryable).
        - 5xx, transport/protocol/connection failures (httpx/httpcore/anyio/aiohttp/OSError family),
          and *unknown exceptions with an empty message* -> retryable MODEL_UNAVAILABLE. Such
          exceptions previously fell through to a terminal branch and surfaced as
          "MODEL_UNAVAILABLE after 1 attempts: " with an empty message.
        The exception cause/context chain is inspected as well (SDKs often wrap transport errors).
        """
        chain = _exception_chain(exc)

        for e in chain:
            if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
                return True, FailureReason.TIMEOUT

        status_int = None
        for e in chain:
            status_int = _status_code_of(e)
            if status_int is not None:
                break
        exc_str = " ".join(str(e) for e in chain).lower()
        status_txt = " ".join(str(getattr(e, "status", "") or "") for e in chain).upper()

        # Immediate detection of schema / additionalProperties unsupported error
        if "additionalproperties" in exc_str or ("schema" in exc_str and "unsupported" in exc_str):
            return False, FailureReason.SCHEMA_UNSUPPORTED

        if status_int is not None:
            if status_int == 429:
                return True, FailureReason.RATE_LIMITED
            if status_int in (408, 504):
                return True, FailureReason.TIMEOUT
            if status_int in (401, 403):
                return False, FailureReason.NO_API_KEY
            if status_int == 409:
                return True, FailureReason.MODEL_UNAVAILABLE
            if 500 <= status_int <= 599:
                return True, FailureReason.MODEL_UNAVAILABLE
            if status_int == 400:
                if "schema" in exc_str:
                    return False, FailureReason.SCHEMA_UNSUPPORTED
                return False, FailureReason.INVALID_REQUEST
            if status_int == 404:
                return False, FailureReason.MODEL_UNAVAILABLE
            if 400 <= status_int <= 499:
                return False, FailureReason.INVALID_REQUEST

        if any(s in status_txt for s in ("UNAVAILABLE", "INTERNAL", "ABORTED")):
            return True, FailureReason.MODEL_UNAVAILABLE
        if "RESOURCE_EXHAUSTED" in status_txt:
            return True, FailureReason.RATE_LIMITED
        if "DEADLINE_EXCEEDED" in status_txt:
            return True, FailureReason.TIMEOUT

        # String matching for API errors and disconnects
        if any(term in exc_str for term in ("invalid_argument", "invalid request", "bad request")):
            return False, FailureReason.INVALID_REQUEST
        if any(term in exc_str for term in ("429", "resource_exhausted", "quota")):
            return True, FailureReason.RATE_LIMITED
        if any(term in exc_str for term in ("timeout", "timed out", "deadline")):
            return True, FailureReason.TIMEOUT
        if any(term in exc_str for term in (
            "connection", "disconnect", "reset by peer", "broken pipe", "incomplete",
            "unavailable", "overloaded", "internal error", "503", "502", "500",
        )):
            return True, FailureReason.MODEL_UNAVAILABLE

        # Transport-layer exception families (by MRO name, no hard import dependency)
        for e in chain:
            if isinstance(e, (ConnectionError, EOFError)):
                return True, FailureReason.MODEL_UNAVAILABLE
            mro_names = {c.__name__ for c in type(e).__mro__}
            mod_root = (type(e).__module__ or "").split(".")[0]
            if mro_names & _TRANSIENT_EXC_NAMES:
                return True, FailureReason.MODEL_UNAVAILABLE
            if mod_root in _TRANSPORT_MODULES:
                return True, FailureReason.MODEL_UNAVAILABLE
            if isinstance(e, OSError):
                return True, FailureReason.MODEL_UNAVAILABLE

        # Local programming errors are never transient
        if isinstance(exc, _PROGRAMMING_ERRORS):
            return False, FailureReason.UNKNOWN

        # Unknown exception (including ones with no message at all, which is how several transport
        # errors surface): treat as transient, bounded by max_retries with backoff.
        return True, FailureReason.MODEL_UNAVAILABLE


def create_llm_client(settings: Optional[Settings] = None, **kwargs: Any) -> Optional[LLMClient]:
    """Factory creating an LLMClient instance.

    Returns None if no API key is configured, allowing callers to handle the
    unconfigured onboarding state without crashing (INV-AC6, D5).
    """
    s = settings or get_settings()
    key = kwargs.pop("api_key", None) or s.gemini_api_key
    if not key or not str(key).strip():
        return None

    return GenAIClient(api_key=key, settings=s, **kwargs)


# Re-export replay and testing utilities
from recipeboxer.ai.replay import (
    CassetteFingerprintMismatchError,
    RecordingLLMClient,
    ReplayLLMClient,
    ScriptedLLMClient,
)

__all__ = [
    "LLMStage",
    "sniff_mime_type",
    "TextPart",
    "ImagePart",
    "UsageTokens",
    "LLMRequest",
    "LLMResponse",
    "LLMClient",
    "UsageRecord",
    "UsageLedger",
    "InMemoryUsageLedger",
    "AsyncTokenBucket",
    "GenAIClient",
    "create_llm_client",
    "create_stage_error",
    "ScriptedLLMClient",
    "RecordingLLMClient",
    "ReplayLLMClient",
    "CassetteFingerprintMismatchError",
]
