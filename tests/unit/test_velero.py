# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import subprocess
from subprocess import CalledProcessError
from unittest.mock import MagicMock, PropertyMock, patch

import httpx
import pytest
from lightkube import ApiError
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.apiextensions_v1 import CustomResourceDefinition
from lightkube.resources.apps_v1 import DaemonSet, Deployment
from lightkube.resources.core_v1 import Secret
from lightkube.resources.rbac_authorization_v1 import ClusterRoleBinding
from lightkube.types import PatchType

from constants import (
    VELERO_BACKUP_LOCATION_NAME,
    VELERO_DEPLOYMENT_NAME,
    VELERO_NODE_AGENT_NAME,
    VELERO_SECRET_KEY,
    VELERO_SECRET_NAME,
    VELERO_VOLUME_SNAPSHOT_LOCATION_NAME,
)
from k8s_utils import K8sResource
from velero import Velero, VeleroError

NAMESPACE = "test-namespace"
VELERO_IMAGE = "velero/velero:latest"
VELERO_BINARY = "/usr/local/bin/velero"
VELERO_EXPECTED_FLAGS = [
    f"--image={VELERO_IMAGE}",
    f"--namespace={NAMESPACE}",
    "--no-default-backup-location",
    "--no-secret",
    "--use-volume-snapshots=false",
]


@pytest.fixture(autouse=True)
def mock_run():
    """Mock subprocess.check_output to return a string."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = "stdout"
        yield mock_run


@pytest.fixture(autouse=True)
def mock_check_output():
    """Mock subprocess.check_output to return a string."""
    with patch("subprocess.check_output") as mock_check_output:
        mock_check_output.return_value = "stdout"
        yield mock_check_output


@pytest.fixture()
def mock_run_failing(mock_run):
    """Mock subprocess.check_run to raise a CalledProcessError."""
    cpe = CalledProcessError(cmd="", returncode=1, stderr="stderr", output="stdout")
    mock_run.return_value = None
    mock_run.side_effect = cpe
    yield mock_run


@pytest.fixture()
def mock_check_output_failing(mock_check_output):
    """Mock subprocess.check_output to raise a CalledProcessError."""
    cpe = CalledProcessError(cmd="", returncode=1, stderr="stderr", output="stdout")
    mock_check_output.side_effect = cpe
    yield mock_check_output


@pytest.fixture()
def mock_lightkube_client():
    """Mock the lightkube Client in velero.py."""
    return MagicMock()


@pytest.fixture()
def velero():
    """Return a Velero instance."""
    return Velero(velero_binary_path=VELERO_BINARY, namespace=NAMESPACE)


@pytest.fixture()
def mock_velero_all_resources():
    """Mock the _all_resources method in Velero."""
    with patch.object(Velero, "_all_resources", new_callable=PropertyMock) as mock_all_resources:
        yield mock_all_resources


def test_velero_correct_crb_name():
    """Check the correct cluster role binding name is returned."""
    velero_1 = Velero(velero_binary_path=VELERO_BINARY, namespace=NAMESPACE)
    assert velero_1._velero_crb_name == "velero-test-namespace"

    velero_2 = Velero(velero_binary_path=VELERO_BINARY, namespace="velero")
    assert velero_2._velero_crb_name == "velero"


@pytest.mark.parametrize(
    "use_node_agent",
    [True, False],
)
def test_install_success(use_node_agent, mock_run, velero, mock_lightkube_client):
    """Check velero.install calls the binary successfully with the expected arguments."""
    velero.install(mock_lightkube_client, VELERO_IMAGE, use_node_agent)

    expected_call_args = [VELERO_BINARY, "install"]
    expected_call_args.extend(VELERO_EXPECTED_FLAGS)
    expected_call_args.append(f"--use-node-agent={use_node_agent}")
    mock_run.assert_called_once_with(
        expected_call_args, check=True, capture_output=True, text=True
    )
    mock_lightkube_client.create.assert_called_once()


def test_install_run_failed(caplog, mock_run_failing, velero, mock_lightkube_client):
    """Check velero.install raises a VeleroError when the subprocess call fails."""
    with pytest.raises(VeleroError):
        velero.install(mock_lightkube_client, VELERO_IMAGE, False)
    assert "'velero install' command returned non-zero exit code: 1." in caplog.text
    assert "stdout: stdout" in caplog.text
    assert "stderr: stderr" in caplog.text


def test_install_api_error(caplog, mock_run, velero, mock_lightkube_client):
    """Check velero.install raises a VeleroError when the API call fails."""
    mock_lightkube_client.create.side_effect = ApiError(
        request=MagicMock(),
        response=MagicMock(),
    )
    with pytest.raises(VeleroError):
        velero.install(mock_lightkube_client, VELERO_IMAGE, False)


def test_check_velero_deployment_success(mock_lightkube_client):
    """Check check_velero_deployment returns None when the deployment is ready."""
    mock_deployment = MagicMock()
    mock_deployment.status.conditions = [MagicMock(type="Available", status="True")]
    mock_lightkube_client.get.return_value = mock_deployment

    assert Velero.check_velero_deployment(mock_lightkube_client, "velero") is None


def test_check_velero_deployment_unavailable(mock_lightkube_client):
    """Check check_velero_deployment raises a VeleroError when the deployment is not ready."""
    mock_deployment = MagicMock()
    mock_deployment.spec.selector.matchLabels = {}
    mock_deployment.status.conditions = [
        MagicMock(type="Available", status="False", message="not ready")
    ]
    mock_lightkube_client.get.return_value = mock_deployment

    with pytest.raises(VeleroError) as ve:
        Velero.check_velero_deployment(mock_lightkube_client, "velero")
    assert str(ve.value) == "Velero Deployment is not ready: not ready"


def test_check_velero_deployment_unavailable_no_pod_status(mock_lightkube_client):
    """Check check_velero_deployment raises a VeleroError when there are no pods."""
    mock_deployment = MagicMock()
    mock_deployment.status.conditions = [
        MagicMock(type="Available", status="False", message="not ready")
    ]
    mock_lightkube_client.get.return_value = mock_deployment

    pod = MagicMock()
    pod.status = None
    mock_lightkube_client.list.return_value = [pod]

    with pytest.raises(VeleroError) as ve:
        Velero.check_velero_deployment(mock_lightkube_client, "velero")
    assert str(ve.value) == "Velero Deployment is not ready: not ready"


def test_check_velero_deployment_unavailable_ready_pod_status(mock_lightkube_client):
    """Check check_velero_deployment raises a VeleroError when there are no failed pod statuses."""
    mock_deployment = MagicMock()
    mock_deployment.status.conditions = [
        MagicMock(type="Available", status="False", message="not ready")
    ]
    mock_lightkube_client.get.return_value = mock_deployment

    ready_state = MagicMock(ready=MagicMock(message="not ready"), terminated=None, waiting=None)
    pod_status = MagicMock(ready=False, state=ready_state)
    pod = MagicMock()
    pod.status.containerStatuses = [pod_status]
    mock_lightkube_client.list.return_value = [pod]

    with pytest.raises(VeleroError) as ve:
        Velero.check_velero_deployment(mock_lightkube_client, "velero")
    assert str(ve.value) == "Velero Deployment is not ready: not ready"


def test_check_velero_deployment_unavailable_with_waiting_pod(mock_lightkube_client):
    """Check heck_velero_deployment raises a VeleroError with the message of waiting pod."""
    mock_deployment = MagicMock()
    mock_deployment.status.conditions = [
        MagicMock(type="Available", status="False", message="not ready")
    ]
    mock_lightkube_client.get.return_value = mock_deployment

    waiting_state = MagicMock(waiting=MagicMock(reason="Image error"), terminated=None)
    pod_status_1 = MagicMock(ready=True, state=None)
    pod_status_2 = MagicMock(ready=False, state=waiting_state)
    pod = MagicMock()
    pod.status.containerStatuses = [pod_status_1, pod_status_2]
    pod.status.initContainerStatuses = []
    mock_lightkube_client.list.return_value = [pod]

    with pytest.raises(VeleroError) as ve:
        Velero.check_velero_deployment(mock_lightkube_client, "velero")
    assert str(ve.value) == "Velero Deployment is not ready: Image error"


def test_check_velero_deployment_unavailable_with_terminated_pod(mock_lightkube_client):
    """Check heck_velero_deployment raises a VeleroError with the message of terminated pod."""
    mock_deployment = MagicMock()
    mock_deployment.status.conditions = [
        MagicMock(type="Available", status="False", message="not ready")
    ]
    mock_lightkube_client.get.return_value = mock_deployment

    terminated_state = MagicMock(waiting=None, terminated=MagicMock(reason="Pod has terminated"))
    pod_status = MagicMock(ready=False, state=terminated_state)
    pod = MagicMock()
    pod.status.containerStatuses = []
    pod.status.initContainerStatuses = [pod_status]
    mock_lightkube_client.list.return_value = [pod]

    with pytest.raises(VeleroError) as ve:
        Velero.check_velero_deployment(mock_lightkube_client, "velero")
    assert str(ve.value) == "Velero Deployment is not ready: Pod has terminated"


def test_check_velero_deployment_no_status(mock_lightkube_client):
    """Check check_velero_deployment raises a VeleroError when the deployment has no status."""
    mock_deployment = MagicMock()
    mock_deployment.status = None
    mock_lightkube_client.get.return_value = mock_deployment

    with pytest.raises(VeleroError) as ve:
        Velero.check_velero_deployment(mock_lightkube_client, "velero")
    assert str(ve.value) == "Velero Deployment is not ready: No status"


def test_check_velero_deployment_no_conditions(mock_lightkube_client):
    """Check check_velero_deployment raises a VeleroError when the deployment has no conditions."""
    mock_deployment = MagicMock()
    mock_deployment.status.conditions = []
    mock_lightkube_client.get.return_value = mock_deployment

    with pytest.raises(VeleroError) as ve:
        Velero.check_velero_deployment(mock_lightkube_client, "velero")
    assert str(ve.value) == "Velero Deployment is not ready: No conditions"


def test_check_velero_deployment_no_available_condition(mock_lightkube_client):
    """Check check_velero_deployment raises a VeleroError when there is no Available condition."""
    mock_deployment = MagicMock()
    mock_deployment.status.conditions = [MagicMock(type="SomeCondition", status="True")]
    mock_lightkube_client.get.return_value = mock_deployment

    with pytest.raises(VeleroError) as ve:
        Velero.check_velero_deployment(mock_lightkube_client, "velero")
    assert str(ve.value) == "Velero Deployment is not ready: No Available condition"


def test_check_velero_deployment_api_error(mock_lightkube_client):
    """Check check_velero_deployment raises a VeleroError when the API call fails."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": 404, "message": "not found"}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    mock_lightkube_client.get.side_effect = api_error

    with pytest.raises(ApiError) as ve:
        Velero.check_velero_deployment(mock_lightkube_client, "velero")
    assert str(ve.value) == "not found"


def test_check_velero_node_agent_success(mock_lightkube_client):
    """Check check_velero_node_agent returns None when the DaemonSet is ready."""
    mock_daemonset = MagicMock()
    mock_daemonset.status.numberAvailable = 3
    mock_daemonset.status.desiredNumberScheduled = 3
    mock_lightkube_client.get.return_value = mock_daemonset

    assert Velero.check_velero_node_agent(mock_lightkube_client, "velero") is None


def test_check_velero_node_agent_not_ready(mock_lightkube_client):
    """Check check_velero_node_agent raises a VeleroError when the DaemonSet is not ready."""
    mock_daemonset = MagicMock()
    mock_daemonset.status.numberAvailable = 1
    mock_daemonset.status.desiredNumberScheduled = 3
    mock_lightkube_client.get.return_value = mock_daemonset

    with pytest.raises(VeleroError) as ve:
        Velero.check_velero_node_agent(mock_lightkube_client, "velero")
    assert str(ve.value) == "Velero NodeAgent is not ready: Not all pods are available"


def test_check_velero_node_agent_no_status(mock_lightkube_client):
    """Check check_velero_node_agent raises a VeleroError when the DaemonSet has no status."""
    mock_daemonset = MagicMock()
    mock_daemonset.status = None
    mock_lightkube_client.get.return_value = mock_daemonset

    with pytest.raises(VeleroError) as ve:
        Velero.check_velero_node_agent(mock_lightkube_client, "velero")
    assert str(ve.value) == "Velero NodeAgent is not ready: No status"


def test_check_velero_node_agent_api_error(mock_lightkube_client):
    """Check check_velero_node_agent raises a VeleroError when the API call fails."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": 404, "message": "not found"}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    mock_lightkube_client.get.side_effect = api_error

    with pytest.raises(ApiError) as ve:
        Velero.check_velero_node_agent(mock_lightkube_client, "velero")
    assert str(ve.value) == "not found"


def test_check_velero_storage_locations_success(mock_lightkube_client):
    """Check check_velero_storage_locations returns None when the storage locations are ready."""
    mock_storage_location = MagicMock()
    mock_storage_location = {"status": {"phase": "Available"}}
    mock_lightkube_client.get.return_value = mock_storage_location

    assert Velero.check_velero_storage_locations(mock_lightkube_client, "velero") is None


def test_check_velero_storage_locations_not_ready(mock_lightkube_client):
    """Check check_velero_storage_locations raises a VeleroError when not ready."""
    mock_storage_location = MagicMock()
    mock_storage_location = {"status": {"phase": "Unavailable"}}
    mock_lightkube_client.get.return_value = mock_storage_location

    with pytest.raises(VeleroError) as ve:
        Velero.check_velero_storage_locations(mock_lightkube_client, "velero")
    assert (
        str(ve.value)
        == "Velero Storage location is not ready: BackupStorageLocation is unavailable"
    )


def test_check_velero_storage_locations_no_status(mock_lightkube_client):
    """Check check_velero_storage_locations raises a VeleroError when no status."""
    mock_storage_location = MagicMock()
    mock_storage_location = {}
    mock_lightkube_client.get.return_value = mock_storage_location

    with pytest.raises(VeleroError) as ve:
        Velero.check_velero_storage_locations(mock_lightkube_client, "velero")
    assert (
        str(ve.value)
        == "Velero Storage location is not ready: BackupStorageLocation has no status"
    )


def test_check_velero_storage_locations_api_error(mock_lightkube_client):
    """Check check_velero_storage_locations raises a VeleroError when the API call fails."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": 404, "message": "not found"}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    mock_lightkube_client.get.side_effect = api_error

    with pytest.raises(ApiError) as ve:
        Velero.check_velero_storage_locations(mock_lightkube_client, "velero")
    assert str(ve.value) == "not found"


def test_is_installed_success(mock_lightkube_client, velero):
    """Check is_installed returns True when all resources are present."""
    mock_lightkube_client.get.return_value = MagicMock()

    assert velero.is_installed(mock_lightkube_client, use_node_agent=True) is True


def test_is_installed_api_error(mock_lightkube_client, velero):
    """Check is_installed raises a ApiError when the API call fails."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": 500, "message": "not found"}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    mock_lightkube_client.get.side_effect = api_error

    with pytest.raises(ApiError):
        velero.is_installed(mock_lightkube_client, use_node_agent=True)


def test_is_installed_missing_daemonset(mock_lightkube_client, velero):
    """Check is_installed returns False when the DaemonSet is missing."""

    def mock_get(resource_type, name, namespace=None):
        if resource_type is DaemonSet:
            mock_response = MagicMock(spec=httpx.Response)
            mock_response.json.return_value = {"code": 404, "message": "not found"}
            raise ApiError(request=MagicMock(), response=mock_response)
        return MagicMock()

    mock_lightkube_client.get.side_effect = mock_get
    assert velero.is_installed(mock_lightkube_client, use_node_agent=True) is False


def test_is_installed_ignore_daemonset(mock_lightkube_client, velero):
    """Check is_installed ignores the DaemonSet when use_node_agent is False."""

    def mock_get(resource_type, name, namespace=None):
        if resource_type is DaemonSet:
            raise AssertionError("DaemonSet should not be accessed with use_node_agent=False")
        return MagicMock()

    mock_lightkube_client.get.side_effect = mock_get
    assert velero.is_installed(mock_lightkube_client, use_node_agent=False) is True


def test_is_storage_configured_success(mock_lightkube_client, velero):
    """Check is_storage_configured returns True when the storage locations are configured."""
    mock_lightkube_client.get.return_value = MagicMock()

    assert velero.is_storage_configured(mock_lightkube_client) is True


def test_is_storage_configured_missing_secret(mock_lightkube_client, velero):
    """Check is_storage_configured returns False when the storage location is missing."""

    def mock_get(resource_type, name, namespace=None):
        if resource_type is Secret:
            mock_response = MagicMock(spec=httpx.Response)
            mock_response.json.return_value = {"code": 404, "message": "not found"}
            raise ApiError(request=MagicMock(), response=mock_response)
        return MagicMock()

    mock_lightkube_client.get.side_effect = mock_get
    assert velero.is_storage_configured(mock_lightkube_client) is False


def test_remove_success(mock_lightkube_client, velero, mock_velero_all_resources):
    """Tests that Velero.remove calls delete on the correct resources."""
    mock_velero_all_resources.return_value = [
        K8sResource(name="crd", type=CustomResourceDefinition),
        K8sResource(name="ns-resource", type=Deployment),
        K8sResource(name="global-resource", type=ClusterRoleBinding),
    ]
    velero.remove(mock_lightkube_client)

    assert mock_lightkube_client.delete.call_count == 3
    mock_lightkube_client.delete.assert_any_call(CustomResourceDefinition, name="crd")
    mock_lightkube_client.delete.assert_any_call(
        Deployment, name="ns-resource", namespace=NAMESPACE
    )
    mock_lightkube_client.delete.assert_any_call(ClusterRoleBinding, name="global-resource")


def test_remove_404_error(caplog, mock_lightkube_client, velero, mock_velero_all_resources):
    """Tests that Velero.remove handles a 404 error gracefully."""
    mock_velero_all_resources.return_value = [
        K8sResource(name="missing-resource", type=Deployment),
    ]

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": 404}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    mock_lightkube_client.delete.side_effect = api_error

    velero.remove(mock_lightkube_client)

    mock_lightkube_client.delete.assert_called_once_with(
        Deployment, name="missing-resource", namespace=NAMESPACE
    )
    assert "Resource Deployment 'missing-resource' not found, skipping deletion" in caplog.text


def test_remove_api_error(caplog, mock_lightkube_client, velero, mock_velero_all_resources):
    """Tests that Velero.remove handles an API error and logs the error."""
    mock_velero_all_resources.return_value = [
        K8sResource(name="error-resource", type=Deployment),
    ]

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": 500}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    mock_lightkube_client.delete.side_effect = api_error

    velero.remove(mock_lightkube_client)

    mock_lightkube_client.delete.assert_called_once_with(
        Deployment, name="error-resource", namespace=NAMESPACE
    )
    assert "Failed to delete Deployment 'error-resource' resource:" in caplog.text


@patch("velero.core.subprocess.check_output")
@patch("velero.core.codecs.load_all_yaml")
def test_crds_property_success(mock_load_all_yaml, mock_check_output, velero):
    mock_check_output.return_value = "fake-yaml-output"
    mock_load_all_yaml.return_value = [
        CustomResourceDefinition(metadata=ObjectMeta(name="crd-1"), spec=MagicMock()),
        CustomResourceDefinition(metadata=ObjectMeta(name="crd-2"), spec=MagicMock()),
    ]

    crds = velero._crds
    assert crds[0].name == "crd-2"
    assert crds[1].name == "crd-1"
    assert len(crds) == 2


@patch("velero.core.subprocess.check_output", side_effect=subprocess.CalledProcessError(1, "cmd"))
def test_crds_property_cmd_error(mock_check_output, velero):
    with pytest.raises(VeleroError):
        _ = velero._crds


@patch.object(Velero, "_crds", new_callable=PropertyMock)
def test_all_resources_property(mock_velero_crds, velero):
    """Ensure _storage_provider_resources and _all_resources are populated correctly."""
    mock_velero_crds.return_value = [
        K8sResource(name="crd-1", type=CustomResourceDefinition),
        K8sResource(name="crd-2", type=CustomResourceDefinition),
    ]

    all_resources = velero._all_resources
    assert len(all_resources) == len(mock_velero_crds.return_value) + len(
        velero._storage_provider_resources
    ) + len(velero._core_resources)


def test_remove_storage_locations_success(
    mock_lightkube_client, velero, mock_velero_all_resources
):
    """Tests that Velero.remove_storage_locations calls delete on the correct resources."""
    velero.remove_storage_locations(mock_lightkube_client)

    assert mock_lightkube_client.delete.call_count == len(velero._storage_provider_resources)
    for resource in velero._storage_provider_resources:
        mock_lightkube_client.delete.assert_any_call(
            resource.type, name=resource.name, namespace=NAMESPACE
        )

    mock_lightkube_client.patch.assert_called_once_with(
        Deployment,
        VELERO_DEPLOYMENT_NAME,
        {"spec": {"template": {"spec": {"initContainers": None}}}},
        namespace=NAMESPACE,
    )


def test_remove_storage_locations_404_error(caplog, mock_lightkube_client, velero):
    """Tests that Velero.remove_storage_locations handles a 404 error gracefully."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": 404}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    mock_lightkube_client.delete.side_effect = api_error

    velero.remove_storage_locations(mock_lightkube_client)

    for resource in velero._storage_provider_resources:
        mock_lightkube_client.delete.assert_any_call(
            resource.type, name=resource.name, namespace=NAMESPACE
        )
        assert (
            f"Resource {resource.type.__name__} '{resource.name}' not found, skipping deletion"
            in caplog.text
        )

    mock_lightkube_client.patch.assert_called_once_with(
        Deployment,
        VELERO_DEPLOYMENT_NAME,
        {"spec": {"template": {"spec": {"initContainers": None}}}},
        namespace=NAMESPACE,
    )


def test_remove_storage_locations_api_error(
    caplog,
    mock_lightkube_client,
    velero,
):
    """Tests that Velero.remove_storage_locations raises a VeleroError on API error."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": 500}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    mock_lightkube_client.delete.side_effect = api_error

    with pytest.raises(VeleroError):
        velero.remove_storage_locations(mock_lightkube_client)
        resource = velero._storage_provider_resources[0]
        assert (
            f"Failed to delete {resource.type.__name__} '{resource.name}' resource:" in caplog.text
        )
    mock_lightkube_client.patch.assert_not_called()

    mock_lightkube_client.patch.side_effect = api_error
    mock_lightkube_client.delete.side_effect = None
    with pytest.raises(VeleroError):
        velero.remove_storage_locations(mock_lightkube_client)
        assert f"Failed to patch Deployment '{VELERO_DEPLOYMENT_NAME}' resource:" in caplog.text


def test_configure_storage_locations_success(
    caplog,
    mock_lightkube_client,
    velero,
):
    """Tests that Velero.configure_storage_locations calls the correct methods."""
    with (
        patch.object(velero, "_create_storage_secret") as mock_create_secret,
        patch.object(velero, "_add_storage_plugin") as mock_add_plugin,
        patch.object(velero, "_add_backup_location") as mock_add_backup,
        patch.object(velero, "_add_volume_snapshot_location") as mock_add_volume,
    ):
        velero.configure_storage_locations(mock_lightkube_client, MagicMock())

        mock_create_secret.assert_called_once()
        mock_add_plugin.assert_called_once()
        mock_add_backup.assert_called_once()
        mock_add_volume.assert_called_once()

        assert "Velero storage locations configured successfully" in caplog.text


def test_create_storage_secret_success(mock_lightkube_client, velero):
    """Tests that Velero.create_storage_secret calls the correct methods."""
    mock_provider = MagicMock()
    mock_provider.secret_data = "test"

    with patch("velero.core.k8s_create_secret") as mock_k8s_create_secret:
        velero._create_storage_secret(mock_lightkube_client, mock_provider)

        mock_k8s_create_secret.assert_called_once_with(
            mock_lightkube_client,
            VELERO_SECRET_NAME,
            NAMESPACE,
            data={
                VELERO_SECRET_KEY: mock_provider.secret_data,
            },
            labels={
                "component": "velero",
            },
        )


def test_create_storage_secret_api_error(mock_lightkube_client, velero):
    """Tests that Velero.create_storage_secret raises a VeleroError on API error."""
    mock_lightkube_client.create.side_effect = ApiError(
        request=MagicMock(),
        response=MagicMock(),
    )
    with pytest.raises(VeleroError):
        velero._create_storage_secret(mock_lightkube_client, MagicMock())


def test_add_storage_plugin_success(mock_run, velero):
    """Check velero._add_storage_plugin calls the binary successfully with the expected args."""
    provider = MagicMock()
    provider.plugin_image = "test-plugin-image"

    velero._add_storage_plugin(provider)

    expected_call_args = [
        VELERO_BINARY,
        "plugin",
        "add",
        provider.plugin_image,
        "--confirm",
        f"--namespace={NAMESPACE}",
    ]
    mock_run.assert_called_once_with(
        expected_call_args, check=True, capture_output=True, text=True
    )


def test_add_storage_plugin_failed(caplog, mock_run_failing, velero):
    """Check velero._add_storage_plugin raises a VeleroError when the subprocess call fails."""
    provider = MagicMock()
    provider.plugin_image = "test-plugin-image"

    with pytest.raises(VeleroError):
        velero._add_storage_plugin(provider)
    assert "'velero plugin add' command returned non-zero exit code: 1." in caplog.text
    assert "stdout: stdout" in caplog.text
    assert "stderr: stderr" in caplog.text


def test_add_backup_location_success(mock_run, velero):
    """Check velero._add_backup_location calls the binary successfully with the expected args."""
    provider = MagicMock()
    provider.plugin = "test-plugin"
    provider.bucket = "test-bucket"
    provider.path = None
    provider.config_flags = {"region": "us-west-2", "other-flag": "value"}

    velero._add_backup_location(provider)

    expected_call_args = [
        VELERO_BINARY,
        "backup-location",
        "create",
        VELERO_BACKUP_LOCATION_NAME,
        "--provider",
        provider.plugin,
        # "--prefix",
        # provider.path,
        "--bucket",
        provider.bucket,
        "--config",
        f"region={provider.config_flags['region']},other-flag={provider.config_flags['other-flag']}",
        f"--credential={VELERO_SECRET_NAME}={VELERO_SECRET_KEY}",
        "--default",
        f"--namespace={NAMESPACE}",
        "--labels",
        "component=velero",
    ]
    mock_run.assert_called_once_with(
        expected_call_args, check=True, capture_output=True, text=True
    )


def test_add_backup_location_failed(caplog, mock_run_failing, velero):
    """Check _add_backup_location raises a VeleroError when the subprocess call fails."""
    provider = MagicMock()
    provider.plugin = "test-plugin"
    provider.bucket = "test-bucket"
    provider.config_flags = {"region": "us-west-2", "other-flag": "value"}

    with pytest.raises(VeleroError):
        velero._add_backup_location(provider)
    assert "'velero backup-location create' command returned non-zero exit code: 1." in caplog.text
    assert "stdout: stdout" in caplog.text
    assert "stderr: stderr" in caplog.text


def test_add_volume_snapshot_location_success(mock_run, velero):
    """Check _add_volume_snapshot_location calls the binary successfully with the expected args."""
    provider = MagicMock()
    provider.plugin = "test-plugin"
    provider.bucket = "test-bucket"
    provider.config_flags = {"region": "us-west-2", "other-flag": "value"}

    velero._add_volume_snapshot_location(provider)

    expected_call_args = [
        VELERO_BINARY,
        "snapshot-location",
        "create",
        VELERO_VOLUME_SNAPSHOT_LOCATION_NAME,
        "--provider",
        provider.plugin,
        "--config",
        f"region={provider.config_flags['region']},other-flag={provider.config_flags['other-flag']}",
        f"--credential={VELERO_SECRET_NAME}={VELERO_SECRET_KEY}",
        f"--namespace={NAMESPACE}",
        "--labels",
        "component=velero",
    ]
    mock_run.assert_called_once_with(
        expected_call_args, check=True, capture_output=True, text=True
    )


def test_add_volume_snapshot_location_failed(caplog, mock_run_failing, velero):
    """Check _add_volume_snapshot_location raises a VeleroError when the subprocess call fails."""
    provider = MagicMock()
    provider.plugin = "test-plugin"
    provider.bucket = "test-bucket"
    provider.config_flags = {"region": "us-west-2", "other-flag": "value"}

    with pytest.raises(VeleroError):
        velero._add_volume_snapshot_location(provider)
    assert (
        "'velero snapshot-location create' command returned non-zero exit code: 1." in caplog.text
    )
    assert "stdout: stdout" in caplog.text
    assert "stderr: stderr" in caplog.text


def test_run_cli_command_success(mock_check_output, velero):
    """Check run_cli_command executes the command successfully and returns output."""
    command = ["backup", "create", "test-backup"]
    result = velero.run_cli_command(command)

    expected_call_args = [VELERO_BINARY] + command + [f"--namespace={NAMESPACE}"]
    mock_check_output.assert_called_once_with(expected_call_args, text=True)
    assert result == "stdout"


def test_run_cli_command_empty_input(velero):
    """Check run_cli_command raises a VeleroError when the command is empty."""
    command = []

    with pytest.raises(ValueError):
        velero.run_cli_command(command)


def test_run_cli_command_failed(caplog, mock_check_output_failing, velero):
    """Check run_cli_command raises a VeleroError when the subprocess call fails."""
    command = ["backup", "create", "test-backup"]

    with pytest.raises(VeleroError):
        velero.run_cli_command(command)
    expected_call_args = [VELERO_BINARY] + command + [f"--namespace={NAMESPACE}"]
    mock_check_output_failing.assert_called_once_with(expected_call_args, text=True)
    assert "'velero backup create test-backup' returned non-zero exit code: 1." in caplog.text
    assert "stdout: stdout" in caplog.text
    assert "stderr: stderr" in caplog.text


def test_update_velero_deployment_image_success(velero, mock_lightkube_client):
    """Check update_velero_deployment_image runs the patches the expected arguments."""
    expected_deployment_spec = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": VELERO_DEPLOYMENT_NAME,
                            "image": VELERO_IMAGE,
                        }
                    ]
                }
            },
            "strategy": {"type": "Recreate", "rollingUpdate": None},
        }
    }
    velero.update_velero_deployment_image(mock_lightkube_client, VELERO_IMAGE)
    mock_lightkube_client.patch.assert_any_call(
        Deployment,
        VELERO_DEPLOYMENT_NAME,
        expected_deployment_spec,
        namespace=NAMESPACE,
    )


def test_update_velero_node_agent_image_success(velero, mock_lightkube_client):
    """Check update_velero_node_agent_image runs the patches the expected arguments."""
    expected_node_agent_spec = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": VELERO_NODE_AGENT_NAME,
                            "image": VELERO_IMAGE,
                        }
                    ]
                }
            },
            "strategy": {"type": "Recreate", "rollingUpdate": None},
        }
    }
    velero.update_velero_node_agent_image(mock_lightkube_client, VELERO_IMAGE)
    mock_lightkube_client.patch.assert_any_call(
        DaemonSet,
        VELERO_NODE_AGENT_NAME,
        expected_node_agent_spec,
        namespace=NAMESPACE,
    )


def test_update_velero_deployment_image_404_error(caplog, velero, mock_lightkube_client):
    """Check update_velero_deployment_image handles a 404 error gracefully."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": 404, "message": "not found"}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    mock_lightkube_client.patch.side_effect = api_error

    assert velero.update_velero_deployment_image(mock_lightkube_client, VELERO_IMAGE) is None


def test_update_velero_node_agent_image_404_error(caplog, velero, mock_lightkube_client):
    """Check update_velero_node_agent_image handles a 404 error gracefully."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": 404, "message": "not found"}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    mock_lightkube_client.patch.side_effect = api_error

    assert velero.update_velero_node_agent_image(mock_lightkube_client, VELERO_IMAGE) is None


def test_update_velero_deployment_image_api_error(caplog, velero, mock_lightkube_client):
    """Check update_velero_deployment_image raises a VeleroError when the subprocess call fails."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": 505, "message": "error"}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    mock_lightkube_client.patch.side_effect = api_error

    with pytest.raises(VeleroError):
        velero.update_velero_deployment_image(mock_lightkube_client, VELERO_IMAGE)
    assert "Failed to update Velero Deployment image" in caplog.text


def test_update_velero_node_agent_image_api_error(caplog, velero, mock_lightkube_client):
    """Check update_velero_node_agent_image raises a VeleroError when the subprocess call fails."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": 505, "message": "error"}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    mock_lightkube_client.patch.side_effect = api_error

    with pytest.raises(VeleroError):
        velero.update_velero_node_agent_image(mock_lightkube_client, VELERO_IMAGE)
    assert "Failed to update Velero NodeAgent image" in caplog.text


def test_remove_node_agent_success(velero, mock_lightkube_client):
    """Check remove_node_agent removes the node agent."""
    assert velero.remove_node_agent(mock_lightkube_client) is None
    mock_lightkube_client.delete.assert_called_once()


def test_remove_node_agent_404_error(caplog, velero, mock_lightkube_client):
    """Check remove_node_agent handles a 404 error gracefully."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": 404, "message": "not found"}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    mock_lightkube_client.delete.side_effect = api_error

    assert velero.remove_node_agent(mock_lightkube_client) is None


def test_remove_node_agent_api_error(velero, mock_lightkube_client):
    """Check remove_node_agent raises a VeleroError when the API call fails."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": 505, "message": "error"}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    mock_lightkube_client.delete.side_effect = api_error

    with pytest.raises(VeleroError):
        velero.remove_node_agent(mock_lightkube_client)


def test_update_plugin_image_success(velero, mock_lightkube_client):
    """Check update_plugin_image updates the plugin image."""
    velero.update_plugin_image(mock_lightkube_client, VELERO_IMAGE)

    mock_lightkube_client.patch.assert_called_once_with(
        Deployment,
        VELERO_DEPLOYMENT_NAME,
        [
            {
                "op": "replace",
                "path": "/spec/template/spec/initContainers/0/image",
                "value": VELERO_IMAGE,
            }
        ],
        patch_type=PatchType.JSON,
        namespace=NAMESPACE,
    )


def test_update_plugin_image_404_error(caplog, velero, mock_lightkube_client):
    """Check update_plugin_image handles a 404 error gracefully."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": 404, "message": "not found"}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    mock_lightkube_client.patch.side_effect = api_error

    assert velero.update_plugin_image(mock_lightkube_client, VELERO_IMAGE) is None


def test_update_plugin_image_api_error(velero, mock_lightkube_client):
    """Check update_plugin_image raises a VeleroError when the API call fails."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": 505, "message": "error"}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    mock_lightkube_client.patch.side_effect = api_error

    with pytest.raises(VeleroError):
        velero.update_plugin_image(mock_lightkube_client, VELERO_IMAGE)
