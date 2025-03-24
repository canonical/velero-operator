from subprocess import CalledProcessError
from unittest.mock import MagicMock, patch

import pytest
from lightkube import ApiError

from velero import CheckResult, StatusError, Velero, VeleroError

NAMESPACE = "test-namespace"
VELERO_IMAGE = "velero/velero:latest"
VELERO_BINARY = "/usr/local/bin/velero"
VELERO_EXPECTED_FLAGS = [
    f"--namespace={NAMESPACE}",
    f"--image={VELERO_IMAGE}",
    "--no-default-backup-location",
    "--no-secret",
    "--use-volume-snapshots=false",
]


@pytest.fixture(autouse=True)
def mocked_check_call():
    with patch("subprocess.check_call") as mocked_check_call:
        mocked_check_call.return_value = 0

        yield mocked_check_call


@pytest.fixture(autouse=True)
def mocked_check_output():
    with patch("subprocess.check_output") as mocked_check_output:
        mocked_check_output.return_value = "stdout"

        yield mocked_check_output


@pytest.fixture()
def mocked_check_call_failing(mocked_check_call):
    cpe = CalledProcessError(cmd="", returncode=1, stderr="stderr", output="stdout")
    mocked_check_call.return_value = None
    mocked_check_call.side_effect = cpe

    yield mocked_check_call


@pytest.fixture()
def mocked_check_output_failing(mocked_check_output):
    cpe = CalledProcessError(cmd="", returncode=1, stderr="stderr", output="stdout")
    mocked_check_output.return_value = None
    mocked_check_output.side_effect = cpe

    yield mocked_check_output


def test_velero_install(mocked_check_call):
    """Tests that velero.install calls the binary successfully with the expected arguments."""
    velero = Velero(
        velero_binary_path=VELERO_BINARY, namespace=NAMESPACE, velero_image=VELERO_IMAGE
    )

    velero.install(False)

    expected_call_args = [VELERO_BINARY, "install"]
    expected_call_args.extend(VELERO_EXPECTED_FLAGS)
    expected_call_args.append("--use-node-agent=False")
    mocked_check_call.assert_called_once_with(expected_call_args)

    velero.install(True)

    expected_call_args[-1] = "--use-node-agent=True"
    mocked_check_call.assert_called_with(expected_call_args)


def test_velero_install_failed(mocked_check_call_failing):
    """Tests that velero.install raises a VeleroError when the subprocess call fails."""
    velero = Velero(
        velero_binary_path=VELERO_BINARY, namespace=NAMESPACE, velero_image=VELERO_IMAGE
    )

    with pytest.raises(VeleroError):
        velero.install(False)


@pytest.fixture
def mock_lightkube_client():
    return MagicMock()


def test_check_velero_deployment_success(mock_lightkube_client):
    mock_deployment = MagicMock()
    mock_deployment.status.conditions = [MagicMock(type="Available", status="True")]
    mock_lightkube_client.get.return_value = mock_deployment

    result = Velero.check_velero_deployment(mock_lightkube_client, "velero")
    assert isinstance(result, CheckResult)
    assert result.ok is True


def test_check_velero_deployment_unavailable(mock_lightkube_client):
    mock_deployment = MagicMock()
    mock_deployment.status.conditions = [
        MagicMock(type="Available", status="False", message="not ready")
    ]
    mock_lightkube_client.get.return_value = mock_deployment

    result = Velero.check_velero_deployment(mock_lightkube_client, "velero")
    assert result.ok is False
    assert isinstance(result.reason, StatusError)


def test_check_velero_deployment_missing_conditions(mock_lightkube_client):
    mock_deployment = MagicMock()
    mock_deployment.status.conditions = []
    mock_lightkube_client.get.return_value = mock_deployment

    result = Velero.check_velero_deployment(mock_lightkube_client, "velero")
    assert result.ok is False
    assert isinstance(result.reason, StatusError)


def test_check_velero_deployment_api_error(mock_lightkube_client):
    mock_lightkube_client.get.side_effect = ApiError(
        request=MagicMock(), response=MagicMock(status_code=500)
    )

    result = Velero.check_velero_deployment(mock_lightkube_client, "velero")
    assert result.ok is False
    assert isinstance(result.reason, ApiError)


def test_check_velero_nodeagent_success(mock_lightkube_client):
    mock_daemonset = MagicMock()
    mock_daemonset.status.numberAvailable = 3
    mock_daemonset.status.desiredNumberScheduled = 3
    mock_lightkube_client.get.return_value = mock_daemonset

    result = Velero.check_velero_nodeagent(mock_lightkube_client, "velero")
    assert result.ok is True


def test_check_velero_nodeagent_not_ready(mock_lightkube_client):
    mock_daemonset = MagicMock()
    mock_daemonset.status.numberAvailable = 1
    mock_daemonset.status.desiredNumberScheduled = 3
    mock_lightkube_client.get.return_value = mock_daemonset

    result = Velero.check_velero_nodeagent(mock_lightkube_client, "velero")
    assert result.ok is False
    assert isinstance(result.reason, StatusError)


def test_check_velero_nodeagent_no_status(mock_lightkube_client):
    mock_daemonset = MagicMock()
    mock_daemonset.status = None
    mock_lightkube_client.get.return_value = mock_daemonset

    result = Velero.check_velero_nodeagent(mock_lightkube_client, "velero")
    assert result.ok is False
    assert isinstance(result.reason, StatusError)


def test_check_velero_nodeagent_api_error(mock_lightkube_client):
    mock_lightkube_client.get.side_effect = ApiError(
        request=MagicMock(), response=MagicMock(status_code=500)
    )

    result = Velero.check_velero_nodeagent(mock_lightkube_client, "velero")
    assert result.ok is False
    assert isinstance(result.reason, ApiError)
