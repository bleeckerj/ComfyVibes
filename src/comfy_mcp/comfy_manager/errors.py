"""Structured ComfyUI Manager errors."""

from __future__ import annotations


class ManagerError(RuntimeError):
    """Base error for the remote Manager API."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        endpoint: str | None = None,
        category: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.endpoint = endpoint
        self.category = category


class ManagerNotInstalledError(ManagerError):
    """Raised when the remote host does not expose Manager v4."""


class ManagerConnectionError(ManagerError):
    """Raised when the remote ComfyUI host cannot be reached."""


class ManagerAPIError(ManagerError):
    """Raised when Manager returns an API or validation failure."""
