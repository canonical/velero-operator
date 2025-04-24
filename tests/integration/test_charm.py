#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging

import pytest
from helpers import (
    APP_NAME,
    DEPLOYMENT_IMAGE_ERROR_MESSAGE_1,
    DEPLOYMENT_IMAGE_ERROR_MESSAGE_2,
    MISSING_RELATION_MESSAGE,
    TIMEOUT,
    UNTRUST_ERROR_MESSAGE,
    get_model,
)
from lightkube.core.exceptions import ApiError
from lightkube.resources.apiextensions_v1 import CustomResourceDefinition
from lightkube.resources.apps_v1 import DaemonSet, Deployment
from lightkube.resources.core_v1 import Secret, Service, ServiceAccount
from lightkube.resources.rbac_authorization_v1 import ClusterRoleBinding
from pytest_operator.plugin import OpsTest

USE_NODE_AGENT_CONFIG_KEY = "use-node-agent"
VELERO_IMAGE_CONFIG_KEY = "velero-image"
VELERO_NODE_AGENT_NAME = "node-agent"

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build and deploy the velero-operator."""
    logger.info("Building and deploying velero-operator charm")

    charm = await ops_test.build_charm(".")
    model = get_model(ops_test)

    await asyncio.gather(
        model.deploy(
            charm, application_name=APP_NAME, trust=False, config={"use-node-agent": True}
        ),
        model.wait_for_idle(apps=[APP_NAME], status="blocked", timeout=TIMEOUT),
    )

    for unit in model.applications[APP_NAME].units:
        assert unit.workload_status_message == UNTRUST_ERROR_MESSAGE


@pytest.mark.abort_on_fail
async def test_trust(ops_test: OpsTest):
    """Trust the velero-operator charm."""
    logger.info("Trusting velero-operator charm")

    model = get_model(ops_test)
    await ops_test.juju("trust", APP_NAME, "--scope=cluster")

    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(
            apps=[APP_NAME],
            status="blocked",
            raise_on_blocked=False,
            timeout=TIMEOUT,
        )

    for unit in model.applications[APP_NAME].units:
        assert unit.workload_status_message == MISSING_RELATION_MESSAGE


@pytest.mark.abort_on_fail
async def test_config_use_node_agent(ops_test: OpsTest, lightkube_client):
    """Test the config-changed hook for the use-node-agent config option."""
    logger.info("Testing use-node-agent config option")

    model = get_model(ops_test)
    app = model.applications[APP_NAME]

    logger.info("Setting use-node-agent to false")
    await asyncio.gather(
        app.set_config({USE_NODE_AGENT_CONFIG_KEY: "false"}),
        model.wait_for_idle(apps=[APP_NAME], timeout=TIMEOUT, status="blocked"),
    )

    for unit in model.applications[APP_NAME].units:
        assert unit.workload_status_message == MISSING_RELATION_MESSAGE

    try:
        lightkube_client.get(DaemonSet, name=VELERO_NODE_AGENT_NAME, namespace=model.name)
        assert False, "DaemonSet was not deleted"
    except ApiError as ae:
        if ae.response.status_code != 404:
            raise ae

    logger.info("Setting use-node-agent to true")
    await asyncio.gather(
        app.set_config({USE_NODE_AGENT_CONFIG_KEY: "true"}),
        model.wait_for_idle(apps=[APP_NAME], timeout=TIMEOUT, status="blocked"),
    )

    for unit in model.applications[APP_NAME].units:
        assert unit.workload_status_message == MISSING_RELATION_MESSAGE

    try:
        lightkube_client.get(DaemonSet, name=VELERO_NODE_AGENT_NAME, namespace=model.name)
    except ApiError as ae:
        if ae.response.status_code != 404:
            raise ae
        assert False, "DaemonSet was not created"


@pytest.mark.abort_on_fail
async def test_config_velero_image(ops_test: OpsTest):
    """Test the config-changed hook for the velero-image config option."""
    logger.info("Testing velero-image config option")

    model = get_model(ops_test)
    app = model.applications[APP_NAME]
    new_image = "velero-test"

    logger.info("Setting velero-image to %s", new_image)
    await app.set_config({VELERO_IMAGE_CONFIG_KEY: new_image})
    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(apps=[APP_NAME], timeout=TIMEOUT, status="blocked")

    for unit in model.applications[APP_NAME].units:
        assert (
            unit.workload_status_message == DEPLOYMENT_IMAGE_ERROR_MESSAGE_1
            or unit.workload_status_message == DEPLOYMENT_IMAGE_ERROR_MESSAGE_2
        )

    logger.info("Resetting velero-image config to default")
    await app.reset_config([VELERO_IMAGE_CONFIG_KEY])
    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(apps=[APP_NAME], timeout=TIMEOUT, status="blocked")

    for unit in model.applications[APP_NAME].units:
        assert unit.workload_status_message == MISSING_RELATION_MESSAGE


@pytest.mark.abort_on_fail
async def test_remove(ops_test: OpsTest, lightkube_client):
    """Remove the applications and assert that all resources are deleted."""
    model = get_model(ops_test)

    logger.info("Removing velero-operator charm")
    await asyncio.gather(
        model.remove_application(APP_NAME),
        model.block_until(
            lambda: model.applications[APP_NAME].status == "unknown",
            timeout=TIMEOUT,
        ),
    )

    logger.info("Checking that all resources are deleted")
    for resource in [
        Deployment,
        DaemonSet,
        ServiceAccount,
        Service,
        ClusterRoleBinding,
        Secret,
        CustomResourceDefinition,
    ]:
        res = list(
            lightkube_client.list(resource, labels={"component": "velero"}, namespace=model.name)
        )
        assert not res, "Velero {} still exists".format(resource.__name__)
