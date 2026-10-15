"""Feature-flagged Gemini text and multimodal embeddings interface (WP-G4).

Provides:
- `Embedder` protocol: asynchronous `embed_texts` and `embed_images`.
- `GenAIEmbedder`: production embedder backed by `google-genai` SDK (`client.aio.models.embed_content`).
  Supports batching, exponential retries with jitter, concurrency gating, RPM rate limiting,
  and usage recording into an optional `UsageLedger`.
- Documented provisional model ID: `text-embedding-004`.
- Image embedding note: As inspected in `google-genai` SDK 2.30.1, the Gemini embedding API
  (`client.aio.models.embed_content`) only supports text content. Multimodal image embedding
  is not supported by the Gemini embedding API endpoint. Accordingly, `embed_images` raises
  `ImageEmbeddingNotImplementedError` (a subclass of both `NotImplementedError` and
  `StageUnavailableError`, INV-AC4). Text embeddings for boundary text and ingredient vocabulary
  are the primary use.
- Cosine similarity and `top_k_by_similarity` vector search utilities (NumPy).
- `create_embedder(settings)` factory returning `None` when `embeddings_enabled` is False or
  no usable Gemini API key is present.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple, Union, runtime_checkable

import numpy as np

from recipeboxer.ai.client import (
    AsyncTokenBucket,
    UsageLedger,
    UsageTokens,
    _extract_retry_after,
)
from recipeboxer.config import get_settings, has_usable_gemini_key
from recipeboxer.models.failures import (
    FailureReason,
    PipelineStage,
    StageUnavailableError,
)

# Documented provisional model identifier for Gemini text embeddings
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"


@runtime_checkable
class Embedder(Protocol):
    """Protocol for embedding generation clients (WAVE4_CONTRACTS §1.7)."""

    async def embed_images(self, images: list[bytes]) -> list[list[float]]:
        """Embeds raw image bytes into dense vector representations."""
        ...

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embeds text strings into dense vector representations."""
        ...


class EmbeddingUnavailable(StageUnavailableError):
    """Raised when an embedding operation cannot produce a valid embedding vector.

    Subclasses StageUnavailableError to uphold INV-AC4 (no silent fabrication or silent degradation).
    """

    stage = PipelineStage.CORRELATION


class ImageEmbeddingNotImplementedError(NotImplementedError, EmbeddingUnavailable):
    """Raised when embed_images is invoked.

    In the Google GenAI SDK 2.30.1, client.aio.models.embed_content only supports text inputs.
    Multimodal image embeddings are not currently supported by the Gemini EmbedContent API endpoint.
    Subclasses both NotImplementedError and StageUnavailableError.
    """

    def __init__(
        self,
        message: str = (
            "Image embedding is not supported by the google-genai embed_content API "
            "(Gemini embedding models like text-embedding-004 are text-only). "
            "Use text embeddings of boundary text, titles, or ingredient vocabulary instead."
        ),
        *,
        reason: FailureReason = FailureReason.MODEL_UNAVAILABLE,
        card_ids: Optional[List[str]] = None,
        retryable: bool = False,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        StageUnavailableError.__init__(
            self,
            message,
            reason=reason,
            card_ids=card_ids,
            retryable=retryable,
            details=details
            or {
                "supported_modalities": ["text"],
                "sdk": "google-genai 2.30.1",
                "api_method": "client.aio.models.embed_content",
            },
        )


def classify_embedding_error(exc: Exception) -> Tuple[bool, FailureReason]:
    """Determines if an embedding API exception is transient/retryable and assigns FailureReason."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True, FailureReason.TIMEOUT

    status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if status_code is not None:
        try:
            status_int = int(status_code)
            if status_int == 429:
                return True, FailureReason.RATE_LIMITED
            if status_int in (401, 403):
                return False, FailureReason.NO_API_KEY
            if status_int in (500, 502, 503, 504):
                return True, FailureReason.MODEL_UNAVAILABLE
            if status_int == 400:
                return False, FailureReason.INVALID_RESPONSE
        except (ValueError, TypeError):
            pass

    exc_str = str(exc).lower()
    if any(term in exc_str for term in ("429", "resource_exhausted", "quota")):
        return True, FailureReason.RATE_LIMITED
    if any(term in exc_str for term in ("timeout", "timed out", "deadline")):
        return True, FailureReason.TIMEOUT
    if any(term in exc_str for term in ("connection", "disconnect", "reset by peer", "503", "502")):
        return True, FailureReason.MODEL_UNAVAILABLE

    return False, FailureReason.MODEL_UNAVAILABLE


class GenAIEmbedder:
    """Production embedder backed by google-genai SDK (client.aio.models.embed_content).

    Features:
    - Text batching (default 16 strings per request)
    - Jittered exponential retries on 429, 5xx, and timeouts (honors Retry-After)
    - Concurrency Semaphore and RPM TokenBucket rate limiting
    - Optional UsageLedger token and latency recording
    - Settings resolution via getattr with safe defaults
    """

    def __init__(
        self,
        settings: Optional[Any] = None,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        batch_size: Optional[int] = None,
        timeout_s: Optional[float] = None,
        max_retries: Optional[int] = None,
        max_concurrency: Optional[int] = None,
        rpm_limit: Optional[int] = None,
        retry_delay_base: Optional[float] = None,
        genai_client: Optional[Any] = None,
        ledger: Optional[UsageLedger] = None,
        task_type: Optional[str] = "SEMANTIC_SIMILARITY",
        output_dimensionality: Optional[int] = None,
    ) -> None:
        self.settings = settings or get_settings()

        # Read configuration via getattr with defaults
        self.api_key = api_key or getattr(self.settings, "gemini_api_key", None)
        self.model = model or getattr(self.settings, "embedding_model", DEFAULT_EMBEDDING_MODEL)
        self.batch_size = (
            batch_size
            if batch_size is not None
            else getattr(self.settings, "embedding_batch_size", 16)
        )
        self.timeout_s = (
            timeout_s
            if timeout_s is not None
            else getattr(self.settings, "embedding_timeout_s", 30.0)
        )
        self.max_retries = (
            max_retries
            if max_retries is not None
            else getattr(
                self.settings,
                "embedding_max_retries",
                getattr(self.settings, "llm_max_retries", 3),
            )
        )
        self.max_concurrency = (
            max_concurrency
            if max_concurrency is not None
            else getattr(
                self.settings,
                "embedding_max_concurrency",
                getattr(self.settings, "llm_max_concurrency", 4),
            )
        )
        self.rpm_limit = (
            rpm_limit
            if rpm_limit is not None
            else getattr(
                self.settings,
                "embedding_rpm_limit",
                getattr(self.settings, "llm_rpm_limit", 60),
            )
        )
        self.retry_delay_base = (
            retry_delay_base if retry_delay_base is not None else 1.0
        )
        self.ledger = ledger
        self.task_type = task_type
        self.output_dimensionality = output_dimensionality

        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._rate_limiter = AsyncTokenBucket(self.rpm_limit)

        if genai_client is not None:
            self._client = genai_client
        else:
            from google import genai

            self._client = genai.Client(api_key=self.api_key)

    async def embed_images(self, images: list[bytes]) -> list[list[float]]:
        """Embeds image bytes into vector representations.

        Raises:
            ImageEmbeddingNotImplementedError: Google GenAI 2.30.1 embed_content
                only supports text embeddings.
        """
        raise ImageEmbeddingNotImplementedError(
            details={
                "supported_modalities": ["text"],
                "sdk": "google-genai 2.30.1",
                "model": self.model,
                "images_count": len(images),
            }
        )

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embeds a list of texts into dense vectors with batching and retries."""
        if not texts:
            return []

        # Partition texts into batches of batch_size
        batch_size = max(1, self.batch_size)
        all_embeddings: List[List[float]] = []

        for i in range(0, len(texts), batch_size):
            chunk = texts[i : i + batch_size]
            chunk_embeddings = await self._embed_chunk_with_retries(chunk)
            all_embeddings.extend(chunk_embeddings)

        return all_embeddings

    async def _embed_chunk_with_retries(self, chunk: List[str]) -> List[List[float]]:
        """Executes a single batch embed request with jittered retries."""
        from google.genai import types

        config_kwargs: Dict[str, Any] = {}
        if self.task_type:
            config_kwargs["task_type"] = self.task_type
        if self.output_dimensionality is not None:
            config_kwargs["output_dimensionality"] = int(self.output_dimensionality)

        config = types.EmbedContentConfig(**config_kwargs) if config_kwargs else None

        attempts = 0
        max_attempts = max(1, self.max_retries)
        last_error: Optional[Exception] = None
        base_delay = self.retry_delay_base

        while attempts < max_attempts:
            attempts += 1
            await self._rate_limiter.acquire()
            start_time = time.monotonic()

            try:
                async with self._semaphore:
                    raw_response = await asyncio.wait_for(
                        self._client.aio.models.embed_content(
                            model=self.model,
                            contents=chunk,
                            config=config,
                        ),
                        timeout=self.timeout_s,
                    )

                latency_ms = (time.monotonic() - start_time) * 1000.0

                # Extract embedding vectors from response
                extracted = self._extract_embeddings(raw_response)
                if len(extracted) != len(chunk):
                    raise EmbeddingUnavailable(
                        f"Mismatched embedding count: expected {len(chunk)}, got {len(extracted)}",
                        reason=FailureReason.INVALID_RESPONSE,
                        retryable=False,
                        details={"expected": len(chunk), "received": len(extracted)},
                    )

                # Estimate or extract token usage for ledger
                input_tokens = 0
                usage_meta = getattr(raw_response, "usage_metadata", None)
                if usage_meta is not None:
                    input_tokens = getattr(usage_meta, "prompt_token_count", 0) or 0
                elif getattr(raw_response, "metadata", None) is not None:
                    billable_chars = getattr(
                        raw_response.metadata, "billable_character_count", None
                    )
                    if billable_chars is not None:
                        input_tokens = max(1, int(billable_chars) // 4)

                if input_tokens == 0:
                    total_chars = sum(len(s) for s in chunk)
                    input_tokens = max(1, total_chars // 4)

                usage = UsageTokens(
                    input_tokens=input_tokens,
                    output_tokens=0,
                    thinking_tokens=0,
                    total_tokens=input_tokens,
                )

                if self.ledger is not None:
                    self.ledger.record(
                        stage=getattr(PipelineStage, "CORRELATION", "CORRELATION"),
                        model=self.model,
                        usage=usage,
                        latency_ms=latency_ms,
                        attempts=attempts,
                        metadata={
                            "batch_size": len(chunk),
                            "chars": sum(len(s) for s in chunk),
                        },
                    )

                return extracted

            except (ImageEmbeddingNotImplementedError, StageUnavailableError):
                raise

            except Exception as exc:
                last_error = exc
                is_retryable, reason = classify_embedding_error(exc)

                if is_retryable and attempts < max_attempts:
                    retry_after = _extract_retry_after(exc)
                    if retry_after is not None:
                        sleep_s = min(max(retry_after, 0.1), 60.0)
                    else:
                        sleep_s = min(
                            60.0,
                            base_delay * (2 ** (attempts - 1))
                            + random.uniform(0.0, 0.5 * base_delay),
                        )
                    await asyncio.sleep(sleep_s)
                    continue

                raise EmbeddingUnavailable(
                    f"Embedding generation failed ({reason.value}) after {attempts} attempts: {exc}",
                    reason=reason,
                    retryable=is_retryable,
                    details={
                        "model": self.model,
                        "attempts": attempts,
                        "batch_size": len(chunk),
                        "exception": str(exc),
                    },
                ) from exc

        raise EmbeddingUnavailable(
            f"Embedding generation exhausted max attempts ({max_attempts}): {last_error}",
            reason=FailureReason.MODEL_UNAVAILABLE,
            details={"attempts": attempts, "error": str(last_error)},
        )

    def _extract_embeddings(self, response: Any) -> List[List[float]]:
        """Parses embeddings list from EmbedContentResponse or test mocks."""
        raw_list = getattr(response, "embeddings", None)
        if raw_list is None:
            single = getattr(response, "embedding", None)
            if single is not None:
                raw_list = [single]
            else:
                raw_list = []

        result: List[List[float]] = []
        for item in raw_list:
            if hasattr(item, "values"):
                result.append([float(x) for x in item.values])
            elif isinstance(item, dict) and "values" in item:
                result.append([float(x) for x in item["values"]])
            elif isinstance(item, (list, tuple)):
                result.append([float(x) for x in item])
            else:
                raise EmbeddingUnavailable(
                    f"Unexpected embedding item type: {type(item)}",
                    reason=FailureReason.INVALID_RESPONSE,
                    retryable=False,
                )
        return result


def create_embedder(
    settings: Optional[Any] = None,
    *,
    api_key: Optional[str] = None,
    genai_client: Optional[Any] = None,
    ledger: Optional[UsageLedger] = None,
    **kwargs: Any,
) -> Optional[Embedder]:
    """Factory creating an Embedder instance if enabled and key is available.

    Returns None if:
    - settings.embeddings_enabled is False (feature-flagged off by default), OR
    - no usable Gemini API key is configured (INV-AC6, D5).
    """
    eff_settings = settings or get_settings()

    # Feature flag check
    enabled = getattr(eff_settings, "embeddings_enabled", False)
    if not enabled:
        return None

    # Usable key check
    extra_keys = [api_key] if api_key else None
    if not has_usable_gemini_key(eff_settings, extra_keys=extra_keys):
        return None

    return GenAIEmbedder(
        settings=eff_settings,
        api_key=api_key,
        genai_client=genai_client,
        ledger=ledger,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Vector Search & Similarity Utilities (NumPy)
# ---------------------------------------------------------------------------


def cosine_similarity(
    a: Sequence[float] | np.ndarray,
    b: Sequence[float] | np.ndarray,
) -> float | np.ndarray:
    """Computes cosine similarity between two 1D vectors or between 1D/2D and 2D arrays.

    Returns:
        float if both a and b are 1D.
        1D ndarray if one is 1D and one is 2D.
        2D ndarray if both are 2D.
    """
    a_arr = np.asarray(a, dtype=np.float32)
    b_arr = np.asarray(b, dtype=np.float32)

    if a_arr.ndim == 1 and b_arr.ndim == 1:
        norm_a = float(np.linalg.norm(a_arr))
        norm_b = float(np.linalg.norm(b_arr))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        dot = float(np.dot(a_arr, b_arr))
        sim = dot / (norm_a * norm_b)
        return float(np.clip(sim, -1.0, 1.0))

    elif a_arr.ndim == 1 and b_arr.ndim == 2:
        norm_a = float(np.linalg.norm(a_arr))
        norm_b = np.linalg.norm(b_arr, axis=1)
        denom = norm_a * norm_b
        zero_mask = denom == 0.0
        denom[zero_mask] = 1.0
        sim = np.dot(b_arr, a_arr) / denom
        sim[zero_mask] = 0.0
        return np.clip(sim, -1.0, 1.0)

    elif a_arr.ndim == 2 and b_arr.ndim == 1:
        return cosine_similarity(b_arr, a_arr)

    elif a_arr.ndim == 2 and b_arr.ndim == 2:
        norm_a = np.linalg.norm(a_arr, axis=1, keepdims=True)
        norm_b = np.linalg.norm(b_arr, axis=1, keepdims=True)
        denom = np.dot(norm_a, norm_b.T)
        zero_mask = denom == 0.0
        denom[zero_mask] = 1.0
        sim = np.dot(a_arr, b_arr.T) / denom
        sim[zero_mask] = 0.0
        return np.clip(sim, -1.0, 1.0)

    else:
        raise ValueError(f"Unsupported array dimensions: a={a_arr.ndim}, b={b_arr.ndim}")


def top_k_by_similarity(
    *args: Any,
    query: Optional[Sequence[float] | np.ndarray | int] = None,
    exclude_self: bool = True,
    return_scores: bool = True,
    **kwargs: Any,
) -> Any:
    """Finds the top-k most similar vectors.

    Supports three calling conventions:
    1. top_k_by_similarity(vectors, k):
       All-pairs mode: For each vector in `vectors`, returns top-k neighbors
       (excluding itself by default). Returns List[List[Tuple[int, float]]] (or List[List[int]]).
    2. top_k_by_similarity(query, candidates, k):
       Query mode: Compares query vector against candidates and returns top-k
       items. Returns List[Tuple[int, float]] (or List[int]).
    3. top_k_by_similarity(vectors, k, query=...):
       Query mode with keyword query (either vector or integer index into vectors).

    Args:
        *args: (vectors, k) or (query, candidates, k)
        query: Optional query vector or integer index into candidates.
        exclude_self: Whether to exclude the vector itself from candidates.
        return_scores: If True (default), returns (index, score) tuples; if False,
            returns indices only.
    """
    if len(args) == 3:
        query_arg, candidates, k_val = args
    elif len(args) == 2:
        candidates, k_val = args
        query_arg = query
    elif len(args) == 1 and "k" in kwargs:
        candidates = args[0]
        k_val = kwargs["k"]
        query_arg = query
    else:
        raise ValueError("Invalid arguments: use (vectors, k) or (query, candidates, k)")

    k = max(0, int(k_val))
    cand_arr = np.asarray(candidates, dtype=np.float32)

    if cand_arr.ndim != 2 or len(cand_arr) == 0 or k == 0:
        if query_arg is not None:
            return []
        return [[] for _ in range(len(cand_arr))]

    # Single-query search
    if query_arg is not None:
        exclude_idx = None
        if isinstance(query_arg, int):
            exclude_idx = query_arg if exclude_self else None
            q_vec = cand_arr[query_arg]
        else:
            q_vec = np.asarray(query_arg, dtype=np.float32)

        sims = np.asarray(cosine_similarity(q_vec, cand_arr), dtype=np.float32)
        if exclude_idx is not None and 0 <= exclude_idx < len(sims):
            sims[exclude_idx] = -np.inf

        order = np.argsort(-sims)
        eff_k = min(k, len(cand_arr) - (1 if exclude_idx is not None else 0))
        results: List[Any] = []
        for idx in order[:eff_k]:
            if sims[idx] != -np.inf:
                results.append((int(idx), float(sims[idx])) if return_scores else int(idx))
        return results

    # All-pairs search
    sim_matrix = np.asarray(cosine_similarity(cand_arr, cand_arr), dtype=np.float32)
    all_results: List[Any] = []
    n = len(cand_arr)
    for i in range(n):
        row = np.copy(sim_matrix[i])
        if exclude_self:
            row[i] = -np.inf
        order = np.argsort(-row)
        eff_k = min(k, n - (1 if exclude_self else 0))
        row_res: List[Any] = []
        for j in order[:eff_k]:
            if row[j] != -np.inf:
                row_res.append((int(j), float(row[j])) if return_scores else int(j))
        all_results.append(row_res)
    return all_results


__all__ = [
    "DEFAULT_EMBEDDING_MODEL",
    "Embedder",
    "EmbeddingUnavailable",
    "ImageEmbeddingNotImplementedError",
    "GenAIEmbedder",
    "classify_embedding_error",
    "create_embedder",
    "cosine_similarity",
    "top_k_by_similarity",
]
