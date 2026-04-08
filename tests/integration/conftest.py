# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import dataclasses
import json
import logging
import os
import socket
import subprocess
import uuid
from pathlib import Path

import boto3
import botocore.exceptions
import pytest
import pytest_asyncio
from azure.core.exceptions import ResourceExistsError, ServiceRequestError
from azure.storage.blob import BlobServiceClient
from helpers import k8s_assert_resource_exists
from lightkube import ApiError, Client, codecs
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.core_v1 import Namespace
from pytest_operator.plugin import OpsTest
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)
OBJECT_STORAGE_BUCKET = "testbucket"
MICROCEPH_RGW_PORT = 7480
AZURITE_BLOB_PORT = 10000
AZURITE_ACCOUNT = "devstoreaccount1"
AZURITE_KEY = (
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="
)
K8S_TEST_NAMESPACE = "velero-integration-tests"
K8S_TEST_RESOURCES_YAML_PATH = "./tests/integration/resources/test_resources.yaml.j2"
K8S_TEST_PVC_RESOURCE_NAME = "test-pvc"
K8S_TEST_PVC_FILE_PATH = "test-file"
VELERO_OPERATOR_CHARM_ENV = "VELERO_OPERATOR_CHARM_PATH"
TEST_CHARM_ENV = "TEST_CHARM_PATH"


@dataclasses.dataclass(frozen=True)
class S3ConnectionInfo:
    access_key_id: str
    secret_access_key: str
    bucket: str


@dataclasses.dataclass(frozen=True)
class AzureBlobConnectionInfo:
    secret_key: str
    storage_account: str
    container: str
    resource_group: str


@dataclasses.dataclass(frozen=True)
class GcsConnectionInfo:
    ci: bool
    bucket: str
    service_account_key_json: str  # JSON string of the GCP service account key


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

    subprocess.check_call(["sudo", "snap", "install", "microceph"])
    subprocess.check_call(["sudo", "microceph", "cluster", "bootstrap"])
    subprocess.check_call(["sudo", "microceph", "disk", "add", "loop,1G,3"])
    subprocess.check_call(
        ["sudo", "microceph", "enable", "rgw", "--port", str(MICROCEPH_RGW_PORT)]
    )
    output = subprocess.check_output(
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
        encoding="utf-8",
    )

    key = json.loads(output)["keys"][0]
    access_key = key["access_key"]
    secret_key = key["secret_key"]

    logger.info("Creating microceph bucket")
    create_microceph_bucket(
        OBJECT_STORAGE_BUCKET, access_key, secret_key, f"http://localhost:{MICROCEPH_RGW_PORT}"
    )

    logger.info("Set up microceph successfully")
    return S3ConnectionInfo(access_key, secret_key, OBJECT_STORAGE_BUCKET)


@retry(
    stop=stop_after_attempt(5),
    wait=wait_fixed(1),
    retry=retry_if_exception_type((ServiceRequestError)),
    reraise=True,
)
def create_azurite_container(connection_str: str, container_name: str) -> None:
    """Attempt to create a container in Azurite with retry logic."""
    logger.info("Attempting to create azurite container")
    blob_service_client = BlobServiceClient.from_connection_string(connection_str)
    try:
        blob_service_client.create_container(container_name)
    except ResourceExistsError:
        logger.info("Container %r already exists", container_name)


def setup_azurite() -> AzureBlobConnectionInfo:
    logger.info("Setting up azurite")

    subprocess.check_call(["npm", "install", "-g", "azurite"])
    subprocess.Popen(
        [
            "azurite-blob",
            "-l",
            "/tmp/azurite",
            "--blobHost",
            "0.0.0.0",
            "--blobPort",
            str(AZURITE_BLOB_PORT),
            "--loose",
            "--skipApiVersionCheck",
        ]
    )

    conn_str = (
        f"DefaultEndpointsProtocol=http;"
        f"AccountName={AZURITE_ACCOUNT};"
        f"AccountKey={AZURITE_KEY};"
        f"BlobEndpoint=http://127.0.0.1:10000/{AZURITE_ACCOUNT};"
    )
    logger.info("Creating azurite bucket")
    create_azurite_container(conn_str, OBJECT_STORAGE_BUCKET)

    return AzureBlobConnectionInfo(
        secret_key=AZURITE_KEY,
        storage_account=AZURITE_ACCOUNT,
        container=OBJECT_STORAGE_BUCKET,
        resource_group="test-resource-group",
    )


@pytest.fixture(scope="session")
def gcs_connection_info(tmp_path_factory: pytest.TempPathFactory) -> GcsConnectionInfo:
    """Return GCS connection info based on environment."""
    if is_ci():
        return GcsConnectionInfo(
            ci=True,
            bucket="fake-bucket",
            service_account_key_json=json.dumps(
                {
                    "type": "service_account",
                    "project_id": "my-project-id",
                    "private_key_id": "abcdef1234567890abcdef1234567890abcdef12",
                    "private_key": """
-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC4lsLkyO2Tt59n
67R4GEP8FUO9l+9X2RHcAmvRXTfkzLGw49guI3A7UzGYgbiVMGtYREcIwabXNgjJ
KVfFiJoe1HU3sWt31XIe+e71HcjTqEY2JTMHs3hd1D0G0uYXGDmqcC+3MageAZmc
35hlCOfeeOm2CSM/fzpaEfT0IczO8ui6ldrcJqT/ln7tBcUt//dhIma2UrypE2Lm
aUQ9BtPKKdEwM6PIbvI2lA9TVdPQo34bI3eeRk2lTebQezFZvl24UL2ULrf4RdUK
YwVn984QFfDZP0EBX0NKOk0rPmHVGG7EGJS7fw4ZYP+H5UQrAbxT77rfsT2NNwmv
0N+ohfeXAgMBAAECggEANeLtKlTt5j2or3HD0Xtj/WdHy0Vbfc3ExPGAADKyanzH
MtiQ94co8GitBdR4yjTEYZQtGIVP62u+zNrg4K2sMGvdfFCzCtyo4BoehDgZtJBf
Ttc1On5OGTYoSqGuwfc0fmkZxOUeKwRUj9NGbdhXuD6cG6Q3QgYmRr0PQWXMoG0Q
pEcUvKS2E1+Zew9j/aRf3B+byhHgJC3xTSSVVx7GVtAX/a90mw62oi3vsVB+1Bzq
MhyW9JBJj9PAn4s1KSPT+xR7PsmlsdzGUYEBov9u96z2UEpeBU74PhDgdlRMhBpS
B+Z54DN9Lfhp1PtbkhmnBZr62ywxr4gCKV2Q0sIIQQKBgQD/oeZEkFSPiGkqCUBA
Et20R/+ZaZzeOl2uLjwHYToAkZ5Fis9kYSMQ/ijdZP3qOaYl2cMO4GzE7FraJlwT
xQ1s3UiHWaQZ75udFnzjFoJy80BQUHDj2neHk7Y7P7Jaa00cTk/08KuY2yMut+5b
P7LvquUoioBEurIvqnsJCfJdCQKBgQC42rXIPJnLfupc76RZNBIT/cr42EaeZD60
aRkiJ5ZEBqET5Dwg3KCw4JR9WtTwpY0hPzLLGetUKVR0giRnpuAvwnV5dpUMPnnU
2fMLaQw17RfO7AdIA8frfrrlqtPfRAXLSPpuJAfI7vPnOEPC5f4gHMjW3ebHGm5Z
cMJoMod3nwKBgCuqwUX3DarTF3vJxsLrNhoEroHLS7OebsBBP5nXHuxX85xXgOPZ
v/64G8zt4n3vSRVwJGTXK11cLozTPqlV4Nw21JviUSjpCEEGRWEZSEFQkizmANK7
T+3F6rwmPlY5vBtYuUnTDsz2qgTiAIJv2CYeoDSTrCORbLy9t3Ss0UzZAoGACWx0
6fFU8c/ViMlauoVyCnzctRTpfLeljrLw6hHUkkE4QvhWrGIy+vFoAH/57Q6zhCdh
ooL+wTqeKJZd3r7eHPEv5fJKpOYmddhqkIFZcwJUPWNA98XhkjrSslSkGnSwSu28
fpLtpquv2XC/25a3/tEY2ANV+X56c6rQ7ljtGQcCgYEAu9yRIfvuGR9rqLLQFDzc
XRXLTYR0tRX2BcNLrugEzTcwRYupPGsTYR3KHlKLs5Rpl3oCNXsNwDJw817BomZQ
PkAx5NKbWtUjXqmNOfWeM+lv9EBUJMfdRURj2vofGlOq4sO6IaRMvVSli7zCeD0w
V86RTfnSHLljzUryAcdURX8=
-----END PRIVATE KEY-----
        """,
                    "client_email": "gcs-integrator@my-project-id.iam.gserviceaccount.com",
                    "client_id": "123456789012345678901",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                    "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/gcs-integrator%40my-project-id.iam.gserviceaccount.com",
                    "universe_domain": "googleapis.com",
                }
            ),
        )

    required_env_vars = ["GCS_SERVICE_ACCOUNT_KEY_JSON", "GCS_BUCKET"]
    missing_or_empty = [var for var in required_env_vars if not os.environ.get(var)]
    if missing_or_empty:
        raise RuntimeError(
            f"Missing or empty required GCS environment variables: {', '.join(missing_or_empty)}"
        )

    return GcsConnectionInfo(
        ci=False,
        bucket=os.environ["GCS_BUCKET"],
        service_account_key_json=os.environ["GCS_SERVICE_ACCOUNT_KEY_JSON"],
    )


@pytest.fixture(scope="session")
def s3_connection_info() -> S3ConnectionInfo:
    """Return S3 connection info based on environment."""
    if is_ci():
        return setup_microceph()

    required_env_vars = ["AWS_ACCESS_KEY", "AWS_SECRET_KEY", "AWS_BUCKET"]
    missing_or_empty = [var for var in required_env_vars if not os.environ.get(var)]
    if missing_or_empty:
        raise RuntimeError(
            f"Missing or empty required AWS environment variables: {', '.join(missing_or_empty)}",
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
def azure_connection_info() -> AzureBlobConnectionInfo:
    """Return Azure connection info based on environment."""
    if is_ci():
        return setup_azurite()

    required_env_vars = [
        "AZURE_SECRET_KEY",
        "AZURE_STORAGE_ACCOUNT",
        "AZURE_RESOURCE_GROUP",
        "AZURE_CONTAINER",
    ]
    missing_or_empty = [var for var in required_env_vars if not os.environ.get(var)]
    if missing_or_empty:
        raise RuntimeError(
            f"Missing or empty required Azure environment variables: {', '.join(missing_or_empty)}"
        )

    return AzureBlobConnectionInfo(
        secret_key=os.environ["AZURE_SECRET_KEY"],
        storage_account=os.environ["AZURE_STORAGE_ACCOUNT"],
        container=os.environ["AZURE_CONTAINER"],
        resource_group=os.environ["AZURE_RESOURCE_GROUP"],
    )


@pytest.fixture(scope="session")
def azure_storage_credentials(
    azure_connection_info: AzureBlobConnectionInfo,
) -> dict[str, str]:
    """Return cloud credentials for Azure."""
    return {
        "secret-key": azure_connection_info.secret_key,
    }


@pytest.fixture(scope="session")
def azure_storage_configs(
    azure_connection_info: AzureBlobConnectionInfo,
) -> dict[str, str]:
    """Return cloud configs for Azure."""
    config = {
        "container": azure_connection_info.container,
        "path": f"velero/{uuid.uuid4()}",
        "storage-account": azure_connection_info.storage_account,
        "resource-group": azure_connection_info.resource_group,
    }

    if is_ci():
        config["endpoint"] = f"http://{get_host_ip()}:{AZURITE_BLOB_PORT}/{AZURITE_ACCOUNT}"
    else:
        if endpoint := os.environ.get("AZURE_ENDPOINT"):
            config["endpoint"] = endpoint

    return config


@pytest.fixture(scope="session")
def lightkube_client() -> Client:
    """Return a lightkube client to use in this session."""
    client = Client(field_manager="integration-tests")
    return client


@pytest_asyncio.fixture(scope="module")
async def velero_operator_charm_path(ops_test: OpsTest) -> Path | str:
    """Return prebuilt velero-operator charm path, or build it if not provided."""
    if prebuilt_path := os.environ.get(VELERO_OPERATOR_CHARM_ENV):
        if os.path.exists(prebuilt_path):
            logger.info("Using prebuilt velero charm from %s", prebuilt_path)
            return prebuilt_path
        logger.warning(
            "%s is set to %s but file does not exist; building charm instead",
            VELERO_OPERATOR_CHARM_ENV,
            prebuilt_path,
        )

    logger.info("Building velero-operator charm")
    return await ops_test.build_charm(".")


@pytest_asyncio.fixture(scope="module")
async def test_charm_path(ops_test: OpsTest) -> Path | str:
    """Return prebuilt test charm path, or build it if not provided."""
    if prebuilt_path := os.environ.get(TEST_CHARM_ENV):
        if os.path.exists(prebuilt_path):
            logger.info("Using prebuilt test charm from %s", prebuilt_path)
            return prebuilt_path
        logger.warning(
            "%s is set to %s but file does not exist; building charm instead",
            TEST_CHARM_ENV,
            prebuilt_path,
        )

    logger.info("Building integration test charm")
    return await ops_test.build_charm("tests/integration/test_charm")


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
