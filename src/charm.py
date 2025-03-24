#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""The Velero Charm."""

import logging
from typing import Type, Union

import ops
from lightkube import ApiError, Client
from lightkube.resources.rbac_authorization_v1 import ClusterRole

from config import (
    USE_NODE_AGENT_CONFIG_KEY,
    VELERO_AWS_PLUGIN_CONFIG_KEY,
    VELERO_AZURE_PLUGIN_CONFIG_KEY,
    VELERO_IMAGE_CONFIG_KEY,
    VELERO_PATH,
)
from velero import Velero, VeleroError

logger = logging.getLogger(__name__)


class WithStatusError(Exception):
    """Base class of exceptions for when the raiser has an opinion on the charm status."""

    def __init__(
        self,
        msg: str,
        status_type: Type[
            Union[ops.ActiveStatus, ops.WaitingStatus, ops.BlockedStatus, ops.MaintenanceStatus]
        ],
    ):
        super().__init__(str(msg))
        self.msg = str(msg)
        self.status_type = status_type

    @property
    def status(self):
        """Return an instance of self.status_type, instantiated with this exception's message."""
        return self.status_type(self.msg)


class VeleroOperatorCharm(ops.CharmBase):
    """Charm the service."""

    _stored = ops.StoredState()

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self._stored.set_default(
            storage_provider_attached=None,
        )

        # Lightkube client needed for interacting with the Kubernetes cluster
        self.lightkube_client = None
        # Velero instance to manage the Velero server
        self.velero = None

        try:
            self._validate_config()
            self._is_trusted()
        except WithStatusError as e:
            self._log_and_set_status(e.status)
            return

        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.update_status, self._on_update_status)

    # PROPERTIES

    @property
    def velero(self):
        """The Velero instance."""
        if not self._velero:
            self._velero = Velero(
                VELERO_PATH, self.model.name, str(self.config[VELERO_IMAGE_CONFIG_KEY])
            )
        return self._velero

    @velero.setter
    def velero(self, value):
        self._velero = value

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

    # EVENT HANDLERS

    def _on_install(self, event: ops.InstallEvent) -> None:
        """Handle the install event."""
        self._log_and_set_status(ops.MaintenanceStatus("Deploying Velero server on the cluster"))

        try:
            self.velero.install(True if self.config[USE_NODE_AGENT_CONFIG_KEY] else False)
        except VeleroError as ve:
            raise RuntimeError("Failed to install Velero on the cluster") from ve

        self._on_update_status(event)

    def _on_update_status(self, event: ops.EventBase) -> None:
        """Handle the update-status event."""
        result = Velero.check_velero_deployment(self.lightkube_client, self.model.name)
        if not result.ok:
            self._log_and_set_status(
                ops.BlockedStatus(f"Deployment is not ready: {result.reason}")
            )
            return

        if self.config[USE_NODE_AGENT_CONFIG_KEY]:
            result = Velero.check_velero_nodeagent(self.lightkube_client, self.model.name)
            if not result.ok:
                self._log_and_set_status(
                    ops.BlockedStatus(f"NodeAgent is not ready: {result.reason}")
                )
                return

        if not self._stored.storage_provider_attached:
            self._log_and_set_status(ops.BlockedStatus("Missing relation: [s3|azure]"))
            return

        self._log_and_set_status(ops.ActiveStatus("Unit is Ready"))

    # HELPER METHODS

    def _log_and_set_status(self, status: ops.StatusBase) -> None:
        """Set the status of the charm and logs the status message.

        Args:
            status: The status to set
        """
        self.unit.status = status

        log_destination_map = {
            ops.ActiveStatus: logger.info,
            ops.BlockedStatus: logger.warning,
            ops.MaintenanceStatus: logger.info,
            ops.WaitingStatus: logger.info,
        }

        log_destination_map[type(status)](status.message)

    def _validate_config(self) -> None:
        """Check the charm configs and raise error if they are not correct.

        Raises:
            ErrorWithStatus: If any of the charm configs is not correct
        """
        for config_key in [
            VELERO_IMAGE_CONFIG_KEY,
            VELERO_AWS_PLUGIN_CONFIG_KEY,
            VELERO_AZURE_PLUGIN_CONFIG_KEY,
        ]:
            if not self.config[config_key]:
                raise WithStatusError(
                    f"The config '{config_key}' cannot be empty", ops.BlockedStatus
                )

    def _is_trusted(self) -> None:
        """Check if the app is trusted. Ie deployed with --trust flag.

        Raises:
            WithStatusError: If the app is not trusted
        """
        try:
            self.lightkube_client.list(ClusterRole)
        except ApiError as ae:
            if ae.status.code == 403:
                raise WithStatusError(
                    "The charm must be deployed with '--trust' flag enabled", ops.BlockedStatus
                )
            else:
                logger.error(f"Failed to check if the app is trusted: {ae}")
                raise WithStatusError(
                    "Failed to check if charm can access K8s API, check logs for details",
                    ops.BlockedStatus,
                )


if __name__ == "__main__":  # pragma: nocover
    ops.main(VeleroOperatorCharm)
