"""Tests for MCP server tool listing helpers."""

from __future__ import annotations

import pytest

from comfy_mcp.mcp_server.server import _list_tool_payloads, validate_tool_arguments


def test_list_tool_payloads_supports_name_filter() -> None:
    tool_defs = [
        {"name": "workflows_run", "description": "Run workflow", "inputSchema": {"type": "object"}},
        {"name": "workflows_list", "description": "List workflows", "inputSchema": {"type": "object"}},
    ]

    payload = _list_tool_payloads(tool_defs, name="workflows_run")

    assert payload["count"] == 1
    assert payload["tools"][0]["name"] == "workflows_run"


def test_list_tool_payloads_can_exclude_schema() -> None:
    tool_defs = [
        {"name": "workflows_run", "description": "Run workflow", "inputSchema": {"type": "object"}},
        {"name": "comfy_queue_get", "description": "Queue", "inputSchema": {"type": "object"}},
    ]

    payload = _list_tool_payloads(tool_defs, prefix="workflows_", include_schema=False)

    assert payload["count"] == 1
    assert payload["tools"][0] == {
        "name": "workflows_run",
        "description": "Run workflow",
    }


def test_validate_tool_arguments_rejects_unknown_values() -> None:
    tool = {
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "workflow_id": {"type": "string"}},
            "required": ["path", "workflow_id"],
        }
    }

    with pytest.raises(ValueError, match=r"Unknown parameter\(s\): overrides"):
        validate_tool_arguments(tool, {"path": "/tmp/a.png", "workflow_id": "demo", "overrides": {"x": 1}})


def test_validate_tool_arguments_unwraps_payload_and_camelcase() -> None:
    tool = {
        "inputSchema": {
            "type": "object",
            "properties": {"image_id": {"type": "string"}, "workflow_id": {"type": "string"}},
            "required": ["image_id", "workflow_id"],
        }
    }

    normalized = validate_tool_arguments(
        tool,
        {"input": {"imageId": "img-123", "workflowId": "wf-123"}},
    )

    assert normalized == {"image_id": "img-123", "workflow_id": "wf-123"}
