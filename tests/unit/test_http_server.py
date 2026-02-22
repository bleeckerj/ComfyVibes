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
