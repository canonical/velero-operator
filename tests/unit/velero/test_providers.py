# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import base64
from unittest.mock import MagicMock, patch

import pytest

from velero import (
    AzureStorageConfig,
    AzureStorageProvider,
    S3StorageConfig,
    S3StorageProvider,
    StorageProviderError,
)

# Valid S3 input data
s3_data_1 = {
    "region": "us-west-1",
    "bucket": "test-bucket",
    "access-key": "test-access-key",
    "secret-key": "test-secret-key",
    "s3-uri-style": "path",
}
s3_data_2 = {
    "region": "us-east-1",
    "bucket": "test-bucket",
    "access-key": "test-access-key",
    "secret-key": "test-secret-key",
    "path": "test/path",
    "endpoint": "https://s3.amazonaws.com",
}

# Invalid S3 input data
s3_invalid_data_1 = {
    "region": "us-west-1",
}

s3_invalid_data_2 = {
    "secret-key": "secret",
    "access-key": "access",
    "bucket": "bucket",
    "s3-uri-style": "invalid",
}

# Valid Azure input data
azure_data_1 = {
    "storage-config": {
        "resource-group": "test-group",
        "secret-key": "test-secret-key",
        "storage-account": "test-storage-account",
        "container": "test-container",
        "endpoint": "https://test-group.blob.core.windows.net",
    },
    "service-principal": None,
}

azure_data_2 = {
    "storage-config": {
        "container": "test-container",
        "storage-account": "test-storage-account",
        "path": "test/path",
        "resource-group": "test-group",
        "secret-key": "test-secret-key",
    },
    "service-principal": {
        "subscription-id": "test-subscription-id",
        "tenant-id": "test-tenant-id",
        "client-id": "test-client-id",
        "client-secret": "test-client-secret",
    },
}

azure_data_3 = {
    "storage-config": {
        "resource-group": "test-group",
        "secret-key": "test-secret-key",
        "storage-account": "test-storage-account",
        "container": "test-container",
    },
    "service-principal": None,
}

# Invalid Azure input data
azure_invalid_data_1 = {
    "storage-config": {
        "storage-account": "account",
        "secret-key": "secret",
        "endpoint": "wasb://invalid-endpoint.com",
    }
}

azure_invalid_data_2 = {
    "storage-config": {
        "container": "container",
        "storage-account": "account",
        "resource-group": "group",
    },
    "service-principal": {
        "subscription-id": "sub-id",
        "tenant-id": "tenant-id",
        "client-id": "client-id",
    },
}

azure_invalid_data_3 = {
    "storage-config": {
        "container": "container",
        "storage-account": "account",
        "resource-group": "group",
    },
}


@pytest.fixture()
def mock_lightkube_client():
    """Mock the lightkube Client in charm.py."""
    mock_lightkube_client = MagicMock()
    with patch("velero.providers.azure.Client", return_value=mock_lightkube_client):
        yield mock_lightkube_client


@pytest.mark.parametrize(
    "s3_data,backup_location_config,volume_snapshot_location_config",
    [
        (s3_data_1, {"s3ForcePathStyle": "true", "region": "us-west-1"}, {"region": "us-west-1"}),
        (
            s3_data_2,
            {"region": "us-east-1", "s3Url": "https://s3.amazonaws.com"},
            {"region": "us-east-1"},
        ),
    ],
)
def test_s3_storage_provider_success(
    s3_data, backup_location_config, volume_snapshot_location_config
):
    """Test S3 storage provider initialization with valid data."""
    provider = S3StorageProvider("s3-plugin-image", s3_data)

    assert provider.plugin == "aws"
    assert provider.bucket == "test-bucket"
    assert provider.plugin_image == "s3-plugin-image"
    assert provider.path == s3_data.get("path")

    expected_secret = (
        "[default]\n"
        f"aws_access_key_id={s3_data['access-key']}\n"
        f"aws_secret_access_key={s3_data['secret-key']}\n"
    )
    encoded_secret = base64.b64encode(expected_secret.encode()).decode()
    assert provider.secret_data == encoded_secret

    assert provider.backup_location_config == backup_location_config
    assert provider.volume_snapshot_location_config == volume_snapshot_location_config


@pytest.mark.parametrize(
    "s3_data,error_fields",
    [
        (s3_invalid_data_1, ["'bucket'", "'access-key'", "'secret-key'"]),
        (s3_invalid_data_2, ["'s3-uri-style'", "'region'"]),
    ],
)
def test_s3_storage_provider_invalid_data(s3_data, error_fields):
    """Test S3 storage provider initialization with invalid data."""
    with pytest.raises(StorageProviderError) as exc_info:
        S3StorageProvider("s3-plugin-image", s3_data)

    assert f"{S3StorageConfig.__name__} errors:" in str(exc_info.value)
    for field in error_fields:
        assert field in str(exc_info.value)


@pytest.mark.parametrize(
    "azure_data,backup_location_config,volume_snapshot_location_config,secret_data",
    [
        (
            azure_data_1,
            {
                "storageAccountURI": "https://test-group.blob.core.windows.net",
                "resourceGroup": "test-group",
                "storageAccount": "test-storage-account",
                "storageAccountKeyEnvVar": "AZURE_STORAGE_ACCOUNT_ACCESS_KEY",
            },
            {},
            (
                "AZURE_STORAGE_ACCOUNT_ACCESS_KEY=test-secret-key\n"
                "AZURE_CLOUD_NAME=AzurePublicCloud\n"
            ),
        ),
        (
            azure_data_2,
            {
                "resourceGroup": "test-group",
                "storageAccount": "test-storage-account",
            },
            {},
            (
                "AZURE_SUBSCRIPTION_ID=test-subscription-id\n"
                "AZURE_TENANT_ID=test-tenant-id\n"
                "AZURE_CLIENT_ID=test-client-id\n"
                "AZURE_CLIENT_SECRET=test-client-secret\n"
                "AZURE_RESOURCE_GROUP=test-ng-group\n"
                "AZURE_CLOUD_NAME=AzurePublicCloud\n"
            ),
        ),
        (
            azure_data_3,
            {
                "resourceGroup": "test-group",
                "storageAccount": "test-storage-account",
                "storageAccountKeyEnvVar": "AZURE_STORAGE_ACCOUNT_ACCESS_KEY",
            },
            {},
            (
                "AZURE_STORAGE_ACCOUNT_ACCESS_KEY=test-secret-key\n"
                "AZURE_CLOUD_NAME=AzurePublicCloud\n"
            ),
        ),
    ],
)
def test_azure_storage_provider_success(
    azure_data,
    backup_location_config,
    volume_snapshot_location_config,
    secret_data,
    mock_lightkube_client,
):
    """Test Azure storage provider initialization with valid data."""
    with patch.object(
        AzureStorageProvider, "_get_node_resource_group", return_value="test-ng-group"
    ):
        provider = AzureStorageProvider(
            "azure-plugin-image",
            azure_data.get("storage-config", {}),
            azure_data.get("service-principal"),
        )

        assert provider.plugin == "azure"
        assert provider.bucket == "test-container"
        assert provider.plugin_image == "azure-plugin-image"
        assert provider.path == azure_data.get("storage-config", {}).get("path")

        encoded_secret = base64.b64encode(secret_data.encode()).decode()
        assert provider.secret_data == encoded_secret

        assert provider.backup_location_config == backup_location_config
        assert provider.volume_snapshot_location_config == volume_snapshot_location_config


@pytest.mark.parametrize(
    "azure_data,error_fields",
    [
        (azure_invalid_data_1, ["'container'", "'resource-group'"]),
        (azure_invalid_data_2, ["'service-principal.client-secret'"]),
        (azure_invalid_data_3, ["'secret_key' or 'service_principal'"]),
    ],
)
def test_azure_storage_provider_invalid_data(azure_data, error_fields):
    """Test Azure storage provider initialization with missing required fields."""
    with pytest.raises(StorageProviderError) as exc_info:
        AzureStorageProvider(
            "azure-plugin-image",
            azure_data.get("storage-config", {}),
            azure_data.get("service-principal"),
        )

    assert f"{AzureStorageConfig.__name__} errors:" in str(exc_info.value)
    for field in error_fields:
        assert field in str(exc_info.value)


def test_azure_storage_provider_get_node_resource_group(mock_lightkube_client):
    """Test _get_node_resource_group method for various scenarios."""
    provider = AzureStorageProvider.__new__(AzureStorageProvider)

    mock_node = MagicMock()
    mock_node.spec.providerID = (
        "azure:///subscriptions/sub-id/resourceGroups/"
        "node-rg/providers/Microsoft.Compute/virtualMachines/vm-name"
    )
    mock_lightkube_client.list.return_value = [mock_node]
    result = provider._get_node_resource_group()
    assert result == "node-rg"

    mock_node.spec.providerID = None
    with pytest.raises(
        StorageProviderError, match="Failed to get the ResourceGroup of the Azure Kubernetes nodes"
    ):
        provider._get_node_resource_group()

    mock_node.spec.providerID = "invalid-provider-id"
    with pytest.raises(
        StorageProviderError, match="Failed to get the ResourceGroup of the Azure Kubernetes nodes"
    ):
        provider._get_node_resource_group()

    mock_lightkube_client.list.return_value = []
    with pytest.raises(
        StorageProviderError, match="Failed to get the ResourceGroup of the Azure Kubernetes nodes"
    ):
        provider._get_node_resource_group()
