from __future__ import annotations

from fastapi.testclient import TestClient

from comfy_mcp.workspace_server.http_server import create_http_app


def test_workspace_http_lists_tools(monkeypatch) -> None:
    monkeypatch.setattr(
        "comfy_mcp.workspace_server.http_server.build_workspace_tool_registry",
        lambda: (
            [
                {
                    "name": "workspace_path_stat",
                    "description": "Stat a path.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                }
            ],
            {
                "workspace_path_stat": lambda path: {"path": path, "found": True},
                "workspace_roots": lambda: {"roots": ["/tmp"]},
            },
        ),
    )
    app = create_http_app()
    client = TestClient(app)

    tools = client.get("/tools")
    assert tools.status_code == 200
    payload = tools.json()
    assert payload["tools"][0]["name"] == "workspace_path_stat"


def test_workspace_http_rejects_unknown_parameters(monkeypatch) -> None:
    monkeypatch.setattr(
        "comfy_mcp.workspace_server.http_server.build_workspace_tool_registry",
        lambda: (
            [
                {
                    "name": "workspace_path_stat",
                    "description": "Stat a path.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                }
            ],
            {
                "workspace_path_stat": lambda path: {"path": path, "found": True},
                "workspace_roots": lambda: {"roots": ["/tmp"]},
            },
        ),
    )
    app = create_http_app()
    client = TestClient(app)

    response = client.post("/tools/workspace_path_stat", json={"path": "/tmp/demo", "unexpected": True})
    assert response.status_code == 400
    payload = response.json()
    assert payload["ok"] is False
    assert "Unknown parameter(s): unexpected" in payload["error"]


def test_workspace_http_rejects_missing_required_parameters(monkeypatch) -> None:
    monkeypatch.setattr(
        "comfy_mcp.workspace_server.http_server.build_workspace_tool_registry",
        lambda: (
            [
                {
                    "name": "workspace_file_write",
                    "description": "Write a file.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                }
            ],
            {
                "workspace_file_write": lambda path, content: {"path": path, "bytes": len(content)},
                "workspace_roots": lambda: {"roots": ["/tmp"]},
            },
        ),
    )
    app = create_http_app()
    client = TestClient(app)

    response = client.post("/tools/workspace_file_write", json={"path": "/tmp/demo.txt"})
    assert response.status_code == 400
    payload = response.json()
    assert payload["ok"] is False
    assert "Missing required parameter(s): content" in payload["error"]


def test_workspace_http_unwraps_arguments_wrapper(monkeypatch) -> None:
    monkeypatch.setattr(
        "comfy_mcp.workspace_server.http_server.build_workspace_tool_registry",
        lambda: (
            [
                {
                    "name": "workspace_file_read",
                    "description": "Read a file.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                }
            ],
            {
                "workspace_file_read": lambda path: {"path": path, "content": "hello"},
                "workspace_roots": lambda: {"roots": ["/tmp"]},
            },
        ),
    )
    app = create_http_app()
    client = TestClient(app)

    response = client.post("/tools/workspace_file_read", json={"arguments": {"path": "/tmp/demo.txt"}})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"] == {"path": "/tmp/demo.txt", "content": "hello"}
