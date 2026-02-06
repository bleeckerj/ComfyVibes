"""Tests for queue retrieval."""

import httpx
import pytest

from comfy_mcp.comfy_client.client import ComfyClient


@pytest.mark.asyncio
async def test_get_queue_calls_expected_path() -> None:
    """ComfyClient should call `/queue` and return JSON payload."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/queue"
        return httpx.Response(200, json={"queue_running": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://example.com", transport=transport) as client:
        comfy = ComfyClient(base_url="http://example.com", http_client=client)
        payload = await comfy.get_queue()
        assert payload == {"queue_running": []}
