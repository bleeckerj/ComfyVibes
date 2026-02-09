from __future__ import annotations

import types
from contextlib import asynccontextmanager

import pytest

from comfy_mcp.tui_client.mcp_router import MCPToolRouter, ToolSpec
from comfy_mcp.tui_client.config import ServerConfig


class FakeSession:
    def __init__(self, tools):
        self._tools = tools
        self.calls = []

    async def initialize(self):
        return None

    async def list_tools(self):
        return self._tools

    async def call_tool(self, name, payload):
        self.calls.append((name, payload))
        return {"ok": True, "name": name, "payload": payload}


def _install_fake_mcp_modules(monkeypatch, tools, export_client_symbols: bool = True):
    fake_client = types.ModuleType("mcp.client")
    fake_stdio = types.ModuleType("mcp.client.stdio")
    fake_session = types.ModuleType("mcp.client.session")

    class StdioServerParameters:
        def __init__(self, command, args, env=None, cwd=None):
            self.command = command
            self.args = args
            self.env = env
            self.cwd = cwd

    @asynccontextmanager
    async def stdio_client(params):
        yield ("read", "write")

    class ClientSession:
        def __init__(self, read_stream, write_stream):
            self._session = FakeSession(tools)

        async def __aenter__(self):
            return self._session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    if export_client_symbols:
        fake_client.ClientSession = ClientSession
        fake_client.StdioServerParameters = StdioServerParameters
    fake_stdio.stdio_client = stdio_client
    fake_stdio.StdioServerParameters = StdioServerParameters
    fake_session.ClientSession = ClientSession

    monkeypatch.setitem(__import__("sys").modules, "mcp.client", fake_client)
    monkeypatch.setitem(__import__("sys").modules, "mcp.client.stdio", fake_stdio)
    monkeypatch.setitem(__import__("sys").modules, "mcp.client.session", fake_session)


def _install_failing_stdio(monkeypatch):
    fake_client = types.ModuleType("mcp.client")
    fake_stdio = types.ModuleType("mcp.client.stdio")
    fake_session = types.ModuleType("mcp.client.session")

    class StdioServerParameters:
        def __init__(self, command, args, env=None, cwd=None):
            self.command = command
            self.args = args
            self.env = env
            self.cwd = cwd

    @asynccontextmanager
    async def stdio_client(params):
        raise RuntimeError("boom")
        yield

    class ClientSession:
        def __init__(self, read_stream, write_stream):
            pass

    fake_stdio.StdioServerParameters = StdioServerParameters
    fake_stdio.stdio_client = stdio_client
    fake_session.ClientSession = ClientSession
    monkeypatch.setitem(__import__("sys").modules, "mcp.client", fake_client)
    monkeypatch.setitem(__import__("sys").modules, "mcp.client.stdio", fake_stdio)
    monkeypatch.setitem(__import__("sys").modules, "mcp.client.session", fake_session)


@pytest.mark.asyncio
async def test_router_connect_and_call(monkeypatch):
    tools = [
        {"name": "workflows.list", "description": "List workflows", "inputSchema": {"type": "object"}},
    ]
    _install_fake_mcp_modules(monkeypatch, tools)

    router = MCPToolRouter(
        [
            ServerConfig(
                name="comfy",
                command="python",
                args=["-m", "comfy_mcp.mcp_server.cli"],
                tool_prefixes=["workflows_"],
            )
        ]
    )

    await router.connect()
    specs = router.list_tool_specs()
    assert specs == [ToolSpec(name="workflows.list", description="List workflows", input_schema={"type": "object"})]

    result = await router.call_tool("workflows.list", {"limit": 1})
    assert result["ok"] is True
    assert result["payload"] == {"limit": 1}

    await router.close()


@pytest.mark.asyncio
async def test_router_unknown_tool(monkeypatch):
    _install_fake_mcp_modules(monkeypatch, [])
    router = MCPToolRouter([ServerConfig(name="comfy", command="python")])
    await router.connect()
    with pytest.raises(RuntimeError):
        await router.call_tool("missing.tool", {})
    await router.close()


@pytest.mark.asyncio
async def test_router_connect_with_new_mcp_import_layout(monkeypatch):
    tools = [{"name": "workflows.list", "description": "List workflows", "inputSchema": {"type": "object"}}]
    _install_fake_mcp_modules(monkeypatch, tools, export_client_symbols=False)
    router = MCPToolRouter([ServerConfig(name="comfy", command="python")])
    await router.connect()
    specs = router.list_tool_specs()
    assert len(specs) == 1
    assert specs[0].name == "workflows.list"
    await router.close()


@pytest.mark.asyncio
async def test_router_error_message_includes_server(monkeypatch):
    _install_failing_stdio(monkeypatch)
    server = ServerConfig(name="photarium", command="node", args=["dist/index.js"], cwd="/tmp")
    router = MCPToolRouter([server])
    with pytest.raises(RuntimeError) as exc:
        await router.connect()
    text = str(exc.value)
    assert "photarium" in text
    assert "dist/index.js" in text


@pytest.mark.asyncio
async def test_router_close_suppresses_cancel_scope_mismatch():
    router = MCPToolRouter([])

    class BadStack:
        async def __aexit__(self, exc_type, exc, tb):
            raise RuntimeError("Attempted to exit cancel scope in a different task than it was entered in")

    router._stack = BadStack()
    await router.close()
