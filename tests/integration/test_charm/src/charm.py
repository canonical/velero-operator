#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Test charm for velero_backup_config and k8s_backup_target libraries."""

import logging

import ops
from charmlibs.interfaces.k8s_backup_target import (
    K8sBackupTargetProvider,
    K8sBackupTargetSpec,
)
from charms.velero_libs.v0.velero_backup_config import (
    VeleroBackupProvider,
    VeleroBackupSpec,
)

logger = logging.getLogger(__name__)


FIRST_RELATION_NAME = "first-velero-backup-config"
SECOND_RELATION_NAME = "second-velero-backup-config"
K8S_BACKUP_ENDPOINT = "k8s-backup-endpoint"


class TestCharm(ops.CharmBase):
    """Test charm velero_backup_config and k8s_backup_target libs."""

    def __init__(self, *args):
        super().__init__(*args)

        # Get schedule config, use None if empty string
        schedule_config = str(self.config.get("schedule", "")) or None
        paused_config = bool(self.config.get("paused", False)) if schedule_config else None

        self._first_config = VeleroBackupProvider(
            self,
            FIRST_RELATION_NAME,
            spec=VeleroBackupSpec(
                include_namespaces=["velero-integration-tests"],
                include_resources=[
                    "deployments",
                    "persistentvolumeclaims",
                    "pods",
                    "persistentvolumes",
                ],
                label_selector={"app": "dummy"},
                ttl=str(self.config["ttl"]),
                include_cluster_resources=None,
                schedule=schedule_config,
                paused=paused_config,
                skip_immediately=True if schedule_config else None,
            ),
            refresh_event=[self.on.config_changed],
        )

        self._second_config = VeleroBackupProvider(
            self,
            SECOND_RELATION_NAME,
            spec=VeleroBackupSpec(
                include_namespaces=["velero-integration-tests"],
                exclude_resources=[
                    "deployments",
                    "persistentvolumeclaims",
                    "pods",
                    "persistentvolumes",
                ],
                ttl="12h30m",
                include_cluster_resources=False,
            ),
        )

        self._k8s_backup_config = K8sBackupTargetProvider(
            self,
            K8S_BACKUP_ENDPOINT,
            spec=K8sBackupTargetSpec(
                include_namespaces=["velero-integration-tests"],
                include_resources=[
                    "deployments",
                    "persistentvolumeclaims",
                    "pods",
                    "persistentvolumes",
                    "services",
                ],
                label_selector={"app": "dummy"},
                ttl=str(self.config["ttl"]),
            ),
            refresh_event=[self.on.config_changed],
        )

        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        for endpoint in (FIRST_RELATION_NAME, SECOND_RELATION_NAME, K8S_BACKUP_ENDPOINT):
            self.framework.observe(self.on[endpoint].relation_joined, self._on_relation_joined)
            self.framework.observe(self.on[endpoint].relation_broken, self._on_relation_broken)

    def _on_config_changed(self, event: ops.ConfigChangedEvent):
        """Handle the config changed event."""
        logger.info("Config changed: %s", self.config)

    def _on_start(self, _) -> None:
        """Handle the start event."""
        self.unit.status = ops.WaitingStatus("Waiting for the relation")

    def _on_relation_joined(self, event: ops.RelationJoinedEvent):
        """Handle the relation joined event."""
        logger.info("%s joined...", event.relation.name)
        self.unit.status = ops.ActiveStatus()

    def _on_relation_broken(self, event: ops.RelationBrokenEvent):
        """Handle the relation broken event."""
        logger.info("%s relation broken...", event.relation.name)
        self.unit.status = ops.WaitingStatus("Waiting for the relation")


if __name__ == "__main__":
    ops.main(TestCharm)
