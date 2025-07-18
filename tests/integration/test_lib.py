#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import json
import logging

import pytest
from helpers import (
    APP_NAME,
    APP_RELATION_NAME,
    TEST_APP_FIRST_RELATION_NAME,
    TEST_APP_NAME,
    TEST_APP_SECOND_RELATION_NAME,
    TIMEOUT,
    get_application_data,
    get_model,
    get_relation_data,
    is_relation_broken,
    is_relation_joined,
    run_charm_action,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build and deploy the velero-operator and test charm."""
    logger.info("Building and deploying velero-operator charm and test charm")
    velero_charm = await ops_test.build_charm(".")
    test_charm = await ops_test.build_charm("tests/integration/test_charm")
    model = get_model(ops_test)

    await asyncio.gather(
        model.deploy(
            velero_charm,
            application_name=APP_NAME,
            trust=True,
        ),
        model.deploy(
            test_charm,
            application_name=TEST_APP_NAME,
        ),
        model.wait_for_idle(apps=[APP_NAME], status="blocked", timeout=TIMEOUT),
        model.wait_for_idle(apps=[TEST_APP_NAME], status="waiting", timeout=TIMEOUT),
    )


@pytest.mark.abort_on_fail
async def test_relate(ops_test: OpsTest):
    """Relate charms and wait for the expected changes in status."""
    logger.info("Relating velero-operator to %s", TEST_APP_NAME)
    model = get_model(ops_test)

    async def integrate_and_check(endpoint_name: str, expected_spec: dict):
        logger.info(
            "Integrating %s with %s using %s endpoint",
            APP_NAME,
            TEST_APP_NAME,
            endpoint_name,
        )
        await model.integrate(
            f"{APP_NAME}:{APP_RELATION_NAME}",
            f"{TEST_APP_NAME}:{endpoint_name}",
        )
        async with ops_test.fast_forward(fast_interval="30s"):
            await model.block_until(lambda: is_relation_joined(model, endpoint_name))
            await model.wait_for_idle(
                apps=[TEST_APP_NAME],
                status="active",
                timeout=TIMEOUT,
            )

        logger.info("Checking the content of the relation data for %s", endpoint_name)
        relation_data = await get_relation_data(
            ops_test, APP_NAME, APP_RELATION_NAME, endpoint_name
        )
        application_data = await get_application_data(
            ops_test, APP_NAME, APP_RELATION_NAME, endpoint_name
        )
        logger.info(relation_data)
        logger.info(application_data)
        assert "app" in application_data
        assert "relation_name" in application_data
        assert "spec" in application_data
        assert application_data["app"] == TEST_APP_NAME
        assert application_data["relation_name"] == endpoint_name
        spec = json.loads(application_data["spec"])
        for key, value in expected_spec.items():
            assert spec.get(key) == value

    await integrate_and_check(
        TEST_APP_FIRST_RELATION_NAME,
        {
            "include_namespaces": ["user-namespace", "other-namespace"],
            "include_resources": ["deployments", "services"],
            "label_selector": {"app": "test"},
            "ttl": "24h5m5s",
            "exclude_namespaces": None,
            "exclude_resources": None,
            "include_cluster_resources": False,
        },
    )

    await integrate_and_check(
        TEST_APP_SECOND_RELATION_NAME,
        {
            "include_namespaces": None,
            "include_resources": None,
            "label_selector": {"tier": "test"},
            "ttl": "12h30m",
            "exclude_namespaces": ["excluded-namespace"],
            "exclude_resources": ["pods"],
            "include_cluster_resources": True,
        },
    )


@pytest.mark.abort_on_fail
async def test_lib_getters(ops_test: OpsTest):
    """Test the getters for the VeleroBackupRequirer."""
    logger.info("Testing VeleroBackupProvider getters")
    model = get_model(ops_test)
    unit = model.applications[APP_NAME].units[0]

    logger.info("Running the create-backup action with non-existent target")
    try:
        await run_charm_action(
            unit,
            "create-backup",
            target="app:endpoint",
        )
        assert False, "Expected an error when running create-backup with non-existent target"
    except AssertionError:
        pass

    logger.info("Running the create-backup action with the correct targets")
    await run_charm_action(
        unit,
        "create-backup",
        target=f"{TEST_APP_NAME}:{TEST_APP_FIRST_RELATION_NAME}",
    )
    await run_charm_action(
        unit,
        "create-backup",
        target=f"{TEST_APP_NAME}:{TEST_APP_SECOND_RELATION_NAME}",
    )


@pytest.mark.abort_on_fail
async def test_unrelate(ops_test: OpsTest):
    """Unrelate charms and check the status."""
    logger.info("Unrelating velero-operator from %s", TEST_APP_NAME)
    model = get_model(ops_test)

    await ops_test.juju(
        *["remove-relation", APP_NAME, f"{TEST_APP_NAME}:{TEST_APP_FIRST_RELATION_NAME}"]
    )
    await ops_test.juju(
        *["remove-relation", APP_NAME, f"{TEST_APP_NAME}:{TEST_APP_SECOND_RELATION_NAME}"]
    )

    async with ops_test.fast_forward(fast_interval="30s"):
        await model.block_until(lambda: is_relation_broken(model, TEST_APP_FIRST_RELATION_NAME))
        await model.block_until(lambda: is_relation_broken(model, TEST_APP_SECOND_RELATION_NAME))
        await model.wait_for_idle(
            apps=[TEST_APP_NAME],
            status="waiting",
            raise_on_blocked=False,
            timeout=TIMEOUT,
        )


@pytest.mark.abort_on_fail
async def test_remove(ops_test: OpsTest):
    """Remove the velero-operator and s3-integrator charms."""
    logger.info("Removing velero-operator and s3-integrator charms")
    model = get_model(ops_test)

    await asyncio.gather(
        model.remove_application(APP_NAME, block_until_done=True),
        model.remove_application(TEST_APP_NAME, block_until_done=True),
    )
