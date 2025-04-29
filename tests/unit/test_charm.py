# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import ANY, MagicMock, PropertyMock, patch

import httpx
import pytest
from lightkube.core.exceptions import ApiError
from ops import testing
from scenario import Relation

from charm import VeleroOperatorCharm
from constants import StorageRelation
from velero import S3StorageProvider, VeleroError, VeleroStatusError

VELERO_IMAGE_CONFIG_KEY = "velero-image"
USE_NODE_AGENT_CONFIG_KEY = "use-node-agent"
VELERO_AWS_PLUGIN_CONFIG_KEY = "velero-aws-plugin-image"
RELATIONS = "|".join([r.value for r in StorageRelation])

READY_MESSAGE = "Unit is Ready"
REMOVE_MESSAGE = "Removing Velero from the cluster"
UNTRUST_ERROR_MESSAGE = (
    "The charm must be deployed with '--trust' flag enabled, run 'juju trust ...'"
)
MISSING_RELATION_MESSAGE = f"Missing relation: [{RELATIONS}]"
INSTALL_ERROR_MESSAGE = "Failed to install Velero. See juju debug-log for details."
MANY_RELATIONS_ERROR_MESSAGE = (
    f"Only one Storage Provider should be related at the time: [{RELATIONS}]"
)
K8S_API_ERROR_MESSAGE = "Failed to access K8s API. See juju debug-log for details."
INVALID_CONFIG_MESSAGE = "Invalid configuration: "


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
    ],
)
def test_invalid_image_config(image_key):
    """Check setting an empty value for the image configs the status to Blocked."""
    # Arrange
    ctx = testing.Context(VeleroOperatorCharm)

    # Act:
    state_out = ctx.run(ctx.on.install(), testing.State(config={image_key: ""}))

    # Assert
    assert state_out.unit_status == testing.BlockedStatus(f"{INVALID_CONFIG_MESSAGE}{image_key}")


@pytest.mark.parametrize(
    "code, expected_status",
    [
        (
            403,
            testing.BlockedStatus(UNTRUST_ERROR_MESSAGE),
        ),
        (
            404,
            testing.BlockedStatus(K8S_API_ERROR_MESSAGE),
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
@patch("velero.Velero.check_velero_storage_locations")
@pytest.mark.parametrize(
    "deployment_ok, nodeagent_ok, has_rel, provider_ok, status, use_node_agent",
    [
        # Deployment not ready
        (
            False,
            True,
            True,
            True,
            testing.BlockedStatus("reason"),
            True,
        ),
        # NodeAgent not ready
        (
            True,
            False,
            True,
            True,
            testing.BlockedStatus("reason"),
            True,
        ),
        # No relations
        (
            True,
            True,
            False,
            True,
            testing.BlockedStatus(MISSING_RELATION_MESSAGE),
            True,
        ),
        # Provider not ready
        (
            True,
            True,
            True,
            False,
            testing.BlockedStatus("reason"),
            True,
        ),
        # All good
        (True, True, True, True, testing.ActiveStatus(READY_MESSAGE), True),
        # All good
        (True, False, True, True, testing.ActiveStatus(READY_MESSAGE), False),
    ],
)
def test_on_update_status(
    check_velero_storage_locations,
    check_velero_deployment,
    check_velero_node_agent,
    mock_lightkube_client,
    deployment_ok,
    nodeagent_ok,
    has_rel,
    provider_ok,
    status,
    use_node_agent,
):
    """Check the charm status is set correctly based on the deployment and nodeagent status."""
    # Arrange
    if not deployment_ok:
        check_velero_deployment.side_effect = VeleroStatusError("reason")
    if not nodeagent_ok:
        check_velero_node_agent.side_effect = VeleroStatusError("reason")
    if not provider_ok:
        check_velero_storage_locations.side_effect = VeleroStatusError("reason")

    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3 if has_rel else None
        ctx = testing.Context(VeleroOperatorCharm)

        with ctx(
            ctx.on.update_status(),
            testing.State(config={USE_NODE_AGENT_CONFIG_KEY: use_node_agent}),
        ) as manager:
            # Act
            state_out = manager.run()

            # Assert
            assert state_out.unit_status == status


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
        mock_velero.install.assert_called_once_with(mock_lightkube_client, "image", False)
    assert state_out.unit_status == testing.BlockedStatus(MISSING_RELATION_MESSAGE)


def test_on_install_error(mock_velero, mock_lightkube_client):
    """Check the install event raises a RuntimeError when Velero installation fails."""
    # Arrange
    mock_velero.is_installed.return_value = False
    mock_velero.install.side_effect = VeleroError("Failed to install Velero")
    ctx = testing.Context(VeleroOperatorCharm)

    # Act
    state_out = ctx.run(ctx.on.install(), testing.State())

    # Assert
    assert state_out.unit_status == testing.BlockedStatus(INSTALL_ERROR_MESSAGE)


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
    assert state_out.unit_status == testing.MaintenanceStatus(REMOVE_MESSAGE)


@pytest.mark.parametrize(
    "relations",
    [
        [Relation(endpoint=StorageRelation.S3.value)],
    ],
)
def test_storage_relation_properties(relations, mock_lightkube_client, mock_velero):
    """Test that the storage_relation properties return the correct value."""
    # Arrange
    ctx = testing.Context(VeleroOperatorCharm)

    # Act and Assert
    with ctx(ctx.on.start(), testing.State(relations=relations)) as manager:
        assert manager.charm.storage_relation == StorageRelation(relations[0].endpoint)


@pytest.mark.parametrize(
    "storage_relation,provider_class,relation_data",
    [
        (
            StorageRelation.S3,
            S3StorageProvider,
            {
                "region": "us-east-1",
                "bucket": "test-bucket",
                "access-key": "test-key",
                "secret-key": "test=key",
            },
        ),
    ],
)
def test_storage_relation_changed_success(
    storage_relation, provider_class, relation_data, mock_velero, mock_lightkube_client
):
    """Test that the relation_changed configures the storage provider."""
    # Arrange
    mock_velero.is_storage_configured.return_value = False
    ctx = testing.Context(VeleroOperatorCharm)
    relation = Relation(endpoint=storage_relation.value, remote_app_data=relation_data)

    # Act
    state_out = ctx.run(
        ctx.on.relation_changed(relation),
        testing.State(relations=[relation]),
    )

    # Assert
    mock_velero.remove_storage_locations.assert_called_once()
    mock_velero.is_storage_configured.assert_called_once()
    mock_velero.configure_storage_locations.calls()
    mock_velero.configure_storage_locations.assert_called_once_with(mock_lightkube_client, ANY)
    _, provider = mock_velero.configure_storage_locations.call_args[0]
    assert isinstance(provider, provider_class)
    assert state_out.unit_status == testing.ActiveStatus(READY_MESSAGE)


@pytest.mark.parametrize(
    "storage_relation,relation_data",
    [
        (
            StorageRelation.S3,
            {"test": "test"},
        ),
    ],
)
def test_storage_relation_changed_invalid_config(
    storage_relation, relation_data, mock_velero, mock_lightkube_client
):
    """Test that the relation_changed event sets the status to Blocked."""
    # Arrange
    mock_velero.is_storage_configured.return_value = False
    ctx = testing.Context(VeleroOperatorCharm)
    relation = Relation(endpoint=storage_relation.value, remote_app_data=relation_data)

    # Act
    state_out = ctx.run(
        ctx.on.relation_changed(relation),
        testing.State(relations=[relation]),
    )

    # Assert
    assert state_out.unit_status.name == testing.BlockedStatus.name
    assert INVALID_CONFIG_MESSAGE in state_out.unit_status.message


@pytest.mark.parametrize(
    "provider_configured",
    [True, False],
)
def test_storage_relation_changed_configure(
    provider_configured, mock_velero, mock_lightkube_client
):
    """Test that the relation_changed event calls Velero.configure_storage_locations."""
    # Arrange
    mock_velero.is_storage_configured.return_value = provider_configured
    ctx = testing.Context(VeleroOperatorCharm)
    relation = Relation(
        endpoint=StorageRelation.S3.value,
        remote_app_data={
            "region": "us-east-1",
            "bucket": "test-bucket",
            "access-key": "test-key",
            "secret-key": "test=key",
        },
    )

    # Act
    state_out = ctx.run(
        ctx.on.update_status(),
        testing.State(relations=[relation]),
    )

    # Assert
    if provider_configured:
        mock_velero.remove_storage_locations.assert_not_called()
        mock_velero.configure_storage_locations.assert_not_called()
    else:
        mock_velero.remove_storage_locations.assert_not_called()
        mock_velero.configure_storage_locations.assert_called_once()
    assert state_out.unit_status == testing.ActiveStatus(READY_MESSAGE)


def test_storage_relation_changed_install_error(mock_velero, mock_lightkube_client):
    """Test that the relation_changed event raises an error if configure fails."""
    # Arrange
    mock_velero.is_storage_configured.return_value = False
    mock_velero.configure_storage_locations.side_effect = VeleroError(
        "Failed to add Velero backup location"
    )
    ctx = testing.Context(VeleroOperatorCharm)
    relation = Relation(
        endpoint=StorageRelation.S3.value,
        remote_app_data={
            "bucket": "test-bucket",
            "region": "us-east-1",
            "secret-key": "test-key",
            "access-key": "test-key",
        },
    )

    # Act
    state_out = ctx.run(
        ctx.on.relation_changed(relation),
        testing.State(relations=[relation]),
    )

    # Assert
    assert state_out.unit_status == testing.BlockedStatus(
        "Failed to add Velero backup location. See juju debug-log for details."
    )


def test_storage_relation_broken_success(mock_velero, mock_lightkube_client):
    """Test that the relation_broken event removes the storage provider."""
    # Arrange
    ctx = testing.Context(VeleroOperatorCharm)
    relation = Relation(endpoint=StorageRelation.S3.value)

    # Act
    state_out = ctx.run(
        ctx.on.relation_broken(relation),
        testing.State(relations=[relation]),
    )

    # Assert
    mock_velero.remove_storage_locations.assert_called_once()
    assert state_out.unit_status == testing.BlockedStatus(MISSING_RELATION_MESSAGE)


def test_storage_relation_broken_error(mock_velero, mock_lightkube_client):
    """Test that the relation_departed event raises an error if remove fails."""
    # Arrange
    ctx = testing.Context(VeleroOperatorCharm)
    relation = Relation(endpoint=StorageRelation.S3.value)
    mock_velero.remove_storage_locations.side_effect = VeleroError(
        "Failed to remove storage locations"
    )

    # Act
    state_out = ctx.run(
        ctx.on.relation_broken(relation),
        testing.State(relations=[relation]),
    )

    # Assert
    assert state_out.unit_status == testing.BlockedStatus(
        "Failed to remove storage locations. See juju debug-log for details."
    )


def test_on_run_action_success(
    mock_velero,
    mock_lightkube_client,
):
    """Test the run_cli_action handler."""
    # Arrange
    command = "backup create my-backup"
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        mock_velero.run_cli_command.return_value = "test output"
        ctx = testing.Context(VeleroOperatorCharm)

        # Act
        ctx.run(ctx.on.action("run-cli", params={"command": command}), testing.State())

        # Assert
        mock_velero.run_cli_command.assert_called_once()
        assert ctx.action_results.get("status") == "success"


@pytest.mark.parametrize(
    "command,rel_configured,velero_error",
    [
        # Invalid command
        (
            "invalid-command",
            True,
            None,
        ),
        # Empty command
        (
            "",
            True,
            None,
        ),
        # Storage not configured
        ("backup create my-backup", False, None),
        # Command raises VeleroError
        (
            "backup create my-backup",
            True,
            VeleroError("simulated error"),
        ),
        # Command raises ValueError
        (
            "backup create my-backup",
            True,
            ValueError("simulated error"),
        ),
    ],
)
def test_on_run_action_failed(
    command,
    rel_configured,
    velero_error,
    mock_velero,
    mock_lightkube_client,
):
    """Test the run_cli_action handler when it fails."""
    # Arrange
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3 if rel_configured else None
        mock_velero.is_storage_configured.return_value = rel_configured
        mock_velero.run_cli_command.side_effect = velero_error
        ctx = testing.Context(VeleroOperatorCharm)

        # Act and Assert
        with pytest.raises(testing.ActionFailed):
            ctx.run(ctx.on.action("run-cli", params={"command": command}), testing.State())


@pytest.mark.parametrize(
    "use_node_agent,relation",
    [
        (False, StorageRelation.S3),
        (True, StorageRelation.S3),
        (False, None),
        (True, None),
    ],
)
def test_on_config_changed_success(
    use_node_agent,
    relation,
    mock_lightkube_client,
    mock_velero,
):
    """Test that the config_changed event is handled correctly."""
    # Arrange
    ctx = testing.Context(VeleroOperatorCharm)
    relations = [Relation(endpoint=relation.value)] if relation else []

    # Act
    ctx.run(
        ctx.on.config_changed(),
        testing.State(
            config={
                USE_NODE_AGENT_CONFIG_KEY: use_node_agent,
                VELERO_AWS_PLUGIN_CONFIG_KEY: "aws-image",
                VELERO_IMAGE_CONFIG_KEY: "velero-image",
            },
            relations=relations,
        ),
    )

    # Assert
    if relation:
        if relation == StorageRelation.S3:
            mock_velero.update_plugin_image.assert_called_once_with(
                mock_lightkube_client,
                "aws-image",
            )
        elif relation == StorageRelation.AZURE:
            mock_velero.update_plugin_image.assert_called_once_with(
                mock_lightkube_client,
                "azure-image",
            )
    else:
        mock_velero.update_plugin_image.assert_not_called()

    if use_node_agent:
        mock_velero.remove_node_agent.assert_not_called()
        mock_velero.update_velero_node_agent_image.assert_called_once_with(
            mock_lightkube_client, "velero-image"
        )
    else:
        mock_velero.remove_node_agent.assert_called_once_with(
            mock_lightkube_client,
        )
        mock_velero.update_velero_node_agent_image.assert_not_called()

    mock_velero.update_velero_deployment_image.assert_called_once_with(
        mock_lightkube_client, "velero-image"
    )


def test_on_config_changed_error(
    mock_lightkube_client,
    mock_velero,
):
    """Test that the config_changed event raises an error if update fails."""
    # Arrange
    ctx = testing.Context(VeleroOperatorCharm)
    mock_velero.update_velero_deployment_image.side_effect = VeleroError(
        "Failed to update Velero Deployment image"
    )

    # Act
    state_out = ctx.run(
        ctx.on.config_changed(),
        testing.State(
            config={
                USE_NODE_AGENT_CONFIG_KEY: False,
                VELERO_IMAGE_CONFIG_KEY: "velero-image",
            },
        ),
    )

    # Assert
    assert state_out.unit_status == testing.BlockedStatus(
        "Failed to update Velero Deployment image. See juju debug-log for details."
    )
