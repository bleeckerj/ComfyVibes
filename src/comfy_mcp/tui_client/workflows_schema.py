"""Schema repair helpers for workflow-oriented tools."""

from __future__ import annotations

from typing import Any, Dict


def ensure_workflows_run_schema(schema: Any) -> Dict[str, Any]:
    """Return a workflows_run-compatible JSON schema.

    Some upstream MCP proxies can expose stale/incomplete schemas for
    workflows_run (for example omitting `overrides` or marking it too strict).
    This repair keeps the tool-call contract stable in the client.
    """
    repaired: Dict[str, Any] = dict(schema) if isinstance(schema, dict) else {}
    repaired["type"] = "object"

    properties = repaired.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    properties = dict(properties)

    workflow_id_schema = properties.get("workflow_id")
    if not isinstance(workflow_id_schema, dict):
        properties["workflow_id"] = {"type": "string"}

    overrides_schema = properties.get("overrides")
    if not isinstance(overrides_schema, dict):
        overrides_schema = {}
    overrides_schema = dict(overrides_schema)
    overrides_schema["type"] = "object"
    # workflows_run overrides are dynamic param names inferred from params.json.
    overrides_schema["additionalProperties"] = True
    properties["overrides"] = overrides_schema

    repaired["properties"] = properties

    required = repaired.get("required")
    required_list = [item for item in required if isinstance(item, str)] if isinstance(required, list) else []
    for key in ("workflow_id", "overrides"):
        if key not in required_list:
            required_list.append(key)
    repaired["required"] = required_list
    return repaired
