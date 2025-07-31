#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Test charm for velero_backup_config library."""

import logging

import ops
from charms.velero_libs.v0.velero_backup_config import (
    VeleroBackupProvider,
    VeleroBackupSpec,
)

logger = logging.getLogger(__name__)


FIRST_RELATION_NAME = "first-velero-backup-config"
SECOND_RELATION_NAME = "second-velero-backup-config"


class TestCharm(ops.CharmBase):
    """Test charm velero_backup_config lib."""

    def __init__(self, *args):
        super().__init__(*args)

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

        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(
            self.on[FIRST_RELATION_NAME].relation_joined, self._on_relation_joined
        )
        self.framework.observe(
            self.on[FIRST_RELATION_NAME].relation_broken, self._on_relation_broken
        )
        self.framework.observe(
            self.on[SECOND_RELATION_NAME].relation_joined, self._on_relation_joined
        )
        self.framework.observe(
            self.on[SECOND_RELATION_NAME].relation_broken, self._on_relation_broken
        )

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
