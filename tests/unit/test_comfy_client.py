"""Tests for ComfyClient HTTP behavior."""

import asyncio
import json

import httpx
import pytest

from comfy_mcp.comfy_client.client import ComfyClient
from comfy_mcp.comfy_client.errors import ComfyClientError


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


@pytest.mark.asyncio
async def test_queue_prompt_sends_extra_data() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"prompt_id": "abc123"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://example.com", transport=transport) as client:
        comfy = ComfyClient(base_url="http://example.com", http_client=client)
        result = await comfy.queue_prompt(
            {"1": {"class_type": "TestNode", "inputs": {}}},
            client_id="client-1",
            extra_data={"auth_token_comfy_org": "token-value"},
        )

    assert result == {"prompt_id": "abc123"}
    assert captured["payload"] == {
        "prompt": {"1": {"class_type": "TestNode", "inputs": {}}},
        "extra_data": {"auth_token_comfy_org": "token-value"},
        "client_id": "client-1",
    }


@pytest.mark.asyncio
async def test_queue_prompt_surfaces_error_payload_details() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "type": "missing_node_type",
                    "message": "Node 'Image_Resizer' not found.",
                    "details": "Node ID '#30'",
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://example.com", transport=transport) as client:
        comfy = ComfyClient(base_url="http://example.com", http_client=client)
        with pytest.raises(ComfyClientError, match="missing_node_type"):
            await comfy.queue_prompt({"1": {"class_type": "TestNode", "inputs": {}}})


@pytest.mark.asyncio
async def test_remote_read_routes_and_encoded_identifiers() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.raw_path.decode("ascii")))
        path = request.url.path
        if path == "/api/assets":
            assert request.url.params.get_list("include_tags") == ["portrait", "hdr"]
            assert request.url.params.get_list("exclude_tags") == ["draft"]
            assert request.url.params["metadata_filter"] == '{"source":"test"}'
            return httpx.Response(200, json={"assets": []})
        return httpx.Response(200, json={"path": path})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://example.com", transport=transport) as client:
        comfy = ComfyClient(base_url="http://example.com", http_client=client)
        assert await comfy.get_features() == {"path": "/features"}
        assert await comfy.get_system_stats() == {"path": "/system_stats"}
        assert await comfy.list_jobs(status=["running", "pending"], workflow_id="wf/x", limit=2) == {"path": "/api/jobs"}
        assert await comfy.get_job("job/x y") == {"path": "/api/jobs/job/x y"}
        assert await comfy.get_workflow_templates() == {"path": "/workflow_templates"}
        assert await comfy.get_workflow_template("pack/x", "templates/demo file.json") == {"path": "/api/workflow_templates/pack/x/templates/demo file.json"}
        assert await comfy.get_global_subgraphs() == {"path": "/global_subgraphs"}
        assert await comfy.get_global_subgraph("subgraph/x") == {"path": "/global_subgraphs/subgraph/x"}
        assert await comfy.get_node_replacements() == {"path": "/node_replacements"}
        assert await comfy.list_assets(
            include_tags=["portrait", "hdr"],
            exclude_tags=["draft"],
            metadata_filter={"source": "test"},
            limit=10,
        ) == {"assets": []}
        assert await comfy.get_asset("asset/x") == {"path": "/api/assets/asset/x"}
        assert await comfy.get_tags(prefix="por", include_zero=False) == {"path": "/api/tags"}

    assert ("GET", "/api/jobs/job%2Fx%20y") in calls
    assert ("GET", "/api/workflow_templates/pack%2Fx/templates%2Fdemo%20file.json") in calls
    assert ("GET", "/global_subgraphs/subgraph%2Fx") in calls
    assert ("GET", "/api/assets/asset%2Fx") in calls


@pytest.mark.asyncio
async def test_remote_mutation_routes_request_bodies_and_empty_responses(tmp_path) -> None:
    image_path = tmp_path / "mask file.png"
    image_path.write_bytes(b"png-bytes")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in {"/interrupt", "/queue", "/free", "/history", "/settings"}:
            captured[path] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(204)
        if path == "/settings/ui.theme":
            captured[path] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json={"saved": True})
        if path in {"/upload/mask", "/api/assets"}:
            captured[path] = request.content
            return httpx.Response(200, json={"uploaded": path})
        raise AssertionError(f"unexpected route: {path}")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://example.com", transport=transport) as client:
        comfy = ComfyClient(base_url="http://example.com", http_client=client)
        assert await comfy.interrupt("prompt-1") == {}
        assert await comfy.delete_queue(prompt_ids=["prompt-1", "prompt-2"]) == {}
        assert await comfy.free_memory(unload_models=True, free_memory=True) == {}
        assert await comfy.delete_history(clear_all=True) == {}
        assert await comfy.set_settings({"ui.theme": "dark"}) == {}
        assert await comfy.set_settings(setting_id="ui.theme", value="light") == {"saved": True}
        assert await comfy.upload_mask(
            str(image_path),
            {"filename": "original.png", "type": "input", "subfolder": "source"},
            subfolder="masks",
        ) == {"uploaded": "/upload/mask"}
        assert await comfy.upload_asset(
            str(image_path),
            tags=["portrait", "hdr"],
            name="mask file",
            user_metadata={"source": "test"},
            content_hash="abc123",
        ) == {"uploaded": "/api/assets"}

    assert captured["/interrupt"] == {"prompt_id": "prompt-1"}
    assert captured["/queue"] == {"delete": ["prompt-1", "prompt-2"]}
    assert captured["/free"] == {"unload_models": True, "free_memory": True}
    assert captured["/history"] == {"clear": True}
    assert captured["/settings"] == {"ui.theme": "dark"}
    assert captured["/settings/ui.theme"] == "light"
    mask_body = captured["/upload/mask"]
    asset_body = captured["/api/assets"]
    assert isinstance(mask_body, bytes)
    assert b'form-data; name="original_ref"' in mask_body
    assert b'{"filename":"original.png","type":"input","subfolder":"source"}' in mask_body
    assert isinstance(asset_body, bytes)
    assert b'form-data; name="tags"' in asset_body
    assert b'["portrait","hdr"]' in asset_body
    assert b'{"source":"test"}' in asset_body


@pytest.mark.asyncio
async def test_remote_mutation_validation_and_http_error_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"type": "missing_route", "message": "Optional route is unavailable"}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://example.com", transport=transport) as client:
        comfy = ComfyClient(base_url="http://example.com", http_client=client)
        with pytest.raises(ComfyClientError) as error:
            await comfy.get_node_replacements()
        assert error.value.status_code == 404
        assert error.value.endpoint == "/node_replacements"
        assert error.value.category == "unsupported"
        assert "missing_route" in str(error.value)
        with pytest.raises(ComfyClientError, match="clear_all and prompt_ids"):
            await comfy.delete_queue(clear_all=True, prompt_ids=["prompt-1"])
        with pytest.raises(ComfyClientError, match="one of unload_models"):
            await comfy.free_memory()
        with pytest.raises(ComfyClientError, match="value is required"):
            await comfy.set_settings(setting_id="ui.theme")


def test_build_ws_url_preserves_remote_base_path_and_encodes_client_id() -> None:
    comfy = ComfyClient("https://gpu.example:8188/comfy?tenant=alpha")

    ws_url = comfy._build_ws_url(client_id="client/x")

    assert ws_url == "wss://gpu.example:8188/comfy/ws?tenant=alpha&clientId=client%2Fx"
