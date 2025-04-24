# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path

import yaml
from juju.model import Model
from pytest_operator.plugin import OpsTest

TIMEOUT = 60 * 10
METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]
MISSING_RELATION_MESSAGE = "Missing relation: [s3-credentials]"
UNTRUST_ERROR_MESSAGE = (
    "The charm must be deployed with '--trust' flag enabled, run 'juju trust ...'"
)
READY_MESSAGE = "Unit is Ready"
DEPLOYMENT_IMAGE_ERROR_MESSAGE_1 = "Velero Deployment is not ready: ImagePullBackOff"
DEPLOYMENT_IMAGE_ERROR_MESSAGE_2 = "Velero Deployment is not ready: ErrImagePull"


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
