# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""File containing constants."""

from enum import Enum

PROMETHEUS_METRICS_PORT: int = 8085
K8S_CHECK_ATTEMPTS = 60
K8S_CHECK_DELAY = 2  # 2 seconds
K8S_CHECK_OBSERVATIONS = 5
VELERO_PATH = "./velero"


class StorageProviders(str, Enum):
    """Storage provider enum."""

    S3 = "s3"
    AZURE = "azure"
