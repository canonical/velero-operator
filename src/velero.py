# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Velero related code."""

import logging
import subprocess

from lightkube import Client
from lightkube.core.exceptions import ApiError
from lightkube.models.apps_v1 import DeploymentCondition
from lightkube.resources.apps_v1 import DaemonSet, Deployment
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
    VELERO_DEPLOYMENT_NAME,
    VELERO_NODE_AGENT_NAME,
)

logger = logging.getLogger(__name__)


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
            velero_image: The Velero image to use.
        """
        self._velero_binary_path = velero_binary_path
        self._namespace = namespace

    @property
    def _velero_install_flags(self) -> list:
        """Return the default Velero install flags."""
        return [
            f"--namespace={self._namespace}",
            "--no-default-backup-location",
            "--no-secret",
            "--use-volume-snapshots=false",
        ]

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
            subprocess.check_call(
                [
                    self._velero_binary_path,
                    "install",
                    f"--image={velero_image}",
                    *self._velero_install_flags,
                    f"--use-node-agent={use_node_agent}",
                ]
            )
        except subprocess.CalledProcessError as cpe:
            error_msg = f"'velero install' command returned non-zero exit code: {cpe.returncode}."
            logging.error(error_msg)
            logging.error("stdout: %s", cpe.stdout)
            logging.error("stderr: %s", cpe.stderr)

            raise VeleroError(error_msg) from cpe

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
                | retry_if_exception_type(VeleroError)
            ),
            reraise=True,
        ):
            with attempt:
                try:
                    deployment = kube_client.get(Deployment, name=name, namespace=namespace)
                except ApiError as ae:
                    raise VeleroError(str(ae)) from ae

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
                | retry_if_exception_type(VeleroError)
            ),
            reraise=True,
        ):
            with attempt:
                try:
                    daemonset = kube_client.get(DaemonSet, name=name, namespace=namespace)
                except ApiError as ae:
                    raise VeleroError(str(ae)) from ae
                status = daemonset.status

                if not status:
                    raise VeleroError("DaemonSet has no status")

                if status.numberAvailable != status.desiredNumberScheduled:
                    raise VeleroError("Not all pods are available")
                observations += 1
            if not attempt.retry_state.outcome.failed:  # type: ignore
                attempt.retry_state.set_result(observations)
