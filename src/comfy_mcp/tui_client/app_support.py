"""Support helpers extracted from the TUI app module."""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
from typing import Any, Dict, List

from comfy_mcp.tui_client.config import ChatClientConfig
from comfy_mcp.tui_client.http_router import HTTPToolRouter
from comfy_mcp.tui_client.hybrid_router import HybridToolRouter
from comfy_mcp.tui_client.mcp_router import MCPToolRouter
from comfy_mcp.tui_client.transport_plan import build_transport_plan


def clipboard_copy(text: str) -> bool:
    system = platform.system()
    if system == "Darwin" and shutil.which("pbcopy"):
        cmd = ["pbcopy"]
    elif system == "Linux" and shutil.which("xclip"):
        cmd = ["xclip", "-selection", "clipboard"]
    elif system == "Linux" and shutil.which("xsel"):
        cmd = ["xsel", "--clipboard", "--input"]
    elif system == "Linux" and shutil.which("wl-copy"):
        cmd = ["wl-copy"]
    elif shutil.which("clip.exe"):
        cmd = ["clip.exe"]
    else:
        return False
    try:
        subprocess.run(cmd, input=text.encode("utf-8"), check=True, timeout=5)
        return True
    except Exception:
        return False


def strip_border_glyphs(text: str) -> str:
    cleaned_lines: List[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"^\s*[│┃║]\s?", "", raw_line.rstrip())
        line = re.sub(r"\s*[▁▂▃▄▅▆▇█]?\s*[│┃║]\s*[▁▂▃▄▅▆▇█]?$", "", line)
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def normalize_openai_tool_schema(schema: Any) -> Dict[str, Any]:
    def _includes_type(node_type: Any, expected: str) -> bool:
        if isinstance(node_type, str):
            return node_type == expected
        if isinstance(node_type, list):
            return expected in node_type
        return False

    def _normalize_node(node: Any) -> Any:
        if not isinstance(node, dict):
            return node
        normalized: Dict[str, Any] = {}
        for key, value in node.items():
            if key in {"properties", "$defs", "definitions", "patternProperties", "dependentSchemas"}:
                normalized[key] = {str(child_key): _normalize_node(child_value) for child_key, child_value in value.items()} if isinstance(value, dict) else {}
                continue
            if key in {"allOf", "anyOf", "oneOf", "prefixItems"}:
                if isinstance(value, list):
                    normalized[key] = [_normalize_node(entry) if isinstance(entry, dict) else {} for entry in value]
                continue
            if key in {"items", "contains", "if", "then", "else", "not", "propertyNames"}:
                if isinstance(value, dict):
                    normalized[key] = _normalize_node(value)
                elif key == "items" and isinstance(value, list):
                    normalized[key] = [_normalize_node(entry) if isinstance(entry, dict) else {} for entry in value]
                continue
            if key in {"additionalProperties", "unevaluatedProperties"}:
                if isinstance(value, bool):
                    normalized[key] = value
                elif isinstance(value, dict):
                    normalized[key] = _normalize_node(value)
                continue
            if key == "unevaluatedItems":
                if isinstance(value, bool):
                    normalized[key] = value
                elif isinstance(value, dict):
                    normalized[key] = _normalize_node(value)
                continue
            if key == "required":
                if isinstance(value, list):
                    normalized[key] = [item for item in value if isinstance(item, str)]
                continue
            normalized[key] = value
        node_type = normalized.get("type")
        is_object = _includes_type(node_type, "object")
        is_array = _includes_type(node_type, "array")
        if is_object and "properties" in normalized and not isinstance(normalized.get("properties"), dict):
            normalized["properties"] = {}
        if is_array and "items" not in normalized:
            normalized["items"] = {}
        return normalized

    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    normalized_schema = _normalize_node(schema)
    if not isinstance(normalized_schema, dict):
        return {"type": "object", "properties": {}}
    return normalized_schema


def build_router(config: ChatClientConfig):
    plan = build_transport_plan(config.servers)
    if plan.http_servers and plan.stdio_servers:
        return HybridToolRouter(http_servers=plan.http_servers, stdio_servers=plan.stdio_servers)
    if plan.stdio_servers:
        return MCPToolRouter(plan.stdio_servers)
    return HTTPToolRouter(plan.http_servers)
