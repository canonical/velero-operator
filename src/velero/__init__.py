# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Velero module."""

from .core import Velero
from .crds import ExistingResourcePolicy
from .providers import (
    AzureStorageConfig,
    AzureStorageProvider,
    S3StorageConfig,
    S3StorageProvider,
    StorageProviderError,
)
from .utils import (
    BackupInfo,
    RestoreParams,
    ScheduleInfo,
    VeleroBackupStatusError,
    VeleroCLIError,
    VeleroError,
    VeleroRestoreStatusError,
    VeleroScheduleStatusError,
    VeleroStatusError,
)

__all__ = [
    "Velero",
    "VeleroError",
    "VeleroBackupStatusError",
    "VeleroRestoreStatusError",
    "VeleroScheduleStatusError",
    "VeleroCLIError",
    "VeleroStatusError",
    "S3StorageProvider",
    "AzureStorageProvider",
    "StorageProviderError",
    "AzureStorageConfig",
    "S3StorageConfig",
    "ExistingResourcePolicy",
    "BackupInfo",
    "ScheduleInfo",
    "RestoreParams",
]
