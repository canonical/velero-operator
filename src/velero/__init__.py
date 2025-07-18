# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Velero module."""

from .core import Velero, VeleroCLIError, VeleroError, VeleroStatusError
from .crds import ExistingResourcePolicy
from .providers import (
    S3StorageConfig,
    S3StorageProvider,
    StorageProviderError,
)

__all__ = [
    "Velero",
    "VeleroError",
    "S3StorageProvider",
    "StorageProviderError",
    "S3StorageConfig",
    "VeleroCLIError",
    "VeleroStatusError",
    "ExistingResourcePolicy",
]
