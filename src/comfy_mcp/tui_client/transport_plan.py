"""Shared transport partitioning helpers for EDGAR TUI clients and launchers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

from comfy_mcp.tui_client.config import ServerConfig


@dataclass(frozen=True)
class TransportPlan:
    http_servers: List[ServerConfig]
    stdio_servers: List[ServerConfig]

    @property
    def http_server_names(self) -> List[str]:
        return [server.name for server in self.http_servers]

    @property
    def stdio_server_names(self) -> List[str]:
        return [server.name for server in self.stdio_servers]


def build_transport_plan(servers: Iterable[ServerConfig]) -> TransportPlan:
    http_servers: List[ServerConfig] = []
    stdio_servers: List[ServerConfig] = []
    for server in servers:
        if server.transport == "http":
            http_servers.append(server)
        else:
            stdio_servers.append(server)
    return TransportPlan(http_servers=http_servers, stdio_servers=stdio_servers)
