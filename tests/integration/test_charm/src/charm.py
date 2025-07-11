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


RELATION_NAME = "velero-backup-config"


class TestCharm(ops.CharmBase):
    """Test charm velero_backup_config lib."""

    def __init__(self, *args):
        super().__init__(*args)

        self._config = VeleroBackupRequirer(
            self,
            self.app.name,
            RELATION_NAME,
            spec=VeleroBackupSpec(
                include_namespaces=["user-namespace", "other-namespace"],
                include_resources=["deployments", "services"],
                label_selector={"app": "test"},
                ttl="24h5m5s",
            ),
        )

        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on[RELATION_NAME].relation_joined, self._on_relation_joined)
        self.framework.observe(self.on[RELATION_NAME].relation_broken, self._on_relation_broken)

    def _on_start(self, _) -> None:
        """Handle the start event."""
        self.unit.status = ops.WaitingStatus("Waiting for the relation")

    def _on_relation_joined(self, _: ops.RelationJoinedEvent):
        """Handle the relation joined event."""
        logger.info("velero-backup-config joined...")
        self.unit.status = ops.ActiveStatus()

    def _on_relation_broken(self, _: ops.RelationBrokenEvent):
        """Handle the relation broken event."""
        logger.info("velero-backup-config relation broken...")
        self.unit.status = ops.WaitingStatus("Waiting for the relation")


if __name__ == "__main__":
    ops.main(TestCharm)
