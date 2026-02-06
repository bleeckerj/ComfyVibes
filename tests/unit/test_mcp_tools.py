"""Tests for MCP tool handlers (isolated)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from comfy_mcp.mcp_server.policy import Policy
from comfy_mcp.mcp_server.tools_comfy import ComfyTools
from comfy_mcp.mcp_server.tools_workflows import WorkflowTools
from comfy_mcp.workflow_store.store import WorkflowStore
from comfy_mcp.workflow_store.hashing import sha256_json


class FakeComfyClient:
    """Test double for ComfyClient."""

    async def get_object_info(self):
        return {"nodes": {"A": {}}}

    async def get_queue(self):
        return {"queue_running": []}

    async def get_history(self, prompt_id=None):
        if prompt_id:
            return {prompt_id: {"status": "done"}}
        return {"history": True}

    async def get_model_types(self):
        return ["checkpoints"]

    async def get_models_in_folder(self, folder: str):
        return ["model.safetensors"]

    async def get_embeddings(self):
        return ["embeddingA"]

    async def queue_prompt(self, prompt, client_id=None):
        return {"prompt_id": "abc123"}


class FakeExtractor:
    """Test double for WorkflowExtractor."""

    def extract_from_path(self, path: str, preserve_format: bool = False):
        return type(
            "Result",
            (),
            {
                "workflow": {"1": {"class_type": "KSampler", "inputs": {"seed": 1}}},
                "workflow_format": "api",
                "raw_metadata": {"source": path},
            },
        )()


@pytest.mark.asyncio
async def test_comfy_tools_read_methods() -> None:
    """Comfy tools should proxy to ComfyClient read calls."""
    print("Test: ComfyTools should return proxied ComfyUI data.")
    tools = ComfyTools(FakeComfyClient())

    assert await tools.nodes_list() == {"nodes": {"A": {}}}
    assert await tools.queue_get() == {"queue_running": []}
    assert await tools.history_get() == {"history": True}
    assert await tools.models_list() == ["checkpoints"]
    assert await tools.models_get("checkpoints") == ["model.safetensors"]
    assert await tools.embeddings_list() == ["embeddingA"]


@pytest.mark.asyncio
async def test_workflow_tools_run_and_wait(tmp_path: Path) -> None:
    """Workflow tools should patch and run workflows and wait on history."""
    print("Test: WorkflowTools should run and wait using stored workflows.")
    store = WorkflowStore(tmp_path)
    workflow = {"1": {"class_type": "KSampler", "inputs": {"seed": 1}}}
    params = {
        "schema_version": "1",
        "workflow_id": "demo",
        "workflow_hash": sha256_json(workflow),
        "params": [
            {
                "name": "seed",
                "type": "int",
                "required": True,
                "target": {"mode": "direct", "node_id": "1", "input": "seed"},
            }
        ],
    }
    store.save_workflow("demo", workflow, params=params)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(store, FakeComfyClient(), policy, extractor=FakeExtractor())

    result = await tools.run("demo", {"seed": 7})
    assert result["prompt_id"] == "abc123"

    wait = await tools.wait("abc123", timeout_s=0.1, poll_ms=10)
    assert wait["status"] in {"complete", "timeout"}


def test_workflow_tools_import_from_artifact(tmp_path: Path) -> None:
    """Workflow tools should import workflows from artifacts."""
    print("Test: WorkflowTools should import workflow from artifact and save.")
    store = WorkflowStore(tmp_path)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(store, FakeComfyClient(), policy, extractor=FakeExtractor())

    result = tools.import_from_artifact("/tmp/fake.png", "imported")
    assert result["id"] == "imported"
    assert store.read_workflow("imported")["1"]["class_type"] == "KSampler"
