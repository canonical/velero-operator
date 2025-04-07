# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Velero related code."""

import logging
import subprocess
from abc import ABC, abstractmethod
from typing import Any, Dict, List

from lightkube import Client, codecs
from lightkube.core.exceptions import ApiError, LoadResourceError
from lightkube.models.apps_v1 import DeploymentCondition
from lightkube.resources.apiextensions_v1 import CustomResourceDefinition
from lightkube.resources.apps_v1 import DaemonSet, Deployment
from lightkube.resources.core_v1 import Secret, ServiceAccount
from lightkube.resources.rbac_authorization_v1 import ClusterRoleBinding

from constants import (
    VELERO_BACKUP_LOCATION_NAME,
    VELERO_BACKUP_LOCATION_RESOURCE,
    VELERO_CLUSTER_ROLE_BINDING_NAME,
    VELERO_DEPLOYMENT_NAME,
    VELERO_NODE_AGENT_NAME,
    VELERO_SECRET_KEY,
    VELERO_SECRET_NAME,
    VELERO_SERVICE_ACCOUNT_NAME,
    VELERO_VOLUME_SNAPSHOT_LOCATION_NAME,
    VELERO_VOLUME_SNAPSHOT_LOCATION_RESOURCE,
)
from utils import (
    K8sResource,
    k8s_create_secret,
    k8s_remove_resource,
    k8s_resource_exists,
    k8s_retry_check,
)

logger = logging.getLogger(__name__)


class VeleroError(Exception):
    """Base class for Velero exceptions."""


class VeleroStorageProvider(ABC):
    """Base class for Velero storage provider."""

    def __init__(self, plugin: str, plugin_image: str, bucket: str) -> None:
        """Initialize the VeleroStorageProvider class.

        Args:
            plugin: The name of the storage provider plugin.
            plugin_image: The image of the storage provider plugin.
            bucket: The bucket name for the storage provider.
        """
        self._plugin = plugin
        self._plugin_image = plugin_image
        self._bucket = bucket

    @property
    @abstractmethod
    def secret_data(self) -> str:
        """Return the secret data for the storage provider.

        Returns:
            str: The base64 encoded secret data for the storage provider.
        """
        pass

    @property
    @abstractmethod
    def config_flags(self) -> Dict[str, str]:
        """Return the configuration flags for the storage provider.

        Returns:
            Dict[str, str]: The configuration flags for the storage provider.
        """
        pass

    @property
    def plugin(self) -> str:
        """Return the storage provider plugin name.

        Returns:
            str: The name of the storage provider plugin.
        """
        return self._plugin

    @property
    def bucket(self) -> str:
        """Return the bucket name for the storage provider.

        Returns:
            str: The bucket name for the storage provider.
        """
        return self._bucket

    @property
    def plugin_image(self) -> str:
        """Return the storage provider plugin image.

        Returns:
            str: The image of the storage provider plugin.
        """
        return self._plugin_image


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
        """Return the Velero CRDs by parsing the dry-run install YAML output.

        Raises:
            VeleroError: If the CRDs cannot be loaded from the dry-run install output.
        """
        try:
            output = subprocess.check_output(
                [self._velero_binary_path, "install", "--crds-only", "--dry-run", "-o", "yaml"],
                text=True,
            )
            resources = codecs.load_all_yaml(output)
        except (LoadResourceError, subprocess.CalledProcessError) as e:
            logger.error("Failed to load Velero CRDs from dry-run install: %s", e)
            raise VeleroError("Failed to load Velero CRDs from dry-run install") from e

        return [
            K8sResource(name=crd.metadata.name, type=CustomResourceDefinition)
            for crd in reversed(resources)
            if isinstance(crd, CustomResourceDefinition) and crd.metadata and crd.metadata.name
        ]

    @property
    def _core_resources(self) -> List[K8sResource]:
        """Return the core Velero resources."""
        return [
            K8sResource(VELERO_DEPLOYMENT_NAME, Deployment),
            K8sResource(VELERO_NODE_AGENT_NAME, DaemonSet),
            K8sResource(VELERO_SERVICE_ACCOUNT_NAME, ServiceAccount),
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
                f"Failed to create secret '{VELERO_SECRET_NAME}' for '{storage_provider.plugin}'"
            ) from ae

    def _add_storage_plugin(self, storage_provider: VeleroStorageProvider) -> None:
        """Add the storage plugin to Velero.

        Args:
            storage_provider (VeleroStorageProvider): The storage provider to add.

        Raises:
            VeleroError: If the plugin addition fails.
        """
        try:
            subprocess.run(
                [
                    self._velero_binary_path,
                    "plugin",
                    "add",
                    storage_provider.plugin_image,
                    "--confirm",
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

            raise VeleroError(error_msg) from cpe

    def _add_backup_location(self, storage_provider: VeleroStorageProvider) -> None:
        """Add the backup location to Velero.

        Args:
            storage_provider (VeleroStorageProvider): The storage provider to add.

        Raises:
            VeleroError: If the backup location addition fails.
        """
        try:
            subprocess.run(
                [
                    self._velero_binary_path,
                    "backup-location",
                    "create",
                    VELERO_BACKUP_LOCATION_NAME,
                    "--provider",
                    storage_provider.plugin,
                    "--bucket",
                    storage_provider.bucket,
                    "--config",
                    *[f"{key}={value}" for key, value in storage_provider.config_flags.items()],
                    "--label",
                    "component=velero",
                    f"--credential={VELERO_SECRET_NAME}={VELERO_SECRET_KEY}",
                    "--default",
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

            raise VeleroError(error_msg) from cpe

    def _add_volume_snapshot_location(self, storage_provider: VeleroStorageProvider) -> None:
        """Add the volume snapshot location to Velero.

        Args:
            storage_provider (VeleroStorageProvider): The storage provider to add.

        Raises:
            VeleroError: If the volume snapshot location addition fails.
        """
        try:
            subprocess.run(
                [
                    self._velero_binary_path,
                    "snapshot-location",
                    "create",
                    VELERO_VOLUME_SNAPSHOT_LOCATION_NAME,
                    "--provider",
                    storage_provider.plugin,
                    "--config",
                    *[f"{key}={value}" for key, value in storage_provider.config_flags.items()],
                    "--label",
                    "component=velero",
                    f"--credential={VELERO_SECRET_NAME}={VELERO_SECRET_KEY}",
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

            raise VeleroError(error_msg) from cpe

    def is_installed(self, kube_client: Client, use_node_agent: bool) -> bool:
        """Check if Velero is installed in the Kubernetes cluster.

        Args:
            kube_client: The lightkube client used to interact with the cluster.
            use_node_agent: Whether to use the Velero node agent (DaemonSet).

        Returns:
            bool: True if Velero is installed, False otherwise.
        """
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

    def install(self, velero_image: str, use_node_agent: bool) -> None:
        """Install Velero in the Kubernetes cluster.

        Args:
            velero_image: The Velero image to use.
            use_node_agent: Whether to use the Velero node agent (DaemonSet).

        Raises:
            VeleroError: If the installation fails.
        """
        install_msg = (
            "Installing the Velero with the following settings:\n"
            f"  Image: '{velero_image}'\n"
            f"  Namespace: '{self._namespace}'\n"
            f"  Node-agent enabled: '{use_node_agent}'"
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
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as cpe:
            error_msg = f"'velero install' command returned non-zero exit code: {cpe.returncode}."
            logging.error(error_msg)
            logging.error("stdout: %s", cpe.stdout)
            logging.error("stderr: %s", cpe.stderr)

            raise VeleroError(error_msg) from cpe

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
            except ApiError as err:
                raise VeleroError(
                    f"Failed to remove resource {resource.type.__name__}: {resource.name}"
                ) from err

        # TODO: Remove the storage provider plugin

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

    # CHECKERS

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
            VeleroError: If the Velero deployment is not ready.
            APIError: If the deployment is not found.
        """

        def get_deployment_availability(deployment: Deployment) -> DeploymentCondition:
            if not deployment.status:
                raise VeleroError("Deployment has no status")

            if not deployment.status.conditions:
                raise VeleroError("Deployment has no conditions")

            for condition in deployment.status.conditions:
                if condition.type == "Available":
                    return condition

            raise VeleroError("Deployment has no Available condition")

        def check_deployment() -> None:
            deployment = kube_client.get(Deployment, name=name, namespace=namespace)
            availability = get_deployment_availability(deployment)

            if availability.status != "True":
                raise VeleroError(availability.message)

        logger.info("Checking the Velero Deployment readiness")
        k8s_retry_check(check_deployment)

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
            VeleroError: If the Velero DaemonSet is not ready.
            APIError: If the DaemonSet is not found.
        """

        def check_node_agent() -> None:
            daemonset = kube_client.get(DaemonSet, name=name, namespace=namespace)
            status = daemonset.status

            if not status:
                raise VeleroError("DaemonSet has no status")

            if status.numberAvailable != status.desiredNumberScheduled:
                raise VeleroError("Not all pods are available")

        logger.info("Checking the Velero NodeAgent readiness")
        k8s_retry_check(check_node_agent)

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
            VeleroError: If the storage locations are not found.
            APIError: If the storage locations are not found.
        """

        def check_backup_location() -> None:
            backup_loc: Dict[str, Any] = kube_client.get(
                VELERO_BACKUP_LOCATION_RESOURCE, name=backup_loc_name, namespace=namespace
            )
            status: Dict[str, Any] = backup_loc.get("status", {})

            if not status and not isinstance(status, dict):
                raise VeleroError("BackupStorageLocation has no status")

            if status.get("phase") != "Available":
                raise VeleroError(
                    "BackupStorageLocation is unavailable, check the storage configuration"
                )

        logger.info("Checking the Velero Storage locations readiness")
        k8s_retry_check(check_backup_location)

        # Will throw if the location does not exist
        kube_client.get(
            VELERO_VOLUME_SNAPSHOT_LOCATION_RESOURCE, volume_loc_name, namespace=namespace
        )
