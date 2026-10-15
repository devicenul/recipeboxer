"""Regression tests for datetime JSON serialization in session persistence (WP-RT2a Fix 4)."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import pytest

from recipeboxer.ingestion.scanner import ScannedCard
from recipeboxer.models.features import CaptureMeta
from recipeboxer.models.pipeline import PipelineState, ProgressMetrics, TokenUsage
from recipeboxer.storage.session_store import SessionStore


def test_session_store_save_cards_with_datetime_capture_meta(tmp_path: Path) -> None:
    """Verifies that ScannedCard with CaptureMeta.taken_at datetime persists without JSON serialization error."""
    db_file = tmp_path / "test_sessions.db"
    store = SessionStore(db_file)

    now = datetime.now(timezone.utc)
    card = ScannedCard(
        card_id="card_dt_01.jpg",
        file_path=tmp_path / "card_dt_01.jpg",
        filename="card_dt_01.jpg",
        sha256_hash="aa" * 32,
        capture_meta=CaptureMeta(
            taken_at=now,
            device="iPhone 13",
            source="camera",
        ),
    )

    store.save_session(
        session_id="sess_dt_01",
        state=PipelineState.SCANNING,
        card_count=1,
    )

    # Must not raise TypeError: Object of type datetime is not JSON serializable
    store.save_cards("sess_dt_01", [card])

    cards = store.get_cards("sess_dt_01")
    assert len(cards) == 1
    assert cards[0]["card_id"] == "card_dt_01.jpg"


def test_session_store_save_session_with_raw_datetime_in_config_and_metrics(tmp_path: Path) -> None:
    """Verifies that save_session serializes dictionaries containing datetime objects."""
    db_file = tmp_path / "test_sessions.db"
    store = SessionStore(db_file)

    now = datetime.now(timezone.utc)
    config = {
        "folder_path": str(tmp_path),
        "scheduled_time": now,
    }
    progress = {
        "stage": "COMPLETED",
        "finished_at": now,
    }
    token_usage = {
        "total_tokens": 120,
        "recorded_at": now,
    }

    # Must not raise TypeError
    store.save_session(
        session_id="sess_dt_02",
        state=PipelineState.COMPLETED,
        config=config,
        progress=progress,
        token_usage=token_usage,
    )

    sess = store.get_session("sess_dt_02")
    assert sess is not None
    assert sess["state"] == PipelineState.COMPLETED.value
