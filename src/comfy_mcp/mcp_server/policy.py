"""Policy and size limit enforcement for MCP tool handlers."""

from __future__ import annotations

from dataclasses import dataclass

from comfy_mcp.mcp_server.auth import require_token


@dataclass(frozen=True)
class Policy:
    """MCP tool policy configuration."""

    api_token: str | None
    readonly_mode: bool
    max_workflow_bytes: int

    def enforce_mutation(self, provided_token: str | None) -> None:
        """Enforce auth for mutating operations."""
        if self.readonly_mode:
            raise PermissionError("Readonly mode is enabled")
        require_token(provided_token, self.api_token)

    def enforce_confirmation(self, confirmed: bool, operation: str) -> None:
        """Require explicit confirmation for a named remote operation."""
        if not confirmed:
            raise PermissionError(f"Confirmation required for {operation}")

    def enforce_remote_mutation(
        self,
        provided_token: str | None,
        *,
        confirmed: bool,
        operation: str,
    ) -> None:
        """Enforce readonly, token, and confirmation gates for remote writes."""
        self.enforce_mutation(provided_token)
        self.enforce_confirmation(confirmed, operation)

    def enforce_payload_size(self, payload_size: int) -> None:
        """Enforce payload size limits."""
        if payload_size > self.max_workflow_bytes:
            raise ValueError("Workflow payload exceeds size limit")
