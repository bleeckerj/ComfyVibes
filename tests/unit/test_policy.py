"""Tests for MCP policy enforcement."""

import pytest

from comfy_mcp.mcp_server.policy import Policy


def test_policy_rejects_mutation_in_readonly() -> None:
    """Policy should reject mutating operations in readonly mode."""
    print("Test: Policy should reject mutation when readonly_mode is True.")
    policy = Policy(api_token=None, readonly_mode=True, max_workflow_bytes=10)

    with pytest.raises(PermissionError):
        policy.enforce_mutation(None)


def test_policy_enforces_payload_size() -> None:
    """Policy should reject payloads larger than max_workflow_bytes."""
    print("Test: Policy should reject payloads exceeding size limit.")
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=5)

    with pytest.raises(ValueError):
        policy.enforce_payload_size(10)
