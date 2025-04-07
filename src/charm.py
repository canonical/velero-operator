#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""The Velero Charm."""

import logging
from typing import Optional, Union

import ops
from charms.data_platform_libs.v0.azure_storage import AzureStorageRequires
from charms.data_platform_libs.v0.data_models import TypedCharmBase
from charms.data_platform_libs.v0.s3 import S3Requirer
from lightkube import ApiError, Client
from lightkube.resources.rbac_authorization_v1 import ClusterRole
from pydantic import ValidationError

from config import CharmConfig
from constants import VELERO_BINARY_PATH, StorageProviders
from velero import Velero, VeleroError

logger = logging.getLogger(__name__)


class CharmPermissionError(PermissionError):
    """Raised when the charm does not have permission to perform an action."""

    pass


class CharmConfigError(Exception):
    """Raised when charm config is invalid."""

    pass


class VeleroOperatorCharm(TypedCharmBase[CharmConfig]):
    """Charm the service."""

    config_type = CharmConfig
    _stored = ops.StoredState()

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self._stored.set_default(
            storage_provider=None,
        )

        # Lightkube client needed for interacting with the Kubernetes cluster
        self.lightkube_client = None
        # Velero class to interact with the Velero binary
        self.velero = None

        try:
            self._validate_config()
            self._check_is_trusted()
        except (CharmConfigError, CharmPermissionError) as ve:
            self._log_and_set_status(ops.BlockedStatus(str(ve)))
            return
        except ApiError:
            self._log_and_set_status(
                ops.BlockedStatus(
                    "Failed to check if charm can access K8s API, check logs for details"
                )
            )
            return

        self.s3_integrator = S3Requirer(self, StorageProviders.S3)
        self.azure_integrator = AzureStorageRequires(self, StorageProviders.AZURE)

        self.framework.observe(self.on.install, self._reconcile)
        self.framework.observe(self.on.update_status, self._reconcile)
        self.framework.observe(self.on.config_changed, self._reconcile)
        self.framework.observe(self.on.s3_relation_joined, self._on_storage_relation_joined)
        self.framework.observe(self.on.azure_relation_joined, self._on_storage_relation_joined)
        self.framework.observe(self.on.s3_relation_departed, self._on_storage_relation_departed)
        self.framework.observe(self.on.azure_relation_departed, self._on_storage_relation_departed)
        self.framework.observe(self.on.remove, self._on_remove)

    # PROPERTIES

    @property
    def lightkube_client(self):
        """The lightkube client to interact with the Kubernetes cluster."""
        if not self._lightkube_client:
            self._lightkube_client = Client(
                field_manager="velero-operator-lightkube", namespace=self.model.name
            )
        return self._lightkube_client

    @lightkube_client.setter
    def lightkube_client(self, value):
        self._lightkube_client = value

    @property
    def velero(self):
        """The Velero class to interact with the Velero binary."""
        if not self._velero:
            self._velero = Velero(VELERO_BINARY_PATH, self.model.name)
        return self._velero

    @velero.setter
    def velero(self, value):
        self._velero = value

    @property
    def _storage_relation_count(self) -> int:
        """Return the number of storage providers currently related."""
        providers = [p.value for p in StorageProviders]
        return sum(len(self.model.relations.get(provider, [])) > 0 for provider in providers)

    @property
    def _active_storage_relation(self) -> Optional[StorageProviders]:
        """Return an active related storage provided.

        If there are more than one storage provider related, return None
        """
        providers = [p.value for p in StorageProviders]
        if self._storage_relation_count == 1:
            for provider in providers:
                if len(self.model.relations.get(provider, [])) > 0:
                    return StorageProviders[provider]
        return None

    @property
    def storage_provider(self) -> Optional[StorageProviders]:
        """Return the stored storage provider."""
        return self._stored.storage_provider

    @storage_provider.setter
    def storage_provider(self, value: Optional[StorageProviders]) -> None:
        """Set the stored storage provider."""
        self._stored.storage_provider = value

    # EVENT HANDLERS

    def _reconcile(self, event: ops.EventBase) -> None:
        """Reconcile the charm state."""
        if not self.velero.is_installed(self.lightkube_client, self.config.use_node_agent):
            try:
                self._install()
            except VeleroError:
                self._log_and_set_status(
                    ops.BlockedStatus(
                        "Failed to install Velero on the cluster. See juju debug-log for details."
                    )
                )
                return

        is_storage_configured = self.velero.is_storage_configured(self.lightkube_client)
        if not self.storage_provider and is_storage_configured:
            try:
                self._remove_storage_locations()
                is_storage_configured = False
            except VeleroError:
                self._log_and_set_status(
                    ops.BlockedStatus(
                        "Failed to remove Velero storage. See juju debug-log for details."
                    )
                )
                return

        if self.storage_provider and not is_storage_configured:
            try:
                self._configure_storage_locations()
            except VeleroError:
                self._log_and_set_status(
                    ops.BlockedStatus(
                        "Failed to configure Velero storage. See juju debug-log for details."
                    )
                )
                return

        self._update_status()

    def _install(self) -> None:
        """Handle the install event.

        Raises:
            VeleroError: If the installation of Velero fails
        """
        self._log_and_set_status(ops.MaintenanceStatus("Deploying Velero on the cluster"))

        self.velero.install(
            self.config.velero_image,
            self.config.use_node_agent,
        )

    def _configure_storage_locations(self) -> None:
        """Handle the configure event.

        Raises:
            VeleroError: If the configuration of Velero fails
        """
        self._log_and_set_status(
            ops.MaintenanceStatus("Configuring Velero Storage Provider on the cluster")
        )

        self.velero.configure_storage_locations(self.lightkube_client)

    def _remove_storage_locations(self) -> None:
        """Handle the remove event.

        Raises:
            VeleroError: If the removal of Velero fails
        """
        self._log_and_set_status(
            ops.MaintenanceStatus("Removing Velero Storage Provider from the cluster")
        )

        self.velero.remove_storage_locations(self.lightkube_client)
        self.storage_provider = self._active_storage_relation

    def _update_status(self) -> None:
        """Handle the update-status event."""
        try:
            Velero.check_velero_deployment(self.lightkube_client, self.model.name)
        except (VeleroError, ApiError) as ve:
            self._log_and_set_status(ops.BlockedStatus(f"Velero Deployment is not ready: {ve}"))
            return

        if self.config.use_node_agent:
            try:
                Velero.check_velero_node_agent(self.lightkube_client, self.model.name)
            except (VeleroError, ApiError) as ve:
                self._log_and_set_status(ops.BlockedStatus(f"Velero NodeAgent is not ready: {ve}"))
                return

        if not self.storage_provider:
            self._log_and_set_status(ops.BlockedStatus("Missing relation: [s3|azure]"))
            return

        if self._storage_relation_count > 1:
            self._log_and_set_status(
                ops.BlockedStatus(
                    "Only one Storage Provider should be related at the time: [s3|azure]"
                )
            )
            return

        try:
            Velero.check_velero_storage_locations(self.lightkube_client, self.model.name)
        except (VeleroError, ApiError):
            self._log_and_set_status(
                ops.BlockedStatus("Velero Storage Locations are not ready: {ve}")
            )
            return

        self._log_and_set_status(ops.ActiveStatus("Unit is Ready"))

    def _on_remove(self, event: ops.RemoveEvent) -> None:
        """Handle the remove event."""
        self._log_and_set_status(ops.MaintenanceStatus("Removing Velero from the cluster"))

        self.velero.remove(self.lightkube_client)

    def _on_storage_relation_joined(self, event: ops.RelationJoinedEvent) -> None:
        """Handle the storage relation joined event."""
        if not self.storage_provider:
            self.storage_provider = StorageProviders[event.relation.name]

        self._reconcile(event)

    def _on_storage_relation_departed(self, event: ops.RelationBrokenEvent) -> None:
        """Handle the departure of a storage relation."""
        if self.storage_provider == StorageProviders[event.relation.name]:
            self.storage_provider = None

        self._reconcile(event)

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
