# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import dataclasses
import json
import logging
import os
import socket
import subprocess
import uuid

import boto3
import botocore.exceptions
import pytest
from helpers import k8s_assert_resource_exists
from lightkube import ApiError, Client, codecs
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.core_v1 import Namespace
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)
MICROCEPH_BUCKET = "testbucket"
MICROCEPH_RGW_PORT = 7480
K8S_TEST_NAMESPACE = "velero-integration-tests"
K8S_TEST_RESOURCES_YAML_PATH = "./tests/integration/resources/test_resources.yaml.j2"
K8S_TEST_PVC_RESOURCE_NAME = "test-pvc"
K8S_TEST_PVC_FILE_PATH = "test-file"


@dataclasses.dataclass(frozen=True)
class S3ConnectionInfo:
    access_key_id: str
    secret_access_key: str
    bucket: str


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
    subprocess.run(["sudo", "microceph", "disk", "add", "loop,1G,3"], check=True)
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
        config["region"] = "radosgw"
    else:
        config["endpoint"] = os.environ.get("AWS_ENDPOINT", "https://s3.amazonaws.com")
        config["s3-uri-style"] = os.environ.get("AWS_S3_URI_STYLE", "virtual")
        config["region"] = os.environ.get("AWS_REGION", "us-east-2")

    return config


@pytest.fixture(scope="session")
def lightkube_client() -> Client:
    """Return a lightkube client to use in this session."""
    client = Client(field_manager="integration-tests")
    return client


@pytest.fixture(scope="module")
def k8s_test_resources(lightkube_client: Client):
    """Set up the test K8s resources."""
    namespace = Namespace(metadata=ObjectMeta(name=K8S_TEST_NAMESPACE))
    test_resources = {
        "namespace": namespace,
        "resources": [],
        "test_file_path": K8S_TEST_PVC_FILE_PATH,
        "pvc_name": K8S_TEST_PVC_RESOURCE_NAME,
    }

    try:
        lightkube_client.create(namespace)
        logger.info("Created test K8s namespace: %s", K8S_TEST_NAMESPACE)
    except ApiError as e:
        if e.status.code == 409:
            logger.warning("Namespace %s already exists, skipping creation", K8S_TEST_NAMESPACE)
        else:
            raise

    with open(K8S_TEST_RESOURCES_YAML_PATH) as f:
        for obj in codecs.load_all_yaml(
            f,
            context={
                "pvc_name": K8S_TEST_PVC_RESOURCE_NAME,
                "test_file": K8S_TEST_PVC_FILE_PATH,
            },
        ):
            if obj.metadata and not obj.metadata.namespace:
                obj.metadata.namespace = K8S_TEST_NAMESPACE
            try:
                lightkube_client.create(obj)
                logger.info("Created %s in namespace %s", obj.kind, K8S_TEST_NAMESPACE)
            except ApiError as e:
                if e.status.code == 409:
                    logger.warning("Resource %s already exists, skipping creation", obj.kind)
                else:
                    raise
            test_resources["resources"].append(obj)

    for resource in test_resources["resources"]:
        k8s_assert_resource_exists(
            lightkube_client,
            type(resource),
            name=resource.metadata.name,
            namespace=K8S_TEST_NAMESPACE,
        )

    yield test_resources

    lightkube_client.delete(Namespace, K8S_TEST_NAMESPACE)
    logger.info("Deleted test K8s namespace: %s", K8S_TEST_NAMESPACE)
