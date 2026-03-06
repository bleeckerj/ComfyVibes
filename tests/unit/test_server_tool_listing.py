"""Tests for MCP server tool listing helpers."""

from __future__ import annotations

from comfy_mcp.mcp_server.server import _filter_supported_kwargs, _list_tool_payloads


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


def test_filter_supported_kwargs_drops_unknown_values() -> None:
    def handler(path: str, workflow_id: str) -> dict:
        return {"path": path, "workflow_id": workflow_id}

    filtered = _filter_supported_kwargs(
        handler,
        {"path": "/tmp/a.png", "workflow_id": "demo", "overrides": {"x": 1}},
    )

    assert filtered == {"path": "/tmp/a.png", "workflow_id": "demo"}


def test_filter_supported_kwargs_unwraps_input_payload() -> None:
    def handler(image_path: str, aspect_ratio: str) -> dict:
        return {"image_path": image_path, "aspect_ratio": aspect_ratio}

    filtered = _filter_supported_kwargs(
        handler,
        {"input": {"image_path": "/tmp/a.png", "aspect_ratio": "3:2"}},
    )

    assert filtered == {"image_path": "/tmp/a.png", "aspect_ratio": "3:2"}


def test_filter_supported_kwargs_accepts_camelcase_aliases() -> None:
    def handler(image_id: str, workflow_id: str) -> dict:
        return {"image_id": image_id, "workflow_id": workflow_id}

    filtered = _filter_supported_kwargs(
        handler,
        {"imageId": "img-123", "workflowId": "wf-123"},
    )

    assert filtered == {"image_id": "img-123", "workflow_id": "wf-123"}
