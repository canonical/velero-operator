# Copyright 2026 Canonical Ltd.
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
from .schedule import (
    Schedule,
    ScheduleModel,
    ScheduleSpecModel,
    ScheduleStatus,
    ScheduleStatusModel,
)

__all__ = [
    "Backup",
    "Restore",
    "Schedule",
    "ExistingResourcePolicy",
    "BackupSpecModel",
    "BackupStatusModel",
    "BackupModel",
    "BackupStatus",
    "RestoreSpecModel",
    "RestoreStatusModel",
    "RestoreModel",
    "RestoreStatus",
    "ScheduleSpecModel",
    "ScheduleStatusModel",
    "ScheduleModel",
    "ScheduleStatus",
]
