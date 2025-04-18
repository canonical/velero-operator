# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import dataclasses
import json
import logging
import os
import socket
import subprocess
import uuid
from typing import Optional

import boto3
import botocore.exceptions
import pytest
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)
MICROCEPH_BUCKET = "testbucket"
MICROCEPH_RGW_PORT = 7480


@dataclasses.dataclass(frozen=True)
class S3ConnectionInfo:
    access_key_id: str
    secret_access_key: str
    bucket: str


@dataclasses.dataclass(frozen=True)
class AzureConnectionInfo:
    secret_key: str
    storage_account: str
    container: str


def is_ci() -> bool:
    """Detect whether we're running in a CI environment."""
    return os.environ.get("CI") == "true"


def get_host_ip() -> str:
    """Figure out the host IP address accessible from pods in CI."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
    finally:
        s.close()
    return ip


@retry(
    stop=stop_after_attempt(5),
    wait=wait_fixed(1),
    retry=retry_if_exception_type(botocore.exceptions.EndpointConnectionError),
    reraise=True,
)
def create_microceph_bucket(
    bucket_name: str, access_key: str, secret_key: str, endpoint: str
) -> None:
    """Attempt to create a bucket in MicroCeph with retry logic."""
    logger.info("Attempting to create microceph bucket")
    s3_client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    s3_client.create_bucket(Bucket=bucket_name)


def setup_microceph() -> S3ConnectionInfo:
    """Set up microceph for testing."""
    logger.info("Setting up microceph")

    subprocess.run(["sudo", "snap", "install", "microceph"], check=True)
    subprocess.run(["sudo", "microceph", "cluster", "bootstrap"], check=True)
    subprocess.run(["sudo", "microceph", "disk", "add", "loop,4G,3"], check=True)
    subprocess.run(
        ["sudo", "microceph", "enable", "rgw", "--port", str(MICROCEPH_RGW_PORT)], check=True
    )
    output = subprocess.run(
        [
            "sudo",
            "microceph.radosgw-admin",
            "user",
            "create",
            "--uid",
            "test",
            "--display-name",
            "test",
        ],
        capture_output=True,
        check=True,
        encoding="utf-8",
    ).stdout

    key = json.loads(output)["keys"][0]
    access_key = key["access_key"]
    secret_key = key["secret_key"]

    logger.info("Creating microceph bucket")
    create_microceph_bucket(
        MICROCEPH_BUCKET, access_key, secret_key, f"http://localhost:{MICROCEPH_RGW_PORT}"
    )

    logger.info("Set up microceph successfully")
    return S3ConnectionInfo(access_key, secret_key, MICROCEPH_BUCKET)


@pytest.fixture(scope="session")
def s3_connection_info() -> S3ConnectionInfo:
    """Return S3 connection info based on environment."""
    if is_ci():
        return setup_microceph()

    required_env_vars = ["AWS_ACCESS_KEY", "AWS_SECRET_KEY", "AWS_BUCKET"]
    missing_or_empty = [var for var in required_env_vars if not os.environ.get(var)]
    if missing_or_empty:
        raise RuntimeError(
            f"Missing or empty required AWS environment variables: {", ".join(missing_or_empty)}",
        )

    return S3ConnectionInfo(
        access_key_id=os.environ["AWS_ACCESS_KEY"],
        secret_access_key=os.environ["AWS_SECRET_KEY"],
        bucket=os.environ["AWS_BUCKET"],
    )


@pytest.fixture(scope="session")
def s3_cloud_credentials(
    s3_connection_info: S3ConnectionInfo,
) -> dict[str, str]:
    """Return cloud credentials for S3."""
    return {
        "access-key": s3_connection_info.access_key_id,
        "secret-key": s3_connection_info.secret_access_key,
    }


@pytest.fixture(scope="session")
def s3_cloud_configs(s3_connection_info: S3ConnectionInfo) -> dict[str, str]:
    """Return cloud configs for S3."""
    config = {
        "bucket": s3_connection_info.bucket,
        "path": f"velero/{uuid.uuid4()}",
    }

    if is_ci():
        config["endpoint"] = f"http://{get_host_ip()}:{MICROCEPH_RGW_PORT}"
        config["s3-uri-style"] = "path"
    else:
        config["endpoint"] = os.environ.get("AWS_ENDPOINT", "https://s3.amazonaws.com")
        config["s3-uri-style"] = os.environ.get("AWS_S3_URI_STYLE", "path")
        config["region"] = os.environ.get("AWS_REGION", "us-east-2")

    return config


@pytest.fixture(scope="session")
def azure_connection_info() -> Optional[AzureConnectionInfo]:
    """Return Azure connection info based on environment."""
    required_env_vars = ["AZURE_SECRET_KEY", "AZURE_STORAGE_ACCOUNT", "AZURE_CONTAINER"]
    missing_or_empty = [var for var in required_env_vars if not os.environ.get(var)]
    if missing_or_empty:
        logger.warning(
            "Missing or empty required Azure environment variables: %s - skipping Azure tests",
            {", ".join(missing_or_empty)},
        )
        return None

    return AzureConnectionInfo(
        secret_key=os.environ["AZURE_SECRET_KEY"],
        storage_account=os.environ["AZURE_STORAGE_ACCOUNT"],
        container=os.environ["AZURE_CONTAINER"],
    )


@pytest.fixture(scope="session")
def azure_cloud_credentials(
    azure_connection_info: Optional[AzureConnectionInfo],
) -> Optional[dict[str, str]]:
    """Return cloud credentials for Azure."""
    if azure_connection_info is None:
        return None
    return {
        "secret-key": azure_connection_info.secret_key,
    }


@pytest.fixture(scope="session")
def azure_cloud_configs(
    azure_connection_info: Optional[AzureConnectionInfo],
) -> Optional[dict[str, str]]:
    """Return cloud configs for Azure."""
    if azure_connection_info is None:
        return None
    return {
        "container": azure_connection_info.container,
        "path": f"velero/{uuid.uuid4()}",
        "storage-account": azure_connection_info.storage_account,
    }
