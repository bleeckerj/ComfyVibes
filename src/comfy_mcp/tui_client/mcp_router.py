"""MCP stdio router for multi-server tool calls."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

from comfy_mcp.tui_client.config import ServerConfig


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: Dict[str, Any]
    server: str = ""


@dataclass
class _ServerState:
    name: str
    prefixes: List[str]
    session: Any
    stderr_log_path: str | None = None


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
                stderr_log_path = _stdio_stderr_log_path(server)
                errlog = self._stack.enter_context(_open_stdio_errlog(stderr_log_path))
                try:
                    read_stream, write_stream = await self._stack.enter_async_context(
                        stdio_client(params, errlog=errlog)
                    )
                    session = await self._stack.enter_async_context(ClientSession(read_stream, write_stream))
                    await session.initialize()
                    raw_tools = await session.list_tools()
                except Exception as exc:
                    raise RuntimeError(
                        "Server '%s' failed to initialize. command=%r args=%r cwd=%r stderr_log=%r: %s"
                        % (server.name, server.command, server.args, server.cwd, stderr_log_path, exc)
                    ) from exc

                tools = _extract_tools(raw_tools)
                state = _ServerState(
                    name=server.name,
                    prefixes=server.tool_prefixes,
                    session=session,
                    stderr_log_path=stderr_log_path,
                )
                for tool in tools:
                    spec = _normalize_tool(tool, server_name=server.name)
                    if server.tool_prefixes:
                        if not any(spec.name.startswith(prefix) for prefix in server.tool_prefixes):
                            continue
                    self._tool_specs.append(spec)
                    self._tool_map[spec.name] = state
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        stack = self._stack
        if stack is None:
            return
        self._stack = None
        self._tool_map = {}
        self._tool_specs = []
        await _close_stack_quietly(stack)

    def list_tool_specs(self) -> List[ToolSpec]:
        return list(self._tool_specs)

    async def get_server_connection_statuses(self) -> List[Dict[str, Any]]:
        stderr_log_by_server = {
            state.name: state.stderr_log_path
            for state in self._tool_map.values()
            if state.stderr_log_path
        }
        return [
            {
                "name": server.name,
                "transport": "stdio",
                "ok": self._stack is not None,
                "connected": self._stack is not None,
                "command": server.command,
                "args": list(server.args),
                "cwd": server.cwd,
                "stderr_log_path": stderr_log_by_server.get(server.name, _stdio_stderr_log_path(server)),
            }
            for server in self._servers
        ]

    async def call_tool(self, name: str, arguments: Dict[str, Any] | None) -> Any:
        state = self._tool_map.get(name)
        if state is None:
            raise RuntimeError(f"Tool not found: {name}")
        payload = arguments or {}
        result = await state.session.call_tool(name, payload)
        serialized = _serialize_tool_result(result)
        return _maybe_parse_json_text_content(serialized)


def _extract_tools(raw: Any) -> Iterable[Any]:
    if isinstance(raw, dict) and "tools" in raw:
        return raw["tools"]
    if hasattr(raw, "tools"):
        return raw.tools
    if isinstance(raw, list):
        return raw
    return []


def _normalize_tool(raw: Any, *, server_name: str) -> ToolSpec:
    if isinstance(raw, dict):
        return ToolSpec(
            name=raw.get("name", ""),
            description=raw.get("description", ""),
            input_schema=raw.get("inputSchema", {}) or {},
            server=server_name,
        )
    return ToolSpec(
        name=getattr(raw, "name", ""),
        description=getattr(raw, "description", ""),
        input_schema=getattr(raw, "inputSchema", {}) or {},
        server=server_name,
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


def _maybe_parse_json_text_content(result: Any) -> Any:
    """Unwrap MCP-style text content that contains JSON.

    Some MCP servers return tool payloads as a `content` array with a single
    `{type:"text", text:"{...json...}"}` item. For downstream tool-grounded
    renderers (and for LLM context), returning the parsed JSON object is much
    more useful than the transport wrapper.
    """
    if isinstance(result, dict):
        content = result.get("content")
        parsed = _parse_json_from_content(content)
        return parsed if parsed is not None else result
    if isinstance(result, list):
        parsed = _parse_json_from_content(result)
        return parsed if parsed is not None else result
    return result


def _parse_json_from_content(content: Any) -> Any | None:
    if not isinstance(content, list) or not content:
        return None
    first = content[0]
    if not isinstance(first, dict):
        return None
    if first.get("type") != "text":
        return None
    text = first.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    candidate = text.strip()
    if not (candidate.startswith("{") or candidate.startswith("[")):
        return None
    try:
        return json.loads(candidate)
    except Exception:
        return None


def _is_ignorable_stdio_close_error(exc: BaseException) -> bool:
    if isinstance(exc, BaseExceptionGroup):
        return bool(exc.exceptions) and all(_is_ignorable_stdio_close_error(item) for item in exc.exceptions)
    if isinstance(exc, GeneratorExit):
        return True
    if isinstance(exc, asyncio.CancelledError):
        return True
    return isinstance(exc, RuntimeError) and "Attempted to exit cancel scope in a different task" in str(exc)


async def _close_stack_quietly(stack: AsyncExitStack) -> None:
    try:
        await stack.aclose()
    except BaseException as exc:
        if not _is_ignorable_stdio_close_error(exc):
            raise


def _stdio_stderr_log_path(server: ServerConfig) -> str | None:
    mode = os.environ.get("EDGAR_TUI_STDIO_ERRLOG", "").strip().lower()
    if mode in {"inherit", "stderr", "console"}:
        return None
    return str(Path.cwd() / ".mcp_chat_logs" / "stdio" / f"{server.name}.stderr.log")


def _open_stdio_errlog(stderr_log_path: str | None):
    if stderr_log_path is None:
        return sys.stderr
    path = Path(stderr_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("w", encoding="utf-8")
