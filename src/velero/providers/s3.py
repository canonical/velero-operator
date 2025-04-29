# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Velero S3 Storage Provider class definitions."""

from enum import Enum
from typing import Dict, Optional

from pydantic import Field

from .classes import StorageConfig, VeleroStorageProvider


class S3UriStyle(str, Enum):
    """Enum for S3 URI styles."""

    PATH = "path"
    VIRTUAL_HOSTED = "virtual"


class S3StorageConfig(StorageConfig):
    """Pydantic model for S3 storage config."""

    bucket: str
    region: Optional[str] = Field(None, alias="region")
    endpoint: Optional[str] = Field(None, alias="endpoint")
    path: Optional[str] = Field(None, alias="path")
    s3_uri_style: Optional[S3UriStyle] = Field(None, alias="s3-uri-style")
    access_key: str = Field(alias="access-key")
    secret_key: str = Field(alias="secret-key")


class S3StorageProvider(VeleroStorageProvider):
    """S3 storage provider for Velero."""

    def __init__(self, plugin_image: str, data: Dict[str, str]) -> None:
        self._config: S3StorageConfig
        super().__init__(plugin_image, data, S3StorageConfig)

    @property
    def plugin(self) -> str:
        """Return the storage provider plugin name."""
        return "aws"

    @property
    def bucket(self) -> str:
        """Return the S3 bucket name."""
        return self._config.bucket

    @property
    def path(self) -> Optional[str]:
        """Return the S3 storage path."""
        return self._config.path

    @property
    def secret_data(self) -> str:
        """Return the base64 encoded secret data for S3 storage provider."""
        secret = (
            "[default]\n"
            f"aws_access_key_id={self._config.access_key}\n"
            f"aws_secret_access_key={self._config.secret_key}\n"
        )
        return self._encode_secret(secret)

    @property
    def backup_location_config(self) -> Dict[str, str]:
        """Return the configuration flags for S3 backup location."""
        flags = {}
        if self._config.endpoint is not None:
            flags["s3Url"] = self._config.endpoint
        if self._config.region is not None:
            flags["region"] = self._config.region
        if self._config.s3_uri_style == S3UriStyle.PATH:
            flags["s3ForcePathStyle"] = "true"
        return flags

    @property
    def volume_snapshot_location_config(self) -> Dict[str, str]:
        """Return the configuration flags for S3 volume snapshot location."""
        flags = {}
        if self._config.region is not None:
            flags["region"] = self._config.region
        return flags
