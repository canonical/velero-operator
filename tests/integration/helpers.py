# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import subprocess
from pathlib import Path
from typing import Dict, Optional, Type

import yaml
from juju.application import Application
from juju.model import Model
from juju.unit import Unit
from lightkube import ApiError, Client
from lightkube.core.resource import GlobalResource, NamespacedResource
from lightkube.generic_resource import create_namespaced_resource
from lightkube.resources.apps_v1 import Deployment
from lightkube.resources.core_v1 import Pod
from pytest_operator.plugin import OpsTest
from tenacity import (
    Retrying,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    stop_after_delay,
    wait_fixed,
)

TIMEOUT = 60 * 10
CHARM_METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
TEST_CHARM_METADATA = yaml.safe_load(
    Path("tests/integration/test_charm/charmcraft.yaml").read_text()
)
APP_NAME = CHARM_METADATA["name"]
TEST_APP_NAME = TEST_CHARM_METADATA["name"]
MISSING_RELATION_MESSAGE = "Missing relation: [s3-credentials]"
UNTRUST_ERROR_MESSAGE = (
    "The charm must be deployed with '--trust' flag enabled, run 'juju trust ...'"
)
APP_RELATION_NAME = "velero-backups"
TEST_APP_FIRST_RELATION_NAME = "first-velero-backup-config"
TEST_APP_SECOND_RELATION_NAME = "second-velero-backup-config"
READY_MESSAGE = "Unit is Ready"
DEPLOYMENT_IMAGE_ERROR_MESSAGE_1 = "Velero Deployment is not ready: ImagePullBackOff"
DEPLOYMENT_IMAGE_ERROR_MESSAGE_2 = "Velero Deployment is not ready: ErrImagePull"

S3_INTEGRATOR = "s3-integrator"
S3_INTEGRATOR_CHANNEL = "latest/stable"


def get_model(ops_test: OpsTest) -> Model:
    """Return the Juju model of the current test.

    Returns:
        A juju.model.Model instance of the current model.

    Raises:
        AssertionError if the test doesn't have a Juju model.
    """
    model = ops_test.model
    if model is None:
        raise AssertionError("ops_test has a None model.")
    return model


def assert_app_status(app: Application, statuses: list[str]) -> None:
    """Assert that the application has one of the expected statuses.

    Args:
        app: The application to check.
        statuses: A list of expected statuses for the application.

    Raises:
        AssertionError if the application does not have one of the expected statuses.
    """
    for unit in app.units:
        assert unit.workload_status_message in statuses


async def run_charm_action(unit: Unit, charm_action: str, **params) -> dict:
    """Assert that the action is run successfully and returns the results.

    Args:
        unit: The unit to run the action on.
        charm_action: The action to run.
        params: The parameters to pass to the action.

    Raises:
        AssertionError if the action did not complete successfully.

    Returns:
        The results of the action.
    """
    action = await unit.run_action(charm_action, **params)
    action = await action.wait()
    assert action.status == "completed"
    return action.results


@retry(stop=stop_after_delay(60), wait=wait_fixed(2), reraise=True)
def k8s_assert_resource_exists(
    client: Client,
    resource: Type[GlobalResource | NamespacedResource],
    name: str,
    namespace: str,
) -> None:
    """Check if a Kubernetes resource exists.

    Args:
        client: The lightkube client to use for the check.
        resource: The resource type to check.
        name: The name of the object to check.
        namespace: The namespace of the object to check.

    Raises:
        AssertionError: If the resource is not found.
    """
    try:
        if issubclass(resource, NamespacedResource):
            client.get(resource, name=name, namespace=namespace)
        elif issubclass(resource, GlobalResource):
            client.get(resource, name=name)
    except ApiError as ae:
        if ae.response.status_code == 404:
            assert False, f"Resource {resource} {name} not found"
        else:
            raise


@retry(stop=stop_after_delay(60), wait=wait_fixed(2), reraise=True)
def k8s_assert_resource_not_exists(
    client: Client,
    resource: Type[GlobalResource | NamespacedResource],
    name: str,
    namespace: str,
) -> None:
    """Check if a Kubernetes resource does not exist.

    Args:
        client: The lightkube client to use for the check.
        resource: The resource type to check.
        name: The name of the object to check.
        namespace: The namespace of the object to check.

    Raises:
        AssertionError: If the resource is found.
    """
    try:
        if issubclass(resource, NamespacedResource):
            client.get(resource, name=name, namespace=namespace)
        elif issubclass(resource, GlobalResource):
            client.get(resource, name=name)
        assert False, f"Resource {resource.__name__} {name} should not exist"
    except ApiError as ae:
        if ae.response.status_code == 404:
            return
        else:
            raise


def k8s_delete_and_wait(
    client: Client,
    resource: Type[GlobalResource | NamespacedResource],
    name: str,
    *,
    grace_period: int = 0,
    namespace: str = None,  # type: ignore
    timeout_seconds: int = 60,
    interval_seconds: int = 2,
) -> None:
    """Delete an object and wait for it to be deleted.

    Args:
        client: The lightkube client to use for the deletion.
        resource: The resource type to delete.
        name: The name of the object to delete.
        namespace: The namespace of the object to delete.
        grace_period: The grace period for the deletion.
        timeout_seconds: The timeout for waiting for deletion.
        interval_seconds: The interval between retries.

    Raises:
        AssertionError: If the object still exists after the timeout.
    """
    if issubclass(resource, NamespacedResource):
        client.delete(resource, name=name, grace_period=grace_period, namespace=namespace)
    elif issubclass(resource, GlobalResource):
        client.delete(resource, name=name, grace_period=grace_period)

    @retry(
        stop=stop_after_delay(timeout_seconds),
        wait=wait_fixed(interval_seconds),
        retry=retry_if_exception_type((ApiError, AssertionError)),
        reraise=True,
    )
    def wait_for_deletion():
        k8s_assert_resource_not_exists(
            client,
            resource,
            name=name,
            namespace=namespace,
        )

    wait_for_deletion()


@retry(stop=stop_after_delay(60), wait=wait_fixed(2), reraise=True)
def k8s_get_pvc_content(
    client: Client, pod_name: str, namespace: str, pvc_name: str, test_file: str
) -> str:
    """Get the content of a mounted PVC in a pod.

    Args:
        client: The lightkube client to use for the retrieval.
        pod_name: The name of the pod.
        namespace: The namespace of the pod.
        pvc_name: The name of the PVC.
        test_file: The path to the test file in the PVC.

    Raises:
        ValueError: If the pod does not have the PVC mounted or if the mount path is not found.
        SubprocessError: If the kubectl command fails.

    Returns:
        The content of the mounted PVC.
    """
    pod = client.get(Pod, name=pod_name, namespace=namespace)

    if not pod.metadata or not pod.spec:
        raise ValueError("Pod metadata or spec is missing")

    if not pod.status or pod.status.phase != "Running":
        raise ValueError(f"Pod {pod.metadata.name} is not in Running state")

    if not pod.status.containerStatuses or not all(
        container.ready for container in pod.status.containerStatuses
    ):
        raise ValueError(f"Pod {pod.metadata.name} has containers not ready")

    volume_name = next(
        (
            v.name
            for v in pod.spec.volumes or []
            if v.persistentVolumeClaim and v.persistentVolumeClaim.claimName == pvc_name
        ),
        None,
    )
    if not volume_name:
        raise ValueError(f"PVC {pvc_name} not found in pod {pod.metadata.name}")

    for container in pod.spec.containers or []:
        for mount in container.volumeMounts or []:
            if mount.name == volume_name:
                cmd = [
                    "kubectl",
                    "exec",
                    pod.metadata.name,
                    "-n",
                    pod.metadata.namespace,
                    "-c",
                    container.name,
                    "--",
                    "cat",
                    f"{mount.mountPath}/{test_file}",
                ]
                result = subprocess.check_output(cmd, text=True)
                return result.strip()

    raise ValueError(f"Mount path for PVC {pvc_name} not found in pod {pod.metadata.name}")


@retry(stop=stop_after_delay(60), wait=wait_fixed(2), reraise=True)
def k8s_get_deployment(
    client: Client,
    name: str,
    namespace: str,
) -> Deployment:
    """Get the deployment object.

    Args:
        client: The lightkube client to use for the retrieval.
        name: The name of the deployment.
        namespace: The namespace of the deployment.

    Returns:
        The deployment object.

    Raises:
        AssertionError: If the deployment is not found or if there is an API error.
    """
    try:
        return client.get(Deployment, name=name, namespace=namespace)
    except ApiError as e:
        if e.status.code == 404:
            assert False, f"Deployment {name} not found in namespace {namespace}"
        else:
            raise


def k8s_get_velero_deployment_container_args(
    client: Client,
    namespace: str,
) -> list[str]:
    """Get the container args of the Velero deployment.

    Args:
        client: The lightkube client to use for the retrieval.
        name: The name of the deployment.
        namespace: The namespace of the deployment.

    Returns:
        The container args of the Velero deployment.

    Raises:
        AssertionError: If the deployment is not found or if there is an API error.
    """
    deployment = k8s_get_deployment(client, "velero", namespace)

    if not deployment.spec or not deployment.spec.template.spec:
        assert False, "Deployment spec or template spec is missing"

    container = next(
        (c for c in deployment.spec.template.spec.containers if c.name == "velero"),
        None,
    )
    if not container or not container.args:
        assert False, "Container 'velero' not found or args are missing"

    return container.args


@retry(stop=stop_after_delay(60), wait=wait_fixed(2), reraise=True)
def k8s_get_velero_backup(
    client: Client,
    backup_name: str,
    namespace: str,
) -> Dict:
    """Get the Velero backup object.

    Args:
        client: The lightkube client to use for the retrieval.
        backup_name: The name of the backup.
        namespace: The namespace of the backup.

    Returns:
        The Velero backup object.

    Raises:
        AssertionError: If the backup is not found or if there is an API error.
    """
    backup = create_namespaced_resource(
        group="velero.io", version="v1", kind="Backup", plural="backups"
    )

    try:
        return client.get(backup, name=backup_name, namespace=namespace)
    except ApiError as e:
        if e.status.code == 404:
            assert False, f"Backup {backup_name} not found in namespace {namespace}"
        else:
            raise


def verify_pvc_content(
    client: Client, namespace: str, pvc_name: str, file: str, expected_lines: int
) -> None:
    """Verify the content of a PVC after a restore operation.

    Args:
        client: The lightkube client to use for the verification.
        namespace: The namespace where the PVC is located.
        pvc_name: The name of the PVC to verify.
        file: The file within the PVC to check.
        expected_lines: The expected number of lines in the file.

    Raises:
        AssertionError: If the PVC content does not match the expected lines.
    """
    for attempt in Retrying(
        stop=stop_after_attempt(10),
        wait=wait_fixed(3),
        retry=retry_if_exception_type(AssertionError),
        reraise=True,
    ):
        with attempt:
            pods = list(client.list(Pod, namespace=namespace, labels={"pvc": pvc_name}))
            assert len(pods) == 1, "Expected one pod with PVC label"
            assert pods[0].metadata and pods[0].metadata.name, "Pod metadata is missing"

            pod_name = pods[0].metadata.name
            content = k8s_get_pvc_content(client, pod_name, namespace, pvc_name, file)
            assert (
                len(content.splitlines()) == expected_lines
            ), f"PVC content is not as expected, should be {expected_lines} lines after restore"


def is_relation_joined(model: Model, endpoint: str) -> bool:
    """Check if a relation is joined.

    Args:
        model: The Juju model to check.
        endpoint: The name of the relation endpoint to check.
    """
    for rel in model.relations:
        endpoints = [endpoint.name for endpoint in rel.endpoints]
        if endpoint in endpoints:
            return True
    return False


def is_relation_broken(model: Model, endpoint: str) -> bool:
    """Check if a relation is broken.

    Args:
        model: The Juju model to check.
        endpoint: The name of the relation endpoint to check.
    """
    for rel in model.relations:
        endpoints = [endpoint.name for endpoint in rel.endpoints]
        if endpoint in endpoints:
            return False
    return True


async def get_relation_data(
    ops_test: OpsTest,
    application_name: str,
    endpoint: str,
    related_endpoint: Optional[str] = None,
) -> list:
    """Return a list that contains the relation-data.

    Args:
        ops_test: The ops test framework instance
        application_name: The name of the application
        endpoint: The name of the relation endpoint
        related_endpoint: The name of the related endpoint (optional)

    Returns:
        A list of relation data dictionaries.
    """
    model = get_model(ops_test)

    units_ids = [
        app_unit.name.split("/")[1] for app_unit in model.applications[application_name].units
    ]
    assert len(units_ids) > 0
    unit_name = f"{application_name}/{units_ids[0]}"
    raw_data = (await ops_test.juju("show-unit", unit_name))[1]

    if not raw_data:
        raise ValueError(f"No data found for unit {unit_name}")

    data = yaml.safe_load(raw_data)
    relation_data = [v for v in data[unit_name]["relation-info"] if v["endpoint"] == endpoint]

    if len(relation_data) == 0:
        raise ValueError(f"No data found for relation {endpoint}")

    if related_endpoint:
        relation_data = [v for v in relation_data if v["related-endpoint"] == related_endpoint]

    return relation_data


async def get_application_data(
    ops_test: OpsTest,
    application_name: str,
    endpoint: str,
    related_endpoint: Optional[str] = None,
) -> Dict:
    """Return the application data bag of a given application and relation.

    Args:
        ops_test: The ops test framework instance
        application_name: The name of the application
        endpoint: The name of the relation endpoint
        related_endpoint: The name of the related endpoint (optional)

    Returns:
        Application data bag as a dictionary.
    """
    relation_data = await get_relation_data(ops_test, application_name, endpoint, related_endpoint)
    application_data = relation_data[0]["application-data"]
    return application_data
