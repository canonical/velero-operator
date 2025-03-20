# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Velero related code."""

import logging
import subprocess

logger = logging.getLogger(__name__)


class VeleroError(Exception):
    """Base class for Velero exceptions."""


class Velero:
    """Wrapper for the Velero binary."""

    def __init__(self, velero_binary_path: str, namespace: str, velero_image: str) -> None:
        """Initialize the Velero class.

        The class provides a python API to interact with the Velero binary, supporting
        operations like install, add backup location, create backup, restore backup, etc.

        Args:
            velero_binary_path: The path to the Velero binary.
            namespace: The namespace where Velero is installed.
            velero_image: The Velero image to use.
        """
        self._velero_binary_path = velero_binary_path
        self._namespace = namespace
        self._velero_image = velero_image

    @property
    def _velero_install_flags(self) -> list:
        """Return the default Velero install flags."""
        return [
            f"--namespace={self._namespace}",
            f"--image={self._velero_image}",
            "--no-default-backup-location",
            "--no-secret",
            "--use-volume-snapshots=false",
        ]

    def install(self, use_node_agent: bool) -> None:
        """Install Velero."""
        install_msg = (
            "Installing the Velero with the following settings:\n"
            f"Image: {self._velero_image}\n"
            f"Namespace: {self._namespace}\n"
            f"Node-agent enabled: {use_node_agent}"
        )
        try:
            logger.info(install_msg)
            subprocess.check_call(
                [
                    self._velero_binary_path,
                    "install",
                    *self._velero_install_flags,
                    f"--use-node-agent={use_node_agent}",
                ]
            )
        except subprocess.CalledProcessError as cpe:
            error_msg = f"'velero install' command returned non-zero exit code: {cpe.returncode}."
            logging.error(error_msg)
            logging.error("stdout: %s", {cpe.stdout})
            logging.error("stderr: %s", {cpe.stderr})

            raise VeleroError(error_msg) from cpe

    def remove(self) -> None:
        """Remove Velero."""
        pass
