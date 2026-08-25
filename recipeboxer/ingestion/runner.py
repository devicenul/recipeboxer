"""High-concurrency async ingestion runner with Parallel and Slow-Batch modes."""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Awaitable, Callable, List, Optional, Tuple, Union
from pydantic import BaseModel, Field

from recipeboxer.ingestion.scanner import ScannedCard
from recipeboxer.models.domain import VisualFingerprint
from recipeboxer.storage.cache import FingerprintCache

logger = logging.getLogger(__name__)


class IngestionMode(str, Enum):
    """Ingestion mode for concurrency control."""
    PARALLEL = "parallel"
    SLOW_BATCH = "slow_batch"


class IngestionStatus(str, Enum):
    """Execution status of an ingestion run."""
    IDLE = "IDLE"
    SCANNING = "SCANNING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class IngestionConfig(BaseModel):
    """Ingestion runner configuration settings."""
    mode: IngestionMode = IngestionMode.PARALLEL
    max_concurrency: int = Field(default=5, ge=1, le=50)
    slow_batch_size: int = Field(default=2, ge=1, le=50)
    slow_batch_interval_seconds: float = Field(default=2.0, ge=0.0)


class CardTaskResult(BaseModel):
    """Internal result of processing an individual scanned card."""
    card: ScannedCard
    success: bool
    fingerprint: Optional[VisualFingerprint] = None
    error: Optional[str] = None
    is_cache_hit: bool = False


class IngestionProgress(BaseModel):
    """Telemetry and progress snapshot emitted during ingestion."""
    total_cards: int = 0
    processed_cards: int = 0
    cached_cards: int = 0
    newly_processed_cards: int = 0
    failed_cards: int = 0
    current_card_id: Optional[str] = None
    status: IngestionStatus = IngestionStatus.IDLE
    elapsed_seconds: float = 0.0
    percent_complete: float = 0.0


class IngestionResult(BaseModel):
    """Aggregated output from completing an ingestion job."""
    fingerprints: List[VisualFingerprint]
    failed_cards: List[Tuple[str, str]] = Field(
        default_factory=list,
        description="List of (card_id, error_reason) tuples for failed cards",
    )
    total_cards: int = 0
    cache_hits: int = 0
    newly_extracted: int = 0
    failed_count: int = 0
    elapsed_seconds: float = 0.0
    status: IngestionStatus = IngestionStatus.COMPLETED


class IngestionCancellationToken:
    """Thread-safe cooperative cancellation token for async ingestion workflows."""

    def __init__(self) -> None:
        self._cancelled = asyncio.Event()

    def cancel(self) -> None:
        """Signal cancellation to in-flight ingestion tasks."""
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        """Whether cancellation has been requested."""
        return self._cancelled.is_set()

    async def wait_cancelled(self) -> None:
        """Asynchronously wait until cancellation is signaled."""
        await self._cancelled.wait()


ExtractorCallable = Callable[[ScannedCard], Awaitable[VisualFingerprint]]
ProgressCallback = Callable[[IngestionProgress], Union[Awaitable[None], None]]


async def _maybe_await(result: Any) -> Any:
    """Await value if it is a coroutine or future, otherwise return directly."""
    if asyncio.iscoroutine(result) or asyncio.isfuture(result):
        return await result
    return result


class IngestionRunner:
    """Coordinates card scanning, fingerprint cache checking, and extraction throttling."""

    def __init__(
        self,
        config: Optional[IngestionConfig] = None,
        cache: Optional[FingerprintCache] = None,
        extractor_fn: Optional[ExtractorCallable] = None,
    ) -> None:
        """Initialize IngestionRunner.

        Args:
            config: Configuration controlling ingestion mode and rate limits.
            cache: Persistent SQLite cache to check before invoking extractor.
            extractor_fn: Async callable that extracts VisualFingerprint from ScannedCard.
        """
        self.config = config or IngestionConfig()
        self.cache = cache
        self.extractor_fn = extractor_fn
        self._lock = asyncio.Lock()

    async def run(
        self,
        cards: List[ScannedCard],
        progress_callback: Optional[ProgressCallback] = None,
        cancellation_token: Optional[IngestionCancellationToken] = None,
    ) -> IngestionResult:
        """Execute ingestion over a list of scanned cards.

        Args:
            cards: List of ScannedCard items to process.
            progress_callback: Optional callback invoked on progress updates.
            cancellation_token: Optional cancellation token to abort in-flight work.

        Returns:
            IngestionResult containing extracted fingerprints and failure diagnostics.
        """
        start_time = time.monotonic()
        total_cards = len(cards)
        cancel_token = cancellation_token or IngestionCancellationToken()

        if total_cards == 0:
            return IngestionResult(
                fingerprints=[],
                failed_cards=[],
                total_cards=0,
                cache_hits=0,
                newly_extracted=0,
                failed_count=0,
                elapsed_seconds=0.0,
                status=IngestionStatus.COMPLETED,
            )

        progress = IngestionProgress(
            total_cards=total_cards,
            status=IngestionStatus.RUNNING,
        )

        async def update_progress(card_id: Optional[str] = None) -> None:
            async with self._lock:
                progress.current_card_id = card_id
                progress.elapsed_seconds = time.monotonic() - start_time
                if progress.total_cards > 0:
                    progress.percent_complete = round(
                        (progress.processed_cards / progress.total_cards) * 100.0, 1
                    )
                if progress_callback:
                    try:
                        await _maybe_await(progress_callback(progress))
                    except Exception as e:
                        logger.warning(f"Progress callback error: {e}")

        semaphore = asyncio.Semaphore(self.config.max_concurrency)

        async def process_single_card(card: ScannedCard) -> CardTaskResult:
            if cancel_token.is_cancelled:
                return CardTaskResult(
                    card=card,
                    success=False,
                    error="Task cancelled before execution",
                )

            async with semaphore:
                if cancel_token.is_cancelled:
                    return CardTaskResult(
                        card=card,
                        success=False,
                        error="Task cancelled before execution",
                    )

                # 1. Check cache by SHA-256 hash
                if self.cache is not None:
                    try:
                        if self.cache.contains(card.sha256_hash):
                            cached_fp = self.cache.get(card.sha256_hash, image_path=str(card.file_path))
                            if cached_fp is not None:
                                async with self._lock:
                                    progress.processed_cards += 1
                                    progress.cached_cards += 1
                                await update_progress(card.card_id)
                                return CardTaskResult(
                                    card=card,
                                    success=True,
                                    fingerprint=cached_fp,
                                    is_cache_hit=True,
                                )
                    except Exception as exc:
                        logger.warning(f"Cache lookup failed for {card.card_id}: {exc}")

                # 2. Cache miss: invoke extractor function
                if self.extractor_fn is None:
                    err = "No extractor_fn provided for cache miss"
                    async with self._lock:
                        progress.processed_cards += 1
                        progress.failed_cards += 1
                    await update_progress(card.card_id)
                    return CardTaskResult(card=card, success=False, error=err)

                try:
                    fp = await self.extractor_fn(card)
                    if fp is not None and not isinstance(fp, VisualFingerprint) and hasattr(fp, "model_dump"):
                        fp = VisualFingerprint.model_validate(fp.model_dump(mode="json"))

                    # Store newly extracted fingerprint in cache
                    if self.cache is not None and fp is not None:
                        try:
                            self.cache.put(fp)
                        except Exception as exc:
                            logger.warning(f"Failed to cache fingerprint for {card.card_id}: {exc}")

                    async with self._lock:
                        progress.processed_cards += 1
                        progress.newly_processed_cards += 1
                    await update_progress(card.card_id)
                    return CardTaskResult(
                        card=card,
                        success=True,
                        fingerprint=fp,
                        is_cache_hit=False,
                    )
                except Exception as exc:
                    logger.error(f"Extraction failed for {card.card_id}: {exc}")
                    async with self._lock:
                        progress.processed_cards += 1
                        progress.failed_cards += 1
                    await update_progress(card.card_id)
                    return CardTaskResult(card=card, success=False, error=str(exc))

        results: List[CardTaskResult] = []

        try:
            if self.config.mode == IngestionMode.PARALLEL:
                # Parallel mode: launch all tasks concurrently bounded by semaphore
                task_coros = [process_single_card(card) for card in cards]
                results = list(await asyncio.gather(*task_coros))

            elif self.config.mode == IngestionMode.SLOW_BATCH:
                # Slow-batch mode: process chunks with interval pauses between chunks
                batch_size = max(1, self.config.slow_batch_size)
                chunks = [cards[i : i + batch_size] for i in range(0, len(cards), batch_size)]

                for idx, chunk in enumerate(chunks):
                    if cancel_token.is_cancelled:
                        break

                    chunk_coros = [process_single_card(card) for card in chunk]
                    chunk_results = await asyncio.gather(*chunk_coros)
                    results.extend(chunk_results)

                    # Sleep between batches, but NEVER after the final batch
                    if idx < len(chunks) - 1 and not cancel_token.is_cancelled:
                        sleep_time = self.config.slow_batch_interval_seconds
                        if sleep_time > 0:
                            try:
                                await asyncio.wait_for(
                                    cancel_token.wait_cancelled(),
                                    timeout=sleep_time,
                                )
                                break  # Cancelled during sleep
                            except asyncio.TimeoutError:
                                pass  # Slept successfully

        except asyncio.CancelledError:
            progress.status = IngestionStatus.CANCELLED
            logger.info("Ingestion runner cancelled by asyncio")
            raise

        # Compile final results
        successful_fps = [r.fingerprint for r in results if r.success and r.fingerprint is not None]
        failed_cards = [(r.card.card_id, r.error or "Unknown error") for r in results if not r.success]

        status = (
            IngestionStatus.CANCELLED
            if cancel_token.is_cancelled
            else IngestionStatus.FAILED
            if len(successful_fps) == 0 and len(failed_cards) > 0
            else IngestionStatus.COMPLETED
        )
        progress.status = status
        await update_progress(None)

        return IngestionResult(
            fingerprints=successful_fps,
            failed_cards=failed_cards,
            total_cards=total_cards,
            cache_hits=sum(1 for r in results if r.is_cache_hit),
            newly_extracted=sum(1 for r in results if r.success and not r.is_cache_hit),
            failed_count=len(failed_cards),
            elapsed_seconds=time.monotonic() - start_time,
            status=status,
        )

    async def ingest_from_provider(
        self,
        provider: Any,
        container_id: Optional[str] = None,
        item_ids: Optional[List[str]] = None,
        target_dir: Optional[Union[str, Any]] = None,
        asset_cache: Optional[Any] = None,
        progress_callback: Optional[ProgressCallback] = None,
        cancellation_token: Optional[IngestionCancellationToken] = None,
    ) -> IngestionResult:
        """Download remote assets from a SourceProvider, cache locally, and run visual fingerprinting."""
        from pathlib import Path

        # 1. Resolve item IDs if not provided
        target_item_ids = list(item_ids) if item_ids else []
        if not target_item_ids:
            list_resp = await provider.list_items(container_id=container_id, page_size=100)
            target_item_ids = [item.id for item in list_resp.items]

        if not target_item_ids:
            return IngestionResult(
                fingerprints=[],
                failed_cards=[],
                total_cards=0,
                cache_hits=0,
                newly_extracted=0,
                failed_count=0,
                elapsed_seconds=0.0,
                status=IngestionStatus.COMPLETED,
            )

        # 2. Download items to target staging directory
        staging_dir = Path(target_dir) if target_dir else Path(".cache/recipeboxer/staging")
        staging_dir.mkdir(parents=True, exist_ok=True)

        cards = await provider.stream_download_items(
            item_ids=target_item_ids,
            target_dir=staging_dir,
            concurrency=self.config.max_concurrency,
            asset_cache=asset_cache,
        )

        # 3. Execute standard ingestion workflow over downloaded ScannedCards
        return await self.run(
            cards=cards,
            progress_callback=progress_callback,
            cancellation_token=cancellation_token,
        )

