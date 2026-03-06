from __future__ import annotations

from fastapi.testclient import TestClient

from comfy_mcp.config.models import AppConfig
from comfy_mcp.mcp_server.http_server import create_http_app


def test_health_and_version_include_runtime_info(monkeypatch) -> None:
    monkeypatch.setattr(
        "comfy_mcp.mcp_server.http_server.collect_runtime_info",
        lambda service_name: {
            "service": service_name,
            "service_version": "0.1.0-test",
            "git_commit": "abc123def456",
            "git_branch": "codex/test",
            "git_dirty": True,
            "python_version": "3.14.2",
            "started_at": "2026-02-21T12:00:00+00:00",
        },
    )
    app = create_http_app(AppConfig())
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    health_payload = health.json()
    assert health_payload["status"] == "ok"
    assert health_payload["service"] == "comfy-mcp-http"
    assert health_payload["service_version"] == "0.1.0-test"
    assert health_payload["git_commit"] == "abc123def456"
    assert isinstance(health_payload["tool_count"], int)
    assert health_payload["tool_count"] > 0

    version = client.get("/version")
    assert version.status_code == 200
    version_payload = version.json()
    assert version_payload["service"] == "comfy-mcp-http"
    assert version_payload["service_version"] == "0.1.0-test"
    assert version_payload["git_commit"] == "abc123def456"
    assert isinstance(version_payload["tool_count"], int)
    assert version_payload["tool_count"] > 0


def test_call_tool_filters_unexpected_kwargs(monkeypatch) -> None:
    def strict_handler(path: str, workflow_id: str) -> dict:
        return {"path": path, "workflow_id": workflow_id}

    monkeypatch.setattr(
        "comfy_mcp.mcp_server.http_server.build_tool_registry",
        lambda config: (
            [{"name": "workflows_import_from_artifact", "description": "", "inputSchema": {"type": "object"}}],
            {"workflows_import_from_artifact": strict_handler},
        ),
    )
    app = create_http_app(AppConfig())
    client = TestClient(app)

    response = client.post(
        "/tools/workflows_import_from_artifact",
        json={"path": "/tmp/fake.png", "workflow_id": "demo", "overrides": {"x": 1}},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"] == {"path": "/tmp/fake.png", "workflow_id": "demo"}


def test_call_tool_unwraps_input_wrapper(monkeypatch) -> None:
    def strict_handler(image_path: str, aspect_ratio: str) -> dict:
        return {"image_path": image_path, "aspect_ratio": aspect_ratio}

    monkeypatch.setattr(
        "comfy_mcp.mcp_server.http_server.build_tool_registry",
        lambda config: (
            [{"name": "workflows_run_aspect_ratio_adjustment", "description": "", "inputSchema": {"type": "object"}}],
            {"workflows_run_aspect_ratio_adjustment": strict_handler},
        ),
    )
    app = create_http_app(AppConfig())
    client = TestClient(app)

    response = client.post(
        "/tools/workflows_run_aspect_ratio_adjustment",
        json={"input": {"image_path": "/tmp/fake.png", "aspect_ratio": "3:2"}},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"] == {"image_path": "/tmp/fake.png", "aspect_ratio": "3:2"}


def test_call_tool_unwraps_nested_arguments_wrapper(monkeypatch) -> None:
    def strict_handler(image_path: str, aspect_ratio: str) -> dict:
        return {"image_path": image_path, "aspect_ratio": aspect_ratio}

    monkeypatch.setattr(
        "comfy_mcp.mcp_server.http_server.build_tool_registry",
        lambda config: (
            [{"name": "workflows_run_aspect_ratio_adjustment", "description": "", "inputSchema": {"type": "object"}}],
            {"workflows_run_aspect_ratio_adjustment": strict_handler},
        ),
    )
    app = create_http_app(AppConfig())
    client = TestClient(app)

    response = client.post(
        "/tools/workflows_run_aspect_ratio_adjustment",
        json={"arguments": {"input": {"image_path": "/tmp/fake.png", "aspect_ratio": "3:2"}}},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"] == {"image_path": "/tmp/fake.png", "aspect_ratio": "3:2"}


def test_call_tool_maps_camelcase_to_snakecase(monkeypatch) -> None:
    def strict_handler(image_id: str, workflow_id: str) -> dict:
        return {"image_id": image_id, "workflow_id": workflow_id}

    monkeypatch.setattr(
        "comfy_mcp.mcp_server.http_server.build_tool_registry",
        lambda config: (
            [{"name": "workflows_import_from_photarium", "description": "", "inputSchema": {"type": "object"}}],
            {"workflows_import_from_photarium": strict_handler},
        ),
    )
    app = create_http_app(AppConfig())
    client = TestClient(app)

    response = client.post(
        "/tools/workflows_import_from_photarium",
        json={"arguments": {"imageId": "img-123", "workflowId": "wf-123"}},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"] == {"image_id": "img-123", "workflow_id": "wf-123"}
