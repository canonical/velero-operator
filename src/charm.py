#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""The Velero Charm."""

import logging
from functools import cached_property
from typing import Optional, Union

import ops
from charms.data_platform_libs.v0.data_models import TypedCharmBase
from charms.data_platform_libs.v0.s3 import S3Requirer
from lightkube import ApiError, Client
from lightkube.resources.rbac_authorization_v1 import ClusterRole
from pydantic import ValidationError

from config import CharmConfig
from constants import VELERO_BINARY_PATH, StorageRelation
from velero import (
    S3StorageProvider,
    StorageProviderError,
    Velero,
    VeleroError,
)

logger = logging.getLogger(__name__)


class CharmError(Exception):
    """Base class for all charm errors."""

    pass


class CharmPermissionError(CharmError, PermissionError):
    """Raised when the charm does not have permission to perform an action."""

    pass


class CharmConfigError(CharmError):
    """Raised when charm config is invalid."""

    pass


class VeleroOperatorCharm(TypedCharmBase[CharmConfig]):
    """Charm the service."""

    config_type = CharmConfig

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)

        self.s3_integrator = S3Requirer(self, StorageRelation.S3.value)

        self.framework.observe(self.on.install, self._reconcile)
        self.framework.observe(self.on.update_status, self._reconcile)
        self.framework.observe(self.on.config_changed, self._reconcile)
        self.framework.observe(self.on.remove, self._on_remove)

        for relation in [r.value for r in StorageRelation]:
            self.framework.observe(self.on[relation].relation_changed, self._reconcile)
            self.framework.observe(self.on[relation].relation_broken, self._reconcile)

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
                self._install()

            if isinstance(event, ops.ConfigChangedEvent):
                self._log_and_set_status(ops.MaintenanceStatus("Updating Velero configuration"))
                self._on_config_changed()

            # FIXME: Avoid running on duplicate events
            # When the relation is created/joined, where will be two RelationChangedEvents
            # triggered, so the remove/configure logic is called twice.
            if isinstance(event, (ops.RelationBrokenEvent, ops.RelationChangedEvent)):
                self._log_and_set_status(ops.MaintenanceStatus("Removing Velero Storage Provider"))
                self._remove_storage_locations()

            if self.storage_relation and not self.velero.is_storage_configured(
                self.lightkube_client
            ):
                self._log_and_set_status(
                    ops.MaintenanceStatus("Configuring Velero Storage Provider")
                )
                self._configure_storage_locations()

            self._check_status()
            self._log_and_set_status(ops.ActiveStatus("Unit is Ready"))
        except CharmError as ce:
            self._log_and_set_status(ops.BlockedStatus(str(ce)))
        except ApiError:
            self._log_and_set_status(
                ops.BlockedStatus("Failed to access K8s API, check logs for details")
            )

    def _install(self) -> None:
        """Handle the install event.

        Raises:
            CharmError: If Velero installation fails
        """
        try:
            self.velero.install(
                self.config.velero_image,
                self.config.use_node_agent,
            )
        except VeleroError as ve:
            raise CharmError(
                "Failed to install Velero on the cluster. See juju debug-log for details."
            ) from ve

    def _configure_storage_locations(self) -> None:
        """Handle the configure event.

        Raises:
            CharmError: If Velero storage configuration fails
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
        except VeleroError as ve:
            raise CharmError(
                "Failed to configure Velero Storage Provider. See juju debug-log for details."
            ) from ve

    def _check_status(self) -> None:
        """Handle the update-status event.

        Raises:
            CharmError: If Velero status check fails
        """
        try:
            Velero.check_velero_deployment(self.lightkube_client, self.model.name)
        except (VeleroError, ApiError) as ve:
            raise CharmError(f"Velero Deployment is not ready: {ve}") from ve

        if self.config.use_node_agent:
            try:
                Velero.check_velero_node_agent(self.lightkube_client, self.model.name)
            except (VeleroError, ApiError) as ve:
                raise CharmError(f"Velero NodeAgent is not ready: {ve}") from ve

        relations = "|".join([r.value for r in StorageRelation])
        if not self.storage_relation:
            raise CharmError(f"Missing relation: [{relations}]")

        try:
            Velero.check_velero_storage_locations(self.lightkube_client, self.model.name)
        except (VeleroError, ApiError) as ve:
            raise CharmError(f"Velero Storage Provider is not ready: {ve}") from ve

    def _remove_storage_locations(self) -> None:
        """Handle the remove-storage-locations event.

        Raises:
            CharmError: If Velero storage configuration fails
        """
        try:
            self.velero.remove_storage_locations(self.lightkube_client)
        except VeleroError as ve:
            raise CharmError(
                "Failed to delete Velero Storage Provider. See juju debug-log for details."
            ) from ve

    def _on_remove(self, event: ops.RemoveEvent) -> None:
        """Handle the remove event."""
        self._log_and_set_status(ops.MaintenanceStatus("Removing Velero from the cluster"))
        self.velero.remove(self.lightkube_client)

    def _on_config_changed(self) -> None:
        """Handle the config-changed event.

        Raises:
            CharmError: If Velero configuration fails
        """
        try:
            if self.storage_relation == StorageRelation.S3:
                self.velero.update_plugin_image(
                    self.lightkube_client, self.config.velero_aws_plugin_image
                )

            if not self.config.use_node_agent:
                self.velero.remove_node_agent(self.lightkube_client)

            self.velero.update_velero_image(
                self.lightkube_client, self.config.velero_image, self.config.use_node_agent
            )
        except VeleroError as ve:
            raise CharmError(
                "Failed to update Velero configuration. See juju debug-log for details."
            ) from ve

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
