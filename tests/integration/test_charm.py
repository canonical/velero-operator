#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
from pathlib import Path

import pytest
import yaml
from juju.model import Model
from lightkube import Client
from lightkube.core.exceptions import ApiError
from pytest_operator.plugin import OpsTest

from velero import Velero

logger = logging.getLogger(__name__)

USE_NODE_AGENT_CONFIG_KEY = "use-node-agent"
METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]

UNTRUST_ERROR_MESSAGE = (
    "The charm must be deployed with '--trust' flag enabled, run 'juju trust ...'"
)
READY_MESSAGE = "Unit is Ready"


@pytest.fixture(scope="session")
def lightkube_client() -> Client:
    """Return a lightkube client to use in this session."""
    client = Client(field_manager=APP_NAME)
    return client


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
async def test_build_and_deploy_without_trust(ops_test: OpsTest):
    """Build the charm-under-test and deploy it together with related charms.

    Assert on the unit status being blocked due to lack of trust.
    """
    charm = await ops_test.build_charm(".")

    model = get_model(ops_test)
    await asyncio.gather(
        model.deploy(
            charm, application_name=APP_NAME, trust=False, config={"use-node-agent": True}
        ),
        model.wait_for_idle(apps=[APP_NAME], status="blocked", timeout=60 * 20),
    )

    for unit in model.applications[APP_NAME].units:
        assert unit.workload_status_message == UNTRUST_ERROR_MESSAGE


@pytest.mark.abort_on_fail
async def test_trust_blocked_deployment(ops_test: OpsTest):
    """Trust existing blocked deployment.

    Assert on the application status recovering to active.
    """
    await ops_test.juju("trust", APP_NAME, "--scope=cluster")
    model = get_model(ops_test)

    await model.wait_for_idle(apps=[APP_NAME], status="active", timeout=60 * 20)

    for unit in model.applications[APP_NAME].units:
        assert unit.workload_status_message == READY_MESSAGE


@pytest.mark.abort_on_fail
async def test_remove(ops_test: OpsTest, lightkube_client):
    """Remove the application and assert that all resources are deleted."""
    model = get_model(ops_test)

    await asyncio.gather(
        model.remove_application(APP_NAME),
        model.block_until(
            lambda: model.applications[APP_NAME].status == "unknown",
            timeout=60 * 2,
        ),
    )

    for resource in Velero("", model.name)._all_resources:
        try:
            lightkube_client.get(resource.type, resource.name)
            assert False, f"Resource {resource.name} was not deleted"
        except ApiError as ae:
            assert ae.response.status_code == 404
