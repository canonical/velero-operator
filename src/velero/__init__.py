# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Velero module."""

from .core import Velero, VeleroCLIError, VeleroError, VeleroStatusError
from .providers import (
    AzureStorageConfig,
    AzureStorageProvider,
    S3StorageConfig,
    S3StorageProvider,
    StorageProviderError,
)

__all__ = [
    "Velero",
    "VeleroError",
    "S3StorageProvider",
    "AzureStorageProvider",
    "StorageProviderError",
    "AzureStorageConfig",
    "S3StorageConfig",
    "VeleroCLIError",
    "VeleroStatusError",
]
