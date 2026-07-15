"""Focused tests for new remote ComfyUI and Manager tool handlers."""

from __future__ import annotations

from typing import Any

import pytest

from comfy_mcp.comfy_client.errors import ComfyClientError
from comfy_mcp.mcp_server.policy import Policy
from comfy_mcp.mcp_server.tools_comfy import ComfyTools
from comfy_mcp.mcp_server.tools_manager import ManagerTools


class RemoteClientFake:
    def __init__(self) -> None:
        self._base_url = "http://gpu.example:8188"
        self.calls: dict[str, int] = {}

    def _count(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    async def get_features(self):
        self._count("features")
        raise ComfyClientError("route missing", status_code=404, endpoint="/features", category="unsupported")

    async def get_system_stats(self):
        self._count("system_stats")
        raise ComfyClientError("remote unavailable", endpoint="/system_stats", category="unreachable")

    async def get_queue(self):
        self._count("queue")
        return {"queue_running": [], "queue_pending": []}

    async def get_model_types(self):
        self._count("models")
        return ["checkpoints"]

    async def get_embeddings(self):
        self._count("embeddings")
        return ["embedding-a"]

    async def get_object_info(self):
        self._count("object_info")
        return {
            "LoadImage": {
                "category": "image/load",
                "display_name": "Load Image",
                "description": "Load an image",
                "input": {"required": {"image": ["STRING"]}},
                "output": ["IMAGE"],
            },
            "KSampler": {
                "category": "sampling",
                "input": {"required": {"seed": ["INT"]}},
                "output": ["LATENT"],
            },
        }

    async def list_jobs(self, **kwargs):
        self._count("jobs_list")
        return {"jobs": [{"id": "job-1"}], "filters": kwargs}

    async def get_job(self, job_id):
        self._count("job_get")
        return {"id": job_id}

    async def get_workflow_templates(self):
        self._count("templates_list")
        return {"packs": ["pack-a"]}

    async def get_workflow_template(self, pack, filename):
        self._count("template_get")
        return {"pack": pack, "filename": filename}

    async def get_global_subgraphs(self):
        self._count("subgraphs_list")
        return {"subgraphs": []}

    async def get_global_subgraph(self, subgraph_id):
        self._count("subgraph_get")
        return {"id": subgraph_id}

    async def get_node_replacements(self):
        self._count("replacements")
        return {"replacements": []}

    async def list_assets(self, **kwargs):
        self._count("assets_list")
        return {"assets": [], "filters": kwargs}

    async def get_asset(self, asset_id):
        self._count("asset_get")
        return {"id": asset_id}

    async def get_tags(self, **kwargs):
        self._count("tags")
        return {"tags": [], "filters": kwargs}

    async def interrupt(self, prompt_id=None):
        self._count("interrupt")
        return {"prompt_id": prompt_id}

    async def delete_queue(self, *, clear_all=False, prompt_ids=None):
        self._count("queue_delete")
        if clear_all and prompt_ids:
            raise ComfyClientError("clear_all and prompt_ids cannot be used together", category="validation")
        return {"clear_all": clear_all, "prompt_ids": prompt_ids}

    async def free_memory(self, *, unload_models=False, free_memory=False):
        self._count("free_memory")
        return {"unload_models": unload_models, "free_memory": free_memory}

    async def delete_history(self, *, clear_all=False, prompt_ids=None):
        self._count("history_delete")
        return {"clear_all": clear_all, "prompt_ids": prompt_ids}

    async def get_settings(self, setting_id=None):
        self._count("settings_get")
        return {"setting_id": setting_id}

    async def set_settings(self, settings=None, *, setting_id=None, value=None):
        self._count("settings_set")
        return {"settings": settings, "setting_id": setting_id, "value": value}

    async def upload_image(self, file_path, **kwargs):
        self._count("upload_image")
        return {"file_path": file_path, **kwargs}

    async def upload_mask(self, file_path, original_ref, **kwargs):
        self._count("upload_mask")
        return {"file_path": file_path, "original_ref": original_ref, **kwargs}

    async def upload_asset(self, file_path, **kwargs):
        self._count("upload_asset")
        return {"file_path": file_path, **kwargs}


class ManagerClientFake:
    async def status(self, *, refresh=False):
        return {"installed": False, "supports_v4": False, "version": "", "queue": None}

    async def list_installed_packs(self, **kwargs):
        return {"packs": [], "filters": kwargs}

    async def search_node_packs(self, **kwargs):
        return [{"id": "pack-a", "filters": kwargs}]

    async def get_node_mappings(self, **kwargs):
        return {"NodeA": {"node_pack_id": "pack-a"}, "filters": kwargs}

    async def list_snapshots(self, **kwargs):
        return {"snapshots": [], "filters": kwargs}

    async def search_external_models(self, **kwargs):
        return [{"name": "Model A", "filters": kwargs}]

    async def queue_status(self, **kwargs):
        return {"running": [], "filters": kwargs}

    async def queue_action(self, operation, payload, **kwargs):
        return {"operation": operation, "payload": payload, **kwargs}


@pytest.mark.asyncio
async def test_comfy_tools_new_read_handlers_and_partial_capabilities() -> None:
    client = RemoteClientFake()
    tools = ComfyTools(client, Policy(api_token=None, readonly_mode=False, max_workflow_bytes=1000), manager_client=ManagerClientFake())

    capabilities = await tools.capabilities_get(probe_queue=False, resolve_dns=False)
    assert capabilities["features"]["status"] == "unsupported"
    assert capabilities["system_stats"]["status"] == "unreachable"
    assert capabilities["queue"] == {"status": "skipped"}
    assert capabilities["manager"]["status"] == "unsupported"
    assert capabilities["models"] == {"status": "available", "value": ["checkpoints"]}
    await tools.capabilities_get(probe_queue=False, resolve_dns=False)
    assert client.calls["features"] == 1
    await tools.capabilities_get(refresh=True, probe_queue=False, resolve_dns=False)
    assert client.calls["features"] == 2

    assert (await tools.jobs_list(limit=2))["jobs"][0]["id"] == "job-1"
    assert await tools.job_get("job-1") == {"id": "job-1"}
    assert await tools.workflow_templates_list() == {"packs": ["pack-a"]}
    assert await tools.workflow_template_get("pack-a", "demo.json") == {"pack": "pack-a", "filename": "demo.json"}
    assert await tools.global_subgraphs_list() == {"subgraphs": []}
    assert await tools.global_subgraph_get("subgraph-a") == {"id": "subgraph-a"}
    assert await tools.node_replacements_get() == {"replacements": []}
    assert (await tools.assets_list())["assets"] == []
    assert await tools.asset_get("asset-a") == {"id": "asset-a"}
    assert (await tools.tags_list())["tags"] == []


@pytest.mark.asyncio
async def test_nodes_search_filters_metadata_and_uses_refreshable_cache() -> None:
    client = RemoteClientFake()
    tools = ComfyTools(client)

    result = await tools.nodes_search(query="load", input_name="image", output_type="IMAGE", limit=10)
    assert result["count"] == 1
    assert result["nodes"][0]["class_type"] == "LoadImage"
    assert result["nodes"][0]["input"]["required"]["image"] == ["STRING"]
    await tools.nodes_search(query="load", input_name="image", output_type="IMAGE", limit=10)
    assert client.calls["object_info"] == 1
    await tools.nodes_search(query="load", input_name="image", output_type="IMAGE", limit=10, refresh=True)
    assert client.calls["object_info"] == 2

    category_result = await tools.nodes_search(category="sampling", class_type="KSampler")
    assert category_result["nodes"][0]["class_type"] == "KSampler"


@pytest.mark.asyncio
async def test_remote_mutations_apply_readonly_confirmation_token_and_validation() -> None:
    client = RemoteClientFake()
    readonly_tools = ComfyTools(client, Policy(api_token=None, readonly_mode=True, max_workflow_bytes=1000))
    rejected = await readonly_tools.queue_interrupt(prompt_id="job-1", confirm=True)
    assert rejected["status"] == "rejected"
    assert rejected["reason"] == "policy"
    assert client.calls.get("interrupt", 0) == 0

    policy = Policy(api_token="secret", readonly_mode=False, max_workflow_bytes=1000)
    tools = ComfyTools(client, policy)
    assert (await tools.queue_interrupt(prompt_id="job-1"))["status"] == "rejected"
    assert (await tools.queue_interrupt(prompt_id="job-1", confirm=True))["status"] == "rejected"
    executed = await tools.queue_interrupt(prompt_id="job-1", confirm=True, token="secret")
    assert executed == {
        "status": "executed",
        "operation": "comfy_queue_interrupt",
        "result": {"prompt_id": "job-1"},
    }
    assert (await tools.queue_delete(clear_all=True, prompt_ids=["job-1"], confirm=True, token="secret"))["reason"] == "validation"
    assert (await tools.memory_free(free_memory=True, confirm=True, token="secret"))["status"] == "executed"
    assert (await tools.history_delete(clear_all=True, confirm=True, token="secret"))["status"] == "executed"
    assert (await tools.settings_get("ui.theme")) == {"setting_id": "ui.theme"}
    assert (await tools.settings_set(settings={"ui.theme": "dark"}, confirm=True, token="secret"))["status"] == "executed"
    assert (await tools.upload_image("/tmp/image.png", confirm=True, token="secret"))["status"] == "executed"
    assert (await tools.upload_mask("/tmp/mask.png", {"filename": "image.png"}, confirm=True, token="secret"))["status"] == "executed"
    assert (await tools.upload_asset("/tmp/asset.bin", ["tag"], confirm=True, token="secret"))["status"] == "executed"
    assert "secret" not in str(executed)


@pytest.mark.asyncio
async def test_manager_tools_are_structured_and_confirmation_gated() -> None:
    policy = Policy(api_token="secret", readonly_mode=False, max_workflow_bytes=1000)
    tools = ManagerTools(ManagerClientFake(), policy)

    status = await tools.status()
    assert status["status"] == "unavailable"
    assert status["availability"] == "unsupported"
    assert (await tools.packs_list())["status"] == "available"
    assert (await tools.packs_search(query="pack"))["status"] == "available"
    assert (await tools.node_mappings_get())["status"] == "available"
    assert (await tools.snapshots_list())["status"] == "available"
    assert (await tools.models_search(query="model"))["status"] == "available"
    assert (await tools.queue_status())["status"] == "available"

    invalid = await tools.operation("arbitrary_endpoint", confirm=True, token="secret")
    assert invalid["status"] == "rejected"
    missing_confirmation = await tools.operation("update_all", token="secret")
    assert missing_confirmation["status"] == "rejected"
    executed = await tools.operation("install_node_pack", payload={"id": "pack-a"}, confirm=True, token="secret")
    assert executed["status"] == "executed"
