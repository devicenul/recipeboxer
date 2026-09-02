"""Unified BaseSourceProvider abstraction and domain schemas for RecipeBoxer."""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union
from PIL import Image
from pydantic import BaseModel, Field

from recipeboxer.ingestion.scanner import (
    ScannedCard,
    compute_file_sha256,
    get_image_mime_type,
    validate_and_load_card,
)
from recipeboxer.storage.cache import RemoteAssetCache

logger = logging.getLogger(__name__)


class SourceCapabilities(BaseModel):
    """Capabilities and feature flags supported by a specific SourceProvider."""
    supports_containers: bool = Field(default=True, description="Supports listing and browsing albums/folders")
    supports_search: bool = Field(default=False, description="Supports media item search queries")
    supports_thumbnails: bool = Field(default=True, description="Supports low-overhead thumbnail preview fetching")
    supports_pagination: bool = Field(default=True, description="Supports paginated container and item browsing")
    supports_streaming: bool = Field(default=True, description="Supports concurrent streaming chunk downloads")
    requires_auth: bool = Field(default=False, description="Whether authentication or credentials are required")
    supported_mime_types: List[str] = Field(
        default_factory=lambda: [
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/tiff",
            "image/bmp",
            "image/heic",
            "image/heif",
            "image/gif",
        ],
        description="List of supported image MIME types",
    )
    category_filters: List[str] = Field(
        default_factory=list,
        description="Available category filters (e.g. ['FOOD', 'DOCUMENTS', 'RECEIPTS'])",
    )


class SourceItem(BaseModel):
    """Represents a remote or local media item (recipe card scan candidate)."""
    id: str = Field(..., description="Unique source-specific identifier (e.g. photo ID, file ID)")
    name: str = Field(..., description="Display filename or title")
    mime_type: str = Field(default="image/jpeg", description="MIME type of the media file")
    size_bytes: Optional[int] = Field(default=None, description="File size in bytes if known")
    width: Optional[int] = Field(default=None, description="Image pixel width if known")
    height: Optional[int] = Field(default=None, description="Image pixel height if known")
    thumbnail_url: Optional[str] = Field(default=None, description="Preview URL or data URI for thumbnail")
    download_url: Optional[str] = Field(default=None, description="Direct download URL or signed media link")
    sha256_hash: Optional[str] = Field(default=None, description="Pre-computed SHA-256 hash if available")
    container_id: Optional[str] = Field(default=None, description="Parent container / album / folder ID")
    created_at: Optional[str] = Field(default=None, description="ISO timestamp of item creation/capture")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Source-specific metadata payload")


class SourceContainer(BaseModel):
    """Represents a container (album, folder) grouping source items."""
    id: str = Field(..., description="Unique container ID (e.g. album ID, folder ID)")
    name: str = Field(..., description="Display name or title of container")
    item_count: Optional[int] = Field(default=None, description="Total media items in container if known")
    cover_image_url: Optional[str] = Field(default=None, description="Cover thumbnail URL if available")
    container_type: str = Field(default="album", description="Container category: album, folder")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Source-specific container metadata")


class SourceProviderConfig(BaseModel):
    """Configuration credentials and parameters for connecting to a SourceProvider."""
    provider_id: str = Field(..., description="Target provider identifier (local, google_photos, google_drive)")
    auth_token: Optional[str] = Field(default=None, description="OAuth2 Bearer token or API token")
    client_id: Optional[str] = Field(default=None, description="OAuth2 Client ID")
    client_secret: Optional[str] = Field(default=None, description="OAuth2 Client Secret")
    refresh_token: Optional[str] = Field(default=None, description="OAuth2 Refresh Token")
    token_uri: Optional[str] = Field(default="https://oauth2.googleapis.com/token", description="OAuth2 Token Endpoint")
    bucket_name: Optional[str] = Field(default=None, description="Optional bucket name (legacy)")
    prefix: Optional[str] = Field(default=None, description="Object key prefix or folder path")
    endpoint_url: Optional[str] = Field(default=None, description="Custom endpoint for compatible APIs")
    region_name: Optional[str] = Field(default="us-east-1", description="Cloud region")
    access_key_id: Optional[str] = Field(default=None, description="Access Key ID")
    secret_access_key: Optional[str] = Field(default=None, description="Secret Access Key")
    credentials_json: Optional[str] = Field(default=None, description="Raw service account JSON or credentials payload")
    local_path: Optional[str] = Field(default=None, description="Local filesystem path for local provider")
    category_filter: Optional[str] = Field(default=None, description="Default category filter for media search")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Additional provider-specific parameters")


class SourceConnectionStatus(BaseModel):
    """Connection and readiness status for a SourceProvider."""
    provider_id: str = Field(..., description="Unique provider ID")
    name: str = Field(..., description="Human-friendly provider name")
    description: str = Field(default="", description="Provider description")
    connected: bool = Field(default=False, description="Whether currently connected and authorized")
    auth_required: bool = Field(default=False, description="Whether authentication is required")
    authenticated: bool = Field(default=False, description="Whether valid authentication exists")
    message: str = Field(default="Ready", description="Status message or error explanation")
    capabilities: SourceCapabilities = Field(default_factory=SourceCapabilities)


class ListContainersResponse(BaseModel):
    """Paginated list of containers (albums, folders)."""
    containers: List[SourceContainer] = Field(default_factory=list)
    next_page_token: Optional[str] = Field(default=None)
    total_count: Optional[int] = Field(default=None)


class ListItemsResponse(BaseModel):
    """Paginated list of source media items."""
    items: List[SourceItem] = Field(default_factory=list)
    next_page_token: Optional[str] = Field(default=None)
    total_count: Optional[int] = Field(default=None)


class BaseSourceProvider(ABC):
    """Abstract base class establishing the pluggable ingestion contract for RecipeBoxer."""

    provider_id: str = "base"
    name: str = "Base Source Provider"
    description: str = "Abstract base source provider"
    capabilities: SourceCapabilities = SourceCapabilities()

    def __init__(self, config: Optional[SourceProviderConfig] = None) -> None:
        self.config = config or SourceProviderConfig(provider_id=self.provider_id)
        self._connected: bool = False

    @abstractmethod
    async def test_connection(self) -> bool:
        """Validate credentials and test connectivity to the remote source."""
        pass

    @abstractmethod
    async def list_containers(
        self,
        page_size: int = 50,
        page_token: Optional[str] = None,
        parent_id: Optional[str] = None,
        **kwargs: Any,
    ) -> ListContainersResponse:
        """List containers (e.g. albums, folders) from the source."""
        pass

    @abstractmethod
    async def list_items(
        self,
        container_id: Optional[str] = None,
        query: Optional[str] = None,
        category: Optional[str] = None,
        page_size: int = 50,
        page_token: Optional[str] = None,
        **kwargs: Any,
    ) -> ListItemsResponse:
        """List media items within a container or matching query/category filters."""
        pass

    @abstractmethod
    async def fetch_thumbnail(self, item_id: str, max_size: int = 256) -> Optional[bytes]:
        """Fetch low-overhead binary image thumbnail bytes for preview."""
        pass

    @abstractmethod
    async def download_image(self, item_id: str) -> bytes:
        """Download complete raw image bytes for a specific source item."""
        pass

    @abstractmethod
    async def get_metadata(self, item_id: str) -> SourceItem:
        """Retrieve full metadata for an individual source item."""
        pass

    async def get_connection_status(self) -> SourceConnectionStatus:
        """Return standardized status and capabilities for this provider."""
        is_conn = False
        msg = "Ready"
        try:
            is_conn = await self.test_connection()
            msg = "Connected" if is_conn else "Disconnected"
        except Exception as exc:
            logger.warning(f"Connection test failed for {self.provider_id}: {exc}")
            msg = str(exc)

        return SourceConnectionStatus(
            provider_id=self.provider_id,
            name=self.name,
            description=self.description,
            connected=is_conn,
            auth_required=self.capabilities.requires_auth,
            authenticated=is_conn if self.capabilities.requires_auth else True,
            message=msg,
            capabilities=self.capabilities,
        )

    async def download_and_cache(
        self,
        item_id: str,
        target_dir: Optional[Path] = None,
        asset_cache: Optional[RemoteAssetCache] = None,
        expected_hash: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> ScannedCard:
        """Download raw item, persist in SQLite content-addressable cache with SHA-256 integrity, and materialize ScannedCard."""
        effective_dir = target_dir or Path(".cache/recipeboxer/staging")
        effective_dir.mkdir(parents=True, exist_ok=True)

        # 1. Check if asset already cached
        if asset_cache is not None:
            # 1a. Check by expected hash if available
            if expected_hash and await asset_cache.acontains(expected_hash):
                cached_rec = await asset_cache.aget_record(expected_hash, include_data=True)
                if cached_rec is not None and cached_rec.data is not None:
                    return await asset_cache.amaterialize_as_scanned_card(
                        cached_rec.sha256_hash, effective_dir, filename=filename or cached_rec.filename
                    )

            # 1b. Check by source provider & item ID
            cached_rec = await asset_cache.aget_by_source(self.provider_id, item_id)
            if cached_rec is not None and cached_rec.data is not None:
                return await asset_cache.amaterialize_as_scanned_card(
                    cached_rec.sha256_hash, effective_dir, filename=filename or cached_rec.filename
                )

        # 2. Download from provider
        data = await self.download_image(item_id)
        if not data:
            raise ValueError(f"Provider {self.provider_id} returned empty binary data for item {item_id}")

        # 3. Compute and verify SHA-256
        computed_hash = hashlib.sha256(data).hexdigest().lower()
        if expected_hash:
            clean_exp = expected_hash.strip().lower()
            if clean_exp != computed_hash:
                raise ValueError(
                    f"Integrity check failed for {item_id}: expected {clean_exp}, got {computed_hash}"
                )

        # 4. Determine filename & metadata
        item_meta = None
        try:
            item_meta = await self.get_metadata(item_id)
        except Exception:
            pass

        resolved_filename = filename or (item_meta.name if item_meta else None) or f"{self.provider_id}_{item_id[:12]}.jpg"
        # Sanitize filename
        safe_filename = Path(resolved_filename).name or f"scan_{computed_hash[:12]}.jpg"
        mime = item_meta.mime_type if item_meta and item_meta.mime_type else get_image_mime_type(safe_filename)

        # 5. Persist to SQLite content-addressable WAL cache
        if asset_cache is not None:
            await asset_cache.aput(
                provider_id=self.provider_id,
                item_id=item_id,
                filename=safe_filename,
                data=data,
                mime_type=mime,
                metadata=item_meta.model_dump(mode="json") if item_meta else {},
                expected_hash=computed_hash,
            )

        # 6. Materialize to disk for downstream PIL verification and pipeline ingestion
        target_path = effective_dir / safe_filename
        if target_path.exists() and compute_file_sha256(target_path) != computed_hash:
            stem = Path(safe_filename).stem
            suffix = Path(safe_filename).suffix or ".jpg"
            safe_filename = f"{stem}_{computed_hash[:8]}{suffix}"
            target_path = effective_dir / safe_filename

        target_path.write_bytes(data)

        return validate_and_load_card(target_path)

    async def stream_download_items(
        self,
        item_ids: Sequence[str],
        target_dir: Path,
        concurrency: int = 5,
        asset_cache: Optional[RemoteAssetCache] = None,
        progress_callback: Optional[Callable[[int, int, Optional[str]], Any]] = None,
    ) -> List[ScannedCard]:
        """Download multiple items concurrently with rate-limit protection and SHA-256 verification."""
        semaphore = asyncio.Semaphore(max(1, concurrency))
        total = len(item_ids)
        completed = 0
        lock = asyncio.Lock()

        async def _worker(item_id: str) -> Optional[ScannedCard]:
            nonlocal completed
            async with semaphore:
                try:
                    card = await self.download_and_cache(
                        item_id=item_id,
                        target_dir=target_dir,
                        asset_cache=asset_cache,
                    )
                    async with lock:
                        completed += 1
                        if progress_callback:
                            res = progress_callback(completed, total, item_id)
                            if asyncio.iscoroutine(res):
                                await res
                    return card
                except Exception as exc:
                    logger.error(f"Failed streaming download for item {item_id} from {self.provider_id}: {exc}")
                    async with lock:
                        completed += 1
                        if progress_callback:
                            res = progress_callback(completed, total, item_id)
                            if asyncio.iscoroutine(res):
                                await res
                    return None

        tasks = [_worker(iid) for iid in item_ids]
        results = await asyncio.gather(*tasks)
        return [c for c in results if c is not None]
