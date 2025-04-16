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
from pytest_operator.plugin import OpsTest

from velero import Velero

logger = logging.getLogger(__name__)

TIMEOUT = 60 * 10
USE_NODE_AGENT_CONFIG_KEY = "use-node-agent"
METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]

S3_INTEGRATOR = "s3-integrator"
S3_INTEGRATOR_CHANNEL = "latest/stable"

UNTRUST_ERROR_MESSAGE = (
    "The charm must be deployed with '--trust' flag enabled, run 'juju trust ...'"
)
READY_MESSAGE = "Unit is Ready"
MISSING_RELATION_MESSAGE = "Missing relation: [s3-credentials]"


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

    async with ops_test.fast_forward():
        await model.wait_for_idle(
            apps=[APP_NAME],
            status="blocked",
            raise_on_blocked=False,
            timeout=TIMEOUT,
            idle_period=30,
        )

    for unit in model.applications[APP_NAME].units:
        assert unit.workload_status_message == MISSING_RELATION_MESSAGE


@pytest.mark.abort_on_fail
@pytest.mark.parametrize(
    "integrator",
    [
        S3_INTEGRATOR,
    ],
)
async def test_integrator_relation(ops_test: OpsTest, integrator: str):
    """Test the relation between the velero-operator charm and the integrator charm."""
    model = get_model(ops_test)

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

    logger.info("Unrelating velero-operator from %s", integrator)
    await ops_test.juju(*["remove-relation", APP_NAME, integrator])
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
