from __future__ import annotations

import asyncio
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
    captured = {}

    class StdioServerParameters:
        def __init__(self, command, args, env=None, cwd=None):
            self.command = command
            self.args = args
            self.env = env
            self.cwd = cwd

    @asynccontextmanager
    async def stdio_client(params, errlog=None):
        captured["errlog"] = errlog
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
    return captured


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
    async def stdio_client(params, errlog=None):
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


def _install_cancelled_session(monkeypatch):
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
    async def stdio_client(params, errlog=None):
        yield ("read", "write")

    class ClientSession:
        def __init__(self, read_stream, write_stream):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def initialize(self):
            raise asyncio.CancelledError()

    fake_client.ClientSession = ClientSession
    fake_client.StdioServerParameters = StdioServerParameters
    fake_stdio.stdio_client = stdio_client
    fake_stdio.StdioServerParameters = StdioServerParameters
    fake_session.ClientSession = ClientSession

    monkeypatch.setitem(__import__("sys").modules, "mcp.client", fake_client)
    monkeypatch.setitem(__import__("sys").modules, "mcp.client.stdio", fake_stdio)
    monkeypatch.setitem(__import__("sys").modules, "mcp.client.session", fake_session)


@pytest.mark.asyncio
async def test_router_connect_and_call(monkeypatch):
    tools = [
        {"name": "workflows_list", "description": "List workflows", "inputSchema": {"type": "object"}},
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
    assert specs == [ToolSpec(name="workflows_list", description="List workflows", input_schema={"type": "object"}, server="comfy")]

    result = await router.call_tool("workflows_list", {"limit": 1})
    assert result["ok"] is True
    assert result["payload"] == {"limit": 1}

    await router.close()


@pytest.mark.asyncio
async def test_stdio_backoffice_prefix_filter_keeps_newsletter_tools(monkeypatch):
    tools = [
        {"name": "newsletter_get", "description": "Load newsletter", "inputSchema": {"type": "object"}},
        {
            "name": "newsletter_add_item",
            "description": "Add newsletter item",
            "inputSchema": {"type": "object"},
        },
        {
            "name": "newsletter_get_section_schema",
            "description": "Get section schema",
            "inputSchema": {"type": "object"},
        },
        {"name": "get_section_types", "description": "List section types", "inputSchema": {"type": "object"}},
        {"name": "search_digests", "description": "Bare digest search", "inputSchema": {"type": "object"}},
    ]
    _install_fake_mcp_modules(monkeypatch, tools)

    router = MCPToolRouter(
        [
            ServerConfig(
                name="backoffice",
                command="python",
                args=["mcp/mcp_backoffice_server.py"],
                tool_prefixes=[
                    "newsletter_",
                    "suggest_newsletter_",
                    "format_",
                    "get_section_types",
                    "create_newsletter_",
                    "backoffice_",
                ],
            )
        ]
    )

    await router.connect()
    try:
        tool_names = {spec.name for spec in router.list_tool_specs()}
        assert "newsletter_get" in tool_names
        assert "newsletter_add_item" in tool_names
        assert "newsletter_get_section_schema" in tool_names
        assert "get_section_types" in tool_names
        assert "search_digests" not in tool_names
    finally:
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
async def test_router_reports_stdio_connection_statuses(monkeypatch):
    _install_fake_mcp_modules(monkeypatch, [])
    router = MCPToolRouter([ServerConfig(name="digester", command="python", args=["mcp_digester_server.py"], cwd="/tmp")])
    await router.connect()
    try:
        statuses = await router.get_server_connection_statuses()
        assert statuses == [
            {
                "name": "digester",
                "transport": "stdio",
                "ok": True,
                "connected": True,
                "command": "python",
                "args": ["mcp_digester_server.py"],
                "cwd": "/tmp",
                "stderr_log_path": str(__import__("pathlib").Path.cwd() / ".mcp_chat_logs" / "stdio" / "digester.stderr.log"),
            }
        ]
    finally:
        await router.close()


@pytest.mark.asyncio
async def test_router_connect_with_new_mcp_import_layout(monkeypatch):
    tools = [{"name": "workflows_list", "description": "List workflows", "inputSchema": {"type": "object"}}]
    _install_fake_mcp_modules(monkeypatch, tools, export_client_symbols=False)
    router = MCPToolRouter([ServerConfig(name="comfy", command="python")])
    await router.connect()
    specs = router.list_tool_specs()
    assert len(specs) == 1
    assert specs[0].name == "workflows_list"
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
    assert "photarium.stderr.log" in text


@pytest.mark.asyncio
async def test_router_close_suppresses_cancel_scope_mismatch():
    router = MCPToolRouter([])

    class BadStack:
        async def aclose(self):
            raise RuntimeError("Attempted to exit cancel scope in a different task than it was entered in")

    router._stack = BadStack()
    await router.close()


@pytest.mark.asyncio
async def test_router_close_suppresses_grouped_stdio_shutdown_noise():
    router = MCPToolRouter([])

    class BadStack:
        async def aclose(self):
            raise BaseExceptionGroup(
                "unhandled errors in a TaskGroup",
                [
                    GeneratorExit(),
                    RuntimeError("Attempted to exit cancel scope in a different task than it was entered in"),
                ],
            )

    router._stack = BadStack()
    await router.close()


@pytest.mark.asyncio
async def test_router_close_suppresses_grouped_cancelled_stdio_shutdown_noise():
    router = MCPToolRouter([])

    class BadStack:
        async def aclose(self):
            raise BaseExceptionGroup(
                "unhandled errors in a TaskGroup",
                [
                    asyncio.CancelledError(),
                    RuntimeError("Attempted to exit cancel scope in a different task than it was entered in"),
                ],
            )

    router._stack = BadStack()
    await router.close()


@pytest.mark.asyncio
async def test_router_connect_closes_stack_on_cancelled_initialize(monkeypatch):
    _install_cancelled_session(monkeypatch)
    router = MCPToolRouter([ServerConfig(name="comfy", command="python")])
    with pytest.raises(asyncio.CancelledError):
        await router.connect()
    assert router._stack is None


@pytest.mark.asyncio
async def test_router_parses_json_from_text_content(monkeypatch):
    tools = [
        {"name": "editorial_ads_preview", "description": "Preview ads", "inputSchema": {"type": "object"}},
    ]

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
    async def stdio_client(params, errlog=None):
        yield ("read", "write")

    class JsonContentSession(FakeSession):
        async def call_tool(self, name, payload):
            self.calls.append((name, payload))
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "{\"previewUrl\":\"http://127.0.0.1:8788/ads/preview\"}",
                    }
                ]
            }

    class ClientSession:
        def __init__(self, read_stream, write_stream):
            self._session = JsonContentSession(tools)

        async def __aenter__(self):
            return self._session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    fake_client.ClientSession = ClientSession
    fake_client.StdioServerParameters = StdioServerParameters
    fake_stdio.stdio_client = stdio_client
    fake_stdio.StdioServerParameters = StdioServerParameters
    fake_session.ClientSession = ClientSession

    monkeypatch.setitem(__import__("sys").modules, "mcp.client", fake_client)
    monkeypatch.setitem(__import__("sys").modules, "mcp.client.stdio", fake_stdio)
    monkeypatch.setitem(__import__("sys").modules, "mcp.client.session", fake_session)

    router = MCPToolRouter([ServerConfig(name="editorial", command="node")])
    await router.connect()
    result = await router.call_tool("editorial_ads_preview", {"adIds": ["a"]})
    assert result == {"previewUrl": "http://127.0.0.1:8788/ads/preview"}
    await router.close()


@pytest.mark.asyncio
async def test_router_routes_stdio_stderr_to_log_file(monkeypatch):
    captured = _install_fake_mcp_modules(monkeypatch, [])
    router = MCPToolRouter([ServerConfig(name="photarium", command="node", args=["dist/index.js"])])
    await router.connect()
    try:
        errlog = captured.get("errlog")
        assert errlog is not None
        assert getattr(errlog, "name", "").endswith("photarium.stderr.log")
    finally:
        await router.close()
