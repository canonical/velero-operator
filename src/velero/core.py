# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Velero Core library to interact with Velero CLI and Kubernetes resources."""

import logging
import subprocess
from typing import Any, Dict, List

from lightkube import Client, codecs
from lightkube.core.exceptions import ApiError, LoadResourceError
from lightkube.models.apps_v1 import DeploymentCondition
from lightkube.models.core_v1 import ContainerStatus, ServicePort
from lightkube.resources.apiextensions_v1 import CustomResourceDefinition
from lightkube.resources.apps_v1 import DaemonSet, Deployment
from lightkube.resources.core_v1 import Pod, Secret, Service, ServiceAccount
from lightkube.resources.rbac_authorization_v1 import ClusterRoleBinding
from lightkube.types import PatchType

from constants import (
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
    k8s_remove_resource,
    k8s_resource_exists,
    k8s_retry_check,
)

from .providers import VeleroStorageProvider

logger = logging.getLogger(__name__)


class VeleroError(Exception):
    """Base class for Velero exceptions."""


class VeleroStatusError(VeleroError):
    """Exception raised for Velero status errors."""


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
        """Return the Velero CRDs by parsing the dry-run install YAML output.

        Raises:
            VeleroCLIError: If the CRDs cannot be loaded from the dry-run install output.
        """
        try:
            output = subprocess.check_output(
                [self._velero_binary_path, "install", "--crds-only", "--dry-run", "-o", "yaml"],
                text=True,
            )
            resources = codecs.load_all_yaml(output)
        except (LoadResourceError, subprocess.CalledProcessError) as e:
            logger.error("Failed to load Velero CRDs from dry-run install: %s", e)
            raise VeleroCLIError("Failed to load Velero CRDs from dry-run install") from e

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

    def install(self, kube_client: Client, velero_image: str, use_node_agent: bool) -> None:
        """Install Velero in the Kubernetes cluster.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.
            velero_image: The Velero image to use.
            use_node_agent: Whether to use the Velero node agent (DaemonSet).

        Raises:
            VeleroCLIError: If the CLI installation fails.
            VeleroError: If metrics service creation fails.
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
        k8s_retry_check(check_deployment, retry_exceptions=(VeleroStatusError, ApiError))

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
        k8s_retry_check(check_node_agent, retry_exceptions=(VeleroStatusError, ApiError))

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
        k8s_retry_check(check_backup_location, retry_exceptions=(VeleroStatusError, ApiError))
        logger.info("Checking the Velero VolumeSnapshotLocation readiness")
        kube_client.get(
            VELERO_VOLUME_SNAPSHOT_LOCATION_RESOURCE, volume_loc_name, namespace=namespace
        )
