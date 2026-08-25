"""SQLite content-addressable persistent cache for RecipeBoxer visual fingerprints.

Provides high-throughput, thread-safe, WAL-enabled storage for VisualFingerprint
models keyed by SHA-256 image content hash. Supports job resumption and zero-overhead
cache hits during ingestion.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field

from recipeboxer.models.domain import CardRole, VisualFingerprint

logger = logging.getLogger(__name__)


class FingerprintCache:
    """Thread-safe SQLite content-addressable cache for VisualFingerprint models.

    Attributes:
        db_path: Path to the SQLite database file or ':memory:'.
    """

    def __init__(
        self,
        db_path: Union[str, Path] = ":memory:",
        max_entries: int = 50000,
    ) -> None:
        """Initialize the fingerprint cache and ensure table schemas exist.

        Args:
            db_path: SQLite database file path or ':memory:'.
            max_entries: Maximum number of fingerprints to retain before LRU eviction.
        """
        self.db_path = str(db_path)
        self.max_entries = max_entries
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
            cur.execute("PRAGMA busy_timeout = 30000;")
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

    def evict_lru(self, max_entries: Optional[int] = None) -> int:
        """Evict oldest extracted fingerprints if count exceeds max_entries."""
        limit = max_entries if max_entries is not None else self.max_entries
        if limit <= 0:
            return 0
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("SELECT COUNT(*) FROM fingerprints;")
                count = int(cur.fetchone()[0])
                if count > limit:
                    excess = count - limit
                    cur.execute("""
                        DELETE FROM fingerprints
                        WHERE sha256_hash IN (
                            SELECT sha256_hash FROM fingerprints
                            ORDER BY extracted_at ASC
                            LIMIT ?
                        );
                    """, (excess,))
                    return cur.rowcount
                return 0
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

    async def aevict_lru(self, max_entries: Optional[int] = None) -> int:
        """Asynchronously evict oldest extracted fingerprints."""
        return await asyncio.to_thread(self.evict_lru, max_entries)

    async def aclear(self) -> int:
        """Asynchronously clear all cached fingerprints."""
        return await asyncio.to_thread(self.clear)

    async def __aenter__(self) -> FingerprintCache:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await asyncio.to_thread(self.close)


class RemoteAssetRecord(BaseModel):
    """Metadata record for a remote asset persisted in SQLite content-addressable cache."""
    sha256_hash: str
    provider_id: str
    item_id: str
    filename: str
    mime_type: str = "image/jpeg"
    size_bytes: int
    data: Optional[bytes] = None
    cached_at: datetime
    last_accessed_at: datetime
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RemoteAssetCache:
    """Thread-safe SQLite content-addressable cache for remote binary assets with SHA-256 integrity."""

    def __init__(
        self,
        db_path: Union[str, Path] = ":memory:",
        max_size_bytes: int = 512 * 1024 * 1024,  # 512MB default
        max_entries: int = 10000,
    ) -> None:
        self.db_path = str(db_path)
        self.max_size_bytes = max_size_bytes
        self.max_entries = max_entries
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
        """Initialize remote_assets schema and WAL mode."""
        with self._lock:
            cur = self._conn.cursor()
            if self.db_path != ":memory:":
                try:
                    cur.execute("PRAGMA journal_mode = WAL;")
                except Exception as e:
                    logger.debug(f"Could not enable WAL mode: {e}")
            cur.execute("PRAGMA synchronous = NORMAL;")
            cur.execute("PRAGMA busy_timeout = 30000;")
            cur.execute("PRAGMA foreign_keys = ON;")
            cur.execute("PRAGMA temp_store = MEMORY;")
            cur.execute("PRAGMA cache_size = -64000;")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS remote_assets (
                    sha256_hash TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    data BLOB NOT NULL,
                    cached_at TEXT NOT NULL,
                    last_accessed_at TEXT NOT NULL,
                    metadata TEXT NOT NULL
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ra_provider_item ON remote_assets(provider_id, item_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ra_cached_at ON remote_assets(cached_at);")

    @staticmethod
    def compute_hash(data: Union[bytes, str, Path]) -> str:
        """Compute SHA-256 hex digest for binary data or file path."""
        if isinstance(data, bytes):
            return hashlib.sha256(data).hexdigest().lower()
        path = Path(data)
        if path.is_file():
            hasher = hashlib.sha256()
            with open(path, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            return hasher.hexdigest().lower()
        if isinstance(data, str) and len(data) == 64:
            try:
                int(data, 16)
                return data.lower()
            except ValueError:
                pass
        raise ValueError(f"Invalid data or path for SHA-256 hash: {data}")

    def get_total_size_bytes(self) -> int:
        """Calculate total binary bytes currently stored in cache."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM remote_assets;")
                return int(cur.fetchone()[0])
            finally:
                cur.close()

    def evict_lru(
        self,
        target_bytes_freed: int = 0,
        target_max_size: Optional[int] = None,
        target_max_entries: Optional[int] = None,
    ) -> int:
        """Evict least recently accessed assets according to LRU policy. Returns number of rows evicted."""
        max_size = target_max_size if target_max_size is not None else self.max_size_bytes
        max_entries = target_max_entries if target_max_entries is not None else self.max_entries

        evicted_count = 0
        with self._lock:
            cur = self._conn.cursor()
            try:
                # 1. Evict until entries count <= max_entries
                if max_entries > 0:
                    cur.execute("SELECT COUNT(*) FROM remote_assets;")
                    count = int(cur.fetchone()[0])
                    if count > max_entries:
                        excess_count = count - max_entries
                        cur.execute("""
                            DELETE FROM remote_assets
                            WHERE sha256_hash IN (
                                SELECT sha256_hash FROM remote_assets
                                ORDER BY last_accessed_at ASC
                                LIMIT ?
                            );
                        """, (excess_count,))
                        evicted_count += cur.rowcount

                # 2. Evict until total size <= max_size or until target_bytes_freed is reached
                cur.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM remote_assets;")
                current_size = int(cur.fetchone()[0])

                if (max_size > 0 and current_size > max_size) or target_bytes_freed > 0:
                    bytes_to_free = max(current_size - max_size if max_size > 0 else 0, target_bytes_freed)
                    freed = 0
                    cur.execute("SELECT sha256_hash, size_bytes FROM remote_assets ORDER BY last_accessed_at ASC;")
                    rows = cur.fetchall()
                    hashes_to_delete = []
                    for row in rows:
                        hashes_to_delete.append(row["sha256_hash"])
                        freed += int(row["size_bytes"])
                        if freed >= bytes_to_free and (max_size <= 0 or (current_size - freed) <= max_size):
                            break

                    if hashes_to_delete:
                        cur.executemany(
                            "DELETE FROM remote_assets WHERE sha256_hash = ?;",
                            [(h,) for h in hashes_to_delete],
                        )
                        evicted_count += len(hashes_to_delete)
            finally:
                cur.close()

        return evicted_count

    def put(
        self,
        provider_id: str,
        item_id: str,
        filename: str,
        data: bytes,
        mime_type: str = "image/jpeg",
        metadata: Optional[Dict[str, Any]] = None,
        expected_hash: Optional[str] = None,
    ) -> str:
        """Store remote binary asset into SQLite cache with exact SHA-256 verification and LRU eviction."""
        computed_hash = hashlib.sha256(data).hexdigest().lower()
        if expected_hash:
            exp_clean = expected_hash.strip().lower()
            if exp_clean != computed_hash:
                raise ValueError(
                    f"SHA-256 integrity verification failed: expected {exp_clean}, computed {computed_hash}"
                )

        now_iso = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata or {})
        size_bytes = len(data)

        # Enforce cache capacity and LRU eviction before inserting
        if self.max_size_bytes > 0 or self.max_entries > 0:
            current_size = self.get_total_size_bytes()
            current_count = self.count()
            if (self.max_size_bytes > 0 and current_size + size_bytes > self.max_size_bytes) or (
                self.max_entries > 0 and current_count >= self.max_entries
            ):
                needed = max(0, current_size + size_bytes - self.max_size_bytes) if self.max_size_bytes > 0 else 0
                self.evict_lru(target_bytes_freed=needed)

        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("""
                    INSERT INTO remote_assets (
                        sha256_hash, provider_id, item_id, filename,
                        mime_type, size_bytes, data, cached_at,
                        last_accessed_at, metadata
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(sha256_hash) DO UPDATE SET
                        provider_id = excluded.provider_id,
                        item_id = excluded.item_id,
                        filename = excluded.filename,
                        mime_type = excluded.mime_type,
                        size_bytes = excluded.size_bytes,
                        data = excluded.data,
                        last_accessed_at = excluded.last_accessed_at,
                        metadata = excluded.metadata;
                """, (
                    computed_hash, provider_id, item_id, filename,
                    mime_type, size_bytes, data, now_iso,
                    now_iso, meta_json
                ))
            finally:
                cur.close()

        return computed_hash

    def get(self, sha256_hash: str) -> Optional[bytes]:
        """Retrieve binary data by SHA-256 hash, updating last accessed time."""
        clean_hash = sha256_hash.strip().lower()
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("SELECT data FROM remote_assets WHERE sha256_hash = ? LIMIT 1;", (clean_hash,))
                row = cur.fetchone()
                if row is not None:
                    cur.execute(
                        "UPDATE remote_assets SET last_accessed_at = ? WHERE sha256_hash = ?;",
                        (now_iso, clean_hash),
                    )
                    return bytes(row["data"])
                return None
            finally:
                cur.close()

    def get_record(self, sha256_hash: str, include_data: bool = False) -> Optional[RemoteAssetRecord]:
        """Retrieve RemoteAssetRecord metadata by SHA-256 hash."""
        clean_hash = sha256_hash.strip().lower()
        cols = "sha256_hash, provider_id, item_id, filename, mime_type, size_bytes, cached_at, last_accessed_at, metadata"
        if include_data:
            cols += ", data"

        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(f"SELECT {cols} FROM remote_assets WHERE sha256_hash = ? LIMIT 1;", (clean_hash,))
                row = cur.fetchone()
            finally:
                cur.close()

        if row is None:
            return None

        meta_dict = {}
        try:
            meta_dict = json.loads(row["metadata"])
        except Exception:
            pass

        return RemoteAssetRecord(
            sha256_hash=row["sha256_hash"],
            provider_id=row["provider_id"],
            item_id=row["item_id"],
            filename=row["filename"],
            mime_type=row["mime_type"],
            size_bytes=row["size_bytes"],
            data=bytes(row["data"]) if include_data and "data" in row.keys() else None,
            cached_at=datetime.fromisoformat(row["cached_at"]),
            last_accessed_at=datetime.fromisoformat(row["last_accessed_at"]),
            metadata=meta_dict,
        )

    def get_by_source(self, provider_id: str, item_id: str) -> Optional[RemoteAssetRecord]:
        """Retrieve RemoteAssetRecord by provider_id and item_id."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("""
                    SELECT sha256_hash, provider_id, item_id, filename, mime_type, size_bytes,
                           cached_at, last_accessed_at, metadata, data
                    FROM remote_assets
                    WHERE provider_id = ? AND item_id = ?
                    LIMIT 1;
                """, (provider_id, item_id))
                row = cur.fetchone()
            finally:
                cur.close()

        if row is None:
            return None

        meta_dict = {}
        try:
            meta_dict = json.loads(row["metadata"])
        except Exception:
            pass

        return RemoteAssetRecord(
            sha256_hash=row["sha256_hash"],
            provider_id=row["provider_id"],
            item_id=row["item_id"],
            filename=row["filename"],
            mime_type=row["mime_type"],
            size_bytes=row["size_bytes"],
            data=bytes(row["data"]),
            cached_at=datetime.fromisoformat(row["cached_at"]),
            last_accessed_at=datetime.fromisoformat(row["last_accessed_at"]),
            metadata=meta_dict,
        )

    def contains(self, sha256_hash: str) -> bool:
        """Check if asset exists in cache by hash."""
        clean_hash = sha256_hash.strip().lower()
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("SELECT 1 FROM remote_assets WHERE sha256_hash = ? LIMIT 1;", (clean_hash,))
                return cur.fetchone() is not None
            finally:
                cur.close()

    def materialize_to_file(self, sha256_hash: str, output_path: Union[str, Path]) -> Path:
        """Write cached asset binary bytes to disk at output_path."""
        data = self.get(sha256_hash)
        if data is None:
            raise KeyError(f"Asset with hash {sha256_hash} not found in cache")
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_bytes(data)
        return out_p

    def materialize_as_scanned_card(
        self,
        sha256_hash: str,
        target_dir: Path,
        filename: Optional[str] = None,
    ) -> Any:
        """Materialize cached asset to disk and validate as a ScannedCard."""
        from recipeboxer.ingestion.scanner import validate_and_load_card

        record = self.get_record(sha256_hash, include_data=True)
        if record is None or record.data is None:
            raise KeyError(f"Asset {sha256_hash} not found in cache")

        target_dir = Path(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        fname = filename or record.filename or f"{sha256_hash[:16]}.jpg"
        file_path = target_dir / fname
        if file_path.exists():
            existing_hash = hashlib.sha256(file_path.read_bytes()).hexdigest().lower()
            if existing_hash != sha256_hash.lower():
                stem = Path(fname).stem
                ext = Path(fname).suffix or ".jpg"
                file_path = target_dir / f"{stem}_{sha256_hash[:8]}{ext}"

        file_path.write_bytes(record.data)

        return validate_and_load_card(file_path)

    def list_assets(self, provider_id: Optional[str] = None, limit: int = 100, offset: int = 0) -> List[RemoteAssetRecord]:
        """List cached remote asset records."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                if provider_id:
                    cur.execute("""
                        SELECT sha256_hash, provider_id, item_id, filename, mime_type, size_bytes,
                               cached_at, last_accessed_at, metadata
                        FROM remote_assets
                        WHERE provider_id = ?
                        ORDER BY cached_at DESC
                        LIMIT ? OFFSET ?;
                    """, (provider_id, limit, offset))
                else:
                    cur.execute("""
                        SELECT sha256_hash, provider_id, item_id, filename, mime_type, size_bytes,
                               cached_at, last_accessed_at, metadata
                        FROM remote_assets
                        ORDER BY cached_at DESC
                        LIMIT ? OFFSET ?;
                    """, (limit, offset))
                rows = cur.fetchall()
            finally:
                cur.close()

        records: List[RemoteAssetRecord] = []
        for r in rows:
            meta = {}
            try:
                meta = json.loads(r["metadata"])
            except Exception:
                pass
            records.append(RemoteAssetRecord(
                sha256_hash=r["sha256_hash"],
                provider_id=r["provider_id"],
                item_id=r["item_id"],
                filename=r["filename"],
                mime_type=r["mime_type"],
                size_bytes=r["size_bytes"],
                cached_at=datetime.fromisoformat(r["cached_at"]),
                last_accessed_at=datetime.fromisoformat(r["last_accessed_at"]),
                metadata=meta,
            ))
        return records

    def count(self, provider_id: Optional[str] = None) -> int:
        """Return total count of cached remote assets."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                if provider_id:
                    cur.execute("SELECT COUNT(*) FROM remote_assets WHERE provider_id = ?;", (provider_id,))
                else:
                    cur.execute("SELECT COUNT(*) FROM remote_assets;")
                return int(cur.fetchone()[0])
            finally:
                cur.close()

    def delete(self, sha256_hash: str) -> bool:
        """Delete an asset from cache by hash."""
        clean_hash = sha256_hash.strip().lower()
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("DELETE FROM remote_assets WHERE sha256_hash = ?;", (clean_hash,))
                return cur.rowcount > 0
            finally:
                cur.close()

    def clear(self, provider_id: Optional[str] = None) -> int:
        """Clear cached assets."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                if provider_id:
                    cur.execute("DELETE FROM remote_assets WHERE provider_id = ?;", (provider_id,))
                else:
                    cur.execute("DELETE FROM remote_assets;")
                return cur.rowcount
            finally:
                cur.close()

    def close(self) -> None:
        """Close the SQLite database connection."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def __enter__(self) -> RemoteAssetCache:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    # --- Async Non-Blocking Interface ---

    async def aput(
        self,
        provider_id: str,
        item_id: str,
        filename: str,
        data: bytes,
        mime_type: str = "image/jpeg",
        metadata: Optional[Dict[str, Any]] = None,
        expected_hash: Optional[str] = None,
    ) -> str:
        return await asyncio.to_thread(
            self.put, provider_id, item_id, filename, data, mime_type, metadata, expected_hash
        )

    async def aget(self, sha256_hash: str) -> Optional[bytes]:
        return await asyncio.to_thread(self.get, sha256_hash)

    async def aget_record(self, sha256_hash: str, include_data: bool = False) -> Optional[RemoteAssetRecord]:
        return await asyncio.to_thread(self.get_record, sha256_hash, include_data)

    async def aget_by_source(self, provider_id: str, item_id: str) -> Optional[RemoteAssetRecord]:
        return await asyncio.to_thread(self.get_by_source, provider_id, item_id)

    async def acontains(self, sha256_hash: str) -> bool:
        return await asyncio.to_thread(self.contains, sha256_hash)

    async def amaterialize_to_file(self, sha256_hash: str, output_path: Union[str, Path]) -> Path:
        return await asyncio.to_thread(self.materialize_to_file, sha256_hash, output_path)

    async def amaterialize_as_scanned_card(
        self,
        sha256_hash: str,
        target_dir: Path,
        filename: Optional[str] = None,
    ) -> Any:
        return await asyncio.to_thread(
            self.materialize_as_scanned_card, sha256_hash, target_dir, filename
        )

    async def alist_assets(self, provider_id: Optional[str] = None, limit: int = 100, offset: int = 0) -> List[RemoteAssetRecord]:
        return await asyncio.to_thread(self.list_assets, provider_id, limit, offset)

    async def acount(self, provider_id: Optional[str] = None) -> int:
        return await asyncio.to_thread(self.count, provider_id)

    async def adelete(self, sha256_hash: str) -> bool:
        return await asyncio.to_thread(self.delete, sha256_hash)

    async def aget_total_size_bytes(self) -> int:
        return await asyncio.to_thread(self.get_total_size_bytes)

    async def aevict_lru(
        self,
        target_bytes_freed: int = 0,
        target_max_size: Optional[int] = None,
        target_max_entries: Optional[int] = None,
    ) -> int:
        return await asyncio.to_thread(
            self.evict_lru, target_bytes_freed, target_max_size, target_max_entries
        )

    async def aclear(self, provider_id: Optional[str] = None) -> int:
        return await asyncio.to_thread(self.clear, provider_id)

    async def __aenter__(self) -> RemoteAssetCache:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await asyncio.to_thread(self.close)


AssetCache = RemoteAssetCache

