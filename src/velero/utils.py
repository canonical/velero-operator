# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Velero utilities."""

from dataclasses import dataclass
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from velero.crds.restore import ExistingResourcePolicy

LABEL_COMPONENT_INVALID_CHARS = r"[^a-zA-Z0-9\-_\./]"


@dataclass
class BackupInfo:
    """Data class to hold backup information."""

    uid: str
    name: str
    labels: Dict[str, str]
    annotations: Dict[str, str]
    phase: str
    start_timestamp: str
    completion_timestamp: Optional[str] = None


@dataclass
class ScheduleInfo:
    """Data class to hold schedule information."""

    name: str
    schedule: str
    phase: str
    labels: Dict[str, str]
    paused: bool = False
    last_backup: Optional[str] = None


class VeleroError(Exception):
    """Base class for Velero exceptions."""


class VeleroStatusError(VeleroError):
    """Exception raised for Velero status errors."""


class VeleroBackupStatusError(VeleroStatusError):
    """Exception raised for Velero backup status errors."""

    def __init__(self, name: str, reason: str) -> None:
        """Initialize the VeleroBackupStatusError with a name and reason."""
        super().__init__(f"Velero backup '{name}' failed: {reason}")
        self.name = name
        self.reason = reason


class VeleroRestoreStatusError(VeleroStatusError):
    """Exception raised for Velero restore status errors."""

    def __init__(self, name: str, reason: str) -> None:
        """Initialize the VeleroRestoreStatusError with a name and reason."""
        super().__init__(f"Velero restore '{name}' failed: {reason}")
        self.name = name
        self.reason = reason


class VeleroScheduleStatusError(VeleroStatusError):
    """Exception raised for Velero schedule status errors."""

    def __init__(self, name: str, reason: str) -> None:
        """Initialize the VeleroScheduleStatusError with a name and reason."""
        super().__init__(f"Velero schedule '{name}' failed: {reason}")
        self.name = name
        self.reason = reason


class VeleroCLIError(VeleroError):
    """Exception raised for Velero CLI errors."""


class RestoreParams(BaseModel):
    """Structured parameters for the restore action."""

    model_config = ConfigDict(populate_by_name=True)

    backup_uid: str = Field(validation_alias="backup-uid")
    existing_resource_policy: ExistingResourcePolicy = Field(
        validation_alias="existing-resource-policy", default=ExistingResourcePolicy.No
    )
    include_namespaces: List[str] | None = Field(
        validation_alias="include-namespaces", default=None
    )
    exclude_namespaces: List[str] | None = Field(
        validation_alias="exclude-namespaces", default=None
    )
    include_resources: List[str] | None = Field(validation_alias="include-resources", default=None)
    exclude_resources: List[str] | None = Field(validation_alias="exclude-resources", default=None)
    selector: Optional[Dict[str, str]] | None = Field(validation_alias="selector", default=None)
    or_selector: Optional[Dict[str, str]] | None = Field(
        validation_alias="or-selector", default=None
    )

    @classmethod
    def _check_component(cls, value: str) -> None:
        """Check if the value contains invalid characters."""
        import re

        if not value.strip():
            raise ValueError("Value cannot be empty or whitespace")
        if re.search(LABEL_COMPONENT_INVALID_CHARS, value):
            raise ValueError(f"Value '{value}' contains invalid characters")

    @field_validator("backup_uid", mode="before")
    @classmethod
    def validate_backup_uid(cls, v):
        """Ensure backup_uid is not empty after stripping whitespace."""
        if not v.strip():
            raise ValueError("backup-uid must not be empty")
        return v

    @model_validator(mode="after")
    def check_pairs(self):
        """Ensure that selector and or_selector are not both set."""
        if self.selector is not None and self.or_selector is not None:
            raise ValueError("Cannot specify both 'selector' and 'or-selector' parameters")

        if self.include_namespaces is not None and self.exclude_namespaces is not None:
            raise ValueError(
                "Cannot specify both 'include-namespaces' and 'exclude-namespaces' parameters"
            )

        if self.include_resources is not None and self.exclude_resources is not None:
            raise ValueError(
                "Cannot specify both 'include-resources' and 'exclude-resources' parameters"
            )

        return self

    @field_validator(
        "include_namespaces",
        "exclude_namespaces",
        "include_resources",
        "exclude_resources",
        mode="before",
    )
    @classmethod
    def split_comma_separated(cls, v):
        """Split comma-separated strings into lists."""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @classmethod
    def _parse_selector(
        cls,
        value,
        field_name: str,
        separator: str,
    ):
        """Parse selector-like values as key=value pairs."""
        items = [item.strip() for item in value.split(separator) if item.strip()]
        if not items:
            raise ValueError(
                f"Invalid {field_name} format: '{value}' "
                f"(expected key=value pairs separated by '{separator.strip()}')"
            )

        parsed = {}

        for item in items:
            if "=" not in item:
                raise ValueError(f"Invalid {field_name} format: '{item}' (expected key=value)")

            key, item_value = item.split("=", 1)
            cls._check_component(key)
            cls._check_component(item_value)
            key = key.strip()
            item_value = item_value.strip()
            parsed[key] = item_value

        return parsed or None

    @field_validator("selector", mode="before")
    @classmethod
    def parse_selector(cls, v):
        """Parse the selector string into a dictionary."""
        return cls._parse_selector(v, field_name="selector", separator=",")

    @field_validator("or_selector", mode="before")
    @classmethod
    def parse_or_selector(cls, v):
        """Parse the or-selector string into a list of dictionaries."""
        return cls._parse_selector(v, field_name="or-selector", separator=" or ")
