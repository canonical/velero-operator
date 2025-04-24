#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
import uuid

import pytest
from helpers import (
    APP_NAME,
    DEPLOYMENT_IMAGE_ERROR_MESSAGE_1,
    DEPLOYMENT_IMAGE_ERROR_MESSAGE_2,
    MISSING_RELATION_MESSAGE,
    READY_MESSAGE,
    TIMEOUT,
    get_model,
)
from lightkube import ApiError
from lightkube.resources.core_v1 import Namespace
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

VELERO_AWS_PLUGIN_IMAGE_KEY = "velero-aws-plugin-image"
S3_INTEGRATOR = "s3-integrator"
S3_INTEGRATOR_CHANNEL = "latest/stable"
BACKUP_NAME = f"test-backup-{uuid.uuid4()}"


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, s3_connection_info):
    """Build the velero-operator and deploy it with the integrator charms."""
    logger.info("Building and deploying velero-operator charm with s3-integrator")

    charm = await ops_test.build_charm(".")
    model = get_model(ops_test)

    await asyncio.gather(
        model.deploy(
            charm, application_name=APP_NAME, trust=True, config={"use-node-agent": True}
        ),
        model.deploy(S3_INTEGRATOR, channel=S3_INTEGRATOR_CHANNEL),
        model.wait_for_idle(apps=[APP_NAME, S3_INTEGRATOR], status="blocked", timeout=TIMEOUT),
    )

    for unit in model.applications[APP_NAME].units:
        assert unit.workload_status_message == MISSING_RELATION_MESSAGE


@pytest.mark.abort_on_fail
async def test_configure_s3_integrator(
    ops_test: OpsTest,
    s3_cloud_credentials,
    s3_cloud_configs,
):
    """Configure the integrator charm with the credentials and configs."""
    model = get_model(ops_test)
    app = model.applications[S3_INTEGRATOR]

    logger.info("Setting credentials for %s", S3_INTEGRATOR)
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
async def test_relate_s3_integrator(ops_test: OpsTest):
    """Test the relation between the velero-operator charm and the s3-integrator charm."""
    model = get_model(ops_test)

    logger.info("Relating velero-operator to %s", S3_INTEGRATOR)
    await model.integrate(APP_NAME, S3_INTEGRATOR)
    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(
            apps=[APP_NAME],
            status="active",
            timeout=TIMEOUT,
        )

    for unit in model.applications[APP_NAME].units:
        assert unit.workload_status_message == READY_MESSAGE


@pytest.mark.abort_on_fail
async def test_configure_s3_plugin_image(ops_test: OpsTest):
    """Test the config-changed hook for the velero-aws-plugin-image config option."""
    model = get_model(ops_test)
    app = model.applications[APP_NAME]
    new_plugin_image = "velero-test-plugin-image"

    logger.info("Setting plugin image to %s", new_plugin_image)
    await app.set_config({VELERO_AWS_PLUGIN_IMAGE_KEY: new_plugin_image})
    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(apps=[APP_NAME], timeout=TIMEOUT, status="blocked")

    for unit in model.applications[APP_NAME].units:
        assert (
            unit.workload_status_message == DEPLOYMENT_IMAGE_ERROR_MESSAGE_1
            or unit.workload_status_message == DEPLOYMENT_IMAGE_ERROR_MESSAGE_2
        )

    logger.info("Resetting plugin image to default")
    await app.reset_config([VELERO_AWS_PLUGIN_IMAGE_KEY])
    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(apps=[APP_NAME], timeout=TIMEOUT, status="active")

    for unit in model.applications[APP_NAME].units:
        assert unit.workload_status_message == READY_MESSAGE


@pytest.mark.abort_on_fail
async def test_s3_backup(ops_test: OpsTest, k8s_test_resources):
    """Test the backup functionality of the velero-operator charm."""
    model = get_model(ops_test)
    app = model.applications[APP_NAME]
    test_namespace = k8s_test_resources["namespace"].metadata.name

    logger.info("Creating a backup")
    action = await app.units[0].run_action(
        "run-cli", command=f"backup create {BACKUP_NAME} --include-namespaces {test_namespace}"
    )
    action = await action.wait()
    assert action.status == "completed"

    logger.info("Verifying the backup")
    action = await app.units[0].run_action("run-cli", command=f"backup describe {BACKUP_NAME}")
    action = await action.wait()
    assert action.status == "completed"


@pytest.mark.abort_on_fail
async def test_s3_restore(ops_test: OpsTest, k8s_test_resources, lightkube_client):
    """Test the restore functionality of the velero-operator charm."""
    model = get_model(ops_test)
    app = model.applications[APP_NAME]
    test_resources = k8s_test_resources["resources"]
    test_namespace = k8s_test_resources["namespace"].metadata.name
    lightkube_client.delete(Namespace, test_namespace)

    logger.info("Creating a restore")
    action = await app.units[0].run_action(
        "run-cli", command=f"restore create --from-backup {BACKUP_NAME}"
    )
    action = await action.wait()
    assert action.status == "completed"

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
async def test_unrelate_s3_integrator(ops_test: OpsTest):
    """Test the unrelation between the velero-operator charm and the s3-integrator charm."""
    model = get_model(ops_test)

    logger.info("Unrelating velero-operator from %s", S3_INTEGRATOR)
    await ops_test.juju(*["remove-relation", APP_NAME, S3_INTEGRATOR])
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
async def test_remove(ops_test: OpsTest):
    """Remove the velero-operator and s3-integrator charms."""
    model = get_model(ops_test)

    await asyncio.gather(
        model.remove_application(APP_NAME),
        model.remove_application(S3_INTEGRATOR),
        model.block_until(
            lambda: model.applications[APP_NAME].status == "unknown",
            timeout=TIMEOUT,
        ),
        model.block_until(
            lambda: model.applications[S3_INTEGRATOR].status == "unknown",
            timeout=TIMEOUT,
        ),
    )
