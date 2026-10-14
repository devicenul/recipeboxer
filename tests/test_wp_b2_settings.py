"""Tests for WP-B2 Settings API, JSON settings store, API key security, and cost estimation."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from recipeboxer.ai.cost import PriceTable, estimate_run_cost
from recipeboxer.api.app import create_app
from recipeboxer.models.session import SessionDetail, SessionSpendStatus, SessionSummary
from recipeboxer.storage.settings_store import (
    SettingsStore,
    UserSettings,
    get_settings_store,
    get_user_config_dir,
    reset_settings_store,
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensures offline test isolation by unsetting Gemini keys."""
    monkeypatch.delenv("RECIPEBOXER_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    reset_settings_store()


@pytest.fixture
def temp_settings_store(tmp_path: Path) -> SettingsStore:
    """Provides an isolated SettingsStore instance bound to a temporary directory."""
    return SettingsStore(config_dir=tmp_path)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """Provides a FastAPI TestClient configured with an isolated temporary SettingsStore."""
    store = SettingsStore(config_dir=tmp_path)
    app = create_app(settings_store=store)
    return TestClient(app)


# ===========================================================================
# 1. SettingsStore Unit Tests
# ===========================================================================

def test_settings_store_defaults(temp_settings_store: SettingsStore) -> None:
    """Verifies default settings are returned when no file exists."""
    settings = temp_settings_store.get_settings()
    assert settings.quality_profile == "balanced"
    assert settings.stage_models == {}
    assert settings.spend_cap_usd is None
    assert settings.monthly_cap_usd is None
    assert settings.rpm_limit == 60
    assert settings.extra_effort is False
    assert settings.embeddings_enabled is False
    assert temp_settings_store.has_api_key() is False
    assert temp_settings_store.get_key_source() == "none"


def test_settings_store_update_and_persistence(tmp_path: Path) -> None:
    """Verifies settings updates persist to disk and reload accurately."""
    store = SettingsStore(config_dir=tmp_path)
    updates = {
        "quality_profile": "best",
        "stage_models": {"TRANSCRIBE": "gemini-3.7-pro"},
        "spend_cap_usd": 5.0,
        "monthly_cap_usd": 25.0,
        "rpm_limit": 30,
        "extra_effort": True,
        "embeddings_enabled": True,
    }
    updated = store.update_settings(updates)
    assert updated.quality_profile == "best"
    assert updated.spend_cap_usd == 5.0
    assert updated.monthly_cap_usd == 25.0
    assert updated.rpm_limit == 30
    assert updated.extra_effort is True
    assert updated.embeddings_enabled is True
    assert updated.stage_models == {"TRANSCRIBE": "gemini-3.7-pro"}

    # Reload from fresh store instance pointing to same tmp_path
    reloaded_store = SettingsStore(config_dir=tmp_path)
    reloaded = reloaded_store.get_settings()
    assert reloaded.quality_profile == "best"
    assert reloaded.spend_cap_usd == 5.0
    assert reloaded.monthly_cap_usd == 25.0
    assert reloaded.rpm_limit == 30
    assert reloaded.extra_effort is True
    assert reloaded.embeddings_enabled is True
    assert reloaded.stage_models == {"TRANSCRIBE": "gemini-3.7-pro"}


def test_settings_store_file_permissions(tmp_path: Path) -> None:
    """Verifies settings.json is written with 0o600 permissions."""
    store = SettingsStore(config_dir=tmp_path)
    store.update_settings({"quality_profile": "economy"})

    settings_file = tmp_path / "settings.json"
    assert settings_file.is_file()

    file_stat = settings_file.stat()
    file_mode = stat.S_IMODE(file_stat.st_mode)
    # 0o600 = user read & write only (400 + 200 = 600)
    assert file_mode == 0o600, f"Expected 0o600, got {oct(file_mode)}"


def test_settings_store_api_key_fallback_to_file(tmp_path: Path) -> None:
    """Verifies API key falls back to file storage with 0o600 perms when keyring is unavailable."""
    store = SettingsStore(config_dir=tmp_path)
    source = store.set_api_key("test-gemini-secret-key-12345")
    assert source == "settings_file"

    assert store.has_api_key() is True
    assert store.get_key_source() == "settings_file"
    assert store.get_api_key() == "test-gemini-secret-key-12345"

    # Verify public settings dict DOES NOT expose the key
    pub = store.get_public_settings_dict()
    assert "api_key" not in pub
    assert pub["has_api_key"] is True
    assert pub["key_source"] == "settings_file"

    # Verify file mode remains 0o600
    file_mode = stat.S_IMODE((tmp_path / "settings.json").stat().st_mode)
    assert file_mode == 0o600

    # Delete API key
    store.delete_api_key()
    assert store.has_api_key() is False
    assert store.get_key_source() == "none"
    assert store.get_api_key() is None


def test_settings_store_api_key_via_keyring(tmp_path: Path) -> None:
    """Verifies API key prefers keyring storage when keyring is available."""
    store = SettingsStore(config_dir=tmp_path)
    mock_vault: Dict[str, str] = {}

    def mock_set(service: str, username: str, password: str) -> None:
        mock_vault[f"{service}:{username}"] = password

    def mock_get(service: str, username: str) -> str | None:
        return mock_vault.get(f"{service}:{username}")

    def mock_del(service: str, username: str) -> None:
        mock_vault.pop(f"{service}:{username}", None)

    mock_keyring = MagicMock()
    mock_keyring.set_password = mock_set
    mock_keyring.get_password = mock_get
    mock_keyring.delete_password = mock_del

    with patch.dict("sys.modules", {"keyring": mock_keyring}):
        source = store.set_api_key("keyring-stored-gemini-key")
        assert source == "keyring"
        assert store.has_api_key() is True
        assert store.get_key_source() == "keyring"
        assert store.get_api_key() == "keyring-stored-gemini-key"

        # Verify it wasn't saved in the file
        raw_settings = store.get_settings()
        assert raw_settings.api_key is None

        # Verify deletion from keyring
        store.delete_api_key()
        assert store.has_api_key() is False
        assert store.get_key_source() == "none"


def test_settings_store_detects_environment_key(
    temp_settings_store: SettingsStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies that an environment Gemini key is detected when no file/keyring key is set."""
    monkeypatch.setenv("RECIPEBOXER_GEMINI_API_KEY", "env-provided-key-xyz")
    assert temp_settings_store.has_api_key() is True
    assert temp_settings_store.get_key_source() == "environment"
    assert temp_settings_store.get_api_key() == "env-provided-key-xyz"


def test_get_user_config_dir_environment_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verifies RECIPEBOXER_CONFIG_DIR overrides the default config directory."""
    custom_dir = tmp_path / "custom_config"
    monkeypatch.setenv("RECIPEBOXER_CONFIG_DIR", str(custom_dir))
    resolved = get_user_config_dir()
    assert resolved == custom_dir


# ===========================================================================
# 2. FastAPI Endpoint Tests
# ===========================================================================

def test_get_settings_endpoint(client: TestClient) -> None:
    """GET /api/settings returns settings and never contains the API key."""
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()

    assert data["quality_profile"] == "balanced"
    assert data["rpm_limit"] == 60
    assert data["has_api_key"] is False
    assert data["key_source"] == "none"
    assert data["extra_effort"] is False
    assert data["embeddings_enabled"] is False

    # CRITICAL: API key must NEVER be in response
    assert "api_key" not in data


def test_put_settings_endpoint_valid_updates(client: TestClient) -> None:
    """PUT /api/settings updates settings preferences and returns updated public schema."""
    payload = {
        "quality_profile": "best",
        "spend_cap_usd": 3.75,
        "monthly_cap_usd": 20.0,
        "rpm_limit": 40,
        "extra_effort": True,
        "embeddings_enabled": True,
        "stage_models": {"FINGERPRINT": "gemini-3.7-flash"},
    }
    response = client.put("/api/settings", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["quality_profile"] == "best"
    assert data["spend_cap_usd"] == 3.75
    assert data["monthly_cap_usd"] == 20.0
    assert data["rpm_limit"] == 40
    assert data["extra_effort"] is True
    assert data["embeddings_enabled"] is True
    assert data["stage_models"] == {"FINGERPRINT": "gemini-3.7-flash"}
    assert "api_key" not in data

    # Verify persistence via GET
    get_resp = client.get("/api/settings")
    assert get_resp.status_code == 200
    get_data = get_resp.json()
    assert get_data["quality_profile"] == "best"
    assert get_data["spend_cap_usd"] == 3.75


def test_put_settings_endpoint_validation_errors(client: TestClient) -> None:
    """PUT /api/settings rejects invalid quality profiles, negative spend caps, and bad RPM."""
    # Invalid quality profile
    resp1 = client.put("/api/settings", json={"quality_profile": "super_deluxe"})
    assert resp1.status_code == 422

    # Negative spend cap
    resp2 = client.put("/api/settings", json={"spend_cap_usd": -5.0})
    assert resp2.status_code == 422

    # Invalid RPM limit (< 1)
    resp3 = client.put("/api/settings", json={"rpm_limit": 0})
    assert resp3.status_code == 422


def test_api_key_lifecycle_endpoints(client: TestClient) -> None:
    """Tests POST /api/settings/api-key and DELETE /api/settings/api-key."""
    # 1. Invalid empty key
    bad_post = client.post("/api/settings/api-key", json={"api_key": "   "})
    assert bad_post.status_code in (400, 422)

    # 2. Set valid API key
    post_resp = client.post("/api/settings/api-key", json={"api_key": "valid-secret-key-999"})
    assert post_resp.status_code == 200
    post_data = post_resp.json()
    assert post_data["success"] is True
    assert post_data["has_api_key"] is True
    assert post_data["key_source"] in ("keyring", "settings_file")

    # 3. Verify GET /api/settings reflects key presence but does not leak the key string
    get_resp = client.get("/api/settings")
    get_data = get_resp.json()
    assert get_data["has_api_key"] is True
    assert get_data["key_source"] in ("keyring", "settings_file")
    assert "api_key" not in get_data

    # 4. DELETE API key
    del_resp = client.delete("/api/settings/api-key")
    assert del_resp.status_code == 200
    del_data = del_resp.json()
    assert del_data["success"] is True
    assert del_data["has_api_key"] is False
    assert del_data["key_source"] == "none"

    # 5. Verify GET /api/settings reflects deleted key
    final_get = client.get("/api/settings")
    assert final_get.json()["has_api_key"] is False
    assert final_get.json()["key_source"] == "none"


def test_estimate_endpoint(client: TestClient) -> None:
    """GET /api/settings/estimate calculates pre-run cost based on card count and profile."""
    # 1. Default profile estimate
    resp = client.get("/api/settings/estimate?n_cards=10")
    assert resp.status_code == 200
    data = resp.json()
    assert "low" in data
    assert "expected" in data
    assert "high" in data
    assert "assumptions" in data
    assert data["assumptions"]["n_cards"] == 10
    assert data["assumptions"]["profile"] == "balanced"

    # 2. Profile override
    resp_economy = client.get("/api/settings/estimate?n_cards=20&profile=economy")
    assert resp_economy.status_code == 200
    data_econ = resp_economy.json()
    assert data_econ["assumptions"]["n_cards"] == 20
    assert data_econ["assumptions"]["profile"] == "economy"

    # 3. Invalid card count (ge=1 enforced)
    resp_bad_cards = client.get("/api/settings/estimate?n_cards=0")
    assert resp_bad_cards.status_code == 422

    # 4. Invalid profile override
    resp_bad_profile = client.get("/api/settings/estimate?n_cards=5&profile=invalid_prof")
    assert resp_bad_profile.status_code == 400


def test_privacy_notice_endpoint(client: TestClient) -> None:
    """GET /api/settings/privacy-notice returns static data-use caution and terms URLs."""
    resp = client.get("/api/settings/privacy-notice")
    assert resp.status_code == 200
    data = resp.json()
    assert "notice" in data
    assert "terms_url" in data
    assert "privacy_policy_url" in data
    assert "paid_vs_free_summary" in data
    assert "free" in data["notice"].lower()
    assert "paid" in data["notice"].lower()
    assert "https://ai.google.dev/gemini-api/terms" in data["terms_url"]


# ===========================================================================
# 3. Session Spend Status Models Tests
# ===========================================================================

def test_session_summary_spend_fields() -> None:
    """Verifies SessionSummary supports spend_usd and estimated_cost_usd with defaults."""
    summary = SessionSummary(
        session_id="sess_001",
        created_at="2026-10-14T20:00:00Z",
    )
    assert summary.spend_usd == 0.0
    assert summary.estimated_cost_usd is None

    populated = SessionSummary(
        session_id="sess_002",
        created_at="2026-10-14T20:00:00Z",
        spend_usd=1.45,
        estimated_cost_usd=2.00,
    )
    assert populated.spend_usd == 1.45
    assert populated.estimated_cost_usd == 2.00


def test_session_detail_spend_fields() -> None:
    """Verifies SessionDetail supports spend_usd, estimated_cost_usd, and spend_breakdown."""
    detail = SessionDetail(session_id="sess_100")
    assert detail.spend_usd == 0.0
    assert detail.estimated_cost_usd is None
    assert detail.spend_breakdown == {}

    breakdown = {"FINGERPRINT": 0.25, "TRANSCRIBE": 1.10}
    detail_with_spend = SessionDetail(
        session_id="sess_101",
        spend_usd=1.35,
        estimated_cost_usd=1.50,
        spend_breakdown=breakdown,
    )
    assert detail_with_spend.spend_usd == 1.35
    assert detail_with_spend.estimated_cost_usd == 1.50
    assert detail_with_spend.spend_breakdown == breakdown


def test_session_spend_status_model() -> None:
    """Verifies SessionSpendStatus model structure and serialization."""
    status = SessionSpendStatus(
        session_id="sess_200",
        spend_usd=0.88,
        estimated_cost_usd=1.20,
        spend_cap_usd=5.00,
        monthly_cap_usd=30.00,
        spend_breakdown={"TRANSCRIBE": 0.88},
    )
    assert status.session_id == "sess_200"
    assert status.spend_usd == 0.88
    assert status.spend_cap_usd == 5.00
    dumped = status.model_dump()
    assert dumped["spend_usd"] == 0.88
    assert dumped["spend_cap_usd"] == 5.00
