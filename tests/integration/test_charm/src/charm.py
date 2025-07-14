#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Test charm for velero_backup_config library."""

import logging

import ops
from charms.velero_operator.v0.velero_backup_config import (
    VeleroBackupRequirer,
    VeleroBackupSpec,
)

logger = logging.getLogger(__name__)


FIRST_RELATION_NAME = "first-velero-backup-config"
SECOND_RELATION_NAME = "second-velero-backup-config"


class TestCharm(ops.CharmBase):
    """Test charm velero_backup_config lib."""

    def __init__(self, *args):
        super().__init__(*args)

        self._first_config = VeleroBackupRequirer(
            self,
            self.app.name,
            FIRST_RELATION_NAME,
            spec=VeleroBackupSpec(
                include_namespaces=["user-namespace", "other-namespace"],
                include_resources=["deployments", "services"],
                label_selector={"app": "test"},
                ttl="24h5m5s",
            ),
        )

        self._second_config = VeleroBackupRequirer(
            self,
            self.app.name,
            SECOND_RELATION_NAME,
            spec=VeleroBackupSpec(
                exclude_namespaces=["excluded-namespace"],
                exclude_resources=["pods"],
                label_selector={"tier": "test"},
                ttl="12h30m",
                include_cluster_resources=True,
            ),
        )

        self.framework.observe(self.on.start, self._on_start)
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
