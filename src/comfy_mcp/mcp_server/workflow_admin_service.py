"""Workflow mutation and filesystem admin operations."""

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from comfy_mcp.mcp_server.policy import Policy
from comfy_mcp.params.infer import sync_meta_requires
from comfy_mcp.workflow_store.errors import WorkflowNotFoundError
from comfy_mcp.workflow_store.hashing import sha256_json
from comfy_mcp.workflow_store.packaging import package_workflow
from comfy_mcp.workflow_store.store import WorkflowStore


class WorkflowAdminService:
    def __init__(self, store: WorkflowStore, policy: Policy) -> None:
        self._store = store
        self._policy = policy

    def _resolve_under_root(self, relative_path: str) -> Path:
        root = self._store.root.resolve()
        target = (root / relative_path).resolve()
        if root not in target.parents and target != root:
            raise ValueError("Path escapes workflows root")
        return target

    def package(self, workflow_id: str, hints: dict[str, Any] | None = None, include_suggestions: bool = True, token: str | None = None) -> dict[str, Any]:
        self._policy.enforce_mutation(token)
        workflow = self._store.read_workflow(workflow_id)
        existing_meta = self._store.read_meta(workflow_id)
        packaged = package_workflow(workflow_id, workflow, existing_meta=existing_meta, hints=hints or {}, include_suggestions=include_suggestions)
        self._store.save_workflow(workflow_id, workflow, meta=packaged["meta"], params=packaged["params"])
        return {"workflow_id": workflow_id, "params_count": len(packaged["params"].get("params", [])), "packaged": True}

    def package_many(self, workflow_ids: list[str] | None = None, hints_by_workflow: dict[str, dict[str, Any]] | None = None, include_suggestions: bool = True, token: str | None = None) -> dict[str, Any]:
        self._policy.enforce_mutation(token)
        target_ids = workflow_ids or [entry.workflow_id for entry in self._store.list_entries()]
        hints_map = hints_by_workflow or {}
        packaged: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for workflow_id in target_ids:
            try:
                workflow = self._store.read_workflow(workflow_id)
                existing_meta = self._store.read_meta(workflow_id)
                item = package_workflow(workflow_id, workflow, existing_meta=existing_meta, hints=hints_map.get(workflow_id, {}), include_suggestions=include_suggestions)
                self._store.save_workflow(workflow_id, workflow, meta=item["meta"], params=item["params"])
                packaged.append({"workflow_id": workflow_id, "params_count": len(item["params"].get("params", []))})
            except Exception as exc:
                errors.append({"workflow_id": workflow_id, "error": str(exc)})
        return {"requested": len(target_ids), "packaged_count": len(packaged), "packaged": packaged, "errors": errors}

    def recompile(self, workflow_id: str, workflow_input_updates: list[dict[str, Any]] | None = None, param_overrides: list[dict[str, Any]] | None = None, hints: dict[str, Any] | None = None, include_suggestions: bool = True, token: str | None = None) -> dict[str, Any]:
        self._policy.enforce_mutation(token)
        workflow = deepcopy(self._store.read_workflow(workflow_id))
        workflow_updates_applied = self._apply_workflow_input_updates(workflow, workflow_input_updates or [])
        existing_meta = self._store.read_meta(workflow_id)
        packaged = package_workflow(workflow_id, workflow, existing_meta=existing_meta, hints=hints or {}, include_suggestions=include_suggestions)
        params = packaged["params"]
        overridden_params = self._apply_param_overrides(params, param_overrides or [])
        params["workflow_hash"] = sha256_json(workflow)
        packaged["meta"] = sync_meta_requires(packaged["meta"], params)
        self._store.save_workflow(workflow_id, workflow, meta=packaged["meta"], params=params)
        return {
            "workflow_id": workflow_id,
            "packaged": True,
            "params_count": len(params.get("params", [])),
            "workflow_updates_applied": workflow_updates_applied,
            "params_overridden": overridden_params,
            "workflow_hash": params.get("workflow_hash"),
        }

    @staticmethod
    def _apply_workflow_input_updates(workflow: dict[str, Any], updates: list[dict[str, Any]]) -> int:
        changed = 0
        for index, update in enumerate(updates):
            if not isinstance(update, dict):
                raise ValueError(f"workflow_input_updates[{index}] must be an object")
            node_id = str(update.get("node_id") or "").strip()
            input_name = str(update.get("input") or "").strip()
            if not node_id or not input_name:
                raise ValueError(f"workflow_input_updates[{index}] requires non-empty 'node_id' and 'input'")
            node = workflow.get(node_id)
            if not isinstance(node, dict):
                raise ValueError(f"workflow_input_updates[{index}] unknown node_id '{node_id}'")
            inputs = node.setdefault("inputs", {})
            if not isinstance(inputs, dict):
                raise ValueError(f"workflow_input_updates[{index}] node '{node_id}' has non-object inputs")
            path = update.get("path")
            value = update.get("value")
            if not path:
                inputs[input_name] = value
                changed += 1
                continue
            if not isinstance(path, list):
                raise ValueError(f"workflow_input_updates[{index}].path must be an array when provided")
            branch = inputs.get(input_name)
            if not isinstance(branch, dict):
                branch = {}
                inputs[input_name] = branch
            cursor = branch
            for part in path[:-1]:
                key = str(part)
                child = cursor.get(key)
                if not isinstance(child, dict):
                    child = {}
                    cursor[key] = child
                cursor = child
            cursor[str(path[-1])] = value
            changed += 1
        return changed

    @staticmethod
    def _apply_param_overrides(params_spec: dict[str, Any], overrides: list[dict[str, Any]]) -> list[str]:
        if not overrides:
            return []
        params = params_spec.get("params")
        if not isinstance(params, list):
            raise ValueError("params payload missing 'params' list")
        allowed_types = {"int", "float", "string", "bool"}
        mutable_fields = {"type", "default", "required", "description", "target"}
        changed_names: list[str] = []
        for index, update in enumerate(overrides):
            if not isinstance(update, dict):
                raise ValueError(f"param_overrides[{index}] must be an object")
            name = str(update.get("name") or "").strip()
            if not name:
                raise ValueError(f"param_overrides[{index}] requires non-empty 'name'")
            target_item = None
            for item in params:
                if isinstance(item, dict) and str(item.get("name") or "") == name:
                    target_item = item
                    break
            if target_item is None:
                raise ValueError(f"param_overrides[{index}] unknown param '{name}'")
            if "type" in update:
                param_type = str(update.get("type") or "").strip()
                if param_type not in allowed_types:
                    raise ValueError(f"param_overrides[{index}] invalid type '{param_type}' (expected one of {sorted(allowed_types)})")
            mutated = False
            for field in mutable_fields:
                if field not in update:
                    continue
                value = update[field]
                if field == "required":
                    value = bool(value)
                if field == "target" and not isinstance(value, dict):
                    raise ValueError(f"param_overrides[{index}].target must be an object")
                target_item[field] = value
                mutated = True
            if mutated:
                changed_names.append(name)
        return changed_names

    def folder_create(self, folder_path: str, token: str | None = None) -> dict[str, Any]:
        self._policy.enforce_mutation(token)
        target = self._resolve_under_root(folder_path)
        target.mkdir(parents=True, exist_ok=True)
        return {"created": True, "path": str(target)}

    def file_write(self, file_path: str, payload: dict[str, Any], overwrite: bool = False, token: str | None = None) -> dict[str, Any]:
        self._policy.enforce_mutation(token)
        target = self._resolve_under_root(file_path)
        if target.exists() and not overwrite:
            raise ValueError(f"File already exists: {file_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        payload_bytes = len(json.dumps(payload).encode("utf-8"))
        self._policy.enforce_payload_size(payload_bytes)
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return {"written": True, "path": str(target)}

    def file_edit(self, file_path: str, updates: dict[str, Any], token: str | None = None) -> dict[str, Any]:
        self._policy.enforce_mutation(token)
        target = self._resolve_under_root(file_path)
        if not target.exists() or not target.is_file():
            raise ValueError(f"File not found: {file_path}")
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Existing JSON payload must be an object")
        payload.update(updates)
        payload_bytes = len(json.dumps(payload).encode("utf-8"))
        self._policy.enforce_payload_size(payload_bytes)
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return {"edited": True, "path": str(target)}

    def file_delete(self, file_path: str, token: str | None = None) -> dict[str, Any]:
        self._policy.enforce_mutation(token)
        target = self._resolve_under_root(file_path)
        if not target.exists() or not target.is_file():
            raise ValueError(f"File not found: {file_path}")
        target.unlink()
        return {"deleted": True, "path": str(target)}

    def file_read(self, file_path: str, encoding: str = "utf-8") -> dict[str, Any]:
        target = self._resolve_under_root(file_path)
        if not target.exists() or not target.is_file():
            raise ValueError(f"File not found: {file_path}")
        content = target.read_text(encoding=encoding)
        return {"path": str(target), "encoding": encoding, "size_bytes": target.stat().st_size, "content": content}

    def file_copy(self, source_path: str, destination_path: str, overwrite: bool = False, token: str | None = None) -> dict[str, Any]:
        self._policy.enforce_mutation(token)
        source = self._resolve_under_root(source_path)
        destination = self._resolve_under_root(destination_path)
        if not source.exists() or not source.is_file():
            raise ValueError(f"Source file not found: {source_path}")
        if destination.exists() and not overwrite:
            raise ValueError(f"Destination file already exists: {destination_path}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return {"copied": True, "source_path": str(source), "destination_path": str(destination)}

    def save(self, workflow_id: str, workflow_json: dict[str, Any], meta: dict[str, Any] | None = None, params: dict[str, Any] | None = None, token: str | None = None) -> dict[str, Any]:
        self._policy.enforce_mutation(token)
        payload_bytes = len(json.dumps(workflow_json).encode("utf-8"))
        self._policy.enforce_payload_size(payload_bytes)
        try:
            existing_meta = self._store.read_meta(workflow_id)
        except WorkflowNotFoundError:
            existing_meta = None
        packaged = package_workflow(workflow_id, workflow_json, existing_meta=existing_meta, hints=meta or {}, include_suggestions=True)
        self._store.save_workflow(workflow_id, workflow_json, meta=packaged["meta"], params=packaged["params"])
        return {"id": workflow_id, "packaged": True, "params_count": len(packaged["params"].get("params", [])), "ignored_explicit_params": params is not None}

    def delete(self, workflow_id: str, token: str | None = None) -> dict[str, Any]:
        self._policy.enforce_mutation(token)
        self._store.delete_workflow(workflow_id)
        return {"deleted": True}
