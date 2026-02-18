"""Tests for MCP server tool listing helpers."""

from __future__ import annotations

from comfy_mcp.mcp_server.server import _list_tool_payloads


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

