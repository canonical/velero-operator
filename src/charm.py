#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""The Velero Charm."""

import logging
from typing import Generic, Type, TypeVar, Union

import ops
from lightkube import ApiError, Client
from lightkube.resources.rbac_authorization_v1 import ClusterRole
from pydantic import BaseModel, ValidationError

from config import (
    USE_NODE_AGENT_CONFIG_KEY,
    VELERO_IMAGE_CONFIG_KEY,
    CharmConfig,
)
from constants import VELERO_BINARY_PATH
from velero import Velero, VeleroError

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class TypedCharmBase(ops.CharmBase, Generic[T]):
    """Class to be used for extending config-typed charms."""

    config_type: Type[T]

    @property
    def config(self) -> T:  # type: ignore
        """Return a config instance validated and parsed using the provided pydantic class."""
        translated_keys = {k.replace("-", "_"): v for k, v in self.model.config.items()}
        return self.config_type(**translated_keys)


class VeleroOperatorCharm(TypedCharmBase[CharmConfig]):
    """Charm the service."""

    config_type = CharmConfig
    _stored = ops.StoredState()

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)

        # Lightkube client needed for interacting with the Kubernetes cluster
        self.lightkube_client = None

        try:
            self._validate_config()
            self._is_trusted()
        except ValueError as ve:
            self._log_and_set_status(ops.BlockedStatus(str(ve)))
            return

        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.framework.observe(self.on.collect_unit_status, self._on_update_status)

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

    # EVENT HANDLERS

    def _on_install(self, event: ops.InstallEvent) -> None:
        """Handle the install event."""
        self._log_and_set_status(ops.MaintenanceStatus("Deploying Velero server on the cluster"))
        velero = Velero(VELERO_BINARY_PATH, self.model.name)

        try:
            velero.install(
                str(self.config[VELERO_IMAGE_CONFIG_KEY]),
                bool(self.config[USE_NODE_AGENT_CONFIG_KEY]),
            )
        except VeleroError as ve:
            raise RuntimeError(
                "Failed to install Velero on the cluster. See juju debug-log for details."
            ) from ve

    def _on_update_status(self, event: ops.EventBase) -> None:
        """Handle the update-status event."""
        result = Velero.check_velero_deployment(self.lightkube_client, self.model.name)
        if not result.ok:
            self._log_and_set_status(
                ops.BlockedStatus(f"Deployment is not ready: {result.reason}")
            )
            return

        if self.config[USE_NODE_AGENT_CONFIG_KEY]:
            result = Velero.check_velero_node_agent(self.lightkube_client, self.model.name)
            if not result.ok:
                self._log_and_set_status(
                    ops.BlockedStatus(f"NodeAgent is not ready: {result.reason}")
                )
                return

        self._log_and_set_status(ops.ActiveStatus("Unit is Ready"))

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
        else:
            raise ValueError(f"Unknown status type: {status}")

        self.unit.status = status

    def _validate_config(self) -> None:
        """Check the charm configs and raise error if they are not correct.

        Raises:
            ValueError: If any of the charm configs is not correct
        """
        try:
            _ = self.config
        except ValidationError as ve:
            fields = []
            for err in ve.errors():
                field = ".".join(str(p).replace("_", "-") for p in err["loc"])
                fields.append(field)
            error_details = ", ".join(fields)
            raise ValueError(f"Invalid configuration: {error_details}")

    def _is_trusted(self) -> None:
        """Check if the app is trusted. Ie deployed with --trust flag.

        Raises:
            VelueError: If the app is not trusted
        """
        try:
            self.lightkube_client.list(ClusterRole)
        except ApiError as ae:
            if ae.status.code == 403:
                raise ValueError(
                    "The charm must be deployed with '--trust' flag enabled, run 'juju trust ...'"
                )
            else:
                logger.error(f"Failed to check if the app is trusted: {ae}")
                raise ValueError(
                    "Failed to check if charm can access K8s API, check logs for details"
                )


if __name__ == "__main__":  # pragma: nocover
    ops.main(VeleroOperatorCharm)
