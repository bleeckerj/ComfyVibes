"""Diagnostics for configured MCP tool exposure."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from comfy_mcp.tui_client.config import ServerConfig


@dataclass(frozen=True)
class ToolExposureWarning:
    server_name: str
    message: str


def count_tools_by_server(tool_specs: Iterable[Any]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for spec in tool_specs:
        server = str(getattr(spec, "server", "") or "")
        if server:
            counts[server] += 1
    return dict(counts)


def build_zero_tool_warnings(
    servers: Iterable[ServerConfig],
    tool_specs: Iterable[Any],
) -> list[ToolExposureWarning]:
    counts = count_tools_by_server(tool_specs)
    warnings: list[ToolExposureWarning] = []
    for server in servers:
        if counts.get(server.name, 0) > 0:
            continue
        prefixes = ", ".join(server.tool_prefixes) if server.tool_prefixes else "(no prefixes)"
        warnings.append(
            ToolExposureWarning(
                server_name=server.name,
                message=(
                    f"Server '{server.name}' contributed zero tools after prefix filtering "
                    f"(transport={server.transport}, prefixes={prefixes})."
                ),
            )
        )
    return warnings
