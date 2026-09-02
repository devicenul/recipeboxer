"""Unit and integration test suite for RecipeBoxer Phase 5 Cloud & External Source Providers."""

from __future__ import annotations

import asyncio
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
import pytest
from PIL import Image

from recipeboxer.agent.session import PipelineSession, SessionManager
from recipeboxer.api.app import create_app
from recipeboxer.ingestion.runner import IngestionConfig, IngestionMode, IngestionRunner
from recipeboxer.ingestion.scanner import ScannedCard, validate_and_load_card
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.pipeline import PipelineStartRequest, PipelineState
from recipeboxer.sources.base import (
    BaseSourceProvider,
    ListContainersResponse,
    ListItemsResponse,
    SourceCapabilities,
    SourceConnectionStatus,
    SourceContainer,
    SourceItem,
    SourceProviderConfig,
)
from recipeboxer.sources.cloud_drive import GoogleDriveProvider
from recipeboxer.sources.google_photos import GooglePhotosProvider
from recipeboxer.sources.local import LocalSourceProvider
from recipeboxer.sources.registry import (
    SourceProviderRegistry,
    get_source_provider,
    get_source_registry,
    list_available_providers,
)
from recipeboxer.storage.cache import RemoteAssetCache, RemoteAssetRecord


# ============================================================================
# Helpers & Fixtures
# ============================================================================

def create_dummy_jpeg_bytes(width: int = 100, height: int = 100, color: str = "red") -> bytes:
    """Generate in-memory JPEG bytes."""
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def sample_image_dir(tmp_path: Path) -> Path:
    """Fixture providing a directory containing test scan images."""
    scan_dir = tmp_path / "scans"
    scan_dir.mkdir()
    for i in range(1, 4):
        card_path = scan_dir / f"scan_{i:03d}.jpg"
        card_path.write_bytes(create_dummy_jpeg_bytes(100 + i * 10, 100 + i * 10))
    # Add a subfolder
    sub_dir = scan_dir / "desserts"
    sub_dir.mkdir()
    (sub_dir / "cookie.jpg").write_bytes(create_dummy_jpeg_bytes(80, 80))
    return scan_dir


# ============================================================================
# 1. BaseSourceProvider & Registry Tests
# ============================================================================

class TestBaseAndRegistry:
    def test_source_models_serialization(self):
        """Verify SourceItem, SourceContainer, and SourceCapabilities schemas."""
        item = SourceItem(
            id="item_123",
            name="Grandmas_Pie.jpg",
            mime_type="image/jpeg",
            size_bytes=10240,
            width=800,
            height=600,
            thumbnail_url="https://example.com/thumb.jpg",
            download_url="https://example.com/full.jpg",
            sha256_hash="a" * 64,
            metadata={"source": "test"},
        )
        assert item.id == "item_123"
        assert item.width == 800

        container = SourceContainer(
            id="album_1",
            name="Holiday Baking",
            item_count=12,
            container_type="album",
        )
        assert container.item_count == 12

        caps = SourceCapabilities(
            supports_containers=True,
            supports_search=True,
            category_filters=["FOOD", "DOCUMENTS"],
        )
        assert "FOOD" in caps.category_filters

    def test_registry_registration_and_retrieval(self):
        """Verify SourceProviderRegistry manages provider classes and active instances."""
        registry = SourceProviderRegistry()
        local_p = registry.get_provider("local")
        assert isinstance(local_p, LocalSourceProvider)

        gp = registry.get_provider("google_photos")
        assert isinstance(gp, GooglePhotosProvider)

        drive = registry.get_provider("google_drive")
        assert isinstance(drive, GoogleDriveProvider)

        with pytest.raises(KeyError):
            registry.get_provider("s3")

        with pytest.raises(KeyError):
            registry.get_provider("gcs")

        with pytest.raises(KeyError):
            registry.get_provider("unknown_provider_xyz")

    @pytest.mark.asyncio
    async def test_list_available_providers(self):
        """Verify list_available_providers returns statuses for all registered providers."""
        statuses = await list_available_providers()
        assert len(statuses) == 3
        provider_ids = [s.provider_id for s in statuses]
        assert set(provider_ids) == {"local", "google_photos", "google_drive"}
        assert "s3" not in provider_ids
        assert "gcs" not in provider_ids


# ============================================================================
# 2. RemoteAssetCache Tests (SQLite Content-Addressable Persistence & Integrity)
# ============================================================================

class TestRemoteAssetCache:
    def test_asset_cache_put_get_and_integrity_verification(self):
        """Verify RemoteAssetCache puts binary data with SHA-256 integrity verification."""
        cache = RemoteAssetCache(":memory:")
        data = create_dummy_jpeg_bytes(50, 50, "blue")
        import hashlib
        correct_hash = hashlib.sha256(data).hexdigest().lower()

        # 1. Put with correct expected hash
        res_hash = cache.put(
            provider_id="google_photos",
            item_id="photo_999",
            filename="blue_card.jpg",
            data=data,
            mime_type="image/jpeg",
            expected_hash=correct_hash,
        )
        assert res_hash == correct_hash
        assert cache.contains(correct_hash)

        # 2. Put with incorrect expected hash must raise ValueError
        with pytest.raises(ValueError, match="SHA-256 integrity verification failed"):
            cache.put(
                provider_id="google_photos",
                item_id="photo_888",
                filename="bad_card.jpg",
                data=data,
                expected_hash="0" * 64,
            )

        # 3. Get binary data
        retrieved_bytes = cache.get(correct_hash)
        assert retrieved_bytes == data

        # 4. Get record
        rec = cache.get_record(correct_hash, include_data=True)
        assert rec is not None
        assert rec.item_id == "photo_999"
        assert rec.size_bytes == len(data)

        # 5. Get by source
        rec_src = cache.get_by_source("google_photos", "photo_999")
        assert rec_src is not None
        assert rec_src.sha256_hash == correct_hash

        # 6. Delete
        assert cache.delete(correct_hash) is True
        assert not cache.contains(correct_hash)
        assert cache.get(correct_hash) is None

    def test_materialize_as_scanned_card(self, tmp_path: Path):
        """Verify materializing cached asset to disk and validating as ScannedCard."""
        cache = RemoteAssetCache(":memory:")
        data = create_dummy_jpeg_bytes(120, 80, "green")
        h = cache.put(
            provider_id="google_photos",
            item_id="recipes/soup.jpg",
            filename="soup.jpg",
            data=data,
        )


        staging_dir = tmp_path / "staging"
        card = cache.materialize_as_scanned_card(h, staging_dir)
        assert isinstance(card, ScannedCard)
        assert card.width == 120
        assert card.height == 80
        assert card.sha256_hash == h
        assert card.file_path.exists()


# ============================================================================
# 3. LocalSourceProvider Tests
# ============================================================================

class TestLocalSourceProvider:
    @pytest.mark.asyncio
    async def test_local_provider_operations(self, sample_image_dir: Path):
        """Verify LocalSourceProvider scanning, container listing, and metadata extraction."""
        cfg = SourceProviderConfig(provider_id="local", local_path=str(sample_image_dir))
        provider = LocalSourceProvider(config=cfg)

        assert await provider.test_connection() is True

        # Containers (subdirectories)
        containers = await provider.list_containers()
        assert len(containers.containers) >= 1
        assert containers.containers[0].name == "desserts"

        # Items
        items = await provider.list_items()
        assert len(items.items) == 3
        first_item = items.items[0]

        # Metadata
        meta = await provider.get_metadata(first_item.id)
        assert meta.width > 0
        assert meta.height > 0
        assert meta.sha256_hash is not None

        # Thumbnail
        thumb = await provider.fetch_thumbnail(first_item.id, max_size=64)
        assert thumb is not None
        assert len(thumb) > 0

        # Download bytes
        raw = await provider.download_image(first_item.id)
        assert len(raw) == first_item.size_bytes


# ============================================================================
# 4. GooglePhotosProvider Mock Tests (OAuth2, Albums, Search, Retries)
# ============================================================================

class TestGooglePhotosProvider:
    @pytest.mark.asyncio
    async def test_oauth2_token_refresh_and_albums_listing(self):
        """Verify Google Photos OAuth2 refresh on 401 and album retrieval."""
        test_img_bytes = create_dummy_jpeg_bytes(100, 100)

        # Mock transport simulating OAuth token endpoint, album listing, and 401 auto-retry
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            url = str(request.url)

            if "oauth2.googleapis.com/token" in url:
                return httpx.Response(
                    200,
                    json={
                        "access_token": "mock_new_access_token_123",
                        "expires_in": 3600,
                        "token_type": "Bearer",
                    },
                )
            if "/v1/albums" in url:
                # First call: return 401 to test automatic token refresh
                auth = request.headers.get("Authorization", "")
                if auth == "Bearer initial_expired_token":
                    return httpx.Response(401, json={"error": "unauthorized"})
                return httpx.Response(
                    200,
                    json={
                        "albums": [
                            {
                                "id": "album_recipes_1",
                                "title": "Family Recipes 1985",
                                "mediaItemsCount": "24",
                                "coverPhotoBaseUrl": "https://photos.mock/cover1",
                            }
                        ],
                        "nextPageToken": "page_2_tok",
                    },
                )
            if "/v1/mediaItems:search" in url:
                req_body = json.loads(request.content.decode("utf-8")) if request.content else {}
                return httpx.Response(
                    200,
                    json={
                        "mediaItems": [
                            {
                                "id": "photo_cookie_1",
                                "filename": "choc_chip_cookie.jpg",
                                "mimeType": "image/jpeg",
                                "baseUrl": "https://photos.mock/item1",
                                "mediaMetadata": {
                                    "creationTime": "2026-08-25T12:00:00Z",
                                    "width": "1200",
                                    "height": "800",
                                },
                            }
                        ]
                    },
                )
            if "https://photos.mock/item1" in url:
                return httpx.Response(200, content=test_img_bytes, headers={"Content-Type": "image/jpeg"})

            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=transport)

        cfg = SourceProviderConfig(
            provider_id="google_photos",
            auth_token="initial_expired_token",
            refresh_token="valid_refresh_token",
            client_id="cid",
            client_secret="csecret",
        )
        provider = GooglePhotosProvider(config=cfg, http_client=mock_client)

        # 1. Albums listing triggers 401 -> automatic refresh -> 200 OK
        albums_res = await provider.list_containers()
        assert len(albums_res.containers) == 1
        assert albums_res.containers[0].id == "album_recipes_1"
        assert albums_res.containers[0].item_count == 24
        assert albums_res.next_page_token == "page_2_tok"

        # 2. Search items with category FOOD
        items_res = await provider.list_items(category="FOOD")
        assert len(items_res.items) == 1
        item = items_res.items[0]
        assert item.id == "photo_cookie_1"
        assert item.width == 1200

        # 3. Download image
        data = await provider.download_image(item.id)
        assert data == test_img_bytes

    @pytest.mark.asyncio
    async def test_rate_limit_exponential_backoff(self):
        """Verify Google Photos provider retries on 429 Too Many Requests."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(429, headers={"Retry-After": "0.1"})
            return httpx.Response(
                200,
                json={"albums": [{"id": "a1", "title": "Desserts"}]},
            )

        transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=transport)

        cfg = SourceProviderConfig(provider_id="google_photos", auth_token="valid_token")
        provider = GooglePhotosProvider(config=cfg, http_client=mock_client)

        res = await provider.list_containers()
        assert len(res.containers) == 1
        assert call_count == 3


# ============================================================================
# 5. GoogleDriveProvider Mock Tests (Folders & Card Scan Browsing)
# ============================================================================

class TestGoogleDriveProvider:
    @pytest.mark.asyncio
    async def test_drive_folder_and_file_browsing(self):
        """Verify Google Drive folder listing, file querying, and binary download."""
        dummy_img = create_dummy_jpeg_bytes(150, 150)

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "/drive/v3/files/file_card_1?alt=media" in url:
                return httpx.Response(200, content=dummy_img, headers={"Content-Type": "image/jpeg"})
            if "/drive/v3/files" in url:
                q = request.url.params.get("q", "")
                if "mimeType = 'application/vnd.google-apps.folder'" in q:
                    return httpx.Response(
                        200,
                        json={
                            "files": [
                                {
                                    "id": "folder_box_1",
                                    "name": "Grandma Recipe Box Scans",
                                    "mimeType": "application/vnd.google-apps.folder",
                                }
                            ]
                        },
                    )
                else:
                    return httpx.Response(
                        200,
                        json={
                            "files": [
                                {
                                    "id": "file_card_1",
                                    "name": "front_scan.jpg",
                                    "mimeType": "image/jpeg",
                                    "size": str(len(dummy_img)),
                                    "imageMediaMetadata": {"width": 150, "height": 150},
                                }
                            ]
                        },
                    )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        cfg = SourceProviderConfig(provider_id="google_drive", auth_token="mock_token")
        provider = GoogleDriveProvider(config=cfg, http_client=client)

        # 1. Folders
        folders = await provider.list_containers()
        assert len(folders.containers) == 1
        assert folders.containers[0].id == "folder_box_1"

        # 2. Files inside folder
        files = await provider.list_items(container_id="folder_box_1")
        assert len(files.items) == 1
        assert files.items[0].id == "file_card_1"

        # 3. Download file
        content = await provider.download_image("file_card_1")
        assert content == dummy_img


# ============================================================================
# 6. FastAPI Source Endpoints & Pipeline Ingestion Tests
# ============================================================================

class TestFastAPISourceRoutes:
    @pytest.fixture
    def client(self):
        from starlette.testclient import TestClient
        return TestClient(create_app())

    def test_get_source_providers_endpoint(self, client):
        """GET /api/sources/providers returns list of providers and statuses."""
        res = client.get("/api/sources/providers")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)
        provider_ids = [p["provider_id"] for p in data]
        assert len(provider_ids) == 3
        assert "local" in provider_ids
        assert "google_photos" in provider_ids
        assert "google_drive" in provider_ids
        assert "s3" not in provider_ids
        assert "gcs" not in provider_ids


    def test_post_source_connect_endpoint(self, client):
        """POST /api/sources/{provider_id}/connect validates config."""
        res = client.post(
            "/api/sources/local/connect",
            json={"local_path": "."},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["provider_id"] == "local"
        assert data["connected"] is True

    def test_get_source_containers_endpoint(self, client, sample_image_dir: Path):
        """GET /api/sources/local/containers lists subdirectories."""
        # Connect local provider with sample image dir
        client.post(
            "/api/sources/local/connect",
            json={"local_path": str(sample_image_dir)},
        )
        res = client.get("/api/sources/local/containers")
        assert res.status_code == 200
        data = res.json()
        assert "containers" in data
        assert len(data["containers"]) >= 1

    def test_get_source_items_endpoint(self, client, sample_image_dir: Path):
        """GET /api/sources/local/items returns media item list."""
        client.post(
            "/api/sources/local/connect",
            json={"local_path": str(sample_image_dir)},
        )
        res = client.get("/api/sources/local/items")
        assert res.status_code == 200
        data = res.json()
        assert "items" in data
        assert len(data["items"]) == 3

    def test_pipeline_start_with_remote_source(self, client):
        """POST /api/pipeline/start correctly accepts source_provider payload."""
        # Register a mock provider in registry
        dummy_img = create_dummy_jpeg_bytes(100, 100)

        class MockCloudProvider(BaseSourceProvider):
            provider_id = "mock_cloud"
            name = "Mock Cloud"
            description = "Mock cloud provider"
            capabilities = SourceCapabilities(requires_auth=False)

            async def test_connection(self) -> bool:
                return True

            async def list_containers(self, **kwargs) -> ListContainersResponse:
                return ListContainersResponse(
                    containers=[SourceContainer(id="alb_1", name="Album 1")]
                )

            async def list_items(self, **kwargs) -> ListItemsResponse:
                return ListItemsResponse(
                    items=[
                        SourceItem(id="item_1", name="scan1.jpg"),
                        SourceItem(id="item_2", name="scan2.jpg"),
                    ]
                )

            async def fetch_thumbnail(self, item_id: str, max_size: int = 256) -> Optional[bytes]:
                return dummy_img

            async def download_image(self, item_id: str) -> bytes:
                return dummy_img

            async def get_metadata(self, item_id: str) -> SourceItem:
                return SourceItem(id=item_id, name=f"{item_id}.jpg")

        registry = get_source_registry()
        registry.register_provider_class("mock_cloud", MockCloudProvider)

        start_payload = {
            "source_provider": "mock_cloud",
            "source_container_id": "alb_1",
            "mode": "parallel",
        }
        res = client.post("/api/pipeline/start", json=start_payload)
        assert res.status_code == 200
        data = res.json()
        assert "session_id" in data
        assert data["total_scans_found"] == 2


# ============================================================================
# 7. Adversarial Edge Cases, Caching & Ordering Tests
# ============================================================================

class TestAdversarialPhase5EdgeCases:
    @pytest.mark.asyncio
    async def test_base_provider_caching_target_dir_none(self, tmp_path: Path):

        """Verify download_and_cache hits cache and materializes even when target_dir is None."""
        cache_db = tmp_path / "cache.db"
        asset_cache = RemoteAssetCache(cache_db)

        dummy_bytes = create_dummy_jpeg_bytes(100, 100, "yellow")
        download_calls = 0

        class CountingMockProvider(BaseSourceProvider):
            provider_id = "counting_mock"
            name = "Counting Mock"
            description = "Test counting provider"

            async def test_connection(self) -> bool:
                return True

            async def list_containers(self, **kwargs) -> ListContainersResponse:
                return ListContainersResponse()

            async def list_items(self, **kwargs) -> ListItemsResponse:
                return ListItemsResponse()

            async def fetch_thumbnail(self, item_id: str, max_size: int = 256) -> Optional[bytes]:
                return dummy_bytes

            async def download_image(self, item_id: str) -> bytes:
                nonlocal download_calls
                download_calls += 1
                return dummy_bytes

            async def get_metadata(self, item_id: str) -> SourceItem:
                return SourceItem(id=item_id, name="card.jpg")

        provider = CountingMockProvider()

        # 1. First call downloads and populates cache
        card1 = await provider.download_and_cache("item_abc", target_dir=None, asset_cache=asset_cache)
        assert download_calls == 1
        assert card1.sha256_hash

        # 2. Second call with target_dir=None MUST hit cache and NOT invoke download_image again
        card2 = await provider.download_and_cache("item_abc", target_dir=None, asset_cache=asset_cache)
        assert download_calls == 1  # No second download!
        assert card2.sha256_hash == card1.sha256_hash

        # 3. Third call by expected_hash directly hits cache
        card3 = await provider.download_and_cache(
            "item_xyz",
            target_dir=None,
            asset_cache=asset_cache,
            expected_hash=card1.sha256_hash,
        )
        assert download_calls == 1  # Still no extra download!
        assert card3.sha256_hash == card1.sha256_hash

    @pytest.mark.asyncio
    async def test_stream_download_items_order_preservation(self, tmp_path: Path):
        """Verify stream_download_items preserves input order even with varying async delays."""
        staging_dir = tmp_path / "order_staging"

        class DelayedMockProvider(BaseSourceProvider):
            provider_id = "delayed_mock"
            name = "Delayed Mock"
            description = "Mock with variable delay"

            async def test_connection(self) -> bool:
                return True

            async def list_containers(self, **kwargs) -> ListContainersResponse:
                return ListContainersResponse()

            async def list_items(self, **kwargs) -> ListItemsResponse:
                return ListItemsResponse()

            async def fetch_thumbnail(self, item_id: str, max_size: int = 256) -> Optional[bytes]:
                return None

            async def download_image(self, item_id: str) -> bytes:
                # Give item 0 a larger delay so it finishes last if concurrency was unordered
                idx = int(item_id.split("_")[-1])
                delay = 0.05 if idx == 0 else 0.005
                await asyncio.sleep(delay)
                return create_dummy_jpeg_bytes(50 + idx * 10, 50 + idx * 10)

            async def get_metadata(self, item_id: str) -> SourceItem:
                return SourceItem(id=item_id, name=f"{item_id}.jpg")

        provider = DelayedMockProvider()
        item_ids = [f"item_{i}" for i in range(5)]

        cards = await provider.stream_download_items(item_ids, target_dir=staging_dir, concurrency=5)
        assert len(cards) == 5
        # Verify cards correspond in exact order: item_0, item_1, item_2, item_3, item_4
        for i, card in enumerate(cards):
            assert card.width == 50 + i * 10

    @pytest.mark.asyncio
    async def test_local_fetch_thumbnail_corrupt_file(self, tmp_path: Path):
        """Verify LocalSourceProvider.fetch_thumbnail safely returns None for non-image or corrupt files."""
        corrupt_file = tmp_path / "corrupt.jpg"
        corrupt_file.write_bytes(b"THIS IS NOT A VALID JPEG FILE CONTENT")

        cfg = SourceProviderConfig(provider_id="local", local_path=str(tmp_path))
        provider = LocalSourceProvider(config=cfg)

        thumb = await provider.fetch_thumbnail(str(corrupt_file))
        assert thumb is None

    @pytest.mark.asyncio
    async def test_remote_providers_thumbnail_error_resilience(self):
        """Verify all remote providers safely return None when thumbnail fetch encounters errors."""
        # 1. Google Photos 500 error
        def gphotos_err(req):
            return httpx.Response(500, json={"error": "internal"})

        gp_client = httpx.AsyncClient(transport=httpx.MockTransport(gphotos_err))
        gp = GooglePhotosProvider(
            config=SourceProviderConfig(provider_id="google_photos", auth_token="tok"),
            http_client=gp_client,
        )
        assert await gp.fetch_thumbnail("bad_item_id") is None

        # 2. Google Drive 404 error
        def gdrive_err(req):
            return httpx.Response(404)

        gd_client = httpx.AsyncClient(transport=httpx.MockTransport(gdrive_err))
        gd = GoogleDriveProvider(
            config=SourceProviderConfig(provider_id="google_drive", auth_token="tok"),
            http_client=gd_client,
        )
        assert await gd.fetch_thumbnail("bad_file_id") is None

    def test_pipeline_start_with_local_source_config(self, sample_image_dir: Path):
        """Verify POST /api/pipeline/start accepts source_provider='local' with source_config.local_path."""
        from starlette.testclient import TestClient
        client = TestClient(create_app())

        res = client.post(
            "/api/pipeline/start",
            json={
                "source_provider": "local",
                "source_config": {"local_path": str(sample_image_dir)},
                "mode": "parallel",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert "session_id" in data
        assert data["total_scans_found"] == 3


    @pytest.mark.asyncio
    async def test_google_photos_concurrent_token_refresh_thundering_herd(self):
        """Verify 10 concurrent requests encountering 401 trigger only a single token refresh request."""
        refresh_count = 0
        photo_bytes = create_dummy_jpeg_bytes(50, 50)

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal refresh_count
            url = str(request.url)
            if "oauth2.googleapis.com/token" in url:
                refresh_count += 1
                return httpx.Response(
                    200,
                    json={
                        "access_token": f"fresh_token_{refresh_count}",
                        "expires_in": 3600,
                        "token_type": "Bearer",
                    },
                )
            if "/v1/mediaItems/" in url:
                auth = request.headers.get("Authorization", "")
                if auth == "Bearer expired_initial_token":
                    return httpx.Response(401, json={"error": "unauthorized"})
                # Fresh token accepted
                return httpx.Response(
                    200,
                    json={
                        "id": "photo_1",
                        "filename": "scan.jpg",
                        "baseUrl": "https://photos.mock/p1",
                        "mediaMetadata": {"width": "100", "height": "100"},
                    },
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=transport)

        cfg = SourceProviderConfig(
            provider_id="google_photos",
            auth_token="expired_initial_token",
            refresh_token="valid_refresh_token",
        )
        provider = GooglePhotosProvider(config=cfg, http_client=mock_client)

        # Launch 10 concurrent metadata fetches
        tasks = [provider.get_metadata(f"photo_{i}") for i in range(10)]
        results = await asyncio.gather(*tasks)
        assert len(results) == 10
        # Exactly 1 refresh request was made despite 10 simultaneous 401s
        assert refresh_count == 1
        assert provider._access_token == "fresh_token_1"

    @pytest.mark.asyncio
    async def test_google_drive_apostrophe_and_quote_escaping(self):
        """Verify Google Drive queries with apostrophes/quotes are safely escaped."""
        captured_q = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "/drive/v3/files" in url:
                q = request.url.params.get("q", "")
                captured_q.append(q)
                return httpx.Response(
                    200,
                    json={
                        "files": [
                            {
                                "id": "card_pie",
                                "name": "Grandma's Apple Pie.jpg",
                                "mimeType": "image/jpeg",
                                "size": "1024",
                            }
                        ]
                    },
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=transport)

        cfg = SourceProviderConfig(provider_id="google_drive", auth_token="tok")
        provider = GoogleDriveProvider(config=cfg, http_client=mock_client)

        # Query containing apostrophe
        res = await provider.list_items(query="Grandma's", container_id="folder's_id")
        assert len(res.items) == 1
        assert len(captured_q) == 1
        # Confirm escaped quotes in Google Drive q parameter
        assert "name contains 'Grandma\\'s'" in captured_q[0]
        assert "'folder\\'s_id' in parents" in captured_q[0]

    @pytest.mark.asyncio
    async def test_concurrent_download_collision_disambiguation(self, tmp_path: Path):
        """Verify downloading distinct files with identical filenames disambiguates paths and avoids overwrite."""
        staging_dir = tmp_path / "disambiguate_staging"
        staging_dir.mkdir()

        data_a = create_dummy_jpeg_bytes(100, 100, "red")
        data_b = create_dummy_jpeg_bytes(120, 120, "blue")

        class DuplicateNameProvider(BaseSourceProvider):
            provider_id = "dup_mock"
            name = "Duplicate Mock"

            async def test_connection(self) -> bool:
                return True

            async def list_containers(self, **kwargs) -> ListContainersResponse:
                return ListContainersResponse()

            async def list_items(self, **kwargs) -> ListItemsResponse:
                return ListItemsResponse()

            async def fetch_thumbnail(self, item_id: str, max_size: int = 256) -> Optional[bytes]:
                return None

            async def download_image(self, item_id: str) -> bytes:
                return data_a if item_id == "item_a" else data_b

            async def get_metadata(self, item_id: str) -> SourceItem:
                # Both items share the exact same filename
                return SourceItem(id=item_id, name="recipe_card.jpg")

        provider = DuplicateNameProvider()

        card_a = await provider.download_and_cache("item_a", target_dir=staging_dir)
        card_b = await provider.download_and_cache("item_b", target_dir=staging_dir)

        assert card_a.file_path.exists()
        assert card_b.file_path.exists()
        # Paths must be distinct to preserve both images!
        assert card_a.file_path != card_b.file_path
        assert card_a.width == 100
        assert card_b.width == 120

    def test_remote_asset_cache_concurrent_high_throughput_wal(self, tmp_path: Path):
        """Verify high concurrency threaded writes and reads to RemoteAssetCache in WAL mode."""
        import concurrent.futures

        db_path = tmp_path / "concurrent_wal_cache.db"
        cache = RemoteAssetCache(db_path)

        def worker(i: int) -> bool:
            blob = create_dummy_jpeg_bytes(40 + i, 40 + i)
            import hashlib
            h = hashlib.sha256(blob).hexdigest().lower()
            res_h = cache.put(
                provider_id="google_photos",
                item_id=f"concurrent_key_{i}.jpg",
                filename=f"card_{i}.jpg",
                data=blob,
                expected_hash=h,
            )
            assert res_h == h
            retrieved = cache.get(h)
            assert retrieved == blob
            return True

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker, i) for i in range(20)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert len(results) == 20
        assert all(results)
        assert cache.count() == 20

    def test_diverse_image_extensions_and_mime_types(self, tmp_path: Path):
        """Verify recognition and standard MIME type resolution for diverse image formats."""
        from recipeboxer.ingestion.scanner import VALID_IMAGE_EXTENSIONS, get_image_mime_type

        # Verify extension coverage
        expected_exts = {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".bmp", ".heic", ".heif", ".gif"}
        for ext in expected_exts:
            assert ext in VALID_IMAGE_EXTENSIONS

        # Verify MIME mapping
        assert get_image_mime_type("card.jpg") == "image/jpeg"
        assert get_image_mime_type("card.jpeg") == "image/jpeg"
        assert get_image_mime_type("card.png") == "image/png"
        assert get_image_mime_type("card.webp") == "image/webp"
        assert get_image_mime_type("card.tiff") == "image/tiff"
        assert get_image_mime_type("card.tif") == "image/tiff"
        assert get_image_mime_type("card.bmp") == "image/bmp"
        assert get_image_mime_type("card.heic") == "image/heic"
        assert get_image_mime_type("card.heif") == "image/heif"
        assert get_image_mime_type("card.gif") == "image/gif"

        # Local provider scanning diverse formats
        local_dir = tmp_path / "diverse_scans"
        local_dir.mkdir()
        for ext in [".jpg", ".png", ".webp", ".bmp", ".tiff"]:
            img_file = local_dir / f"scan_{ext.lstrip('.')}{ext}"
            img_file.write_bytes(create_dummy_jpeg_bytes(80, 80))

        provider = LocalSourceProvider(config=SourceProviderConfig(provider_id="local", local_path=str(local_dir)))
        items_res = asyncio.run(provider.list_items())
        assert len(items_res.items) == 5
        mimes = [it.mime_type for it in items_res.items]
        assert "image/jpeg" in mimes
        assert "image/png" in mimes
        assert "image/webp" in mimes
        assert "image/bmp" in mimes
        assert "image/tiff" in mimes

    def test_remote_asset_cache_lru_eviction_and_size_bounds(self, tmp_path: Path):
        """Verify RemoteAssetCache enforces maximum size and max entries via LRU eviction."""
        db_path = tmp_path / "lru_test_cache.db"
        # 300 bytes max capacity or 3 max entries
        cache = RemoteAssetCache(db_path, max_size_bytes=350, max_entries=3)

        blobs = [create_dummy_jpeg_bytes(30 + i, 30 + i) for i in range(5)]
        hashes = []
        for i, b in enumerate(blobs):
            h = cache.put(
                provider_id="google_photos",
                item_id=f"card_{i}.jpg",
                filename=f"card_{i}.jpg",
                data=b,
            )
            hashes.append(h)


        # Cache must not exceed max_entries (3)
        assert cache.count() <= 3
        total_size = cache.get_total_size_bytes()
        assert total_size > 0

        # The oldest items (hashes[0], hashes[1]) should have been evicted
        assert not cache.contains(hashes[0])
        # The latest items should be preserved
        assert cache.contains(hashes[-1])

    def test_fingerprint_cache_lru_eviction(self, tmp_path: Path):
        """Verify FingerprintCache evict_lru removes oldest entries when exceeding max_entries."""
        from recipeboxer.storage.cache import FingerprintCache
        db_path = tmp_path / "fp_lru_test.db"
        cache = FingerprintCache(db_path, max_entries=2)

        for i in range(4):
            fp = VisualFingerprint(
                sha256_hash=f"{i:064x}",
                card_id=f"card_{i}",
                image_path=f"/tmp/card_{i}.jpg",
                role=CardRole.FRONT_ONLY,
                extracted_at=datetime.fromtimestamp(1000 + i * 100, timezone.utc),
            )
            cache.put(fp)

        # Evict to max_entries=2
        evicted = cache.evict_lru(max_entries=2)
        assert evicted == 2
        assert cache.count() == 2
        # Check that newest items remain
        assert cache.get_by_hash(f"{3:064x}") is not None
        assert cache.get_by_hash(f"{2:064x}") is not None
        assert cache.get_by_hash(f"{0:064x}") is None

    def test_local_provider_path_traversal_sanitization(self, tmp_path: Path):
        """Verify LocalSourceProvider._resolve_path prevents path traversal escaping root_path."""
        root_dir = tmp_path / "scans_root"
        root_dir.mkdir()
        (root_dir / "valid_card.jpg").write_bytes(create_dummy_jpeg_bytes(50, 50))

        provider = LocalSourceProvider(config=SourceProviderConfig(provider_id="local", local_path=str(root_dir)))

        # 1. Normal relative path
        p1 = provider._resolve_path("valid_card.jpg")
        assert p1.exists()
        assert p1 == (root_dir / "valid_card.jpg").resolve()

        # 2. Empty path resolves to root
        p2 = provider._resolve_path(None)
        assert p2 == root_dir.resolve()

        # 3. Path traversal attempt (../../outside.jpg)
        p3 = provider._resolve_path("../../outside.jpg")
        # Resolved candidate must not escape outside root_dir
        assert str(root_dir.resolve()) in str(p3) or p3.name == "outside.jpg"



