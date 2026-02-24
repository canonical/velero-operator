#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging

import pytest
from helpers import (
    APP_NAME,
    APP_RELATION_NAME,
    S3_INTEGRATOR,
    S3_INTEGRATOR_CHANNEL,
    TEST_APP_FIRST_RELATION_NAME,
    TEST_APP_NAME,
    TIMEOUT,
    get_model,
    is_relation_joined,
    k8s_list_velero_schedules,
)
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_build_and_deploy(
    ops_test: OpsTest,
    s3_connection_info,
    velero_operator_charm_path,
    test_charm_path,
):
    """Build and deploy the velero-operator and test charm."""
    logger.info("Building and deploying velero-operator charm and test charm")
    model = get_model(ops_test)

    await asyncio.gather(
        model.deploy(
            velero_operator_charm_path,
            application_name=APP_NAME,
            trust=True,
            config={"use-node-agent": True, "default-volumes-to-fs-backup": True},
        ),
        model.deploy(
            test_charm_path,
            application_name=TEST_APP_NAME,
            config={"schedule": "*/5 * * * *"},  # Every 5 minutes
        ),
        model.deploy(S3_INTEGRATOR, channel=S3_INTEGRATOR_CHANNEL),
        model.wait_for_idle(apps=[APP_NAME], status="blocked", timeout=TIMEOUT),
        model.wait_for_idle(apps=[TEST_APP_NAME], status="waiting", timeout=TIMEOUT),
    )


@pytest.mark.abort_on_fail
async def test_configure_s3_integrator(
    ops_test: OpsTest,
    s3_cloud_credentials,
    s3_cloud_configs,
):
    """Configure the integrator charm with the credentials and configs."""
    logger.info("Setting credentials for %s", S3_INTEGRATOR)
    model = get_model(ops_test)
    app = model.applications[S3_INTEGRATOR]

    await app.set_config(s3_cloud_configs)
    action = await app.units[0].run_action("sync-s3-credentials", **s3_cloud_credentials)
    result = await action.wait()
    assert result.results.get("return-code") == 0

    await model.wait_for_idle(
        apps=[S3_INTEGRATOR],
        status="active",
        timeout=TIMEOUT,
    )


@pytest.mark.abort_on_fail
async def test_relate_with_schedule(ops_test: OpsTest, lightkube_client):
    """Relate charms and verify schedule CR is created."""
    logger.info("Relating velero-operator to %s with schedule config", TEST_APP_NAME)
    model = get_model(ops_test)

    # Integrate with S3 storage
    await model.integrate(APP_NAME, S3_INTEGRATOR)

    # Integrate velero-operator with test charm
    await model.integrate(
        f"{APP_NAME}:{APP_RELATION_NAME}",
        f"{TEST_APP_NAME}:{TEST_APP_FIRST_RELATION_NAME}",
    )

    async with ops_test.fast_forward(fast_interval="30s"):
        await model.block_until(lambda: is_relation_joined(model, TEST_APP_FIRST_RELATION_NAME))
        await model.wait_for_idle(
            apps=[APP_NAME, TEST_APP_NAME],
            status="active",
            timeout=TIMEOUT,
        )

    # Wait for schedule to be created
    logger.info("Waiting for schedule CR to be created")
    schedule_labels = {
        "app": TEST_APP_NAME,
        "endpoint": TEST_APP_FIRST_RELATION_NAME,
        "managed-by": "velero-operator",
    }

    schedule = None
    for attempt in Retrying(
        stop=stop_after_attempt(30),
        wait=wait_fixed(2),
        retry=retry_if_exception_type(AssertionError),
        reraise=True,
    ):
        with attempt:
            schedules = k8s_list_velero_schedules(
                lightkube_client, model.name, labels=schedule_labels
            )
            assert len(schedules) == 1, f"Expected 1 schedule, found {len(schedules)}"
            schedule = schedules[0]

    assert schedule is not None, "Schedule should have been found"
    assert schedule["spec"]["schedule"] == "*/5 * * * *", "Schedule cron expression mismatch"
    assert (
        schedule.get("status", {}).get("lastSkipped") is not None
    ), "lastSkipped should be set (Velero resets skipImmediately to false after processing)"
    logger.info("Schedule CR created: %s", schedule["metadata"]["name"])


@pytest.mark.abort_on_fail
async def test_update_schedule(ops_test: OpsTest, lightkube_client):
    """Test that schedule CR is updated when config changes."""
    logger.info("Testing schedule update on config change")
    model = get_model(ops_test)
    app = model.applications[TEST_APP_NAME]

    # Get original schedule name
    schedule_labels = {
        "app": TEST_APP_NAME,
        "endpoint": TEST_APP_FIRST_RELATION_NAME,
        "managed-by": "velero-operator",
    }
    original_schedules = k8s_list_velero_schedules(
        lightkube_client, model.name, labels=schedule_labels
    )
    assert len(original_schedules) == 1
    original_name = original_schedules[0]["metadata"]["name"]

    # Update schedule config
    await app.set_config({"schedule": "0 2 * * *"})  # Daily at 2am

    async with ops_test.fast_forward(fast_interval="30s"):
        await model.wait_for_idle(
            apps=[TEST_APP_NAME],
            status="active",
            timeout=TIMEOUT,
        )

    # Wait for schedule to be updated
    for attempt in Retrying(
        stop=stop_after_attempt(30),
        wait=wait_fixed(2),
        retry=retry_if_exception_type(AssertionError),
        reraise=True,
    ):
        with attempt:
            schedules = k8s_list_velero_schedules(
                lightkube_client, model.name, labels=schedule_labels
            )
            assert len(schedules) == 1, f"Expected 1 schedule, found {len(schedules)}"
            # Schedule should be updated (same name)
            assert schedules[0]["metadata"]["name"] == original_name, "Schedule name should remain"
            assert (
                schedules[0]["spec"]["schedule"] == "0 2 * * *"
            ), "Schedule cron should be updated"

    logger.info("Schedule CR updated successfully")


@pytest.mark.abort_on_fail
async def test_pause_schedule(ops_test: OpsTest, lightkube_client):
    """Test that schedule can be paused via config."""
    logger.info("Testing schedule pause")
    model = get_model(ops_test)
    app = model.applications[TEST_APP_NAME]

    # Pause the schedule
    await app.set_config({"paused": "true"})

    async with ops_test.fast_forward(fast_interval="30s"):
        await model.wait_for_idle(
            apps=[TEST_APP_NAME],
            status="active",
            timeout=TIMEOUT,
        )

    # Wait for schedule to be paused
    schedule_labels = {
        "app": TEST_APP_NAME,
        "endpoint": TEST_APP_FIRST_RELATION_NAME,
        "managed-by": "velero-operator",
    }

    for attempt in Retrying(
        stop=stop_after_attempt(30),
        wait=wait_fixed(2),
        retry=retry_if_exception_type(AssertionError),
        reraise=True,
    ):
        with attempt:
            schedules = k8s_list_velero_schedules(
                lightkube_client, model.name, labels=schedule_labels
            )
            assert len(schedules) == 1
            assert schedules[0]["spec"].get("paused") is True, "Schedule should be paused"

    logger.info("Schedule paused successfully")


@pytest.mark.abort_on_fail
async def test_resume_schedule(ops_test: OpsTest, lightkube_client):
    """Test that schedule can be resumed via config."""
    logger.info("Testing schedule resume")
    model = get_model(ops_test)
    app = model.applications[TEST_APP_NAME]

    # Resume the schedule
    await app.set_config({"paused": "false"})

    async with ops_test.fast_forward(fast_interval="30s"):
        await model.wait_for_idle(
            apps=[TEST_APP_NAME],
            status="active",
            timeout=TIMEOUT,
        )

    # Wait for schedule to be resumed
    schedule_labels = {
        "app": TEST_APP_NAME,
        "endpoint": TEST_APP_FIRST_RELATION_NAME,
        "managed-by": "velero-operator",
    }

    for attempt in Retrying(
        stop=stop_after_attempt(30),
        wait=wait_fixed(2),
        retry=retry_if_exception_type(AssertionError),
        reraise=True,
    ):
        with attempt:
            schedules = k8s_list_velero_schedules(
                lightkube_client, model.name, labels=schedule_labels
            )
            assert len(schedules) == 1
            # paused should be False or None (not set)
            paused = schedules[0]["spec"].get("paused")
            assert paused is False or paused is None, "Schedule should not be paused"

    logger.info("Schedule resumed successfully")


@pytest.mark.abort_on_fail
async def test_delete_schedule_on_config_change(ops_test: OpsTest, lightkube_client):
    """Test that schedule CR is deleted when schedule config is removed."""
    logger.info("Testing schedule deletion on config removal")
    model = get_model(ops_test)
    app = model.applications[TEST_APP_NAME]

    # Remove schedule config (empty string)
    await app.set_config({"schedule": ""})

    async with ops_test.fast_forward(fast_interval="30s"):
        await model.wait_for_idle(
            apps=[TEST_APP_NAME],
            status="active",
            timeout=TIMEOUT,
        )

    # Wait for schedule to be deleted
    schedule_labels = {
        "app": TEST_APP_NAME,
        "endpoint": TEST_APP_FIRST_RELATION_NAME,
        "managed-by": "velero-operator",
    }

    for attempt in Retrying(
        stop=stop_after_attempt(30),
        wait=wait_fixed(2),
        retry=retry_if_exception_type(AssertionError),
        reraise=True,
    ):
        with attempt:
            schedules = k8s_list_velero_schedules(
                lightkube_client, model.name, labels=schedule_labels
            )
            assert len(schedules) == 0, f"Expected 0 schedules, found {len(schedules)}"

    logger.info("Schedule CR deleted successfully")


@pytest.mark.abort_on_fail
async def test_schedule_cleanup_on_relation_broken(ops_test: OpsTest, lightkube_client):
    """Test that schedules are cleaned up when relation is broken."""
    logger.info("Testing schedule cleanup on relation broken")
    model = get_model(ops_test)

    # Re-enable schedule (was disabled in previous test)
    test_app = model.applications[TEST_APP_NAME]
    await test_app.set_config({"schedule": "0 2 * * *"})

    async with ops_test.fast_forward(fast_interval="30s"):
        await model.wait_for_idle(
            apps=[TEST_APP_NAME],
            status="active",
            timeout=TIMEOUT,
        )

    # Verify schedule is created
    logger.info("Verifying schedule was created")
    schedule_labels = {
        "app": TEST_APP_NAME,
        "endpoint": TEST_APP_FIRST_RELATION_NAME,
        "managed-by": "velero-operator",
    }

    for attempt in Retrying(
        stop=stop_after_attempt(30),
        wait=wait_fixed(2),
        retry=retry_if_exception_type(AssertionError),
        reraise=True,
    ):
        with attempt:
            schedules = k8s_list_velero_schedules(
                lightkube_client, model.name, labels=schedule_labels
            )
            assert len(schedules) == 1, f"Expected 1 schedule, found {len(schedules)}"

    logger.info(f"Schedule exists: {schedules[0]['metadata']['name']}")

    # Remove the relation
    logger.info(f"Removing relation with {TEST_APP_FIRST_RELATION_NAME}")
    await ops_test.juju(
        *["remove-relation", APP_NAME, f"{TEST_APP_NAME}:{TEST_APP_FIRST_RELATION_NAME}"]
    )

    async with ops_test.fast_forward(fast_interval="30s"):
        await model.wait_for_idle(
            apps=[APP_NAME, TEST_APP_NAME],
            timeout=TIMEOUT,
        )

    # Verify that the schedule is cleaned up
    for attempt in Retrying(
        stop=stop_after_attempt(30),
        wait=wait_fixed(2),
        retry=retry_if_exception_type(AssertionError),
        reraise=True,
    ):
        with attempt:
            schedules = k8s_list_velero_schedules(
                lightkube_client, model.name, labels=schedule_labels
            )
            assert (
                len(schedules) == 0
            ), f"Expected 0 schedules for broken relation, found {len(schedules)}"

    logger.info("Schedule cleanup on relation broken verified successfully")


@pytest.mark.abort_on_fail
async def test_unrelate(ops_test: OpsTest):
    """Unrelate charms and check the status."""
    logger.info("Unrelating velero-operator from S3")
    model = get_model(ops_test)

    # Remove S3 relation (first relation was already removed in previous test)
    await ops_test.juju(*["remove-relation", APP_NAME, S3_INTEGRATOR])

    async with ops_test.fast_forward(fast_interval="30s"):
        await model.wait_for_idle(
            apps=[APP_NAME],
            status="blocked",
            timeout=TIMEOUT,
        )


@pytest.mark.abort_on_fail
async def test_remove(ops_test: OpsTest):
    """Remove the velero-operator and s3-integrator charms."""
    logger.info("Removing velero-operator and s3-integrator charms")
    model = get_model(ops_test)

    await asyncio.gather(
        model.remove_application(APP_NAME, block_until_done=True),
        model.remove_application(S3_INTEGRATOR, block_until_done=True),
        model.remove_application(TEST_APP_NAME, block_until_done=True),
    )
