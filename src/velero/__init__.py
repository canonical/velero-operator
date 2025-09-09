# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Velero module."""

from .core import (
    BackupInfo,
    Velero,
    VeleroBackupStatusError,
    VeleroCLIError,
    VeleroError,
    VeleroRestoreStatusError,
    VeleroStatusError,
)
from .crds import ExistingResourcePolicy
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
    "ExistingResourcePolicy",
    "BackupInfo",
    "VeleroBackupStatusError",
    "VeleroRestoreStatusError",
]
