from __future__ import annotations

import httpx
import pytest

from comfy_mcp.tui_client.http_router import HTTPToolRouter, _HTTPServerState


class _FakeClient:
    def __init__(self, response: httpx.Response, get_response: httpx.Response | None = None) -> None:
        self._response = response
        self._get_response = get_response or response

    async def post(self, url: str, json=None):  # noqa: ANN001
        return self._response

    async def get(self, url: str):  # noqa: ANN001
        return self._get_response

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_http_router_surfaces_json_error_payload() -> None:
    router = HTTPToolRouter([])
    router._client = _FakeClient(httpx.Response(400, json={"ok": False, "error": "Workflow hash mismatch"}))
    router._tool_map["workflows_run"] = _HTTPServerState(
        name="comfy",
        base_url="http://127.0.0.1:8181",
        prefixes=[],
        tools=[],
    )

    with pytest.raises(RuntimeError, match="Workflow hash mismatch"):
        await router.call_tool("workflows_run", {"workflow_id": "image_edit"})


@pytest.mark.asyncio
async def test_http_router_unwraps_success_envelope() -> None:
    router = HTTPToolRouter([])
    router._client = _FakeClient(httpx.Response(200, json={"ok": True, "result": {"status": "complete"}}))
    router._tool_map["workflows_wait"] = _HTTPServerState(
        name="comfy",
        base_url="http://127.0.0.1:8181",
        prefixes=[],
        tools=[],
    )

    result = await router.call_tool("workflows_wait", {"prompt_id": "abc"})
    assert result == {"status": "complete"}


@pytest.mark.asyncio
async def test_http_router_collects_server_health_statuses() -> None:
    router = HTTPToolRouter([])
    router._client = _FakeClient(
        httpx.Response(200, json={"ok": True}),
        get_response=httpx.Response(
            200,
            json={
                "status": "ok",
                "service_version": "0.1.0",
                "git_commit": "abc123def456",
                "git_branch": "codex/test",
                "git_dirty": False,
            },
        ),
    )
    router._server_states = [
        _HTTPServerState(
            name="comfy",
            base_url="http://127.0.0.1:8181",
            prefixes=[],
            tools=[],
        )
    ]

    statuses = await router.get_server_health_statuses()
    assert len(statuses) == 1
    entry = statuses[0]
    assert entry["name"] == "comfy"
    assert entry["base_url"] == "http://127.0.0.1:8181"
    assert entry["ok"] is True
    assert entry["status_code"] == 200
    assert isinstance(entry["payload"], dict)
    assert entry["payload"]["service_version"] == "0.1.0"
    assert entry["payload"]["git_commit"] == "abc123def456"
