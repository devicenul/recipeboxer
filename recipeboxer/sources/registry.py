"""Pluggable registry and factory for RecipeBoxer SourceProviders."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Type
from pydantic import BaseModel

from recipeboxer.sources.base import (
    BaseSourceProvider,
    SourceConnectionStatus,
    SourceProviderConfig,
)
from recipeboxer.sources.cloud_drive import GoogleDriveProvider
from recipeboxer.sources.google_photos import GooglePhotosProvider
from recipeboxer.sources.local import LocalSourceProvider
from recipeboxer.sources.s3_gcs import GCSSourceProvider, S3SourceProvider

logger = logging.getLogger(__name__)


class SourceProviderRegistry:
    """Registry coordinating available source provider classes and configured instances."""

    def __init__(self) -> None:
        self._provider_classes: Dict[str, Type[BaseSourceProvider]] = {}
        self._active_instances: Dict[str, BaseSourceProvider] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        """Register built-in source provider implementations."""
        self.register_provider_class("local", LocalSourceProvider)
        self.register_provider_class("google_photos", GooglePhotosProvider)
        self.register_provider_class("google_drive", GoogleDriveProvider)
        self.register_provider_class("s3", S3SourceProvider)
        self.register_provider_class("gcs", GCSSourceProvider)

    def register_provider_class(
        self, provider_id: str, provider_cls: Type[BaseSourceProvider]
    ) -> None:
        """Register a new or custom SourceProvider class."""
        self._provider_classes[provider_id.lower()] = provider_cls

    def register_configured_instance(
        self, provider_id: str, instance: BaseSourceProvider
    ) -> None:
        """Register an authorized or customized provider instance."""
        self._active_instances[provider_id.lower()] = instance

    def get_provider(
        self,
        provider_id: str,
        config: Optional[SourceProviderConfig] = None,
    ) -> BaseSourceProvider:
        """Retrieve active provider instance or instantiate new provider with config."""
        pid = provider_id.lower()
        if config is not None:
            if pid not in self._provider_classes:
                raise KeyError(f"Unknown source provider ID: '{provider_id}'")
            cls = self._provider_classes[pid]
            instance = cls(config=config)
            self._active_instances[pid] = instance
            return instance

        if pid in self._active_instances:
            return self._active_instances[pid]

        if pid in self._provider_classes:
            cls = self._provider_classes[pid]
            instance = cls(config=SourceProviderConfig(provider_id=pid))
            self._active_instances[pid] = instance
            return instance

        raise KeyError(f"Unknown source provider ID: '{provider_id}'")

    async def list_available_providers(self) -> List[SourceConnectionStatus]:
        """Return list of all registered source providers and their connection statuses."""
        statuses: List[SourceConnectionStatus] = []
        for pid, cls in self._provider_classes.items():
            instance = self._active_instances.get(pid) or cls(config=SourceProviderConfig(provider_id=pid))
            try:
                status = await instance.get_connection_status()
                statuses.append(status)
            except Exception as exc:
                statuses.append(
                    SourceConnectionStatus(
                        provider_id=pid,
                        name=instance.name,
                        description=instance.description,
                        connected=False,
                        auth_required=instance.capabilities.requires_auth,
                        authenticated=False,
                        message=f"Error checking status: {exc}",
                        capabilities=instance.capabilities,
                    )
                )
        return statuses


_registry_instance: Optional[SourceProviderRegistry] = None


def get_source_registry() -> SourceProviderRegistry:
    """Return singleton instance of SourceProviderRegistry."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = SourceProviderRegistry()
    return _registry_instance


def get_source_provider(
    provider_id: str,
    config: Optional[SourceProviderConfig] = None,
) -> BaseSourceProvider:
    """Convenience helper to retrieve configured source provider."""
    return get_source_registry().get_provider(provider_id, config=config)


async def list_available_providers() -> List[SourceConnectionStatus]:
    """Convenience helper to list available source providers."""
    return await get_source_registry().list_available_providers()
