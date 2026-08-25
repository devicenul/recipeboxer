"""Local filesystem directory and multipart upload source provider for RecipeBoxer."""

from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from PIL import Image

from recipeboxer.ingestion.scanner import (
    VALID_IMAGE_EXTENSIONS,
    compute_file_sha256,
    get_image_mime_type,
    natural_sort_key,
    validate_and_load_card,
)
from recipeboxer.sources.base import (
    BaseSourceProvider,
    ListContainersResponse,
    ListItemsResponse,
    SourceCapabilities,
    SourceContainer,
    SourceItem,
    SourceProviderConfig,
)


class LocalSourceProvider(BaseSourceProvider):
    """Source provider for local directories and direct file uploads."""

    provider_id: str = "local"
    name: str = "Local Directory / Uploads"
    description: str = "Ingest scanned cards from local filesystem directories or multipart uploads"
    capabilities: SourceCapabilities = SourceCapabilities(
        supports_containers=True,
        supports_search=True,
        supports_thumbnails=True,
        supports_pagination=True,
        supports_streaming=True,
        requires_auth=False,
    )

    def __init__(self, config: Optional[SourceProviderConfig] = None) -> None:
        super().__init__(config=config)
        self.root_path: Optional[Path] = (
            Path(self.config.local_path) if self.config and self.config.local_path else None
        )

    def _resolve_path(self, path_or_id: Optional[Union[str, Path]]) -> Path:
        """Resolve a local path or item identifier securely within root_path if configured."""
        if not path_or_id:
            return (self.root_path or Path(".")).resolve()
        p = Path(path_or_id)
        if self.root_path is not None:
            root_resolved = self.root_path.resolve()
            if p.is_absolute():
                try:
                    p_resolved = p.resolve()
                    p_resolved.relative_to(root_resolved)
                    return p_resolved
                except ValueError:
                    cand = (root_resolved / p.name).resolve()
                    if cand.exists():
                        return cand
                    return p_resolved
            else:
                cand = (root_resolved / p).resolve()
                return cand
        return p.resolve() if p.exists() else p

    async def test_connection(self) -> bool:
        """Verify root directory exists if specified."""
        if self.root_path is not None:
            return self.root_path.exists() and self.root_path.is_dir()
        return True

    async def list_containers(
        self,
        page_size: int = 50,
        page_token: Optional[str] = None,
        parent_id: Optional[str] = None,
        **kwargs: Any,
    ) -> ListContainersResponse:
        """List subdirectories as containers."""
        base = self._resolve_path(parent_id) if parent_id else self.root_path
        if base is None or not base.exists() or not base.is_dir():
            return ListContainersResponse(containers=[], next_page_token=None, total_count=0)

        subdirs = [p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")]
        subdirs.sort(key=natural_sort_key)

        containers: List[SourceContainer] = []
        for d in subdirs:
            img_count = 0
            try:
                img_count = sum(
                    1
                    for f in d.iterdir()
                    if f.is_file() and f.suffix.lower() in VALID_IMAGE_EXTENSIONS
                )
            except (PermissionError, OSError):
                pass
            containers.append(
                SourceContainer(
                    id=str(d.resolve()),
                    name=d.name,
                    item_count=img_count,
                    container_type="folder",
                    metadata={"path": str(d.resolve())},
                )
            )

        offset = int(page_token) if page_token and page_token.isdigit() else 0
        paginated = containers[offset : offset + page_size]
        next_tok = str(offset + page_size) if offset + page_size < len(containers) else None

        return ListContainersResponse(
            containers=paginated,
            next_page_token=next_tok,
            total_count=len(containers),
        )

    async def list_items(
        self,
        container_id: Optional[str] = None,
        query: Optional[str] = None,
        category: Optional[str] = None,
        page_size: int = 50,
        page_token: Optional[str] = None,
        **kwargs: Any,
    ) -> ListItemsResponse:
        """List image files within local directory."""
        dir_to_scan = self._resolve_path(container_id) if container_id else self.root_path
        if dir_to_scan is None or not dir_to_scan.exists():
            return ListItemsResponse(items=[], next_page_token=None, total_count=0)

        files: List[Path] = []
        if dir_to_scan.is_file():
            files = [dir_to_scan]
        else:
            try:
                files = [
                    f
                    for f in dir_to_scan.iterdir()
                    if f.is_file()
                    and not f.name.startswith(".")
                    and f.suffix.lower() in VALID_IMAGE_EXTENSIONS
                ]
            except (PermissionError, OSError):
                files = []

        if query:
            q_lower = query.lower()
            files = [f for f in files if q_lower in f.name.lower()]

        files.sort(key=natural_sort_key)

        items: List[SourceItem] = []
        for f in files:
            stat = f.stat()
            items.append(
                SourceItem(
                    id=str(f.resolve()),
                    name=f.name,
                    mime_type=get_image_mime_type(f),
                    size_bytes=stat.st_size,
                    container_id=str(dir_to_scan.resolve()) if dir_to_scan.is_dir() else None,
                    metadata={"path": str(f.resolve())},
                )
            )

        offset = int(page_token) if page_token and page_token.isdigit() else 0
        paginated = items[offset : offset + page_size]
        next_tok = str(offset + page_size) if offset + page_size < len(items) else None

        return ListItemsResponse(
            items=paginated,
            next_page_token=next_tok,
            total_count=len(items),
        )

    async def fetch_thumbnail(self, item_id: str, max_size: int = 256) -> Optional[bytes]:
        """Generate on-the-fly thumbnail bytes for local image."""
        p = self._resolve_path(item_id)
        if not p.is_file():
            return None

        def _gen_thumb() -> Optional[bytes]:
            try:
                with Image.open(p) as img:
                    img = img.convert("RGB")
                    img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=85)
                    return buf.getvalue()
            except Exception as exc:
                return None

        return await asyncio.to_thread(_gen_thumb)

    async def download_image(self, item_id: str) -> bytes:
        """Read local file bytes directly."""
        p = self._resolve_path(item_id)
        if not p.is_file():
            raise FileNotFoundError(f"Local source item not found: {item_id}")
        return await asyncio.to_thread(p.read_bytes)

    async def get_metadata(self, item_id: str) -> SourceItem:
        """Extract metadata and dimensions from local image."""
        p = self._resolve_path(item_id)
        if not p.is_file():
            raise FileNotFoundError(f"Local file not found: {item_id}")

        stat = p.stat()
        sha256 = compute_file_sha256(p)

        width = 0
        height = 0
        mime = get_image_mime_type(p)
        try:
            with Image.open(p) as img:
                width, height = img.size
        except Exception:
            pass

        return SourceItem(
            id=str(p.resolve()),
            name=p.name,
            mime_type=mime,
            size_bytes=stat.st_size,
            width=width,
            height=height,
            sha256_hash=sha256,
            metadata={"path": str(p.resolve())},
        )
