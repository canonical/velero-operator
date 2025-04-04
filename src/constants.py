# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""File containing constants."""

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
