"""Tests for the remote ComfyUI Manager v4 client."""

from __future__ import annotations

import json

import httpx
import pytest

from comfy_mcp.comfy_manager.client import ComfyManagerClient
from comfy_mcp.comfy_manager.errors import ManagerAPIError, ManagerConnectionError


@pytest.mark.asyncio
async def test_manager_v4_detection_routes_and_discovery_cache() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        path = request.url.path
        if path == "/features":
            return httpx.Response(200, json={"extension": {"manager": {"supports_v4": True}}})
        if path == "/v2/customnode/installed":
            return httpx.Response(200, json={"Pack One": {"cnr_id": "pack/one", "enabled": True}})
        if path == "/v2/customnode/getmappings":
            return httpx.Response(200, json={"pack/one": [["NodeA"], {"title_aux": "author/pack"}]})
        if path == "/v2/snapshot/getlist":
            return httpx.Response(200, json={"snapshots": ["snapshot-a"]})
        if path == "/v2/externalmodel/getlist":
            return httpx.Response(200, json={"models": [{"name": "Model A", "installed": True}]})
        if path == "/v2/manager/queue/status":
            return httpx.Response(200, json={"running": []})
        raise AssertionError(f"unexpected Manager route: {path}")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://gpu.example:8188", transport=transport) as http_client:
        manager = ComfyManagerClient("http://gpu.example:8188", http_client=http_client)

        version = await manager.check_installed()
        assert version.supports_v4 is True
        assert (await manager.check_installed()).version == "v4"
        assert calls.count("/features") == 1

        packs = await manager.list_installed_packs()
        assert packs["Pack One"]["enabled"] is True
        assert await manager.list_installed_packs() == packs
        assert calls.count("/v2/customnode/installed") == 1
        await manager.list_installed_packs(refresh=True)
        assert calls.count("/v2/customnode/installed") == 2

        mappings = await manager.get_node_mappings()
        assert mappings["NodeA"] == {
            "node_type": "NodeA",
            "node_pack_id": "pack/one",
            "node_pack_name": "author/pack",
        }
        assert (await manager.list_snapshots())["snapshots"] == ["snapshot-a"]
        assert (await manager.search_external_models(query="model"))[0]["installed"] is True
        assert (await manager.queue_status())["running"] == []

    assert calls == [
        "/features",
        "/v2/customnode/installed",
        "/v2/customnode/installed",
        "/v2/customnode/getmappings",
        "/v2/snapshot/getlist",
        "/v2/externalmodel/getlist",
        "/v2/manager/queue/status",
    ]


@pytest.mark.asyncio
async def test_manager_operation_uses_constrained_v4_routes_and_invalidates_cache() -> None:
    requests: list[tuple[str, dict[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8")) if request.content else None
        requests.append((request.url.path, body))
        if request.url.path == "/features":
            return httpx.Response(200, json={"extension": {"manager": {"supports_v4": True}}})
        if request.url.path == "/v2/manager/queue/task":
            assert body == {
                "ui_id": "ui-1",
                "client_id": "client-1",
                "kind": "install",
                "params": {"id": "pack/one"},
            }
            return httpx.Response(200, json={"task_id": "task-1"})
        if request.url.path == "/v2/manager/queue/start":
            return httpx.Response(204)
        if request.url.path == "/v2/manager/queue/update_comfyui":
            assert request.url.params["stable"] == "true"
            return httpx.Response(200, json={"task_id": "task-2"})
        raise AssertionError(f"unexpected Manager route: {request.url.path}")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://gpu.example:8188", transport=transport) as http_client:
        manager = ComfyManagerClient("http://gpu.example:8188", http_client=http_client)
        result = await manager.queue_action(
            "install_node_pack",
            {"id": "pack/one"},
            client_id="client-1",
            ui_id="ui-1",
        )
        assert result["success"] is True
        assert result["queue_start"] == {"success": True, "status": 204}
        await manager.queue_action("update_comfyui", {"stable": True}, start_queue=False)

    assert [path for path, _body in requests] == [
        "/features",
        "/v2/manager/queue/task",
        "/v2/manager/queue/start",
        "/features",
        "/v2/manager/queue/update_comfyui",
    ]


@pytest.mark.asyncio
async def test_manager_unavailable_and_api_failures_are_structured() -> None:
    def missing_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/features"
        return httpx.Response(404, json={"error": "Manager unavailable"})

    missing_transport = httpx.MockTransport(missing_handler)
    async with httpx.AsyncClient(base_url="http://gpu.example:8188", transport=missing_transport) as http_client:
        manager = ComfyManagerClient("http://gpu.example:8188", http_client=http_client)
        version = await manager.check_installed()
        assert version.installed is False
        assert version.supports_v4 is False
        assert (await manager.status())["queue"] is None

    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"type": "manager_failure", "message": "broken"}})

    error_transport = httpx.MockTransport(error_handler)
    async with httpx.AsyncClient(base_url="http://gpu.example:8188", transport=error_transport) as http_client:
        manager = ComfyManagerClient("http://gpu.example:8188", http_client=http_client)
        with pytest.raises(ManagerAPIError, match="manager_failure") as error:
            await manager.check_installed()
        assert error.value.status_code == 500
        assert error.value.endpoint == "/features"
        assert error.value.category == "remote"

    def connection_handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    connection_transport = httpx.MockTransport(connection_handler)
    async with httpx.AsyncClient(base_url="http://gpu.example:8188", transport=connection_transport) as http_client:
        manager = ComfyManagerClient("http://gpu.example:8188", http_client=http_client)
        with pytest.raises(ManagerConnectionError, match="Failed to connect"):
            await manager.check_installed()
