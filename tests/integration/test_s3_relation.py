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
    assert_app_status,
    get_model,
    k8s_assert_resource_exists,
    k8s_delete_and_wait,
    k8s_get_pvc_content,
    k8s_get_velero_backup,
    run_charm_action,
)
from lightkube.resources.core_v1 import Namespace, Pod
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_fixed

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
            charm,
            application_name=APP_NAME,
            trust=True,
            config={"use-node-agent": True, "default-volumes-to-fs-backup": True},
        ),
        model.deploy(S3_INTEGRATOR, channel=S3_INTEGRATOR_CHANNEL),
        model.wait_for_idle(apps=[APP_NAME, S3_INTEGRATOR], status="blocked", timeout=TIMEOUT),
    )
    assert_app_status(model.applications[APP_NAME], [MISSING_RELATION_MESSAGE])


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
async def test_relate_s3_integrator(ops_test: OpsTest):
    """Test the relation between the velero-operator charm and the s3-integrator charm."""
    logger.info("Relating velero-operator to %s", S3_INTEGRATOR)
    model = get_model(ops_test)

    await model.integrate(APP_NAME, S3_INTEGRATOR)
    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(
            apps=[APP_NAME],
            status="active",
            timeout=TIMEOUT,
        )
    assert_app_status(model.applications[APP_NAME], [READY_MESSAGE])


@pytest.mark.abort_on_fail
async def test_configure_s3_plugin_image(ops_test: OpsTest):
    """Test the config-changed hook for the velero-aws-plugin-image config option."""
    logger.info("Testing velero-aws-plugin-image config option")
    model = get_model(ops_test)
    app = model.applications[APP_NAME]
    new_plugin_image = "velero-test-plugin-image"

    logger.info("Setting plugin image to %s", new_plugin_image)
    await app.set_config({VELERO_AWS_PLUGIN_IMAGE_KEY: new_plugin_image})
    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(apps=[APP_NAME], timeout=TIMEOUT, status="blocked")
    assert_app_status(app, [DEPLOYMENT_IMAGE_ERROR_MESSAGE_1, DEPLOYMENT_IMAGE_ERROR_MESSAGE_2])

    logger.info("Resetting plugin image to default")
    await app.reset_config([VELERO_AWS_PLUGIN_IMAGE_KEY])
    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(apps=[APP_NAME], timeout=TIMEOUT, status="active")
    assert_app_status(app, [READY_MESSAGE])


@pytest.mark.abort_on_fail
async def test_s3_backup(ops_test: OpsTest, k8s_test_resources, lightkube_client):
    """Test the backup functionality of the velero-operator charm."""
    logger.info("Testing backup functionality")
    model = get_model(ops_test)
    unit = model.applications[APP_NAME].units[0]
    test_namespace = k8s_test_resources["namespace"].metadata.name
    test_file = k8s_test_resources["test_file_path"]
    test_pvc_name = k8s_test_resources["pvc_name"]

    logger.info("Waiting for the test namespace to be ready")
    for attempt in Retrying(
        stop=stop_after_attempt(10),
        wait=wait_fixed(3),
        retry=retry_if_exception_type(AssertionError),
        reraise=True,
    ):
        with attempt:
            pods = list(
                lightkube_client.list(Pod, namespace=test_namespace, labels={"pvc": test_pvc_name})
            )
            assert len(pods) == 1, "Expected one pod with PVC label"

            logger.info("Checking PVC content before backup")
            pod_name = pods[0].metadata.name
            content = k8s_get_pvc_content(
                lightkube_client, pod_name, test_namespace, test_pvc_name, test_file
            )
            assert (
                len(content.splitlines()) == 1
            ), "PVC content is not as expected, should be 1 line before backup"

    logger.info("Creating a backup")
    # Includes pv to test if the VolumeSnapshotLocation is configured correctly
    await run_charm_action(
        unit,
        "run-cli",
        command=f"backup create {BACKUP_NAME} --wait --include-namespaces {test_namespace} "
        f"--include-cluster-scoped-resources persistentvolumes",
    )

    logger.info("Verifying the backup")
    backup = k8s_get_velero_backup(lightkube_client, BACKUP_NAME, model.name)
    assert backup["status"]["phase"] == "Completed", "Backup is not completed"


@pytest.mark.abort_on_fail
async def test_s3_restore(ops_test: OpsTest, k8s_test_resources, lightkube_client):
    """Test the restore functionality of the velero-operator charm."""
    logger.info("Testing restore functionality")
    model = get_model(ops_test)
    unit = model.applications[APP_NAME].units[0]
    test_resources = k8s_test_resources["resources"]
    test_namespace = k8s_test_resources["namespace"].metadata.name
    test_file = k8s_test_resources["test_file_path"]
    test_pvc_name = k8s_test_resources["pvc_name"]
    k8s_delete_and_wait(lightkube_client, Namespace, test_namespace, grace_period=0)

    logger.info("Creating a restore")
    await run_charm_action(
        unit, "run-cli", command=f"restore create --from-backup {BACKUP_NAME} --wait"
    )

    logger.info("Verifying the restore")
    for resource in test_resources:
        k8s_assert_resource_exists(
            lightkube_client, type(resource), name=resource.metadata.name, namespace=test_namespace
        )

    for attempt in Retrying(
        stop=stop_after_attempt(10),
        wait=wait_fixed(3),
        retry=retry_if_exception_type(AssertionError),
        reraise=True,
    ):
        with attempt:
            pods = list(
                lightkube_client.list(Pod, namespace=test_namespace, labels={"pvc": test_pvc_name})
            )
            assert len(pods) == 1, "Expected one pod with PVC label"

            logger.info("Checking PVC content after restore")
            pod_name = pods[0].metadata.name
            content = k8s_get_pvc_content(
                lightkube_client, pod_name, test_namespace, test_pvc_name, test_file
            )
            assert (
                len(content.splitlines()) == 2
            ), "PVC content is not as expected, should be 2 lines after restore"


@pytest.mark.abort_on_fail
async def test_unrelate_s3_integrator(ops_test: OpsTest):
    """Test the unrelation between the velero-operator charm and the s3-integrator charm."""
    logger.info("Unrelating velero-operator from %s", S3_INTEGRATOR)
    model = get_model(ops_test)

    await ops_test.juju(*["remove-relation", APP_NAME, S3_INTEGRATOR])
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
        model.remove_application(APP_NAME, block_until_done=True),
        model.remove_application(S3_INTEGRATOR, block_until_done=True),
    )
