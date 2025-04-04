# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Velero related code."""

import logging
import subprocess
from dataclasses import dataclass
from typing import List, Type, Union

from lightkube import Client, codecs
from lightkube.core.exceptions import ApiError
from lightkube.core.resource import GlobalResource, NamespacedResource
from lightkube.generic_resource import create_namespaced_resource
from lightkube.models.apps_v1 import DeploymentCondition
from lightkube.resources.apiextensions_v1 import CustomResourceDefinition
from lightkube.resources.apps_v1 import DaemonSet, Deployment
from lightkube.resources.core_v1 import Secret, ServiceAccount
from lightkube.resources.rbac_authorization_v1 import ClusterRoleBinding
from tenacity import (
    Retrying,
    retry_if_exception_type,
    retry_if_result,
    stop_after_attempt,
    wait_fixed,
)

from constants import (
    K8S_CHECK_ATTEMPTS,
    K8S_CHECK_DELAY,
    K8S_CHECK_OBSERVATIONS,
    VELERO_BACKUP_LOCATION_NAME,
    VELERO_CLUSTER_ROLE_BINDING_NAME,
    VELERO_DEPLOYMENT_NAME,
    VELERO_NODE_AGENT_NAME,
    VELERO_SECRET_NAME,
    VELERO_SERVICE_ACCOUNT_NAME,
    VELERO_VOLUME_SNAPSHOT_LOCATION_NAME,
)

logger = logging.getLogger(__name__)


@dataclass
class VeleroResource:
    """Velero Kubernetes resource."""

    name: str
    type: Type[Union[NamespacedResource, GlobalResource]]


@dataclass
class VeleroCRD:
    """Velero Custom Resource Definition."""

    name: str
    type: Type[CustomResourceDefinition]


class VeleroError(Exception):
    """Base class for Velero exceptions."""


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
    def _crds(self) -> List[VeleroCRD]:
        """Return the Velero CRDs by parsing the dry-run install YAML output."""
        try:
            output = subprocess.check_output(
                [self._velero_binary_path, "install", "--crds-only", "--dry-run", "-o", "yaml"],
                text=True,
            )
            resources = codecs.load_all_yaml(output)
        except Exception as err:
            raise VeleroError("Failed to load Velero CRDs from dry-run install.") from err

        return [
            VeleroCRD(name=crd.metadata.name, type=CustomResourceDefinition)
            for crd in reversed(resources)
            if isinstance(crd, CustomResourceDefinition) and crd.metadata and crd.metadata.name
        ]

    @property
    def _core_resources(self) -> List[VeleroResource]:
        """Return the core Velero resources."""
        return [
            VeleroResource(VELERO_DEPLOYMENT_NAME, Deployment),
            VeleroResource(VELERO_NODE_AGENT_NAME, DaemonSet),
            VeleroResource(VELERO_SECRET_NAME, Secret),
            VeleroResource(VELERO_SERVICE_ACCOUNT_NAME, ServiceAccount),
            VeleroResource(self._velero_crb_name, ClusterRoleBinding),
        ]

    @property
    def _storage_provider_resources(self) -> List[VeleroResource]:
        """Return all Velero resources."""
        return [
            VeleroResource(
                VELERO_BACKUP_LOCATION_NAME,
                create_namespaced_resource(
                    "velero.io", "v1", "BackupStorageLocation", "backupstoragelocations"
                ),
            ),
            VeleroResource(
                VELERO_VOLUME_SNAPSHOT_LOCATION_NAME,
                create_namespaced_resource(
                    "velero.io", "v1", "VolumeSnapshotLocation", "volumesnapshotlocations"
                ),
            ),
        ]

    @property
    def _all_resources(self) -> List[Union[VeleroResource, VeleroCRD]]:
        """Return all Velero resources."""
        return self._crds + self._core_resources + self._storage_provider_resources

    # METHODS

    def is_installed(self, kube_client: Client, use_node_agent: bool) -> bool:
        """Check if Velero is installed in the Kubernetes cluster.

        Args:
            kube_client: The lightkube client used to interact with the cluster.
            namespace: The namespace where Velero is installed.
            use_node_agent: Whether to use the Velero node agent (DaemonSet).

        Returns:
            bool: True if Velero is installed, False otherwise.
        """
        for resource in self._core_resources:
            if not use_node_agent and resource.type is DaemonSet:
                continue
            try:
                if issubclass(resource.type, NamespacedResource):
                    kube_client.get(resource.type, name=resource.name, namespace=self._namespace)
                elif issubclass(resource.type, GlobalResource):
                    kube_client.get(resource.type, name=resource.name)
                else:  # pragma: no cover
                    raise ValueError(f"Unknown resource type: {resource.type}")
            except ApiError:
                logger.warning("Resource %s '%s' not found", resource.type.__name__, resource.name)
                return False
        return True

    def install(self, velero_image: str, use_node_agent: bool) -> None:
        """Install Velero in the Kubernetes cluster.

        Args:
            velero_image: The Velero image to use.
            use_node_agent: Whether to use the Velero node agent (DaemonSet).
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
                if issubclass(resource.type, NamespacedResource):
                    kube_client.delete(
                        resource.type, name=resource.name, namespace=self._namespace
                    )
                elif issubclass(resource.type, GlobalResource):
                    kube_client.delete(resource.type, name=resource.name)
                else:  # pragma: no cover
                    raise ValueError(f"Unknown resource type: {resource.type}")
            except ApiError as ae:
                if ae.status.code == 404:
                    logging.warning(
                        "Resource %s '%s' not found, skipping deletion",
                        resource.type.__name__,
                        resource.name,
                    )
                else:
                    logging.error(
                        "Failed to delete %s '%s' resource: %s",
                        resource.type.__name__,
                        resource.name,
                        ae,
                    )

    # CHECKERS

    @staticmethod
    def get_deployment_availability(deployment: Deployment) -> DeploymentCondition:
        """Get the Availability Condition from Deployment lightkube object.

        Args:
            deployment (Deployment): The Deployment object to check.

        Raises:
            VeleroError: If Availability condition is not found.

        Returns:
            DeploymentCondition: The Availability condition.
        """
        if not deployment.status:
            raise VeleroError("Deployment has no status")

        if not deployment.status.conditions:
            raise VeleroError("Deployment has no conditions")

        for condition in deployment.status.conditions:
            if condition.type == "Available":
                return condition

        raise VeleroError("Deployment has no Available condition")

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
        """
        logger.info("Checking the Velero Deployment readiness")
        observations = 0

        for attempt in Retrying(
            stop=stop_after_attempt(K8S_CHECK_ATTEMPTS),
            wait=wait_fixed(K8S_CHECK_DELAY),
            retry=(
                retry_if_result(lambda obs: obs < K8S_CHECK_OBSERVATIONS)
                | retry_if_exception_type((VeleroError, ApiError))
            ),
            reraise=True,
        ):
            with attempt:
                deployment = kube_client.get(Deployment, name=name, namespace=namespace)
                availability = Velero.get_deployment_availability(deployment)

                if availability.status != "True":
                    raise VeleroError(availability.message)
                observations += 1
            if not attempt.retry_state.outcome.failed:  # type: ignore
                attempt.retry_state.set_result(observations)

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
        """
        observations = 0
        logger.info("Checking the Velero NodeAgent readiness")

        for attempt in Retrying(
            stop=stop_after_attempt(K8S_CHECK_ATTEMPTS),
            wait=wait_fixed(K8S_CHECK_DELAY),
            retry=(
                retry_if_result(lambda obs: obs < K8S_CHECK_OBSERVATIONS)
                | retry_if_exception_type((VeleroError, ApiError))
            ),
            reraise=True,
        ):
            with attempt:
                daemonset = kube_client.get(DaemonSet, name=name, namespace=namespace)
                status = daemonset.status

                if not status:
                    raise VeleroError("DaemonSet has no status")

                if status.numberAvailable != status.desiredNumberScheduled:
                    raise VeleroError("Not all pods are available")
                observations += 1
            if not attempt.retry_state.outcome.failed:  # type: ignore
                attempt.retry_state.set_result(observations)
