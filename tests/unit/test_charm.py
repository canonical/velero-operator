# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import MagicMock, PropertyMock, patch

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


@pytest.fixture()
def mock_lightkube_client():
    with patch.object(
        VeleroOperatorCharm, "lightkube_client", new_callable=PropertyMock
    ) as mock_client:
        yield VeleroOperatorCharm, mock_client


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
def test_charm_kube_access_failed(mock_lightkube_client, code, expected_status):
    """Test that the charm status is set to Blocked if the charm cannot access the K8s API."""
    # Arrange
    charm, mock_lightkube_client = mock_lightkube_client

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": code}
    api_error = ApiError(request=MagicMock(), response=mock_response)

    mock_client = MagicMock()
    mock_client.list.side_effect = api_error
    mock_lightkube_client.return_value = mock_client

    ctx = testing.Context(charm)

    # Act:
    state_out = ctx.run(ctx.on.install(), testing.State())

    # Assert
    assert state_out.unit_status == expected_status


@patch("velero.Velero.check_velero_nodeagent")
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
    check_velero_nodeagent,
    deployment_ok,
    nodeagent_ok,
    storage_attached,
    expected_status,
    use_node_agent_config,
):
    """Test that the charm status is set correctly based on the deployment and nodeagent status."""
    # Arrange
    check_velero_deployment.return_value = MagicMock(ok=deployment_ok, reason="reason")
    check_velero_nodeagent.return_value = MagicMock(ok=nodeagent_ok, reason="reason")

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
        # check_velero_nodeagent.assert_called_once()
