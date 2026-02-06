"""Workflow normalization helpers."""

from __future__ import annotations

from typing import Any, Dict


def detect_workflow_format(workflow: Dict[str, Any]) -> str:
    """Detect whether workflow is UI or API format."""
    if not isinstance(workflow, dict):
        return "unknown"

    if "nodes" in workflow and isinstance(workflow["nodes"], list):
        return "ui"

    if all(
        isinstance(value, dict) and "class_type" in value
        for value in workflow.values()
        if isinstance(value, dict)
    ):
        return "api"

    return "unknown"


def ui_to_api_format(ui_workflow: Dict[str, Any]) -> Dict[str, Any]:
    """Convert ComfyUI UI workflow format to API format (minimal mapping)."""
    if not isinstance(ui_workflow, dict):
        return ui_workflow

    if "nodes" not in ui_workflow:
        return ui_workflow

    api_workflow: Dict[str, Any] = {}
    nodes = ui_workflow.get("nodes", [])

    for node in nodes:
        node_id = str(node.get("id"))
        node_type = node.get("type", "")

        api_node = {
            "inputs": {},
            "class_type": node_type,
        }

        if "widgets_values" in node and node["widgets_values"]:
            api_node["inputs"]["values"] = node["widgets_values"]

        api_workflow[node_id] = api_node

    return api_workflow
