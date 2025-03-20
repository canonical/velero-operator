#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""The Velero Charm."""

import logging

import ops
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from lightkube import Client

from config import PROMETHEUS_METRICS_PORT, VELERO_PATH
from utils import check_velero_deployment, check_velero_nodeagent
from velero import Velero, VeleroError

logger = logging.getLogger(__name__)


class VeleroOperatorCharm(ops.CharmBase):
    """Charm the service."""

    _stored = ops.StoredState()

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self._field_manager = "velero-operator"
        self._velero = Velero(VELERO_PATH, self.model.name, str(self.config["velero-image"]))
        self._stored.set_default(
            storage_provider_attached=None,
        )

        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.remove, self._on_remove)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)

        self.framework.observe(self.on.s3_relation_joined, self._on_storage_relation_joined)
        self.framework.observe(self.on.s3_relation_changed, self._on_storage_relation_changed)
        self.framework.observe(self.on.s3_relation_departed, self._on_storage_relation_departed)

        self.framework.observe(self.on.azure_relation_joined, self._on_storage_relation_joined)
        self.framework.observe(self.on.azure_relation_changed, self._on_storage_relation_changed)
        self.framework.observe(self.on.azure_relation_departed, self._on_storage_relation_departed)

        self.prometheus_provider = MetricsEndpointProvider(
            charm=self,
            relation_name="metrics-endpoint",
            jobs=[
                {
                    "metrics_path": "/metrics",
                    "static_configs": [
                        {"targets": [f"velero-server.velero.svc:{PROMETHEUS_METRICS_PORT}"]}
                    ],
                }
            ],
        )

    # PROPERTIES

    @property
    def _lightkube_client(self):
        """Returns a lightkube client configured for this charm."""
        return Client(namespace=self.model.name, field_manager=self._field_manager)

    # EVENT HANDLERS

    def _on_config_changed(self, event: ops.ConfigChangedEvent) -> None:
        """Handle changed configuration."""
        pass

    def _on_install(self, event: ops.InstallEvent) -> None:
        """Handle the install event."""
        self._log_and_set_status(ops.MaintenanceStatus("Deploying Velero server on the cluster"))

        try:
            self._velero.install(True if self.config["use-node-agent"] else False)
        except VeleroError as ve:
            raise RuntimeError("Failed to install Velero on the cluster") from ve

        self._on_update_status(event)

    def _on_remove(self, event: ops.RemoveEvent) -> None:
        """Handle the remove event."""
        self._log_and_set_status(ops.MaintenanceStatus("Removing Velero server from the cluster"))

        self._velero.remove(self._lightkube_client)

    def _on_update_status(self, event: ops.EventBase) -> None:
        """Handle the update-status event."""
        result = check_velero_deployment(self._lightkube_client, self.model.name)
        if not result.ok:
            self._log_and_set_status(
                ops.BlockedStatus(f"Deployment is not ready: {result.reason}")
            )
            return

        if self.config["use-node-agent"]:
            result = check_velero_nodeagent(self._lightkube_client, self.model.name)
            if not result.ok:
                self._log_and_set_status(
                    ops.BlockedStatus(f"NodeAgent is not ready: {result.reason}")
                )
                return

        if not self._stored.storage_provider_attached:
            self._log_and_set_status(ops.BlockedStatus("Missing relation: [s3|azure]"))
            return

        self._log_and_set_status(ops.ActiveStatus("Unit is Ready"))

    def _on_upgrade_charm(self, event: ops.UpgradeCharmEvent) -> None:
        """Handle the upgrade-charm event."""
        pass

    def _on_storage_relation_joined(self, event: ops.RelationJoinedEvent) -> None:
        """Handle the s3-relation-joined event."""
        pass

    def _on_storage_relation_changed(self, event: ops.RelationChangedEvent) -> None:
        """Handle the s3-relation-changed event."""
        pass

    def _on_storage_relation_departed(self, event: ops.RelationDepartedEvent) -> None:
        """Handle the s3-relation-departed event."""
        pass

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


if __name__ == "__main__":  # pragma: nocover
    ops.main(VeleroOperatorCharm)
