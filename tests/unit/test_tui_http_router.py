from __future__ import annotations

import httpx
import pytest

from comfy_mcp.tui_client.http_router import HTTPToolRouter, _HTTPServerState


class _FakeClient:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    async def post(self, url: str, json=None):  # noqa: ANN001
        return self._response

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
