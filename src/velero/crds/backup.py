# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Subset of the Velero Backup CRD model.

Reference: https://velero.io/docs/v1.16/api-types/backup
"""

from typing import ClassVar, Dict, List, Optional

from lightkube.codecs import resource_registry
from lightkube.core import resource as res
from lightkube.core.schema import DictMixin, dataclass
from lightkube.models import meta_v1


@dataclass
class BackupSpecModel(DictMixin):
    """Backup specification model."""

    storageLocation: str
    volumeSnapshotLocations: Optional[List[str]] = None
    includedNamespaces: Optional[List[str]] = None
    excludedNamespaces: Optional[List[str]] = None
    includedResources: Optional[List[str]] = None
    excludedResources: Optional[List[str]] = None
    orderedResources: Optional[Dict[str, str]] = None
    includeClusterResources: Optional[bool] = None
    labelSelector: Optional[Dict[str, Dict[str, str]]] = None
    ttl: Optional[str] = None
    defaultVolumesToFsBackup: Optional[bool] = None


@dataclass
class BackupStatusModel(DictMixin):
    """Backup status model."""

    version: Optional[int] = None
    expiration: Optional[str] = None
    phase: Optional[str] = None
    validationErrors: Optional[List[str]] = None
    startTimestamp: Optional[str] = None
    completionTimestamp: Optional[str] = None
    volumeSnapshotsAttempted: Optional[int] = None
    volumeSnapshotsCompleted: Optional[int] = None
    backupItemOperationsAttempted: Optional[int] = None
    backupItemOperationsCompleted: Optional[int] = None
    backupItemOperationsFailed: Optional[int] = None
    warnings: Optional[int] = None
    errors: Optional[int] = None
    failureReason: Optional[str] = None


@dataclass
class BackupModel(DictMixin):
    """Backup model representing the Velero Backup CRD."""

    apiVersion: Optional[str] = None
    kind: Optional[str] = None
    metadata: Optional[meta_v1.ObjectMeta] = None
    spec: Optional[BackupSpecModel] = None
    status: Optional[BackupStatusModel] = None


class BackupStatus(res.NamespacedSubResource, BackupStatusModel):
    """Backup status sub-resource for the Velero Backup CRD."""

    _api_info = res.ApiInfo(
        resource=res.ResourceDef("velero.io", "v1", "Backup"),
        parent=res.ResourceDef("velero.io", "v1", "Backup"),
        plural="backups",
        verbs=["get", "patch", "put"],
        action="status",
    )


@resource_registry.register
class Backup(res.NamespacedResourceG, BackupModel):
    """Backup resource for the Velero Backup CRD."""

    _api_info = res.ApiInfo(
        resource=res.ResourceDef("velero.io", "v1", "Backup"),
        plural="backups",
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
    Status: ClassVar = BackupStatus
