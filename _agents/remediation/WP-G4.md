# Work Package Report: WP-G4 (Embeddings, Feature-Flagged)

## 1. Summary

WP-G4 implements `recipeboxer/ai/embeddings.py` and its comprehensive offline test suite in `tests/test_wp_g4_embeddings.py` complying with WAVE 4 Contracts §1.7 and anti-cheat invariants (INV-AC1–AC6):
- **Embedder Protocol**: Defined `@runtime_checkable class Embedder(Protocol)` with `async def embed_images` and `async def embed_texts`.
- **Production Implementation**: `GenAIEmbedder` backed by `google-genai` SDK (`client.aio.models.embed_content`), with text batching (default chunk size 16), exponential jittered retries (reusing `_extract_retry_after` from `client.py`), concurrency gating via `asyncio.Semaphore`, token bucket rate limiting (`AsyncTokenBucket`), and optional `UsageLedger` token/latency recording.
- **Multimodal Embedding Status (SDK 2.30.1)**: Inspected installed `google-genai` 2.30.1. `client.aio.models.embed_content` and its MLDev backend (`batchEmbedContents`) only support text inputs (`text-embedding-004`). Multimodal image embedding is not supported by the API endpoint. Accordingly, `embed_images` raises `ImageEmbeddingNotImplementedError`, which subclasses both `NotImplementedError` and `StageUnavailableError` (`EmbeddingUnavailable`, retryable=False, reason=FailureReason.MODEL_UNAVAILABLE), documenting that text embeddings of boundary text and ingredient vocabulary (WP-G5, WP-G11) are the primary use.
- **Vector Utilities**: Delivered NumPy-based `cosine_similarity` (1D/2D, zero-norm safe clipping to [-1, 1]) and `top_k_by_similarity` (supporting query vector, query index with self-exclusion, and all-pairs modes).
- **Safe Factory**: `create_embedder(settings)` returns `None` when `embeddings_enabled` is False (default) or no usable Gemini key is present (`recipeboxer.config.has_usable_gemini_key`).

---

## 2. Files Changed

### Owned & Created
- `recipeboxer/ai/embeddings.py`: Embedder protocol, GenAIEmbedder, exception hierarchy, create_embedder factory, cosine_similarity, and top_k_by_similarity.
- `tests/test_wp_g4_embeddings.py`: 20 offline unit/integration tests stubbing SDK client objects.
- `_agents/remediation/WP-G4.md`: This work package report.

### Touched Outside Ownership
- None. `recipeboxer/ai/client.py` and `recipeboxer/config.py` were not modified.

---

## 3. Commands Executed & Results

All commands executed using python venv with `timeout` and isolated cache directory:

```bash
# 1. Inspect google-genai version & SDK capabilities
$py -c "import google.genai; print(google.genai.__version__)"
# Output: 2.30.1

# 2. Run WP-G4 unit tests
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 600 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_g4_embeddings.py -q -p no:randomly
# Output:
# .................... [100%]
# 20 passed in 0.92s

# 3. Run anticheat test suite
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 600 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/anticheat -q -p no:randomly
# Output:
# .......... [100%]
# 10 passed in 4.00s

# 4. Run WP-X1 regression test suite
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 600 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_x1_client.py -q -p no:randomly
# Output:
# .................... [100%]
# 20 passed in 3.38s
```

---

## 4. Contract Deviations

None. Adheres strictly to WAVE 4 Contracts §1.7 and common rules §0.

---

## 5. Requests for Other WPs & Orchestrator

1. **WP-B1 (`recipeboxer/config.py`)**:
   Please add the following fields to `Settings`:
   ```python
   embeddings_enabled: bool = Field(
       default=False,
       description="Feature flag enabling dense text/semantic embeddings",
   )
   embedding_model: str = Field(
       default="text-embedding-004",
       description="Gemini model identifier for embeddings",
   )
   embedding_batch_size: int = Field(
       default=16,
       description="Batch size for embed_content calls",
   )
   embedding_timeout_s: float = Field(
       default=30.0,
       description="Per-batch embedding timeout in seconds",
   )
   ```
2. **Orchestrator / WP-B1 (`recipeboxer/ai/__init__.py`)**:
   Optionally re-export `create_embedder`, `Embedder`, `GenAIEmbedder`, `cosine_similarity`, `top_k_by_similarity` from `recipeboxer.ai`.
3. **Downstream WP-G5 / WP-G11**:
   Use `top_k_by_similarity` and `embed_texts` for candidate screening over card boundary text and ingredient vocabulary.

---

## 6. Risks & Mitigations

- **Image Embeddings Unsupported**: Google GenAI `embed_content` does not support images. Mitigated by raising `ImageEmbeddingNotImplementedError` (subclass of `StageUnavailableError` and `NotImplementedError`) with explicit reasoning, and relying on text embeddings of OCR/fingerprint transcripts for candidate screening.
- **Quota & Rate Limits**: Gemini embedding calls are throttled using `AsyncTokenBucket` (RPM limit) and `asyncio.Semaphore` (concurrency limit) with exponential backoff on HTTP 429.
