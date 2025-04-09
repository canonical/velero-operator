import base64

import pytest

from velero import (
    AzureStorageProvider,
    S3StorageProvider,
    StorageProviderError,
)

# Valid S3 input data
s3_data = {
    "region": "us-east-1",
    "bucket": "test-bucket",
    "access-key": "test-access-key",
    "secret-key": "test-secret-key",
}

# Valid Azure input data
azure_data = {
    "container": "test-container",
    "storage-account": "testaccount",
    "secret-key": "azure-secret-key",
}


def test_s3_storage_provider_success():
    """Test S3 storage provider initialization with valid data."""
    provider = S3StorageProvider("s3-plugin-image", s3_data)

    assert provider.plugin == "aws"
    assert provider.bucket == "test-bucket"
    assert provider.plugin_image == "s3-plugin-image"

    expected_secret = (
        "[default]\n"
        f"aws_access_key_id={s3_data['access-key']}\n"
        f"aws_secret_access_key={s3_data['secret-key']}\n"
    )
    encoded_secret = base64.b64encode(expected_secret.encode()).decode()
    assert provider.secret_data == encoded_secret

    assert provider.config_flags == {"region": "us-east-1"}


def test_s3_storage_provider_invalid_data():
    """Test S3 storage provider initialization with missing required fields."""
    with pytest.raises(StorageProviderError) as exc_info:
        S3StorageProvider("s3-plugin-image", {"region": "us-west-1"})
    assert "S3Config required fields" in str(exc_info.value)


def test_azure_storage_provider_success():
    """Test Azure storage provider initialization with valid data."""
    provider = AzureStorageProvider("azure-plugin-image", azure_data)

    assert provider.plugin == "azure"
    assert provider.bucket == "test-container"
    assert provider.plugin_image == "azure-plugin-image"

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
    assert "AzureConfig required fields" in str(exc_info.value)
