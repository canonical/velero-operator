# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Velero related code."""

import logging
import subprocess

from lightkube import Client
from lightkube.core.exceptions import ApiError
from lightkube.generic_resource import create_namespaced_resource
from lightkube.resources.apps_v1 import DaemonSet, Deployment
from lightkube.resources.core_v1 import Secret, ServiceAccount
from lightkube.resources.rbac_authorization_v1 import ClusterRoleBinding
from lightkube.types import CascadeType

logger = logging.getLogger(__name__)


class VeleroError(Exception):
    """Base class for Velero exceptions."""


class Velero:
    """Wrapper for the Velero binary."""

    def __init__(self, velero_binary_path: str, namespace: str, velero_image: str) -> None:
        """Initialize the Velero class.

        The class provides a python API to interact with the Velero binary, supporting
        operations like install, add backup location, create backup, restore backup, etc.

        Args:
            velero_binary_path: The path to the Velero binary.
            namespace: The namespace where Velero is installed.
            velero_image: The Velero image to use.
        """
        self._velero_binary_path = velero_binary_path
        self._namespace = namespace
        self._velero_image = velero_image

    @property
    def _velero_install_flags(self) -> list:
        """Return the default Velero install flags."""
        return [
            f"--namespace={self._namespace}",
            f"--image={self._velero_image}",
            "--no-default-backup-location",
            "--no-secret",
            "--use-volume-snapshots=false",
        ]

    def install(self, use_node_agent: bool) -> None:
        """Install Velero in the Kubernetes cluster.

        Args:
            use_node_agent: Whether to use the Velero node agent (DaemonSet).
        """
        install_msg = (
            "Installing the Velero with the following settings:\n"
            f"  Image: '{self._velero_image}'\n"
            f"  Namespace: '{self._namespace}'\n"
            f"  Node-agent enabled: '{use_node_agent}'"
        )
        try:
            logger.info(install_msg)
            subprocess.check_call(
                [
                    self._velero_binary_path,
                    "install",
                    *self._velero_install_flags,
                    f"--use-node-agent={use_node_agent}",
                ]
            )
        except subprocess.CalledProcessError as cpe:
            error_msg = f"'velero install' command returned non-zero exit code: {cpe.returncode}."
            logging.error(error_msg)
            logging.error("stdout: %s", {cpe.stdout})
            logging.error("stderr: %s", {cpe.stderr})

            raise VeleroError(error_msg) from cpe

    def remove(self, kube_client: Client) -> None:
        """Remove Velero resourses from the cluster.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.
        """
        remove_msg = (
            f"Unistalling the following Velero resources from '{self._namespace}' namespace:\n"
            "   Deployment: 'velero'\n"
            "   DaemonSet: 'node-agent'\n"
            "   Secret: 'velero-cloud-credentials'\n"
            "   ServiceAccount: 'velero'\n"
            "   ClusterRoleBinding: 'velero'\n"
            "   BackupStorageLocation: 'default'\n"
            "   VolumeSnapshotLocation: 'default'"
        )
        logger.info(remove_msg)

        # Delete the Deployment
        try:
            kube_client.delete(
                Deployment,
                name="velero",
                cascade=CascadeType.FOREGROUND,
                namespace=self._namespace,
            )
        except ApiError as err:
            logger.warning("Failed to delete the Velero Deployment: %s", err)

        # Delete the DaemonSet
        try:
            kube_client.delete(
                DaemonSet,
                name="node-agent",
                cascade=CascadeType.FOREGROUND,
                namespace=self._namespace,
            )
        except ApiError as err:
            logger.warning("Failed to delete the Velero NogeAgent: %s", err)

        # Delete the Secret
        try:
            kube_client.delete(Secret, name="velero-cloud-credentials", namespace=self._namespace)
        except ApiError as err:
            logger.warning("Failed to delete the Velero Secret: %s", err)

        # Delete the ServiceAccount
        try:
            kube_client.delete(ServiceAccount, name="velero", namespace=self._namespace)
        except ApiError as err:
            logger.warning("Failed to delete the Velero ServiceAccount: %s", err)

        # Delete the BackupStorageLocation
        try:
            backup_storage_location = create_namespaced_resource(
                "velero.io", "v1", "BackupStorageLocation", "backupstoragelocations"
            )
            kube_client.delete(backup_storage_location, name="default", namespace=self._namespace)
        except ApiError as err:
            logger.warning("Failed to delete the Velero BackupStorageLocation: %s", err)

        # Delete the VolumeSnapshotLocation
        try:
            volume_storage_location = create_namespaced_resource(
                "velero.io", "v1", "VolumeSnapshotLocation", "volumesnapshotlocations"
            )
            kube_client.delete(volume_storage_location, name="default", namespace=self._namespace)
        except ApiError as err:
            logger.warning("Failed to delete the Velero VolumeSnapshotLocation: %s", err)

        # Delete the ClusterRoleBinding
        try:
            kube_client.delete(ClusterRoleBinding, name="velero")
        except ApiError as err:
            logger.warning("Failed to delete the Velero ClusterRoleBinding: %s", err)
