"""Read-only live smoke tests for remote ComfyUI integrations."""

from __future__ import annotations

import os

import pytest

from comfy_mcp.comfy_client.client import ComfyClient
from comfy_mcp.comfy_manager.client import ComfyManagerClient
from comfy_mcp.mcp_server.policy import Policy
from comfy_mcp.mcp_server.tools_comfy import ComfyTools


def _get_base_url() -> str | None:
    return os.getenv("COMFY_MCP_TEST_URL") or os.getenv("COMFY_MCP_COMFY_BASE_URL")


@pytest.mark.asyncio
async def test_live_capabilities_node_search_and_manager_status() -> None:
    if os.getenv("COMFY_MCP_LIVE_TESTS") != "1":
        pytest.skip("Set COMFY_MCP_LIVE_TESTS=1 for read-only remote ComfyUI smoke tests.")
    base_url = _get_base_url()
    if not base_url:
        pytest.skip("Set COMFY_MCP_TEST_URL or COMFY_MCP_COMFY_BASE_URL to run live tests.")

    async with ComfyClient(base_url=base_url) as comfy_client:
        manager_client = ComfyManagerClient(base_url)
        try:
            tools = ComfyTools(
                comfy_client,
                Policy(api_token=None, readonly_mode=True, max_workflow_bytes=5_000_000),
                manager_client=manager_client,
            )
            capabilities = await tools.capabilities_get(probe_queue=True, resolve_dns=True)
            assert capabilities["target"]["host"]
            assert capabilities["features"]["status"] in {"available", "unsupported", "unreachable"}

            nodes = await tools.nodes_search(limit=5)
            assert isinstance(nodes["nodes"], list)

            manager_status = await manager_client.status()
            assert manager_status["supports_v4"] in {True, False}
        finally:
            await manager_client.close()
