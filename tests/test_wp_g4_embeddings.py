"""Offline unit and integration tests for WP-G4 (Embeddings, feature-flagged).

Verifies:
- Embedder protocol compliance
- Factory create_embedder behavior (feature flag off, no key, active)
- GenAIEmbedder initialization and settings resolution via getattr with defaults
- embed_texts execution, batching across chunk boundaries, and result order preservation
- embed_images raising ImageEmbeddingNotImplementedError (subclass of both NotImplementedError and StageUnavailableError)
- Error classification and jittered retries on 429/503/timeout
- Immediate failure on non-retryable 400 errors
- Mismatched embedding count validation
- Optional UsageLedger token and latency recording
- Cosine similarity: 1D-1D, 1D-2D, 2D-2D, zero-vector handling, and numerical clipping
- top_k_by_similarity: single query vector, query by index with exclude_self, all-pairs top-k, and return_scores toggle
"""

from __future__ import annotations

import asyncio
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from recipeboxer.ai.client import InMemoryUsageLedger
from recipeboxer.ai.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    Embedder,
    EmbeddingUnavailable,
    GenAIEmbedder,
    ImageEmbeddingNotImplementedError,
    classify_embedding_error,
    cosine_similarity,
    create_embedder,
    top_k_by_similarity,
)
from recipeboxer.config import Settings
from recipeboxer.models.failures import FailureReason, StageUnavailableError


def _make_mock_embed_response(vectors: List[List[float]], *, prompt_tokens: int = 10) -> MagicMock:
    """Builds a stubbed EmbedContentResponse matching google-genai 2.30.1 types."""
    resp = MagicMock()
    embeddings = []
    for vec in vectors:
        emb = MagicMock()
        emb.values = vec
        embeddings.append(emb)
    resp.embeddings = embeddings

    usage_meta = MagicMock()
    usage_meta.prompt_token_count = prompt_tokens
    resp.usage_metadata = usage_meta
    return resp


def _make_mock_client(responses: List[Any]) -> MagicMock:
    """Builds a stubbed SDK client returning responses in call order."""
    mock_client = MagicMock()
    aio_models = MagicMock()

    call_index = 0

    async def _mock_embed_content(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_index
        if call_index >= len(responses):
            raise RuntimeError(f"Unexpected embed_content call #{call_index + 1}")
        item = responses[call_index]
        call_index += 1
        if isinstance(item, Exception):
            raise item
        return item

    aio_models.embed_content = AsyncMock(side_effect=_mock_embed_content)
    mock_client.aio.models = aio_models
    return mock_client


class MockAPIError(Exception):
    """Simulated Google GenAI HTTP error."""

    def __init__(self, message: str, code: int, headers: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = code
        if headers:
            resp = MagicMock()
            resp.headers = headers
            self.response = resp


# ---------------------------------------------------------------------------
# Factory & Configuration Tests
# ---------------------------------------------------------------------------


def test_embedder_protocol_conformance() -> None:
    """GenAIEmbedder conforms to the Embedder protocol."""
    embedder = GenAIEmbedder(
        api_key="AIzaSyTestKey12345",
        genai_client=MagicMock(),
    )
    assert isinstance(embedder, Embedder)
    assert hasattr(embedder, "embed_texts")
    assert hasattr(embedder, "embed_images")


def test_create_embedder_flag_off_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_embedder returns None when embeddings_enabled is False (default)."""
    monkeypatch.delenv("RECIPEBOXER_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    settings = Settings(gemini_api_key="AIzaSyValidKey123")
    # embeddings_enabled is not set on standard Settings, getattr returns False
    embedder = create_embedder(settings)
    assert embedder is None


from types import SimpleNamespace

def test_create_embedder_no_usable_key_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_embedder returns None when flag is True but key is missing or mock."""
    monkeypatch.delenv("RECIPEBOXER_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    # Settings object with embeddings_enabled=True but empty key
    settings = SimpleNamespace(embeddings_enabled=True, gemini_api_key=None)

    embedder = create_embedder(settings)
    assert embedder is None

    # Key starts with 'mock' (considered unusable per is_usable_gemini_key)
    settings.gemini_api_key = "mock-key-12345"
    embedder2 = create_embedder(settings)
    assert embedder2 is None


def test_create_embedder_with_flag_and_usable_key() -> None:
    """create_embedder returns a GenAIEmbedder when flag is on and key is valid."""
    settings = SimpleNamespace(
        embeddings_enabled=True,
        gemini_api_key="AIzaSyValidProductionKey",
        embedding_model=DEFAULT_EMBEDDING_MODEL,
    )

    mock_client = MagicMock()
    embedder = create_embedder(settings, genai_client=mock_client)
    assert embedder is not None
    assert isinstance(embedder, GenAIEmbedder)
    assert embedder.model == DEFAULT_EMBEDDING_MODEL


def test_settings_getattr_resolution_and_defaults() -> None:
    """GenAIEmbedder resolves configuration attributes with robust defaults."""
    settings = Settings(gemini_api_key="AIzaSyTestKey123")
    embedder = GenAIEmbedder(settings=settings, genai_client=MagicMock())

    assert embedder.model == DEFAULT_EMBEDDING_MODEL
    assert embedder.batch_size == 16
    assert embedder.timeout_s == 30.0
    assert embedder.max_retries == settings.llm_max_retries
    assert embedder.max_concurrency == settings.llm_max_concurrency
    assert embedder.rpm_limit == settings.llm_rpm_limit


# ---------------------------------------------------------------------------
# embed_texts & Batching Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_texts_empty_list() -> None:
    """Calling embed_texts with an empty list immediately returns empty list without API calls."""
    mock_client = MagicMock()
    embedder = GenAIEmbedder(api_key="AIzaSyTestKey", genai_client=mock_client)

    result = await asyncio.wait_for(embedder.embed_texts([]), timeout=2.0)
    assert result == []
    mock_client.aio.models.embed_content.assert_not_called()


@pytest.mark.asyncio
async def test_embed_texts_single_batch_success() -> None:
    """embed_texts successfully calls embed_content and unpacks vector values."""
    mock_resp = _make_mock_embed_response([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
    mock_client = _make_mock_client([mock_resp])

    embedder = GenAIEmbedder(api_key="AIzaSyTestKey", genai_client=mock_client)
    texts = ["1 cup sugar", "2 tsp cinnamon"]

    result = await asyncio.wait_for(embedder.embed_texts(texts), timeout=2.0)
    assert len(result) == 2
    assert result[0] == [0.1, 0.2, 0.3]
    assert result[1] == [0.4, 0.5, 0.6]

    # Verify embed_content arguments
    mock_client.aio.models.embed_content.assert_called_once()
    _, kwargs = mock_client.aio.models.embed_content.call_args
    assert kwargs["model"] == DEFAULT_EMBEDDING_MODEL
    assert kwargs["contents"] == texts


@pytest.mark.asyncio
async def test_embed_texts_multi_batch_partitioning() -> None:
    """embed_texts partitions input into chunks of batch_size and concatenates in order."""
    batch_1 = _make_mock_embed_response([[1.0, 0.0], [0.9, 0.1]])
    batch_2 = _make_mock_embed_response([[0.8, 0.2], [0.7, 0.3]])
    batch_3 = _make_mock_embed_response([[0.6, 0.4]])
    mock_client = _make_mock_client([batch_1, batch_2, batch_3])

    embedder = GenAIEmbedder(
        api_key="AIzaSyTestKey",
        genai_client=mock_client,
        batch_size=2,
    )

    texts = ["Item 1", "Item 2", "Item 3", "Item 4", "Item 5"]
    result = await asyncio.wait_for(embedder.embed_texts(texts), timeout=5.0)

    assert len(result) == 5
    assert result[0] == [1.0, 0.0]
    assert result[1] == [0.9, 0.1]
    assert result[2] == [0.8, 0.2]
    assert result[3] == [0.7, 0.3]
    assert result[4] == [0.6, 0.4]
    assert mock_client.aio.models.embed_content.call_count == 3


# ---------------------------------------------------------------------------
# embed_images Failure Contract Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_images_raises_not_implemented_stage_unavailable() -> None:
    """embed_images raises ImageEmbeddingNotImplementedError (subclassing NotImplementedError and StageUnavailableError)."""
    embedder = GenAIEmbedder(api_key="AIzaSyTestKey", genai_client=MagicMock())
    sample_image_bytes = [b"\x89PNG\r\n\x1a\nfakeimagebytes"]

    with pytest.raises(ImageEmbeddingNotImplementedError) as exc_info:
        await asyncio.wait_for(embedder.embed_images(sample_image_bytes), timeout=2.0)

    exc = exc_info.value
    # Assert multiple inheritance contract
    assert isinstance(exc, NotImplementedError)
    assert isinstance(exc, StageUnavailableError)
    assert isinstance(exc, EmbeddingUnavailable)

    # Assert typed failure attributes
    assert exc.reason == FailureReason.MODEL_UNAVAILABLE
    assert exc.retryable is False
    assert "text" in exc.details.get("supported_modalities", [])
    assert "google-genai 2.30.1" in exc.details.get("sdk", "")


# ---------------------------------------------------------------------------
# Resilience, Retries, and Error Classification Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_texts_retries_on_429_then_succeeds() -> None:
    """Transient 429 rate limit error triggers retry and succeeds on subsequent attempt."""
    rate_limit_err = MockAPIError("Resource exhausted: quota exceeded", code=429, headers={"retry-after": "0.01"})
    success_resp = _make_mock_embed_response([[0.5, 0.5]])
    mock_client = _make_mock_client([rate_limit_err, success_resp])

    embedder = GenAIEmbedder(
        api_key="AIzaSyTestKey",
        genai_client=mock_client,
        retry_delay_base=0.01,
        max_retries=3,
    )

    result = await asyncio.wait_for(embedder.embed_texts(["test line"]), timeout=5.0)
    assert result == [[0.5, 0.5]]
    assert mock_client.aio.models.embed_content.call_count == 2


@pytest.mark.asyncio
async def test_embed_texts_exhausts_retries_raises_embedding_unavailable() -> None:
    """Repeated 503 errors exhaust max_retries and raise EmbeddingUnavailable with MODEL_UNAVAILABLE."""
    err_503 = MockAPIError("Backend server overloaded", code=503)
    mock_client = _make_mock_client([err_503, err_503, err_503])

    embedder = GenAIEmbedder(
        api_key="AIzaSyTestKey",
        genai_client=mock_client,
        retry_delay_base=0.01,
        max_retries=2,
    )

    with pytest.raises(EmbeddingUnavailable) as exc_info:
        await asyncio.wait_for(embedder.embed_texts(["sample text"]), timeout=5.0)

    exc = exc_info.value
    assert isinstance(exc, StageUnavailableError)
    assert exc.reason == FailureReason.MODEL_UNAVAILABLE
    assert exc.retryable is True
    assert exc.details["attempts"] == 2


@pytest.mark.asyncio
async def test_embed_texts_terminal_400_fails_immediately() -> None:
    """Non-retryable 400 bad request fails immediately on attempt 1 without retrying."""
    bad_request_err = MockAPIError("Invalid argument", code=400)
    mock_client = _make_mock_client([bad_request_err])

    embedder = GenAIEmbedder(
        api_key="AIzaSyTestKey",
        genai_client=mock_client,
        max_retries=3,
    )

    with pytest.raises(EmbeddingUnavailable) as exc_info:
        await asyncio.wait_for(embedder.embed_texts(["bad query"]), timeout=2.0)

    exc = exc_info.value
    assert exc.reason == FailureReason.INVALID_RESPONSE
    assert exc.retryable is False
    assert mock_client.aio.models.embed_content.call_count == 1


@pytest.mark.asyncio
async def test_embed_texts_mismatched_count_raises_invalid_response() -> None:
    """Returning fewer embeddings than requested inputs raises EmbeddingUnavailable."""
    # Sent 2 texts, but response only contains 1 embedding
    mock_resp = _make_mock_embed_response([[0.1, 0.2]])
    mock_client = _make_mock_client([mock_resp])

    embedder = GenAIEmbedder(api_key="AIzaSyTestKey", genai_client=mock_client)

    with pytest.raises(EmbeddingUnavailable) as exc_info:
        await asyncio.wait_for(embedder.embed_texts(["text1", "text2"]), timeout=2.0)

    exc = exc_info.value
    assert exc.reason == FailureReason.INVALID_RESPONSE
    assert exc.retryable is False


# ---------------------------------------------------------------------------
# UsageLedger Integration Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_usage_ledger_recording() -> None:
    """GenAIEmbedder records token counts, latency, and model id into UsageLedger."""
    ledger = InMemoryUsageLedger()
    mock_resp = _make_mock_embed_response([[0.1, 0.2], [0.3, 0.4]], prompt_tokens=42)
    mock_client = _make_mock_client([mock_resp])

    embedder = GenAIEmbedder(
        api_key="AIzaSyTestKey",
        genai_client=mock_client,
        ledger=ledger,
    )

    await asyncio.wait_for(embedder.embed_texts(["flour", "sugar"]), timeout=2.0)

    records = ledger.get_records()
    assert len(records) == 1
    rec = records[0]
    assert rec.model == DEFAULT_EMBEDDING_MODEL
    assert rec.usage.input_tokens == 42
    assert rec.usage.output_tokens == 0
    assert rec.latency_ms >= 0.0
    assert rec.metadata.get("batch_size") == 2


# ---------------------------------------------------------------------------
# Vector Search & Similarity Utilities Tests
# ---------------------------------------------------------------------------


def test_cosine_similarity_1d() -> None:
    """cosine_similarity correctly calculates 1D dot-product cosine distances."""
    v1 = [1.0, 0.0, 0.0]
    v2 = [0.0, 1.0, 0.0]
    v3 = [-1.0, 0.0, 0.0]
    v_zero = [0.0, 0.0, 0.0]

    # Identical
    assert pytest.approx(cosine_similarity(v1, v1), abs=1e-5) == 1.0
    # Orthogonal
    assert pytest.approx(cosine_similarity(v1, v2), abs=1e-5) == 0.0
    # Opposite
    assert pytest.approx(cosine_similarity(v1, v3), abs=1e-5) == -1.0
    # Zero vector returns 0.0 without divide by zero warning
    assert cosine_similarity(v1, v_zero) == 0.0


def test_cosine_similarity_matrix() -> None:
    """cosine_similarity handles 1D-vs-2D and 2D-vs-2D arrays."""
    q = [1.0, 0.0]
    candidates = [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]]

    sims_1d_2d = cosine_similarity(q, candidates)
    assert isinstance(sims_1d_2d, np.ndarray)
    assert sims_1d_2d.shape == (3,)
    assert pytest.approx(sims_1d_2d[0], abs=1e-5) == 1.0
    assert pytest.approx(sims_1d_2d[1], abs=1e-5) == 0.0
    assert pytest.approx(sims_1d_2d[2], abs=1e-5) == -1.0

    # 2D-vs-2D pairwise matrix
    sims_2d_2d = cosine_similarity(candidates, candidates)
    assert sims_2d_2d.shape == (3, 3)
    assert pytest.approx(sims_2d_2d[0, 0], abs=1e-5) == 1.0
    assert pytest.approx(sims_2d_2d[1, 1], abs=1e-5) == 1.0
    assert pytest.approx(sims_2d_2d[2, 2], abs=1e-5) == 1.0


def test_top_k_by_similarity_query_mode() -> None:
    """top_k_by_similarity with query vector returns sorted top-k (index, score) pairs."""
    candidates = [
        [1.0, 0.0, 0.0],   # idx 0: sim 1.0
        [0.0, 1.0, 0.0],   # idx 1: sim 0.0
        [0.8, 0.2, 0.0],   # idx 2: sim ~0.97
        [-1.0, 0.0, 0.0],  # idx 3: sim -1.0
    ]
    query = [1.0, 0.0, 0.0]

    # Top-2 candidates
    top2 = top_k_by_similarity(query, candidates, 2)
    assert len(top2) == 2
    assert top2[0][0] == 0
    assert pytest.approx(top2[0][1], abs=1e-3) == 1.0
    assert top2[1][0] == 2
    assert top2[1][1] > 0.9

    # Return indices only
    top2_idx_only = top_k_by_similarity(query, candidates, 2, return_scores=False)
    assert top2_idx_only == [0, 2]


def test_top_k_by_similarity_all_pairs_mode() -> None:
    """top_k_by_similarity in all-pairs mode returns top-k neighbors for each vector excluding self."""
    vectors = [
        [1.0, 0.0],  # 0
        [0.9, 0.1],  # 1 (close to 0)
        [0.0, 1.0],  # 2 (far from 0, orthogonal)
    ]

    # k=1 neighbor for each vector, self excluded
    neighbors = top_k_by_similarity(vectors, 1)
    assert len(neighbors) == 3

    # Vector 0's closest neighbor is Vector 1
    assert neighbors[0][0][0] == 1
    # Vector 1's closest neighbor is Vector 0
    assert neighbors[1][0][0] == 0


def test_top_k_by_similarity_query_by_index_with_exclude_self() -> None:
    """top_k_by_similarity with integer index query excludes that index when exclude_self=True."""
    vectors = [
        [1.0, 0.0],  # 0
        [0.9, 0.1],  # 1
        [0.0, 1.0],  # 2
    ]

    res = top_k_by_similarity(vectors, 1, query=0, exclude_self=True)
    assert len(res) == 1
    # Candidate 0 must be excluded, closest remaining is 1
    assert res[0][0] == 1


def test_top_k_by_similarity_edge_cases() -> None:
    """top_k_by_similarity handles empty lists, k=0, and k larger than candidates."""
    assert top_k_by_similarity([], 5) == []
    assert top_k_by_similarity([1.0, 0.0], [], 5) == []

    vectors = [[1.0, 0.0], [0.0, 1.0]]
    assert top_k_by_similarity(vectors, 0) == [[], []]
    assert top_k_by_similarity([1.0, 0.0], vectors, 0) == []

    # k larger than available items
    top_large_k = top_k_by_similarity([1.0, 0.0], vectors, 100)
    assert len(top_large_k) == 2
