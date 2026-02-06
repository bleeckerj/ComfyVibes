"""Workflow MCP tool handlers."""

from __future__ import annotations

import json
import asyncio
import time
from typing import Any, Dict, Optional

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
