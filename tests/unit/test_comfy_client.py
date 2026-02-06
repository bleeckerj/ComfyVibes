"""Tests for ComfyClient HTTP behavior."""

import httpx
import pytest

from comfy_mcp.comfy_client.client import ComfyClient


@pytest.mark.asyncio
async def test_get_object_info_uses_expected_path() -> None:
    """ComfyClient should call `/object_info` and return JSON payload."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/object_info"
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://example.com", transport=transport) as client:
        comfy = ComfyClient(base_url="http://example.com", http_client=client)
        payload = await comfy.get_object_info()
        assert payload == {"ok": True}


@pytest.mark.asyncio
async def test_get_history_with_prompt_id() -> None:
    """ComfyClient should call `/history/{prompt_id}` when provided."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/history/abc123"
        return httpx.Response(200, json={"prompt": "abc123"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://example.com", transport=transport) as client:
        comfy = ComfyClient(base_url="http://example.com", http_client=client)
        payload = await comfy.get_history(prompt_id="abc123")
        assert payload == {"prompt": "abc123"}
