#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging

import pytest
from helpers import (
    APP_NAME,
    AZURE_INTEGRATOR,
    AZURE_INTEGRATOR_CHANNEL,
    DEPLOYMENT_IMAGE_ERROR_MESSAGE_1,
    DEPLOYMENT_IMAGE_ERROR_MESSAGE_2,
    MISSING_RELATION_MESSAGE,
    MULTIPLE_RELATIONS_MESSAGE,
    S3_INTEGRATOR,
    S3_INTEGRATOR_CHANNEL,
    TIMEOUT,
    UNTRUST_ERROR_MESSAGE,
    assert_app_status,
    get_model,
    k8s_assert_resource_exists,
    k8s_assert_resource_not_exists,
    k8s_get_velero_deployment_container_args,
)
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
            charm,
            application_name=APP_NAME,
            trust=False,
            config={"use-node-agent": True, "default-volumes-to-fs-backup": True},
        ),
        model.deploy(S3_INTEGRATOR, channel=S3_INTEGRATOR_CHANNEL),
        model.deploy(AZURE_INTEGRATOR, channel=AZURE_INTEGRATOR_CHANNEL),
        model.wait_for_idle(
            apps=[APP_NAME, S3_INTEGRATOR, AZURE_INTEGRATOR], status="blocked", timeout=TIMEOUT
        ),
    )
    assert_app_status(model.applications[APP_NAME], [UNTRUST_ERROR_MESSAGE])


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
            timeout=TIMEOUT,
        )
    assert_app_status(model.applications[APP_NAME], [MISSING_RELATION_MESSAGE])


async def test_multiple_integrator_relations(ops_test: OpsTest):
    """Relate the S3 and Azure integrator charms to the velero-operator charm."""
    model = get_model(ops_test)

    logger.info("Relating velero-operator to s3-integrator and azure-integrator")
    await model.integrate(APP_NAME, S3_INTEGRATOR)
    await model.integrate(APP_NAME, AZURE_INTEGRATOR)
    await model.wait_for_idle(
        apps=[APP_NAME],
        status="blocked",
        timeout=TIMEOUT,
    )
    assert_app_status(model.applications[APP_NAME], [MULTIPLE_RELATIONS_MESSAGE])

    logger.info("Unrelating velero-operator from s3-integrator and azure-integrator")
    await ops_test.juju(*["remove-relation", APP_NAME, AZURE_INTEGRATOR])
    await ops_test.juju(*["remove-relation", APP_NAME, S3_INTEGRATOR])
    await model.wait_for_idle(
        apps=[APP_NAME],
        status="blocked",
        timeout=TIMEOUT,
    )
    assert_app_status(model.applications[APP_NAME], [MISSING_RELATION_MESSAGE])


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
    assert_app_status(app, [MISSING_RELATION_MESSAGE])
    k8s_assert_resource_not_exists(
        lightkube_client, DaemonSet, name=VELERO_NODE_AGENT_NAME, namespace=model.name
    )

    logger.info("Setting use-node-agent to true")
    await asyncio.gather(
        app.set_config({USE_NODE_AGENT_CONFIG_KEY: "true"}),
        model.wait_for_idle(apps=[APP_NAME], timeout=TIMEOUT, status="blocked"),
    )
    assert_app_status(app, [MISSING_RELATION_MESSAGE])
    k8s_assert_resource_exists(
        lightkube_client, DaemonSet, name=VELERO_NODE_AGENT_NAME, namespace=model.name
    )


@pytest.mark.abort_on_fail
async def test_config_default_volumes_to_fs_backup(ops_test: OpsTest, lightkube_client):
    """Test the config-changed hook for the default-volumes-to-fs-backup config option."""
    logger.info("Testing default-volumes-to-fs-backup config option")
    model = get_model(ops_test)
    app = model.applications[APP_NAME]

    logger.info("Setting default-volumes-to-fs-backup to false")
    await asyncio.gather(
        app.set_config({"default-volumes-to-fs-backup": "false"}),
        model.wait_for_idle(apps=[APP_NAME], timeout=TIMEOUT, status="blocked"),
    )
    assert_app_status(app, [MISSING_RELATION_MESSAGE])
    args = k8s_get_velero_deployment_container_args(lightkube_client, model.name)
    assert "--default-volumes-to-fs-backup=false" in args

    logger.info("Setting default-volumes-to-fs-backup to true")
    await asyncio.gather(
        app.set_config({"default-volumes-to-fs-backup": "true"}),
        model.wait_for_idle(apps=[APP_NAME], timeout=TIMEOUT, status="blocked"),
    )
    assert_app_status(app, [MISSING_RELATION_MESSAGE])
    args = k8s_get_velero_deployment_container_args(lightkube_client, model.name)
    assert "--default-volumes-to-fs-backup=true" in args


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
    assert_app_status(app, [DEPLOYMENT_IMAGE_ERROR_MESSAGE_1, DEPLOYMENT_IMAGE_ERROR_MESSAGE_2])

    logger.info("Resetting velero-image config to default")
    await app.reset_config([VELERO_IMAGE_CONFIG_KEY])
    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(apps=[APP_NAME], timeout=TIMEOUT, status="blocked")
    assert_app_status(app, [MISSING_RELATION_MESSAGE])


@pytest.mark.abort_on_fail
async def test_remove(ops_test: OpsTest, lightkube_client):
    """Remove the applications and assert that all resources are deleted."""
    logger.info("Removing velero-operator charm and checking resources")
    model = get_model(ops_test)

    await asyncio.gather(
        model.remove_application(APP_NAME, block_until_done=True),
        model.remove_application(S3_INTEGRATOR, block_until_done=True),
        model.remove_application(AZURE_INTEGRATOR, block_until_done=True),
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
