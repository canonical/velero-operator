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
from velero import (
    BackupInfo,
    S3StorageProvider,
    VeleroBackupStatusError,
    VeleroError,
    VeleroRestoreStatusError,
    VeleroStatusError,
)

VELERO_IMAGE_CONFIG_KEY = "velero-image"
USE_NODE_AGENT_CONFIG_KEY = "use-node-agent"
DEFAULT_VOLUMES_TO_FS_BACKUP_CONFIG_KEY = "default-volumes-to-fs-backup"
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
UPGRADE_MESSAGE = "Upgrading Velero"
VELERO_BACKUP_ENDPOINT = "velero-backups"


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
    "code,expected_status",
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
    "deployment_ok,nodeagent_ok,has_rel,provider_ok,status,use_node_agent",
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
        testing.State(
            config={
                VELERO_IMAGE_CONFIG_KEY: "image",
                USE_NODE_AGENT_CONFIG_KEY: False,
                DEFAULT_VOLUMES_TO_FS_BACKUP_CONFIG_KEY: True,
            }
        ),
    )

    # Assert
    if velero_installed:
        mock_velero.install.assert_not_called()
    else:
        mock_velero.install.assert_called_once_with(mock_lightkube_client, "image", False, True)
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


def test_run_cli_action_success(
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
def test_on_run_cli_action_failed(
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
    "use_node_agent,default_volumes_to_fs_backup,relation",
    [
        (False, False, StorageRelation.S3),
        (True, False, StorageRelation.S3),
        (False, True, None),
        (True, True, None),
    ],
)
def test_on_config_changed_success(
    use_node_agent,
    default_volumes_to_fs_backup,
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
                DEFAULT_VOLUMES_TO_FS_BACKUP_CONFIG_KEY: default_volumes_to_fs_backup,
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

    mock_velero.update_velero_deployment_flags.assert_called_once_with(
        mock_lightkube_client, default_volumes_to_fs_backup
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


def test_on_upgrade_charm_success(
    mock_lightkube_client,
    mock_velero,
):
    """Test that the upgrade_charm event is handled correctly."""
    # Arrange
    ctx = testing.Context(VeleroOperatorCharm)

    # Act
    state_out = ctx.run(ctx.on.upgrade_charm(), testing.State())

    # Assert
    assert state_out.unit_status == testing.MaintenanceStatus(UPGRADE_MESSAGE)
    mock_velero.upgrade.assert_called_once_with(mock_lightkube_client)


def test_run_create_backup_action_success(
    mock_velero,
    mock_lightkube_client,
):
    """Test the run_backup_action handler."""
    # Arrange
    command = "test-app:test-endpoint"
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        ctx = testing.Context(VeleroOperatorCharm)
        relation = Relation(
            endpoint=VELERO_BACKUP_ENDPOINT,
            remote_app_name="test-app",
            remote_app_data={
                "app": "test-app",
                "relation_name": "test-endpoint",
                "spec": '{"include_namespaces": ["test-namespace"]}',
            },
        )

        # Act
        ctx.run(
            ctx.on.action("create-backup", params={"target": command}),
            testing.State(relations=[relation]),
        )

        # Assert
        mock_velero.create_backup.assert_called_once()
        assert ctx.action_results.get("status") == "success"


@pytest.mark.parametrize(
    "command,relation,storage_configured,backup_side_effect,expected_exc",
    [
        # Storage not configured
        (
            "test-app:test-endpoint",
            Relation(
                endpoint=VELERO_BACKUP_ENDPOINT,
                remote_app_name="test-app",
                remote_app_data={
                    "app": "test-app",
                    "relation_name": "test-endpoint",
                    "spec": '{"include_namespaces": ["test-namespace"]}',
                },
            ),
            False,
            None,
            testing.ActionFailed,
        ),
        # Invalid target (no relation)
        (
            "invalid-target",
            None,
            True,
            None,
            testing.ActionFailed,
        ),
        # No relation provided at all (valid target, but relation not present)
        (
            "test-app:test-endpoint",
            None,
            True,
            None,
            testing.ActionFailed,
        ),
        # Backup fails (VeleroStatusError)
        (
            "test-app:test-endpoint",
            Relation(
                endpoint=VELERO_BACKUP_ENDPOINT,
                remote_app_name="test-app",
                remote_app_data={
                    "app": "test-app",
                    "relation_name": "test-endpoint",
                    "spec": '{"include_namespaces": ["test-namespace"]}',
                },
            ),
            True,
            VeleroBackupStatusError(name="test-backup-name", reason="Backup creation failed"),
            testing.ActionFailed,
        ),
        # Backup creation fails (VeleroError)
        (
            "test-app:test-endpoint",
            Relation(
                endpoint=VELERO_BACKUP_ENDPOINT,
                remote_app_name="test-app",
                remote_app_data={
                    "app": "test-app",
                    "relation_name": "test-endpoint",
                    "spec": '{"include_namespaces": ["test-namespace"]}',
                },
            ),
            True,
            VeleroError("Backup creation failed"),
            testing.ActionFailed,
        ),
    ],
)
def test_run_create_backup_action_failed(
    command,
    relation,
    storage_configured,
    backup_side_effect,
    expected_exc,
    mock_velero,
    mock_lightkube_client,
):
    """Test the run_backup_action handler for various failure cases."""
    with patch.object(
        VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
    ) as mock_storage_rel:
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = storage_configured
        mock_velero.create_backup.side_effect = backup_side_effect
        ctx = testing.Context(VeleroOperatorCharm)

        relations = [relation] if relation else []

        with pytest.raises(expected_exc):
            ctx.run(
                ctx.on.action("create-backup", params={"target": command}),
                testing.State(relations=relations),
            )


def test_run_restore_action_success(
    mock_velero,
    mock_lightkube_client,
):
    """Test the run_restore_action handler."""
    # Arrange
    backup_uid = "test-backup-uid"
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        mock_velero.create_restore.return_value = "test-restore"
        ctx = testing.Context(VeleroOperatorCharm)

        # Act
        ctx.run(
            ctx.on.action("restore", params={"backup-uid": backup_uid}),
            testing.State(),
        )

        # Assert
        mock_velero.create_restore.assert_called_once()
        assert ctx.action_results.get("status") == "success"


@pytest.mark.parametrize(
    "backup_uid,storage_configured,restore_side_effect",
    [
        # Storage not configured
        ("test-backup-uid", False, None),
        # Restore fails (VeleroError)
        (
            "test-backup-uid",
            True,
            VeleroError("Restore creation failed"),
        ),
        # Restore creation fails (VeleroRestoreStatusError)
        (
            "test-backup",
            True,
            VeleroRestoreStatusError(name="test-restore-name", reason="Restore creation failed"),
        ),
    ],
)
def test_run_restore_action_failed(
    backup_uid,
    storage_configured,
    restore_side_effect,
    mock_velero,
    mock_lightkube_client,
):
    """Test the run_restore_action handler for various failure cases."""
    with patch.object(
        VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
    ) as mock_storage_rel:
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = storage_configured
        mock_velero.create_restore.side_effect = restore_side_effect
        ctx = testing.Context(VeleroOperatorCharm)

        with pytest.raises(testing.ActionFailed):
            ctx.run(
                ctx.on.action("restore", params={"backup-uid": backup_uid}),
                testing.State(),
            )


def test_run_list_backups_action_success(
    mock_velero,
    mock_lightkube_client,
):
    """Test the run_list_backups_action handler."""
    # Arrange
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        mock_velero.get_backups.return_value = [
            BackupInfo(
                uid="backup1-uid",
                name="backup1",
                labels={
                    "app": "app1",
                    "endpoint": "endpoint1",
                },
                annotations={},
                phase="Completed",
                start_timestamp="2023-01-01T00:00:00Z",
            ),
            BackupInfo(
                uid="backup2-uid",
                name="backup2",
                labels={
                    "app": "app2",
                },
                annotations={},
                phase="InProgress",
                start_timestamp="2023-01-02T00:00:00Z",
            ),
        ]
        ctx = testing.Context(VeleroOperatorCharm)

        # Act
        ctx.run(ctx.on.action("list-backups"), testing.State())

        # Assert
        mock_velero.get_backups.assert_called_once()
        assert ctx.action_results.get("status") == "success"
        assert ctx.action_results.get("backups") == {
            "backup1-uid": {
                "name": "backup1",
                "app": "app1",
                "endpoint": "endpoint1",
                "phase": "Completed",
                "start-timestamp": "2023-01-01T00:00:00Z",
                "completion-timestamp": None,
            },
            "backup2-uid": {
                "name": "backup2",
                "app": "app2",
                "endpoint": "N/A",
                "phase": "InProgress",
                "start-timestamp": "2023-01-02T00:00:00Z",
                "completion-timestamp": None,
            },
        }


def test_run_list_backups_action_storage_not_configured(
    mock_velero,
    mock_lightkube_client,
):
    """Test the run_list_backups_action handler when storage is not configured."""
    # Arrange
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = False
        ctx = testing.Context(VeleroOperatorCharm)

        # Act and Assert
        with pytest.raises(testing.ActionFailed):
            ctx.run(ctx.on.action("list-backups"), testing.State())


def test_run_list_backups_action_invalid_params(
    mock_velero,
    mock_lightkube_client,
):
    """Test the run_list_backups_action handler with invalid parameters."""
    # Arrange
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        ctx = testing.Context(VeleroOperatorCharm)

        # Act and Assert
        with pytest.raises(testing.ActionFailed):
            ctx.run(
                ctx.on.action("list-backups", params={"endpoint": "endpoint"}), testing.State()
            )


def test_run_list_backups_action_failed(
    mock_velero,
    mock_lightkube_client,
):
    """Test the run_list_backups_action handler when an error occurs."""
    # Arrange
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        mock_velero.get_backups.side_effect = VeleroError("Failed to list backups")
        ctx = testing.Context(VeleroOperatorCharm)

        # Act and Assert
        with pytest.raises(testing.ActionFailed):
            ctx.run(ctx.on.action("list-backups"), testing.State())
