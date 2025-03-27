# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Configuration for the charm."""

from pydantic import BaseModel, field_validator

VELERO_IMAGE_CONFIG_KEY = "velero-image"
VELERO_AWS_PLUGIN_CONFIG_KEY = "velero-aws-plugin-image"
VELERO_AZURE_PLUGIN_CONFIG_KEY = "velero-azure-plugin-image"
USE_NODE_AGENT_CONFIG_KEY = "use-node-agent"


class BaseConfigModel(BaseModel):
    """Class to be used for defining the structured configuration options."""

    def __getitem__(self, x):
        """Return the item using the notation instance[key]."""
        return getattr(self, x.replace("-", "_"))


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
