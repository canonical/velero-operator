"""Utility function used by the charm."""

import logging
import time
from dataclasses import dataclass
from typing import Optional

from lightkube import Client
from lightkube.core.exceptions import ApiError
from lightkube.resources.apps_v1 import DaemonSet, Deployment

from config import K8S_CHECK_ATTEMPTS, K8S_CHECK_DELAY, K8S_CHECK_OBSERVATIONS

logger = logging.getLogger(__name__)


class StatusError(Exception):
    """Base class for Status exceptions."""


@dataclass
class CheckResult:
    """Represents the outcome of the check call."""

    ok: bool = False
    reason: Optional[Exception] = None


def check_velero_deployment(kube_client: Client, name: str = "velero") -> CheckResult:
    """Check the readiness of the Velero deployment in the Kubernetes cluster.

    This function attempts to verify the availability status of the Velero deployment
    by querying the Kubernetes API server using the provided kube_client. It performs
    multiple attempts to check the deployment status and logs errors if the deployment
    is not ready.

    Args:
        kube_client (Client): The Kubernetes client used to interact with the cluster.
        name (str, optional): The name of the Velero deployment. Defaults to "velero".

    Returns:
        CheckResult: An object containing the result of the check, including any errors
        encountered during the process.
    """
    result = CheckResult()
    attempts = 0
    observations = 0

    logger.info("Checking the Velero Deployment readiness")

    while attempts < K8S_CHECK_ATTEMPTS:
        try:
            deployment = kube_client.get(Deployment, name=name)
            conditions = (
                deployment.status.conditions
                if deployment.status and deployment.status.conditions
                else []
            )

            availability = next((cond for cond in conditions if cond.type == "Available"), None)

            if availability:
                if availability.status == "True":
                    observations += 1
                    logger.info(
                        "The Velero Deployment is ready (observation: %d/%d)",
                        attempts,
                        K8S_CHECK_ATTEMPTS,
                    )
                    if observations > K8S_CHECK_OBSERVATIONS:
                        result.ok = True
                        return result
                else:
                    result.reason = StatusError(availability.reason)
                    logger.warning(
                        "The Velero Deployment is not ready: %s (attempt: %d/%d)",
                        result.reason,
                        attempts,
                        K8S_CHECK_ATTEMPTS,
                    )
            else:
                result.reason = StatusError("Availability status is not present")
                logger.warning(
                    "The Velero Deployment is not ready: %s (attempt: %d/%d)",
                    result.reason,
                    attempts,
                    K8S_CHECK_ATTEMPTS,
                )
        except ApiError as err:
            result.reason = err
            logger.error("Failed to confirm the Velero Deployment readiness: %s", err)
            return result

        attempts += 1
        time.sleep(K8S_CHECK_DELAY)

    return result


def check_velero_nodeagent(kube_client: Client, name: str = "velero") -> CheckResult:
    """Check the readiness of the Velero DaemonSet in a Kubernetes cluster.

    This function attempts to verify if the Velero DaemonSet is fully available
    by checking if the number of available pods matches the desired number of scheduled pods.
    It performs multiple attempts and observations to ensure the DaemonSet's readiness.

    Args:
        kube_client (Client): The Kubernetes client used to interact with the cluster.
        name (str, optional): The name of the Velero DaemonSet. Defaults to "velero".

    Returns:
        CheckResult: An object containing the result of the readiness check.
    """
    result = CheckResult()
    attempts = 0
    observations = 0

    logger.info("Checking the Velero NodeAgent readiness")

    while attempts < K8S_CHECK_ATTEMPTS:
        try:
            daemonset = kube_client.get(DaemonSet, name=name)
            status = daemonset.status

            if status:
                if status.numberAvailable == status.desiredNumberScheduled:
                    observations += 1
                    logger.info(
                        "The Velero DaemonSet is ready (observation: %d/%d)",
                        attempts,
                        K8S_CHECK_ATTEMPTS,
                    )
                    if observations > K8S_CHECK_OBSERVATIONS:
                        result.ok = True
                        return result
                else:
                    result.reason = StatusError("Not all pods are available")
                    logger.error(
                        "The Velero DaemonSet is not ready: %s (attempt: %d/%d)",
                        result.reason,
                        attempts,
                        K8S_CHECK_ATTEMPTS,
                    )
            else:
                result.reason = StatusError("Status is not present")
                logger.error(
                    "The Velero DaemonSet is not ready: %s (attempt: %d/%d)",
                    result.reason,
                    attempts,
                    K8S_CHECK_ATTEMPTS,
                )
        except ApiError as err:
            result.reason = err
            logger.error("Failed to confirm the Velero DaemonSet readiness: %s", err)
            return result

        attempts += 1
        time.sleep(K8S_CHECK_DELAY)

    return result
