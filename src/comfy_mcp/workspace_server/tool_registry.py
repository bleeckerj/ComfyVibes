"""Canonical tool registry for the workspace HTTP server."""

from __future__ import annotations

from typing import Any

from comfy_mcp.mcp_server.server import assert_tool_contracts
from comfy_mcp.workspace_server.fs_tools import WorkspaceFsTools


def _tool_def(
    name: str,
    description: str,
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required or [],
        },
    }


def build_workspace_tool_registry() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tools = WorkspaceFsTools()

    tool_defs: list[dict[str, Any]] = [
        _tool_def(
            "workspace_roots",
            "List allowlisted filesystem roots for workspace tools.",
            properties={},
        ),
        _tool_def(
            "workspace_path_stat",
            "Stat a path within allowed roots.",
            properties={"path": {"type": "string"}},
            required=["path"],
        ),
        _tool_def(
            "workspace_folder_list",
            "List directory entries within allowed roots.",
            properties={
                "path": {"type": "string"},
                "recursive": {"type": "boolean"},
                "limit": {"type": "integer"},
            },
            required=["path"],
        ),
        _tool_def(
            "workspace_file_read",
            "Read a text file (UTF-8) within allowed roots.",
            properties={"path": {"type": "string"}, "max_bytes": {"type": "integer"}},
            required=["path"],
        ),
        _tool_def(
            "workspace_file_write",
            "Write a text file (UTF-8) within allowed roots.",
            properties={
                "path": {"type": "string"},
                "content": {"type": "string"},
                "mode": {"type": "string", "enum": ["overwrite", "append", "create"]},
                "dry_run": {"type": "boolean"},
            },
            required=["path", "content"],
        ),
        _tool_def(
            "workspace_file_copy",
            "Copy a file within allowed roots.",
            properties={
                "src": {"type": "string"},
                "dst": {"type": "string"},
                "overwrite": {"type": "boolean"},
                "dry_run": {"type": "boolean"},
            },
            required=["src", "dst"],
        ),
        _tool_def(
            "workspace_file_move",
            "Move a file within allowed roots.",
            properties={
                "src": {"type": "string"},
                "dst": {"type": "string"},
                "overwrite": {"type": "boolean"},
                "dry_run": {"type": "boolean"},
            },
            required=["src", "dst"],
        ),
        _tool_def(
            "workspace_file_delete",
            "Delete a file within allowed roots.",
            properties={"path": {"type": "string"}, "dry_run": {"type": "boolean"}},
            required=["path"],
        ),
    ]

    handlers = {
        "workspace_roots": tools.workspace_roots,
        "workspace_path_stat": tools.workspace_path_stat,
        "workspace_folder_list": tools.workspace_folder_list,
        "workspace_file_read": tools.workspace_file_read,
        "workspace_file_write": tools.workspace_file_write,
        "workspace_file_copy": tools.workspace_file_copy,
        "workspace_file_move": tools.workspace_file_move,
        "workspace_file_delete": tools.workspace_file_delete,
    }

    assert_tool_contracts(tool_defs, handlers)
    return tool_defs, handlers
