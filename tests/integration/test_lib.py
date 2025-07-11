#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import json
import logging

import pytest
from helpers import (
    APP_BACKUP_RELATION_NAME,
    APP_NAME,
    TEST_APP_BACKUP_RELATION_NAME,
    TEST_APP_NAME,
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

    await model.integrate(
        f"{APP_NAME}:{APP_BACKUP_RELATION_NAME}",
        f"{TEST_APP_NAME}:{TEST_APP_BACKUP_RELATION_NAME}",
    )
    async with ops_test.fast_forward(fast_interval="30s"):
        await model.block_until(lambda: is_relation_joined(model, TEST_APP_BACKUP_RELATION_NAME))
        await model.wait_for_idle(
            apps=[TEST_APP_NAME],
            status="active",
            timeout=TIMEOUT,
        )

    logger.info("Checking the content of the relation data")
    relation_data = await get_relation_data(
        ops_test, APP_NAME, APP_BACKUP_RELATION_NAME, TEST_APP_BACKUP_RELATION_NAME
    )
    application_data = await get_application_data(
        ops_test, APP_NAME, APP_BACKUP_RELATION_NAME, TEST_APP_BACKUP_RELATION_NAME
    )
    logger.info(relation_data)
    logger.info(application_data)

    assert "app" in application_data
    assert "relation_name" in application_data
    assert "spec" in application_data
    assert application_data["app"] == TEST_APP_NAME
    assert application_data["relation_name"] == TEST_APP_BACKUP_RELATION_NAME

    spec = json.loads(application_data["spec"])
    assert spec["include_namespaces"] == ["user-namespace", "other-namespace"]
    assert spec["include_resources"] == ["deployments", "services"]
    assert spec["label_selector"] == {"app": "test"}
    assert spec["ttl"] == "24h5m5s"
    assert spec["exclude_namespaces"] is None
    assert spec["exclude_resources"] is None
    assert spec["include_cluster_resources"] is False


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
            target=f"{APP_NAME}:{APP_BACKUP_RELATION_NAME}",
        )
        assert False, "Expected an error when running create-backup with non-existent target"
    except AssertionError:
        pass

    logger.info("Running the create-backup action with the correct target")
    await run_charm_action(
        unit,
        "create-backup",
        target=f"{TEST_APP_NAME}:{TEST_APP_BACKUP_RELATION_NAME}",
    )


@pytest.mark.abort_on_fail
async def test_unrelate(ops_test: OpsTest):
    """Unrelate charms and check the status."""
    logger.info("Unrelating velero-operator from %s", TEST_APP_NAME)
    model = get_model(ops_test)

    await ops_test.juju(*["remove-relation", APP_NAME, TEST_APP_NAME])

    async with ops_test.fast_forward(fast_interval="30s"):
        await model.block_until(lambda: is_relation_broken(model, TEST_APP_BACKUP_RELATION_NAME))
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
