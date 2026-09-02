"""Pluggable cloud and external source provider architecture for RecipeBoxer."""

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

__all__ = [
    # Base abstraction & Models
    "BaseSourceProvider",
    "SourceCapabilities",
    "SourceItem",
    "SourceContainer",
    "SourceProviderConfig",
    "SourceConnectionStatus",
    "ListContainersResponse",
    "ListItemsResponse",
    # Built-in Providers
    "LocalSourceProvider",
    "GooglePhotosProvider",
    "GoogleDriveProvider",
    # Registry & Utilities
    "SourceProviderRegistry",
    "get_source_registry",
    "get_source_provider",
    "list_available_providers",
]

