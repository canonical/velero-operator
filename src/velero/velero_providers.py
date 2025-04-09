# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Velero Storage Provider classes."""

import base64
from abc import ABC, abstractmethod
from typing import Dict, Optional, Type

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class StorageProviderError(Exception):
    """Base class for storage provider exceptions."""


class StorageConfig(BaseModel):
    """Base Pydantic model for storage config."""

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def get_required(cls) -> str:
        """Return a string representation of the model."""
        required_fields = [
            f.alias or name for name, f in cls.model_fields.items() if f.is_required()
        ]
        return f"{cls.__name__} required fields: {", ".join(required_fields)}"


class VeleroStorageProvider(ABC):
    """Base class for Velero storage provider."""

    def __init__(
        self, plugin_image: str, data: Dict[str, str], config_cls: Type[StorageConfig]
    ) -> None:
        self._plugin_image = plugin_image
        try:
            self._config = config_cls(**data)
        except ValidationError as ve:
            raise StorageProviderError(config_cls.get_required()) from ve

    @property
    @abstractmethod
    def plugin(self) -> str:  # pragma: no cover
        """Return the storage provider plugin name."""
        ...

    @property
    def plugin_image(self) -> str:
        """Return the storage provider plugin image."""
        return self._plugin_image

    @property
    @abstractmethod
    def bucket(self) -> str:  # pragma: no cover
        """Return the storage bucket name."""
        ...

    @property
    @abstractmethod
    def path(self) -> Optional[str]:  # pragma: no cover
        """Return the storage path."""
        ...

    @property
    @abstractmethod
    def secret_data(self) -> str:  # pragma: no cover
        """Return the base64 encoded secret data for the storage provider."""
        ...

    @property
    @abstractmethod
    def config_flags(self) -> Dict[str, str]:  # pragma: no cover
        """Return the configuration flags for the storage provider."""
        ...

    def _encode_secret(self, secret: str) -> str:
        """Encode the secret data to base64."""
        return base64.b64encode(secret.encode("utf-8")).decode("utf-8")


class S3Config(StorageConfig):
    """Pydantic model for S3 storage config."""

    bucket: str
    region: Optional[str] = Field(None, alias="region")
    endpoint: Optional[str] = Field(None, alias="endpoint")
    path: Optional[str] = Field(None, alias="path")
    access_key: str = Field(alias="access-key")
    secret_key: str = Field(alias="secret-key")


class S3StorageProvider(VeleroStorageProvider):
    """S3 storage provider for Velero."""

    def __init__(self, plugin_image: str, data: Dict[str, str]) -> None:
        self._config: S3Config
        super().__init__(plugin_image, data, S3Config)

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
    def config_flags(self) -> Dict[str, str]:
        """Return the configuration flags for S3 storage provider."""
        flags = {}
        if self._config.endpoint is not None:
            flags["s3Url"] = f"{self._config.endpoint}"
        if self._config.region is not None:
            flags["region"] = f"{self._config.region}"
        return flags


class AzureConfig(StorageConfig):
    """Pydantic model for Azure storage config."""

    container: str
    storage_account: str = Field(alias="storage-account")
    secret_key: str = Field(alias="secret-key")
    path: Optional[str] = Field(None, alias="path")


class AzureStorageProvider(VeleroStorageProvider):
    """Azure storage provider for Velero."""

    def __init__(self, plugin_image: str, data: Dict[str, str]) -> None:
        self._config: AzureConfig
        super().__init__(plugin_image, data, AzureConfig)

    @property
    def plugin(self) -> str:
        """Return the storage provider plugin name."""
        return "azure"

    @property
    def bucket(self) -> str:
        """Return the Azure storage bucket name."""
        return self._config.container

    @property
    def path(self) -> Optional[str]:
        """Return the Azure storage path."""
        return self._config.path

    @property
    def secret_data(self) -> str:
        """Return the base64 encoded secret data for Azure storage provider."""
        secret = (
            f"AZURE_STORAGE_ACCOUNT_ACCESS_KEY={self._config.secret_key}\n"
            "AZURE_CLOUD_NAME=AzurePublicCloud\n"
        )
        return self._encode_secret(secret)

    @property
    def config_flags(self) -> Dict[str, str]:
        """Return the configuration flags for Azure storage provider."""
        return {
            "storageAccount": self._config.storage_account,
            "storageAccountKeyEnvVar": "AZURE_STORAGE_ACCOUNT_ACCESS_KEY",
        }
