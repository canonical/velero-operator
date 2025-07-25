# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Velero Core library to interact with Velero CLI and Kubernetes resources."""

import logging
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

from charms.velero_libs.v0.velero_backup_config import VeleroBackupSpec
from lightkube import Client, codecs
from lightkube.core.exceptions import ApiError, LoadResourceError
from lightkube.models.apps_v1 import DeploymentCondition
from lightkube.models.core_v1 import ContainerStatus, ServicePort
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.apiextensions_v1 import CustomResourceDefinition
from lightkube.resources.apps_v1 import DaemonSet, Deployment
from lightkube.resources.core_v1 import Pod, Secret, Service, ServiceAccount
from lightkube.resources.rbac_authorization_v1 import ClusterRoleBinding
from lightkube.types import PatchType

from constants import (
    K8S_CHECK_ATTEMPTS,
    K8S_CHECK_DELAY,
    K8S_CHECK_OBSERVATIONS,
    K8S_CHECK_VELERO_ATTEMPTS,
    K8S_CHECK_VELERO_DELAY,
    K8S_CHECK_VELERO_OBSERVATIONS,
    VELERO_BACKUP_LOCATION_NAME,
    VELERO_BACKUP_LOCATION_RESOURCE,
    VELERO_CLUSTER_ROLE_BINDING_NAME,
    VELERO_DEPLOYMENT_NAME,
    VELERO_METRICS_PORT,
    VELERO_METRICS_SERVICE_NAME,
    VELERO_NODE_AGENT_NAME,
    VELERO_SECRET_KEY,
    VELERO_SECRET_NAME,
    VELERO_SERVICE_ACCOUNT_NAME,
    VELERO_VOLUME_SNAPSHOT_LOCATION_NAME,
    VELERO_VOLUME_SNAPSHOT_LOCATION_RESOURCE,
)
from k8s_utils import (
    K8sResource,
    k8s_create_cluster_ip_service,
    k8s_create_secret,
    k8s_get_backup_name_by_uid,
    k8s_remove_resource,
    k8s_resource_exists,
    k8s_retry_check,
)

from .crds import Backup, BackupSpecModel, ExistingResourcePolicy, Restore, RestoreSpecModel
from .providers import VeleroStorageProvider

logger = logging.getLogger(__name__)


@dataclass
class BackupInfo:
    """Data class to hold backup information."""

    uid: str
    name: str
    labels: Dict[str, str]
    annotations: Dict[str, str]
    phase: str
    start_timestamp: str
    completion_timestamp: Optional[str] = None


class VeleroError(Exception):
    """Base class for Velero exceptions."""


class VeleroStatusError(VeleroError):
    """Exception raised for Velero status errors."""


class VeleroBackupStatusError(VeleroStatusError):
    """Exception raised for Velero backup status errors."""

    def __init__(self, name: str, reason: str) -> None:
        """Initialize the VeleroBackupStatusError with a name and reason."""
        super().__init__(f"Velero backup '{name}' failed: {reason}")
        self.name = name
        self.reason = reason


class VeleroRestoreStatusError(VeleroStatusError):
    """Exception raised for Velero restore status errors."""

    def __init__(self, name: str, reason: str) -> None:
        """Initialize the VeleroRestoreStatusError with a name and reason."""
        super().__init__(f"Velero restore '{name}' failed: {reason}")
        self.name = name
        self.reason = reason


class VeleroCLIError(VeleroError):
    """Exception raised for Velero CLI errors."""


class Velero:
    """Wrapper for the Velero binary."""

    def __init__(self, velero_binary_path: str, namespace: str) -> None:
        """Initialize the Velero class.

        The class provides a python API to interact with the Velero binary, supporting
        operations like install, add backup location, create backup, restore backup, etc.

        Args:
            velero_binary_path: The path to the Velero binary.
            namespace: The namespace where Velero is installed.
        """
        self._velero_binary_path = velero_binary_path
        self._namespace = namespace

    # PROPERTIES

    @property
    def _velero_install_flags(self) -> list:
        """Return the default Velero install flags."""
        return [
            f"--namespace={self._namespace}",
            "--no-default-backup-location",
            "--no-secret",
            "--use-volume-snapshots=false",
        ]

    @property
    def _velero_crb_name(self) -> str:
        """Return the Velero ClusterRoleBinding name."""
        postfix = f"-{self._namespace}" if self._namespace != "velero" else ""
        return VELERO_CLUSTER_ROLE_BINDING_NAME + postfix

    @property
    def _crds(self) -> List[K8sResource]:
        """Return the Velero CRDs.

        Raises:
            VeleroCLIError: If the CRDs cannot be loaded from the dry-run install output.
        """
        return [
            K8sResource(name=crd.metadata.name, type=CustomResourceDefinition)
            for crd in reversed(self._get_crds())
            if crd.metadata and crd.metadata.name
        ]

    @property
    def _core_resources(self) -> List[K8sResource]:
        """Return the core Velero resources."""
        return [
            K8sResource(VELERO_DEPLOYMENT_NAME, Deployment),
            K8sResource(VELERO_NODE_AGENT_NAME, DaemonSet),
            K8sResource(VELERO_SERVICE_ACCOUNT_NAME, ServiceAccount),
            K8sResource(VELERO_METRICS_SERVICE_NAME, Service),
            K8sResource(self._velero_crb_name, ClusterRoleBinding),
        ]

    @property
    def _storage_provider_resources(self) -> List[K8sResource]:
        """Return the Velero storage provider resources."""
        return [
            K8sResource(VELERO_SECRET_NAME, Secret),
            K8sResource(
                VELERO_BACKUP_LOCATION_NAME,
                VELERO_BACKUP_LOCATION_RESOURCE,
            ),
            K8sResource(
                VELERO_VOLUME_SNAPSHOT_LOCATION_NAME,
                VELERO_VOLUME_SNAPSHOT_LOCATION_RESOURCE,
            ),
        ]

    @property
    def _all_resources(self) -> List[K8sResource]:
        """Return all Velero resources."""
        return self._storage_provider_resources + self._crds + self._core_resources

    # METHODS

    def _get_crds(self) -> List[CustomResourceDefinition]:
        """Get the Velero CRDs from the dry-run install output.

        Raises:
            VeleroCLIError: If the CRDs cannot be loaded from the dry-run install output.
        """
        try:
            output = subprocess.check_output(
                [self._velero_binary_path, "install", "--crds-only", "--dry-run", "-o", "yaml"],
                text=True,
            )
            return [
                crd
                for crd in codecs.load_all_yaml(output)
                if isinstance(crd, CustomResourceDefinition)
            ]
        except (LoadResourceError, subprocess.CalledProcessError) as e:
            logger.error("Failed to load Velero CRDs from dry-run install: %s", e)
            raise VeleroCLIError("Failed to load Velero CRDs from dry-run install") from e

    def _create_storage_secret(
        self, kube_client: Client, storage_provider: VeleroStorageProvider
    ) -> None:
        """Create a secret for the storage provider.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.
            storage_provider (VeleroStorageProvider): The storage provider to configure.

        Raises:
            VeleroError: If the secret creation fails.
        """
        try:
            k8s_create_secret(
                kube_client,
                VELERO_SECRET_NAME,
                self._namespace,
                data={
                    VELERO_SECRET_KEY: storage_provider.secret_data,
                },
                labels={
                    "component": "velero",
                },
            )
        except ApiError as ae:
            raise VeleroError(
                f"Failed to create secret for '{storage_provider.plugin}' storage provider"
            ) from ae

    def _add_storage_plugin(self, storage_provider: VeleroStorageProvider) -> None:
        """Add the storage plugin to Velero.

        Args:
            storage_provider (VeleroStorageProvider): The storage provider to add.

        Raises:
            VeleroCLIError: If the plugin addition fails.
        """
        try:
            subprocess.run(
                [
                    self._velero_binary_path,
                    "plugin",
                    "add",
                    storage_provider.plugin_image,
                    "--confirm",
                    f"--namespace={self._namespace}",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as cpe:
            error_msg = (
                f"'velero plugin add' command returned non-zero exit code: {cpe.returncode}."
            )
            logging.error(error_msg)
            logging.error("stdout: %s", cpe.stdout)
            logging.error("stderr: %s", cpe.stderr)
            raise VeleroCLIError("Failed to add Velero provider plugin") from cpe

    def _add_backup_location(self, storage_provider: VeleroStorageProvider) -> None:
        """Add the backup location to Velero.

        Args:
            storage_provider (VeleroStorageProvider): The storage provider to add.

        Raises:
            VeleroCLIError: If the backup location addition fails.
        """
        try:
            config_flags = ",".join(
                [
                    f"{key}={value}"
                    for key, value in storage_provider.backup_location_config.items()
                ]
            )
            config = ["--config", config_flags] if config_flags else []
            prefix = ["--prefix", storage_provider.path] if storage_provider.path else []
            subprocess.run(
                [
                    self._velero_binary_path,
                    "backup-location",
                    "create",
                    VELERO_BACKUP_LOCATION_NAME,
                    "--provider",
                    storage_provider.plugin,
                    *prefix,
                    "--bucket",
                    storage_provider.bucket,
                    *config,
                    f"--credential={VELERO_SECRET_NAME}={VELERO_SECRET_KEY}",
                    "--default",
                    f"--namespace={self._namespace}",
                    "--labels",
                    "component=velero",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as cpe:
            error_msg = (
                "'velero backup-location create' command returned non-zero exit code: "
                f"{cpe.returncode}."
            )
            logging.error(error_msg)
            logging.error("stdout: %s", cpe.stdout)
            logging.error("stderr: %s", cpe.stderr)
            raise VeleroCLIError("Failed to add Velero backup location") from cpe

    def _add_volume_snapshot_location(self, storage_provider: VeleroStorageProvider) -> None:
        """Add the volume snapshot location to Velero.

        Args:
            storage_provider (VeleroStorageProvider): The storage provider to add.

        Raises:
            VeleroCLIError: If the volume snapshot location addition fails.
        """
        try:
            config_flags = ",".join(
                [
                    f"{key}={value}"
                    for key, value in storage_provider.volume_snapshot_location_config.items()
                ]
            )
            config = ["--config", config_flags] if config_flags else []
            subprocess.run(
                [
                    self._velero_binary_path,
                    "snapshot-location",
                    "create",
                    VELERO_VOLUME_SNAPSHOT_LOCATION_NAME,
                    "--provider",
                    storage_provider.plugin,
                    *config,
                    f"--credential={VELERO_SECRET_NAME}={VELERO_SECRET_KEY}",
                    f"--namespace={self._namespace}",
                    "--labels",
                    "component=velero",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as cpe:
            error_msg = (
                "'velero snapshot-location create' command returned non-zero exit code: "
                f"{cpe.returncode}."
            )
            logging.error(error_msg)
            logging.error("stdout: %s", cpe.stdout)
            logging.error("stderr: %s", cpe.stderr)
            raise VeleroCLIError("Failed to add Velero volume snapshot location") from cpe

    def _configure_metrics_service(self, kube_client: Client) -> None:
        """Configure the Velero metrics Cluster IP service.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.

        Raises:
            VeleroError: If the configuration fails.
        """
        try:
            k8s_create_cluster_ip_service(
                kube_client,
                VELERO_METRICS_SERVICE_NAME,
                self._namespace,
                selector={"deploy": "velero"},
                ports=[
                    ServicePort(
                        name="metrics",
                        port=VELERO_METRICS_PORT,
                        targetPort=VELERO_METRICS_PORT,
                        protocol="TCP",
                    )
                ],
                labels={
                    "component": "velero",
                },
            )
        except ApiError as ae:
            if ae.status.code != 409:
                raise VeleroError(
                    "Failed to create ClusterIP service for the Velero Deployment"
                ) from ae

    def is_installed(self, kube_client: Client, use_node_agent: bool) -> bool:
        """Check if Velero is installed in the Kubernetes cluster.

        Args:
            kube_client: The lightkube client used to interact with the cluster.
            use_node_agent: Whether to use the Velero node agent (DaemonSet).

        Returns:
            bool: True if Velero is installed, False otherwise.
        """
        logger.info("Checking if Velero is installed")
        for resource in self._core_resources:
            if not use_node_agent and resource.type is DaemonSet:
                continue
            if not k8s_resource_exists(kube_client, resource, self._namespace):
                return False
        return True

    def is_storage_configured(self, kube_client: Client) -> bool:
        """Check if the storage provider resources are  configured in the Kubernetes cluster.

        Args:
            kube_client (Client): The Kubernetes client used to query the cluster.

        Returns:
            bool: True if all storage provider resources exist in the cluster, False otherwise.
        """
        logger.info("Checking if Velero storage locations are configured")
        for resource in self._storage_provider_resources:
            if not k8s_resource_exists(kube_client, resource, self._namespace):
                return False
        return True

    def configure_storage_locations(
        self, kube_client: Client, storage_provider: VeleroStorageProvider
    ) -> None:
        """Configure the storage locations for Velero.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.
            storage_provider (VeleroStorageProvider): The storage provider to configure.

        Raises:
            VeleroError: If the configuration fails.
            VeleroCLIError: If the CLI command fails.
        """
        create_msg = (
            "Configuring Velero storage locations with the following settings:\n"
            f"  Backup location: '{VELERO_BACKUP_LOCATION_NAME}'\n"
            f"  Volume location: '{VELERO_VOLUME_SNAPSHOT_LOCATION_NAME}'\n"
            f"  Namespace: '{self._namespace}'\n"
            f"  Storage provider: '{storage_provider.plugin}'\n"
            f"  Plugin image: '{storage_provider.plugin_image}'\n"
            f"  Secret name: '{VELERO_SECRET_NAME}'\n"
        )
        logger.info(create_msg)

        self._create_storage_secret(kube_client, storage_provider)
        self._add_storage_plugin(storage_provider)
        self._add_backup_location(storage_provider)
        self._add_volume_snapshot_location(storage_provider)

        logger.info("Velero storage locations configured successfully")

    def install(
        self,
        kube_client: Client,
        velero_image: str,
        use_node_agent: bool,
        default_volumes_to_fs_backup: bool,
    ) -> None:
        """Install Velero in the Kubernetes cluster.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.
            velero_image: The Velero image to use.
            use_node_agent: Whether to use the Velero node agent (DaemonSet).
            default_volumes_to_fs_backup: Whether to default volumes to filesystem backup.

        Raises:
            VeleroCLIError: If the CLI installation fails.
            VeleroError: If metrics service creation fails.
        """
        install_msg = (
            "Installing the Velero with the following settings:\n"
            f"  Image: '{velero_image}'\n"
            f"  Namespace: '{self._namespace}'\n"
            f"  Node-agent enabled: '{use_node_agent}'"
            f"  Default volumes to filesystem backup: '{default_volumes_to_fs_backup}'\n"
        )
        try:
            logger.info(install_msg)
            subprocess.run(
                [
                    self._velero_binary_path,
                    "install",
                    f"--image={velero_image}",
                    *self._velero_install_flags,
                    f"--use-node-agent={use_node_agent}",
                    f"--default-volumes-to-fs-backup={default_volumes_to_fs_backup}",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self._configure_metrics_service(kube_client)
        except subprocess.CalledProcessError as cpe:
            error_msg = f"'velero install' command returned non-zero exit code: {cpe.returncode}."
            logging.error(error_msg)
            logging.error("stdout: %s", cpe.stdout)
            logging.error("stderr: %s", cpe.stderr)
            raise VeleroCLIError("Failed to install Velero on the cluster") from cpe

    def remove_storage_locations(self, kube_client: Client) -> None:
        """Remove the storage locations from the cluster.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.

        Raises:
            VeleroError: If the removal fails.
        """
        remove_msg = (
            f"Uninstalling the following Velero resources from '{self._namespace}' namespace:\n"
            + "\n".join(
                [
                    f"    {res.type.__name__}: '{res.name}'"
                    for res in self._storage_provider_resources
                ]
            )
        )
        logger.info(remove_msg)

        for resource in self._storage_provider_resources:
            try:
                k8s_remove_resource(kube_client, resource, self._namespace)
            except ApiError as ae:
                raise VeleroError(
                    f"Failed to remove resource {resource.type.__name__}: {resource.name}"
                ) from ae

        try:
            kube_client.patch(
                Deployment,
                VELERO_DEPLOYMENT_NAME,
                {"spec": {"template": {"spec": {"initContainers": None}}}},
                namespace=self._namespace,
            )
        except ApiError as ae:
            logger.error("Failed to patch deployment: %s", ae)
            raise VeleroError(f"Failed to patch deployment {VELERO_DEPLOYMENT_NAME}") from ae

    def remove_node_agent(self, kube_client: Client) -> None:
        """Remove the Velero node agent from the cluster.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.

        Raises:
            VeleroError: If the removal fails.
        """
        remove_msg = f"Uninstalling the Velero NodeAgent from '{self._namespace}' namespace"
        logger.info(remove_msg)

        try:
            k8s_remove_resource(
                kube_client, K8sResource(VELERO_NODE_AGENT_NAME, DaemonSet), self._namespace
            )
        except ApiError as ae:
            raise VeleroError("Failed to remove Velero NodeAgent") from ae

    def update_velero_deployment_flags(
        self, kube_client: Client, default_volumes_to_fs_backup: bool
    ) -> None:
        """Update the Velero Deployment flags.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.
            default_volumes_to_fs_backup (bool): The new value for the default-volumes-to-fs-backup

        Raises:
            VeleroError: If the update fails.
        """
        flags: Dict[str, Union[str, bool]] = {
            "default-volumes-to-fs-backup": default_volumes_to_fs_backup,
        }
        try:
            deployment = kube_client.get(
                Deployment, VELERO_DEPLOYMENT_NAME, namespace=self._namespace
            )
            if (
                not deployment.spec
                or not deployment.spec.template
                or not deployment.spec.template.spec
            ):
                raise VeleroError("Velero Deployment has no valid spec")

            container = next(
                (
                    c
                    for c in deployment.spec.template.spec.containers
                    if c.name == VELERO_DEPLOYMENT_NAME
                ),
                None,
            )
            if not container or not container.args:
                raise VeleroError("Failed to get Velero Deployment container arguments")

            new_args = [
                arg
                for arg in container.args
                if not any(arg.startswith(f"--{flag}=") for flag in flags.keys())
            ]
            new_args += [f"--{flag}={str(value).lower()}" for flag, value in flags.items()]

            new_deployment_spec = {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": VELERO_DEPLOYMENT_NAME,
                                    "args": new_args,
                                }
                            ]
                        }
                    },
                    "strategy": {"type": "Recreate", "rollingUpdate": None},
                }
            }
            kube_client.patch(
                Deployment,
                VELERO_DEPLOYMENT_NAME,
                new_deployment_spec,
                namespace=self._namespace,
            )
        except ApiError as ae:
            if ae.status.code != 404:
                logger.error("Failed to update Velero Deployment arguments: %s", ae)
                raise VeleroError("Failed to update Velero Deployment arguments") from ae

    def update_velero_node_agent_image(self, kube_client: Client, new_image: str) -> None:
        """Update the Velero NodeAgent image.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.
            new_image (str): The new Velero NodeAgent image to use.

        Raises:
            VeleroError: If the update fails.
        """
        new_node_agent_spec = {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": VELERO_NODE_AGENT_NAME,
                                "image": new_image,
                            }
                        ]
                    }
                },
                "strategy": {"type": "Recreate", "rollingUpdate": None},
            }
        }
        try:
            kube_client.patch(
                DaemonSet,
                VELERO_NODE_AGENT_NAME,
                new_node_agent_spec,
                namespace=self._namespace,
            )
        except ApiError as ae:
            if ae.status.code != 404:
                logger.error("Failed to update Velero NodeAgent image: %s", ae)
                raise VeleroError(
                    f"Failed to update Velero NodeAgent image to '{new_image}'"
                ) from ae

    def update_velero_deployment_image(self, kube_client: Client, new_image: str) -> None:
        """Update the Velero Deployment image.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.
            new_image (str): The new Velero image to use.

        Raises:
            VeleroError: If the update fails.
        """
        new_deployment_spec = {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": VELERO_DEPLOYMENT_NAME,
                                "image": new_image,
                            }
                        ]
                    }
                },
                "strategy": {"type": "Recreate", "rollingUpdate": None},
            }
        }
        try:
            kube_client.patch(
                Deployment,
                VELERO_DEPLOYMENT_NAME,
                new_deployment_spec,
                namespace=self._namespace,
            )
        except ApiError as ae:
            if ae.status.code != 404:
                logger.error("Failed to update Velero Deployment image: %s", ae)
                raise VeleroError(
                    f"Failed to update Velero Deployment image to '{new_image}'"
                ) from ae

    def update_plugin_image(self, kube_client: Client, new_image: str):
        """Update the Velero plugin image.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.
            new_image (str): The new Velero plugin image to use.

        Raises:
            VeleroError: If the update fails.
        """
        try:
            kube_client.patch(
                Deployment,
                VELERO_DEPLOYMENT_NAME,
                [
                    {
                        "op": "replace",
                        "path": "/spec/template/spec/initContainers/0/image",
                        "value": new_image,
                    }
                ],
                patch_type=PatchType.JSON,
                namespace=self._namespace,
            )
        except ApiError as ae:
            if ae.status.code != 404:
                logger.error("Failed to update Velero plugin image: %s", ae)
                raise VeleroError(f"Failed to update Velero plugin image to '{new_image}'") from ae

    def remove(self, kube_client: Client) -> None:
        """Remove Velero resources from the cluster.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.
        """
        remove_msg = (
            f"Uninstalling the following Velero resources from '{self._namespace}' namespace:\n"
            + "\n".join([f"    {res.type.__name__}: '{res.name}'" for res in self._all_resources])
        )
        logger.info(remove_msg)

        for resource in self._all_resources:
            try:
                k8s_remove_resource(kube_client, resource, self._namespace)
            except ApiError:
                pass

    def upgrade(self, kube_client: Client) -> None:
        """Upgrade Velero deployment.

        Raises:
            VeleroError: If the upgrade fails.
        """
        logger.info("Upgrading Velero CRDs")
        for crd in self._get_crds():
            try:
                kube_client.apply(crd)
            except ApiError as ae:
                if ae.status.code != 404:
                    logger.error("Failed to upgrade Velero CRDs: %s", ae)
                    raise VeleroError("Failed to upgrade Velero CRDs") from ae

    def run_cli_command(self, command: List[str]) -> str:
        """Run a Velero CLI command.

        Args:
            command (List[str]): The command to run, as a list of strings.

        Returns:
            str: The output of the command.

        Raises:
            VeleroCLIError: If the command fails.
            ValueError: If the command is empty.
        """
        if not command:
            raise ValueError("Command cannot be empty")

        try:
            result = subprocess.check_output(
                [self._velero_binary_path, *command, f"--namespace={self._namespace}"],
                text=True,
            )
            return result.strip()
        except subprocess.CalledProcessError as cpe:
            error_msg = (
                f"'velero {' '.join(command)}' returned non-zero exit code: {cpe.returncode}."
            )
            logging.error(error_msg)
            logging.error("stdout: %s", cpe.stdout)
            logging.error("stderr: %s", cpe.stderr)

            raise VeleroCLIError(error_msg) from cpe

    def create_backup(
        self,
        kube_client: Client,
        name_prefix: str,
        spec: VeleroBackupSpec,
        default_volumes_to_fs_backup: bool,
        labels: Optional[Dict[str, str]] = None,
        annotations: Optional[Dict[str, str]] = None,
    ) -> str:
        """Create a Velero Backup Custom Resource using the provided spec.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.
            name_prefix (str): The name of the application for which the backup is created.
                The backup name will be prefixed with this value and `generateName` will be used
            spec (VeleroBackupSpec): The backup specification containing the backup details.
            default_volumes_to_fs_backup (bool): Whether to default volumes to filesystem backup.
            labels (Optional[Dict[str, str]]): Additional labels to apply to the backup resource.
            annotations (Optional[Dict[str, str]]):
                Additional annotations to apply to the backup resource.

        Returns:
            str: The name of the created backup.

        Raises:
            ApiError: If status check fails or if the backup creation fails.
            VeleroError: If the backup creation fails
            VeleroBackupStatusError: If the backup status is not successful.
        """
        backup = Backup(
            metadata=ObjectMeta(
                generateName=name_prefix,
                namespace=self._namespace,
                labels=labels,
                annotations=annotations,
            ),
            spec=BackupSpecModel(
                storageLocation=VELERO_BACKUP_LOCATION_NAME,
                volumeSnapshotLocations=[VELERO_VOLUME_SNAPSHOT_LOCATION_NAME],
                includedNamespaces=spec.include_namespaces,
                includedResources=spec.include_resources,
                excludedNamespaces=spec.exclude_namespaces,
                excludedResources=spec.exclude_resources,
                ttl=spec.ttl,
                includeClusterResources=spec.include_cluster_resources,
                labelSelector=(
                    {"matchLabels": spec.label_selector} if spec.label_selector else None
                ),
                defaultVolumesToFsBackup=default_volumes_to_fs_backup,
            ),
        )

        logger.info("Creating Velero Backup: name_prefix: '%s', spec: %s", name_prefix, spec)
        try:
            created_backup = kube_client.create(backup)
            if not created_backup.metadata or not created_backup.metadata.name:  # pragma: no cover
                raise VeleroError("Failed to create Velero Backup: no name in metadata")
            name = created_backup.metadata.name
        except ApiError as ae:
            logger.error("Failed to create Velero Backup '%s': %s", name_prefix, ae)
            raise VeleroError(f"Failed to create Velero Backup '{name_prefix}'") from ae

        Velero.check_velero_backup(kube_client, self._namespace, name)
        return name

    def create_restore(
        self,
        kube_client: Client,
        backup_uid: str,
        existing_resource_policy: ExistingResourcePolicy = ExistingResourcePolicy.No,
        labels: Optional[Dict[str, str]] = None,
        annotations: Optional[Dict[str, str]] = None,
    ) -> str:
        """Create a Velero Restore Custom Resource using the provided backup name.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.
            backup_uid (str): The UID of the backup to restore from.
                Will be used to generate the restore name.
            existing_resource_policy (ExistingResourcePolicy, optional):
                Policy for existing resources. Defaults to ExistingResourcePolicy.No ("none").
            labels (Optional[Dict[str, str]], optional):
                Additional labels to apply to the restore resource.
            annotations (Optional[Dict[str, str]], optional):
                Additional annotations to apply to the restore resource.

        Returns:
            str: The name of the created restore.

        Raises:
            ApiError: If the backup does not exist or if the restore creation fails.
            VeleroError: If the restore creation fails.
            VeleroRestoreStatusError: If the restore status is not successful.
        """
        logger.info("Checking if Velero Backup with UID '%s' exists", backup_uid)
        backup_name = k8s_get_backup_name_by_uid(
            kube_client,
            backup_uid,
            self._namespace,
        )

        if not backup_name:
            raise VeleroError(f"Velero Backup with UID '{backup_uid}' not found")

        restore = Restore(
            metadata=ObjectMeta(
                generateName=backup_name,
                namespace=self._namespace,
                labels=labels,
                annotations=annotations,
            ),
            spec=RestoreSpecModel(
                backupName=backup_name,
                existingResourcePolicy=existing_resource_policy,
            ),
        )

        logger.info("Creating Velero Restore: bakcup_name: '%s'", backup_name)
        try:
            created_restore = kube_client.create(restore)
            if (
                not created_restore.metadata or not created_restore.metadata.name
            ):  # pragma: no cover
                raise VeleroError("Failed to create Velero Restore: no name in metadata")
            restore_name = created_restore.metadata.name
        except ApiError as ae:
            logger.error("Failed to create Velero Restore from backup '%s': %s", backup_name, ae)
            raise VeleroError(
                f"Failed to create Velero Restore from backup '{backup_name}'"
            ) from ae

        Velero.check_velero_restore(kube_client, self._namespace, restore_name)
        return restore_name

    def list_backups(
        self, kube_client: Client, labels: Optional[Dict[str, Optional[str]]] = None
    ) -> List[BackupInfo]:
        """List all Velero backups in the cluster.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.
            labels (Optional[Dict[str, Optional[str]]], optional):
                Labels to filter the backups. Defaults to None.

        Raises:
            VeleroError: If the backup listing fails.
        """
        try:
            backups = kube_client.list(
                Backup,
                namespace=self._namespace,
                labels=labels,  # type: ignore
            )
            backup_infos = []
            for backup in backups:
                if not backup.metadata or not backup.metadata.name or not backup.metadata.uid:
                    logger.warning("Backup metadata is missing or has no name")
                    continue
                if not backup.metadata.labels or not backup.metadata.annotations:
                    logger.warning(
                        f"Backup metadata labels are missing for {backup.metadata.name}"
                    )
                    continue
                if (
                    not backup.status
                    or not backup.status.phase
                    or not backup.status.startTimestamp
                ):
                    logger.warning(f"Backup status is missing for {backup.metadata.name}")
                    continue
                backup_infos.append(
                    BackupInfo(
                        uid=backup.metadata.uid,
                        name=backup.metadata.name,
                        labels=backup.metadata.labels,
                        annotations=backup.metadata.annotations,
                        phase=backup.status.phase,
                        start_timestamp=backup.status.startTimestamp,
                        completion_timestamp=backup.status.completionTimestamp,
                    )
                )
            return backup_infos
        except ApiError as ae:
            logger.error("Failed to list Velero Backups: %s", ae)
            raise VeleroError("Failed to list Velero Backups") from ae

    # CHECKERS

    @staticmethod
    def _get_deployment_availability(
        deployment: Deployment, error_message: str
    ) -> DeploymentCondition:
        if not deployment.status:
            raise VeleroStatusError(error_message.format(reason="No status"))

        if not deployment.status.conditions:
            raise VeleroStatusError(error_message.format(reason="No conditions"))

        for condition in deployment.status.conditions:
            if condition.type == "Available":
                return condition

        raise VeleroStatusError(error_message.format(reason="No Available condition"))

    @staticmethod
    def _get_deployment_pods(
        kube_client: Client, deployment: Deployment, namespace: str
    ) -> List[Pod]:
        if (
            not deployment.spec
            or not deployment.spec.selector
            or not deployment.spec.selector.matchLabels
        ):
            return []
        return list(
            kube_client.list(Pod, namespace=namespace, labels=deployment.spec.selector.matchLabels)
        )

    @staticmethod
    def _get_pod_container_statuses(pod: Pod) -> List[ContainerStatus]:
        if pod.status:
            container_statuses = (
                pod.status.containerStatuses if pod.status.containerStatuses else []
            )
            init_container_statuses = (
                pod.status.initContainerStatuses if pod.status.initContainerStatuses else []
            )
            return container_statuses + init_container_statuses
        return []

    @staticmethod
    def check_velero_backup(kube_client: Client, namespace: str, name: str) -> None:
        """Check the readiness of the Velero Backup in the Kubernetes cluster.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.
            namespace (str): The namespace where the backup is deployed.
            name (str): The name of the Velero Backup.

        Raises:
            VeleroBackupStatusError: If the Velero Backup is not ready.
            APIError: If the backup is not found.
        """

        def check_backup() -> None:
            backup = kube_client.get(Backup, name=name, namespace=namespace)
            if not backup.status or not backup.status.phase:
                raise VeleroBackupStatusError(name=name, reason="No status or phase present")

            if backup.status.phase == "Completed":
                return

            if backup.status.phase in ["PartiallyFailed", "Failed"]:
                raise VeleroBackupStatusError(
                    name=name, reason=f"Status is '{backup.status.phase}'"
                )
            else:
                raise VeleroStatusError(
                    f"Velero Backup is still in progress: '{backup.status.phase}'"
                )

        logger.info("Checking the Velero Backup completeness")
        k8s_retry_check(
            check_backup,
            retry_exceptions=(VeleroStatusError, ApiError),
            attempts=K8S_CHECK_VELERO_ATTEMPTS,
            delay=K8S_CHECK_VELERO_DELAY,
            min_successful=K8S_CHECK_VELERO_OBSERVATIONS,
        )

    @staticmethod
    def check_velero_restore(kube_client: Client, namespace: str, name: str) -> None:
        """Check the readiness of the Velero Restore in the Kubernetes cluster.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.
            namespace (str): The namespace where the restore is deployed.
            name (str): The name of the Velero Restore.

        Raises:
            VeleroRestoreStatusError: If the Velero Restore is not ready.
            APIError: If the restore is not found.
        """

        def check_restore() -> None:
            restore = kube_client.get(Restore, name=name, namespace=namespace)
            if not restore.status or not restore.status.phase:
                raise VeleroRestoreStatusError(name=name, reason="No status or phase present")

            if restore.status.phase == "Completed":
                return
            if restore.status.phase in ["PartiallyFailed", "Failed"]:
                raise VeleroRestoreStatusError(
                    name=name, reason=f"Status is '{restore.status.phase}'"
                )
            else:
                raise VeleroStatusError(
                    f"Velero Restore is still in progress: '{restore.status.phase}'"
                )

        logger.info("Checking the Velero Restore completeness")
        k8s_retry_check(
            check_restore,
            retry_exceptions=(VeleroStatusError, ApiError),
            attempts=K8S_CHECK_VELERO_ATTEMPTS,
            delay=K8S_CHECK_VELERO_DELAY,
            min_successful=K8S_CHECK_VELERO_OBSERVATIONS,
        )

    @staticmethod
    def check_velero_deployment(
        kube_client: Client, namespace: str, name: str = VELERO_DEPLOYMENT_NAME
    ) -> None:
        """Check the readiness of the Velero deployment in the Kubernetes cluster.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.
            namespace (str): The namespace where the deployment is deployed.
            name (str, optional): The name of the Velero deployment. Defaults to "velero".

        Raises:
            VeleroStatusError: If the Velero deployment is not ready.
            APIError: If the deployment is not found.
        """
        error_message = "Velero Deployment is not ready: {reason}"

        def check_deployment() -> None:
            deployment = kube_client.get(Deployment, name=name, namespace=namespace)
            availability = Velero._get_deployment_availability(deployment, error_message)

            if availability.status != "True":
                message: str | None = availability.message
                for pod in Velero._get_deployment_pods(kube_client, deployment, namespace):
                    for status in Velero._get_pod_container_statuses(pod):
                        if status.ready is False and status.state:
                            if status.state.waiting:
                                message = status.state.waiting.reason
                            if status.state.terminated:
                                message = status.state.terminated.reason
                raise VeleroStatusError(
                    error_message.format(reason=f"{message or "Not Available"}")
                )

        logger.info("Checking the Velero Deployment readiness")
        k8s_retry_check(
            check_deployment,
            retry_exceptions=(VeleroStatusError, ApiError),
            attempts=K8S_CHECK_ATTEMPTS,
            delay=K8S_CHECK_DELAY,
            min_successful=K8S_CHECK_OBSERVATIONS,
        )

    @staticmethod
    def check_velero_node_agent(
        kube_client: Client, namespace: str, name: str = VELERO_NODE_AGENT_NAME
    ) -> None:
        """Check the readiness of the Velero DaemonSet in a Kubernetes cluster.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.
            namespace (str): The namespace where the DaemonSet is deployed.
            name (str, optional): The name of the Velero DaemonSet. Defaults to "velero".

        Raises:
            VeleroStatusError: If the Velero DaemonSet is not ready.
            APIError: If the DaemonSet is not found.
        """
        error_message = "Velero NodeAgent is not ready: {reason}"

        def check_node_agent() -> None:
            daemonset = kube_client.get(DaemonSet, name=name, namespace=namespace)
            status = daemonset.status

            if not status:
                raise VeleroStatusError(error_message.format(reason="No status"))

            if status.numberAvailable != status.desiredNumberScheduled:
                raise VeleroStatusError(error_message.format(reason="Not all pods are available"))

        logger.info("Checking the Velero NodeAgent readiness")
        k8s_retry_check(
            check_node_agent,
            retry_exceptions=(VeleroStatusError, ApiError),
            attempts=K8S_CHECK_ATTEMPTS,
            delay=K8S_CHECK_DELAY,
            min_successful=K8S_CHECK_OBSERVATIONS,
        )

    @staticmethod
    def check_velero_storage_locations(
        kube_client: Client,
        namespace: str,
        backup_loc_name: str = VELERO_BACKUP_LOCATION_NAME,
        volume_loc_name: str = VELERO_VOLUME_SNAPSHOT_LOCATION_NAME,
    ) -> None:
        """Check the Velero storage locations in the Kubernetes cluster.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.
            namespace (str): The namespace where the storage locations are deployed.
            backup_loc_name (str, optional): The name of the Velero backup storage location.
                Defaults to "default".
            volume_loc_name (str, optional): The name of the Velero volume snapshot location.
                Defaults to "default".

        Raises:
            VeleroStatusError: If the storage locations are not found.
            APIError: If the storage locations are not found.
        """
        error_message = "Velero Storage location is not ready: {reason}"

        def check_backup_location() -> None:
            backup_loc: Dict[str, Any] = kube_client.get(
                VELERO_BACKUP_LOCATION_RESOURCE, name=backup_loc_name, namespace=namespace
            )
            status: Dict[str, Any] = backup_loc.get("status", {})

            if not status or not isinstance(status, dict):
                raise VeleroStatusError(
                    error_message.format(reason="BackupStorageLocation has no status")
                )

            if status.get("phase") != "Available":
                raise VeleroStatusError(
                    error_message.format(reason="BackupStorageLocation is unavailable")
                )

        logger.info("Checking the Velero BackupStorageLocation readiness")
        k8s_retry_check(
            check_backup_location,
            retry_exceptions=(VeleroStatusError, ApiError),
            attempts=K8S_CHECK_ATTEMPTS,
            delay=K8S_CHECK_DELAY,
            min_successful=K8S_CHECK_OBSERVATIONS,
        )
        logger.info("Checking the Velero VolumeSnapshotLocation readiness")
        kube_client.get(
            VELERO_VOLUME_SNAPSHOT_LOCATION_RESOURCE, volume_loc_name, namespace=namespace
        )
