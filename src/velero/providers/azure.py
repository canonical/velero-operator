# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Velero Azure Storage Provider class definitions."""

from typing import Dict, Optional

from pydantic import Field

from .classes import StorageConfig, VeleroStorageProvider


class AzureStorageConfig(StorageConfig):
    """Pydantic model for Azure storage config."""

    container: str
    storage_account: str = Field(alias="storage-account")
    secret_key: str = Field(alias="secret-key")
    path: Optional[str] = Field(None, alias="path")


class AzureStorageProvider(VeleroStorageProvider):
    """Azure storage provider for Velero."""

    def __init__(self, plugin_image: str, data: Dict[str, str]) -> None:
        self._config: AzureStorageConfig
        super().__init__(plugin_image, data, AzureStorageConfig)

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
