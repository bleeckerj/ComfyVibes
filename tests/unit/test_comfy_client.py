"""Tests for ComfyClient HTTP behavior."""

import asyncio
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


class _FakeWebSocket:
    def __init__(self, messages):
        self._messages = list(messages)

    async def recv(self):
        if not self._messages:
            await asyncio.sleep(0.01)
            raise asyncio.TimeoutError
        return self._messages.pop(0)


class _FakeWSContext:
    def __init__(self, websocket):
        self._websocket = websocket

    async def __aenter__(self):
        return self._websocket

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.mark.asyncio
async def test_watch_prompt_uses_history_short_circuit() -> None:
    """watch_prompt should return complete immediately when history already contains prompt."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/history/abc123"
        return httpx.Response(200, json={"abc123": {"outputs": {}}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://example.com", transport=transport) as client:
        comfy = ComfyClient(base_url="http://example.com", http_client=client)
        payload = await comfy.watch_prompt("abc123", inactivity_timeout_s=0.2, max_wait_s=0.5)
        assert payload["status"] == "complete"
        assert payload["source"] == "history"


@pytest.mark.asyncio
async def test_peek_progress_returns_normalized_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """peek_progress should normalize websocket payload fields."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://example.com", transport=transport) as client:
        comfy = ComfyClient(base_url="http://example.com", http_client=client)
        fake_ws = _FakeWebSocket(
            [
                '{"type":"progress","data":{"prompt_id":"abc123","node":"3","value":2,"max":4}}',
            ]
        )
        monkeypatch.setattr(comfy, "_ws_connect", lambda _url: _FakeWSContext(fake_ws))
        payload = await comfy.peek_progress(prompt_id="abc123", timeout_s=0.2)
        assert payload is not None
        assert payload["type"] == "progress"
        assert payload["node"] == "3"
        assert payload["percent"] == 50.0


def test_prompt_in_queue_handles_list_payload_shape() -> None:
    queue = {
        "queue_running": [[0, "abc123"]],
        "queue_pending": [[1, "def456"]],
    }
    assert ComfyClient._prompt_in_queue("abc123", queue) is True
    assert ComfyClient._prompt_in_queue("def456", queue) is True
    assert ComfyClient._prompt_in_queue("zzz999", queue) is False
