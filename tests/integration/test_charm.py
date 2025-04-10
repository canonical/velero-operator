#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from httpx import HTTPStatusError
from juju.model import Model
from lightkube import Client
from lightkube.core.exceptions import ApiError
from lightkube.resources.apiextensions_v1 import CustomResourceDefinition
from pytest_operator.plugin import OpsTest

from constants import StorageRelation
from velero import Velero

logger = logging.getLogger(__name__)

TIMEOUT = 60 * 5
USE_NODE_AGENT_CONFIG_KEY = "use-node-agent"
METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]
S3_INTEGRATOR = "s3-integrator"
S3_INTEGRATOR_CHANNEL = "latest/stable"
AZURE_INTEGRATOR = "azure-storage-integrator"
AZURE_INTEGRATOR_CHANNEL = "latest/edge"
AZURE_SECRET_NAME = f"azure-secret-{time.time()}"

UNTRUST_ERROR_MESSAGE = (
    "The charm must be deployed with '--trust' flag enabled, run 'juju trust ...'"
)
READY_MESSAGE = "Unit is Ready"
RELATIONS = "|".join([r.value for r in StorageRelation])
MISSING_RELATION_MESSAGE = f"Missing relation: [{RELATIONS}]"
MULTIPLE_RELATIONS_MESSAGE = (
    f"Only one Storage Provider should be related at the time: [{RELATIONS}]"
)


@pytest.fixture(scope="session")
def lightkube_client() -> Client:
    """Return a lightkube client to use in this session."""
    client = Client(field_manager=APP_NAME)
    return client


def get_velero(model: str) -> Velero:
    """Return a Velero instance for the given model."""
    return Velero("./velero", model)


def get_model(ops_test: OpsTest) -> Model:
    """Return the Juju model of the current test.

    Returns:
        A juju.model.Model instance of the current model.

    Raises:
        AssertionError if the test doesn't have a Juju model.
    """
    model = ops_test.model
    if model is None:
        raise AssertionError("ops_test has a None model.")
    return model


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, s3_cloud_credentials, s3_cloud_configs):
    endpoint = s3_cloud_configs.get("endpoint")
    
    logger.info(f"Launching test pod to verify connectivity to {endpoint}")
    try:
        cmd = [
            "microk8s", "kubectl", "run", "--rm", "-i", "--tty",
            "connectivity-test",
            "--image=busybox",
            "--restart=Never",
            "--",
            "wget", endpoint, "-O", "-"
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=10, check=True, encoding="utf-8")
        logger.info(f"Pod output: {result.stdout}")
        logger.info("Pod successfully reached the host IP")
    except subprocess.CalledProcessError as e:
        logger.warning(f"Pod failed to connect to {endpoint}: {e.stderr}")
        assert False
