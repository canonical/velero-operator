#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
import time
import uuid

import pytest
from helpers import (
    APP_NAME,
    AZURE_INTEGRATOR,
    AZURE_INTEGRATOR_CHANNEL,
    DEPLOYMENT_IMAGE_ERROR_MESSAGE_1,
    DEPLOYMENT_IMAGE_ERROR_MESSAGE_2,
    MISSING_RELATION_MESSAGE,
    READY_MESSAGE,
    TIMEOUT,
    VELERO_AZURE_PLUGIN_IMAGE_KEY,
    assert_app_status,
    get_model,
    run_charm_action,
)
from lightkube import ApiError
from lightkube.resources.core_v1 import Namespace
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

AZURE_SECRET_NAME = f"azure-secret-{time.time()}"
BACKUP_NAME = f"test-backup-{uuid.uuid4()}"


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, azure_connection_info):
    """Build the velero-operator and deploy it with the integrator charms."""
    logger.info("Building and deploying velero-operator charm with azure-integrator")
    charm = await ops_test.build_charm(".")
    model = get_model(ops_test)

    await asyncio.gather(
        model.deploy(
            charm, application_name=APP_NAME, trust=True, config={"use-node-agent": True}
        ),
        model.deploy(AZURE_INTEGRATOR, channel=AZURE_INTEGRATOR_CHANNEL),
        model.wait_for_idle(apps=[APP_NAME, AZURE_INTEGRATOR], status="blocked", timeout=TIMEOUT),
    )
    assert_app_status(model.applications[APP_NAME], [MISSING_RELATION_MESSAGE])


@pytest.mark.abort_on_fail
async def test_configure_azure_integrator(
    ops_test: OpsTest,
    azure_cloud_credentials,
    azure_cloud_configs,
):
    """Configure the integrator charm with the credentials and configs."""
    logger.info("Setting credentials for %s", AZURE_INTEGRATOR)
    model = get_model(ops_test)
    app = model.applications[AZURE_INTEGRATOR]

    await app.set_config(azure_cloud_configs)
    _, stdout, _ = await ops_test.juju(
        *["add-secret", AZURE_SECRET_NAME, f"secret-key={azure_cloud_credentials['secret-key']}"]
    )
    await model.grant_secret(AZURE_SECRET_NAME, AZURE_INTEGRATOR)
    await app.set_config({"credentials": stdout.strip()})

    await model.wait_for_idle(
        apps=[AZURE_INTEGRATOR],
        status="active",
        timeout=TIMEOUT,
    )


@pytest.mark.abort_on_fail
async def test_relate_azure_integrator(ops_test: OpsTest):
    """Test the relation between the velero-operator charm and the s3-integrator charm."""
    logger.info("Relating velero-operator to %s", AZURE_INTEGRATOR)
    model = get_model(ops_test)

    await model.integrate(APP_NAME, AZURE_INTEGRATOR)
    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(
            apps=[APP_NAME],
            status="active",
            timeout=TIMEOUT,
        )
    assert_app_status(model.applications[APP_NAME], [READY_MESSAGE])


@pytest.mark.abort_on_fail
async def test_configure_azure_plugin_image(ops_test: OpsTest):
    """Test the config-changed hook for the velero-azure-plugin-image config option."""
    logger.info("Testing velero-azure-plugin-image config option")
    model = get_model(ops_test)
    app = model.applications[APP_NAME]
    new_plugin_image = "velero-test-plugin-image"

    logger.info("Setting plugin image to %s", new_plugin_image)
    await app.set_config({VELERO_AZURE_PLUGIN_IMAGE_KEY: new_plugin_image})
    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(apps=[APP_NAME], timeout=TIMEOUT, status="blocked")
    assert_app_status(app, [DEPLOYMENT_IMAGE_ERROR_MESSAGE_1, DEPLOYMENT_IMAGE_ERROR_MESSAGE_2])

    logger.info("Resetting plugin image to default")
    await app.reset_config([VELERO_AZURE_PLUGIN_IMAGE_KEY])
    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(apps=[APP_NAME], timeout=TIMEOUT, status="active")
    assert_app_status(app, [READY_MESSAGE])


@pytest.mark.abort_on_fail
async def test_azure_backup(ops_test: OpsTest, k8s_test_resources):
    """Test the backup functionality of the velero-operator charm."""
    logger.info("Testing backup functionality")
    model = get_model(ops_test)
    unit = model.applications[APP_NAME].units[0]
    test_namespace = k8s_test_resources["namespace"].metadata.name

    logger.info("Creating a backup")
    await run_charm_action(
        unit,
        "run-cli",
        command=f"backup create {BACKUP_NAME} --include-namespaces {test_namespace}",
    )

    logger.info("Verifying the backup")
    await run_charm_action(unit, "run-cli", command=f"backup describe {BACKUP_NAME}")


@pytest.mark.abort_on_fail
async def test_azure_restore(ops_test: OpsTest, k8s_test_resources, lightkube_client):
    """Test the restore functionality of the velero-operator charm."""
    logger.info("Testing restore functionality")
    model = get_model(ops_test)
    unit = model.applications[APP_NAME].units[0]
    test_resources = k8s_test_resources["resources"]
    test_namespace = k8s_test_resources["namespace"].metadata.name
    lightkube_client.delete(Namespace, test_namespace, grace_period=0)

    logger.info("Creating a restore")
    await run_charm_action(unit, "run-cli", command=f"restore create --from-backup {BACKUP_NAME}")

    logger.info("Verifying the restore")
    for resource in test_resources:
        try:
            lightkube_client.get(
                type(resource), name=resource.metadata.name, namespace=test_namespace
            )
        except ApiError as ae:
            if ae.response.status_code == 404:
                assert (
                    False
                ), f"Resource {resource.kind} {resource.metadata.name} not found after restore"
            else:
                raise


@pytest.mark.abort_on_fail
async def test_unrelate_azure_integrator(ops_test: OpsTest):
    """Test the unrelation between the velero-operator charm and the s3-integrator charm."""
    logger.info("Unrelating velero-operator from %s", AZURE_INTEGRATOR)
    model = get_model(ops_test)

    await ops_test.juju(*["remove-relation", APP_NAME, AZURE_INTEGRATOR])
    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(
            apps=[APP_NAME],
            status="blocked",
            raise_on_blocked=False,
            timeout=TIMEOUT,
        )
    assert_app_status(model.applications[APP_NAME], [MISSING_RELATION_MESSAGE])


@pytest.mark.abort_on_fail
async def test_remove(ops_test: OpsTest):
    """Remove the velero-operator and s3-integrator charms."""
    logger.info("Removing velero-operator and s3-integrator charms")
    model = get_model(ops_test)

    await asyncio.gather(
        model.remove_application(APP_NAME),
        model.remove_application(AZURE_INTEGRATOR),
        model.remove_secret(AZURE_SECRET_NAME),
        model.block_until(
            lambda: model.applications[APP_NAME].status == "unknown",
            timeout=TIMEOUT,
        ),
        model.block_until(
            lambda: model.applications[AZURE_INTEGRATOR].status == "unknown",
            timeout=TIMEOUT,
        ),
    )
