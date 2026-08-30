"""SQLite-backed session, card, proposal, and recipe persistence for RecipeBoxer."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from recipeboxer.config import get_settings
from recipeboxer.models.pipeline import PipelineState, ProgressMetrics, TokenUsage
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.models.session import SessionDetail, SessionSummary

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    """Returns current UTC timestamp formatted as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    """Thread-safe SQLite storage for PipelineSession state, cards, proposals, and recipes."""

    def __init__(self, db_path: Union[str, Path] = ":memory:") -> None:
        """Initialize SQLite database for session persistence.

        Args:
            db_path: Path to SQLite file or ':memory:'.
        """
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            parent_dir = os.path.dirname(os.path.abspath(self.db_path))
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)

        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=30.0,
            isolation_level=None,  # Autocommit mode
        )
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        """Create database tables, indexes, and performance PRAGMAs."""
        with self._lock:
            cur = self._conn.cursor()
            if self.db_path != ":memory:":
                try:
                    cur.execute("PRAGMA journal_mode = WAL;")
                except Exception as e:
                    logger.debug(f"Could not enable WAL mode for session store: {e}")
            cur.execute("PRAGMA synchronous = NORMAL;")
            cur.execute("PRAGMA busy_timeout = 5000;")
            cur.execute("PRAGMA foreign_keys = ON;")
            cur.execute("PRAGMA temp_store = MEMORY;")

            # 1. Sessions table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    mode TEXT,
                    folder_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    config_json TEXT,
                    metrics_json TEXT,
                    token_usage_json TEXT,
                    error_message TEXT,
                    csv_output_path TEXT,
                    card_count INTEGER DEFAULT 0,
                    recipe_count INTEGER DEFAULT 0
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_state ON sessions(state);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON sessions(created_at);")

            # 2. Session Cards table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS session_cards (
                    session_id TEXT NOT NULL,
                    card_id TEXT NOT NULL,
                    image_path TEXT NOT NULL,
                    sha256_hash TEXT,
                    role TEXT,
                    json_data TEXT,
                    PRIMARY KEY (session_id, card_id)
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_session_cards_sid ON session_cards(session_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_session_cards_cid ON session_cards(card_id);")

            # 3. Session Proposals table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS session_proposals (
                    session_id TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    ordered_card_ids TEXT NOT NULL,
                    json_data TEXT,
                    PRIMARY KEY (session_id, proposal_id)
                );
            """)
            cur.execute("PRAGMA table_info(session_proposals);")
            cols = [row[1] for row in cur.fetchall()]
            if cols and ("front_card_id" in cols or "ordered_card_ids" not in cols):
                cur.execute("DROP TABLE session_proposals;")
                cur.execute("""
                    CREATE TABLE session_proposals (
                        session_id TEXT NOT NULL,
                        proposal_id TEXT NOT NULL,
                        ordered_card_ids TEXT NOT NULL,
                        json_data TEXT,
                        PRIMARY KEY (session_id, proposal_id)
                    );
                """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_session_props_sid ON session_proposals(session_id);")

            # 4. Session Recipes table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS session_recipes (
                    session_id TEXT NOT NULL,
                    recipe_id TEXT NOT NULL,
                    json_data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, recipe_id)
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_session_recipes_sid ON session_recipes(session_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_session_recipes_rid ON session_recipes(recipe_id);")
            cur.close()

    # --- Session Operations ---

    def save_session(
        self,
        session_id: str,
        state: Union[PipelineState, str],
        mode: Optional[str] = None,
        folder_path: Optional[str] = None,
        config: Optional[Union[Dict[str, Any], Any]] = None,
        progress: Optional[Union[ProgressMetrics, Dict[str, Any]]] = None,
        token_usage: Optional[Union[TokenUsage, Dict[str, Any]]] = None,
        error: Optional[str] = None,
        csv_output_path: Optional[str] = None,
        card_count: Optional[int] = None,
        recipe_count: Optional[int] = None,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
    ) -> None:
        """Inserts or updates a session metadata record."""
        state_str = state.value if isinstance(state, PipelineState) else str(state)
        now_str = _utc_now_iso()
        created_str = created_at or now_str
        updated_str = updated_at or now_str

        # Serialize config
        if config is None:
            config_json = "{}"
        elif hasattr(config, "model_dump_json"):
            config_json = config.model_dump_json()
        elif isinstance(config, dict):
            config_json = json.dumps(config)
        else:
            config_json = str(config)

        # Extract folder path from config if not directly supplied
        if not folder_path and isinstance(config, dict):
            folder_path = config.get("scans_dir") or config.get("directory_path") or config.get("folder_path")
        elif not folder_path and hasattr(config, "scans_dir"):
            folder_path = getattr(config, "scans_dir", None) or getattr(config, "directory_path", None)

        # Serialize metrics
        if progress is None:
            metrics_json = "{}"
        elif hasattr(progress, "model_dump_json"):
            metrics_json = progress.model_dump_json()
        elif isinstance(progress, dict):
            metrics_json = json.dumps(progress)
        else:
            metrics_json = str(progress)

        # Serialize token usage
        if token_usage is None:
            token_json = "{}"
        elif hasattr(token_usage, "model_dump_json"):
            token_json = token_usage.model_dump_json()
        elif isinstance(token_usage, dict):
            token_json = json.dumps(token_usage)
        else:
            token_json = str(token_usage)

        with self._lock:
            cur = self._conn.cursor()
            try:
                # Check if session exists to preserve card_count / recipe_count if not supplied
                cur.execute("SELECT card_count, recipe_count, created_at FROM sessions WHERE session_id = ?;", (session_id,))
                existing = cur.fetchone()

                final_card_count = (
                    card_count
                    if card_count is not None
                    else (existing["card_count"] if existing else 0)
                )
                final_recipe_count = (
                    recipe_count
                    if recipe_count is not None
                    else (existing["recipe_count"] if existing else 0)
                )
                final_created_at = (
                    existing["created_at"]
                    if existing and not created_at
                    else created_str
                )

                cur.execute("""
                    INSERT INTO sessions (
                        session_id, state, mode, folder_path, created_at, updated_at,
                        config_json, metrics_json, token_usage_json, error_message,
                        csv_output_path, card_count, recipe_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        state = excluded.state,
                        mode = COALESCE(excluded.mode, sessions.mode),
                        folder_path = COALESCE(excluded.folder_path, sessions.folder_path),
                        updated_at = excluded.updated_at,
                        config_json = excluded.config_json,
                        metrics_json = excluded.metrics_json,
                        token_usage_json = excluded.token_usage_json,
                        error_message = excluded.error_message,
                        csv_output_path = COALESCE(excluded.csv_output_path, sessions.csv_output_path),
                        card_count = excluded.card_count,
                        recipe_count = excluded.recipe_count;
                """, (
                    session_id,
                    state_str,
                    mode,
                    str(folder_path) if folder_path else None,
                    final_created_at,
                    updated_str,
                    config_json,
                    metrics_json,
                    token_json,
                    error,
                    csv_output_path,
                    final_card_count,
                    final_recipe_count,
                ))
            finally:
                cur.close()

    def save_pipeline_session(self, session: Any) -> None:
        """Extracts complete runtime state from a PipelineSession and persists it."""
        sid = session.session_id
        state = getattr(session, "state", PipelineState.IDLE)
        config = getattr(session, "config", {})
        folder_path = None
        mode = "parallel"
        if hasattr(config, "scans_dir"):
            folder_path = config.scans_dir or getattr(config, "directory_path", None)
            mode = getattr(config, "mode", "parallel")
            if hasattr(mode, "value"):
                mode = mode.value
        elif isinstance(config, dict):
            folder_path = config.get("scans_dir") or config.get("directory_path")
            mode = config.get("mode", "parallel")

        progress = getattr(session, "progress_metrics", None)
        tokens = getattr(session, "token_usage", None)
        error = getattr(session, "error_message", None)
        csv_path = getattr(session, "csv_output_path", None)

        cards = getattr(session, "cards", [])
        proposals = getattr(session, "proposals", [])
        recipes = getattr(session, "recipes", [])

        card_count = len(cards) if cards else None
        recipe_count = len(recipes) if recipes else None

        self.save_session(
            session_id=sid,
            state=state,
            mode=str(mode),
            folder_path=str(folder_path) if folder_path else None,
            config=config,
            progress=progress,
            token_usage=tokens,
            error=error,
            csv_output_path=csv_path,
            card_count=card_count,
            recipe_count=recipe_count,
        )

        if cards:
            self.save_cards(sid, cards)
        if proposals:
            self.save_proposals(sid, proposals)
        if recipes:
            self.save_recipes(sid, recipes)

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves raw dictionary record of a session."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("SELECT * FROM sessions WHERE session_id = ? LIMIT 1;", (session_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                return dict(row)
            finally:
                cur.close()

    def get_session_summary(self, session_id: str) -> Optional[SessionSummary]:
        """Retrieves a SessionSummary model by ID."""
        row = self.get_session(session_id)
        if not row:
            return None
        return SessionSummary(
            session_id=row["session_id"],
            folder_path=row.get("folder_path"),
            status=row["state"],
            created_at=row["created_at"],
            updated_at=row.get("updated_at"),
            card_count=row.get("card_count", 0),
            recipe_count=row.get("recipe_count", 0),
            mode=row.get("mode"),
            error=row.get("error_message"),
        )

    def get_session_detail(self, session_id: str) -> Optional[SessionDetail]:
        """Retrieves full SessionDetail including metrics, cards, proposals, and recipes."""
        row = self.get_session(session_id)
        if not row:
            return None

        # Deserializations
        try:
            config = json.loads(row.get("config_json") or "{}")
        except Exception:
            config = {}

        try:
            progress_dict = json.loads(row.get("metrics_json") or "{}")
            progress = ProgressMetrics.model_validate(progress_dict)
        except Exception:
            progress = ProgressMetrics()

        try:
            tokens_dict = json.loads(row.get("token_usage_json") or "{}")
            tokens = TokenUsage.model_validate(tokens_dict)
        except Exception:
            tokens = TokenUsage()

        cards = self.get_cards(session_id)
        proposals = self.get_proposals(session_id)
        recipes = self.list_recipes(session_id)

        return SessionDetail(
            session_id=row["session_id"],
            status=row["state"],
            mode=row.get("mode") or "parallel",
            folder_path=row.get("folder_path"),
            config=config,
            progress=progress,
            token_usage=tokens,
            elapsed_seconds=progress.elapsed_seconds if hasattr(progress, "elapsed_seconds") else 0.0,
            card_count=len(cards) if cards else row.get("card_count", 0),
            recipe_count=len(recipes) if recipes else row.get("recipe_count", 0),
            cards=cards,
            proposals=proposals,
            recipes=recipes,
            created_at=row["created_at"],
            updated_at=row.get("updated_at") or row["created_at"],
            csv_output_path=row.get("csv_output_path"),
            error=row.get("error_message"),
        )

    def list_sessions(self) -> List[SessionSummary]:
        """Returns all sessions ordered by creation date descending."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("SELECT * FROM sessions ORDER BY created_at DESC;")
                rows = cur.fetchall()
            finally:
                cur.close()

        summaries: List[SessionSummary] = []
        for r in rows:
            rd = dict(r)
            summaries.append(
                SessionSummary(
                    session_id=rd["session_id"],
                    folder_path=rd.get("folder_path"),
                    status=rd["state"],
                    created_at=rd["created_at"],
                    updated_at=rd.get("updated_at"),
                    card_count=rd.get("card_count", 0),
                    recipe_count=rd.get("recipe_count", 0),
                    mode=rd.get("mode"),
                    error=rd.get("error_message"),
                )
            )
        return summaries

    def delete_session(self, session_id: str) -> bool:
        """Deletes a session and all related cards, proposals, and recipes."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("DELETE FROM session_cards WHERE session_id = ?;", (session_id,))
                cur.execute("DELETE FROM session_proposals WHERE session_id = ?;", (session_id,))
                cur.execute("DELETE FROM session_recipes WHERE session_id = ?;", (session_id,))
                cur.execute("DELETE FROM sessions WHERE session_id = ?;", (session_id,))
                return cur.rowcount > 0
            finally:
                cur.close()

    # --- Card Persistence ---

    def save_card(
        self,
        session_id: str,
        card_id: str,
        image_path: str,
        sha256_hash: Optional[str] = None,
        role: Optional[str] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Saves a scanned card entry for a session."""
        j_str = json.dumps(json_data) if json_data else "{}"
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("""
                    INSERT INTO session_cards (session_id, card_id, image_path, sha256_hash, role, json_data)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, card_id) DO UPDATE SET
                        image_path = excluded.image_path,
                        sha256_hash = excluded.sha256_hash,
                        role = excluded.role,
                        json_data = excluded.json_data;
                """, (session_id, card_id, str(image_path), sha256_hash, role, j_str))
                # Update card_count
                cur.execute(
                    "UPDATE sessions SET card_count = (SELECT COUNT(*) FROM session_cards WHERE session_id = ?) WHERE session_id = ?;",
                    (session_id, session_id),
                )
            finally:
                cur.close()

    def save_cards(self, session_id: str, cards: List[Any]) -> None:
        """Batch saves scanned cards for a session."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                for c in cards:
                    card_id = getattr(c, "card_id", None) or (c.get("card_id") if isinstance(c, dict) else str(c))
                    file_path = getattr(c, "file_path", None) or (c.get("file_path") if isinstance(c, dict) else "")
                    sha = getattr(c, "sha256_hash", None) or (c.get("sha256_hash") if isinstance(c, dict) else "")
                    role = getattr(c, "role", None)
                    if hasattr(role, "value"):
                        role = role.value
                    elif isinstance(c, dict):
                        role = c.get("role")
                    role_str = str(role) if role else None

                    j_str = "{}"
                    if hasattr(c, "model_dump_json"):
                        j_str = c.model_dump_json()
                    elif isinstance(c, dict):
                        j_str = json.dumps(c)
                    elif hasattr(c, "__dict__"):
                        try:
                            j_str = json.dumps({k: str(v) for k, v in c.__dict__.items()})
                        except Exception:
                            j_str = "{}"

                    cur.execute("""
                        INSERT INTO session_cards (session_id, card_id, image_path, sha256_hash, role, json_data)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(session_id, card_id) DO UPDATE SET
                            image_path = excluded.image_path,
                            sha256_hash = excluded.sha256_hash,
                            role = excluded.role,
                            json_data = excluded.json_data;
                    """, (session_id, str(card_id), str(file_path), sha, role_str, j_str))

                cur.execute(
                    "UPDATE sessions SET card_count = (SELECT COUNT(*) FROM session_cards WHERE session_id = ?) WHERE session_id = ?;",
                    (session_id, session_id),
                )
            finally:
                cur.close()

    def get_cards(self, session_id: str) -> List[Dict[str, Any]]:
        """Retrieves list of card metadata dictionaries for a session."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("SELECT * FROM session_cards WHERE session_id = ? ORDER BY card_id ASC;", (session_id,))
                rows = cur.fetchall()
            finally:
                cur.close()

        cards = []
        for r in rows:
            card_dict = dict(r)
            try:
                card_dict["metadata"] = json.loads(r["json_data"])
            except Exception:
                card_dict["metadata"] = {}
            cards.append(card_dict)
        return cards

    def get_card_path(self, card_id: str, session_id: Optional[str] = None) -> Optional[str]:
        """Resolves file path for a card ID across a session or globally."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                if session_id:
                    cur.execute(
                        "SELECT image_path FROM session_cards WHERE card_id = ? AND session_id = ? LIMIT 1;",
                        (card_id, session_id),
                    )
                else:
                    cur.execute(
                        "SELECT image_path FROM session_cards WHERE card_id = ? LIMIT 1;",
                        (card_id,),
                    )
                row = cur.fetchone()
                if row:
                    return str(row["image_path"])
                return None
            finally:
                cur.close()

    # --- Proposal Persistence ---

    def save_proposals(self, session_id: str, proposals: List[Any]) -> None:
        """Batch saves correlation proposals for a session."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                for p in proposals:
                    prop_id = getattr(p, "proposal_id", None) or (p.get("proposal_id") if isinstance(p, dict) else str(p))
                    raw_cards = getattr(p, "ordered_card_ids", None)
                    if raw_cards is None and isinstance(p, dict):
                        raw_cards = p.get("ordered_card_ids") or p.get("card_ids")
                        if not raw_cards and p.get("front_card_id"):
                            raw_cards = [p["front_card_id"]]
                            if p.get("back_card_id"):
                                raw_cards.append(p["back_card_id"])
                    if raw_cards is None:
                        raw_cards = []
                    cards_str = json.dumps(list(raw_cards)) if isinstance(raw_cards, (list, tuple)) else str(raw_cards)

                    if hasattr(p, "model_dump_json"):
                        j_str = p.model_dump_json()
                    elif isinstance(p, dict):
                        j_str = json.dumps(p)
                    else:
                        j_str = "{}"

                    cur.execute("""
                        INSERT INTO session_proposals (session_id, proposal_id, ordered_card_ids, json_data)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(session_id, proposal_id) DO UPDATE SET
                            ordered_card_ids = excluded.ordered_card_ids,
                            json_data = excluded.json_data;
                    """, (session_id, str(prop_id), cards_str, j_str))
            finally:
                cur.close()

    def get_proposals(self, session_id: str) -> List[Dict[str, Any]]:
        """Retrieves list of proposals for a session."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("SELECT * FROM session_proposals WHERE session_id = ?;", (session_id,))
                rows = cur.fetchall()
            finally:
                cur.close()

        proposals = []
        for r in rows:
            try:
                p_dict = json.loads(r["json_data"])
            except Exception:
                p_dict = dict(r)
            proposals.append(p_dict)
        return proposals

    # --- Recipe Persistence & CRUD ---

    def save_recipe(self, session_id: str, recipe: RecipeSchema) -> None:
        """Persists or updates a single RecipeSchema model in the session."""
        now_str = _utc_now_iso()
        recipe_dict = recipe.model_dump()
        if not recipe_dict.get("created_at"):
            recipe_dict["created_at"] = now_str
        recipe_dict["updated_at"] = now_str

        # Update model in place if needed
        recipe.created_at = recipe_dict["created_at"]
        recipe.updated_at = recipe_dict["updated_at"]

        json_data = json.dumps(recipe_dict)
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("""
                    INSERT INTO session_recipes (session_id, recipe_id, json_data, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, recipe_id) DO UPDATE SET
                        json_data = excluded.json_data,
                        updated_at = excluded.updated_at;
                """, (session_id, recipe.recipe_id, json_data, recipe_dict["created_at"], recipe_dict["updated_at"]))

                # Update recipe count in session
                cur.execute(
                    "UPDATE sessions SET recipe_count = (SELECT COUNT(*) FROM session_recipes WHERE session_id = ?) WHERE session_id = ?;",
                    (session_id, session_id),
                )
            finally:
                cur.close()

    def save_recipes(self, session_id: str, recipes: List[RecipeSchema]) -> None:
        """Batch saves a list of RecipeSchema objects."""
        for r in recipes:
            self.save_recipe(session_id, r)

    def get_recipe(self, recipe_id: str, session_id: Optional[str] = None) -> Optional[RecipeSchema]:
        """Retrieves a single RecipeSchema by recipe_id."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                if session_id:
                    cur.execute(
                        "SELECT json_data FROM session_recipes WHERE recipe_id = ? AND session_id = ? LIMIT 1;",
                        (recipe_id, session_id),
                    )
                else:
                    cur.execute(
                        "SELECT json_data FROM session_recipes WHERE recipe_id = ? LIMIT 1;",
                        (recipe_id,),
                    )
                row = cur.fetchone()
                if row is None:
                    return None
                return RecipeSchema.model_validate_json(row["json_data"])
            finally:
                cur.close()

    def list_recipes(self, session_id: Optional[str] = None) -> List[RecipeSchema]:
        """Lists all RecipeSchema objects for a session or across all sessions."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                if session_id:
                    cur.execute(
                        "SELECT json_data FROM session_recipes WHERE session_id = ? ORDER BY created_at ASC;",
                        (session_id,),
                    )
                else:
                    cur.execute("SELECT json_data FROM session_recipes ORDER BY created_at ASC;")
                rows = cur.fetchall()
            finally:
                cur.close()

        recipes: List[RecipeSchema] = []
        for r in rows:
            try:
                recipes.append(RecipeSchema.model_validate_json(r["json_data"]))
            except Exception as exc:
                logger.error(f"Failed to deserialize recipe JSON: {exc}")
        return recipes

    def delete_recipe(self, recipe_id: str, session_id: Optional[str] = None) -> bool:
        """Deletes a recipe by ID. Returns True if row was deleted."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                if session_id:
                    cur.execute(
                        "DELETE FROM session_recipes WHERE recipe_id = ? AND session_id = ?;",
                        (recipe_id, session_id),
                    )
                else:
                    cur.execute(
                        "DELETE FROM session_recipes WHERE recipe_id = ?;",
                        (recipe_id,),
                    )
                deleted = cur.rowcount > 0
                if deleted and session_id:
                    cur.execute(
                        "UPDATE sessions SET recipe_count = (SELECT COUNT(*) FROM session_recipes WHERE session_id = ?) WHERE session_id = ?;",
                        (session_id, session_id),
                    )
                return deleted
            finally:
                cur.close()

    def clear(self) -> None:
        """Clears all sessions, cards, proposals, and recipes from the store."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("DELETE FROM session_cards;")
                cur.execute("DELETE FROM session_proposals;")
                cur.execute("DELETE FROM session_recipes;")
                cur.execute("DELETE FROM sessions;")
            finally:
                cur.close()

    def close(self) -> None:
        """Closes the underlying SQLite connection."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def __enter__(self) -> SessionStore:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    # --- Async Non-Blocking Interface ---

    async def asave_session(self, *args: Any, **kwargs: Any) -> None:
        """Asynchronously save session."""
        await asyncio.to_thread(self.save_session, *args, **kwargs)

    async def asave_pipeline_session(self, session: Any) -> None:
        """Asynchronously persist a PipelineSession."""
        await asyncio.to_thread(self.save_pipeline_session, session)

    async def aget_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Asynchronously get raw session dictionary."""
        return await asyncio.to_thread(self.get_session, session_id)

    async def aget_session_summary(self, session_id: str) -> Optional[SessionSummary]:
        """Asynchronously get SessionSummary."""
        return await asyncio.to_thread(self.get_session_summary, session_id)

    async def aget_session_detail(self, session_id: str) -> Optional[SessionDetail]:
        """Asynchronously get SessionDetail."""
        return await asyncio.to_thread(self.get_session_detail, session_id)

    async def alist_sessions(self) -> List[SessionSummary]:
        """Asynchronously list all SessionSummary items."""
        return await asyncio.to_thread(self.list_sessions)

    async def adelete_session(self, session_id: str) -> bool:
        """Asynchronously delete session."""
        return await asyncio.to_thread(self.delete_session, session_id)

    async def asave_recipe(self, session_id: str, recipe: RecipeSchema) -> None:
        """Asynchronously save recipe."""
        await asyncio.to_thread(self.save_recipe, session_id, recipe)

    async def aget_recipe(self, recipe_id: str, session_id: Optional[str] = None) -> Optional[RecipeSchema]:
        """Asynchronously get recipe."""
        return await asyncio.to_thread(self.get_recipe, recipe_id, session_id)

    async def alist_recipes(self, session_id: Optional[str] = None) -> List[RecipeSchema]:
        """Asynchronously list recipes."""
        return await asyncio.to_thread(self.list_recipes, session_id)

    async def adelete_recipe(self, recipe_id: str, session_id: Optional[str] = None) -> bool:
        """Asynchronously delete recipe."""
        return await asyncio.to_thread(self.delete_recipe, recipe_id, session_id)

    async def asave_cards(self, session_id: str, cards: List[Any]) -> None:
        """Asynchronously save cards."""
        await asyncio.to_thread(self.save_cards, session_id, cards)

    async def aget_cards(self, session_id: str) -> List[Dict[str, Any]]:
        """Asynchronously get cards."""
        return await asyncio.to_thread(self.get_cards, session_id)

    async def aget_card_path(self, card_id: str, session_id: Optional[str] = None) -> Optional[str]:
        """Asynchronously get card image path."""
        return await asyncio.to_thread(self.get_card_path, card_id, session_id)

    async def __aenter__(self) -> SessionStore:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await asyncio.to_thread(self.close)


_GLOBAL_SESSION_STORE: Optional[SessionStore] = None
_STORE_LOCK = threading.RLock()


def get_session_store(db_path: Optional[Union[str, Path]] = None) -> SessionStore:
    """Returns singleton instance of SessionStore."""
    global _GLOBAL_SESSION_STORE
    with _STORE_LOCK:
        if _GLOBAL_SESSION_STORE is None or db_path is not None:
            effective_path = db_path or get_settings().cache_db_path
            _GLOBAL_SESSION_STORE = SessionStore(db_path=effective_path)
        return _GLOBAL_SESSION_STORE
