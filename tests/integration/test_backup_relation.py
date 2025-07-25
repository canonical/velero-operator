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
    S3_INTEGRATOR,
    S3_INTEGRATOR_CHANNEL,
    TEST_APP_FIRST_RELATION_NAME,
    TEST_APP_NAME,
    TEST_APP_SECOND_RELATION_NAME,
    TIMEOUT,
    get_application_data,
    get_model,
    get_relation_data,
    is_relation_broken,
    is_relation_joined,
    k8s_assert_resource_exists,
    k8s_delete_and_wait,
    k8s_get_velero_backup,
    run_charm_action,
    verify_pvc_content,
)
from lightkube.resources.core_v1 import Namespace
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, s3_connection_info):
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
            config={"use-node-agent": True, "default-volumes-to-fs-backup": True},
        ),
        model.deploy(
            test_charm,
            application_name=TEST_APP_NAME,
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

    await model.integrate(APP_NAME, S3_INTEGRATOR)
    await integrate_and_check(
        TEST_APP_FIRST_RELATION_NAME,
        {
            "include_namespaces": ["velero-integration-tests"],
            "include_resources": ["deployments", "persistentvolumeclaims", "pods"],
            "label_selector": {"app": "dummy"},
            "ttl": "24h5m5s",
            "exclude_namespaces": None,
            "exclude_resources": None,
            "include_cluster_resources": True,
        },
    )
    await integrate_and_check(
        TEST_APP_SECOND_RELATION_NAME,
        {
            "include_namespaces": ["velero-integration-tests"],
            "include_resources": None,
            "ttl": "12h30m",
            "exclude_namespaces": None,
            "exclude_resources": ["deployments", "persistentvolumeclaims", "pods"],
            "label_selector": None,
            "include_cluster_resources": False,
        },
    )
    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(
            apps=[APP_NAME],
            status="active",
            timeout=TIMEOUT,
        )


@pytest.mark.abort_on_fail
async def test_create_backup(ops_test: OpsTest, k8s_test_resources, lightkube_client):
    """Test create-backup action of the velero-operator charm."""
    logger.info("Testing VeleroBackupProvider getters")
    model = get_model(ops_test)
    unit = model.applications[APP_NAME].units[0]
    test_namespace = k8s_test_resources["namespace"].metadata.name
    test_file = k8s_test_resources["test_file_path"]
    test_pvc_name = k8s_test_resources["pvc_name"]

    logger.info("Waiting for the test namespace to be ready")
    verify_pvc_content(lightkube_client, test_namespace, test_pvc_name, test_file, 1)

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
    result = await run_charm_action(
        unit,
        "create-backup",
        target=f"{TEST_APP_NAME}:{TEST_APP_FIRST_RELATION_NAME}",
    )
    first_backup_name = result["backup-name"]
    result = await run_charm_action(
        unit,
        "create-backup",
        target=f"{TEST_APP_NAME}:{TEST_APP_SECOND_RELATION_NAME}",
    )
    second_backup_name = result["backup-name"]

    logger.info("Verifying the backup")
    first_backup = k8s_get_velero_backup(lightkube_client, first_backup_name, model.name)
    second_backup = k8s_get_velero_backup(lightkube_client, second_backup_name, model.name)
    assert first_backup["status"]["phase"] == "Completed", "First backup is not completed"
    assert second_backup["status"]["phase"] == "Completed", "Second backup is not completed"
    logger.info("Created backups: %s and %s", first_backup_name, second_backup_name)


@pytest.mark.abort_on_fail
async def test_list_backups(ops_test: OpsTest):
    """Test the list-backups action of the velero-operator charm."""
    logger.info("Testing list-backups action")
    model = get_model(ops_test)
    unit = model.applications[APP_NAME].units[0]

    result = await run_charm_action(unit, "list-backups", app=TEST_APP_NAME)
    backups = result["backups"]
    assert len(backups) == 2, "Expected two backups for the specified app"

    result = await run_charm_action(
        unit, "list-backups", app=TEST_APP_NAME, endpoint=TEST_APP_FIRST_RELATION_NAME
    )
    backups = result["backups"]
    assert len(backups) == 1, "Expected one backup for the specified app and endpoint"


@pytest.mark.abort_on_fail
async def test_create_restore(ops_test: OpsTest, k8s_test_resources, lightkube_client):
    """Test the create-restore action of the velero-operator charm."""
    logger.info("Testing restore functionality")
    model = get_model(ops_test)
    unit = model.applications[APP_NAME].units[0]
    test_resources = k8s_test_resources["resources"]
    test_namespace = k8s_test_resources["namespace"].metadata.name
    test_file = k8s_test_resources["test_file_path"]
    test_pvc_name = k8s_test_resources["pvc_name"]
    k8s_delete_and_wait(lightkube_client, Namespace, test_namespace, grace_period=0)

    logger.info("Getting current backups")
    result = await run_charm_action(unit, "list-backups", app=TEST_APP_NAME)
    assert len(result["backups"]) > 0, "No backups found"
    backup_uids = list(result["backups"].keys())

    logger.info("Creating restores for each backup")
    for backup_uid in backup_uids:
        await run_charm_action(
            unit,
            "restore",
            **{"backup-uid": backup_uid},
        )

    logger.info("Verifying the restore")
    for resource in test_resources:
        k8s_assert_resource_exists(
            lightkube_client, type(resource), name=resource.metadata.name, namespace=test_namespace
        )
    verify_pvc_content(lightkube_client, test_namespace, test_pvc_name, test_file, 2)


@pytest.mark.abort_on_fail
async def test_unrelate(ops_test: OpsTest):
    """Unrelate charms and check the status."""
    logger.info("Unrelating velero-operator from %s", TEST_APP_NAME)
    model = get_model(ops_test)

    await ops_test.juju(*["remove-relation", APP_NAME, S3_INTEGRATOR])
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
        await model.wait_for_idle(
            apps=[APP_NAME],
            status="blocked",
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
        model.remove_application(S3_INTEGRATOR, block_until_done=True),
        model.remove_application(TEST_APP_NAME, block_until_done=True),
    )
