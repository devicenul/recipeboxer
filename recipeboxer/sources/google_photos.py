"""Google Photos Library API integration with OAuth2, category search, and resilient streaming."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Dict, List, Optional, Union
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

GOOGLE_PHOTOS_API_BASE = "https://photoslibrary.googleapis.com/v1"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"


class GooglePhotosProvider(BaseSourceProvider):
    """Google Photos Library API provider supporting OAuth2, albums, and category filters."""

    provider_id: str = "google_photos"
    name: str = "Google Photos"
    description: str = "Import recipe card scans from Google Photos albums or category search"
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
        category_filters=["FOOD", "DOCUMENTS", "RECEIPTS", "ALL"],
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
        """Return the active HTTP client or create a new instance."""
        if self._custom_client is not None:
            return self._custom_client
        return httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))

    async def _refresh_access_token(self, failed_token: Optional[str] = None) -> str:
        """Exchange OAuth2 refresh_token for a fresh access_token."""
        async with self._token_lock:
            # If another concurrent task already refreshed the token while we waited:
            if failed_token and self._access_token and self._access_token != failed_token:
                return self._access_token

            if not self.config.refresh_token:
                if self._access_token:
                    return self._access_token
                raise ValueError("No OAuth2 access_token or refresh_token provided for Google Photos")

            client = await self._get_client()
            should_close = self._custom_client is None
            try:
                payload = {
                    "client_id": self.config.client_id or "recipeboxer-client",
                    "client_secret": self.config.client_secret or "recipeboxer-secret",
                    "refresh_token": self.config.refresh_token,
                    "grant_type": "refresh_token",
                }
                token_url = self.config.token_uri or GOOGLE_OAUTH_TOKEN_URL
                resp = await client.post(token_url, data=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    new_token = data.get("access_token")
                    if new_token:
                        self._access_token = new_token
                        self.config.auth_token = new_token
                        logger.info("Successfully refreshed Google Photos OAuth2 access token")
                        return new_token
                raise PermissionError(f"OAuth2 token refresh failed: HTTP {resp.status_code} {resp.text}")
            finally:
                if should_close:
                    await client.aclose()

    async def _ensure_token(self) -> str:
        """Ensure a valid access token is available."""
        if self._access_token:
            return self._access_token
        if self.config.refresh_token:
            return await self._refresh_access_token()
        raise PermissionError("Google Photos requires authentication (auth_token or refresh_token).")

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        max_retries: int = 4,
        is_binary: bool = False,
    ) -> httpx.Response:
        """Execute HTTP request with exponential backoff and automatic OAuth2 token renewal."""
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

                    # 1. Check for 401 Unauthorized -> Refresh token and retry
                    if resp.status_code == 401 and self.config.refresh_token and attempt < max_retries - 1:
                        logger.info("Access token expired (401), refreshing token...")
                        token = await self._refresh_access_token(failed_token=token)
                        headers["Authorization"] = f"Bearer {token}"
                        continue

                    # 2. Check for 429 / 503 Rate Limiting -> Exponential backoff
                    if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                        jitter = random.uniform(0.1, 0.3)
                        sleep_secs = backoff + jitter
                        logger.warning(
                            f"Google Photos request rate-limited ({resp.status_code}). Backing off {sleep_secs:.2f}s..."
                        )
                        await asyncio.sleep(sleep_secs)
                        backoff *= 2.0
                        continue

                    if resp.status_code >= 400:
                        raise httpx.HTTPStatusError(
                            f"Google Photos API error {resp.status_code}: {resp.text}",
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
            raise RuntimeError(f"Google Photos API request failed after {max_retries} attempts.")
        finally:
            if should_close:
                await client.aclose()

    async def test_connection(self) -> bool:
        """Test authentication against Google Photos API."""
        try:
            # Test listing 1 album or verifying token
            resp = await self._request_with_retry("GET", f"{GOOGLE_PHOTOS_API_BASE}/albums", params={"pageSize": 1})
            return resp.status_code == 200
        except Exception as exc:
            logger.debug(f"Google Photos test_connection failed: {exc}")
            return False

    async def list_containers(
        self,
        page_size: int = 50,
        page_token: Optional[str] = None,
        parent_id: Optional[str] = None,
        **kwargs: Any,
    ) -> ListContainersResponse:
        """List Google Photos albums."""
        params: Dict[str, Any] = {"pageSize": min(page_size, 50)}
        if page_token:
            params["pageToken"] = page_token

        resp = await self._request_with_retry("GET", f"{GOOGLE_PHOTOS_API_BASE}/albums", params=params)
        data = resp.json()

        albums_raw = data.get("albums", [])
        containers: List[SourceContainer] = []
        for a in albums_raw:
            album_id = a.get("id", "")
            title = a.get("title", "Untitled Album")
            item_count = int(a.get("mediaItemsCount", 0)) if a.get("mediaItemsCount") else None
            cover_base = a.get("coverPhotoBaseUrl")
            cover_thumb = f"{cover_base}=w300-h300" if cover_base else None

            containers.append(
                SourceContainer(
                    id=album_id,
                    name=title,
                    item_count=item_count,
                    cover_image_url=cover_thumb,
                    container_type="album",
                    metadata={
                        "productUrl": a.get("productUrl"),
                        "coverPhotoBaseUrl": cover_base,
                    },
                )
            )

        next_token = data.get("nextPageToken")
        return ListContainersResponse(
            containers=containers,
            next_page_token=next_token,
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
        """Search or list Google Photos media items with album or category filtering."""
        body: Dict[str, Any] = {"pageSize": min(page_size, 100)}
        if page_token:
            body["pageToken"] = page_token

        if container_id:
            body["albumId"] = container_id
        else:
            cat = category or self.config.category_filter
            if cat and cat.upper() not in ("ALL", "NONE", ""):
                body["filters"] = {
                    "categoryFilter": {
                        "includedMediaCategories": [cat.upper()]
                    }
                }

        resp = await self._request_with_retry("POST", f"{GOOGLE_PHOTOS_API_BASE}/mediaItems:search", json_data=body)
        data = resp.json()
        media_items_raw = data.get("mediaItems", [])

        items: List[SourceItem] = []
        for m in media_items_raw:
            m_id = m.get("id", "")
            fname = m.get("filename", f"photo_{m_id[:8]}.jpg")
            mime = m.get("mimeType", "image/jpeg")
            base_url = m.get("baseUrl", "")
            meta = m.get("mediaMetadata", {})

            width = int(meta["width"]) if "width" in meta and meta["width"] else None
            height = int(meta["height"]) if "height" in meta and meta["height"] else None
            created_at = meta.get("creationTime")

            thumb_url = f"{base_url}=w256-h256" if base_url else None
            download_url = f"{base_url}=d" if base_url else None

            s_item = SourceItem(
                id=m_id,
                name=fname,
                mime_type=mime,
                width=width,
                height=height,
                thumbnail_url=thumb_url,
                download_url=download_url,
                container_id=container_id,
                created_at=created_at,
                metadata={"baseUrl": base_url, "mediaMetadata": meta},
            )
            items.append(s_item)
            self._item_cache[m_id] = s_item

        next_tok = data.get("nextPageToken")
        return ListItemsResponse(
            items=items,
            next_page_token=next_tok,
            total_count=None,
        )

    async def get_metadata(self, item_id: str) -> SourceItem:
        """Fetch media item metadata by ID."""
        if item_id in self._item_cache:
            return self._item_cache[item_id]

        resp = await self._request_with_retry("GET", f"{GOOGLE_PHOTOS_API_BASE}/mediaItems/{item_id}")
        m = resp.json()
        m_id = m.get("id", item_id)
        fname = m.get("filename", f"photo_{m_id[:8]}.jpg")
        mime = m.get("mimeType", "image/jpeg")
        base_url = m.get("baseUrl", "")
        meta = m.get("mediaMetadata", {})

        width = int(meta["width"]) if "width" in meta and meta["width"] else None
        height = int(meta["height"]) if "height" in meta and meta["height"] else None
        created_at = meta.get("creationTime")

        s_item = SourceItem(
            id=m_id,
            name=fname,
            mime_type=mime,
            width=width,
            height=height,
            thumbnail_url=f"{base_url}=w256-h256" if base_url else None,
            download_url=f"{base_url}=d" if base_url else None,
            created_at=created_at,
            metadata={"baseUrl": base_url, "mediaMetadata": meta},
        )
        self._item_cache[m_id] = s_item
        return s_item

    async def fetch_thumbnail(self, item_id: str, max_size: int = 256) -> Optional[bytes]:
        """Fetch low-res thumbnail image bytes directly from Google Photos baseUrl."""
        try:
            item = await self.get_metadata(item_id)
            base_url = item.metadata.get("baseUrl")
            if not base_url:
                return None

            thumb_url = f"{base_url}=w{max_size}-h{max_size}"
            resp = await self._request_with_retry("GET", thumb_url, is_binary=True)
            return resp.content
        except Exception as exc:
            logger.warning(f"Failed fetching thumbnail for Google Photos item {item_id}: {exc}")
            return None

    async def download_image(self, item_id: str) -> bytes:
        """Download high-resolution image bytes using baseUrl=d or full width/height."""
        item = await self.get_metadata(item_id)
        base_url = item.metadata.get("baseUrl")
        if not base_url:
            raise ValueError(f"No baseUrl found in metadata for Google Photos item {item_id}")

        # Try =d (direct download of original bytes)
        download_url = f"{base_url}=d"
        try:
            resp = await self._request_with_retry("GET", download_url, is_binary=True)
            return resp.content
        except Exception as exc:
            # Fallback to high resolution dimensions if =d is restricted
            w = item.width or 2048
            h = item.height or 2048
            fb_url = f"{base_url}=w{w}-h{h}"
            logger.debug(f"Direct download fallback to resolution {w}x{h}: {exc}")
            resp = await self._request_with_retry("GET", fb_url, is_binary=True)
            return resp.content
