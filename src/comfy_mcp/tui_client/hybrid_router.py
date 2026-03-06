"""Hybrid MCP router that supports both HTTP and stdio servers in one session."""

from __future__ import annotations

from typing import Any, Dict, List

from comfy_mcp.tui_client.config import ServerConfig
from comfy_mcp.tui_client.http_router import HTTPToolRouter
from comfy_mcp.tui_client.mcp_router import MCPToolRouter, ToolSpec


class HybridToolRouter:
    """Route tool calls across both HTTP and stdio MCP transports."""

    def __init__(self, http_servers: List[ServerConfig], stdio_servers: List[ServerConfig]):
        self._http_router = HTTPToolRouter(http_servers) if http_servers else None
        self._stdio_router = MCPToolRouter(stdio_servers) if stdio_servers else None
        self._tool_specs: List[ToolSpec] = []
        self._tool_to_router: Dict[str, Any] = {}

    async def connect(self) -> None:
        self._tool_specs = []
        self._tool_to_router = {}

        try:
            if self._stdio_router is not None:
                await self._stdio_router.connect()
                self._register_tools(self._stdio_router, source_label="stdio")

            if self._http_router is not None:
                await self._http_router.connect()
                self._register_tools(self._http_router, source_label="http")
        except Exception:
            await self.close()
            raise

    async def close(self) -> None:
        error: Exception | None = None

        if self._http_router is not None:
            try:
                await self._http_router.close()
            except Exception as exc:  # pragma: no cover
                error = error or exc

        if self._stdio_router is not None:
            try:
                await self._stdio_router.close()
            except Exception as exc:  # pragma: no cover
                error = error or exc

        if error is not None:
            raise error

    def list_tool_specs(self) -> List[ToolSpec]:
        return list(self._tool_specs)

    async def call_tool(self, name: str, arguments: Dict[str, Any] | None) -> Any:
        router = self._tool_to_router.get(name)
        if router is None:
            raise RuntimeError(f"Tool not found: {name}")
        return await router.call_tool(name, arguments)

    async def get_server_health_statuses(self) -> List[Dict[str, Any]]:
        if self._http_router is None:
            return []
        return await self._http_router.get_server_health_statuses()

    def _register_tools(self, router: Any, *, source_label: str) -> None:
        for spec in router.list_tool_specs():
            existing = self._tool_to_router.get(spec.name)
            if existing is not None:
                raise RuntimeError(
                    f"Duplicate MCP tool name detected: '{spec.name}'. "
                    f"Rename or namespace this tool so EDGAR can route calls unambiguously "
                    f"(collision encountered while loading {source_label} servers)."
                )
            self._tool_specs.append(spec)
            self._tool_to_router[spec.name] = router
