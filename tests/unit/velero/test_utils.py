# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import pytest
from pydantic import ValidationError

from velero import ExistingResourcePolicy, RestoreParams

valid_restore_params = [
    pytest.param(
        {"backup-uid": "backup.uid-1"},
        {
            "backup_uid": "backup.uid-1",
            "existing_resource_policy": ExistingResourcePolicy.No,
            "include_namespaces": None,
            "exclude_namespaces": None,
            "include_resources": None,
            "exclude_resources": None,
            "selector": None,
            "or_selector": None,
        },
        id="minimal-required",
    ),
    pytest.param(
        {
            "backup-uid": "backup.uid-2",
            "existing-resource-policy": "update",
            "include-namespaces": "ns-a, ns-b",
            "include-resources": "pods, services",
            "selector": "app=my-app,tier=backend",
        },
        {
            "backup_uid": "backup.uid-2",
            "existing_resource_policy": ExistingResourcePolicy.Update,
            "include_namespaces": ["ns-a", "ns-b"],
            "exclude_namespaces": None,
            "include_resources": ["pods", "services"],
            "exclude_resources": None,
            "selector": {"app": "my-app", "tier": "backend"},
            "or_selector": None,
        },
        id="comma-separated-and-selector",
    ),
    pytest.param(
        {
            "backup-uid": "backup.uid-3",
            "exclude-namespaces": ["kube-system"],
            "exclude-resources": ["secrets"],
            "or-selector": "env=prod or app=velero",
        },
        {
            "backup_uid": "backup.uid-3",
            "existing_resource_policy": ExistingResourcePolicy.No,
            "include_namespaces": None,
            "exclude_namespaces": ["kube-system"],
            "include_resources": None,
            "exclude_resources": ["secrets"],
            "selector": None,
            "or_selector": {"env": "prod", "app": "velero"},
        },
        id="list-values-and-or-selector",
    ),
]

invalid_restore_params = [
    pytest.param(
        {
            "backup-uid": "backup.uid-4",
            "selector": "app=velero",
            "or-selector": "env=prod",
        },
        id="selector-and-or-selector-mutually-exclusive",
    ),
    pytest.param(
        {
            "backup-uid": "backup.uid-5",
            "include-namespaces": "team-a",
            "exclude-namespaces": "team-b",
        },
        id="include-exclude-namespaces-mutually-exclusive",
    ),
    pytest.param(
        {
            "backup-uid": "backup.uid-6",
            "include-resources": "pods",
            "exclude-resources": "services",
        },
        id="include-exclude-resources-mutually-exclusive",
    ),
    pytest.param(
        {"backup-uid": "backup.uid-7", "selector": "appvelero"},
        id="selector-missing-equals",
    ),
    pytest.param(
        {"backup-uid": "backup.uid-8", "selector": "app$=velero"},
        id="selector-invalid-characters",
    ),
    pytest.param(
        {"backup-uid": "backup.uid-9", "or-selector": "envprod"},
        id="or-selector-missing-equals",
    ),
    pytest.param(
        {"backup-uid": "backup.uid-10", "or-selector": "env=prod or app$=velero"},
        id="or-selector-invalid-characters",
    ),
    pytest.param(
        {"backup-uid": "backup.uid-11", "existing-resource-policy": "replace"},
        id="invalid-existing-resource-policy",
    ),
    pytest.param(
        {"selector": "app=velero"},
        id="missing-required-backup-uid",
    ),
    pytest.param({"backup-uid": ""}, id="empty-backup-uid"),
    pytest.param({"backup-uid": "   "}, id="whitespace-backup-uid"),
    pytest.param({"backup-uid": "backup.uid-11", "selector": "app="}, id="selector-empty-value"),
    pytest.param({"backup-uid": "backup.uid-11", "selector": "   "}, id="selector-empty-value"),
]


@pytest.mark.parametrize("params, expected", valid_restore_params)
def test_restore_params_valid(params, expected):
    """Test that valid restore parameters are accepted."""
    try:
        model = RestoreParams.model_validate(params)
    except ValidationError as ve:
        pytest.fail(f"Valid parameters failed validation: {ve}")

    for key, value in expected.items():
        assert getattr(model, key) == value


@pytest.mark.parametrize("params", invalid_restore_params)
def test_restore_params_invalid(params):
    """Test that invalid restore parameters are rejected."""
    with pytest.raises(ValidationError):
        RestoreParams.model_validate(params)
