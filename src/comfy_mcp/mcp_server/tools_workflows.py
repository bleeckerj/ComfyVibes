"""Workflow MCP tool handlers."""

from __future__ import annotations

import json
import asyncio
import time
from typing import Any, Dict, Optional

from pathlib import Path

from comfy_mcp.comfy_client.client import ComfyClient
from comfy_mcp.extraction.adapter import WorkflowExtractor
from comfy_mcp.mcp_server.policy import Policy
from comfy_mcp.params.patch import patch_workflow
from comfy_mcp.params.schema import ParamSpec
from comfy_mcp.workflow_store.meta import build_meta
from comfy_mcp.workflow_store.store import WorkflowStore


class WorkflowTools:
    """Tool handlers for workflow library operations."""

    def __init__(
        self,
        store: WorkflowStore,
        client: ComfyClient,
        policy: Policy,
        extractor: Optional[WorkflowExtractor] = None,
    ) -> None:
        self._store = store
        self._client = client
        self._policy = policy
        self._extractor = extractor

    def extract_from_artifact(self, path: str, preserve_format: bool = False) -> Dict[str, Any]:
        """Extract workflow from an image or video artifact."""
        extractor = self._get_extractor()
        result = extractor.extract_from_path(path, preserve_format=preserve_format)
        return {
            "workflow": result.workflow,
            "workflow_format": result.workflow_format,
            "raw_metadata": result.raw_metadata,
        }

    def import_from_artifact(
        self,
        path: str,
        workflow_id: str,
        name: Optional[str] = None,
        tags: Optional[list[str]] = None,
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Extract workflow from artifact and save to the store."""
        self._policy.enforce_mutation(token)
        extractor = self._get_extractor()
        result = extractor.extract_from_path(path, preserve_format=False)
        meta = build_meta(
            workflow_id=workflow_id,
            name=name or workflow_id,
            description="Imported from artifact",
            tags=tags or [],
            workflow_json=result.workflow,
        )
        payload_bytes = len(json.dumps(result.workflow).encode("utf-8"))
        self._policy.enforce_payload_size(payload_bytes)
        self._store.save_workflow(workflow_id, result.workflow, meta=meta.to_dict())
        return {"id": workflow_id}

    def _get_extractor(self) -> WorkflowExtractor:
        if self._extractor is None:
            self._extractor = WorkflowExtractor()
        return self._extractor

    def list(self) -> Dict[str, Any]:
        """List workflows in the store."""
        entries = self._store.list_entries()
        return {
            "workflows": [
                {
                    "id": entry.workflow_id,
                    "has_meta": entry.has_meta,
                    "has_params": entry.has_params,
                }
                for entry in entries
            ]
        }

    def get(self, workflow_id: str) -> Dict[str, Any]:
        """Return workflow JSON and metadata."""
        workflow = self._store.read_workflow(workflow_id)
        return {
            "workflow": workflow,
            "meta": self._store.read_meta(workflow_id),
            "params": self._store.read_params(workflow_id),
        }

    def params_get(self, workflow_id: str) -> Dict[str, Any]:
        """Return params.json for a workflow id."""
        params = self._store.read_params(workflow_id)
        return {"params": params}

    def save(
        self,
        workflow_id: str,
        workflow_json: Dict[str, Any],
        meta: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Save a workflow entry to the store."""
        self._policy.enforce_mutation(token)
        payload_bytes = len(json.dumps(workflow_json).encode("utf-8"))
        self._policy.enforce_payload_size(payload_bytes)
        self._store.save_workflow(workflow_id, workflow_json, meta=meta, params=params)
        return {"id": workflow_id}

    def delete(self, workflow_id: str, token: Optional[str] = None) -> Dict[str, Any]:
        """Delete a workflow entry from the store."""
        self._policy.enforce_mutation(token)
        self._store.delete_workflow(workflow_id)
        return {"deleted": True}

    async def run(
        self,
        workflow_id: str,
        overrides: Dict[str, Any],
        client_id: Optional[str] = None,
        token: Optional[str] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Run a workflow with parameter overrides via ComfyUI."""
        self._policy.enforce_mutation(token)
        workflow = self._store.read_workflow(workflow_id)
        params = self._store.read_params(workflow_id)
        if not params:
            raise ValueError("Params schema missing for workflow")
        spec = ParamSpec.model_validate(params)
        patched = patch_workflow(workflow, spec, overrides, force=force)
        payload_bytes = len(json.dumps(patched).encode("utf-8"))
        self._policy.enforce_payload_size(payload_bytes)
        result = await self._client.queue_prompt(patched, client_id=client_id)
        return result

    async def wait(
        self,
        prompt_id: str,
        timeout_s: float = 60.0,
        poll_ms: int = 500,
    ) -> Dict[str, Any]:
        """Wait for a prompt to appear in history.

        Returns status and history data if found.
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            history = await self._client.get_history(prompt_id=prompt_id)
            if history:
                return {"status": "complete", "history": history}
            await asyncio.sleep(poll_ms / 1000.0)
        return {"status": "timeout", "history": {}}

    async def run_aspect_ratio_adjustment(
        self,
        image_path: str,
        aspect_ratio: str,
        workflow_id: str = "aspect_ratio_adjustment",
        positive_prompt: Optional[str] = None,
        negative_prompt: Optional[str] = None,
        client_id: Optional[str] = None,
        token: Optional[str] = None,
        upload_subfolder: Optional[str] = None,
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Run the aspect ratio adjustment workflow with an uploaded image."""
        self._policy.enforce_mutation(token)

        workflow = self._store.read_workflow(workflow_id)
        if not workflow:
            if workflow_id != "aspect_comfyui_01077":
                workflow = self._store.read_workflow("aspect_comfyui_01077")
                workflow_id = "aspect_comfyui_01077"
            if not workflow:
                raise ValueError("Workflow not found: aspect_ratio_adjustment or aspect_comfyui_01077")

        image_file = Path(image_path)
        if not image_file.exists():
            raise ValueError(f"Image file not found: {image_path}")

        upload_result = await self._client.upload_image(
            str(image_file),
            image_type="input",
            subfolder=upload_subfolder,
            overwrite=overwrite,
        )
        uploaded_name = upload_result.get("name")
        uploaded_subfolder = upload_result.get("subfolder", "")
        if not uploaded_name:
            raise ValueError("Image upload did not return a filename")

        object_info = await self._client.get_object_info()
        flux_node = object_info.get("FluxResolutionNode") or {}
        flux_inputs = flux_node.get("input", {})
        aspect_entry = flux_inputs.get("required", {}).get("aspect_ratio") or flux_inputs.get("optional", {}).get("aspect_ratio")
        allowed_aspects = []
        if isinstance(aspect_entry, list) and aspect_entry:
            allowed_aspects = aspect_entry[0]
        if allowed_aspects and aspect_ratio not in allowed_aspects:
            raise ValueError(
                f"aspect_ratio '{aspect_ratio}' not in allowed list: {allowed_aspects}"
            )

        patched = json.loads(json.dumps(workflow))

        def _slug_aspect(value: str) -> str:
            return (
                value.replace(" (", "_")
                .replace(")", "")
                .replace(":", "x")
                .replace(" ", "_")
                .lower()
            )

        if "109" in patched and isinstance(patched["109"], dict):
            patched["109"].setdefault("inputs", {})["image"] = uploaded_name
            if uploaded_subfolder:
                patched["109"]["inputs"]["subfolder"] = uploaded_subfolder

        if "79" in patched and isinstance(patched["79"], dict):
            inputs = patched["79"].setdefault("inputs", {})
            base_prefix = inputs.get("filename_prefix", "ComfyUI")
            inputs["filename_prefix"] = f"{base_prefix}_{_slug_aspect(aspect_ratio)}"

        if "115" in patched and isinstance(patched["115"], dict):
            inputs = patched["115"].setdefault("inputs", {})
            inputs["aspect_ratio"] = aspect_ratio
            inputs["custom_ratio"] = False
            ratio_value = aspect_ratio.split(" ")[0]
            inputs["custom_aspect_ratio"] = ratio_value

        if positive_prompt is not None and "113" in patched:
            patched["113"].setdefault("inputs", {})["prompt"] = positive_prompt
        if negative_prompt is not None and "114" in patched:
            patched["114"].setdefault("inputs", {})["prompt"] = negative_prompt

        result = await self._client.queue_prompt(patched, client_id=client_id)
        return {
            "workflow_id": workflow_id,
            "prompt_id": result.get("prompt_id"),
            "upload": upload_result,
            "aspect_ratio": aspect_ratio,
        }
