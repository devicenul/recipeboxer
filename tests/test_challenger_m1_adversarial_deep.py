"""Adversarial stress and verification test suite for Milestone 1 (S3/GCS removal and provider registry integrity)."""

from __future__ import annotations

import io
from pathlib import Path
import pytest
from PIL import Image
from starlette.testclient import TestClient

from recipeboxer.api.app import create_app
from recipeboxer.sources.base import (
    BaseSourceProvider,
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
import recipeboxer.sources


def _create_dummy_jpeg(width: int = 100, height: int = 100, color: str = "red") -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def app_client():
    app = create_app()
    return TestClient(app)


@pytest.fixture
def temp_scans_dir(tmp_path: Path) -> Path:
    scans = tmp_path / "adversarial_scans"
    scans.mkdir()
    for i in range(3):
        (scans / f"card_{i}.jpg").write_bytes(_create_dummy_jpeg())
    return scans


class TestAdversarialS3GCSRemoval:
    """Rigorous verification that Amazon S3 and Google Cloud Storage are completely purged."""

    def test_s3_gcs_file_is_completely_deleted(self):
        """Ensure recipeboxer/sources/s3_gcs.py does not exist."""
        s3_gcs_path = Path("recipeboxer/sources/s3_gcs.py")
        assert not s3_gcs_path.exists(), "recipeboxer/sources/s3_gcs.py must not exist!"

    def test_sources_package_exports_are_clean(self):
        """Ensure __all__ and module attributes do not export S3/GCS classes."""
        assert not hasattr(recipeboxer.sources, "S3SourceProvider")
        assert not hasattr(recipeboxer.sources, "GCSSourceProvider")
        assert "S3SourceProvider" not in recipeboxer.sources.__all__
        assert "GCSSourceProvider" not in recipeboxer.sources.__all__

    @pytest.mark.parametrize("provider_id", ["s3", "S3", "gcs", "GCS", "aws_s3", "google_cloud_storage"])
    def test_registry_get_provider_raises_key_error(self, provider_id: str):
        """Calling get_source_provider for s3/gcs/etc raises KeyError."""
        with pytest.raises(KeyError, match=f"Unknown source provider ID: '{provider_id}'"):
            get_source_provider(provider_id)

    @pytest.mark.parametrize("provider_id", ["s3", "S3", "gcs", "GCS", "aws_s3", "google_cloud_storage"])
    def test_registry_instance_get_provider_raises_key_error(self, provider_id: str):
        """Calling registry.get_provider for s3/gcs/etc raises KeyError on a fresh registry."""
        reg = SourceProviderRegistry()
        with pytest.raises(KeyError, match=f"Unknown source provider ID: '{provider_id}'"):
            reg.get_provider(provider_id)

    @pytest.mark.parametrize("provider_id", ["s3", "gcs", "S3", "GCS"])
    def test_api_sources_containers_returns_404(self, app_client: TestClient, provider_id: str):
        """GET /api/sources/{provider_id}/containers must return 404 for removed providers."""
        res = app_client.get(f"/api/sources/{provider_id}/containers")
        assert res.status_code == 404
        assert f"Source provider '{provider_id}' not found" in res.json().get("detail", "")

    @pytest.mark.parametrize("provider_id", ["s3", "gcs", "S3", "GCS"])
    def test_api_sources_items_returns_404(self, app_client: TestClient, provider_id: str):
        """GET /api/sources/{provider_id}/items must return 404 for removed providers."""
        res = app_client.get(f"/api/sources/{provider_id}/items")
        assert res.status_code == 404
        assert f"Source provider '{provider_id}' not found" in res.json().get("detail", "")

    @pytest.mark.parametrize("provider_id", ["s3", "gcs", "S3", "GCS"])
    def test_api_sources_thumbnail_returns_404(self, app_client: TestClient, provider_id: str):
        """GET /api/sources/{provider_id}/thumbnail/{item_id} must return 404 for removed providers."""
        res = app_client.get(f"/api/sources/{provider_id}/thumbnail/test_item_123.jpg")
        assert res.status_code == 404
        assert f"Source provider '{provider_id}' not found" in res.json().get("detail", "")

    @pytest.mark.parametrize("provider_id", ["s3", "gcs", "S3", "GCS"])
    def test_api_sources_connect_returns_404(self, app_client: TestClient, provider_id: str):
        """POST /api/sources/{provider_id}/connect must return 404 for removed providers."""
        res = app_client.post(f"/api/sources/{provider_id}/connect", json={})
        assert res.status_code == 404
        assert f"Source provider '{provider_id}' not found" in res.json().get("detail", "")

    @pytest.mark.parametrize("bad_provider", ["s3", "gcs", "S3", "GCS", "amazon_s3", "random_provider"])
    def test_pipeline_start_with_invalid_or_removed_provider_returns_400(
        self, app_client: TestClient, bad_provider: str
    ):
        """POST /api/pipeline/start with S3/GCS or invalid provider returns 400 Bad Request."""
        res = app_client.post(
            "/api/pipeline/start",
            json={
                "source_provider": bad_provider,
                "source_container_id": "some_bucket_or_album",
                "mode": "parallel",
            },
        )
        assert res.status_code == 400
        assert "Failed to start pipeline from source provider" in res.json().get("detail", "")


class TestActiveProvidersIntegrity:
    """Verify that supported providers (local, google_photos, google_drive) remain robust."""

    def test_registry_contains_only_supported_providers(self):
        """Registry default class mappings must be exactly local, google_photos, google_drive."""
        reg = SourceProviderRegistry()
        assert set(reg._provider_classes.keys()) == {"local", "google_photos", "google_drive"}

    def test_get_supported_providers_returns_correct_types(self):
        """get_source_provider returns appropriate class instances."""
        local = get_source_provider("local")
        assert isinstance(local, LocalSourceProvider)

        gp = get_source_provider("google_photos")
        assert isinstance(gp, GooglePhotosProvider)

        gd = get_source_provider("google_drive")
        assert isinstance(gd, GoogleDriveProvider)

    @pytest.mark.asyncio
    async def test_list_available_providers_output(self):
        """list_available_providers() returns 3 statuses without S3/GCS."""
        statuses = await list_available_providers()
        assert len(statuses) == 3
        pids = {s.provider_id for s in statuses}
        assert pids == {"local", "google_photos", "google_drive"}
        assert "s3" not in pids
        assert "gcs" not in pids

    def test_api_sources_providers_endpoint(self, app_client: TestClient):
        """GET /api/sources/providers returns 3 providers in API output."""
        res = app_client.get("/api/sources/providers")
        assert res.status_code == 200
        providers = res.json()
        assert isinstance(providers, list)
        pids = [p["provider_id"] for p in providers]
        assert set(pids) == {"local", "google_photos", "google_drive"}
        assert "s3" not in pids
        assert "gcs" not in pids

    @pytest.mark.asyncio
    async def test_local_provider_end_to_end(self, temp_scans_dir: Path):
        """Verify LocalSourceProvider functions properly for local ingestion."""
        cfg = SourceProviderConfig(provider_id="local", local_path=str(temp_scans_dir))
        provider = LocalSourceProvider(config=cfg)

        # test_connection
        assert await provider.test_connection() is True

        # list_items
        items = await provider.list_items()
        assert len(items.items) == 3

        # get_metadata & fetch_thumbnail
        item = items.items[0]
        meta = await provider.get_metadata(item.id)
        assert meta.id == item.id
        assert meta.width == 100
        assert meta.height == 100

        thumb = await provider.fetch_thumbnail(item.id)
        assert thumb is not None
        assert len(thumb) > 0

        # download_image
        downloaded = await provider.download_image(item.id)
        assert len(downloaded) == item.size_bytes

    def test_pipeline_start_local_success(self, app_client: TestClient, temp_scans_dir: Path):
        """POST /api/pipeline/start with local directory successfully initializes pipeline."""
        res = app_client.post(
            "/api/pipeline/start",
            json={
                "source_provider": "local",
                "source_config": {"local_path": str(temp_scans_dir)},
                "mode": "parallel",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert "session_id" in data
        assert data["total_scans_found"] == 3
        assert data["status"] in ("PROCESSING", "IDLE", "INDEXING", "EXTRACTING", "COMPLETED")
