# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import MagicMock, patch

import httpx
import pytest
from lightkube.core.exceptions import ApiError
from ops import testing

from charm import VeleroOperatorCharm
from config import (
    USE_NODE_AGENT_CONFIG_KEY,
    VELERO_AWS_PLUGIN_CONFIG_KEY,
    VELERO_AZURE_PLUGIN_CONFIG_KEY,
    VELERO_IMAGE_CONFIG_KEY,
)
from velero import VeleroError


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
    """Test that setting an empty value for the image configs the status to Blocked."""
    # Arrange
    ctx = testing.Context(VeleroOperatorCharm)

    # Act:
    state_out = ctx.run(ctx.on.install(), testing.State(config={image_key: ""}))

    # Assert
    assert state_out.unit_status == testing.BlockedStatus(
        f"The config '{image_key}' cannot be empty"
    )


@pytest.mark.parametrize(
    "code, expected_status",
    [
        (403, testing.BlockedStatus("The charm must be deployed with '--trust' flag enabled")),
        (
            404,
            testing.BlockedStatus(
                "Failed to check if charm can access K8s API, check logs for details"
            ),
        ),
    ],
)
def test_charm_k8s_access_failed(mock_lightkube_client, code, expected_status):
    """Test that the charm status is set to Blocked if the charm cannot access the K8s API."""
    # Arrange
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": code}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    mock_lightkube_client.list.side_effect = api_error

    ctx = testing.Context(VeleroOperatorCharm)

    # Act:
    state_out = ctx.run(ctx.on.install(), testing.State())

    # Assert
    assert state_out.unit_status == expected_status


@patch("velero.Velero.check_velero_node_agent")
@patch("velero.Velero.check_velero_deployment")
@pytest.mark.parametrize(
    "deployment_ok, nodeagent_ok, storage_attached, expected_status, use_node_agent_config",
    [
        # Deployment not ready
        (False, True, True, testing.BlockedStatus("Deployment is not ready: reason"), True),
        # NodeAgent not ready
        (True, False, True, testing.BlockedStatus("NodeAgent is not ready: reason"), True),
        # Missing storage relation
        (True, True, False, testing.BlockedStatus("Missing relation: [s3|azure]"), True),
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
    """Test that the charm status is set correctly based on the deployment and nodeagent status."""
    # Arrange
    check_velero_deployment.return_value = MagicMock(ok=deployment_ok, reason="reason")
    check_velero_node_agent.return_value = MagicMock(ok=nodeagent_ok, reason="reason")

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


def test_on_install(mock_velero, mock_lightkube_client):
    """Test that the install event calls Velero.install with the correct arguments."""
    # Arrange
    ctx = testing.Context(VeleroOperatorCharm)

    # Act
    state_out = ctx.run(ctx.on.install(), testing.State())

    # Assert
    mock_velero.install.assert_called_once_with(False)
    assert state_out.unit_status == testing.BlockedStatus("Missing relation: [s3|azure]")


def test_on_install_error(mock_velero, mock_lightkube_client):
    """Test that the install event raises a RuntimeError when Velero installation fails."""
    # Arrange
    mock_velero.install.side_effect = VeleroError("Failed to install Velero")
    ctx = testing.Context(VeleroOperatorCharm)

    # Act
    with pytest.raises(RuntimeError):
        ctx.run(ctx.on.install(), testing.State())


@patch("charm.logger")
@pytest.mark.parametrize(
    "status,message,expected_log_level,expect_exception",
    [
        (testing.ActiveStatus("active"), "active", "info", False),
        (testing.MaintenanceStatus("maintenance"), "maintenance", "info", False),
        (testing.WaitingStatus("waiting"), "waiting", "info", False),
        (testing.BlockedStatus("error"), "error", "warning", False),
        (testing.UnknownStatus(), None, None, True),
    ],
)
def test_log_and_set_status(logger, status, message, expected_log_level, expect_exception):
    ctx = testing.Context(VeleroOperatorCharm)

    with ctx(ctx.on.start(), testing.State()) as manager:
        if expect_exception:
            with pytest.raises(ValueError, match="Unknown status type"):
                manager.charm._log_and_set_status(status)
        else:
            manager.charm._log_and_set_status(status)
            log_method = getattr(logger, expected_log_level)
            log_method.assert_called_once_with(message)

        manager.run()
