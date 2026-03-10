# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Velero module."""

from .core import (
    BackupInfo,
    ScheduleInfo,
    Velero,
    VeleroBackupStatusError,
    VeleroCLIError,
    VeleroError,
    VeleroRestoreStatusError,
    VeleroScheduleStatusError,
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
    "ScheduleInfo",
    "VeleroBackupStatusError",
    "VeleroRestoreStatusError",
    "VeleroScheduleStatusError",
]
