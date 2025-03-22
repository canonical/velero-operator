# Copyright 2025 Iurii Kondrakov
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import ops.testing
import pytest

from charm import VeleroOperatorCharm


@pytest.fixture
def harness():
    harness = ops.testing.Harness(VeleroOperatorCharm)
    harness.begin()
    yield harness
    harness.cleanup()


def test_dummy(harness: ops.testing.Harness[VeleroOperatorCharm]):
    assert True
