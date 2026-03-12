#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
import os
import time
import uuid

import pytest
from helpers import (
    APP_NAME,
    BACKUP_STORAGE_LOCALTION_UNAVAILABLE_MESSAGE,
    DEPLOYMENT_IMAGE_ERROR_MESSAGE_1,
    DEPLOYMENT_IMAGE_ERROR_MESSAGE_2,
    GCS_INTEGRATOR,
    GCS_INTEGRATOR_CHANNEL,
    MISSING_RELATION_MESSAGE,
    READY_MESSAGE,
    TIMEOUT,
    VELERO_GCP_PLUGIN_IMAGE_KEY,
    assert_app_status,
    get_model,
    k8s_assert_resource_exists,
    k8s_delete_and_wait,
    k8s_get_velero_backup,
    run_charm_action,
    verify_pvc_content,
)
from lightkube.resources.core_v1 import Namespace
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

GCS_SA_SECRET_NAME = f"gcs-sa-secret-{time.time()}"
BACKUP_NAME = f"test-backup-{uuid.uuid4()}"


@pytest.mark.abort_on_fail
async def test_build_and_deploy(
    ops_test: OpsTest, gcs_connection_info, velero_operator_charm_path, lightkube_client
):
    """Build the velero-operator and deploy it with the gcs-integrator charm."""
    logger.info("Building and deploying velero-operator charm with gcs-integrator")
    model = get_model(ops_test)

    await asyncio.gather(
        model.deploy(
            velero_operator_charm_path,
            application_name=APP_NAME,
            trust=True,
            config={"use-node-agent": False, "default-volumes-to-fs-backup": False},
        ),
        model.deploy(GCS_INTEGRATOR, channel=GCS_INTEGRATOR_CHANNEL),
        model.wait_for_idle(apps=[APP_NAME, GCS_INTEGRATOR], status="blocked", timeout=TIMEOUT),
    )
    assert_app_status(model.applications[APP_NAME], [MISSING_RELATION_MESSAGE])


@pytest.mark.abort_on_fail
async def test_configure_gcs_integrator(ops_test: OpsTest, gcs_connection_info):
    """Configure the gcs-integrator charm with the bucket and service account credentials."""
    logger.info("Setting credentials for %s", GCS_INTEGRATOR)
    model = get_model(ops_test)
    app = model.applications[GCS_INTEGRATOR]

    _, stdout, _ = await ops_test.juju(
        *[
            "add-secret",
            GCS_SA_SECRET_NAME,
            f"secret-key={gcs_connection_info.service_account_key_json}",
        ]
    )
    await model.grant_secret(GCS_SA_SECRET_NAME, GCS_INTEGRATOR)
    await app.set_config({"bucket": gcs_connection_info.bucket, "credentials": stdout.strip()})

    await model.wait_for_idle(
        apps=[GCS_INTEGRATOR],
        status="active",
        timeout=TIMEOUT,
    )


@pytest.mark.abort_on_fail
async def test_relate_gcs_integrator(ops_test: OpsTest, gcs_connection_info):
    """Test the relation between the velero-operator charm and the gcs-integrator charm."""
    logger.info("Relating velero-operator to %s", GCS_INTEGRATOR)
    model = get_model(ops_test)

    await model.integrate(APP_NAME, GCS_INTEGRATOR)

    if gcs_connection_info.ci:
        async with ops_test.fast_forward(fast_interval="60s"):
            await model.wait_for_idle(
                apps=[APP_NAME],
                status="blocked",
                timeout=TIMEOUT,
            )
        assert_app_status(
            model.applications[APP_NAME], [BACKUP_STORAGE_LOCALTION_UNAVAILABLE_MESSAGE]
        )
    else:
        async with ops_test.fast_forward(fast_interval="60s"):
            await model.wait_for_idle(
                apps=[APP_NAME],
                status="active",
                timeout=TIMEOUT,
            )
        assert_app_status(model.applications[APP_NAME], [READY_MESSAGE])


@pytest.mark.abort_on_fail
@pytest.mark.skipif(os.environ.get("CI") == "true", reason="Cannot test change gcs plugin on CI.")
async def test_configure_gcs_plugin_image(ops_test: OpsTest):
    """Test the config-changed hook for the velero-gcp-plugin-image config option."""
    logger.info("Testing velero-gcp-plugin-image config option")
    model = get_model(ops_test)
    app = model.applications[APP_NAME]
    new_plugin_image = "velero-test-plugin-image"

    logger.info("Setting plugin image to %s", new_plugin_image)
    await app.set_config({VELERO_GCP_PLUGIN_IMAGE_KEY: new_plugin_image})
    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(apps=[APP_NAME], timeout=TIMEOUT, status="blocked")
    assert_app_status(app, [DEPLOYMENT_IMAGE_ERROR_MESSAGE_1, DEPLOYMENT_IMAGE_ERROR_MESSAGE_2])

    logger.info("Resetting plugin image to default")
    await app.reset_config([VELERO_GCP_PLUGIN_IMAGE_KEY])
    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(apps=[APP_NAME], timeout=TIMEOUT, status="active")
    assert_app_status(app, [READY_MESSAGE])


@pytest.mark.abort_on_fail
@pytest.mark.skipif(os.environ.get("CI") == "true", reason="Cannot test backup to GCP on CI.")
async def test_gcs_backup(ops_test: OpsTest, k8s_test_resources, lightkube_client):
    """Test the backup functionality of the velero-operator charm."""
    logger.info("Testing backup functionality")
    model = get_model(ops_test)
    unit = model.applications[APP_NAME].units[0]
    test_namespace = k8s_test_resources["namespace"].metadata.name
    test_file = k8s_test_resources["test_file_path"]
    test_pvc_name = k8s_test_resources["pvc_name"]

    logger.info("Waiting for the test namespace to be ready")
    verify_pvc_content(lightkube_client, test_namespace, test_pvc_name, test_file, 1)

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
@pytest.mark.skipif(os.environ.get("CI") == "true", reason="Cannot test restore to GCP on CI.")
async def test_gcs_restore(ops_test: OpsTest, k8s_test_resources, lightkube_client):
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
    verify_pvc_content(lightkube_client, test_namespace, test_pvc_name, test_file, 2)


@pytest.mark.abort_on_fail
async def test_unrelate_gcs_integrator(ops_test: OpsTest):
    """Test the unrelation between the velero-operator charm and the gcs-integrator charm."""
    logger.info("Unrelating velero-operator from %s", GCS_INTEGRATOR)
    model = get_model(ops_test)

    await ops_test.juju(*["remove-relation", APP_NAME, GCS_INTEGRATOR])
    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(
            apps=[APP_NAME],
            status="blocked",
            timeout=TIMEOUT,
        )
    assert_app_status(
        model.applications[APP_NAME],
        [MISSING_RELATION_MESSAGE, BACKUP_STORAGE_LOCALTION_UNAVAILABLE_MESSAGE],
    )


@pytest.mark.abort_on_fail
async def test_remove(ops_test: OpsTest):
    """Remove the velero-operator and gcs-integrator charms."""
    logger.info("Removing velero-operator and gcs-integrator charms")
    model = get_model(ops_test)

    await asyncio.gather(
        model.remove_application(APP_NAME, block_until_done=True),
        model.remove_application(GCS_INTEGRATOR, block_until_done=True),
    )
