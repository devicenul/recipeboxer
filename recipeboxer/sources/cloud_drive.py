"""Google Drive source connector for browsing and ingesting recipe card folder scans."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Dict, List, Optional
import httpx
from pydantic import Field

from recipeboxer.sources.base import (
    BaseSourceProvider,
    ListContainersResponse,
    ListItemsResponse,
    SourceCapabilities,
    SourceContainer,
    SourceItem,
    SourceProviderConfig,
)

logger = logging.getLogger(__name__)

GOOGLE_DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"


class GoogleDriveProvider(BaseSourceProvider):
    """Google Drive connector supporting folder browsing and card scan ingestion."""

    provider_id: str = "google_drive"
    name: str = "Google Drive"
    description: str = "Browse and select specific Google Drive folders containing recipe card scans"
    capabilities: SourceCapabilities = SourceCapabilities(
        supports_containers=True,
        supports_search=True,
        supports_thumbnails=True,
        supports_pagination=True,
        supports_streaming=True,
        requires_auth=True,
        supported_mime_types=[
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/tiff",
            "image/bmp",
            "image/heic",
            "image/heif",
            "image/gif",
        ],
        category_filters=[],
    )

    def __init__(
        self,
        config: Optional[SourceProviderConfig] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(config=config)
        self._custom_client = http_client
        self._access_token: Optional[str] = self.config.auth_token if self.config else None
        self._item_cache: Dict[str, SourceItem] = {}
        self._token_lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._custom_client is not None:
            return self._custom_client
        return httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))

    async def _refresh_access_token(self, failed_token: Optional[str] = None) -> str:
        """Exchange refresh token for access token."""
        async with self._token_lock:
            if failed_token and self._access_token and self._access_token != failed_token:
                return self._access_token

            if not self.config.refresh_token:
                if self._access_token:
                    return self._access_token
                raise ValueError("No OAuth2 access_token or refresh_token provided for Google Drive")

            client = await self._get_client()
            should_close = self._custom_client is None
            try:
                payload = {
                    "client_id": self.config.client_id or "recipeboxer-drive-client",
                    "client_secret": self.config.client_secret or "recipeboxer-drive-secret",
                    "refresh_token": self.config.refresh_token,
                    "grant_type": "refresh_token",
                }
                token_url = self.config.token_uri or GOOGLE_OAUTH_TOKEN_URL
                resp = await client.post(token_url, data=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    new_tok = data.get("access_token")
                    if new_tok:
                        self._access_token = new_tok
                        self.config.auth_token = new_tok
                        return new_tok
                raise PermissionError(f"Google Drive OAuth2 refresh failed: HTTP {resp.status_code} {resp.text}")
            finally:
                if should_close:
                    await client.aclose()

    async def _ensure_token(self) -> str:
        if self._access_token:
            return self._access_token
        if self.config.refresh_token:
            return await self._refresh_access_token()
        raise PermissionError("Google Drive requires authentication (auth_token or refresh_token).")

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        max_retries: int = 4,
        is_binary: bool = False,
    ) -> httpx.Response:
        """Execute request with exponential backoff on 429/503 and auto-refresh on 401."""
        token = await self._ensure_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json" if not is_binary else "*/*",
        }

        client = await self._get_client()
        should_close = self._custom_client is None
        backoff = 0.5

        try:
            for attempt in range(max_retries):
                try:
                    resp = await client.request(
                        method=method,
                        url=url,
                        headers=headers,
                        params=params,
                        json=json_data,
                    )

                    if resp.status_code == 401 and self.config.refresh_token and attempt < max_retries - 1:
                        token = await self._refresh_access_token(failed_token=token)
                        headers["Authorization"] = f"Bearer {token}"
                        continue

                    if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                        sleep_secs = backoff + random.uniform(0.1, 0.3)
                        await asyncio.sleep(sleep_secs)
                        backoff *= 2.0
                        continue

                    if resp.status_code >= 400:
                        raise httpx.HTTPStatusError(
                            f"Google Drive API error {resp.status_code}: {resp.text}",
                            request=resp.request,
                            response=resp,
                        )

                    return resp

                except (httpx.ConnectError, httpx.TimeoutException) as exc:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(backoff)
                        backoff *= 2.0
                        continue
                    raise exc
            raise RuntimeError(f"Google Drive API request failed after {max_retries} attempts.")
        finally:
            if should_close:
                await client.aclose()

    async def test_connection(self) -> bool:
        """Test connection to Google Drive API."""
        try:
            resp = await self._request_with_retry(
                "GET",
                f"{GOOGLE_DRIVE_API_BASE}/files",
                params={"pageSize": 1, "fields": "files(id)"},
            )
            return resp.status_code == 200
        except Exception as exc:
            logger.debug(f"Google Drive test_connection failed: {exc}")
            return False

    async def list_containers(
        self,
        page_size: int = 50,
        page_token: Optional[str] = None,
        parent_id: Optional[str] = None,
        **kwargs: Any,
    ) -> ListContainersResponse:
        """List Google Drive folders."""
        query_parts = [
            "mimeType = 'application/vnd.google-apps.folder'",
            "trashed = false",
        ]
        if parent_id:
            safe_parent = parent_id.replace("\\", "\\\\").replace("'", "\\'")
            query_parts.append(f"'{safe_parent}' in parents")

        params: Dict[str, Any] = {
            "q": " and ".join(query_parts),
            "pageSize": min(page_size, 100),
            "fields": "nextPageToken,files(id,name,mimeType,iconLink,thumbnailLink,createdTime)",
        }
        if page_token:
            params["pageToken"] = page_token

        resp = await self._request_with_retry("GET", f"{GOOGLE_DRIVE_API_BASE}/files", params=params)
        data = resp.json()
        files = data.get("files", [])

        containers: List[SourceContainer] = []
        for f in files:
            containers.append(
                SourceContainer(
                    id=f.get("id", ""),
                    name=f.get("name", "Untitled Folder"),
                    container_type="folder",
                    cover_image_url=f.get("thumbnailLink"),
                    metadata={
                        "iconLink": f.get("iconLink"),
                        "createdTime": f.get("createdTime"),
                    },
                )
            )

        return ListContainersResponse(
            containers=containers,
            next_page_token=data.get("nextPageToken"),
            total_count=None,
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
        """List image scan files in Google Drive folder."""
        query_parts = [
            "mimeType != 'application/vnd.google-apps.folder'",
            "trashed = false",
            "(mimeType contains 'image/' or name contains '.jpg' or name contains '.jpeg' or name contains '.png' or name contains '.webp' or name contains '.tiff' or name contains '.tif' or name contains '.bmp' or name contains '.heic' or name contains '.heif' or name contains '.gif')",
        ]
        if container_id:
            safe_cid = container_id.replace("\\", "\\\\").replace("'", "\\'")
            query_parts.append(f"'{safe_cid}' in parents")
        if query:
            safe_query = query.replace("\\", "\\\\").replace("'", "\\'")
            query_parts.append(f"name contains '{safe_query}'")

        params: Dict[str, Any] = {
            "q": " and ".join(query_parts),
            "pageSize": min(page_size, 100),
            "fields": "nextPageToken,files(id,name,mimeType,size,imageMediaMetadata,thumbnailLink,webContentLink,createdTime,sha256Checksum)",
        }
        if page_token:
            params["pageToken"] = page_token

        resp = await self._request_with_retry("GET", f"{GOOGLE_DRIVE_API_BASE}/files", params=params)
        data = resp.json()
        files = data.get("files", [])

        items: List[SourceItem] = []
        for f in files:
            f_id = f.get("id", "")
            fname = f.get("name", f"drive_{f_id[:8]}.jpg")
            mime = f.get("mimeType", "image/jpeg")
            size = int(f["size"]) if "size" in f and f["size"] else None
            meta = f.get("imageMediaMetadata", {})
            width = int(meta["width"]) if "width" in meta and meta["width"] else None
            height = int(meta["height"]) if "height" in meta and meta["height"] else None

            item = SourceItem(
                id=f_id,
                name=fname,
                mime_type=mime,
                size_bytes=size,
                width=width,
                height=height,
                thumbnail_url=f.get("thumbnailLink"),
                download_url=f.get("webContentLink"),
                sha256_hash=f.get("sha256Checksum"),
                container_id=container_id,
                created_at=f.get("createdTime"),
                metadata=meta,
            )
            items.append(item)
            self._item_cache[f_id] = item

        return ListItemsResponse(
            items=items,
            next_page_token=data.get("nextPageToken"),
            total_count=None,
        )

    async def get_metadata(self, item_id: str) -> SourceItem:
        """Fetch file metadata from Google Drive."""
        if item_id in self._item_cache:
            return self._item_cache[item_id]

        params = {
            "fields": "id,name,mimeType,size,imageMediaMetadata,thumbnailLink,webContentLink,createdTime,sha256Checksum",
        }
        resp = await self._request_with_retry("GET", f"{GOOGLE_DRIVE_API_BASE}/files/{item_id}", params=params)
        f = resp.json()
        f_id = f.get("id", item_id)
        meta = f.get("imageMediaMetadata", {})

        item = SourceItem(
            id=f_id,
            name=f.get("name", f"drive_{f_id[:8]}.jpg"),
            mime_type=f.get("mimeType", "image/jpeg"),
            size_bytes=int(f["size"]) if "size" in f and f["size"] else None,
            width=int(meta["width"]) if "width" in meta and meta["width"] else None,
            height=int(meta["height"]) if "height" in meta and meta["height"] else None,
            thumbnail_url=f.get("thumbnailLink"),
            download_url=f.get("webContentLink"),
            sha256_hash=f.get("sha256Checksum"),
            created_at=f.get("createdTime"),
            metadata=meta,
        )
        self._item_cache[f_id] = item
        return item

    async def fetch_thumbnail(self, item_id: str, max_size: int = 256) -> Optional[bytes]:
        """Fetch thumbnail bytes for Google Drive file."""
        try:
            item = await self.get_metadata(item_id)
            if item.thumbnail_url:
                resp = await self._request_with_retry("GET", item.thumbnail_url, is_binary=True)
                return resp.content
            # Fallback: download image and resize
            data = await self.download_image(item_id)
            import io
            from PIL import Image

            def _resize() -> Optional[bytes]:
                try:
                    with Image.open(io.BytesIO(data)) as img:
                        img = img.convert("RGB")
                        img.thumbnail((max_size, max_size))
                        buf = io.BytesIO()
                        img.save(buf, format="JPEG", quality=85)
                        return buf.getvalue()
                except Exception:
                    return None

            return await asyncio.to_thread(_resize)
        except Exception as exc:
            logger.warning(f"Failed fetching thumbnail for Google Drive item {item_id}: {exc}")
            return None

    async def download_image(self, item_id: str) -> bytes:
        """Download raw binary file content from Google Drive."""
        url = f"{GOOGLE_DRIVE_API_BASE}/files/{item_id}?alt=media"
        resp = await self._request_with_retry("GET", url, is_binary=True)
        return resp.content
