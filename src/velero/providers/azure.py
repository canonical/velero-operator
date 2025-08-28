# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Velero Azure Storage Provider class definitions."""

from typing import Dict, Optional, Self, Union

from pydantic import BaseModel, Field, model_validator

from .classes import StorageConfig, VeleroStorageProvider


class AzureServicePrincipal(BaseModel):
    """Pydantic model for Azure service principal."""

    subscription_id: str = Field(alias="subscription-id")
    tenant_id: str = Field(alias="tenant-id")
    client_id: str = Field(alias="client-id")
    client_secret: str = Field(alias="client-secret")


class AzureStorageConfig(StorageConfig):
    """Pydantic model for Azure storage config."""

    container: str
    storage_account: str = Field(alias="storage-account")
    path: Optional[str] = Field(None, alias="path")

    secret_key: Optional[str] = Field(None, alias="secret-key")
    service_principal: Optional[AzureServicePrincipal] = Field(None, alias="service-principal")

    @model_validator(mode="after")
    def check_credentials(self) -> Self:
        """Ensure either secret_key or service_principal is provided, but not both."""
        if not self.secret_key and not self.service_principal:
            raise ValueError("Either 'secret_key' or 'service_principal' must be provided")
        return self


class AzureStorageProvider(VeleroStorageProvider):
    """Azure storage provider for Velero."""

    def __init__(
        self, plugin_image: str, data: Dict[str, Union[str, Dict[str, str], None]]
    ) -> None:
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
        if self._config.service_principal:
            service_principal = self._config.service_principal
            return self._encode_secret(
                f"AZURE_SUBSCRIPTION_ID={service_principal.subscription_id}\n"
                f"AZURE_TENANT_ID={service_principal.tenant_id}\n"
                f"AZURE_CLIENT_ID={service_principal.client_id}\n"
                f"AZURE_CLIENT_SECRET={service_principal.client_secret}\n"
                "AZURE_CLOUD_NAME=AzurePublicCloud\n"
            )
        return self._encode_secret(
            f"AZURE_STORAGE_ACCOUNT_ACCESS_KEY={self._config.secret_key}\n"
            "AZURE_CLOUD_NAME=AzurePublicCloud\n"
        )

    @property
    def backup_location_config(self) -> Dict[str, str]:
        """Return the configuration flags for Azure storage provider."""
        if self._config.service_principal:
            return {
                "useAAD": "true",
                "storageAccount": self._config.storage_account,
            }
        return {
            "storageAccount": self._config.storage_account,
            "storageAccountKeyEnvVar": "AZURE_STORAGE_ACCOUNT_ACCESS_KEY",
        }

    @property
    def volume_snapshot_location_config(self) -> Dict[str, str]:
        """Return the configuration flags for Azure volume snapshot location."""
        return {}
