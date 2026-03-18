from __future__ import annotations

from comfy_mcp.tui_client.config import ServerConfig
from comfy_mcp.tui_client.transport_plan import build_transport_plan


def test_transport_plan_partitions_mixed_servers() -> None:
    plan = build_transport_plan(
        [
            ServerConfig(name="comfy", transport="stdio", command="python"),
            ServerConfig(name="photarium", transport="stdio", command="node"),
            ServerConfig(name="workspace", transport="http", http_url="http://127.0.0.1:8777"),
        ]
    )

    assert plan.stdio_server_names == ["comfy", "photarium"]
    assert plan.http_server_names == ["workspace"]
