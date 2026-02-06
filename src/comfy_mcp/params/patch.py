"""Deterministic patching of workflows using ParamSpec."""

from __future__ import annotations

import copy
from typing import Any, Dict

from comfy_mcp.params.errors import ParamPatchError
from comfy_mcp.params.schema import ParamSpec
from comfy_mcp.params.validate import validate_overrides
from comfy_mcp.workflow_store.hashing import sha256_json


def patch_workflow(
    workflow: Dict[str, Any],
    spec: ParamSpec,
    overrides: Dict[str, Any],
    force: bool = False,
) -> Dict[str, Any]:
    """Patch a workflow dict with validated overrides."""
    current_hash = sha256_json(workflow)
    if current_hash != spec.workflow_hash and not force:
        raise ParamPatchError("Workflow hash mismatch; set force=True to override")

    normalized, param_map = validate_overrides(spec, overrides)
    patched = copy.deepcopy(workflow)

    for name, value in normalized.items():
        target = param_map[name].target
        if target.mode != "direct":
            raise ParamPatchError("Only direct targets are supported in MVP")
        if not target.node_id or not target.input:
            raise ParamPatchError(f"Param {name} target must include node_id and input")

        node = patched.get(str(target.node_id))
        if not isinstance(node, dict):
            raise ParamPatchError(f"Node {target.node_id} not found in workflow")
        inputs = node.setdefault("inputs", {})
        if not isinstance(inputs, dict):
            raise ParamPatchError(f"Node {target.node_id} inputs must be a dict")

        if target.path:
            _set_nested(inputs, target.input, target.path, value)
        else:
            inputs[target.input] = value

    return patched


def _set_nested(inputs: Dict[str, Any], key: str, path: list[str], value: Any) -> None:
    if key not in inputs or not isinstance(inputs[key], dict):
        inputs[key] = {}

    cursor = inputs[key]
    for segment in path[:-1]:
        if segment not in cursor or not isinstance(cursor[segment], dict):
            cursor[segment] = {}
        cursor = cursor[segment]

    cursor[path[-1]] = value
