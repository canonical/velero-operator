# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""File containing constants."""

from dataclasses import dataclass
from typing import List, Type, Union

from lightkube.core.resource import GlobalResource, NamespacedResource
from lightkube.generic_resource import create_namespaced_resource
from lightkube.resources.apps_v1 import DaemonSet, Deployment
from lightkube.resources.core_v1 import Secret, ServiceAccount
from lightkube.resources.rbac_authorization_v1 import ClusterRoleBinding

VELERO_PATH = "./velero"
VELERO_IMAGE_CONFIG_KEY = "velero-image"
VELERO_AWS_PLUGIN_CONFIG_KEY = "velero-aws-plugin-image"
VELERO_AZURE_PLUGIN_CONFIG_KEY = "velero-azure-plugin-image"
USE_NODE_AGENT_CONFIG_KEY = "use-node-agent"

K8S_CHECK_ATTEMPTS = 30
K8S_CHECK_DELAY = 2
K8S_CHECK_OBSERVATIONS = 5

VELERO_DEPLOYMENT_NAME = "velero"
VELERO_NODE_AGENT_NAME = "node-agent"
VELERO_SECRET_NAME = "cloud-credentials"
VELERO_SERVICE_ACCOUNT_NAME = "velero"
VELERO_CLUSTER_ROLE_BINDING_NAME = "velero"
VELERO_BACKUP_LOCATION_NAME = "default"
VELERO_VOLUME_SNAPSHOT_LOCATION_NAME = "default"


@dataclass
class VeleroResource:
    """Velero Kubernetes resource."""

    name: str
    type: Type[Union[NamespacedResource, GlobalResource]]


VELERO_SERVER_RESOURCES: List[VeleroResource] = [
    VeleroResource(VELERO_DEPLOYMENT_NAME, Deployment),
    VeleroResource(VELERO_NODE_AGENT_NAME, DaemonSet),
    VeleroResource(VELERO_SECRET_NAME, Secret),
    VeleroResource(VELERO_SERVICE_ACCOUNT_NAME, ServiceAccount),
    VeleroResource(VELERO_CLUSTER_ROLE_BINDING_NAME, ClusterRoleBinding),
    VeleroResource(
        VELERO_BACKUP_LOCATION_NAME,
        create_namespaced_resource(
            "velero.io", "v1", "BackupStorageLocation", "backupstoragelocations"
        ),
    ),
    VeleroResource(
        VELERO_VOLUME_SNAPSHOT_LOCATION_NAME,
        create_namespaced_resource(
            "velero.io", "v1", "VolumeSnapshotLocation", "volumesnapshotlocations"
        ),
    ),
]
