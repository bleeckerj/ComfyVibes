"""MCP stdio router for multi-server tool calls."""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from comfy_mcp.tui_client.config import ServerConfig


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: Dict[str, Any]


@dataclass
class _ServerState:
    name: str
    prefixes: List[str]
    session: Any


class MCPToolRouter:
    def __init__(self, servers: List[ServerConfig]):
        self._servers = servers
        self._stack: AsyncExitStack | None = None
        self._tool_map: Dict[str, _ServerState] = {}
        self._tool_specs: List[ToolSpec] = []

    async def connect(self) -> None:
        try:
            # MCP SDK import locations differ across versions.
            try:
                from mcp.client import ClientSession, StdioServerParameters
                from mcp.client.stdio import stdio_client
            except ImportError:
                from mcp.client.session import ClientSession
                from mcp.client.stdio import StdioServerParameters, stdio_client
        except ImportError as exc:
            raise RuntimeError(
                "MCP client SDK not available in this Python environment."
            ) from exc

        self._stack = AsyncExitStack()
        await self._stack.__aenter__()

        try:
            for server in self._servers:
                params = StdioServerParameters(
                    command=server.command,
                    args=server.args,
                    env=server.env or None,
                    cwd=server.cwd,
                )
                try:
                    read_stream, write_stream = await self._stack.enter_async_context(stdio_client(params))
                    session = await self._stack.enter_async_context(ClientSession(read_stream, write_stream))
                    await session.initialize()
                    raw_tools = await session.list_tools()
                except Exception as exc:
                    raise RuntimeError(
                        "Server '%s' failed to initialize. command=%r args=%r cwd=%r: %s"
                        % (server.name, server.command, server.args, server.cwd, exc)
                    ) from exc

                tools = _extract_tools(raw_tools)
                state = _ServerState(name=server.name, prefixes=server.tool_prefixes, session=session)
                for tool in tools:
                    spec = _normalize_tool(tool)
                    self._tool_specs.append(spec)
                    self._tool_map[spec.name] = state
        except Exception:
            await self.close()
            raise

    async def close(self) -> None:
        if self._stack is None:
            return
        try:
            await self._stack.__aexit__(None, None, None)
        except RuntimeError as exc:
            # mcp/anyio stdio contexts can raise this during task-cancel shutdown.
            # We suppress it to avoid noisy tracebacks when quitting the TUI.
            if "Attempted to exit cancel scope in a different task" not in str(exc):
                raise
        finally:
            self._stack = None

    def list_tool_specs(self) -> List[ToolSpec]:
        return list(self._tool_specs)

    async def call_tool(self, name: str, arguments: Dict[str, Any] | None) -> Any:
        state = self._tool_map.get(name)
        if state is None:
            raise RuntimeError(f"Tool not found: {name}")
        payload = arguments or {}
        result = await state.session.call_tool(name, payload)
        return _serialize_tool_result(result)


def _extract_tools(raw: Any) -> Iterable[Any]:
    if isinstance(raw, dict) and "tools" in raw:
        return raw["tools"]
    if hasattr(raw, "tools"):
        return raw.tools
    if isinstance(raw, list):
        return raw
    return []


def _normalize_tool(raw: Any) -> ToolSpec:
    if isinstance(raw, dict):
        return ToolSpec(
            name=raw.get("name", ""),
            description=raw.get("description", ""),
            input_schema=raw.get("inputSchema", {}) or {},
        )
    return ToolSpec(
        name=getattr(raw, "name", ""),
        description=getattr(raw, "description", ""),
        input_schema=getattr(raw, "inputSchema", {}) or {},
    )


def _serialize_tool_result(result: Any) -> Any:
    if isinstance(result, (str, int, float, bool)) or result is None:
        return result
    if isinstance(result, (list, dict)):
        return result
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if hasattr(result, "dict"):
        return result.dict()
    if hasattr(result, "__dict__"):
        return result.__dict__
    try:
        json.dumps(result)
    except TypeError:
        return str(result)
    return result
