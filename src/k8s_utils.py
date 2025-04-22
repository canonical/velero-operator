# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Utility functions for Velero operations."""

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, Union

from lightkube import Client
from lightkube.core.exceptions import ApiError
from lightkube.core.resource import GlobalResource, NamespacedResource
from lightkube.models.core_v1 import ServicePort, ServiceSpec
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.core_v1 import Secret, Service
from tenacity import (
    Retrying,
    retry_if_exception_type,
    retry_if_result,
    stop_after_attempt,
    wait_fixed,
)

from constants import K8S_CHECK_ATTEMPTS, K8S_CHECK_DELAY, K8S_CHECK_OBSERVATIONS

logger = logging.getLogger(__name__)


@dataclass
class K8sResource:
    """Velero Kubernetes resource."""

    name: str
    type: Type[Union[NamespacedResource, GlobalResource]]


def k8s_resource_exists(kube_client: Client, resource: K8sResource, namespace: str) -> bool:
    """Check if a specified Kubernetes resource exists.

    Args:
        kube_client (Client): The Kubernetes client used to interact with the cluster.
        resource (VeleroResource): The resource to check.
        namespace (str): The namespace of the resource.

    Returns:
        bool: True if the resource exists, False otherwise.

    Raises:
        ValueError: If the resource type is neither a NamespacedResource nor a GlobalResource.
        APiError: If the resource cannot be retrieved.
    """
    try:
        if issubclass(resource.type, NamespacedResource):
            kube_client.get(resource.type, name=resource.name, namespace=namespace)
        elif issubclass(resource.type, GlobalResource):
            kube_client.get(resource.type, name=resource.name)
        else:  # pragma: no cover
            raise ValueError(f"Unknown resource type: {resource.type}")
    except ApiError as ae:
        if ae.status.code == 404:
            logger.warning("Resource %s '%s' not found", resource.type.__name__, resource.name)
            return False
        raise ae
    return True


def k8s_remove_resource(kube_client: Client, resource: K8sResource, namespace: str) -> None:
    """Remove a specified Kubernetes resource.

    Args:
        kube_client (Client): The Kubernetes client used to interact with the cluster.
        resource (VeleroResource): The resource to remove.
        namespace (str): The namespace of the resource.

    Raises:
        ValueError: If the resource type is neither a NamespacedResource nor a GlobalResource.
        ApiError: If the resource cannot be deleted.
    """
    try:
        if issubclass(resource.type, NamespacedResource):
            kube_client.delete(resource.type, name=resource.name, namespace=namespace)
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
            raise ae


def k8s_retry_check(
    check_func: Callable[[], None],
    *,
    retry_exceptions: Tuple[Type[BaseException], ...] = (),
    attempts: int = K8S_CHECK_ATTEMPTS,
    delay: float = K8S_CHECK_DELAY,
    min_successful: int = K8S_CHECK_OBSERVATIONS,
) -> None:
    """Retry a check function until it succeeds or the maximum number of attempts is reached.

    Args:
        check_func (Callable[[], None]): The function to check.
            Should raise an exception if the check fails.
        retry_exceptions (Tuple[Type[BaseException], ...]): Exceptions to retry on.
        attempts (int): Maximum number of attempts.
        delay (float): Delay between attempts in seconds.
        min_successful (int): Minimum number of successful observations before stopping retries.
    """
    observations = 0

    for attempt in Retrying(
        stop=stop_after_attempt(attempts),
        wait=wait_fixed(delay),
        retry=(
            retry_if_result(lambda obs: obs < min_successful)
            | retry_if_exception_type((ApiError,) + retry_exceptions)
        ),
        reraise=True,
    ):
        with attempt:
            check_func()
            observations += 1
        if not attempt.retry_state.outcome.failed:  # type: ignore
            attempt.retry_state.set_result(observations)


def k8s_create_secret(
    kube_client: Client,
    name: str,
    namespace: str,
    data: Dict[str, Any],
    labels: Optional[Dict[str, Any]] = None,
) -> None:
    """Create a Kubernetes secret.

    Args:
        kube_client (Client): The Kubernetes client used to interact with the cluster.
        name (str): The name of the secret.
        namespace (str): The namespace of the secret.
        data (Dict[str, Any]): The base64 encoded data for the secret.
        labels (Optional[Dict[str, Any]]): Optional labels for the secret.

    Raises:
        ApiError: If the secret cannot be created.
    """
    try:
        kube_client.create(
            Secret(
                apiVersion="v1",
                kind="Secret",
                metadata=ObjectMeta(name=name, namespace=namespace, labels=labels),
                type="Opaque",
                data=data,
            )
        )
    except ApiError as ae:
        logging.error("Failed to create secret '%s' in namespace '%s': %s", name, namespace, ae)
        raise ae


def k8s_create_cluster_ip_service(
    kube_client: Client,
    name: str,
    namespace: str,
    selector: Dict[str, str],
    ports: List[ServicePort],
    labels: Optional[Dict[str, Any]] = None,
) -> None:
    """Create a Kubernetes service.

    Args:
        kube_client (Client): The Kubernetes client used to interact with the cluster.
        name (str): The name of the service.
        namespace (str): The namespace of the service.
        ports (List[ServicePort]): The ports for the service.
        selector (Dict[str, str]): The selector for the service.
        labels (Optional[Dict[str, Any]]): Optional labels for the service.

    Raises:
        ApiError: If the service cannot be created.
    """
    try:
        kube_client.create(
            Service(
                apiVersion="v1",
                kind="Service",
                metadata=ObjectMeta(name=name, namespace=namespace, labels=labels),
                spec=ServiceSpec(type="ClusterIP", selector=selector, ports=ports),
            )
        )
    except ApiError as ae:
        logging.error("Failed to create service '%s' in namespace '%s': %s", name, namespace, ae)
        raise ae
