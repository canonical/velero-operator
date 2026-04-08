# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import ANY, MagicMock, PropertyMock, patch

import httpx
import pytest
from lightkube.core.exceptions import ApiError
from ops import testing

from charm import VeleroOperatorCharm
from constants import StorageRelation
from velero import (
    AzureStorageProvider,
    BackupInfo,
    GCSStorageProvider,
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
VELERO_AZURE_PLUGIN_CONFIG_KEY = "velero-azure-plugin-image"
VELERO_GCP_PLUGIN_CONFIG_KEY = "velero-gcp-plugin-image"
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
K8S_BACKUP_TARGET_ENDPOINT = "k8s-backup-target"


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
        VELERO_GCP_PLUGIN_CONFIG_KEY,
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
    "deployment_ok,nodeagent_ok,has_many_rels,has_rel,provider_ok,status,use_node_agent",
    [
        # Deployment not ready
        (
            False,
            True,
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
            True,
            testing.BlockedStatus("reason"),
            True,
        ),
        # Has many relations
        (
            True,
            True,
            True,
            True,
            True,
            testing.BlockedStatus(MANY_RELATIONS_ERROR_MESSAGE),
            True,
        ),
        # No relations
        (
            True,
            True,
            False,
            False,
            True,
            testing.BlockedStatus(MISSING_RELATION_MESSAGE),
            True,
        ),
        # Provider not ready
        (
            True,
            True,
            False,
            True,
            False,
            testing.BlockedStatus("reason"),
            True,
        ),
        # All good
        (True, True, False, True, True, testing.ActiveStatus(READY_MESSAGE), True),
        # All good
        (True, False, False, True, True, testing.ActiveStatus(READY_MESSAGE), False),
    ],
)
def test_on_update_status(
    check_velero_storage_locations,
    check_velero_deployment,
    check_velero_node_agent,
    mock_lightkube_client,
    deployment_ok,
    nodeagent_ok,
    has_many_rels,
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
            VeleroOperatorCharm, "has_many_storage_relations", new_callable=PropertyMock
        ) as mock_many_rels,
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_many_rels.return_value = has_many_rels
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
        [testing.Relation(endpoint=StorageRelation.S3.value)],
        [testing.Relation(endpoint=StorageRelation.AZURE.value)],
        [testing.Relation(endpoint=StorageRelation.GCS.value)],
        [
            testing.Relation(endpoint=StorageRelation.S3.value),
            testing.Relation(endpoint=StorageRelation.AZURE.value),
        ],
    ],
)
def test_storage_relation_properties(relations, mock_lightkube_client, mock_velero):
    """Test that the storage_relation properties return the correct value."""
    # Arrange
    ctx = testing.Context(VeleroOperatorCharm)

    # Act and Assert
    with ctx(ctx.on.start(), testing.State(relations=relations)) as manager:
        if len(relations) == 1:
            assert manager.charm.storage_relation == StorageRelation(relations[0].endpoint)
            assert not manager.charm.has_many_storage_relations
        else:
            assert manager.charm.storage_relation is None
            assert manager.charm.has_many_storage_relations


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
        (
            StorageRelation.AZURE,
            AzureStorageProvider,
            {
                "container": "test-container",
                "storage-account": "test-account",
                "secret-key": "test-key",
                "connection-protocol": "test-protocol",
                "resource-group": "test-group",
            },
        ),
        (
            StorageRelation.GCS,
            GCSStorageProvider,
            {
                "bucket": "test-gcs-bucket",
                "secret-key": '{"type": "service_account"}',
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
    relation = testing.Relation(endpoint=storage_relation.value, remote_app_data=relation_data)

    # Act
    state_out = ctx.run(
        ctx.on.relation_changed(relation),
        testing.State(relations=[relation]),
    )

    # Assert
    mock_velero.remove_storage_locations.assert_called_once()
    # is_storage_configured called twice: storage check + schedule reconciliation
    assert mock_velero.is_storage_configured.call_count == 2
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
        (
            StorageRelation.AZURE,
            {"test": "test"},
        ),
        (
            StorageRelation.GCS,
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
    relation = testing.Relation(endpoint=storage_relation.value, remote_app_data=relation_data)

    # Act
    state_out = ctx.run(
        ctx.on.relation_changed(relation),
        testing.State(relations=[relation]),
    )

    # Assert
    assert state_out.unit_status.name == testing.BlockedStatus.name
    assert INVALID_CONFIG_MESSAGE in state_out.unit_status.message


def test_storage_relation_changed_many_relations(mock_velero, mock_lightkube_client):
    """Test that the relation_changed acts correctly when there are many relations."""
    # Arrange
    mock_velero.is_storage_configured.return_value = False
    ctx = testing.Context(VeleroOperatorCharm)
    s3_relation = testing.Relation(endpoint=StorageRelation.S3.value)
    azure_relation = testing.Relation(endpoint=StorageRelation.AZURE.value)

    # Act
    state_out = ctx.run(
        ctx.on.relation_changed(s3_relation),
        testing.State(relations=[azure_relation, s3_relation]),
    )

    # Assert
    mock_velero.remove_storage_locations.assert_called_once()
    mock_velero.configure_storage_locations.assert_not_called()
    assert state_out.unit_status == testing.BlockedStatus(MANY_RELATIONS_ERROR_MESSAGE)


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
    relation = testing.Relation(
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
    relation = testing.Relation(
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
    relation = testing.Relation(endpoint=StorageRelation.S3.value)

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
    relation = testing.Relation(endpoint=StorageRelation.S3.value)
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
        (False, False, StorageRelation.AZURE),
        (True, False, StorageRelation.AZURE),
        (False, False, StorageRelation.GCS),
        (True, False, StorageRelation.GCS),
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
    relations = [testing.Relation(endpoint=relation.value)] if relation else []

    # Act
    ctx.run(
        ctx.on.config_changed(),
        testing.State(
            config={
                USE_NODE_AGENT_CONFIG_KEY: use_node_agent,
                DEFAULT_VOLUMES_TO_FS_BACKUP_CONFIG_KEY: default_volumes_to_fs_backup,
                VELERO_AWS_PLUGIN_CONFIG_KEY: "aws-image",
                VELERO_IMAGE_CONFIG_KEY: "velero-image",
                VELERO_AZURE_PLUGIN_CONFIG_KEY: "azure-image",
                VELERO_GCP_PLUGIN_CONFIG_KEY: "gcp-image",
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
        elif relation == StorageRelation.GCS:
            mock_velero.update_plugin_image.assert_called_once_with(
                mock_lightkube_client,
                "gcp-image",
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
    target = "test-app:test-endpoint"
    model = "test-model"
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        ctx = testing.Context(VeleroOperatorCharm)
        relation = testing.Relation(
            endpoint=VELERO_BACKUP_ENDPOINT,
            remote_app_name="test-app",
            remote_app_data={
                "app": "test-app",
                "model": "test-model",
                "relation_name": "test-endpoint",
                "spec": '{"include_namespaces": ["test-namespace"]}',
            },
        )

        # Act
        ctx.run(
            ctx.on.action("create-backup", params={"target": target, "model": model}),
            testing.State(relations=[relation]),
        )

        # Assert
        mock_velero.create_backup.assert_called_once()
        assert ctx.action_results.get("status") == "success"


@pytest.mark.parametrize(
    "target,model,relation,storage_configured,backup_side_effect,expected_exc",
    [
        # Storage not configured
        (
            "test-app:test-endpoint",
            "test-model",
            testing.Relation(
                endpoint=VELERO_BACKUP_ENDPOINT,
                remote_app_name="test-app",
                remote_app_data={
                    "app": "test-app",
                    "model": "test-model",
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
            "test-model",
            None,
            True,
            None,
            testing.ActionFailed,
        ),
        # No relation provided at all (valid target, but relation not present)
        (
            "test-app:test-endpoint",
            "test-model",
            None,
            True,
            None,
            testing.ActionFailed,
        ),
        # No backup spec with the provided model
        (
            "test-app:test-endpoint",
            "test-model",
            testing.Relation(
                endpoint=VELERO_BACKUP_ENDPOINT,
                remote_app_name="test-app",
                remote_app_data={
                    "app": "test-app",
                    "relation_name": "test-endpoint",
                    "model": "test-model-other",
                },
            ),
            True,
            None,
            testing.ActionFailed,
        ),
        # Backup fails (VeleroStatusError)
        (
            "test-app:test-endpoint",
            "test-model",
            testing.Relation(
                endpoint=VELERO_BACKUP_ENDPOINT,
                remote_app_name="test-app",
                remote_app_data={
                    "app": "test-app",
                    "relation_name": "test-endpoint",
                    "model": "test-model",
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
            "test-model",
            testing.Relation(
                endpoint=VELERO_BACKUP_ENDPOINT,
                remote_app_name="test-app",
                remote_app_data={
                    "app": "test-app",
                    "relation_name": "test-endpoint",
                    "model": "test-model",
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
    target,
    model,
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
                ctx.on.action("create-backup", params={"target": target, "model": model}),
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
        # Invalid parameters (empty backup UID)
        ("", True, None),
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
        mock_velero.list_backups.return_value = [
            BackupInfo(
                uid="backup1-uid",
                name="backup1",
                labels={
                    "app": "app1",
                    "endpoint": "endpoint1",
                    "model": "model1",
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
        mock_velero.list_backups.assert_called_once()
        assert ctx.action_results.get("status") == "success"
        assert ctx.action_results.get("backups") == {
            "backup1-uid": {
                "name": "backup1",
                "app": "app1",
                "endpoint": "endpoint1",
                "model": "model1",
                "phase": "Completed",
                "start-timestamp": "2023-01-01T00:00:00Z",
                "completion-timestamp": None,
            },
            "backup2-uid": {
                "name": "backup2",
                "app": "app2",
                "endpoint": "N/A",
                "model": "N/A",
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
        mock_velero.list_backups.side_effect = VeleroError("Failed to list backups")
        ctx = testing.Context(VeleroOperatorCharm)

        # Act and Assert
        with pytest.raises(testing.ActionFailed):
            ctx.run(ctx.on.action("list-backups"), testing.State())


def test_reconcile_schedules_creates_schedule(mock_velero, mock_lightkube_client):
    """Test that _reconcile_schedules creates a schedule when spec has schedule field."""
    # Arrange
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        mock_velero.is_installed.return_value = True
        ctx = testing.Context(VeleroOperatorCharm)
        relation = testing.Relation(
            endpoint=VELERO_BACKUP_ENDPOINT,
            remote_app_name="test-app",
            remote_app_data={
                "app": "test-app",
                "model": "test-model",
                "relation_name": "test-endpoint",
                "spec": '{"include_namespaces": ["test-namespace"], "schedule": "0 2 * * *"}',
            },
        )

        # Act
        state_out = ctx.run(
            ctx.on.relation_changed(relation),
            testing.State(relations=[relation]),
        )

        # Assert
        mock_velero.create_or_update_schedule.assert_called_once()
        call_args = mock_velero.create_or_update_schedule.call_args
        assert call_args[0][1] == "test-app-test-endpoint-"
        assert call_args[1]["labels"]["app"] == "test-app"
        assert call_args[1]["labels"]["endpoint"] == "test-endpoint"
        assert state_out.unit_status == testing.ActiveStatus(READY_MESSAGE)


def test_reconcile_schedules_deletes_schedule_when_no_schedule_in_spec(
    mock_velero, mock_lightkube_client
):
    """Test that _reconcile_schedules deletes schedule when spec has no schedule field."""
    # Arrange
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        mock_velero.is_installed.return_value = True
        ctx = testing.Context(VeleroOperatorCharm)
        relation = testing.Relation(
            endpoint=VELERO_BACKUP_ENDPOINT,
            remote_app_name="test-app",
            remote_app_data={
                "app": "test-app",
                "model": "test-model",
                "relation_name": "test-endpoint",
                "spec": '{"include_namespaces": ["test-namespace"]}',
            },
        )

        # Act
        ctx.run(
            ctx.on.relation_changed(relation),
            testing.State(relations=[relation]),
        )

        # Assert
        mock_velero.delete_schedule_by_labels.assert_called_once()
        call_args = mock_velero.delete_schedule_by_labels.call_args
        assert call_args[1]["labels"]["app"] == "test-app"
        assert call_args[1]["labels"]["endpoint"] == "test-endpoint"


def test_reconcile_schedules_handles_create_error(mock_velero, mock_lightkube_client, caplog):
    """Test that _reconcile_schedules handles VeleroError during schedule creation."""
    # Arrange
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        mock_velero.is_installed.return_value = True
        mock_velero.create_or_update_schedule.side_effect = VeleroError("Schedule creation failed")
        ctx = testing.Context(VeleroOperatorCharm)
        relation = testing.Relation(
            endpoint=VELERO_BACKUP_ENDPOINT,
            remote_app_name="test-app",
            remote_app_data={
                "app": "test-app",
                "model": "test-model",
                "relation_name": "test-endpoint",
                "spec": '{"include_namespaces": ["test-namespace"], "schedule": "0 2 * * *"}',
            },
        )

        # Act
        state_out = ctx.run(
            ctx.on.relation_changed(relation),
            testing.State(relations=[relation]),
        )

        # Assert
        assert state_out.unit_status == testing.ActiveStatus(READY_MESSAGE)
        assert "Failed to create/update schedule" in caplog.text


def test_reconcile_schedules_handles_delete_error(mock_velero, mock_lightkube_client, caplog):
    """Test that _reconcile_schedules handles VeleroError during schedule deletion."""
    # Arrange
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        mock_velero.is_installed.return_value = True
        mock_velero.delete_schedule_by_labels.side_effect = VeleroError("Schedule deletion failed")
        ctx = testing.Context(VeleroOperatorCharm)
        relation = testing.Relation(
            endpoint=VELERO_BACKUP_ENDPOINT,
            remote_app_name="test-app",
            remote_app_data={
                "app": "test-app",
                "model": "test-model",
                "relation_name": "test-endpoint",
                "spec": '{"include_namespaces": ["test-namespace"]}',
            },
        )

        # Act
        state_out = ctx.run(
            ctx.on.relation_changed(relation),
            testing.State(relations=[relation]),
        )

        # Assert
        assert state_out.unit_status == testing.ActiveStatus(READY_MESSAGE)
        assert "Failed to delete schedule" in caplog.text


def test_reconcile_schedules_skips_invalid_spec(mock_velero, mock_lightkube_client, caplog):
    """Test that _reconcile_schedules skips relations with invalid spec JSON."""
    # Arrange
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        mock_velero.is_installed.return_value = True
        ctx = testing.Context(VeleroOperatorCharm)
        relation = testing.Relation(
            endpoint=VELERO_BACKUP_ENDPOINT,
            remote_app_name="test-app",
            remote_app_data={
                "app": "test-app",
                "model": "test-model",
                "relation_name": "test-endpoint",
                "spec": "invalid-json{{{",
            },
        )

        # Act
        state_out = ctx.run(
            ctx.on.relation_changed(relation),
            testing.State(relations=[relation]),
        )

        # Assert
        mock_velero.create_or_update_schedule.assert_not_called()
        mock_velero.delete_schedule_by_labels.assert_not_called()
        assert "Failed to parse backup spec" in caplog.text
        assert state_out.unit_status == testing.ActiveStatus(READY_MESSAGE)


def test_reconcile_schedules_skips_missing_app_or_endpoint(mock_velero, mock_lightkube_client):
    """Test that _reconcile_schedules skips relations with missing app or endpoint."""
    # Arrange
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        mock_velero.is_installed.return_value = True
        ctx = testing.Context(VeleroOperatorCharm)
        relation = testing.Relation(
            endpoint=VELERO_BACKUP_ENDPOINT,
            remote_app_name="test-app",
            remote_app_data={
                "app": "test-app",
                "model": "test-model",
                "spec": '{"include_namespaces": ["test-namespace"], "schedule": "0 2 * * *"}',
            },
        )

        # Act
        ctx.run(
            ctx.on.relation_changed(relation),
            testing.State(relations=[relation]),
        )

        # Assert
        mock_velero.create_or_update_schedule.assert_not_called()
        mock_velero.delete_schedule_by_labels.assert_not_called()


def test_reconcile_schedules_skips_relation_without_app(mock_velero, mock_lightkube_client):
    """Test that _reconcile_schedules skips relations where relation.app is None."""
    # Arrange
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        mock_velero.is_installed.return_value = True
        ctx = testing.Context(VeleroOperatorCharm)

        # Use context manager to access charm instance
        with ctx(ctx.on.collect_unit_status(), testing.State()) as mgr:
            charm = mgr.charm

            # Reset mock calls from charm initialization
            mock_velero.reset_mock()

            # Create a mock relation with app=None
            mock_relation = MagicMock()
            mock_relation.app = None

            # Patch the model.relations.get to return our mock relation
            with patch.object(charm.model.relations, "get", return_value=[mock_relation]):
                # Directly call _reconcile_schedules
                charm._reconcile_schedules()

            # Assert - no schedule operations should have been called
            mock_velero.create_or_update_schedule.assert_not_called()
            mock_velero.delete_schedule_by_labels.assert_not_called()


def test_relation_broken_cleans_up_schedule(mock_velero, mock_lightkube_client):
    """Test that relation-broken event cleans up schedules."""
    # Arrange
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        mock_velero.is_installed.return_value = True
        ctx = testing.Context(VeleroOperatorCharm)
        relation = testing.Relation(
            endpoint=VELERO_BACKUP_ENDPOINT,
            remote_app_name="test-app",
            remote_app_data={
                "app": "test-app",
                "model": "test-model",
                "relation_name": "test-endpoint",
                "spec": '{"include_namespaces": ["test-namespace"], "schedule": "0 2 * * *"}',
            },
        )

        # Act
        ctx.run(
            ctx.on.relation_broken(relation),
            testing.State(relations=[relation]),
        )

        # Assert
        # Get the actual call to verify labels (model name is auto-generated by test framework)
        call_args = mock_velero.delete_schedule_by_labels.call_args
        assert call_args is not None, "delete_schedule_by_labels was not called"
        labels = call_args.kwargs["labels"]
        assert labels["app"] == "test-app"
        assert labels["endpoint"] == "test-endpoint"
        assert labels["managed-by"] == "velero-operator"
        assert "model" in labels  # Model name is present (but auto-generated)


def test_relation_broken_missing_app_name_or_endpoint(mock_velero, mock_lightkube_client, caplog):
    """Test that relation-broken event handles missing app_name or endpoint gracefully."""
    # Arrange
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        mock_velero.is_installed.return_value = True
        ctx = testing.Context(VeleroOperatorCharm)
        relation = testing.Relation(
            endpoint=VELERO_BACKUP_ENDPOINT,
            remote_app_name="test-app",
            remote_app_data={
                "spec": '{"include_namespaces": ["test-namespace"]}',
                # Missing "app" and "relation_name" fields
            },
        )

        # Act
        with caplog.at_level("DEBUG"):
            ctx.run(
                ctx.on.relation_broken(relation),
                testing.State(relations=[relation]),
            )

        # Assert - cleanup should not be called when app_name or endpoint is missing
        mock_velero.delete_schedule_by_labels.assert_not_called()
        assert "Skipping schedule cleanup" in caplog.text


def test_relation_broken_delete_fails(mock_velero, mock_lightkube_client):
    """Test that relation-broken event handles delete failures gracefully."""
    # Arrange
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        mock_velero.is_installed.return_value = True
        mock_velero.delete_schedule_by_labels.side_effect = VeleroError("Delete failed")
        ctx = testing.Context(VeleroOperatorCharm)
        relation = testing.Relation(
            endpoint=VELERO_BACKUP_ENDPOINT,
            remote_app_name="test-app",
            remote_app_data={
                "app": "test-app",
                "model": "test-model",
                "relation_name": "test-endpoint",
                "spec": '{"include_namespaces": ["test-namespace"], "schedule": "0 2 * * *"}',
            },
        )

        # Act - should not raise exception
        ctx.run(
            ctx.on.relation_broken(relation),
            testing.State(relations=[relation]),
        )

        # Assert - cleanup was attempted
        mock_velero.delete_schedule_by_labels.assert_called_once()


def test_run_list_backups_action_with_app_and_endpoint(
    mock_velero,
    mock_lightkube_client,
):
    """Test the run_list_backups_action handler with app and endpoint parameters."""
    # Arrange
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        mock_velero.list_backups.return_value = [
            BackupInfo(
                uid="backup1-uid",
                name="backup1",
                labels={
                    "app": "test-app",
                    "endpoint": "test-endpoint",
                    "model": "test-model",
                },
                annotations={},
                phase="Completed",
                start_timestamp="2023-01-01T00:00:00Z",
            ),
        ]
        ctx = testing.Context(VeleroOperatorCharm)

        # Act
        ctx.run(
            ctx.on.action("list-backups", params={"app": "test-app", "endpoint": "test-endpoint"}),
            testing.State(),
        )

        # Assert
        call_args = mock_velero.list_backups.call_args
        labels = call_args.kwargs["labels"]
        assert labels["app"] == "test-app"
        assert labels["endpoint"] == "test-endpoint"
        assert ctx.action_results.get("status") == "success"


# --- k8s-backup-target tests ---


K8S_BACKUP_TARGET_APP_DATA = {
    "backup_targets": '[{"app": "test-app", "relation_name": "test-endpoint",'
    ' "model": "test-model", "spec": {"include_namespaces": ["test-namespace"]}}]',
}


def test_create_backup_action_with_k8s_backup_target(
    mock_velero,
    mock_lightkube_client,
):
    """Test create-backup action works with k8s-backup-target relation."""
    target = "test-app:test-endpoint"
    model = "test-model"
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        ctx = testing.Context(VeleroOperatorCharm)
        relation = testing.Relation(
            endpoint=K8S_BACKUP_TARGET_ENDPOINT,
            remote_app_name="test-app",
            remote_app_data=K8S_BACKUP_TARGET_APP_DATA,
        )

        # Act
        ctx.run(
            ctx.on.action("create-backup", params={"target": target, "model": model}),
            testing.State(relations=[relation]),
        )

        # Assert
        mock_velero.create_backup.assert_called_once()
        assert ctx.action_results.get("status") == "success"


def test_create_backup_action_prefers_velero_backup_config(
    mock_velero,
    mock_lightkube_client,
):
    """Test create-backup prefers velero_backup_config over k8s_backup_target."""
    target = "test-app:test-endpoint"
    model = "test-model"
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        ctx = testing.Context(VeleroOperatorCharm)
        velero_relation = testing.Relation(
            endpoint=VELERO_BACKUP_ENDPOINT,
            remote_app_name="test-app",
            remote_app_data={
                "app": "test-app",
                "model": "test-model",
                "relation_name": "test-endpoint",
                "spec": '{"include_namespaces": ["velero-ns"], "schedule": "0 2 * * *"}',
            },
        )
        k8s_relation = testing.Relation(
            endpoint=K8S_BACKUP_TARGET_ENDPOINT,
            remote_app_name="test-app",
            remote_app_data=K8S_BACKUP_TARGET_APP_DATA,
        )

        # Act
        ctx.run(
            ctx.on.action("create-backup", params={"target": target, "model": model}),
            testing.State(relations=[velero_relation, k8s_relation]),
        )

        # Assert - should use velero_backup_config spec (with velero-ns, not test-namespace)
        mock_velero.create_backup.assert_called_once()
        call_args = mock_velero.create_backup.call_args
        spec = call_args[0][2]
        assert spec.include_namespaces == ["velero-ns"]


def test_create_backup_action_k8s_backup_target_no_spec(
    mock_velero,
    mock_lightkube_client,
):
    """Test create-backup fails when k8s-backup-target relation has no matching spec."""
    target = "test-app:test-endpoint"
    model = "wrong-model"
    with patch.object(
        VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
    ) as mock_storage_rel:
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        ctx = testing.Context(VeleroOperatorCharm)
        relation = testing.Relation(
            endpoint=K8S_BACKUP_TARGET_ENDPOINT,
            remote_app_name="test-app",
            remote_app_data=K8S_BACKUP_TARGET_APP_DATA,
        )

        with pytest.raises(testing.ActionFailed):
            ctx.run(
                ctx.on.action("create-backup", params={"target": target, "model": model}),
                testing.State(relations=[relation]),
            )


def test_find_backup_relation_skips_relation_without_app(
    mock_velero,
    mock_lightkube_client,
):
    """Test _find_backup_relation skips relations where relation.app is None."""
    target = "test-app:test-endpoint"
    model = "test-model"
    with patch.object(
        VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
    ) as mock_storage_rel:
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        ctx = testing.Context(VeleroOperatorCharm)
        # A relation with no remote_app_name simulates relation.app being None
        # during a relation-departed or similar transient state.
        # We need a relation that has app=None plus a valid one to find.
        relation_no_app = testing.Relation(
            endpoint=K8S_BACKUP_TARGET_ENDPOINT,
        )
        relation_with_app = testing.Relation(
            endpoint=K8S_BACKUP_TARGET_ENDPOINT,
            remote_app_name="test-app",
            remote_app_data=K8S_BACKUP_TARGET_APP_DATA,
        )

        ctx.run(
            ctx.on.action("create-backup", params={"target": target, "model": model}),
            testing.State(relations=[relation_no_app, relation_with_app]),
        )

        mock_velero.create_backup.assert_called_once()
        assert ctx.action_results.get("status") == "success"


def test_resolve_backup_spec_unknown_relation(
    mock_velero,
    mock_lightkube_client,
):
    """Test _resolve_backup_spec returns None for an unknown relation type."""
    ctx = testing.Context(VeleroOperatorCharm)
    state = testing.State()
    with ctx(ctx.on.start(), state) as mgr:
        mock_relation = MagicMock()
        mock_relation.name = "unknown-endpoint"
        result = mgr.charm._resolve_backup_spec(
            mock_relation, "test-app", "test-endpoint", "test-model"
        )
        assert result is None


def test_k8s_backup_target_relation_changed_triggers_reconcile(
    mock_velero,
    mock_lightkube_client,
):
    """Test that k8s-backup-target relation changed triggers reconcile."""
    with (
        patch.object(
            VeleroOperatorCharm, "storage_relation", new_callable=PropertyMock
        ) as mock_storage_rel,
    ):
        mock_storage_rel.return_value = StorageRelation.S3
        mock_velero.is_storage_configured.return_value = True
        mock_velero.is_installed.return_value = True
        ctx = testing.Context(VeleroOperatorCharm)
        relation = testing.Relation(
            endpoint=K8S_BACKUP_TARGET_ENDPOINT,
            remote_app_name="test-app",
            remote_app_data=K8S_BACKUP_TARGET_APP_DATA,
        )

        # Act
        state_out = ctx.run(
            ctx.on.relation_changed(relation),
            testing.State(relations=[relation]),
        )

        # Assert - charm should reach active status (reconcile ran successfully)
        assert state_out.unit_status == testing.ActiveStatus(READY_MESSAGE)
