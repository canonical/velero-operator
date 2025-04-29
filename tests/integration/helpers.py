# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path

import yaml
from juju.application import Application
from juju.model import Model
from juju.unit import Unit
from pytest_operator.plugin import OpsTest

TIMEOUT = 60 * 10
METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]

AZURE_INTEGRATOR = "azure-storage-integrator"
AZURE_INTEGRATOR_CHANNEL = "latest/edge"

S3_INTEGRATOR = "s3-integrator"
S3_INTEGRATOR_CHANNEL = "latest/stable"

VELERO_AWS_PLUGIN_IMAGE_KEY = "velero-aws-plugin-image"
VELERO_AZURE_PLUGIN_IMAGE_KEY = "velero-azure-plugin-image"

MISSING_RELATION_MESSAGE = "Missing relation: [s3-credentials|azure-credentials]"
UNTRUST_ERROR_MESSAGE = (
    "The charm must be deployed with '--trust' flag enabled, run 'juju trust ...'"
)
READY_MESSAGE = "Unit is Ready"
DEPLOYMENT_IMAGE_ERROR_MESSAGE_1 = "Velero Deployment is not ready: ImagePullBackOff"
DEPLOYMENT_IMAGE_ERROR_MESSAGE_2 = "Velero Deployment is not ready: ErrImagePull"
MULTIPLE_RELATIONS_MESSAGE = (
    "Only one Storage Provider should be related at the time: [s3-credentials|azure-credentials]"
)


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


def assert_app_status(app: Application, statuses: list[str]) -> None:
    """Assert that the application has one of the expected statuses.

    Args:
        app: The application to check.
        statuses: A list of expected statuses for the application.

    Raises:
        AssertionError if the application does not have one of the expected statuses.
    """
    for unit in app.units:
        assert unit.workload_status_message in statuses


async def run_charm_action(unit: Unit, charm_action: str, **params) -> dict:
    """Assert that the action is run successfully and returns the results.

    Args:
        unit: The unit to run the action on.
        charm_action: The action to run.
        params: The parameters to pass to the action.

    Raises:
        AssertionError if the action does not run successfully.

    Returns:
        The results of the action.
    """
    action = await unit.run_action(charm_action, **params)
    action = await action.wait()
    assert action.status == "completed"
    return action.results
