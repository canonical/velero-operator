# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Velero Base Storage Provider class definitions."""

import base64
from abc import ABC, abstractmethod
from typing import Dict, Optional, Type

from pydantic import BaseModel, ConfigDict, ValidationError


class StorageProviderError(Exception):
    """Base class for storage provider exceptions."""


class StorageConfig(BaseModel):
    """Base Pydantic model for storage config."""

    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True)

    @classmethod
    def verror_to_str(cls, ve: ValidationError) -> str:
        """Convert a Pydantic ValidationError to a string."""
        error_messages = []
        for error in ve.errors():
            field = ".".join(map(str, error["loc"]))
            message = error["msg"].replace("Field ", "")
            error_messages.append(f"'{field}' {message}")
        return f"{cls.__name__} errors: " + "; ".join(error_messages)


class VeleroStorageProvider(ABC):
    """Base class for Velero storage provider."""

    def __init__(
        self, plugin_image: str, data: Dict[str, str], config_cls: Type[StorageConfig]
    ) -> None:
        self._plugin_image = plugin_image
        try:
            self._config = config_cls(**data)
        except ValidationError as ve:
            raise StorageProviderError(config_cls.verror_to_str(ve)) from ve

    @property
    @abstractmethod
    def plugin(self) -> str:  # pragma: no cover
        """Return the storage provider plugin name."""
        ...

    @property
    def plugin_image(self) -> str:
        """Return the storage provider plugin image."""
        return self._plugin_image

    @property
    @abstractmethod
    def bucket(self) -> str:  # pragma: no cover
        """Return the storage bucket name."""
        ...

    @property
    @abstractmethod
    def path(self) -> Optional[str]:  # pragma: no cover
        """Return the storage path."""
        ...

    @property
    @abstractmethod
    def secret_data(self) -> str:  # pragma: no cover
        """Return the base64 encoded secret data for the storage provider."""
        ...

    @property
    @abstractmethod
    def backup_location_config(self) -> Dict[str, str]:  # pragma: no cover
        """Return the configuration flags for the backup location."""
        ...

    @property
    @abstractmethod
    def volume_snapshot_location_config(self) -> Dict[str, str]:  # pragma: no cover
        """Return the configuration flags for the volume snapshot location."""
        ...

    def _encode_secret(self, secret: str) -> str:
        """Encode the secret data to base64."""
        return base64.b64encode(secret.encode("utf-8")).decode("utf-8")
