from __future__ import annotations

import httpx
import pytest

from comfy_mcp.tui_client.config import ServerConfig
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


class _FakeDiscoveryClient:
    async def get(self, url: str):  # noqa: ANN001
        if url.endswith("/health"):
            return httpx.Response(
                200,
                json={"status": "ok"},
                request=httpx.Request("GET", url),
            )
        if url == "http://127.0.0.1:9001/tools":
            return httpx.Response(
                200,
                json={
                    "tools": [
                        {
                            "name": "dup_tool",
                            "description": "dup via server1",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                    ]
                },
                request=httpx.Request("GET", url),
            )
        if url == "http://127.0.0.1:9002/tools":
            return httpx.Response(
                200,
                json={
                    "tools": [
                        {
                            "name": "dup_tool",
                            "description": "dup via server2",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                    ]
                },
                request=httpx.Request("GET", url),
            )
        return httpx.Response(
            404,
            json={"error": "not found"},
            request=httpx.Request("GET", url),
        )

    async def post(self, url: str, json=None):  # noqa: ANN001
        return httpx.Response(200, json={"ok": True, "result": {"url": url}})

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
async def test_http_router_enriches_missing_workflows_run_overrides_error() -> None:
    router = HTTPToolRouter([])
    router._client = _FakeClient(httpx.Response(400, json={"ok": False, "error": "Field required: overrides"}))
    router._tool_map["workflows_run"] = _HTTPServerState(
        name="comfy",
        base_url="http://127.0.0.1:8181",
        prefixes=[],
        tools=[],
    )

    with pytest.raises(RuntimeError, match="explicit overrides object"):
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
async def test_http_router_parses_json_text_content_from_http_result() -> None:
    router = HTTPToolRouter([])
    router._client = _FakeClient(
        httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                '{"mode":"catalog","previewUrl":"http://127.0.0.1:8788/ads/preview'
                                '?ids=fashion-8bit-pants-skyscraper"}'
                            ),
                        }
                    ]
                },
            },
        )
    )
    router._tool_map["editorial_ads_preview"] = _HTTPServerState(
        name="editorial",
        base_url="http://127.0.0.1:8788",
        prefixes=[],
        tools=[],
    )

    result = await router.call_tool("editorial_ads_preview", {"adId": "fashion-8bit-pants-skyscraper"})
    assert result == {
        "mode": "catalog",
        "previewUrl": "http://127.0.0.1:8788/ads/preview?ids=fashion-8bit-pants-skyscraper",
    }


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


@pytest.mark.asyncio
async def test_http_router_connection_statuses_include_transport() -> None:
    router = HTTPToolRouter([])
    router._client = _FakeClient(
        httpx.Response(200, json={"ok": True}),
        get_response=httpx.Response(200, json={"status": "ok"}),
    )
    router._server_states = [
        _HTTPServerState(
            name="photarium",
            base_url="http://127.0.0.1:8787",
            prefixes=[],
            tools=[],
        )
    ]

    statuses = await router.get_server_connection_statuses()
    assert statuses == [
        {
            "name": "photarium",
            "base_url": "http://127.0.0.1:8787",
            "health_url": "http://127.0.0.1:8787/health",
            "ok": True,
            "status_code": 200,
            "payload": {"status": "ok"},
            "error": None,
            "transport": "http",
        }
    ]


@pytest.mark.asyncio
async def test_http_router_auto_namespaces_duplicate_tool_names(monkeypatch) -> None:
    monkeypatch.setattr("comfy_mcp.tui_client.http_router.httpx.AsyncClient", lambda timeout: _FakeDiscoveryClient())
    router = HTTPToolRouter(
        [
            ServerConfig(name="editorial", transport="http", http_url="http://127.0.0.1:9001"),
            ServerConfig(name="digester", transport="http", http_url="http://127.0.0.1:9002"),
        ]
    )
    await router.connect()
    try:
        tool_names = [spec.name for spec in router.list_tool_specs()]
        assert "dup_tool" in tool_names
        assert "digester__dup_tool" in tool_names

        canonical = await router.call_tool("dup_tool", {})
        aliased = await router.call_tool("digester__dup_tool", {})
        assert "127.0.0.1:9001" in canonical["url"]
        assert "127.0.0.1:9002" in aliased["url"]
    finally:
        await router.close()
