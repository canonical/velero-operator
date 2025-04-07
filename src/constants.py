# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""File containing constants."""

from enum import Enum

from lightkube.generic_resource import create_namespaced_resource

VELERO_BINARY_PATH = "./velero"

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
VELERO_SECRET_KEY = "creds"

VELERO_BACKUP_LOCATION_RESOURCE = create_namespaced_resource(
    "velero.io", "v1", "BackupStorageLocation", "backupstoragelocations"
)
VELERO_VOLUME_SNAPSHOT_LOCATION_RESOURCE = create_namespaced_resource(
    "velero.io", "v1", "VolumeSnapshotLocation", "volumesnapshotlocations"
)


class StorageProviders(str, Enum):
    """Storage provider enum."""

    S3 = "s3"
    AZURE = "azure"
