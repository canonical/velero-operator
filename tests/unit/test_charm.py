# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import MagicMock, patch

import httpx
import pytest
from lightkube.core.exceptions import ApiError
from ops import testing

from charm import VeleroOperatorCharm
from velero import VeleroError

VELERO_IMAGE_CONFIG_KEY = "velero-image"
VELERO_AZURE_PLUGIN_CONFIG_KEY = "velero-azure-plugin-image"
USE_NODE_AGENT_CONFIG_KEY = "use-node-agent"
VELERO_AWS_PLUGIN_CONFIG_KEY = "velero-aws-plugin-image"


@pytest.fixture()
def mock_lightkube_client():
    """Mock the lightkube Client in charm.py."""
    mock_lightkube_client = MagicMock()
    with patch("charm.Client", return_value=mock_lightkube_client):
        yield mock_lightkube_client


@pytest.fixture()
def mock_velero():
    """Mock the Velero class in charm.py."""
    mock_velero = MagicMock()
    with patch("charm.Velero", return_value=mock_velero):
        yield mock_velero


@pytest.mark.parametrize(
    "image_key",
    [
        VELERO_IMAGE_CONFIG_KEY,
        VELERO_AWS_PLUGIN_CONFIG_KEY,
        VELERO_AZURE_PLUGIN_CONFIG_KEY,
    ],
)
def test_invalid_image_config(image_key):
    """Check setting an empty value for the image configs the status to Blocked."""
    # Arrange
    ctx = testing.Context(VeleroOperatorCharm)

    # Act:
    state_out = ctx.run(ctx.on.install(), testing.State(config={image_key: ""}))

    # Assert
    assert state_out.unit_status == testing.BlockedStatus(f"Invalid configuration: {image_key}")


@pytest.mark.parametrize(
    "code, expected_status",
    [
        (
            403,
            testing.BlockedStatus(
                "The charm must be deployed with '--trust' flag enabled, run 'juju trust ...'"
            ),
        ),
        (
            404,
            testing.BlockedStatus(
                "Failed to check if charm can access K8s API, check logs for details"
            ),
        ),
    ],
)
def test_charm_k8s_access_failed(mock_lightkube_client, code, expected_status):
    """Check the charm status is set to Blocked if the charm cannot access the K8s API."""
    # Arrange
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": code}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    mock_lightkube_client.list.side_effect = api_error

    ctx = testing.Context(VeleroOperatorCharm)

    # Act
    state_out = ctx.run(ctx.on.install(), testing.State())

    # Assert
    assert state_out.unit_status == expected_status


@patch("velero.Velero.check_velero_node_agent")
@patch("velero.Velero.check_velero_deployment")
@pytest.mark.parametrize(
    "deployment_ok, nodeagent_ok, storage_attached, expected_status, use_node_agent_config",
    [
        # Deployment not ready
        (False, True, True, testing.BlockedStatus("Velero Deployment is not ready: reason"), True),
        # NodeAgent not ready
        (True, False, True, testing.BlockedStatus("Velero NodeAgent is not ready: reason"), True),
        # All good
        (True, True, True, testing.ActiveStatus("Unit is Ready"), True),
        # All good
        (True, False, True, testing.ActiveStatus("Unit is Ready"), False),
    ],
)
def test_on_update_status(
    check_velero_deployment,
    check_velero_node_agent,
    mock_lightkube_client,
    deployment_ok,
    nodeagent_ok,
    storage_attached,
    expected_status,
    use_node_agent_config,
):
    """Check the charm status is set correctly based on the deployment and nodeagent status."""
    # Arrange
    if not deployment_ok:
        check_velero_deployment.side_effect = VeleroError("reason")
    if not nodeagent_ok:
        check_velero_node_agent.side_effect = VeleroError("reason")

    ctx = testing.Context(VeleroOperatorCharm)

    with ctx(
        ctx.on.update_status(),
        testing.State(config={USE_NODE_AGENT_CONFIG_KEY: use_node_agent_config}),
    ) as manager:
        manager.charm._stored.storage_provider_attached = storage_attached

        # Act
        state_out = manager.run()

        # Assert
        assert state_out.unit_status == expected_status


@pytest.mark.parametrize(
    "velero_installed",
    [True, False],
)
def test_on_install(velero_installed, mock_velero, mock_lightkube_client):
    """Check the install event calls Velero.install with the correct arguments."""
    # Arrange
    ctx = testing.Context(VeleroOperatorCharm)
    mock_velero.is_installed.return_value = velero_installed

    # Act
    state_out = ctx.run(
        ctx.on.install(),
        testing.State(config={VELERO_IMAGE_CONFIG_KEY: "image", USE_NODE_AGENT_CONFIG_KEY: False}),
    )

    # Assert
    if velero_installed:
        mock_velero.install.assert_not_called()
    else:
        mock_velero.install.assert_called_once_with("image", False)
    assert state_out.unit_status == testing.ActiveStatus("Unit is Ready")


def test_on_install_error(mock_velero, mock_lightkube_client):
    """Check the install event raises a RuntimeError when Velero installation fails."""
    # Arrange
    mock_velero.is_installed.return_value = False
    mock_velero.install.side_effect = VeleroError("Failed to install Velero")
    ctx = testing.Context(VeleroOperatorCharm)

    # Act
    state_out = ctx.run(ctx.on.install(), testing.State())

    # Assert
    assert state_out.unit_status == testing.BlockedStatus(
        "Failed to install Velero on the cluster. See juju debug-log for details."
    )


@patch("charm.logger")
@pytest.mark.parametrize(
    "status,message,expected_log_level",
    [
        (testing.ActiveStatus("active"), "active", "info"),
        (testing.MaintenanceStatus("maintenance"), "maintenance", "info"),
        (testing.WaitingStatus("waiting"), "waiting", "info"),
        (testing.BlockedStatus("error"), "error", "warning"),
    ],
)
def test_log_and_set_status(logger, status, message, expected_log_level, mock_lightkube_client):
    """Check _log_and_set_status logs the status message with the correct log level."""
    # Arrange
    ctx = testing.Context(VeleroOperatorCharm)

    # Act and Assert
    with ctx(ctx.on.start(), testing.State()) as manager:
        manager.charm._log_and_set_status(status)
        log_method = getattr(logger, expected_log_level)
        log_method.assert_called_once_with(message)


def test_on_remove(mock_velero, mock_lightkube_client):
    """Test that the install event calls Velero.install with the correct arguments."""
    # Arrange
    ctx = testing.Context(VeleroOperatorCharm)

    # Act
    state_out = ctx.run(ctx.on.remove(), testing.State())

    # Assert
    mock_velero.remove.assert_called_once()
    assert state_out.unit_status == testing.MaintenanceStatus("Removing Velero from the cluster")
