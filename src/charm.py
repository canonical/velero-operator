#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""The Velero Charm."""

import logging

import ops
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from lightkube import Client

from config import PROMETHEUS_METRICS_PORT, VELERO_PATH, StorageProviders
from velero import Velero, VeleroError

logger = logging.getLogger(__name__)


class VeleroOperatorCharm(ops.CharmBase):
    """Charm the service."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self._field_manager = "lightkube"

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
    def is_storage_provider_related(self) -> bool:
        """Return True if the charm is related to any storage provider."""
        providers = [p.value for p in StorageProviders]
        return any(len(self.model.relations.get(provider, [])) > 0 for provider in providers)
    
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

        velero_image = str(self.config["velero-image"])
        use_node_agent = bool(self.config["use-node-agent"])

        velero = Velero(VELERO_PATH, self.model.name, velero_image)

        try:
            velero.install(use_node_agent)
        except VeleroError as ve:
            raise RuntimeError("Failed to install Velero on the cluster") from ve
        
        self._on_update_status(event)

    def _on_remove(self, event: ops.RemoveEvent) -> None:
        """Handle the remove event."""
        pass

    def _on_update_status(self, event: ops.EventBase) -> None:
        """Handle the update-status event."""
        pass

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
