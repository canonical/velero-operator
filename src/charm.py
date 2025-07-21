#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""The Velero Charm."""

import logging
import shlex
import time
from functools import cached_property
from typing import List, Optional, Union

import ops
from charms.data_platform_libs.v0.data_models import TypedCharmBase
from charms.data_platform_libs.v0.s3 import S3Requirer
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.velero_libs.v0.velero_backup_config import VeleroBackupProvider
from lightkube import ApiError, Client
from lightkube.resources.rbac_authorization_v1 import ClusterRole
from pydantic import ValidationError

from config import CharmConfig
from constants import (
    VELERO_ALLOWED_SUBCOMMANDS,
    VELERO_BINARY_PATH,
    VELERO_METRICS_PATH,
    VELERO_METRICS_PORT,
    VELERO_METRICS_SERVICE_NAME,
    StorageRelation,
)
from velero import (
    BackupInfo,
    ExistingResourcePolicy,
    S3StorageProvider,
    StorageProviderError,
    Velero,
    VeleroBackupStatusError,
    VeleroError,
    VeleroRestoreStatusError,
    VeleroStatusError,
)

logger = logging.getLogger(__name__)


class CharmError(Exception):
    """Base class for all charm errors."""


class CharmPermissionError(CharmError, PermissionError):
    """Raised when the charm does not have permission to perform an action."""


class CharmConfigError(CharmError):
    """Raised when charm config is invalid."""


class VeleroOperatorCharm(TypedCharmBase[CharmConfig]):
    """Charm the service."""

    config_type = CharmConfig

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)

        self.s3_integrator = S3Requirer(self, StorageRelation.S3.value)

        self._scraping = MetricsEndpointProvider(
            self,
            relation_name="metrics-endpoint",
            jobs=[
                {
                    "metrics_path": VELERO_METRICS_PATH,
                    "static_configs": [
                        {
                            "targets": [
                                f"{VELERO_METRICS_SERVICE_NAME}.{self.model.name}.svc:{VELERO_METRICS_PORT}"
                            ]
                        }
                    ],
                }
            ],
        )

        self._backup_configs = VeleroBackupProvider(self, "velero-backups")

        self.framework.observe(self.on.install, self._reconcile)
        self.framework.observe(self.on.update_status, self._reconcile)
        self.framework.observe(self.on.config_changed, self._reconcile)
        self.framework.observe(self.on.upgrade_charm, self._on_upgrade)
        self.framework.observe(self.on.remove, self._on_remove)

        for relation in [r.value for r in StorageRelation]:
            self.framework.observe(self.on[relation].relation_changed, self._reconcile)
            self.framework.observe(self.on[relation].relation_broken, self._reconcile)

        self.framework.observe(self.on.run_cli_action, self._on_run_cli_action)
        self.framework.observe(self.on.create_backup_action, self._on_create_backup_action)
        self.framework.observe(self.on.list_backups_action, self.on_list_backups_action)
        self.framework.observe(self.on.restore_action, self.on_restore_action)

    # PROPERTIES

    @cached_property
    def lightkube_client(self):
        """The lightkube client to interact with the Kubernetes cluster."""
        return Client(field_manager="velero-operator-lightkube", namespace=self.model.name)

    @cached_property
    def velero(self):
        """The Velero class to interact with the Velero binary."""
        return Velero(VELERO_BINARY_PATH, self.model.name)

    @property
    def storage_relation(self) -> Optional[StorageRelation]:
        """Return an active related storage provided.

        If there are more than one storage provider related, return None
        """
        relations = [r.value for r in StorageRelation]
        for relation in relations:
            if bool(self.model.get_relation(relation)):
                return StorageRelation(relation)
        return None

    # EVENT HANDLERS

    def _reconcile(self, event: ops.EventBase) -> None:
        """Reconcile the charm state."""
        try:
            self._validate_config()
            self._check_is_trusted()

            if not self.velero.is_installed(self.lightkube_client, self.config.use_node_agent):
                self._log_and_set_status(ops.MaintenanceStatus("Deploying Velero on the cluster"))
                self.velero.install(
                    self.lightkube_client,
                    self.config.velero_image,
                    self.config.use_node_agent,
                    self.config.default_volumes_to_fs_backup,
                )

            if isinstance(event, ops.ConfigChangedEvent):
                self._log_and_set_status(ops.MaintenanceStatus("Updating Velero configuration"))
                self._update_config()

            # FIXME: Avoid running on duplicate events
            # When the relation is created/joined, where will be two RelationChangedEvents
            # triggered, so the remove/configure logic is called twice.
            if isinstance(event, (ops.RelationBrokenEvent, ops.RelationChangedEvent)):
                self._log_and_set_status(ops.MaintenanceStatus("Removing Velero Storage Provider"))
                self.velero.remove_storage_locations(self.lightkube_client)

            if self.storage_relation and not self.velero.is_storage_configured(
                self.lightkube_client
            ):
                self._log_and_set_status(
                    ops.MaintenanceStatus("Configuring Velero Storage Provider")
                )
                self._configure_storage_locations()

            self._check_status()
            self._log_and_set_status(ops.ActiveStatus("Unit is Ready"))
        except (CharmError, VeleroError) as e:
            message = (
                str(e)
                if isinstance(e, (CharmError, VeleroStatusError))
                else f"{str(e)}. See juju debug-log for details."
            )
            self._log_and_set_status(ops.BlockedStatus(message))
        except ApiError:
            self._log_and_set_status(
                ops.BlockedStatus("Failed to access K8s API. See juju debug-log for details.")
            )

    def _on_run_cli_action(self, event: ops.ActionEvent) -> None:
        """Handle the run-cli action event."""
        command = event.params["command"]

        if not self.storage_relation or not self.velero.is_storage_configured(
            self.lightkube_client
        ):
            event.fail("Velero Storage Provider is not configured")
            return

        if not command.strip():
            event.fail("Command should not be empty")
            return

        try:
            args = shlex.split(command)
            if args[0] not in VELERO_ALLOWED_SUBCOMMANDS:
                event.fail(f"Invalid command: '{args[0]}', allowed: {VELERO_ALLOWED_SUBCOMMANDS}")
                return

            result = self.velero.run_cli_command(args)
            event.log(f"Command output:\n{result}")
            event.set_results({"status": "success"})
        except VeleroError as ve:
            event.fail(f"Failed to run command: {ve}")
            return
        except ValueError as ve:
            event.fail(f"Invalid command: {ve}")
            return

    def _on_create_backup_action(self, event: ops.ActionEvent) -> None:
        """Handle the create-backup action event."""
        target = event.params["target"]
        check_message = (
            "You may check for more information using "
            "`run-cli command='backup describe {backup_name}'` "
            "and `run-cli command='backup logs {backup_name}'`"
        )

        if not self.storage_relation or not self.velero.is_storage_configured(
            self.lightkube_client
        ):
            event.fail("Velero Storage Provider is not configured")
            return

        try:
            app, endpoint = target.split(":", 1)
        except ValueError:
            event.fail("Invalid target format. Use 'app:endpoint'")
            return

        backup_spec = self._backup_configs.get_backup_spec(app, endpoint)
        if not backup_spec:
            event.fail(f"No backup spec found for target '{target}'")
            return

        event.log("Creating a backup...")
        backup_name_prefix = f"{app}-{endpoint}"
        try:
            backup_name = self.velero.create_backup(
                self.lightkube_client,
                backup_name_prefix,
                backup_spec,
                self.config.default_volumes_to_fs_backup,
                labels={
                    "app": app,
                    "endpoint": endpoint,
                    "app.kubernetes.io/managed-by": "velero-operator",
                },
                annotations={
                    "created-at": str(round(time.time())),
                },
            )
            event.log(f"Backup '{backup_name}' created successfully.")
            event.log(check_message.format(backup_name=backup_name))
            event.set_results({"status": "success", "backup-name": backup_name})
        except VeleroBackupStatusError as ve:
            event.log(check_message.format(backup_name=ve.name))
            event.fail(f"Velero Backup failed: {ve.reason}")
        except (VeleroError, ApiError) as e:
            event.fail("%s" % e)
            return

    def on_list_backups_action(self, event: ops.ActionEvent) -> None:
        """Handle the list-backups action event."""
        app = event.params.get("app", None)
        endpoint = event.params.get("endpoint", None)

        if not self.storage_relation or not self.velero.is_storage_configured(
            self.lightkube_client
        ):
            event.fail("Velero Storage Provider is not configured")
            return

        if app is None and endpoint is not None:
            event.fail("If 'endpoint' is provided, 'app' must also be provided")
            return

        event.log("Listing backups...")
        try:
            backups = self.velero.get_backups(
                self.lightkube_client, labels={"app": app, "endpoint": endpoint}
            )
            event.set_results(
                {
                    "status": "success",
                    "backups": self._backup_list_to_dict(backups),
                }
            )
        except VeleroError as e:
            event.fail("%s" % e)
            return

    def on_restore_action(self, event: ops.ActionEvent) -> None:
        """Handle the restore action event."""
        backup_uid = event.params["backup-uid"]
        existing_resource_policy = ExistingResourcePolicy(
            event.params.get("existing-resource-policy", "none")
        )
        check_message = (
            "You may check for more information using "
            "`run-cli command='restore describe {restore_name}'` "
            "and `run-cli command='restore logs {restore_name}'`"
        )

        if not self.storage_relation or not self.velero.is_storage_configured(
            self.lightkube_client
        ):
            event.fail("Velero Storage Provider is not configured")
            return

        event.log("Creating a restore...")
        try:
            restore_name = self.velero.create_restore(
                self.lightkube_client,
                backup_uid,
                existing_resource_policy,
                labels={
                    "app.kubernetes.io/managed-by": "velero-operator",
                },
                annotations={
                    "created-at": str(round(time.time())),
                },
            )
            event.log(check_message.format(restore_name=restore_name))
            event.set_results({"status": "success", "restore-name": restore_name})
        except VeleroRestoreStatusError as ve:
            event.log(check_message.format(restore_name=ve.name))
            event.fail(f"Velero Restore failed: {ve.reason}")
        except (VeleroError, ApiError) as e:
            event.fail("%s" % e)
            return

    def _configure_storage_locations(self) -> None:
        """Configure the Velero storage locations.

        Raises:
            CharmError: If Velero storage configuration fails
            VeleroError: If Velero configuration fails
        """
        try:
            if self.storage_relation == StorageRelation.S3:
                provider = S3StorageProvider(
                    self.config.velero_aws_plugin_image,
                    self.s3_integrator.get_s3_connection_info(),
                )
            else:  # pragma: no cover
                raise ValueError("Unsupported storage provider or no provider configured.")

            self.velero.configure_storage_locations(self.lightkube_client, provider)
        except StorageProviderError as ve:
            raise CharmError(f"Invalid configuration: {str(ve)}") from ve

    def _check_status(self) -> None:
        """Check the status of Velero and its components.

        Raises:
            CharmError: If Velero status check fails
            VeleroStatusError: If Velero status check fails
            ApiError: If the charm cannot access the K8s API
        """
        Velero.check_velero_deployment(self.lightkube_client, self.model.name)

        if self.config.use_node_agent:
            Velero.check_velero_node_agent(self.lightkube_client, self.model.name)

        relations = "|".join([r.value for r in StorageRelation])
        if not self.storage_relation:
            raise CharmError(f"Missing relation: [{relations}]")

        Velero.check_velero_storage_locations(self.lightkube_client, self.model.name)

    def _on_remove(self, event: ops.RemoveEvent) -> None:
        """Handle the remove event."""
        self._log_and_set_status(ops.MaintenanceStatus("Removing Velero from the cluster"))
        self.velero.remove(self.lightkube_client)

    def _update_config(self) -> None:
        """Handle the config-changed event.

        Raises:
            VeleroError: If Velero configuration fails
        """
        if self.storage_relation == StorageRelation.S3:
            self.velero.update_plugin_image(
                self.lightkube_client, self.config.velero_aws_plugin_image
            )

        if not self.config.use_node_agent:
            self.velero.remove_node_agent(self.lightkube_client)

        self.velero.update_velero_deployment_image(self.lightkube_client, self.config.velero_image)
        if self.config.use_node_agent:
            self.velero.update_velero_node_agent_image(
                self.lightkube_client, self.config.velero_image
            )

        self.velero.update_velero_deployment_flags(
            self.lightkube_client, self.config.default_volumes_to_fs_backup
        )

    def _on_upgrade(self, event: ops.UpgradeCharmEvent) -> None:
        """Handle the upgrade-charm event."""
        self._log_and_set_status(ops.MaintenanceStatus("Upgrading Velero"))
        self.velero.upgrade(self.lightkube_client)

    # HELPER METHODS

    def _log_and_set_status(
        self,
        status: Union[
            ops.ActiveStatus, ops.MaintenanceStatus, ops.WaitingStatus, ops.BlockedStatus
        ],
    ) -> None:
        """Set the status of the charm and logs the status message.

        Args:
            status: The status to set
        """
        if isinstance(status, ops.ActiveStatus):
            logger.info(status.message)
        elif isinstance(status, ops.MaintenanceStatus):
            logger.info(status.message)
        elif isinstance(status, ops.WaitingStatus):
            logger.info(status.message)
        elif isinstance(status, ops.BlockedStatus):
            logger.warning(status.message)
        else:  # pragma: no cover
            raise ValueError(f"Unknown status type: {status}")

        self.unit.status = status

    def _backup_list_to_dict(self, backups: List[BackupInfo]) -> dict:
        """Convert a list of BackupInfo objects to a dictionary, printable for action results."""
        result = {}
        for b in backups:
            result[b.uid] = {
                "name": b.name,
                "app": b.labels.get("app", "N/A"),
                "endpoint": b.labels.get("endpoint", "N/A"),
                "phase": b.phase,
                "start-timestamp": b.start_timestamp,
                "completion-timestamp": b.completion_timestamp,
            }
        return result

    def _validate_config(self) -> None:
        """Check the charm configs and raise error if they are not correct.

        Raises:
            CharmConfigError: If any of the charm configs is not correct
        """
        try:
            _ = self.config
        except ValidationError as ve:
            fields = []
            for err in ve.errors():
                field = ".".join(str(p).replace("_", "-") for p in err["loc"])
                fields.append(field)
            error_details = ", ".join(fields)
            raise CharmConfigError(f"Invalid configuration: {error_details}")

    def _check_is_trusted(self) -> None:
        """Check if the app is trusted. Ie deployed with --trust flag.

        Raises:
            CharmPermissionError: If the app is not trusted
            ApiError: If the charm cannot access the K8s API
        """
        try:
            list(self.lightkube_client.list(ClusterRole))
        except ApiError as ae:
            if ae.status.code == 403:
                raise CharmPermissionError(
                    "The charm must be deployed with '--trust' flag enabled, run 'juju trust ...'"
                )
            logger.error(f"Failed to check if the app is trusted: {ae}")
            raise ae


if __name__ == "__main__":  # pragma: nocover
    ops.main(VeleroOperatorCharm)
