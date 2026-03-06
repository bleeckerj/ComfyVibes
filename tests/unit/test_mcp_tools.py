"""Tests for MCP tool handlers (isolated)."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import pytest

from comfy_mcp.mcp_server.policy import Policy
from comfy_mcp.mcp_server.tools_comfy import ComfyTools
from comfy_mcp.mcp_server.tools_workflows import WorkflowTools
from comfy_mcp.workflow_store.store import WorkflowStore
from comfy_mcp.workflow_store.hashing import sha256_json


class FakeComfyClient:
    """Test double for ComfyClient."""

    def __init__(self) -> None:
        self.last_prompt = None
        self.uploaded_files: list[dict] = []
        self._base_url = "http://127.0.0.1:8188"

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
        self.last_prompt = prompt
        return {"prompt_id": "abc123"}

    async def upload_image(self, file_path: str, image_type: str = "input", subfolder: str | None = None, overwrite: bool = False):
        payload = {
            "file_path": file_path,
            "image_type": image_type,
            "subfolder": subfolder,
            "overwrite": overwrite,
        }
        self.uploaded_files.append(payload)
        return {"name": Path(file_path).name, "subfolder": subfolder or ""}


class FakeComfyClientCachedNoOutputs(FakeComfyClient):
    """Comfy history response showing successful cached run with empty outputs."""

    async def get_history(self, prompt_id=None):
        if prompt_id:
            return {
                prompt_id: {
                    "outputs": {},
                    "status": {
                        "status_str": "success",
                        "completed": True,
                        "execution_cached": ["79"],
                    },
                }
            }
        return {}


class FakeComfyClientWithProgress(FakeComfyClient):
    """Comfy client double with queue status and websocket-style watch helpers."""

    async def get_queue(self):
        return {
            "queue_running": [[0, "abc123"]],
            "queue_pending": [[1, "def456"]],
        }

    async def watch_prompt(
        self,
        prompt_id: str,
        *,
        inactivity_timeout_s: float = 120.0,
        max_wait_s: float = 1800.0,
        max_events: int = 200,
        client_id: str | None = None,
    ):
        return {
            "status": "complete",
            "prompt_id": prompt_id,
            "events": [
                {"type": "progress", "prompt_id": prompt_id, "node": "3", "percent": 50.0},
                {"type": "executing", "prompt_id": prompt_id, "node": None, "percent": None},
            ],
            "history": {prompt_id: {"outputs": {}}},
            "source": "websocket",
        }

    async def peek_progress(self, *, prompt_id: str | None = None, timeout_s: float = 1.0, client_id: str | None = None):
        return {
            "type": "progress",
            "prompt_id": prompt_id or "abc123",
            "node": "3",
            "percent": 42.5,
        }


class FakeComfyClientWithAspectEnum(FakeComfyClient):
    async def get_object_info(self):
        return {
            "FluxResolutionNode": {
                "input": {
                    "required": {
                        "aspect_ratio": [
                            [
                                "1:1 (Perfect Square)",
                                "4:5 (Artistic Frame)",
                                "16:9 (Panorama)",
                            ]
                        ]
                    }
                }
            }
        }


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
    server_info = await tools.server_info()
    assert server_info["configured_base_url"] == "http://127.0.0.1:8188"
    assert server_info["target"]["host"] == "127.0.0.1"
    assert server_info["target"]["host_is_ip"] is True
    assert server_info["probe"]["ok"] is True
    assert server_info["probe"]["endpoint"].endswith("/queue")


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


@pytest.mark.asyncio
async def test_workflow_tools_run_auto_recovers_hash_mismatch_once(tmp_path: Path) -> None:
    """workflows_run should auto-force patching on hash mismatch and continue."""
    store = WorkflowStore(tmp_path)
    workflow = {"1": {"class_type": "KSampler", "inputs": {"seed": 1}}}
    params = {
        "schema_version": "1",
        "workflow_id": "demo",
        # Intentionally stale hash to simulate local workflow edits since packaging.
        "workflow_hash": "0" * 64,
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
    assert result["workflow_hash_mismatch_recovered"] is True
    assert result["workflow_hash_expected"] == "0" * 64
    assert result["workflow_hash_actual"] == sha256_json(workflow)
    assert result["workflow_force_applied"] is True


@pytest.mark.asyncio
async def test_workflow_tools_run_auto_promotes_float_friendly_int_param_types(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path)
    workflow = {"3": {"class_type": "KSampler", "inputs": {"seed": 1, "denoise": 1}}}
    params = {
        "schema_version": "1",
        "workflow_id": "demo",
        "workflow_hash": sha256_json(workflow),
        "params": [
            {
                "name": "seed",
                "type": "int",
                "required": True,
                "target": {"mode": "direct", "node_id": "3", "input": "seed"},
            },
            {
                "name": "denoise",
                "type": "int",
                "required": False,
                "target": {"mode": "direct", "node_id": "3", "input": "denoise"},
            },
        ],
    }
    store.save_workflow("demo", workflow, params=params)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(store, FakeComfyClient(), policy, extractor=FakeExtractor())

    result = await tools.run("demo", {"seed": 7, "denoise": 0.5})

    assert result["prompt_id"] == "abc123"
    assert result["param_type_auto_promoted"] == ["denoise"]
    updated_params = store.read_params("demo") or {}
    denoise = next(item for item in updated_params.get("params", []) if item.get("name") == "denoise")
    assert denoise.get("type") == "float"


@pytest.mark.asyncio
async def test_workflow_tools_status_reports_queue_and_progress(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(store, FakeComfyClientWithProgress(), policy, extractor=FakeExtractor())

    payload = await tools.status(prompt_id="abc123", include_progress=True, progress_timeout_s=0.1)

    assert payload["status"] in {"running", "complete"}
    assert payload["queue_running_count"] == 1
    assert payload["queue_pending_count"] == 1
    assert payload.get("progress", {}).get("node") == "3"


def test_workflow_tools_image_info_reports_dimensions_and_ratio(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(store, FakeComfyClient(), policy, extractor=FakeExtractor())

    png_1x1 = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+c6xkAAAAASUVORK5CYII="
    )
    image_path = tmp_path / "one.png"
    image_path.write_bytes(png_1x1)

    payload = tools.image_info(str(image_path))

    assert payload["width"] == 1
    assert payload["height"] == 1
    assert payload["aspect_ratio_simple"] == "1:1"
    assert payload["orientation"] == "square"


@pytest.mark.asyncio
async def test_workflow_tools_watch_prefers_websocket_when_available(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(store, FakeComfyClientWithProgress(), policy, extractor=FakeExtractor())

    payload = await tools.watch("abc123", inactivity_timeout_s=0.2, max_wait_s=1.0, include_history=True)

    assert payload["status"] == "complete"
    assert payload["source"] == "websocket"
    assert payload["events"]
    assert payload["history"]


@pytest.mark.asyncio
async def test_workflow_tools_run_honors_wait_timeout_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """workflows_run should pass caller wait timeout/poll overrides to wait-and-extract."""
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
    captured: dict[str, object] = {}

    async def _fake_wait_and_extract(  # noqa: ANN202
        prompt_id: str,
        timeout_s: float = 120.0,
        poll_ms: int = 1000,
        queued_workflow=None,
        queued_at=None,
    ):
        captured["prompt_id"] = prompt_id
        captured["timeout_s"] = timeout_s
        captured["poll_ms"] = poll_ms
        return {"status": "complete", "prompt_id": prompt_id, "output_images": []}

    monkeypatch.setattr(tools, "_wait_and_extract", _fake_wait_and_extract)

    result = await tools.run("demo", {"seed": 7}, wait_timeout_s=420, wait_poll_ms=1500)

    assert result["prompt_id"] == "abc123"
    assert captured["prompt_id"] == "abc123"
    assert captured["timeout_s"] == 420.0
    assert captured["poll_ms"] == 1500


@pytest.mark.asyncio
async def test_workflow_tools_run_uploads_local_path_for_loadimage(tmp_path: Path) -> None:
    """workflows_run should upload local image paths for LoadImage params before queueing."""
    store = WorkflowStore(tmp_path)
    workflow = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "placeholder.png"}},
        "3": {"class_type": "KSampler", "inputs": {"seed": 1}},
    }
    params = {
        "schema_version": "1",
        "workflow_id": "demo",
        "workflow_hash": sha256_json(workflow),
        "params": [
            {
                "name": "image",
                "type": "string",
                "required": True,
                "target": {"mode": "direct", "node_id": "1", "input": "image"},
            }
        ],
    }
    store.save_workflow("demo", workflow, params=params)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    client = FakeComfyClient()
    tools = WorkflowTools(store, client, policy, extractor=FakeExtractor())

    source_image = tmp_path / "source.png"
    source_image.write_bytes(b"fakepng")

    result = await tools.run("demo", {"image": str(source_image)})

    assert result["prompt_id"] == "abc123"
    assert client.uploaded_files
    assert client.last_prompt is not None
    assert client.last_prompt["1"]["inputs"]["image"] == "source.png"


@pytest.mark.asyncio
async def test_workflow_tools_run_auto_preserves_aspect_from_local_input(tmp_path: Path) -> None:
    """workflows_run should auto-select nearest allowed aspect when input image is local and no explicit aspect override is provided."""
    store = WorkflowStore(tmp_path)
    workflow = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "placeholder.png"}},
        "115": {
            "class_type": "FluxResolutionNode",
            "inputs": {
                "aspect_ratio": "19:9 (Cinematic Ultrawide)",
                "custom_ratio": False,
                "custom_aspect_ratio": "1:1",
            },
        },
        "3": {"class_type": "KSampler", "inputs": {"seed": 1}},
    }
    params = {
        "schema_version": "1",
        "workflow_id": "demo",
        "workflow_hash": sha256_json(workflow),
        "params": [
            {
                "name": "image",
                "type": "string",
                "required": True,
                "target": {"mode": "direct", "node_id": "1", "input": "image"},
            },
            {
                "name": "aspect_ratio",
                "type": "string",
                "required": False,
                "target": {"mode": "direct", "node_id": "115", "input": "aspect_ratio"},
            },
            {
                "name": "custom_ratio",
                "type": "bool",
                "required": False,
                "target": {"mode": "direct", "node_id": "115", "input": "custom_ratio"},
            },
            {
                "name": "custom_aspect_ratio",
                "type": "string",
                "required": False,
                "target": {"mode": "direct", "node_id": "115", "input": "custom_aspect_ratio"},
            },
        ],
    }
    store.save_workflow("demo", workflow, params=params)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    client = FakeComfyClientWithAspectEnum()
    tools = WorkflowTools(store, client, policy, extractor=FakeExtractor())

    png_1x1 = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+c6xkAAAAASUVORK5CYII="
    )
    source_image = tmp_path / "source.png"
    source_image.write_bytes(png_1x1)

    result = await tools.run("demo", {"image": str(source_image)})

    assert result["prompt_id"] == "abc123"
    assert result["auto_aspect_ratio_source"] == "1:1"
    assert result["auto_aspect_ratio_applied"] == "1:1"
    assert result["auto_aspect_ratio_anchor"] == "1:1 (Perfect Square)"
    assert client.last_prompt is not None
    assert client.last_prompt["115"]["inputs"]["aspect_ratio"] == "1:1 (Perfect Square)"
    assert client.last_prompt["115"]["inputs"]["custom_ratio"] is True
    assert client.last_prompt["115"]["inputs"]["custom_aspect_ratio"] == "1:1"


@pytest.mark.asyncio
async def test_workflow_tools_run_auto_preserves_aspect_when_defaults_are_passed(tmp_path: Path) -> None:
    """Auto-preserve should still apply when caller passes default aspect fields."""
    store = WorkflowStore(tmp_path)
    workflow = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "placeholder.png"}},
        "115": {
            "class_type": "FluxResolutionNode",
            "inputs": {
                "aspect_ratio": "19:9 (Cinematic Ultrawide)",
                "custom_ratio": False,
                "custom_aspect_ratio": "1:1",
            },
        },
        "3": {"class_type": "KSampler", "inputs": {"seed": 1}},
    }
    params = {
        "schema_version": "1",
        "workflow_id": "demo",
        "workflow_hash": sha256_json(workflow),
        "params": [
            {
                "name": "image",
                "type": "string",
                "required": True,
                "target": {"mode": "direct", "node_id": "1", "input": "image"},
            },
            {
                "name": "aspect_ratio",
                "type": "string",
                "default": "19:9 (Cinematic Ultrawide)",
                "required": False,
                "target": {"mode": "direct", "node_id": "115", "input": "aspect_ratio"},
            },
            {
                "name": "custom_ratio",
                "type": "bool",
                "default": False,
                "required": False,
                "target": {"mode": "direct", "node_id": "115", "input": "custom_ratio"},
            },
            {
                "name": "custom_aspect_ratio",
                "type": "string",
                "default": "1:1",
                "required": False,
                "target": {"mode": "direct", "node_id": "115", "input": "custom_aspect_ratio"},
            },
        ],
    }
    store.save_workflow("demo", workflow, params=params)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    client = FakeComfyClientWithAspectEnum()
    tools = WorkflowTools(store, client, policy, extractor=FakeExtractor())

    png_1x1 = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+c6xkAAAAASUVORK5CYII="
    )
    source_image = tmp_path / "source.png"
    source_image.write_bytes(png_1x1)

    result = await tools.run(
        "demo",
        {
            "image": str(source_image),
            "aspect_ratio": "19:9 (Cinematic Ultrawide)",
            "custom_ratio": False,
            "custom_aspect_ratio": "1:1",
        },
    )

    assert result["prompt_id"] == "abc123"
    assert result["auto_aspect_ratio_source"] == "1:1"
    assert result["auto_aspect_ratio_applied"] == "1:1"
    assert result["auto_aspect_ratio_anchor"] == "1:1 (Perfect Square)"
    assert client.last_prompt is not None
    assert client.last_prompt["115"]["inputs"]["aspect_ratio"] == "1:1 (Perfect Square)"
    assert client.last_prompt["115"]["inputs"]["custom_ratio"] is True
    assert client.last_prompt["115"]["inputs"]["custom_aspect_ratio"] == "1:1"


@pytest.mark.asyncio
async def test_run_aspect_ratio_adjustment_honors_wait_timeout_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Aspect-ratio tool should pass caller wait timeout/poll overrides to wait-and-extract."""
    store = WorkflowStore(tmp_path)
    workflow = {
        "109": {"class_type": "LoadImage", "inputs": {"image": "placeholder.png"}},
        "115": {
            "class_type": "FluxResolutionNode",
            "inputs": {
                "aspect_ratio": "16:9 (Panorama)",
                "custom_ratio": False,
                "custom_aspect_ratio": "16:9",
            },
        },
        "79": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI"}},
    }
    store.save_workflow("aspect_ratio_adjustment", workflow, meta={"id": "aspect_ratio_adjustment"})
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(store, FakeComfyClient(), policy, extractor=FakeExtractor())
    captured: dict[str, object] = {}

    async def _fake_wait_and_extract(  # noqa: ANN202
        prompt_id: str,
        timeout_s: float = 120.0,
        poll_ms: int = 1000,
        queued_workflow=None,
        queued_at=None,
    ):
        captured["prompt_id"] = prompt_id
        captured["timeout_s"] = timeout_s
        captured["poll_ms"] = poll_ms
        return {"status": "complete", "prompt_id": prompt_id, "output_images": []}

    monkeypatch.setattr(tools, "_wait_and_extract", _fake_wait_and_extract)

    source_image = tmp_path / "source.png"
    source_image.write_bytes(b"fakepng")
    result = await tools.run_aspect_ratio_adjustment(
        image_path=str(source_image),
        aspect_ratio="16:9 (Panorama)",
        wait_timeout_s=480,
        wait_poll_ms=900,
    )

    assert result["prompt_id"] == "abc123"
    assert result["workflow_id"] == "aspect_ratio_adjustment"
    assert result["aspect_ratio"] == "16:9 (Panorama)"
    assert captured["prompt_id"] == "abc123"
    assert captured["timeout_s"] == 480.0
    assert captured["poll_ms"] == 900


@pytest.mark.asyncio
async def test_workflow_tools_run_maps_ratio_token_to_custom_mode(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path)
    workflow = {
        "115": {
            "class_type": "FluxResolutionNode",
            "inputs": {
                "aspect_ratio": "1:1 (Perfect Square)",
                "custom_ratio": False,
                "custom_aspect_ratio": "1:1",
            },
        },
        "3": {"class_type": "KSampler", "inputs": {"seed": 1}},
    }
    params = {
        "schema_version": "1",
        "workflow_id": "demo",
        "workflow_hash": sha256_json(workflow),
        "params": [
            {
                "name": "aspect_ratio",
                "type": "string",
                "required": False,
                "target": {"mode": "direct", "node_id": "115", "input": "aspect_ratio"},
            },
            {
                "name": "custom_ratio",
                "type": "bool",
                "required": False,
                "target": {"mode": "direct", "node_id": "115", "input": "custom_ratio"},
            },
            {
                "name": "custom_aspect_ratio",
                "type": "string",
                "required": False,
                "target": {"mode": "direct", "node_id": "115", "input": "custom_aspect_ratio"},
            },
        ],
    }
    store.save_workflow("demo", workflow, params=params)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    client = FakeComfyClientWithAspectEnum()
    tools = WorkflowTools(store, client, policy, extractor=FakeExtractor())

    result = await tools.run(
        "demo",
        {
            "aspect_ratio": "4:5",
            "custom_ratio": False,
            "custom_aspect_ratio": "1:1",
        },
    )

    assert result["prompt_id"] == "abc123"
    assert client.last_prompt is not None
    inputs = client.last_prompt["115"]["inputs"]
    assert inputs["aspect_ratio"] == "4:5 (Artistic Frame)"
    assert inputs["custom_ratio"] is True
    assert inputs["custom_aspect_ratio"] == "4:5"


@pytest.mark.asyncio
async def test_run_aspect_ratio_adjustment_maps_ratio_token_to_allowed_enum(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path)
    workflow = {
        "109": {"class_type": "LoadImage", "inputs": {"image": "placeholder.png"}},
        "115": {
            "class_type": "FluxResolutionNode",
            "inputs": {
                "aspect_ratio": "1:1 (Perfect Square)",
                "custom_ratio": False,
                "custom_aspect_ratio": "1:1",
            },
        },
        "79": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI"}},
    }
    store.save_workflow("aspect_ratio_adjustment", workflow, meta={"id": "aspect_ratio_adjustment"})
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    client = FakeComfyClientWithAspectEnum()
    tools = WorkflowTools(store, client, policy, extractor=FakeExtractor())

    source_image = tmp_path / "source.png"
    source_image.write_bytes(b"fakepng")
    result = await tools.run_aspect_ratio_adjustment(
        image_path=str(source_image),
        aspect_ratio="4:5",
    )

    assert client.last_prompt is not None
    inputs = client.last_prompt["115"]["inputs"]
    assert inputs["aspect_ratio"] == "4:5 (Artistic Frame)"
    assert inputs["custom_ratio"] is True
    assert inputs["custom_aspect_ratio"] == "4:5"
    assert result["aspect_ratio_applied"] == "4:5"
    assert result["aspect_ratio_anchor"] == "4:5 (Artistic Frame)"


@pytest.mark.asyncio
async def test_workflow_tools_run_sets_unique_filename_prefix_for_saveimage(tmp_path: Path) -> None:
    """workflows_run should set a unique runtime filename_prefix for SaveImage outputs."""
    store = WorkflowStore(tmp_path)
    workflow = {
        "3": {"class_type": "KSampler", "inputs": {"seed": 1}},
        "79": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI", "images": ["3", 0]}},
    }
    params = {
        "schema_version": "1",
        "workflow_id": "demo",
        "workflow_hash": sha256_json(workflow),
        "params": [
            {
                "name": "seed",
                "type": "int",
                "required": True,
                "target": {"mode": "direct", "node_id": "3", "input": "seed"},
            },
            {
                "name": "filename_prefix",
                "type": "string",
                "required": False,
                "target": {"mode": "direct", "node_id": "79", "input": "filename_prefix"},
            },
        ],
    }
    store.save_workflow("demo", workflow, params=params)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    client = FakeComfyClient()
    tools = WorkflowTools(store, client, policy, extractor=FakeExtractor())

    result = await tools.run("demo", {"seed": 7})

    assert result["prompt_id"] == "abc123"
    assert result.get("filename_prefix_used", "").startswith("mcp_demo_")
    assert client.last_prompt is not None
    assert client.last_prompt["79"]["inputs"]["filename_prefix"].startswith("mcp_demo_")


@pytest.mark.asyncio
async def test_workflow_tools_run_uses_semantic_camelcase_prefix_from_prompt_text(tmp_path: Path, monkeypatch) -> None:
    store = WorkflowStore(tmp_path)
    workflow = {
        "3": {"class_type": "KSampler", "inputs": {"seed": 1}},
        "11": {"class_type": "CLIPTextEncode", "inputs": {"text": "default"}},
        "79": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI", "images": ["3", 0]}},
    }
    params = {
        "schema_version": "1",
        "workflow_id": "demo",
        "workflow_hash": sha256_json(workflow),
        "params": [
            {
                "name": "seed",
                "type": "int",
                "required": True,
                "target": {"mode": "direct", "node_id": "3", "input": "seed"},
            },
            {
                "name": "positive_prompt",
                "type": "string",
                "required": False,
                "target": {"mode": "direct", "node_id": "11", "input": "text"},
            },
            {
                "name": "filename_prefix",
                "type": "string",
                "required": False,
                "target": {"mode": "direct", "node_id": "79", "input": "filename_prefix"},
            },
        ],
    }
    store.save_workflow("demo", workflow, params=params)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    client = FakeComfyClient()
    tools = WorkflowTools(store, client, policy, extractor=FakeExtractor())
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = await tools.run(
        "demo",
        {"seed": 7, "positive_prompt": "neon city street rain reflections"},
    )

    assert result["prompt_id"] == "abc123"
    assert result.get("filename_prefix_used", "").startswith("NeonCityStreetRainReflections_")
    assert client.last_prompt is not None
    assert client.last_prompt["79"]["inputs"]["filename_prefix"].startswith("NeonCityStreetRainReflections_")


@pytest.mark.asyncio
async def test_workflow_tools_run_respects_user_filename_prefix_override(tmp_path: Path) -> None:
    """workflows_run should not overwrite caller-provided filename_prefix."""
    store = WorkflowStore(tmp_path)
    workflow = {
        "3": {"class_type": "KSampler", "inputs": {"seed": 1}},
        "79": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI", "images": ["3", 0]}},
    }
    params = {
        "schema_version": "1",
        "workflow_id": "demo",
        "workflow_hash": sha256_json(workflow),
        "params": [
            {
                "name": "seed",
                "type": "int",
                "required": True,
                "target": {"mode": "direct", "node_id": "3", "input": "seed"},
            },
            {
                "name": "filename_prefix",
                "type": "string",
                "required": False,
                "target": {"mode": "direct", "node_id": "79", "input": "filename_prefix"},
            },
        ],
    }
    store.save_workflow("demo", workflow, params=params)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    client = FakeComfyClient()
    tools = WorkflowTools(store, client, policy, extractor=FakeExtractor())

    result = await tools.run("demo", {"seed": 7, "filename_prefix": "my_custom_prefix"})

    assert result["prompt_id"] == "abc123"
    assert "filename_prefix_used" not in result
    assert client.last_prompt is not None
    assert client.last_prompt["79"]["inputs"]["filename_prefix"] == "my_custom_prefix"


@pytest.mark.asyncio
async def test_workflow_tools_run_reports_cached_success_with_no_output_images(tmp_path: Path) -> None:
    """workflows_run should explain Comfy cached success when history outputs are empty."""
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
    tools = WorkflowTools(store, FakeComfyClientCachedNoOutputs(), policy, extractor=FakeExtractor())

    result = await tools.run("demo", {"seed": 7})

    assert result["status"] == "complete"
    assert result["prompt_id"] == "abc123"
    assert result["output_images"] == []
    assert result["likely_cached"] is True
    assert "force=true" in result["message"]


@pytest.mark.asyncio
async def test_workflow_tools_run_can_infer_outputs_from_output_dir_when_history_is_empty(tmp_path: Path) -> None:
    """workflows_run should fallback to local output-dir scan when Comfy history omits image outputs."""
    store = WorkflowStore(tmp_path)
    workflow = {
        "1": {"class_type": "KSampler", "inputs": {"seed": 1}},
        "79": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI", "images": ["1", 0]}},
    }
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
            },
            {
                "name": "filename_prefix",
                "type": "string",
                "required": False,
                "target": {"mode": "direct", "node_id": "79", "input": "filename_prefix"},
            },
        ],
    }
    store.save_workflow("demo", workflow, params=params)
    output_dir = tmp_path / "comfy-output"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "ComfyUI_99999_.png"
    output_file.write_bytes(b"png")

    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(
        store,
        FakeComfyClientCachedNoOutputs(),
        policy,
        extractor=FakeExtractor(),
        comfy_output_dir=output_dir,
    )

    result = await tools.run("demo", {"seed": 7, "filename_prefix": "ComfyUI"})

    assert result["status"] == "complete"
    assert result["prompt_id"] == "abc123"
    assert result["output_images_source"] == "filesystem_fallback"
    assert result["output_images"]
    assert result["output_images"][0]["filename"] == "ComfyUI_99999_.png"
    assert result["output_images"][0]["local_path"] == str(output_file)
    assert result["output_images"][0]["inferred"] is True


@pytest.mark.asyncio
async def test_workflow_tools_run_can_infer_outputs_from_dated_subfolder(tmp_path: Path) -> None:
    """Fallback output scan should support nested date-stamped output directories."""
    store = WorkflowStore(tmp_path)
    workflow = {
        "1": {"class_type": "KSampler", "inputs": {"seed": 1}},
        "79": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI", "images": ["1", 0]}},
    }
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
            },
            {
                "name": "filename_prefix",
                "type": "string",
                "required": False,
                "target": {"mode": "direct", "node_id": "79", "input": "filename_prefix"},
            },
        ],
    }
    store.save_workflow("demo", workflow, params=params)
    output_dir = tmp_path / "comfy-output"
    dated_subfolder = output_dir / "2026-02-14"
    dated_subfolder.mkdir(parents=True, exist_ok=True)
    output_file = dated_subfolder / "ComfyUI_77777_.png"
    output_file.write_bytes(b"png")

    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(
        store,
        FakeComfyClientCachedNoOutputs(),
        policy,
        extractor=FakeExtractor(),
        comfy_output_dir=output_dir,
    )

    result = await tools.run("demo", {"seed": 7, "filename_prefix": "ComfyUI"})

    assert result["status"] == "complete"
    assert result["output_images_source"] == "filesystem_fallback"
    assert result["output_images"]
    assert result["output_images"][0]["filename"] == "ComfyUI_77777_.png"
    assert result["output_images"][0]["subfolder"] == "2026-02-14"
    assert "subfolder=2026-02-14" in result["output_images"][0]["view_url"]


def test_workflow_tools_import_from_artifact(tmp_path: Path) -> None:
    """Workflow tools should import workflows from artifacts."""
    print("Test: WorkflowTools should import workflow from artifact and save.")
    store = WorkflowStore(tmp_path)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(store, FakeComfyClient(), policy, extractor=FakeExtractor())

    result = tools.import_from_artifact("/tmp/fake.png", "imported")
    assert result["id"] == "imported"
    assert store.read_workflow("imported")["1"]["class_type"] == "KSampler"
    params = store.read_params("imported")
    assert params is not None
    assert any(item.get("name") == "seed" for item in params.get("params", []))
    meta = store.read_meta("imported") or {}
    assert "seed" in (meta.get("requires", {}).get("params") or [])


def test_workflow_tools_package_single(tmp_path: Path) -> None:
    """Workflow packaging should update meta heuristics and params from workflow."""
    store = WorkflowStore(tmp_path)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(store, FakeComfyClient(), policy, extractor=FakeExtractor())
    workflow = {"1": {"class_type": "KSampler", "inputs": {"seed": 11, "steps": 8, "cfg": 1.1}}}
    store.save_workflow(
        "demo",
        workflow,
        meta={"id": "demo", "name": "Demo", "description": "", "tags": []},
    )

    result = tools.package(
        "demo",
        hints={
            "description": "A packaged workflow",
            "use_cases": ["Generate controlled variants"],
            "tags": ["test-tag"],
        },
    )

    assert result["packaged"] is True
    assert result["params_count"] >= 3
    meta = store.read_meta("demo") or {}
    assert meta["description"] == "A packaged workflow"
    assert "Generate controlled variants" in (meta.get("use_cases") or [])
    assert "test-tag" in (meta.get("tags") or [])
    params = store.read_params("demo") or {}
    assert any(item.get("name") == "seed" for item in params.get("params", []))


def test_workflow_tools_package_template_get(tmp_path: Path) -> None:
    """Template tool should return editable capability-hints schema."""
    store = WorkflowStore(tmp_path)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(store, FakeComfyClient(), policy, extractor=FakeExtractor())
    workflow = {"1": {"class_type": "KSampler", "inputs": {"seed": 5}}}
    store.save_workflow("demo", workflow, meta={"id": "demo", "name": "Demo", "description": "", "tags": []})

    result = tools.package_template_get("demo")
    template = result["hints_template"]
    assert result["workflow_id"] == "demo"
    assert "use_cases" in template
    assert "io_contract" in template
    assert "examples" in template


@pytest.mark.asyncio
async def test_workflow_tools_import_from_photarium(tmp_path: Path, monkeypatch) -> None:
    """Photarium import tool should extract, normalize, and save packaged workflow."""
    store = WorkflowStore(tmp_path)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(store, FakeComfyClient(), policy, extractor=FakeExtractor())

    async def _fake_remote(base_url: str, tool_name: str, args: dict):
        assert base_url == "http://127.0.0.1:8787"
        if tool_name == "photarium_extract_workflow":
            assert args["namespace"] == "cf-default"
            return {
                "extracted": True,
                "prompt": {"1": {"class_type": "KSampler", "inputs": {"seed": 123, "steps": 8}}},
            }
        if tool_name == "photarium_get":
            assert args["namespace"] == "cf-default"
            return {"tags": ["photarium-source", "editorial"]}
        raise AssertionError(f"Unexpected remote tool call: {tool_name}")

    monkeypatch.setattr(tools, "_call_remote_tool", _fake_remote)

    result = await tools.import_from_photarium(
        image_id="img_123",
        workflow_id="from_photarium",
        photarium_mcp_url="http://127.0.0.1:8787",
        namespace="cf-default",
    )

    assert result["id"] == "from_photarium"
    assert result["image_id"] == "img_123"
    assert result["extraction_source"] == "photarium_extract_workflow"
    workflow = store.read_workflow("from_photarium")
    assert workflow["1"]["class_type"] == "KSampler"
    params = store.read_params("from_photarium") or {}
    assert any(item.get("name") == "seed" for item in params.get("params", []))
    meta = store.read_meta("from_photarium") or {}
    assert "photarium-source" in (meta.get("tags") or [])


@pytest.mark.asyncio
async def test_workflow_tools_import_from_photarium_falls_back_to_extras(tmp_path: Path, monkeypatch) -> None:
    """Photarium import should use extras.comfyWorkflow when PNG extraction is unavailable."""
    store = WorkflowStore(tmp_path)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(store, FakeComfyClient(), policy, extractor=FakeExtractor())

    async def _fake_remote(base_url: str, tool_name: str, args: dict):
        assert base_url == "http://127.0.0.1:8787"
        if tool_name == "photarium_extract_workflow":
            assert args["namespace"] == "cf-default"
            return {
                "extracted": False,
                "message": "Not a PNG file; Comfy workflow extraction currently supports PNG embedded metadata.",
            }
        if tool_name == "photarium_extras_get":
            assert args["namespace"] == "cf-default"
            return {
                "imageId": "img_456",
                "record": {
                    "comfyWorkflow": {
                        "workflowJson": json.dumps(
                            {"1": {"class_type": "KSampler", "inputs": {"seed": 321, "steps": 8}}}
                        )
                    }
                },
            }
        if tool_name == "photarium_get":
            assert args["namespace"] == "cf-default"
            return {"tags": ["photarium-source", "jpeg-original"]}
        raise AssertionError(f"Unexpected remote tool call: {tool_name}")

    monkeypatch.setattr(tools, "_call_remote_tool", _fake_remote)

    result = await tools.import_from_photarium(
        image_id="img_456",
        workflow_id="from_photarium_extras",
        photarium_mcp_url="http://127.0.0.1:8787",
        namespace="cf-default",
    )

    assert result["id"] == "from_photarium_extras"
    assert result["image_id"] == "img_456"
    assert result["extraction_source"] == "photarium_extras_get"
    workflow = store.read_workflow("from_photarium_extras")
    assert workflow["1"]["class_type"] == "KSampler"
    params = store.read_params("from_photarium_extras") or {}
    assert any(item.get("name") == "seed" for item in params.get("params", []))


@pytest.mark.asyncio
async def test_workflow_tools_extract_from_photarium_downloads_original_when_needed(
    tmp_path: Path, monkeypatch
) -> None:
    """Photarium extraction should fall back to downloading original artifact and extracting locally."""
    store = WorkflowStore(tmp_path)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(store, FakeComfyClient(), policy, extractor=FakeExtractor())

    async def _fake_remote(base_url: str, tool_name: str, args: dict):
        assert base_url == "http://127.0.0.1:8787"
        if tool_name == "photarium_extract_workflow":
            return {
                "extracted": False,
                "message": "Not a PNG file; Comfy workflow extraction currently supports PNG embedded metadata.",
            }
        if tool_name == "photarium_extras_get":
            return {"record": {}}
        if tool_name == "photarium_get":
            return {"raw": {"originalUrl": "https://example.invalid/original.png"}}
        if tool_name == "photarium_download_original":
            # Simulate the tool writing the file at savedPath.
            saved = Path(args["savePath"])
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(b"fake-png")
            return {"savedPath": str(saved), "filename": "original.png", "contentType": "image/png"}
        raise AssertionError(f"Unexpected remote tool call: {tool_name}")

    monkeypatch.setattr(tools, "_call_remote_tool", _fake_remote)

    result = await tools.extract_from_photarium(
        image_id="img_789",
        photarium_mcp_url="http://127.0.0.1:8787",
        namespace="cf-default",
    )

    assert result["workflow_format"] == "api"
    assert isinstance(result.get("workflow"), dict)
    assert result["workflow"]["1"]["class_type"] == "KSampler"
    assert result["extraction_source"] == "photarium_download_original"


def test_workflow_tools_recompile_updates_param_schema_and_hash(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=100_000)
    tools = WorkflowTools(store, FakeComfyClient(), policy, extractor=FakeExtractor())
    workflow = {
        "3": {
            "class_type": "KSampler",
            "inputs": {"seed": 1, "steps": 4, "cfg": 1.0, "denoise": 1},
        }
    }
    store.save_workflow("aspect_ratio_adjustment", workflow)

    result = tools.recompile(
        workflow_id="aspect_ratio_adjustment",
        param_overrides=[{"name": "denoise", "type": "float", "default": 1.0}],
    )

    assert result["packaged"] is True
    assert result["workflow_updates_applied"] == 0
    assert result["params_overridden"] == ["denoise"]
    params = store.read_params("aspect_ratio_adjustment") or {}
    denoise = next(item for item in params.get("params", []) if item.get("name") == "denoise")
    assert denoise.get("type") == "float"
    assert denoise.get("default") == 1.0
    assert params.get("workflow_hash") == sha256_json(workflow)


def test_workflow_tools_recompile_updates_workflow_inputs(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=100_000)
    tools = WorkflowTools(store, FakeComfyClient(), policy, extractor=FakeExtractor())
    workflow = {
        "3": {
            "class_type": "KSampler",
            "inputs": {"seed": 1, "steps": 4, "cfg": 1.0, "denoise": 1},
        }
    }
    store.save_workflow("aspect_ratio_adjustment", workflow)

    result = tools.recompile(
        workflow_id="aspect_ratio_adjustment",
        workflow_input_updates=[{"node_id": "3", "input": "denoise", "value": 0.55}],
    )

    assert result["workflow_updates_applied"] == 1
    updated_workflow = store.read_workflow("aspect_ratio_adjustment")
    assert updated_workflow["3"]["inputs"]["denoise"] == 0.55
    params = store.read_params("aspect_ratio_adjustment") or {}
    denoise = next(item for item in params.get("params", []) if item.get("name") == "denoise")
    assert denoise.get("type") == "float"
    assert denoise.get("default") == 0.55


def test_workflow_tools_save_infers_params_and_syncs_requires(tmp_path: Path) -> None:
    """Workflow save should package params + metadata and keep requires in sync."""
    store = WorkflowStore(tmp_path)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(store, FakeComfyClient(), policy, extractor=FakeExtractor())

    workflow = {"1": {"class_type": "KSampler", "inputs": {"seed": 11, "steps": 4, "cfg": 1.2}}}
    store.save_workflow(
        "demo",
        workflow,
        meta={"id": "demo", "name": "Demo", "description": "", "tags": [], "requires": {"inputs": []}},
    )

    result = tools.save("demo", workflow)
    assert result["packaged"] is True
    assert result["params_count"] >= 3

    params = store.read_params("demo")
    assert params is not None
    names = [item.get("name") for item in params.get("params", [])]
    assert "seed" in names
    assert "steps" in names
    assert "cfg" in names
    meta = store.read_meta("demo") or {}
    assert "seed" in (meta.get("requires", {}).get("params") or [])


def test_workflow_tools_save_creates_meta_when_missing(tmp_path: Path) -> None:
    """Workflow save should generate meta.json for new entries."""
    store = WorkflowStore(tmp_path)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(store, FakeComfyClient(), policy, extractor=FakeExtractor())

    workflow = {"1": {"class_type": "KSampler", "inputs": {"seed": 19, "steps": 8}}}
    tools.save("new_entry", workflow)

    meta = store.read_meta("new_entry") or {}
    assert meta.get("id") == "new_entry"
    assert meta.get("name")
    assert "seed" in (meta.get("requires", {}).get("params") or [])


def test_workflow_tools_list_includes_source_and_params_count(tmp_path: Path) -> None:
    """Workflow list should expose source root metadata for operator visibility."""
    store = WorkflowStore(tmp_path)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(store, FakeComfyClient(), policy, extractor=FakeExtractor())

    workflow = {"1": {"class_type": "KSampler", "inputs": {"seed": 1}}}
    tools.save("demo", workflow)

    listing = tools.list()
    assert listing["primary_root"] == str(tmp_path.resolve())
    assert listing["extra_roots"] == []
    assert len(listing["workflows"]) == 1
    item = listing["workflows"][0]
    assert item["id"] == "demo"
    assert item["params_count"] >= 1
    assert item["source_root"] == str(tmp_path.resolve())
    assert item["is_primary_root"] is True


def test_workflow_tools_capabilities(tmp_path: Path) -> None:
    """Workflow tools should expose capability-card discovery."""
    store = WorkflowStore(tmp_path)
    workflow = {"1": {"class_type": "LoadImage", "inputs": {"image": "x.png"}}}
    store.save_workflow(
        "image_edit",
        workflow,
        meta={
            "id": "image_edit",
            "name": "Image Edit",
            "description": "Edit an image with text prompts.",
            "tags": ["image-edit", "fashion"],
            "use_cases": ["fabric restyle"],
        },
        params={
            "schema_version": "1",
            "workflow_id": "image_edit",
            "workflow_hash": sha256_json(workflow),
            "params": [
                {
                    "name": "image_filename",
                    "type": "string",
                    "required": True,
                    "description": "Input image filename",
                }
            ],
        },
    )
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(store, FakeComfyClient(), policy, extractor=FakeExtractor())

    listing = tools.capabilities_list(query="fashion", include_params=False)
    assert listing["count"] == 1
    assert listing["capabilities"][0]["workflow_id"] == "image_edit"

    detail = tools.capabilities_get("image_edit", include_params=True)
    assert detail["capability"]["required_params"] == ["image_filename"]


def test_workflow_tools_search_loose_text_to_image_aliases(tmp_path: Path) -> None:
    """Workflow search should match natural language phrasing for txt2img workflows."""
    store = WorkflowStore(tmp_path)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(store, FakeComfyClient(), policy, extractor=FakeExtractor())

    workflow = {"1": {"class_type": "KSampler", "inputs": {"seed": 1}}}
    store.save_workflow(
        "nunchaku_qwen_txt2img_00004",
        workflow,
        meta={
            "id": "nunchaku_qwen_txt2img_00004",
            "name": "Nunchaku Qwen Text-to-Image",
            "description": "Qwen text generation workflow.",
            "tags": ["nunchaku", "text-to-image"],
        },
    )
    store.save_workflow(
        "image_edit_demo",
        workflow,
        meta={
            "id": "image_edit_demo",
            "name": "Image Edit Demo",
            "description": "Image editing workflow.",
            "tags": ["image-edit"],
        },
    )

    result = tools.search("What params must I provide for the nunchaku text 2 image workflow?")

    assert result["count"] >= 1
    assert result["workflows"][0]["id"] == "nunchaku_qwen_txt2img_00004"


def test_workflow_tools_search_prioritizes_flux_klein_for_image_edit_intent(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(store, FakeComfyClient(), policy, extractor=FakeExtractor())
    workflow = {"1": {"class_type": "KSampler", "inputs": {"seed": 1}}}

    store.save_workflow(
        "flux_2_klein_4B",
        workflow,
        meta={
            "id": "flux_2_klein_4B",
            "name": "Flux 2 Klein 4B",
            "description": "Edit existing images with prompt-guided controls.",
            "tags": ["image-edit", "img2img"],
        },
    )
    store.save_workflow(
        "image_edit_demo",
        workflow,
        meta={
            "id": "image_edit_demo",
            "name": "Image Edit Demo",
            "description": "Image editing workflow.",
            "tags": ["image-edit"],
        },
    )

    result = tools.search("Edit this image to change the background.", limit=5)

    assert result["count"] >= 1
    assert result["workflows"][0]["id"] == "flux_2_klein_4B"


def test_workflow_tools_file_operations(tmp_path: Path) -> None:
    """Workflow tools should support create/write/edit/delete in workflow root."""
    store = WorkflowStore(tmp_path)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(store, FakeComfyClient(), policy, extractor=FakeExtractor())

    created = tools.folder_create("custom/new-workflow")
    assert created["created"] is True
    assert (tmp_path / "custom" / "new-workflow").exists()

    written = tools.file_write(
        "custom/new-workflow/workflow.json",
        {"1": {"class_type": "KSampler", "inputs": {"seed": 1}}},
    )
    assert written["written"] is True
    workflow_path = tmp_path / "custom" / "new-workflow" / "workflow.json"
    assert workflow_path.exists()

    edited = tools.file_edit("custom/new-workflow/workflow.json", {"meta": {"name": "Demo"}})
    assert edited["edited"] is True
    payload = json.loads(workflow_path.read_text(encoding="utf-8"))
    assert payload["meta"]["name"] == "Demo"

    read = tools.file_read("custom/new-workflow/workflow.json")
    assert read["path"] == str(workflow_path)
    assert read["encoding"] == "utf-8"
    assert read["content"]
    assert json.loads(read["content"])["meta"]["name"] == "Demo"

    copied = tools.file_copy(
        "custom/new-workflow/workflow.json",
        "custom/new-workflow/workflow-copy.json",
    )
    assert copied["copied"] is True
    copied_path = tmp_path / "custom" / "new-workflow" / "workflow-copy.json"
    assert copied_path.exists()
    copied_payload = json.loads(copied_path.read_text(encoding="utf-8"))
    assert copied_payload["meta"]["name"] == "Demo"

    deleted = tools.file_delete("custom/new-workflow/workflow.json")
    assert deleted["deleted"] is True
    assert not workflow_path.exists()


def test_workflow_tools_file_operations_reject_escape(tmp_path: Path) -> None:
    """Workflow file operations should reject paths escaping the workflow root."""
    store = WorkflowStore(tmp_path)
    policy = Policy(api_token=None, readonly_mode=False, max_workflow_bytes=10_000)
    tools = WorkflowTools(store, FakeComfyClient(), policy, extractor=FakeExtractor())

    with pytest.raises(ValueError, match="escapes workflows root"):
        tools.file_write("../outside.json", {"x": 1})
    with pytest.raises(ValueError, match="escapes workflows root"):
        tools.file_read("../outside.json")
    with pytest.raises(ValueError, match="escapes workflows root"):
        tools.file_copy("../outside.json", "copy.json")
