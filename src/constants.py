# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""File containing constants."""

from enum import Enum

from lightkube.generic_resource import create_namespaced_resource

VELERO_BINARY_PATH = "./velero"

K8S_CHECK_ATTEMPTS = 15
K8S_CHECK_DELAY = 2
K8S_CHECK_OBSERVATIONS = 5

K8S_CHECK_VELERO_ATTEMPTS = 60
K8S_CHECK_VELERO_DELAY = 5
K8S_CHECK_VELERO_OBSERVATIONS = 3

VELERO_METRICS_PORT = 8085
VELERO_METRICS_SERVICE_NAME = "velero-metrics"
VELERO_METRICS_PATH = "/metrics"

VELERO_DEPLOYMENT_NAME = "velero"
VELERO_NODE_AGENT_NAME = "node-agent"
VELERO_SECRET_NAME = "cloud-credentials"
VELERO_SERVICE_ACCOUNT_NAME = "velero"
VELERO_CLUSTER_ROLE_BINDING_NAME = "velero"
VELERO_BACKUP_LOCATION_NAME = "default"
VELERO_VOLUME_SNAPSHOT_LOCATION_NAME = "default"
VELERO_SECRET_KEY = "creds"

VELERO_ALLOWED_SUBCOMMANDS = {"backup", "restore", "schedule"}

VELERO_BACKUP_LOCATION_RESOURCE = create_namespaced_resource(
    "velero.io", "v1", "BackupStorageLocation", "backupstoragelocations"
)
VELERO_VOLUME_SNAPSHOT_LOCATION_RESOURCE = create_namespaced_resource(
    "velero.io", "v1", "VolumeSnapshotLocation", "volumesnapshotlocations"
)


class StorageRelation(str, Enum):
    """Storage provider enum."""

    S3 = "s3-credentials"
