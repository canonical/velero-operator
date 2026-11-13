# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Configuration for the charm."""

from typing import Literal

from charms.data_platform_libs.v0.data_models import BaseConfigModel
from pydantic import field_validator


class CharmConfig(BaseConfigModel):
    """Manager for the structured configuration."""

    velero_image: str
    velero_aws_plugin_image: str
    velero_azure_plugin_image: str
    velero_gcp_plugin_image: str
    use_node_agent: bool
    default_volumes_to_fs_backup: bool
    aws_checksum_algorithm: Literal["auto", "disabled", "CRC32", "CRC32C", "SHA1", "SHA256"]

    @field_validator("*", mode="before")
    @classmethod
    def blank_string(cls, value):
        """Convert empty strings to None."""
        if value == "":
            return None
        return value

    @property
    def velero_checksum_algorithm(self) -> str | None:
        """Translate the charm setting into Velero's checksum algorithm."""
        if self.aws_checksum_algorithm == "auto":
            return None
        if self.aws_checksum_algorithm == "disabled":
            return ""
        return self.aws_checksum_algorithm
