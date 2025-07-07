"""# VeleroBackupConfig library.

This library implements the Requirer and Provider roles for the `velero_backup_config` relation
interface. It is used by client charms to declare backup specifications, and by the Velero Operator
charm to consume them and execute backup and restore operations.

The `velero_backup_config` interface allows a charm (the requirer) to provide a declarative
description of what Kubernetes resources should be included in a backup. These specifications are
sent to the Velero Operator charm (the provider), which executes the backup using the Velero CLI
and Kubernetes CRDs.

This interface follows a least-privilege model: client charms do not manipulate cluster resources
themselves. Instead, they define what should be backed up
and leave execution to the Velero Operator.

See Also:
- Interface spec: https://github.com/canonical/charm-relation-interfaces/tree/main/interfaces/velero_backup_config/v0
- Velero Operator charm: https://charmhub.io/velero-operator

## Getting Started

To get started using the library, fetch the library with `charmcraft`.

```shell
cd some-charm
charmcraft fetch-lib charms.velero_operator.v0.velero_backup_config
```

Then in your charm, do:

```python
from charms.velero_operatpr.v0.velero_backup_config import (
    VeleroBackupRequirer,
    VeleroBackupSpec,
)

class SomeCharm(CharmBase):
  def __init__(self, *args):
    # ...
    self.user_workload_backup = VeleroBackupRequirer(
        self,
        app="kubeflow",
        endpoint="user-workloads-backup",
        spec=VeleroBackupSpec(
            include_namespaces=["user-namespace],
            include_resources=["persistentvolumeclaims", "services", "deployments"],
        )
    )
    # ...
```
"""

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

from ops import EventBase
from ops.charm import CharmBase
from ops.framework import Object

# The unique Charmhub library identifier, never change it
LIBID = "3fcd828c77024b0f9a7ea3544805456b"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

# Regex to check if the provided TTL is a correct duration
DURATION_REGEX = r"^(?=.*\d)(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$"

SPEC_FIELD_NAME = "spec"
APP_FIELD_NAME = "app"
RELATION_FIELD_NAME = "relation_name"

logger = logging.getLogger(__name__)


@dataclass
class VeleroBackupSpec:
    """Dataclass representing the Velero backup configuration.

    Args:
        include_namespaces (Optional[List[str]]): Namespaces to include in the backup.
        include_resources (Optional[List[str]]): Resources to include in the backup.
        exclude_namespaces (Optional[List[str]]): Namespaces to exclude from the backup.
        exclude_resources (Optional[List[str]]): Resources to exclude from the backup.
        label_selector (Optional[Dict[str, str]]): Label selector for filtering resources.
        include_cluster_resources (bool): Whether to include cluster-wide resources in the backup.
        ttl (Optional[str]): TTL for the backup, if applicable. Example: "24h", "10m10s", etc.
    """

    include_namespaces: Optional[List[str]] = None
    include_resources: Optional[List[str]] = None
    exclude_namespaces: Optional[List[str]] = None
    exclude_resources: Optional[List[str]] = None
    label_selector: Optional[Dict[str, str]] = None
    ttl: Optional[str] = None
    include_cluster_resources: bool = False

    def __post_init__(self):
        """Validate the specification."""
        if self.ttl and not re.match(DURATION_REGEX, self.ttl):
            raise ValueError(
                f"Invalid TTL format: {self.ttl}. Expected format: '24h', '10h10m10s', etc."
            )


class VeleroBackupProvider(Object):
    """Provider class for the Velero backup configuration relation."""

    def __init__(self, charm: CharmBase, relation_name: str):
        """Initialize the provider and binds to relation events.

        Args:
            charm (CharmBase): The charm instance that provides backup configuration.
            relation_name (str): The name of the relation. (from metadata.yaml)
        """
        pass


class VeleroBackupRequirer(Object):
    """Requirer class for the Velero backup configuration relation."""

    def __init__(
        self, charm: CharmBase, app_name: str, relation_name: str, spec: VeleroBackupSpec
    ):
        """Intialize the requirer with the specified backup configuration.

        Args:
            charm (CharmBase): The charm instance that requires backup.
            app_name (str): The name of the application for which the backup is configured
            relation_name (str): The name of the relation. (from metadata.yaml)
            spec (VeleroBackupSpec): The backup specification to be used
        """
        super().__init__(charm, relation_name)
        self._charm = charm
        self._app_name = app_name
        self._relation_name = relation_name
        self._spec = spec

        self.framework.observe(self._charm.on.leader_elected, self._send_data)
        self.framework.observe(
            self._charm.on[self._relation_name].relation_created, self._send_data
        )
        self.framework.observe(self._charm.on.upgrade_charm, self._send_data)

    def _send_data(self, event: EventBase):
        """Handle any event where we should send data to the relation."""
        if not self._charm.model.unit.is_leader():
            logger.warning(
                "VeleroBackupRequirer handled send_data event when it is not a leader. "
                "Skiping event - no data sent"
            )
            return

        relations = self._charm.model.relations.get(self._relation_name)

        if not relations:
            logger.warning(
                "VeleroBackupRequirer handled send_data event but no relation with name '%s' found"
                "Skiping event - no data sent",
                self._relation_name,
            )
            return

        for relation in relations:
            relation_data = relation.data[self._charm.app]
            relation_data.update(
                {
                    APP_FIELD_NAME: self._app_name,
                    RELATION_FIELD_NAME: self._relation_name,
                    SPEC_FIELD_NAME: backup_spec_to_json(self._spec),
                }
            )


def backup_spec_to_json(spec: VeleroBackupSpec) -> str:
    """Return VeleroBackupSpec as a JSON string."""
    return json.dumps(asdict(spec))
