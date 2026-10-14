# WP-X1: Unified Hardened LLM Client

## 1. Overview & Objectives

**Work Package WP-X1** delivers a unified, production-hardened LLM interface for RecipeBoxer based on the official `google-genai` SDK (version `2.30.1`, pinned to `>=2.30,<3`).

It replaces fragmented `httpx` and raw model calls across visual fingerprinting, correlation/set-verification, and recipe extraction with a typed, resilient client adhering to all architectural and anti-cheat guardrails (**INV-AC1** through **INV-AC6**):
- **Native Configuration**: `system_instruction` is placed directly in `GenerateContentConfig` (never appended as a user part).
- **Native Structured Output**: Pydantic models map to `response_schema` with `application/json` MIME type, validated on response.
- **Reasoning Support**: Native `ThinkingConfig(thinking_budget=...)` for handwriting and hard card transcripts.
- **MIME Sniffing**: Magic bytes inspect image headers (`PNG`, `JPEG`, `WebP`, `GIF`, `TIFF`, `BMP`) rather than hardcoding MIME types or relying on filenames.
- **Resilience & Rate Limiting**: Exponential backoff with jitter on 429, 500, 502, 503, 504, and timeouts; honors `Retry-After` headers. Concurrency is guarded by an `asyncio.Semaphore` and an async token-bucket RPM rate limiter.
- **Stage-Aware Failures**: Exceptions subclass `StageUnavailableError` with typed `FailureReason` enums (`RATE_LIMITED`, `TIMEOUT`, `INVALID_RESPONSE`, `SCHEMA_VALIDATION`, `MODEL_UNAVAILABLE`).
- **Spend & Cost Tracking**: `UsageLedger` protocol with an `InMemoryUsageLedger` implementation recording input, output, and thinking tokens along with latency.
- **100% Offline Testability**: `ScriptedLLMClient`, `RecordingLLMClient`, and `ReplayLLMClient` with deterministic SHA-256 request fingerprinting and opaque cassette storage.

---

## 2. Architecture & Core Types

### 2.1 Request & Response Data Models

```python
class LLMStage(str, Enum):
    FINGERPRINT = "FINGERPRINT"
    SET_VERIFY = "SET_VERIFY"
    TRANSCRIBE = "TRANSCRIBE"
    STRUCTURE = "STRUCTURE"
    GROUNDING = "GROUNDING"
    REPAIR = "REPAIR"
    JUDGE = "JUDGE"
    MODALITY = "MODALITY"

class TextPart(BaseModel):
    text: str
    label: Optional[str] = None

class ImagePart(BaseModel):
    data: bytes  # Accessed as .bytes or .data
    mime_type: Optional[str] = None
    label: Optional[str] = None

class LLMRequest(BaseModel):
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

    def compute_fingerprint(self) -> str:
        """Deterministic sha256 of stage, model, instruction, text parts, and image byte hashes."""
        ...

class UsageTokens(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    total_tokens: int = 0

class LLMResponse(BaseModel, Generic[T]):
    parsed: Optional[T] = None
    raw_text: str = ""
    usage: UsageTokens
    model: str
    latency_ms: float = 0.0
    attempts: int = 1
    finish_reason: Optional[str] = None
    request_fingerprint: str = ""
```

---

## 3. Usage Guide for Stage Authors

Downstream work packages (WP-G1, WP-G6, WP-X4, WP-X5, WP-X6) should instantiate or receive `LLMClient` and call `await client.generate(request)`.

### 3.1 Visual Fingerprinting (WP-G1)
```python
from recipeboxer.ai import LLMRequest, LLMStage, ImagePart
from recipeboxer.config import get_settings

settings = get_settings()
request = LLMRequest(
    stage=LLMStage.FINGERPRINT,
    model=settings.get_stage_model(LLMStage.FINGERPRINT),
    system_instruction="Analyze card visual properties: title, line boundaries, ruling, and ink.",
    parts=[ImagePart(data=card_image_bytes)],
    response_model=CardFingerprintSchema,
    temperature=0.0,
)
response = await client.generate(request)
fingerprint: CardFingerprintSchema = response.parsed
```

### 3.2 Set-Level Verification (WP-G6)
```python
from recipeboxer.ai import LLMRequest, LLMStage, ImagePart, TextPart

request = LLMRequest(
    stage=LLMStage.SET_VERIFY,
    model=settings.get_stage_model(LLMStage.SET_VERIFY),
    system_instruction="Verify whether the candidate card faces continue the anchor recipe. Cite visible text.",
    parts=[
        TextPart(text="Anchor Card:"),
        ImagePart(data=anchor_bytes, label="anchor"),
        TextPart(text="Candidate Card 1:"),
        ImagePart(data=candidate_bytes, label="candidate_1"),
    ],
    response_model=SetVerificationResult,
    temperature=0.1,
)
response = await client.generate(request)
verification: SetVerificationResult = response.parsed
```

### 3.3 Verbatim Transcription with Thinking (WP-X4)
```python
from recipeboxer.ai import LLMRequest, LLMStage, ImagePart

request = LLMRequest(
    stage=LLMStage.TRANSCRIBE,
    model=settings.get_stage_model(LLMStage.TRANSCRIBE),
    system_instruction="Transcribe the recipe card verbatim. Retain original line breaks and note [illegible: reason] if faded or torn.",
    parts=[ImagePart(data=faded_card_bytes)],
    thinking_budget=1024,  # Allocated dynamically for difficult cursive cards
    temperature=0.2,
)
response = await client.generate(request)
transcript_text: str = response.raw_text
```

### 3.4 Structuring Pass (WP-X5)
```python
from recipeboxer.ai import LLMRequest, LLMStage, TextPart

request = LLMRequest(
    stage=LLMStage.STRUCTURE,
    model=settings.get_stage_model(LLMStage.STRUCTURE),
    system_instruction="Structure the transcribed text into Recipe Schema v2. Never invent missing quantities.",
    parts=[TextPart(text=f"Transcript:\n{transcript_text}")],
    response_model=RecipeSchemaV2,
    temperature=0.0,
)
response = await client.generate(request)
structured_recipe: RecipeSchemaV2 = response.parsed
```

---

## 4. Offline Testing Architecture

To comply with **INV-AC1** and **INV-AC2**, test simulators must **never** key by filenames, corpus paths, or answer keys.

1. **`ScriptedLLMClient`**:
   - Returns mock responses or exceptions in strict **call order**.
   - Accepts Pydantic models, dicts, raw strings, or typed exceptions.
   - Records all received requests in `.requests`.
2. **`RecordingLLMClient`**:
   - Wraps any active client and writes JSONL cassette entries.
   - Images are serialized strictly as `{"type": "image", "sha256": "<hash>", "mime_type": "..."}`.
   - Raw image bytes are **never** dumped to cassettes, preventing repo bloat and leakage.
3. **`ReplayLLMClient`**:
   - Replays recorded interactions strictly in call order.
   - Computes the incoming request fingerprint (`sha256` of stage, model, instruction, text parts, and image byte hashes) and asserts byte-level equivalence against the cassette.
   - Mismatches raise `CassetteFingerprintMismatchError`.

---

## 5. Test Suite & Verification Results

All 20 tests in `tests/test_wp_x1_client.py` execute offline without API keys or live network calls:

```
tests/test_wp_x1_client.py::test_system_instruction_placed_in_config PASSED [  5%]
tests/test_wp_x1_client.py::test_response_schema_set_from_pydantic_model PASSED [ 10%]
tests/test_wp_x1_client.py::test_thinking_config_set_when_budget_provided PASSED [ 15%]
tests/test_wp_x1_client.py::test_mime_sniffing_magic_bytes PASSED        [ 20%]
tests/test_wp_x1_client.py::test_image_part_mime_sniffing_in_generate PASSED [ 25%]
tests/test_wp_x1_client.py::test_retries_on_429_503_then_success PASSED  [ 30%]
tests/test_wp_x1_client.py::test_gives_up_after_max_attempts_with_rate_limited PASSED [ 35%]
tests/test_wp_x1_client.py::test_timeout_mapping_and_exhaustion PASSED   [ 40%]
tests/test_wp_x1_client.py::test_invalid_json_raises_invalid_response PASSED [ 45%]
tests/test_wp_x1_client.py::test_schema_validation_failure_raises_schema_validation PASSED [ 50%]
tests/test_wp_x1_client.py::test_stage_aware_exception_mapping PASSED    [ 55%]
tests/test_wp_x1_client.py::test_in_memory_usage_ledger PASSED           [ 60%]
tests/test_wp_x1_client.py::test_scripted_llm_client_call_order PASSED   [ 65%]
tests/test_wp_x1_client.py::test_recording_and_replay_roundtrip_with_cassette PASSED [ 70%]
tests/test_wp_x1_client.py::test_replay_fingerprint_mismatch_detection PASSED [ 75%]
tests/test_wp_x1_client.py::test_create_llm_client_returns_none_without_key PASSED [ 80%]
tests/test_wp_x1_client.py::test_create_llm_client_returns_client_with_key PASSED [ 85%]
tests/test_wp_x1_client.py::test_list_available_models_strips_prefix PASSED [ 90%]
tests/test_wp_x1_client.py::test_token_bucket_rate_limiter PASSED        [ 95%]
tests/test_wp_x1_client.py::test_settings_quality_profiles_and_stage_models PASSED [100%]
============================== 20 passed in 3.25s ==============================
```

---

## 6. SDK Pin Rationale (`google-genai>=2.30,<3`)

`google-genai` is pinned to `>=2.30,<3` in `requirements.txt` and `pyproject.toml`:
1. **Python 3.13 Compatibility**: The `2.30.x` series supports Python 3.13 without C-extension incompatibilities.
2. **Native Async & Continuation Loops**: Includes `client.aio.models.generate_content` and automatic continuation token processing.
3. **Structured Outputs**: Direct Pydantic model passing via `response_schema` and automatic parsing to `response.parsed`.
4. **ThinkingConfig**: Native support for `thinking_budget` in `types.ThinkingConfig`.
5. **API Stability**: Minor version changes in `google-genai` previously altered configuration signatures; constraining `<3` avoids major breaking changes while allowing patch/minor bug fixes.

---

## 7. PyInstaller Packaging Request (`recipeboxer.spec`)

> [!IMPORTANT]
> **Request to Orchestrator / Packaging Agent**:
> The `google-genai` SDK uses internal submodule discovery. Please update `recipeboxer.spec` hidden imports:
> ```python
> hidden_imports.extend([
>     "google.genai",
>     "google.genai._api_client",
>     "google.genai._common",
>     "google.genai._extra_utils",
>     "google.genai._transformers",
>     "google.genai.models",
>     "google.genai.types",
>     "google.genai.errors",
>     "google.genai.pagers",
>     "google.auth",
>     "google.auth.transport.requests",
> ])
> ```
> Or include `collect_submodules("google.genai")` in the dynamic collection loop.
