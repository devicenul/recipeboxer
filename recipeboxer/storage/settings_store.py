"""Persistent JSON settings store and secure credential management for RecipeBoxer.

Stores user preferences (quality profile, per-stage model overrides, spend caps,
RPM limit, extra-effort reasoning toggle, embeddings toggle) in a JSON file
under the user's platform configuration directory.

Credentials (Gemini API key) are stored in the OS keyring if available,
falling back to the settings file with strict 0o600 permissions.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import stat
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Union

from pydantic import BaseModel, Field, field_validator

from recipeboxer.config import is_usable_gemini_key

logger = logging.getLogger(__name__)

SUPPORTED_PROFILES = ("economy", "balanced", "best")


def get_user_config_dir() -> Path:
    """Resolves the user configuration directory following platform conventions.

    Precedence:
    1. RECIPEBOXER_CONFIG_DIR environment variable (used in tests/custom deployments)
    2. platformdirs.user_config_dir("recipeboxer") if installed
    3. Platform-specific conventions:
       - Linux/BSD: $XDG_CONFIG_HOME/recipeboxer or ~/.config/recipeboxer
       - Darwin (macOS): ~/Library/Application Support/recipeboxer
       - Windows: %APPDATA%/recipeboxer or ~/AppData/Roaming/recipeboxer
    """
    env_dir = os.environ.get("RECIPEBOXER_CONFIG_DIR")
    if env_dir:
        return Path(env_dir)

    try:
        import platformdirs  # type: ignore

        return Path(platformdirs.user_config_dir("recipeboxer"))
    except ImportError:
        pass

    sys_name = platform.system().lower()
    if sys_name == "darwin":
        return Path.home() / "Library" / "Application Support" / "recipeboxer"
    elif sys_name == "windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "recipeboxer"
        return Path.home() / "AppData" / "Roaming" / "recipeboxer"
    else:
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config:
            return Path(xdg_config) / "recipeboxer"
        return Path.home() / ".config" / "recipeboxer"


class UserSettings(BaseModel):
    """Internal representation of persisted user settings."""

    quality_profile: str = Field(
        default="balanced",
        description="Active quality profile: economy, balanced, or best",
    )
    stage_models: Dict[str, str] = Field(
        default_factory=dict,
        description="Per-stage model overrides for advanced users (e.g. FINGERPRINT, TRANSCRIBE)",
    )
    spend_cap_usd: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Per-run spend cap in USD (pipeline pauses when reached)",
    )
    monthly_cap_usd: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Monthly spend cap in USD",
    )
    rpm_limit: int = Field(
        default=60,
        ge=1,
        le=10000,
        description="Requests per minute rate limit for Gemini API calls",
    )
    extra_effort: bool = Field(
        default=False,
        description="Toggle extra reasoning / thinking budget for hard-to-read cards",
    )
    embeddings_enabled: bool = Field(
        default=False,
        description="Toggle embedding-based semantic retrieval and pairing",
    )
    api_key: Optional[str] = Field(
        default=None,
        description="Fallback storage for Gemini API key when OS keyring is unavailable",
    )

    @field_validator("quality_profile")
    @classmethod
    def validate_quality_profile(cls, v: str) -> str:
        norm = v.lower().strip()
        if norm not in SUPPORTED_PROFILES:
            raise ValueError(
                f"Invalid quality_profile '{v}'. Must be one of: {', '.join(SUPPORTED_PROFILES)}"
            )
        return norm


class SettingsStore:
    """Thread-safe persistent JSON store for user settings and API key credentials."""

    def __init__(self, config_dir: Optional[Union[str, Path]] = None) -> None:
        """Initializes SettingsStore with specified or default configuration directory.

        Args:
            config_dir: Directory where settings.json is stored. If None, resolves via
                        get_user_config_dir().
        """
        if config_dir is not None:
            self.config_dir = Path(config_dir)
        else:
            self.config_dir = get_user_config_dir()

        self.settings_file = self.config_dir / "settings.json"
        self._lock = threading.RLock()
        self._settings = self._load()

    def _load(self) -> UserSettings:
        """Loads settings from settings.json or initializes defaults."""
        with self._lock:
            if not self.settings_file.is_file():
                return UserSettings()
            try:
                raw_text = self.settings_file.read_text(encoding="utf-8")
                data = json.loads(raw_text)
                return UserSettings.model_validate(data)
            except Exception as exc:
                logger.warning(
                    "Failed to load user settings from %s (%s); reverting to defaults",
                    self.settings_file,
                    exc,
                )
                return UserSettings()

    def _save_to_disk(self) -> None:
        """Atomically saves settings to settings.json with 0o600 permissions."""
        with self._lock:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            tmp_file = self.config_dir / f".settings_{os.getpid()}_{id(self)}.tmp"
            data = self._settings.model_dump()

            tmp_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            try:
                os.chmod(tmp_file, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
            except OSError:
                pass

            tmp_file.replace(self.settings_file)
            try:
                os.chmod(self.settings_file, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
            except OSError:
                pass

    def get_settings(self) -> UserSettings:
        """Returns a snapshot of the current user settings."""
        with self._lock:
            return self._settings.model_copy()

    def update_settings(self, updates: Dict[str, Any]) -> UserSettings:
        """Applies partial updates to settings, validates, and persists to disk.

        Args:
            updates: Dictionary of setting fields to update.

        Returns:
            Updated UserSettings instance.
        """
        with self._lock:
            current_dict = self._settings.model_dump()
            # Retain existing API key unless explicitly passed
            api_key = current_dict.get("api_key")

            for k, v in updates.items():
                if v is not None and k in current_dict and k != "api_key":
                    current_dict[k] = v

            current_dict["api_key"] = api_key
            self._settings = UserSettings.model_validate(current_dict)
            self._save_to_disk()
            return self.get_settings()

    def set_api_key(self, api_key: str) -> str:
        """Stores Gemini API key securely.

        Tries OS keyring first. If keyring is unavailable or fails, stores in
        the settings file with strict 0o600 permissions.

        Args:
            api_key: Non-empty API key string.

        Returns:
            Key storage source: 'keyring' or 'settings_file'.
        """
        stripped_key = api_key.strip()
        if not stripped_key:
            raise ValueError("API key cannot be empty")

        # 1. Attempt keyring storage
        try:
            import keyring  # type: ignore

            keyring.set_password("recipeboxer", "gemini_api_key", stripped_key)
            # Verify successful round-trip
            retrieved = keyring.get_password("recipeboxer", "gemini_api_key")
            if retrieved == stripped_key:
                with self._lock:
                    if self._settings.api_key:
                        self._settings.api_key = None
                        self._save_to_disk()
                return "keyring"
        except Exception as exc:
            logger.info("Keyring not available (%s); saving key to settings file with 0o600 perms", exc)

        # 2. Fallback to settings file with 0o600 permissions
        with self._lock:
            self._settings.api_key = stripped_key
            self._save_to_disk()
        return "settings_file"

    def delete_api_key(self) -> None:
        """Deletes API key from keyring and/or settings file."""
        # 1. Clear from keyring
        try:
            import keyring  # type: ignore

            keyring.delete_password("recipeboxer", "gemini_api_key")
        except Exception:
            pass

        # 2. Clear from settings file
        with self._lock:
            if self._settings.api_key is not None:
                self._settings.api_key = None
                self._save_to_disk()

    def get_api_key(self) -> Optional[str]:
        """Resolves the active API key across keyring, settings file, and environment.

        Precedence:
        1. Keyring
        2. Settings file
        3. Environment variable (RECIPEBOXER_GEMINI_API_KEY or GEMINI_API_KEY)
        """
        # 1. Keyring
        try:
            import keyring  # type: ignore

            key = keyring.get_password("recipeboxer", "gemini_api_key")
            if is_usable_gemini_key(key):
                return key
        except Exception:
            pass

        # 2. Settings file
        with self._lock:
            if is_usable_gemini_key(self._settings.api_key):
                return self._settings.api_key

        # 3. Environment variable
        env_key = os.environ.get("RECIPEBOXER_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if is_usable_gemini_key(env_key):
            return env_key

        return None

    def has_api_key(self) -> bool:
        """Checks if a valid, usable API key is configured."""
        return self.get_api_key() is not None

    def get_key_source(self) -> str:
        """Returns the origin source of the active API key.

        Returns:
            'keyring', 'settings_file', 'environment', or 'none'.
        """
        # 1. Keyring
        try:
            import keyring  # type: ignore

            key = keyring.get_password("recipeboxer", "gemini_api_key")
            if is_usable_gemini_key(key):
                return "keyring"
        except Exception:
            pass

        # 2. Settings file
        with self._lock:
            if is_usable_gemini_key(self._settings.api_key):
                return "settings_file"

        # 3. Environment
        env_key = os.environ.get("RECIPEBOXER_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if is_usable_gemini_key(env_key):
            return "environment"

        return "none"

    def get_public_settings_dict(self) -> Dict[str, Any]:
        """Returns a public dictionary of settings safe for external exposure.

        CRITICAL: Never includes the API key string.
        """
        with self._lock:
            return {
                "quality_profile": self._settings.quality_profile,
                "stage_models": dict(self._settings.stage_models),
                "spend_cap_usd": self._settings.spend_cap_usd,
                "monthly_cap_usd": self._settings.monthly_cap_usd,
                "rpm_limit": self._settings.rpm_limit,
                "extra_effort": self._settings.extra_effort,
                "embeddings_enabled": self._settings.embeddings_enabled,
                "has_api_key": self.has_api_key(),
                "key_source": self.get_key_source(),
            }

    def reset(self, delete_file: bool = False) -> None:
        """Resets in-memory settings to defaults and optionally removes the file."""
        with self._lock:
            self._settings = UserSettings()
            if delete_file and self.settings_file.exists():
                try:
                    self.settings_file.unlink()
                except OSError:
                    pass


_GLOBAL_SETTINGS_STORE: Optional[SettingsStore] = None
_STORE_LOCK = threading.RLock()


def get_settings_store(config_dir: Optional[Union[str, Path]] = None) -> SettingsStore:
    """Returns the singleton or custom SettingsStore instance."""
    global _GLOBAL_SETTINGS_STORE
    if config_dir is not None:
        return SettingsStore(config_dir=config_dir)

    with _STORE_LOCK:
        if _GLOBAL_SETTINGS_STORE is None:
            _GLOBAL_SETTINGS_STORE = SettingsStore()
        return _GLOBAL_SETTINGS_STORE


def reset_settings_store() -> None:
    """Resets the singleton store instance (primarily for tests)."""
    global _GLOBAL_SETTINGS_STORE
    with _STORE_LOCK:
        _GLOBAL_SETTINGS_STORE = None
