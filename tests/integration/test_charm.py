#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
from pathlib import Path

import pytest
import yaml
from juju.model import Model
from pytest_operator.plugin import OpsTest

from config import USE_NODE_AGENT_CONFIG_KEY

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]


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
        model.wait_for_idle(apps=[APP_NAME], status="active", timeout=60 * 20),
    )

    for unit in model.applications[APP_NAME].units:
        assert unit.workload_status_message == "Unit is Ready"
