"""Unit tests for RecipeBoxer configuration management (recipeboxer/config.py)."""

import os
from pathlib import Path
from unittest.mock import patch
import pytest

from recipeboxer.config import Settings, get_settings


def test_default_settings():
    """Verify default settings configuration."""
    settings = Settings()
    assert settings.default_model == "gemini-3.7-flash"
    assert settings.fingerprint_model is None
    assert settings.correlation_model is None
    assert settings.extraction_model is None
    assert settings.thinking_mode is False
    assert settings.thinking_budget_tokens is None
    assert settings.max_concurrency == 5
    assert settings.slow_batch_size == 2
    assert settings.slow_batch_interval_seconds == 2.0
    assert settings.cache_dir == Path(".cache/recipeboxer")
    assert settings.cache_db_filename == "fingerprints.db"
    assert settings.export_dir == Path("exports")
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000
    assert settings.debug is False
    assert settings.heuristic_screen_threshold == 0.50
    assert settings.heuristic_weight == 0.30
    assert settings.semantic_weight == 0.70


def test_model_override_helpers():
    """Verify get_*_model() helpers fallback to default_model or return overrides."""
    settings = Settings(
        default_model="gemini-3.7-flash",
        fingerprint_model=None,
        correlation_model="gemini-2.5-pro",
        extraction_model="gemini-3.7-thinking",
    )
    assert settings.get_fingerprint_model() == "gemini-3.7-flash"
    assert settings.get_correlation_model() == "gemini-2.5-pro"
    assert settings.get_extraction_model() == "gemini-3.7-thinking"


def test_cache_db_path():
    """Verify resolved cache database path."""
    settings = Settings(
        cache_dir=Path("/custom/cache"),
        cache_db_filename="custom_fp.db",
    )
    assert settings.cache_db_path == Path("/custom/cache/custom_fp.db")


def test_ensure_directories(tmp_path: Path):
    """Verify ensure_directories creates cache and export folders on disk."""
    cache_path = tmp_path / "sub1" / "cache"
    export_path = tmp_path / "sub2" / "exports"

    settings = Settings(cache_dir=cache_path, export_dir=export_path)
    assert not cache_path.exists()
    assert not export_path.exists()

    settings.ensure_directories()
    assert cache_path.exists()
    assert export_path.exists()


def test_env_variable_overrides():
    """Verify environment variables with RECIPEBOXER_ prefix are loaded properly."""
    env_vars = {
        "RECIPEBOXER_DEFAULT_MODEL": "gemini-custom",
        "RECIPEBOXER_MAX_CONCURRENCY": "12",
        "RECIPEBOXER_SLOW_BATCH_SIZE": "4",
        "RECIPEBOXER_SLOW_BATCH_INTERVAL_SECONDS": "5.5",
        "RECIPEBOXER_THINKING_MODE": "true",
        "RECIPEBOXER_DEBUG": "true",
        "RECIPEBOXER_API_PORT": "9000",
    }
    with patch.dict(os.environ, env_vars, clear=False):
        settings = Settings()
        assert settings.default_model == "gemini-custom"
        assert settings.max_concurrency == 12
        assert settings.slow_batch_size == 4
        assert settings.slow_batch_interval_seconds == 5.5
        assert settings.thinking_mode is True
        assert settings.debug is True
        assert settings.api_port == 9000


def test_gemini_api_key_alias():
    """Verify GEMINI_API_KEY and RECIPEBOXER_GEMINI_API_KEY alias loading."""
    with patch.dict(os.environ, {"GEMINI_API_KEY": "standard_key_123"}, clear=False):
        settings = Settings()
        assert settings.gemini_api_key == "standard_key_123"

    with patch.dict(os.environ, {"RECIPEBOXER_GEMINI_API_KEY": "prefixed_key_456"}, clear=False):
        settings = Settings()
        assert settings.gemini_api_key == "prefixed_key_456"


def test_get_settings_singleton():
    """Verify get_settings returns a cached singleton."""
    get_settings.cache_clear()
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2

    get_settings.cache_clear()
    s3 = get_settings()
    assert s3 is not s1
