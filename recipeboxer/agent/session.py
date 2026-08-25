"""Pipeline session state machine and async HITL synchronization bridge for RecipeBoxer."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from recipeboxer.config import Settings, get_settings
from recipeboxer.ingestion.runner import IngestionCancellationToken
from recipeboxer.ingestion.scanner import ScannedCard
from recipeboxer.models.correlation import CorrelationProposal, CorrelationResult
from recipeboxer.models.domain import VisualFingerprint
from recipeboxer.models.hitl import CardPairApproval, HITLDecision
from recipeboxer.models.pipeline import (
    IngestionMode,
    PipelineStartRequest,
    PipelineState,
    PipelineStatus,
    ProgressMetrics,
    TokenUsage,
)
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.storage.cache import FingerprintCache

logger = logging.getLogger(__name__)


class PipelineSession:
    """Manages state, intermediate data, telemetry, and HITL synchronization for a pipeline run."""

    def __init__(
        self,
        session_id: Optional[str] = None,
        config: Optional[Union[PipelineStartRequest, Dict[str, Any]]] = None,
        settings: Optional[Settings] = None,
        cache: Optional[FingerprintCache] = None,
        extractor: Optional[Any] = None,
        verifier: Optional[Any] = None,
        optimizer: Optional[Any] = None,
        recipe_extractor_fn: Optional[Callable[..., Any]] = None,
        mock_client: Optional[Any] = None,
    ) -> None:
        """Initialize a new PipelineSession.

        Args:
            session_id: Unique identifier for this session. Defaults to random UUID.
            config: Launch configuration or request payload.
            settings: Settings override.
            cache: Persistent SQLite fingerprint cache.
            extractor: Optional visual extractor override.
            verifier: Optional semantic verifier override.
            optimizer: Optional pairing optimizer override.
            recipe_extractor_fn: Optional recipe extraction function override.
            mock_client: Optional mock client for testing.
        """
        self.session_id: str = session_id or f"sess_{uuid.uuid4().hex[:8]}"
        self.settings: Settings = settings or get_settings()

        if config is None:
            self.config: Union[PipelineStartRequest, Dict[str, Any]] = {}
        elif isinstance(config, dict):
            try:
                self.config = PipelineStartRequest.model_validate(config)
            except Exception:
                self.config = config
        else:
            self.config = config

        self.state: PipelineState = PipelineState.IDLE

        # Storage & Subsystems (for dependency injection / overrides)
        self.cache: Optional[FingerprintCache] = cache
        self.extractor: Optional[Any] = extractor or mock_client
        self.verifier: Optional[Any] = verifier
        self.optimizer: Optional[Any] = optimizer
        self.recipe_extractor_fn: Optional[Callable[..., Any]] = recipe_extractor_fn
        self.mock_client: Optional[Any] = mock_client

        # Ingested and extracted domain artifacts
        self.cards: List[ScannedCard] = []
        self.scanned_paths: List[Path] = []
        self.fingerprints: Dict[str, VisualFingerprint] = {}
        self.proposals: List[CorrelationProposal] = []
        self.unassigned_cards: List[Union[VisualFingerprint, str]] = []
        self.unmatched_card_ids: List[str] = []
        self.approvals: List[CardPairApproval] = []
        self.rejected_card_ids: List[str] = []
        self.recipes: List[RecipeSchema] = []
        self.csv_content: Optional[str] = None
        self.csv_output_path: Optional[str] = None

        # Telemetry & diagnostics
        self.progress_metrics: ProgressMetrics = ProgressMetrics()
        self.token_usage: TokenUsage = TokenUsage()
        self.errors: List[str] = []
        self.error_message: Optional[str] = None
        self.start_time: float = time.monotonic()
        self.end_time: Optional[float] = None

        # Async synchronization primitives
        self.approval_event: asyncio.Event = asyncio.Event()
        self.cancellation_event: asyncio.Event = asyncio.Event()
        self.cancellation_token: IngestionCancellationToken = IngestionCancellationToken()
        self.pipeline_task: Optional[asyncio.Task] = None
        self._lock: asyncio.Lock = asyncio.Lock()

    # --- Property Aliases for Compatibility ---

    @property
    def scanned_cards(self) -> List[ScannedCard]:
        return self.cards

    @scanned_cards.setter
    def scanned_cards(self, value: List[ScannedCard]) -> None:
        self.cards = value

    @property
    def approved_items(self) -> List[CardPairApproval]:
        return self.approvals

    @approved_items.setter
    def approved_items(self, value: List[CardPairApproval]) -> None:
        self.approvals = value

    @property
    def extracted_recipes(self) -> List[RecipeSchema]:
        return self.recipes

    @extracted_recipes.setter
    def extracted_recipes(self, value: List[RecipeSchema]) -> None:
        self.recipes = value

    @property
    def csv_output(self) -> Optional[str]:
        return self.csv_content

    @csv_output.setter
    def csv_output(self, value: Optional[str]) -> None:
        self.csv_content = value

    @property
    def csv_output_content(self) -> Optional[str]:
        return self.csv_content

    @csv_output_content.setter
    def csv_output_content(self, value: Optional[str]) -> None:
        self.csv_content = value

    @property
    def progress(self) -> ProgressMetrics:
        return self.progress_metrics

    @progress.setter
    def progress(self, value: ProgressMetrics) -> None:
        self.progress_metrics = value

    @property
    def ingestion_cancel_token(self) -> IngestionCancellationToken:
        return self.cancellation_token

    @ingestion_cancel_token.setter
    def ingestion_cancel_token(self, value: IngestionCancellationToken) -> None:
        self.cancellation_token = value

    @property
    def is_cancelled(self) -> bool:
        """Whether cancellation has been requested."""
        return self.cancellation_event.is_set() or self.cancellation_token.is_cancelled

    # --- Synchronization & State Lifecycle Methods ---

    async def pause_for_hitl(self) -> bool:
        """Pauses background execution at the HITL boundary until approval or cancellation.

        Returns:
            True if approval was received and pipeline should resume extraction.
            False if pipeline was cancelled during HITL wait.
        """
        self.state = PipelineState.HITL_PENDING
        self.approval_event.clear()
        self.progress_metrics.proposals = len(self.proposals)

        logger.info(
            f"[{self.session_id}] Pipeline paused for HITL review with {len(self.proposals)} proposals."
        )

        wait_approval = asyncio.create_task(self.approval_event.wait())
        wait_cancel = asyncio.create_task(self.cancellation_event.wait())

        done, pending = await asyncio.wait(
            [wait_approval, wait_cancel],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()

        if self.cancellation_event.is_set():
            self.state = PipelineState.CANCELLED
            self.end_time = time.monotonic()
            logger.info(f"[{self.session_id}] Pipeline cancelled while in HITL_PENDING.")
            return False

        self.state = PipelineState.EXTRACTING
        logger.info(f"[{self.session_id}] Pipeline unblocked by HITL approval. Resuming extraction.")
        return True

    async def wait_for_approval(self) -> bool:
        """Alias for pause_for_hitl()."""
        return await self.pause_for_hitl()

    def resume_with_approvals(
        self,
        approvals: List[CardPairApproval],
        rejected_ids: Optional[List[str]] = None,
        continue_pipeline: bool = True,
    ) -> None:
        """Submits human review decisions and unblocks the paused pipeline task.

        Args:
            approvals: List of approved, overridden, or standalone card configurations.
            rejected_ids: Optional list of card IDs to explicitly reject.
            continue_pipeline: Whether to set the approval_event immediately.

        Raises:
            ValueError: If session is not currently in HITL_PENDING state.
        """
        if self.state != PipelineState.HITL_PENDING:
            raise ValueError(
                f"Cannot resume session '{self.session_id}' in state '{self.state.value}'. "
                f"Expected '{PipelineState.HITL_PENDING.value}'."
            )

        self.approvals = approvals
        self.rejected_card_ids = rejected_ids or []
        self.progress_metrics.approved = len(approvals)

        if continue_pipeline:
            self.approval_event.set()

    def submit_approval(
        self,
        approvals: List[CardPairApproval],
        rejected_card_ids: Optional[List[str]] = None,
        rejected_ids: Optional[List[str]] = None,
        continue_pipeline: bool = True,
    ) -> None:
        """Alias for resume_with_approvals."""
        rej = rejected_card_ids if rejected_card_ids is not None else rejected_ids
        self.resume_with_approvals(
            approvals=approvals,
            rejected_ids=rej,
            continue_pipeline=continue_pipeline,
        )

    def cancel(self) -> None:
        """Signals cooperative cancellation across all pipeline stages."""
        self.cancellation_event.set()
        self.cancellation_token.cancel()
        self.state = PipelineState.CANCELLED
        self.end_time = time.monotonic()
        if self.pipeline_task and not self.pipeline_task.done():
            self.pipeline_task.cancel()
        logger.info(f"[{self.session_id}] PipelineSession cancelled.")

    def record_token_usage(
        self,
        prompt_tokens: int = 0,
        candidates_tokens: int = 0,
        thoughts_tokens: int = 0,
    ) -> None:
        """Accumulates API token usage into session telemetry."""
        self.token_usage.prompt_tokens += prompt_tokens
        self.token_usage.candidates_tokens += candidates_tokens
        self.token_usage.thoughts_tokens += thoughts_tokens
        self.token_usage.total_tokens += (prompt_tokens + candidates_tokens + thoughts_tokens)

    def record_tokens(
        self,
        prompt_tokens: int = 0,
        candidates_tokens: int = 0,
        thoughts_tokens: int = 0,
    ) -> None:
        """Alias for record_token_usage."""
        self.record_token_usage(prompt_tokens, candidates_tokens, thoughts_tokens)

    def get_status(self) -> PipelineStatus:
        """Computes and returns a snapshot of pipeline status and telemetry."""
        now = self.end_time if self.end_time is not None else time.monotonic()
        elapsed = round(max(0.0, now - self.start_time), 2)

        # Compute dynamic percentage
        pct = 0.0
        if self.state == PipelineState.IDLE:
            pct = 0.0
        elif self.state in (PipelineState.INGESTING, PipelineState.SCANNING):
            pct = 5.0
        elif self.state == PipelineState.FINGERPRINTING:
            total = max(1, self.progress_metrics.total_scans)
            done = min(total, self.progress_metrics.fingerprinted)
            pct = round(5.0 + 35.0 * (done / total), 1)
        elif self.state == PipelineState.CORRELATING:
            pct = 45.0
        elif self.state == PipelineState.HITL_PENDING:
            pct = 55.0
        elif self.state == PipelineState.EXTRACTING:
            approved = max(1, self.progress_metrics.approved)
            extracted = min(approved, self.progress_metrics.extracted)
            pct = round(55.0 + 35.0 * (extracted / approved), 1)
        elif self.state == PipelineState.EXPORTING:
            pct = 95.0
        elif self.state == PipelineState.COMPLETED:
            pct = 100.0
        elif self.state in (PipelineState.FAILED, PipelineState.CANCELLED):
            pct = self.progress_metrics.percent_complete

        self.progress_metrics.percent_complete = min(100.0, max(0.0, pct))

        mode = IngestionMode.PARALLEL
        if isinstance(self.config, PipelineStartRequest):
            mode = self.config.mode
        elif isinstance(self.config, dict) and "mode" in self.config:
            try:
                mode = IngestionMode(self.config["mode"])
            except ValueError:
                mode = IngestionMode.PARALLEL

        return PipelineStatus(
            session_id=self.session_id,
            status=self.state,
            mode=mode,
            progress=self.progress_metrics,
            token_usage=self.token_usage,
            elapsed_seconds=elapsed,
            csv_output_path=self.csv_output_path,
            error=self.error_message,
        )

    def get_status_model(self) -> PipelineStatus:
        """Alias for get_status()."""
        return self.get_status()

    async def run(self) -> None:
        """Executes the pipeline lifecycle for this session."""
        from recipeboxer.agent.orchestrator import RecipeBoxerAgent

        agent = RecipeBoxerAgent(
            settings=self.settings,
            cache=self.cache,
            visual_extractor=self.extractor,
            semantic_verifier=self.verifier,
            pairing_optimizer=self.optimizer,
            recipe_extractor=self.recipe_extractor_fn,
        )
        await agent.run_pipeline(self)


class SessionManager:
    """Thread-safe registry managing active and past PipelineSessions."""

    def __init__(self) -> None:
        self._sessions: Dict[str, PipelineSession] = {}
        self._active_session_id: Optional[str] = None
        self._lock = threading.RLock()

    def create_session(
        self,
        config: Optional[Union[PipelineStartRequest, Dict[str, Any]]] = None,
        session_id: Optional[str] = None,
        settings: Optional[Settings] = None,
        **kwargs: Any,
    ) -> PipelineSession:
        """Creates and registers a new pipeline session.

        Args:
            config: Configuration settings for the run.
            session_id: Optional custom session ID.
            settings: Settings instance override.
            **kwargs: Extra parameters passed to PipelineSession.

        Returns:
            Registered PipelineSession.
        """
        sid = session_id or f"sess_{uuid.uuid4().hex[:8]}"
        session = PipelineSession(
            session_id=sid,
            config=config,
            settings=settings,
            **kwargs,
        )
        with self._lock:
            self._sessions[sid] = session
            self._active_session_id = sid
        logger.info(f"Created and registered session '{sid}'.")
        return session

    def get_session(self, session_id: Optional[str] = None) -> Optional[PipelineSession]:
        """Retrieves session by ID or returns the currently active session if None given."""
        with self._lock:
            sid = session_id or self._active_session_id
            if not sid:
                return None
            return self._sessions.get(sid)

    def get_active_session(self) -> Optional[PipelineSession]:
        """Returns the most recently created or active PipelineSession."""
        return self.get_session()

    def get_active_session_id(self) -> Optional[str]:
        """Returns the active session ID string."""
        with self._lock:
            return self._active_session_id

    def list_sessions(self) -> List[PipelineSession]:
        """Returns all registered sessions."""
        with self._lock:
            return list(self._sessions.values())

    def get_all_sessions(self) -> List[PipelineSession]:
        """Alias for list_sessions()."""
        return self.list_sessions()

    def cleanup_stale_sessions(self, max_age_seconds: float = 86400.0) -> int:
        """Removes sessions that terminated more than max_age_seconds ago."""
        now = time.monotonic()
        to_remove = []
        with self._lock:
            for sid, s in self._sessions.items():
                if s.end_time is not None and (now - s.end_time) > max_age_seconds:
                    to_remove.append(sid)
            for sid in to_remove:
                del self._sessions[sid]
                if self._active_session_id == sid:
                    self._active_session_id = None
        return len(to_remove)

    def clear(self) -> None:
        """Clears all registered sessions."""
        with self._lock:
            self._sessions.clear()
            self._active_session_id = None


_GLOBAL_SESSION_MANAGER = SessionManager()


def get_session_manager() -> SessionManager:
    """Returns singleton SessionManager instance."""
    return _GLOBAL_SESSION_MANAGER
