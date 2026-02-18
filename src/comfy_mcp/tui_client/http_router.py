"""HTTP-based MCP tool router for multi-server tool calls.

Talks to MCP HTTP proxy servers (like comfy_mcp.mcp_server.http_server
and the Photarium MCP HTTP proxy) instead of using stdio subprocesses.

This is the recommended transport when the MCP servers are already running.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from comfy_mcp.tui_client.config import ServerConfig
from comfy_mcp.tui_client.mcp_router import ToolSpec


@dataclass
class _HTTPServerState:
    name: str
    base_url: str
    prefixes: List[str]
    tools: List[ToolSpec]


class HTTPToolRouter:
    """Tool router that calls MCP HTTP proxy servers directly.

    No subprocesses, no stdio, no MCP SDK needed.
    Just HTTP POST to /tools/<name> on each server.
    """

    def __init__(self, servers: List[ServerConfig]):
        self._servers = servers
        self._server_states: List[_HTTPServerState] = []
        self._tool_map: Dict[str, _HTTPServerState] = {}
        self._tool_specs: List[ToolSpec] = []
        self._client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(timeout=300.0)

        for server in self._servers:
            base_url = server.http_url
            if not base_url:
                raise RuntimeError(
                    f"Server '{server.name}' has no http_url configured. "
                    "Set 'http_url' in config or use the stdio router."
                )

            # Health check
            try:
                resp = await self._client.get(f"{base_url}/health")
                resp.raise_for_status()
            except Exception as exc:
                raise RuntimeError(
                    f"Server '{server.name}' health check failed at {base_url}/health: {exc}"
                ) from exc

            # Fetch tool definitions
            try:
                resp = await self._client.get(f"{base_url}/tools")
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                raise RuntimeError(
                    f"Server '{server.name}' tool listing failed at {base_url}/tools: {exc}"
                ) from exc

            raw_tools = data.get("tools", [])
            tools: List[ToolSpec] = []
            for raw in raw_tools:
                spec = ToolSpec(
                    name=raw.get("name", ""),
                    description=raw.get("description", ""),
                    input_schema=raw.get("inputSchema", {}) or {},
                )
                tools.append(spec)

            state = _HTTPServerState(
                name=server.name,
                base_url=base_url,
                prefixes=server.tool_prefixes,
                tools=tools,
            )
            self._server_states.append(state)

            for spec in tools:
                self._tool_specs.append(spec)
                self._tool_map[spec.name] = state

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def list_tool_specs(self) -> List[ToolSpec]:
        return list(self._tool_specs)

    async def call_tool(self, name: str, arguments: Dict[str, Any] | None) -> Any:
        state = self._tool_map.get(name)
        if state is None:
            raise RuntimeError(f"Tool not found: {name}")
        if self._client is None:
            raise RuntimeError("Router not connected")

        url = f"{state.base_url}/tools/{name}"
        resp = await self._client.post(url, json=arguments or {})
        if resp.status_code >= 400:
            message = f"HTTP {resp.status_code} calling {name}"
            try:
                payload = resp.json()
                if isinstance(payload, dict):
                    if "error" in payload and payload.get("error"):
                        message = str(payload["error"])
                    elif "detail" in payload and payload.get("detail"):
                        message = str(payload["detail"])
                    elif "result" in payload and isinstance(payload["result"], dict):
                        nested_error = payload["result"].get("error")
                        if nested_error:
                            message = str(nested_error)
            except Exception:
                if resp.text:
                    message = f"{message}: {resp.text}"
            raise RuntimeError(message)
        payload = resp.json() if resp.content else {}

        # Unwrap the {ok, result} envelope from the HTTP proxy
        if isinstance(payload, dict) and "ok" in payload:
            if not payload.get("ok"):
                raise RuntimeError(str(payload.get("error", "Tool call failed")))
            return payload.get("result")

        return payload
