#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
from datetime import datetime

import pytest
from helpers import (
    APP_NAME,
    K8S_BACKUP_TARGET_RELATION_NAME,
    S3_INTEGRATOR,
    TEST_APP_K8S_BACKUP_ENDPOINT,
    TEST_APP_NAME,
    TIMEOUT,
    configure_s3_integrator,
    deploy_velero_and_test_charm,
    get_application_data,
    get_model,
    get_relation_data,
    is_relation_broken,
    is_relation_joined,
    k8s_assert_resource_exists,
    k8s_delete_and_wait,
    k8s_get_velero_backup,
    remove_all_applications,
    run_charm_action,
    verify_pvc_content,
)
from lightkube.resources.core_v1 import Namespace
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_build_and_deploy(
    ops_test: OpsTest,
    s3_connection_info,
    velero_operator_charm_path,
    test_charm_path,
):
    """Build and deploy the velero-operator and test charm."""
    await deploy_velero_and_test_charm(ops_test, velero_operator_charm_path, test_charm_path)


@pytest.mark.abort_on_fail
async def test_configure_s3_integrator(
    ops_test: OpsTest,
    s3_cloud_credentials,
    s3_cloud_configs,
):
    """Configure the integrator charm with the credentials and configs."""
    await configure_s3_integrator(ops_test, s3_cloud_credentials, s3_cloud_configs)


@pytest.mark.abort_on_fail
async def test_relate(ops_test: OpsTest):
    """Relate charms using the k8s-backup-target interface."""
    logger.info("Relating velero-operator to %s via k8s-backup-target", TEST_APP_NAME)
    model = get_model(ops_test)

    await model.integrate(APP_NAME, S3_INTEGRATOR)

    logger.info(
        "Integrating %s:%s with %s:%s",
        APP_NAME,
        K8S_BACKUP_TARGET_RELATION_NAME,
        TEST_APP_NAME,
        TEST_APP_K8S_BACKUP_ENDPOINT,
    )
    await model.integrate(
        f"{APP_NAME}:{K8S_BACKUP_TARGET_RELATION_NAME}",
        f"{TEST_APP_NAME}:{TEST_APP_K8S_BACKUP_ENDPOINT}",
    )
    async with ops_test.fast_forward(fast_interval="30s"):
        await model.block_until(lambda: is_relation_joined(model, TEST_APP_K8S_BACKUP_ENDPOINT))
        await model.wait_for_idle(
            apps=[TEST_APP_NAME],
            status="active",
            timeout=TIMEOUT,
        )

    logger.info("Checking the content of the relation data for k8s-backup-target")
    relation_data = await get_relation_data(
        ops_test, APP_NAME, K8S_BACKUP_TARGET_RELATION_NAME, TEST_APP_K8S_BACKUP_ENDPOINT
    )
    application_data = await get_application_data(
        ops_test, APP_NAME, K8S_BACKUP_TARGET_RELATION_NAME, TEST_APP_K8S_BACKUP_ENDPOINT
    )
    logger.info(relation_data)
    logger.info(application_data)
    assert "backup_targets" in application_data
    targets = json.loads(application_data["backup_targets"])
    assert len(targets) > 0
    target = targets[0]
    assert target["app"] == TEST_APP_NAME
    assert target["relation_name"] == TEST_APP_K8S_BACKUP_ENDPOINT
    spec = target["spec"]
    expected_spec = {
        "include_namespaces": ["velero-integration-tests"],
        "include_resources": [
            "deployments",
            "persistentvolumeclaims",
            "pods",
            "persistentvolumes",
            "services",
        ],
        "label_selector": {"app": "dummy"},
        "ttl": "24h5m5s",
        "exclude_namespaces": None,
        "exclude_resources": None,
        "include_cluster_resources": None,
    }
    for key, value in expected_spec.items():
        assert spec.get(key) == value

    async with ops_test.fast_forward(fast_interval="60s"):
        await model.wait_for_idle(
            apps=[APP_NAME],
            status="active",
            timeout=TIMEOUT,
        )


@pytest.mark.abort_on_fail
async def test_refresh_event(ops_test: OpsTest):
    """Test the refresh event for the K8sBackupTargetProvider."""
    logger.info("Testing refresh event for K8sBackupTargetProvider")
    model = get_model(ops_test)
    app = model.applications[TEST_APP_NAME]

    await app.set_config({"ttl": "48h"})
    async with ops_test.fast_forward(fast_interval="30s"):
        await model.wait_for_idle(
            apps=[TEST_APP_NAME],
            status="active",
            timeout=TIMEOUT,
        )

    application_data = await get_application_data(
        ops_test, APP_NAME, K8S_BACKUP_TARGET_RELATION_NAME, TEST_APP_K8S_BACKUP_ENDPOINT
    )
    assert "backup_targets" in application_data
    targets = json.loads(application_data["backup_targets"])
    assert len(targets) > 0
    assert targets[0]["spec"]["ttl"] == "48h"


@pytest.mark.abort_on_fail
async def test_create_backup(ops_test: OpsTest, k8s_test_resources, lightkube_client):
    """Test create-backup action via k8s-backup-target relation."""
    logger.info("Testing create-backup action via k8s-backup-target")
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

    logger.info("Running the create-backup action with k8s-backup-target")
    result = await run_charm_action(
        unit,
        "create-backup",
        target=f"{TEST_APP_NAME}:{TEST_APP_K8S_BACKUP_ENDPOINT}",
    )
    backup_name = result["backup-name"]

    logger.info("Verifying the backup")
    backup = k8s_get_velero_backup(lightkube_client, backup_name, model.name)
    assert backup["status"]["phase"] == "Completed", "K8s backup target backup is not completed"
    logger.info("Created backup: %s", backup_name)


@pytest.mark.abort_on_fail
async def test_list_backups(ops_test: OpsTest):
    """Test the list-backups action for k8s-backup-target backups."""
    logger.info("Testing list-backups action")
    model = get_model(ops_test)
    unit = model.applications[APP_NAME].units[0]

    result = await run_charm_action(
        unit, "list-backups", app=TEST_APP_NAME, endpoint=TEST_APP_K8S_BACKUP_ENDPOINT
    )
    backups = result["backups"]
    assert len(backups) == 1, "Expected one backup for the k8s-backup-target endpoint"


@pytest.mark.abort_on_fail
async def test_create_restore(ops_test: OpsTest, k8s_test_resources, lightkube_client):
    """Test the create-restore action for a k8s-backup-target backup.

    The backup spec uses label_selector={"app": "dummy"}, so only resources with
    that label are included: PVC, Deployment, and Service (dummy-service).
    Resources labelled app=dummy-2 (ConfigMap, dummy-service-2) are not backed up.
    """
    logger.info("Testing restore functionality via k8s-backup-target")
    model = get_model(ops_test)
    unit = model.applications[APP_NAME].units[0]
    test_namespace = k8s_test_resources["namespace"].metadata.name
    test_file = k8s_test_resources["test_file_path"]
    test_pvc_name = k8s_test_resources["pvc_name"]
    k8s_delete_and_wait(
        lightkube_client, Namespace, test_namespace, grace_period=0, timeout_seconds=300
    )

    logger.info("Getting current backups")
    result = await run_charm_action(
        unit, "list-backups", app=TEST_APP_NAME, endpoint=TEST_APP_K8S_BACKUP_ENDPOINT
    )
    assert len(result["backups"]) > 0, "No backups found"
    logger.info("Backups found: %s", result["backups"])

    backups = result["backups"]
    backup_uids = [
        uid
        for uid, _ in sorted(
            backups.items(),
            key=lambda item: datetime.strptime(item[1]["start-timestamp"], "%Y-%m-%dT%H:%M:%SZ"),
        )
    ]

    logger.info("Creating restores for each backup")
    for backup_uid in backup_uids:
        await run_charm_action(
            unit,
            "restore",
            **{"backup-uid": backup_uid},
        )

    logger.info("Verifying the restore — only app=dummy resources should be restored")
    # The backup only includes resources with label app=dummy:
    # PVC (test-pvc), Deployment (dummy-deployment), Service (dummy-service)
    expected_resources = [
        r
        for r in k8s_test_resources["resources"]
        if r.metadata.labels and r.metadata.labels.get("app") == "dummy"
    ]
    for resource in expected_resources:
        k8s_assert_resource_exists(
            lightkube_client, type(resource), name=resource.metadata.name, namespace=test_namespace
        )
    verify_pvc_content(lightkube_client, test_namespace, test_pvc_name, test_file, 2)


@pytest.mark.abort_on_fail
async def test_unrelate(ops_test: OpsTest):
    """Unrelate the k8s-backup-target relation and check the status."""
    logger.info("Unrelating velero-operator from %s (k8s-backup-target)", TEST_APP_NAME)
    model = get_model(ops_test)

    await ops_test.juju(*["remove-relation", APP_NAME, S3_INTEGRATOR])
    await ops_test.juju(
        *[
            "remove-relation",
            APP_NAME,
            f"{TEST_APP_NAME}:{TEST_APP_K8S_BACKUP_ENDPOINT}",
        ]
    )

    async with ops_test.fast_forward(fast_interval="30s"):
        await model.block_until(lambda: is_relation_broken(model, TEST_APP_K8S_BACKUP_ENDPOINT))
        await model.wait_for_idle(
            apps=[TEST_APP_NAME],
            status="waiting",
            timeout=TIMEOUT,
        )
        await model.wait_for_idle(
            apps=[APP_NAME],
            status="blocked",
            timeout=TIMEOUT,
        )


@pytest.mark.abort_on_fail
async def test_remove(ops_test: OpsTest):
    """Remove the velero-operator and s3-integrator charms."""
    await remove_all_applications(ops_test)
