# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Subset of the Velero Restore CRD model.

Reference: https://velero.io/docs/v1.16/api-types/restore
"""

from enum import Enum
from typing import ClassVar, List, Optional

from lightkube.codecs import resource_registry
from lightkube.core import resource as res
from lightkube.core.schema import DictMixin, dataclass
from lightkube.models import meta_v1


class ExistingResourcePolicy(str, Enum):
    """Storage provider enum."""

    No = "none"
    Update = "update"


@dataclass
class RestoreSpecModel(DictMixin):
    """Restore specification model."""

    backupName: str
    restorePVs: Optional[bool] = None
    existingResourcePolicy: Optional[ExistingResourcePolicy] = None


@dataclass
class RestoreStatusModel(DictMixin):
    """Restore status model."""

    phase: Optional[str] = None
    validationErrors: Optional[List[str]] = None
    restoreItemOperationsAttempted: Optional[int] = None
    restoreItemOperationsCompleted: Optional[int] = None
    restoreItemOperationsFailed: Optional[int] = None
    warnings: Optional[int] = None
    errors: Optional[int] = None
    failureReason: Optional[str] = None


@dataclass
class RestoreModel(DictMixin):
    """Restore model representing the Velero Restore CRD."""

    apiVersion: Optional[str] = None
    kind: Optional[str] = None
    metadata: Optional[meta_v1.ObjectMeta] = None
    spec: Optional[RestoreSpecModel] = None
    status: Optional[RestoreStatusModel] = None


class RestoreStatus(res.NamespacedSubResource, RestoreStatusModel):
    """Restore status sub-resource for the Velero Restore CRD."""

    _api_info = res.ApiInfo(
        resource=res.ResourceDef("velero.io", "v1", "Restore"),
        parent=res.ResourceDef("velero.io", "v1", "Restore"),
        plural="restores",
        verbs=["get", "patch", "put"],
        action="status",
    )


@resource_registry.register
class Restore(res.NamespacedResourceG, RestoreModel):
    """Restore resource for the Velero Restore CRD."""

    _api_info = res.ApiInfo(
        resource=res.ResourceDef("velero.io", "v1", "Restore"),
        plural="restores",
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
    Status: ClassVar = RestoreStatus
