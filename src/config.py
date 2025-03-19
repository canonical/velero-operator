# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""File containing constants."""

from enum import Enum

PROMETHEUS_METRICS_PORT: int = 8085
VELERO_PATH = "./velero"


class StorageProviders(str, Enum):
    """Storage provider enum."""

    S3 = "s3"
    AZURE = "azure"
