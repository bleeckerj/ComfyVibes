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


def test_policy_requires_confirmation_for_remote_mutations() -> None:
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10)

    with pytest.raises(PermissionError, match="Confirmation required"):
        policy.enforce_remote_mutation(None, confirmed=False, operation="comfy_queue_delete")

    policy.enforce_remote_mutation(None, confirmed=True, operation="comfy_queue_delete")


def test_policy_requires_configured_token_before_confirmation() -> None:
    policy = Policy(api_token="secret", readonly_mode=False, max_workflow_bytes=10)

    with pytest.raises(PermissionError, match="Invalid API token"):
        policy.enforce_remote_mutation(None, confirmed=True, operation="comfy_memory_free")

    with pytest.raises(PermissionError, match="Invalid API token"):
        policy.enforce_remote_mutation("wrong", confirmed=True, operation="comfy_memory_free")

    policy.enforce_remote_mutation("secret", confirmed=True, operation="comfy_memory_free")


def test_policy_readonly_precedes_confirmation_and_token() -> None:
    policy = Policy(api_token="secret", readonly_mode=True, max_workflow_bytes=10)

    with pytest.raises(PermissionError, match="Readonly mode"):
        policy.enforce_remote_mutation(None, confirmed=False, operation="comfy_upload_asset")
