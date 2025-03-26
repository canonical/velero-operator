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

from config import USE_NODE_AGENT_CONFIG_KEY, VELERO_SERVER_RESOURCES

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]


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
async def test_build_and_deploy(ops_test: OpsTest):
    """Build the charm-under-test and deploy it together with related charms.

    Assert on the unit status before any relations/configurations take place.
    """
    # Build and deploy charm from local source folder
    charm = await ops_test.build_charm(".")

    # Deploy the charm and wait for blocked/idle status
    model = get_model(ops_test)
    await asyncio.gather(
        model.deploy(
            charm, application_name=APP_NAME, trust=True, config={USE_NODE_AGENT_CONFIG_KEY: True}
        ),
        model.wait_for_idle(apps=[APP_NAME], status="blocked", timeout=60 * 20),
    )

    for unit in model.applications[APP_NAME].units:
        assert unit.workload_status_message == "Missing relation: [s3|azure]"


async def test_remove(ops_test: OpsTest, lightkube_client):
    """Remove the charm-under-test and assert on the unit status."""
    model = get_model(ops_test)

    await asyncio.gather(
        model.remove_application(APP_NAME),
        model.block_until(
            lambda: model.applications[APP_NAME].status == "unknown",
            timeout=60 * 2,
        ),
    )

    for resource in VELERO_SERVER_RESOURCES:
        try:
            lightkube_client.get(resource.type, resource.name)
            assert False, f"Resource {resource.name} was not deleted"
        except ApiError as ae:
            assert ae.response.status_code == 404
