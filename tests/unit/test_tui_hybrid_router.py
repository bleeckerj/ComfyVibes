from __future__ import annotations

import asyncio

import pytest

from comfy_mcp.tui_client import hybrid_router as hybrid_router_module
from comfy_mcp.tui_client.config import ServerConfig
from comfy_mcp.tui_client.hybrid_router import HybridToolRouter
from comfy_mcp.tui_client.mcp_router import MCPToolRouter, ToolSpec
from comfy_mcp.tui_client.script_runner import _build_router as runner_build_router


class _FakeDelegateRouter:
    def __init__(
        self,
        specs: list[ToolSpec],
        *,
        response_prefix: str,
        health_statuses: list[dict] | None = None,
    ) -> None:
        self._specs = list(specs)
        self._response_prefix = response_prefix
        self._health_statuses = list(health_statuses or [])
        self.calls: list[tuple[str, dict]] = []
        self.connected = False
        self.closed = False

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.closed = True

    def list_tool_specs(self) -> list[ToolSpec]:
        return list(self._specs)

    async def call_tool(self, name: str, arguments: dict | None):
        self.calls.append((name, dict(arguments or {})))
        return {"router": self._response_prefix, "tool": name}

    async def get_server_health_statuses(self) -> list[dict]:
        return list(self._health_statuses)

    async def get_server_connection_statuses(self) -> list[dict]:
        return list(self._health_statuses)


@pytest.mark.asyncio
async def test_hybrid_router_connects_and_dispatches(monkeypatch) -> None:
    stdio_delegate = _FakeDelegateRouter(
        [ToolSpec(name="digester_get_signal", description="", input_schema={})],
        response_prefix="stdio",
    )
    http_delegate = _FakeDelegateRouter(
        [ToolSpec(name="photarium_search", description="", input_schema={})],
        response_prefix="http",
        health_statuses=[{"name": "photarium", "ok": True}],
    )

    monkeypatch.setattr(hybrid_router_module, "MCPToolRouter", lambda servers: stdio_delegate)
    monkeypatch.setattr(hybrid_router_module, "HTTPToolRouter", lambda servers: http_delegate)

    router = HybridToolRouter(
        http_servers=[ServerConfig(name="photarium", transport="http", http_url="http://127.0.0.1:8787")],
        stdio_servers=[ServerConfig(name="digester", command="python", args=["mcp_digester_server.py"])],
    )
    await router.connect()

    specs = router.list_tool_specs()
    assert {spec.name for spec in specs} == {"digester_get_signal", "photarium_search"}

    stdio_result = await router.call_tool("digester_get_signal", {"id": "sig-1"})
    http_result = await router.call_tool("photarium_search", {"query": "futures"})
    assert stdio_result["router"] == "stdio"
    assert http_result["router"] == "http"

    statuses = await router.get_server_health_statuses()
    assert statuses == [{"name": "photarium", "ok": True}]

    connection_statuses = await router.get_server_connection_statuses()
    assert connection_statuses == [{"name": "photarium", "ok": True}]

    await router.close()
    assert stdio_delegate.closed is True
    assert http_delegate.closed is True


@pytest.mark.asyncio
async def test_hybrid_router_rejects_duplicate_tool_names(monkeypatch) -> None:
    duplicate_spec = ToolSpec(name="search_digests", description="", input_schema={})
    stdio_delegate = _FakeDelegateRouter([duplicate_spec], response_prefix="stdio")
    http_delegate = _FakeDelegateRouter([duplicate_spec], response_prefix="http")

    monkeypatch.setattr(hybrid_router_module, "MCPToolRouter", lambda servers: stdio_delegate)
    monkeypatch.setattr(hybrid_router_module, "HTTPToolRouter", lambda servers: http_delegate)

    router = HybridToolRouter(
        http_servers=[ServerConfig(name="photarium", transport="http", http_url="http://127.0.0.1:8787")],
        stdio_servers=[ServerConfig(name="digester", command="python", args=["mcp_digester_server.py"])],
    )

    with pytest.raises(RuntimeError, match="Duplicate MCP tool name detected"):
        await router.connect()


@pytest.mark.asyncio
async def test_hybrid_router_closes_stdio_delegate_on_cancelled_connect(monkeypatch) -> None:
    class _CancelledDelegate(_FakeDelegateRouter):
        async def connect(self) -> None:
            self.connected = True
            raise asyncio.CancelledError()

    stdio_delegate = _CancelledDelegate([], response_prefix="stdio")
    http_delegate = _FakeDelegateRouter([], response_prefix="http")

    monkeypatch.setattr(hybrid_router_module, "MCPToolRouter", lambda servers: stdio_delegate)
    monkeypatch.setattr(hybrid_router_module, "HTTPToolRouter", lambda servers: http_delegate)

    router = HybridToolRouter(
        http_servers=[ServerConfig(name="workspace", transport="http", http_url="http://127.0.0.1:8777")],
        stdio_servers=[ServerConfig(name="photarium", command="node", args=["dist/index.js"])],
    )

    with pytest.raises(asyncio.CancelledError):
        await router.connect()

    assert stdio_delegate.closed is True


def test_build_router_uses_hybrid_for_mixed_servers() -> None:
    servers = [
        ServerConfig(name="comfy", transport="http", http_url="http://127.0.0.1:8181"),
        ServerConfig(name="digester", command="python", args=["mcp_digester_server.py"]),
    ]
    runner_router = runner_build_router(servers)

    assert isinstance(runner_router, HybridToolRouter)


def test_build_router_honors_explicit_stdio_even_with_http_url() -> None:
    servers = [
        ServerConfig(
            name="comfy",
            transport="stdio",
            command="python",
            args=["-m", "comfy_mcp.mcp_server.cli"],
            http_url="http://127.0.0.1:8181",
        )
    ]
    runner_router = runner_build_router(servers)

    assert isinstance(runner_router, MCPToolRouter)
