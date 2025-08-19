# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Velero CRDs module."""

from .backup import Backup, BackupModel, BackupSpecModel, BackupStatus, BackupStatusModel
from .restore import (
    ExistingResourcePolicy,
    Restore,
    RestoreModel,
    RestoreSpecModel,
    RestoreStatus,
    RestoreStatusModel,
)

__all__ = [
    "Backup",
    "Restore",
    "ExistingResourcePolicy",
    "BackupSpecModel",
    "BackupStatusModel",
    "BackupModel",
    "BackupStatus",
    "RestoreSpecModel",
    "RestoreStatusModel",
    "RestoreModel",
    "RestoreStatus",
]
