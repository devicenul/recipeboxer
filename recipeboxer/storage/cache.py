"""SQLite content-addressable persistent cache for RecipeBoxer visual fingerprints.

Provides high-throughput, thread-safe, WAL-enabled storage for VisualFingerprint
models keyed by SHA-256 image content hash. Supports job resumption and zero-overhead
cache hits during ingestion.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Union

from recipeboxer.models.domain import CardRole, VisualFingerprint

logger = logging.getLogger(__name__)


class FingerprintCache:
    """Thread-safe SQLite content-addressable cache for VisualFingerprint models.

    Attributes:
        db_path: Path to the SQLite database file or ':memory:'.
    """

    def __init__(self, db_path: Union[str, Path] = ":memory:") -> None:
        """Initialize the fingerprint cache and ensure table schemas exist.

        Args:
            db_path: SQLite database file path or ':memory:'.
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
        """Initialize schema, WAL journal mode, and performance PRAGMAs."""
        with self._lock:
            cur = self._conn.cursor()
            if self.db_path != ":memory:":
                try:
                    cur.execute("PRAGMA journal_mode = WAL;")
                except Exception as e:
                    logger.debug(f"Could not enable WAL mode: {e}")
            cur.execute("PRAGMA synchronous = NORMAL;")
            cur.execute("PRAGMA busy_timeout = 5000;")
            cur.execute("PRAGMA foreign_keys = ON;")
            cur.execute("PRAGMA temp_store = MEMORY;")
            cur.execute("PRAGMA cache_size = -64000;")  # 64MB page cache

            cur.execute("""
                CREATE TABLE IF NOT EXISTS fingerprints (
                    sha256_hash TEXT PRIMARY KEY,
                    card_id TEXT NOT NULL,
                    image_path TEXT NOT NULL,
                    role TEXT NOT NULL,
                    json_data TEXT NOT NULL,
                    extracted_at TEXT NOT NULL
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_fp_card_id ON fingerprints(card_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_fp_image_path ON fingerprints(image_path);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_fp_role ON fingerprints(role);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_fp_extracted_at ON fingerprints(extracted_at);")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)
            cur.execute("INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('schema_version', '1');")

    @staticmethod
    def compute_hash(image_bytes_or_path: Union[bytes, str, Path]) -> str:
        """Compute SHA-256 hex digest for byte array, file path, or validate hex string."""
        if isinstance(image_bytes_or_path, bytes):
            return hashlib.sha256(image_bytes_or_path).hexdigest().lower()

        path = Path(image_bytes_or_path)
        if path.is_file():
            hasher = hashlib.sha256()
            with open(path, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            return hasher.hexdigest().lower()

        if isinstance(image_bytes_or_path, str) and len(image_bytes_or_path) == 64:
            try:
                int(image_bytes_or_path, 16)
                return image_bytes_or_path.lower()
            except ValueError:
                pass

        raise ValueError(f"Invalid image content or path for hashing: {image_bytes_or_path}")

    def _resolve_hash(self, key: Union[bytes, str, Path]) -> str:
        """Helper to resolve a key into a valid SHA-256 hash string."""
        if isinstance(key, str) and len(key) == 64:
            try:
                int(key, 16)
                return key.lower()
            except ValueError:
                pass
        return self.compute_hash(key)

    def get(
        self,
        image_bytes_or_hash: Union[bytes, str, Path],
        image_path: Optional[str] = None,
    ) -> Optional[VisualFingerprint]:
        """Retrieve a cached VisualFingerprint by image bytes, path, or SHA-256 hash.

        Args:
            image_bytes_or_hash: Image bytes, Path object, file path, or 64-char hex hash.
            image_path: Optional path to override in retrieved fingerprint if moved.

        Returns:
            VisualFingerprint if found and deserialized, else None.
        """
        try:
            sha256_hash = self._resolve_hash(image_bytes_or_hash)
        except Exception as e:
            logger.warning(f"Failed to resolve hash for cache lookup: {e}")
            return None

        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("SELECT json_data FROM fingerprints WHERE sha256_hash = ? LIMIT 1;", (sha256_hash,))
                row = cur.fetchone()
            finally:
                cur.close()

            if row is None:
                return None

            try:
                fp = VisualFingerprint.model_validate_json(row["json_data"])
                if image_path and fp.image_path != image_path:
                    fp.image_path = image_path
                return fp
            except Exception as exc:
                logger.error(f"Corrupt fingerprint JSON for hash {sha256_hash}: {exc}")
                return None

    def get_by_hash(self, sha256_hash: str) -> Optional[VisualFingerprint]:
        """Retrieve a cached VisualFingerprint by pre-computed SHA-256 hash."""
        return self.get(sha256_hash)

    def put(self, fingerprint: VisualFingerprint) -> None:
        """Insert or update a VisualFingerprint in the SQLite cache.

        Args:
            fingerprint: VisualFingerprint instance to persist.
        """
        sha256_hash = fingerprint.sha256_hash.lower()
        card_id = fingerprint.card_id
        image_path = str(fingerprint.image_path)
        role = fingerprint.role.value if isinstance(fingerprint.role, CardRole) else str(fingerprint.role)
        json_data = fingerprint.model_dump_json()
        extracted_at = (
            fingerprint.extracted_at.isoformat()
            if isinstance(fingerprint.extracted_at, datetime)
            else str(fingerprint.extracted_at)
        )

        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("""
                    INSERT INTO fingerprints (sha256_hash, card_id, image_path, role, json_data, extracted_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(sha256_hash) DO UPDATE SET
                        card_id = excluded.card_id,
                        image_path = excluded.image_path,
                        role = excluded.role,
                        json_data = excluded.json_data,
                        extracted_at = excluded.extracted_at;
                """, (sha256_hash, card_id, image_path, role, json_data, extracted_at))
            finally:
                cur.close()

    def contains(self, image_bytes_or_hash: Union[bytes, str, Path]) -> bool:
        """Check if a fingerprint exists for the given image or hash."""
        try:
            sha256_hash = self._resolve_hash(image_bytes_or_hash)
        except Exception:
            return False

        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("SELECT 1 FROM fingerprints WHERE sha256_hash = ? LIMIT 1;", (sha256_hash,))
                return cur.fetchone() is not None
            finally:
                cur.close()

    def get_all(self) -> List[VisualFingerprint]:
        """Retrieve all cached fingerprints ordered by extraction timestamp."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("SELECT json_data FROM fingerprints ORDER BY extracted_at ASC;")
                rows = cur.fetchall()
            finally:
                cur.close()

        results: List[VisualFingerprint] = []
        for row in rows:
            try:
                results.append(VisualFingerprint.model_validate_json(row["json_data"]))
            except Exception as exc:
                logger.error(f"Error deserializing fingerprint row: {exc}")
        return results

    def get_by_card_id(self, card_id: str) -> Optional[VisualFingerprint]:
        """Retrieve a cached fingerprint by card_id."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("SELECT json_data FROM fingerprints WHERE card_id = ? LIMIT 1;", (card_id,))
                row = cur.fetchone()
            finally:
                cur.close()
            if row is None:
                return None
            return VisualFingerprint.model_validate_json(row["json_data"])

    def get_by_role(self, role: Union[CardRole, str]) -> List[VisualFingerprint]:
        """Retrieve all cached fingerprints matching a specific CardRole."""
        role_str = role.value if isinstance(role, CardRole) else str(role)
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("SELECT json_data FROM fingerprints WHERE role = ? ORDER BY extracted_at ASC;", (role_str,))
                rows = cur.fetchall()
            finally:
                cur.close()

        results: List[VisualFingerprint] = []
        for row in rows:
            try:
                results.append(VisualFingerprint.model_validate_json(row["json_data"]))
            except Exception as exc:
                logger.error(f"Error deserializing fingerprint row for role {role_str}: {exc}")
        return results

    def count(self) -> int:
        """Return total number of cached fingerprints."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("SELECT COUNT(*) FROM fingerprints;")
                return int(cur.fetchone()[0])
            finally:
                cur.close()

    def delete(self, image_bytes_or_hash: Union[bytes, str, Path]) -> bool:
        """Delete a fingerprint entry from cache. Returns True if row was deleted."""
        try:
            sha256_hash = self._resolve_hash(image_bytes_or_hash)
        except Exception:
            return False

        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("DELETE FROM fingerprints WHERE sha256_hash = ?;", (sha256_hash,))
                return cur.rowcount > 0
            finally:
                cur.close()

    def clear(self) -> int:
        """Clear all cached fingerprints. Returns number of rows deleted."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("DELETE FROM fingerprints;")
                return cur.rowcount
            finally:
                cur.close()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def __enter__(self) -> FingerprintCache:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    # --- Async Non-Blocking Interface ---

    async def aget(
        self,
        image_bytes_or_hash: Union[bytes, str, Path],
        image_path: Optional[str] = None,
    ) -> Optional[VisualFingerprint]:
        """Asynchronously retrieve a cached VisualFingerprint."""
        return await asyncio.to_thread(self.get, image_bytes_or_hash, image_path)

    async def aget_by_hash(self, sha256_hash: str) -> Optional[VisualFingerprint]:
        """Asynchronously retrieve a cached VisualFingerprint by hash."""
        return await asyncio.to_thread(self.get_by_hash, sha256_hash)

    async def aput(self, fingerprint: VisualFingerprint) -> None:
        """Asynchronously persist a VisualFingerprint."""
        await asyncio.to_thread(self.put, fingerprint)

    async def acontains(self, image_bytes_or_hash: Union[bytes, str, Path]) -> bool:
        """Asynchronously check if a fingerprint exists in cache."""
        return await asyncio.to_thread(self.contains, image_bytes_or_hash)

    async def aget_all(self) -> List[VisualFingerprint]:
        """Asynchronously retrieve all cached fingerprints."""
        return await asyncio.to_thread(self.get_all)

    async def aget_by_card_id(self, card_id: str) -> Optional[VisualFingerprint]:
        """Asynchronously retrieve a cached fingerprint by card_id."""
        return await asyncio.to_thread(self.get_by_card_id, card_id)

    async def aget_by_role(self, role: Union[CardRole, str]) -> List[VisualFingerprint]:
        """Asynchronously retrieve cached fingerprints by role."""
        return await asyncio.to_thread(self.get_by_role, role)

    async def acount(self) -> int:
        """Asynchronously return total cached count."""
        return await asyncio.to_thread(self.count)

    async def adelete(self, image_bytes_or_hash: Union[bytes, str, Path]) -> bool:
        """Asynchronously delete a fingerprint."""
        return await asyncio.to_thread(self.delete, image_bytes_or_hash)

    async def aclear(self) -> int:
        """Asynchronously clear all cached fingerprints."""
        return await asyncio.to_thread(self.clear)

    async def __aenter__(self) -> FingerprintCache:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await asyncio.to_thread(self.close)
