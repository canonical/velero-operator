# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Configuration for the charm."""

from charms.data_platform_libs.v0.data_models import BaseConfigModel
from pydantic import field_validator


class CharmConfig(BaseConfigModel):
    """Manager for the structured configuration."""

    velero_image: str
    velero_aws_plugin_image: str
    velero_azure_plugin_image: str
    use_node_agent: bool

    @field_validator("*", mode="before")
    @classmethod
    def blank_string(cls, value):
        """Convert empty strings to None."""
        if value == "":
            return None
        return value
