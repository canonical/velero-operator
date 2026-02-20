# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for Velero Schedule management (ScheduleMixin)."""

from unittest.mock import MagicMock

import httpx
import pytest
from charms.velero_libs.v0.velero_backup_config import VeleroBackupSpec
from lightkube import ApiError
from lightkube.models.meta_v1 import ObjectMeta

from velero import Velero, VeleroError, VeleroScheduleStatusError
from velero.crds import Schedule, ScheduleSpecModel

NAMESPACE = "test-namespace"
VELERO_BINARY = "/usr/local/bin/velero"


@pytest.fixture()
def mock_lightkube_client():
    """Mock the lightkube Client."""
    return MagicMock()


@pytest.fixture()
def velero():
    """Return a Velero instance."""
    return Velero(velero_binary_path=VELERO_BINARY, namespace=NAMESPACE)


def test_create_schedule_success(mock_lightkube_client, velero):
    """Check that a new schedule is created when none exists."""
    spec = VeleroBackupSpec(
        schedule="0 2 * * *",
        include_namespaces=["default"],
        ttl="168h",
    )
    labels = {"app": "test-app", "endpoint": "backup"}

    mock_created_schedule = MagicMock()
    mock_created_schedule.metadata.name = "test-app-backup-abc123"
    mock_lightkube_client.create.return_value = mock_created_schedule
    mock_lightkube_client.list.return_value = []

    result = velero.create_or_update_schedule(
        mock_lightkube_client,
        "test-app-backup-",
        spec,
        default_volumes_to_fs_backup=True,
        labels=labels,
    )

    assert result == "test-app-backup-abc123"
    mock_lightkube_client.create.assert_called_once()
    created_schedule = mock_lightkube_client.create.call_args[0][0]
    assert isinstance(created_schedule, Schedule)
    assert created_schedule.metadata is not None
    assert created_schedule.metadata.generateName == "test-app-backup-"
    assert created_schedule.metadata.namespace == NAMESPACE
    assert created_schedule.metadata.labels == labels
    assert created_schedule.spec is not None
    assert created_schedule.spec.schedule == "0 2 * * *"


def test_create_schedule_with_all_spec_fields(mock_lightkube_client, velero):
    """Check that all spec fields are properly mapped to the schedule."""
    spec = VeleroBackupSpec(
        schedule="0 */6 * * *",
        include_namespaces=["ns1", "ns2"],
        include_resources=["deployments", "services"],
        exclude_namespaces=["kube-system"],
        exclude_resources=["secrets"],
        label_selector={"app": "myapp"},
        ttl="720h",
        include_cluster_resources=True,
        paused=True,
        skip_immediately=True,
        use_owner_references_in_backup=True,
    )

    mock_created_schedule = MagicMock()
    mock_created_schedule.metadata.name = "full-spec-schedule"
    mock_lightkube_client.create.return_value = mock_created_schedule
    mock_lightkube_client.list.return_value = []

    velero.create_or_update_schedule(
        mock_lightkube_client,
        "full-spec-",
        spec,
        default_volumes_to_fs_backup=False,
    )

    created_schedule = mock_lightkube_client.create.call_args[0][0]
    assert created_schedule.spec.schedule == "0 */6 * * *"
    assert created_schedule.spec.paused is True
    assert created_schedule.spec.skipImmediately is True
    assert created_schedule.spec.useOwnerReferencesInBackup is True

    template = created_schedule.spec.template
    assert template.includedNamespaces == ["ns1", "ns2"]
    assert template.includedResources == ["deployments", "services"]
    assert template.excludedNamespaces == ["kube-system"]
    assert template.excludedResources == ["secrets"]
    assert template.labelSelector == {"matchLabels": {"app": "myapp"}}
    assert template.ttl == "720h"
    assert template.includeClusterResources is True
    assert template.defaultVolumesToFsBackup is False


def test_update_existing_schedule(mock_lightkube_client, velero):
    """Check that an existing schedule is updated when found by labels."""
    spec = VeleroBackupSpec(
        schedule="0 3 * * *",
        include_namespaces=["updated-ns"],
    )
    labels = {"app": "test-app", "endpoint": "backup"}

    mock_existing_schedule = MagicMock()
    mock_existing_schedule.metadata = ObjectMeta(name="existing-schedule-xyz")
    mock_existing_schedule.spec = ScheduleSpecModel(schedule="0 2 * * *")
    mock_lightkube_client.list.return_value = [mock_existing_schedule]
    mock_lightkube_client.get.return_value = mock_existing_schedule

    result = velero.create_or_update_schedule(
        mock_lightkube_client,
        "test-app-backup-",
        spec,
        default_volumes_to_fs_backup=True,
        labels=labels,
    )

    assert result == "existing-schedule-xyz"
    mock_lightkube_client.replace.assert_called_once()
    mock_lightkube_client.create.assert_not_called()


def test_create_schedule_no_schedule_in_spec_raises_error(mock_lightkube_client, velero):
    """Check that ValueError is raised when spec has no schedule."""
    spec = VeleroBackupSpec(include_namespaces=["default"])

    with pytest.raises(ValueError) as exc:
        velero.create_or_update_schedule(
            mock_lightkube_client,
            "test-",
            spec,
            default_volumes_to_fs_backup=True,
        )
    assert "Schedule cron expression is required" in str(exc.value)


def test_create_schedule_api_error(mock_lightkube_client, velero):
    """Check that VeleroError is raised on API error during creation."""
    spec = VeleroBackupSpec(schedule="0 2 * * *")

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": 500, "message": "Internal error"}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    mock_lightkube_client.list.return_value = []
    mock_lightkube_client.create.side_effect = api_error

    with pytest.raises(VeleroError):
        velero.create_or_update_schedule(
            mock_lightkube_client,
            "test-",
            spec,
            default_volumes_to_fs_backup=True,
        )


def test_delete_schedule_success(mock_lightkube_client, velero):
    """Check that schedule is deleted successfully."""
    velero.delete_schedule(mock_lightkube_client, "test-schedule")

    mock_lightkube_client.delete.assert_called_once_with(
        Schedule, "test-schedule", namespace=NAMESPACE
    )


def test_delete_schedule_not_found(mock_lightkube_client, velero):
    """Check that 404 error is handled gracefully."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": 404, "message": "not found"}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    api_error.status = MagicMock(code=404)
    mock_lightkube_client.delete.side_effect = api_error

    # Should not raise
    velero.delete_schedule(mock_lightkube_client, "nonexistent-schedule")


def test_delete_schedule_api_error(mock_lightkube_client, velero):
    """Check that VeleroError is raised on non-404 API error."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": 500, "message": "Internal error"}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    api_error.status = MagicMock(code=500)
    mock_lightkube_client.delete.side_effect = api_error

    with pytest.raises(VeleroError):
        velero.delete_schedule(mock_lightkube_client, "test-schedule")


def test_delete_schedule_by_labels_found(mock_lightkube_client, velero):
    """Check that schedule is deleted when found by labels."""
    labels = {"app": "test", "endpoint": "backup"}

    mock_schedule = MagicMock()
    mock_schedule.metadata = ObjectMeta(name="labeled-schedule", labels=labels)
    mock_schedule.spec = ScheduleSpecModel(schedule="0 2 * * *")
    mock_schedule.status = MagicMock(phase="Enabled", lastBackup=None)
    mock_lightkube_client.list.return_value = [mock_schedule]

    velero.delete_schedule_by_labels(mock_lightkube_client, labels)

    mock_lightkube_client.delete.assert_called_once_with(
        Schedule, "labeled-schedule", namespace=NAMESPACE
    )


def test_delete_schedule_by_labels_not_found(mock_lightkube_client, velero):
    """Check that no error when schedule not found by labels."""
    labels = {"app": "test", "endpoint": "backup"}
    mock_lightkube_client.list.return_value = []

    velero.delete_schedule_by_labels(mock_lightkube_client, labels)
    mock_lightkube_client.delete.assert_not_called()


def test_get_schedule_success(mock_lightkube_client, velero):
    """Check that schedule info is returned when found."""
    mock_schedule = MagicMock()
    mock_schedule.metadata = ObjectMeta(name="my-schedule", labels={"app": "test", "env": "prod"})
    mock_schedule.spec = ScheduleSpecModel(schedule="0 2 * * *", paused=False)
    mock_schedule.status = MagicMock(phase="Enabled", lastBackup="2025-01-15T02:00:00Z")
    mock_lightkube_client.get.return_value = mock_schedule

    result = velero.get_schedule(mock_lightkube_client, "my-schedule")

    assert result is not None
    assert result.name == "my-schedule"
    assert result.schedule == "0 2 * * *"
    assert result.phase == "Enabled"
    assert result.labels == {"app": "test", "env": "prod"}
    assert result.paused is False
    assert result.last_backup == "2025-01-15T02:00:00Z"


def test_get_schedule_not_found(mock_lightkube_client, velero):
    """Check that None is returned when schedule not found."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": 404, "message": "not found"}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    api_error.status = MagicMock(code=404)
    mock_lightkube_client.get.side_effect = api_error

    result = velero.get_schedule(mock_lightkube_client, "nonexistent")

    assert result is None


def test_get_schedule_api_error(mock_lightkube_client, velero):
    """Check that VeleroError is raised on non-404 API error."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": 500, "message": "Internal error"}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    api_error.status = MagicMock(code=500)
    mock_lightkube_client.get.side_effect = api_error

    with pytest.raises(VeleroError):
        velero.get_schedule(mock_lightkube_client, "error-schedule")


def test_list_schedules_success(mock_lightkube_client, velero):
    """Check that schedules are listed successfully."""
    mock_schedule_1 = MagicMock()
    mock_schedule_1.metadata = ObjectMeta(name="schedule-1", labels={"app": "app1"})
    mock_schedule_1.spec = ScheduleSpecModel(schedule="0 1 * * *", paused=False)
    mock_schedule_1.status = MagicMock(phase="Enabled", lastBackup="2025-01-15T01:00:00Z")

    mock_schedule_2 = MagicMock()
    mock_schedule_2.metadata = ObjectMeta(name="schedule-2", labels={"app": "app2"})
    mock_schedule_2.spec = ScheduleSpecModel(schedule="0 2 * * *", paused=True)
    mock_schedule_2.status = MagicMock(phase="Enabled", lastBackup=None)

    mock_lightkube_client.list.return_value = [mock_schedule_1, mock_schedule_2]

    result = velero.list_schedules(mock_lightkube_client)

    assert len(result) == 2
    assert result[0].name == "schedule-1"
    assert result[0].paused is False
    assert result[1].name == "schedule-2"
    assert result[1].paused is True


def test_list_schedules_with_labels(mock_lightkube_client, velero):
    """Check that schedules are filtered by labels."""
    labels = {"app": "specific-app"}

    mock_schedule = MagicMock()
    mock_schedule.metadata = ObjectMeta(name="filtered-schedule", labels=labels)
    mock_schedule.spec = ScheduleSpecModel(schedule="0 3 * * *", paused=False)
    mock_schedule.status = MagicMock(phase="Enabled", lastBackup=None)
    mock_lightkube_client.list.return_value = [mock_schedule]

    velero.list_schedules(mock_lightkube_client, labels=labels)

    mock_lightkube_client.list.assert_called_once_with(
        Schedule, namespace=NAMESPACE, labels=labels
    )


def test_list_schedules_api_error(mock_lightkube_client, velero):
    """Check that VeleroError is raised on API error."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {"code": 500, "message": "Internal error"}
    api_error = ApiError(request=MagicMock(), response=mock_response)
    mock_lightkube_client.list.side_effect = api_error

    with pytest.raises(VeleroError):
        velero.list_schedules(mock_lightkube_client)


def test_list_schedules_skip_invalid(mock_lightkube_client, velero):
    """Check that schedules with missing metadata/spec are skipped."""
    mock_valid_schedule = MagicMock()
    mock_valid_schedule.metadata = ObjectMeta(name="valid-schedule", labels={})
    mock_valid_schedule.spec = ScheduleSpecModel(schedule="0 4 * * *", paused=False)
    mock_valid_schedule.status = MagicMock(phase="Enabled", lastBackup=None)

    mock_invalid_no_metadata = MagicMock()
    mock_invalid_no_metadata.metadata = None
    mock_invalid_no_metadata.spec = ScheduleSpecModel(schedule="0 5 * * *")

    mock_invalid_no_spec = MagicMock()
    mock_invalid_no_spec.metadata = ObjectMeta(name="no-spec", labels={})
    mock_invalid_no_spec.spec = None

    mock_lightkube_client.list.return_value = [
        mock_valid_schedule,
        mock_invalid_no_metadata,
        mock_invalid_no_spec,
    ]

    result = velero.list_schedules(mock_lightkube_client)

    assert len(result) == 1
    assert result[0].name == "valid-schedule"


def test_schedule_status_error():
    """Check that VeleroScheduleStatusError has correct attributes."""
    error = VeleroScheduleStatusError(name="test-schedule", reason="FailedValidation")
    assert error.name == "test-schedule"
    assert error.reason == "FailedValidation"
    assert "test-schedule" in str(error)


def test_update_schedule_no_metadata(mock_lightkube_client, velero):
    """Check that update works when existing schedule has no metadata."""
    spec = VeleroBackupSpec(schedule="0 3 * * *")
    labels = {"app": "test-app", "endpoint": "backup"}

    # Schedule found by list
    mock_existing_in_list = MagicMock()
    mock_existing_in_list.metadata = ObjectMeta(name="existing-schedule")
    mock_existing_in_list.spec = ScheduleSpecModel(schedule="0 2 * * *")
    mock_existing_in_list.status = MagicMock(phase="Enabled", lastBackup=None)
    mock_lightkube_client.list.return_value = [mock_existing_in_list]

    # Schedule returned by get has no metadata
    mock_existing_schedule = MagicMock()
    mock_existing_schedule.metadata = None
    mock_existing_schedule.spec = ScheduleSpecModel(schedule="0 2 * * *")
    mock_lightkube_client.get.return_value = mock_existing_schedule

    result = velero.create_or_update_schedule(
        mock_lightkube_client,
        "test-app-backup-",
        spec,
        default_volumes_to_fs_backup=True,
        labels=labels,
    )

    assert result == "existing-schedule"
    mock_lightkube_client.replace.assert_called_once()


def test_create_schedule_no_name_in_metadata(mock_lightkube_client, velero):
    """Check that VeleroError is raised when created schedule has no name."""
    spec = VeleroBackupSpec(schedule="0 2 * * *")

    mock_created_schedule = MagicMock()
    mock_created_schedule.metadata = MagicMock()
    mock_created_schedule.metadata.name = None
    mock_lightkube_client.create.return_value = mock_created_schedule
    mock_lightkube_client.list.return_value = []

    with pytest.raises(VeleroError) as exc:
        velero.create_or_update_schedule(
            mock_lightkube_client,
            "test-",
            spec,
            default_volumes_to_fs_backup=True,
        )
    assert "no name in metadata" in str(exc.value)


def test_get_schedule_no_spec(mock_lightkube_client, velero):
    """Check that None is returned when schedule has no spec."""
    mock_schedule = MagicMock()
    mock_schedule.metadata = ObjectMeta(name="no-spec-schedule", labels={})
    mock_schedule.spec = None
    mock_lightkube_client.get.return_value = mock_schedule

    result = velero.get_schedule(mock_lightkube_client, "no-spec-schedule")

    assert result is None
