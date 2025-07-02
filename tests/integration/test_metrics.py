#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging

import pytest
from charmed_kubeflow_chisme.testing import assert_metrics_endpoint
from helpers import (
    APP_NAME,
    MISSING_RELATION_MESSAGE,
    TIMEOUT,
    assert_app_status,
    get_model,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

GRAFANA_AGENT_APP = "grafana-agent-k8s"
GRAFANA_AGENT_CHANNEL = "1/stable"
METRICS_ENDPOINT = "metrics-endpoint"
METRICS_PORT = 8085
METRICS_PATH = "/metrics"


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build and deploy the velero-operator with grafana-agent-k8s."""
    logger.info("Building and deploying velero-operator charm with grafana-agent-k8s")
    charm = await ops_test.build_charm(".")
    model = get_model(ops_test)

    await asyncio.gather(
        model.deploy(
            charm, application_name=APP_NAME, trust=True, config={"use-node-agent": True}
        ),
        model.deploy(GRAFANA_AGENT_APP, channel=GRAFANA_AGENT_CHANNEL, trust=True),
        model.wait_for_idle(apps=[APP_NAME, GRAFANA_AGENT_APP], status="blocked", timeout=TIMEOUT),
    )
    assert_app_status(model.applications[APP_NAME], [MISSING_RELATION_MESSAGE])


@pytest.mark.abort_on_fail
async def test_relate_grafana_agent(ops_test: OpsTest):
    """Relate the velero-operator with grafana-agent-k8s."""
    logger.info("Relating velero-operator with grafana-agent-k8s using %s", METRICS_ENDPOINT)
    model = get_model(ops_test)

    await model.integrate(
        f"{APP_NAME}:{METRICS_ENDPOINT}",
        f"{GRAFANA_AGENT_APP}:{METRICS_ENDPOINT}",
    )

    await model.wait_for_idle(
        apps=[GRAFANA_AGENT_APP],
        status="blocked",
        timeout=TIMEOUT,
    )


@pytest.mark.abort_on_fail
async def test_metrics_enpoint(ops_test: OpsTest):
    """Test metrics_endpoints are defined in relation data bag and their accessibility."""
    logger.info("Testing metrics endpoints")
    model = get_model(ops_test)
    app = model.applications[APP_NAME]
    await assert_metrics_endpoint(app, metrics_port=METRICS_PORT, metrics_path=METRICS_PATH)


@pytest.mark.abort_on_fail
async def test_remove(ops_test: OpsTest):
    """Remove the velero-operator and s3-integrator charms."""
    logger.info("Removing velero-operator and grafana-agent-k8s charms")
    model = get_model(ops_test)

    await asyncio.gather(
        model.remove_application(APP_NAME, block_until_done=True),
        model.remove_application(GRAFANA_AGENT_APP, destroy_storage=True, block_until_done=True),
    )
