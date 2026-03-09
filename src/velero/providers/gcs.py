# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Velero GCS Storage Provider class definitions."""

import json
from typing import Any, Dict, Optional

from pydantic import Field, field_validator

from .classes import StorageConfig, VeleroStorageProvider


class GCSStorageConfig(StorageConfig):
    """Pydantic model for GCS storage config."""

    bucket: str = Field(alias="bucket")
    secret_key: Dict[str, Any] = Field(alias="secret-key")
    storage_class: Optional[str] = Field(None, alias="storage-class")
    path: Optional[str] = Field(None, alias="path")

    @field_validator("bucket")
    @classmethod
    def validate_bucket(cls, v: str) -> str:
        """Validate bucket is not an empty string."""
        if not v:
            raise ValueError("'bucket' cannot be an empty string.")
        return v

    @field_validator("secret_key")
    @classmethod
    def validate_secret_key(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        """Validate secret_key is not an empty dict."""
        if not v:
            raise ValueError("'secret_key' cannot be an empty dict.")
        return v


class GCSStorageProvider(VeleroStorageProvider):
    """GCS storage provider for Velero."""

    def __init__(self, plugin_image: str, data: Dict[str, str]) -> None:
        self._config: GCSStorageConfig
        super().__init__(plugin_image, data, GCSStorageConfig)

    @property
    def plugin(self) -> str:
        """Return the storage provider plugin name."""
        return "gcp"

    @property
    def bucket(self) -> str:
        """Return the GCS bucket name."""
        return self._config.bucket

    @property
    def path(self) -> Optional[str]:
        """Return the GCS storage path."""
        return self._config.path

    @property
    def secret_data(self) -> str:
        """Return the base64 encoded GCP service account JSON."""
        return self._encode_secret(json.dumps(self._config.secret_key))

    @property
    def backup_location_config(self) -> Dict[str, str]:
        """Return the configuration flags for GCS backup location."""
        return {}

    @property
    def volume_snapshot_location_config(self) -> Dict[str, str]:
        """Return the configuration flags for GCS volume snapshot location."""
        return {}
