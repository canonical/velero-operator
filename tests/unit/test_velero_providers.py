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

# Valid Azure input data
azure_data = {
    "container": "test-container",
    "storage-account": "testaccount",
    "secret-key": "azure-secret-key",
}


@pytest.mark.parametrize(
    "s3_data, expected_config",
    [
        (s3_data_1, {"s3ForcePathStyle": "true"}),
        (s3_data_2, {"region": "us-east-1", "s3Url": "https://s3.amazonaws.com"}),
    ],
)
def test_s3_storage_provider_success(s3_data, expected_config):
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

    assert provider.config_flags == expected_config


def test_s3_storage_provider_invalid_data():
    """Test S3 storage provider initialization with invalid data."""
    with pytest.raises(StorageProviderError) as exc_info:
        S3StorageProvider("s3-plugin-image", {"region": "us-west-1"})
    assert f"{S3StorageConfig.__name__} errors:" in str(exc_info.value)

    with pytest.raises(StorageProviderError) as exc_info:
        S3StorageProvider(
            "s3-plugin-image",
            {
                "secret-key": "secret",
                "access-key": "access",
                "bucket": "bucket",
                "s3-uri-style": "invalid",
            },
        )
    assert f"{S3StorageConfig.__name__} errors: 's3-uri-style'" in str(exc_info.value)


def test_azure_storage_provider_success():
    """Test Azure storage provider initialization with valid data."""
    provider = AzureStorageProvider("azure-plugin-image", azure_data)

    assert provider.plugin == "azure"
    assert provider.bucket == "test-container"
    assert provider.plugin_image == "azure-plugin-image"
    assert provider.path is None

    expected_secret = (
        f"AZURE_STORAGE_ACCOUNT_ACCESS_KEY={azure_data['secret-key']}\n"
        "AZURE_CLOUD_NAME=AzurePublicCloud\n"
    )
    encoded_secret = base64.b64encode(expected_secret.encode()).decode()
    assert provider.secret_data == encoded_secret

    assert provider.config_flags == {
        "storageAccount": "testaccount",
        "storageAccountKeyEnvVar": "AZURE_STORAGE_ACCOUNT_ACCESS_KEY",
    }


def test_azure_storage_provider_invalid_data():
    """Test Azure storage provider initialization with missing required fields."""
    with pytest.raises(StorageProviderError) as exc_info:
        AzureStorageProvider("azure-plugin-image", {"storage-account": "missing-container"})
    assert f"{AzureStorageConfig.__name__} errors:" in str(exc_info.value)
