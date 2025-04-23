#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
from pathlib import Path

import pytest
import yaml
from httpx import HTTPStatusError
from juju.model import Model
from lightkube import Client
from lightkube.core.exceptions import ApiError
from lightkube.resources.apiextensions_v1 import CustomResourceDefinition
from lightkube.resources.apps_v1 import DaemonSet
from pytest_operator.plugin import OpsTest

from constants import VELERO_NODE_AGENT_NAME
from velero import Velero

logger = logging.getLogger(__name__)

TIMEOUT = 60 * 10
USE_NODE_AGENT_CONFIG_KEY = "use-node-agent"
VELERO_AWS_PLUGIN_IMAGE_KEY = "velero-aws-plugin-image"
VELERO_IMAGE_CONFIG_KEY = "velero-image"
METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]
DEFAULT_VELERO_IMAGE = METADATA["config"]["options"][VELERO_IMAGE_CONFIG_KEY]["default"]

S3_INTEGRATOR = "s3-integrator"
S3_INTEGRATOR_CHANNEL = "latest/stable"

UNTRUST_ERROR_MESSAGE = (
    "The charm must be deployed with '--trust' flag enabled, run 'juju trust ...'"
)
READY_MESSAGE = "Unit is Ready"
MISSING_RELATION_MESSAGE = "Missing relation: [s3-credentials]"
DEPLOYMENT_IS_NOT_READY_MESSAGE = "Velero Deployment is not ready: "


@pytest.fixture(scope="session")
def lightkube_client() -> Client:
    """Return a lightkube client to use in this session."""
    client = Client(field_manager=APP_NAME)
    return client


def get_velero(model: str) -> Velero:
    """Return a Velero instance for the given model."""
    return Velero("./velero", model)


def get_model(ops_test: OpsTest) -> Model:
    """Return the Juju model of the current test.

    Returns:
        A juju.model.Model instance of the current model.

    Raises:
        AssertionError if the test doesn't have a Juju model.
    """
    model = ops_test.model
    if model is None:
        raise AssertionError("ops_test has a None model.")
    return model


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, s3_connection_info):
    """Build the velero-operator and deploy it with the integrator charms."""
    logger.info("Building and deploying velero-operator charm with s3-integrator")

    charm = await ops_test.build_charm(".")
    model = get_model(ops_test)

    await asyncio.gather(
        model.deploy(
            charm, application_name=APP_NAME, trust=False, config={"use-node-agent": True}
        ),
        model.deploy(S3_INTEGRATOR, channel=S3_INTEGRATOR_CHANNEL),
        model.wait_for_idle(apps=[APP_NAME, S3_INTEGRATOR], status="blocked", timeout=TIMEOUT),
    )

    for unit in model.applications[APP_NAME].units:
        assert unit.workload_status_message == UNTRUST_ERROR_MESSAGE


@pytest.mark.abort_on_fail
async def test_configure_s3_integrator(
    ops_test: OpsTest,
    s3_cloud_credentials,
    s3_cloud_configs,
):
    """Configure the integrator charm with the credentials and configs."""
    model = get_model(ops_test)

    logger.info("Setting credentials for %s", S3_INTEGRATOR)
    await model.applications[S3_INTEGRATOR].set_config(s3_cloud_configs)
    action = await model.units[f"{S3_INTEGRATOR}/0"].run_action(
        "sync-s3-credentials", **s3_cloud_credentials
    )
    result = await action.wait()
    assert result.results.get("return-code") == 0

    logger.info("Waiting for %s to be active", S3_INTEGRATOR)
    await model.wait_for_idle(
        apps=[S3_INTEGRATOR],
        status="active",
        timeout=TIMEOUT,
    )


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

    await asyncio.gather(
        app.set_config({USE_NODE_AGENT_CONFIG_KEY: "false"}),
        model.wait_for_idle(apps=[APP_NAME], timeout=TIMEOUT),
    )

    try:
        lightkube_client.get(DaemonSet, name=VELERO_NODE_AGENT_NAME, namespace=model.name)
        assert False, "DaemonSet was not deleted"
    except ApiError as ae:
        if ae.response.status_code != 404:
            raise ae

    await asyncio.gather(
        app.set_config({USE_NODE_AGENT_CONFIG_KEY: "true"}),
        model.wait_for_idle(apps=[APP_NAME], timeout=TIMEOUT),
    )

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

    await app.set_config({VELERO_IMAGE_CONFIG_KEY: new_image})
    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(apps=[APP_NAME], timeout=TIMEOUT, status="blocked")

    for unit in model.applications[APP_NAME].units:
        assert DEPLOYMENT_IS_NOT_READY_MESSAGE in unit.workload_status_message and (
            "ImagePullBackOff" in unit.workload_status_message
            or "ErrImagePull" in unit.workload_status_message
        )

    await app.reset_config([VELERO_IMAGE_CONFIG_KEY])
    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(apps=[APP_NAME], timeout=TIMEOUT, status="blocked")

    for unit in model.applications[APP_NAME].units:
        assert unit.workload_status_message == MISSING_RELATION_MESSAGE


@pytest.mark.abort_on_fail
@pytest.mark.parametrize(
    "integrator,plugin_image_key",
    [(S3_INTEGRATOR, VELERO_AWS_PLUGIN_IMAGE_KEY)],
)
async def test_integrator_relation(ops_test: OpsTest, integrator: str, plugin_image_key: str):
    """Test the relation between the velero-operator charm and the integrator charm."""
    model = get_model(ops_test)
    app = model.applications[APP_NAME]
    new_plugin_image = "velero-test-plugin-image"

    logger.info("Relating velero-operator to %s", integrator)
    await model.integrate(APP_NAME, integrator)
    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(
            apps=[APP_NAME],
            status="active",
            timeout=TIMEOUT,
        )
    for unit in model.applications[APP_NAME].units:
        assert unit.workload_status_message == READY_MESSAGE

    await app.set_config({plugin_image_key: new_plugin_image})
    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(apps=[APP_NAME], timeout=TIMEOUT, status="blocked")

    await app.reset_config([plugin_image_key])
    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(apps=[APP_NAME], timeout=TIMEOUT, status="active")

    logger.info("Unrelating velero-operator from %s", integrator)
    await ops_test.juju(*["remove-relation", APP_NAME, integrator])
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
async def test_remove(ops_test: OpsTest, lightkube_client):
    """Remove the applications and assert that all resources are deleted."""
    model = get_model(ops_test)
    velero = get_velero(model.name)

    await asyncio.gather(
        model.remove_application(S3_INTEGRATOR),
        model.remove_application(APP_NAME),
        model.block_until(
            lambda: model.applications[APP_NAME].status == "unknown",
            timeout=TIMEOUT,
        ),
    )

    for resource in velero._core_resources + velero._storage_provider_resources:
        try:
            lightkube_client.get(resource.type, resource.name)
            assert False, f"Resource {resource.name} was not deleted"
        except (ApiError, HTTPStatusError) as ae:
            assert ae.response.status_code == 404

    result = list(
        lightkube_client.list(
            CustomResourceDefinition, labels={"component": "velero"}, namespace=model.name
        )
    )
    assert not result, "CustomResourceDefinitions were not deleted"
