# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Velero related code."""

import logging
import subprocess
import time
from typing import Optional

from lightkube import Client
from lightkube.core.exceptions import ApiError
from lightkube.resources.apps_v1 import DaemonSet, Deployment

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
    def check_velero_deployment(
        kube_client: Client, namespace: str, name: str = VELERO_DEPLOYMENT_NAME
    ) -> None:
        """Check the readiness of the Velero deployment in the Kubernetes cluster.

        This function attempts to verify the availability status of the Velero deployment
        by querying the Kubernetes API server using the provided kube_client. It performs
        multiple attempts to check the deployment status and logs errors if the deployment
        is not ready.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.
            namespace (str): The namespace where the deployment is deployed.
            name (str, optional): The name of the Velero deployment. Defaults to "velero".

        Raises:
            VeleroError: If the Velero deployment is not ready.
        """
        attempts = 0
        observations = 0
        reason: Optional[str] = None

        logger.info("Checking the Velero Deployment readiness")

        while attempts < K8S_CHECK_ATTEMPTS:
            try:
                deployment = kube_client.get(Deployment, name=name, namespace=namespace)
                conditions = (
                    deployment.status.conditions
                    if deployment.status and deployment.status.conditions
                    else []
                )

                availability = next(
                    (cond for cond in conditions if cond.type == "Available"), None
                )

                if not availability:
                    logger.error(
                        "The Velero Deployment is not ready: Availability condition not found"
                    )
                    raise VeleroError("Availability condition not found")

                if availability.status == "True":
                    observations += 1
                    logger.info(
                        "The Velero Deployment is ready (observation: %d/%d)",
                        attempts,
                        K8S_CHECK_OBSERVATIONS,
                    )
                    if observations > K8S_CHECK_OBSERVATIONS:
                        return
                else:
                    reason = availability.message
                    logger.warning(
                        "The Velero Deployment is not ready: %s (attempt: %d/%d)",
                        reason,
                        attempts,
                        K8S_CHECK_ATTEMPTS,
                    )
            except ApiError as err:
                logger.error("Failed to confirm the Velero Deployment readiness: %s", err)
                raise VeleroError(str(err)) from err

            attempts += 1
            time.sleep(K8S_CHECK_DELAY)

        raise VeleroError(reason)

    @staticmethod
    def check_velero_node_agent(
        kube_client: Client, namespace: str, name: str = VELERO_NODE_AGENT_NAME
    ) -> None:
        """Check the readiness of the Velero DaemonSet in a Kubernetes cluster.

        This function attempts to verify if the Velero DaemonSet is fully available
        by checking if the number of available pods matches the desired number of scheduled pods.
        It performs multiple attempts and observations to ensure the DaemonSet's readiness.

        Args:
            kube_client (Client): The lightkube client used to interact with the cluster.
            namespace (str): The namespace where the DaemonSet is deployed.
            name (str, optional): The name of the Velero DaemonSet. Defaults to "velero".

        Raises:
            VeleroError: If the Velero DaemonSet is not
        """
        attempts = 0
        observations = 0
        reason: Optional[str] = None

        logger.info("Checking the Velero NodeAgent readiness")

        while attempts < K8S_CHECK_ATTEMPTS:
            try:
                daemonset = kube_client.get(DaemonSet, name=name, namespace=namespace)
                status = daemonset.status

                if not status:
                    logger.error(
                        "The Velero DaemonSet is not ready: Status not found in the DaemonSet"
                    )
                    raise VeleroError("Status not found in the DaemonSet")

                if status.numberAvailable == status.desiredNumberScheduled:
                    observations += 1
                    logger.info(
                        "The Velero DaemonSet is ready (observation: %d/%d)",
                        attempts,
                        K8S_CHECK_ATTEMPTS,
                    )
                    if observations > K8S_CHECK_OBSERVATIONS:
                        return
                else:
                    reason = "Not all pods are available"
                    logger.error(
                        "The Velero DaemonSet is not ready: %s (attempt: %d/%d)",
                        reason,
                        attempts,
                        K8S_CHECK_ATTEMPTS,
                    )

            except ApiError as err:
                logger.error("Failed to confirm the Velero DaemonSet readiness: %s", err)
                raise VeleroError(str(err)) from err

            attempts += 1
            time.sleep(K8S_CHECK_DELAY)

        raise VeleroError(reason)
