"""Auth helpers for MCP tools."""

from __future__ import annotations


def require_token(provided_token: str | None, expected_token: str | None) -> None:
    """Require a bearer token when configured."""
    if expected_token is None:
        return
    if not provided_token or provided_token != expected_token:
        raise PermissionError("Invalid API token")
