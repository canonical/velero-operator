# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Subset of the Velero Schedule CRD model.

Reference: https://velero.io/docs/v1.17/api-types/schedule/
"""

from typing import ClassVar, List, Optional

from lightkube.codecs import resource_registry
from lightkube.core import resource as res
from lightkube.core.schema import DictMixin, dataclass
from lightkube.models import meta_v1

from .backup import BackupSpecModel


@dataclass
class ScheduleSpecModel(DictMixin):
    """Schedule specification model.

    Attributes:
        schedule: Cron expression defining when to run backups (required).
        template: Backup specification template used to create backups.
        paused: Whether the schedule is paused (default: False).
        skipImmediately: If true, skip running backup immediately upon schedule creation.
        useOwnerReferencesInBackup: If true, add OwnerReferences to backups created by this
            schedule. When the schedule is deleted, all backups will be garbage collected.
    """

    schedule: str
    template: Optional[BackupSpecModel] = None
    paused: Optional[bool] = None
    skipImmediately: Optional[bool] = None
    useOwnerReferencesInBackup: Optional[bool] = None


@dataclass
class ScheduleStatusModel(DictMixin):
    """Schedule status model.

    Attributes:
        phase: Current phase of the schedule (New, Enabled, FailedValidation).
        lastBackup: Timestamp of the last backup created by this schedule.
        validationErrors: List of validation errors if phase is FailedValidation.
    """

    phase: Optional[str] = None
    lastBackup: Optional[str] = None
    validationErrors: Optional[List[str]] = None


@dataclass
class ScheduleModel(DictMixin):
    """Schedule model representing the Velero Schedule CRD."""

    apiVersion: str = "velero.io/v1"
    kind: str = "Schedule"
    metadata: Optional[meta_v1.ObjectMeta] = None
    spec: Optional[ScheduleSpecModel] = None
    status: Optional[ScheduleStatusModel] = None


class ScheduleStatus(res.NamespacedSubResource, ScheduleStatusModel):
    """Schedule status sub-resource for the Velero Schedule CRD."""

    _api_info = res.ApiInfo(
        resource=res.ResourceDef("velero.io", "v1", "Schedule"),
        parent=res.ResourceDef("velero.io", "v1", "Schedule"),
        plural="schedules",
        verbs=["get", "patch", "put"],
        action="status",
    )


@resource_registry.register
class Schedule(res.NamespacedResourceG, ScheduleModel):
    """Schedule resource for the Velero Schedule CRD."""

    _api_info = res.ApiInfo(
        resource=res.ResourceDef("velero.io", "v1", "Schedule"),
        plural="schedules",
        verbs=[
            "delete",
            "deletecollection",
            "get",
            "global_list",
            "global_watch",
            "list",
            "patch",
            "post",
            "put",
            "watch",
        ],
    )
    Status: ClassVar = ScheduleStatus
