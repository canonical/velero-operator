#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
import time
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

TIMEOUT = 60 * 5
USE_NODE_AGENT_CONFIG_KEY = "use-node-agent"
METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]

S3_INTEGRATOR = "s3-integrator"
S3_INTEGRATOR_CHANNEL = "latest/stable"
AZURE_INTEGRATOR = "azure-storage-integrator"
AZURE_INTEGRATOR_CHANNEL = "latest/edge"
AZURE_SECRET_NAME = f"azure-secret-{time.time()}"

UNTRUST_ERROR_MESSAGE = (
    "The charm must be deployed with '--trust' flag enabled, run 'juju trust ...'"
)
READY_MESSAGE = "Unit is Ready"
MISSING_RELATION_MESSAGE = "Missing relation: [s3-credentials|azure-credentials]"
MULTIPLE_RELATIONS_MESSAGE = (
    "Only one Storage Provider should be related at the time: [s3-credentials|azure-credentials]"
)


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
async def test_build_and_deploy(ops_test: OpsTest, s3_connection_info, azure_connection_info):
    """Build the velero-operator and deploy it with the integrator charms."""
    logger.info(
        "Building and deploying velero-operator charm with s3-integrator, azure-storage-integrator"
    )

    charm = await ops_test.build_charm(".")
    model = get_model(ops_test)

    await asyncio.gather(
        model.deploy(
            charm, application_name=APP_NAME, trust=False, config={"use-node-agent": True}
        ),
        model.deploy(S3_INTEGRATOR, channel=S3_INTEGRATOR_CHANNEL),
        model.deploy(AZURE_INTEGRATOR, channel=AZURE_INTEGRATOR_CHANNEL),
        model.wait_for_idle(
            apps=[APP_NAME, S3_INTEGRATOR, AZURE_INTEGRATOR], status="blocked", timeout=TIMEOUT
        ),
    )

    for unit in model.applications[APP_NAME].units:
        assert unit.workload_status_message == UNTRUST_ERROR_MESSAGE


@pytest.mark.abort_on_fail
async def test_configure_integrators(
    ops_test: OpsTest,
    s3_cloud_credentials,
    s3_cloud_configs,
    azure_cloud_credentials,
    azure_cloud_configs,
):
    """Configure the integrator charms with the credentials and configs."""
    model = get_model(ops_test)

    logger.info("Setting credentials for s3-integrator")
    await model.applications[S3_INTEGRATOR].set_config(s3_cloud_configs)
    action = await model.units[f"{S3_INTEGRATOR}/0"].run_action(
        "sync-s3-credentials", **s3_cloud_credentials
    )
    result = await action.wait()
    assert result.results.get("return-code") == 0

    logger.info("Setting credentials for azure-storage-integrator")
    await model.applications[AZURE_INTEGRATOR].set_config(azure_cloud_configs)
    _, stdout, _ = await ops_test.juju(
        *["add-secret", AZURE_SECRET_NAME, f"secret-key={azure_cloud_credentials['secret-key']}"]
    )
    await model.grant_secret(AZURE_SECRET_NAME, AZURE_INTEGRATOR)
    await model.applications[AZURE_INTEGRATOR].set_config({"credentials": stdout.strip()})

    logger.info("Waiting for integrators to be active")
    await model.wait_for_idle(
        apps=[S3_INTEGRATOR, AZURE_INTEGRATOR],
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
async def test_multiple_integrator_relations(ops_test: OpsTest):
    """Relate the S3 and Azure integrator charms to the velero-operator charm.

    The velero-operator charm should be in a blocked state after the relation is created,
    since both storage providers are related.
    """
    model = get_model(ops_test)

    logger.info("Relating velero-operator to s3-integrator and azure-storage-integrator")
    await model.integrate(APP_NAME, S3_INTEGRATOR)
    await model.integrate(APP_NAME, AZURE_INTEGRATOR)
    await model.wait_for_idle(
        apps=[APP_NAME],
        status="blocked",
        raise_on_blocked=False,
        timeout=TIMEOUT,
    )
    for unit in model.applications[APP_NAME].units:
        assert unit.workload_status_message == MULTIPLE_RELATIONS_MESSAGE

    logger.info("Unrelating velero-operator from s3-integrator and azure-storage-integrator")
    await ops_test.juju(*["remove-relation", APP_NAME, AZURE_INTEGRATOR])
    await ops_test.juju(*["remove-relation", APP_NAME, S3_INTEGRATOR])
    await model.wait_for_idle(
        apps=[APP_NAME],
        status="blocked",
        raise_on_blocked=False,
        timeout=TIMEOUT,
    )
    for unit in model.applications[APP_NAME].units:
        assert unit.workload_status_message == MISSING_RELATION_MESSAGE


@pytest.mark.abort_on_fail
@pytest.mark.parametrize(
    "integrator",
    [
        S3_INTEGRATOR,
        AZURE_INTEGRATOR,
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
    """Remove the application and assert that all resources are deleted."""
    model = get_model(ops_test)
    velero = get_velero(model.name)

    await asyncio.gather(
        model.remove_application(AZURE_INTEGRATOR),
        model.remove_application(S3_INTEGRATOR),
        model.remove_secret(AZURE_SECRET_NAME),
        model.remove_application(APP_NAME),
        model.block_until(
            lambda: model.applications[APP_NAME].status == "unknown",
            timeout=60 * 2,
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
