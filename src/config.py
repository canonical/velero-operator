# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""File containing constants."""

VELERO_PATH = "./velero"
VELERO_IMAGE_CONFIG_KEY = "velero-image"
VELERO_AWS_PLUGIN_CONFIG_KEY = "velero-aws-plugin-image"
VELERO_AZURE_PLUGIN_CONFIG_KEY = "velero-azure-plugin-image"
USE_NODE_AGENT_CONFIG_KEY = "use-node-agent"

K8S_CHECK_ATTEMPTS = 30
K8S_CHECK_DELAY = 2
K8S_CHECK_OBSERVATIONS = 5
