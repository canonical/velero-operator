# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import base64

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
    "container": "test-container",
    "storage-account": "test-storage-account",
    "secret-key": "test-secret-key",
    "service-principal": None,
}

azure_data_2 = {
    "container": "test-container",
    "storage-account": "test-storage-account",
    "path": "test/path",
    "service-principal": {
        "subscription-id": "test-subscription-id",
        "tenant-id": "test-tenant-id",
        "client-id": "test-client-id",
        "client-secret": "test-client-secret",
    },
    "secret-key": "test-secret-key",
}

# Invalid Azure input data
azure_invalid_data_1 = {
    "storage-account": "account",
    "secret-key": "secret",
}

azure_invalid_data_2 = {
    "container": "container",
    "storage-account": "account",
    "service-principal": {
        "subscription-id": "sub-id",
        "tenant-id": "tenant-id",
        "client-id": "client-id",
    },
}

azure_invalid_data_3 = {
    "container": "container",
    "storage-account": "account",
}


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
                "useAAD": "true",
                "storageAccount": "test-storage-account",
            },
            {},
            (
                "AZURE_SUBSCRIPTION_ID=test-subscription-id\n"
                "AZURE_TENANT_ID=test-tenant-id\n"
                "AZURE_CLIENT_ID=test-client-id\n"
                "AZURE_CLIENT_SECRET=test-client-secret\n"
                "AZURE_CLOUD_NAME=AzurePublicCloud\n"
            ),
        ),
    ],
)
def test_azure_storage_provider_success(
    azure_data, backup_location_config, volume_snapshot_location_config, secret_data
):
    """Test Azure storage provider initialization with valid data."""
    provider = AzureStorageProvider("azure-plugin-image", azure_data)

    assert provider.plugin == "azure"
    assert provider.bucket == "test-container"
    assert provider.plugin_image == "azure-plugin-image"
    assert provider.path == azure_data.get("path")

    encoded_secret = base64.b64encode(secret_data.encode()).decode()
    assert provider.secret_data == encoded_secret

    assert provider.backup_location_config == backup_location_config
    assert provider.volume_snapshot_location_config == volume_snapshot_location_config


@pytest.mark.parametrize(
    "azure_data,error_fields",
    [
        (azure_invalid_data_1, ["'container'"]),
        (azure_invalid_data_2, ["'service-principal.client-secret'"]),
        (azure_invalid_data_3, ["'secret_key' or 'service_principal'"]),
    ],
)
def test_azure_storage_provider_invalid_data(azure_data, error_fields):
    """Test Azure storage provider initialization with missing required fields."""
    with pytest.raises(StorageProviderError) as exc_info:
        AzureStorageProvider("azure-plugin-image", azure_data)

    assert f"{AzureStorageConfig.__name__} errors:" in str(exc_info.value)
    for field in error_fields:
        assert field in str(exc_info.value)
