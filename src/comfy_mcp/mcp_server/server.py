"""MCP server wiring (requires MCP Python SDK at runtime)."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict

from comfy_mcp.comfy_client.client import ComfyClient
from comfy_mcp.config.models import AppConfig
from comfy_mcp.mcp_server.policy import Policy
from comfy_mcp.mcp_server.tools_comfy import ComfyTools
from comfy_mcp.mcp_server.tools_workflows import WorkflowTools
from comfy_mcp.workflow_store.store import WorkflowStore


def build_tools(config: AppConfig) -> tuple[ComfyTools, WorkflowTools]:
    """Build tool handlers for MCP server wiring."""
    client = ComfyClient(str(config.comfy_base_url))
    primary_root = config.resolved_workflow_root()
    extra_roots: list[Path] = []

    if config.include_extra_workflow_roots:
        # Optional additional roots:
        # - repo-local ./workflows (when running from source)
        # - user default ~/.comfy-mcp/workflows
        local_root = (Path.cwd() / "workflows").expanduser().resolve()
        home_root = Path("~/.comfy-mcp/workflows").expanduser().resolve()
        for candidate in (local_root, home_root):
            if candidate == primary_root:
                continue
            if candidate.exists() and candidate.is_dir():
                extra_roots.append(candidate)

    store = WorkflowStore(primary_root, extra_roots=extra_roots)
    policy = Policy(
        api_token=config.api_token,
        readonly_mode=config.readonly_mode,
        max_workflow_bytes=config.max_workflow_bytes,
    )
    return ComfyTools(client), WorkflowTools(
        store,
        client,
        policy,
        comfy_output_dir=config.comfy_output_dir,
    )


def _tool_schema(properties: Dict[str, Any] | None = None, required: list[str] | None = None):
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


def _tool_to_payload(tool: Any) -> Dict[str, Any]:
    if isinstance(tool, dict):
        return {
            "name": tool.get("name"),
            "description": tool.get("description"),
            "inputSchema": tool.get("inputSchema"),
        }
    if hasattr(tool, "model_dump"):
        return tool.model_dump()
    if hasattr(tool, "dict"):
        return tool.dict()
    return {
        "name": getattr(tool, "name", None),
        "description": getattr(tool, "description", None),
        "inputSchema": getattr(tool, "inputSchema", None),
    }


def _list_tool_payloads(
    tool_defs: list[Any],
    name: str | None = None,
    prefix: str | None = None,
    limit: int | None = None,
    include_schema: bool = True,
) -> Dict[str, Any]:
    tools = [_tool_to_payload(tool) for tool in tool_defs]
    if name:
        tools = [tool for tool in tools if tool.get("name") == name]
    if prefix:
        tools = [tool for tool in tools if str(tool.get("name") or "").startswith(prefix)]
    if limit is not None:
        tools = tools[: max(0, int(limit))]
    if not include_schema:
        tools = [
            {
                "name": tool.get("name"),
                "description": tool.get("description"),
            }
            for tool in tools
        ]
    return {"count": len(tools), "tools": tools}


def build_tool_registry(config: AppConfig) -> tuple[list[Any], Dict[str, Callable[..., Any] | Callable[..., Awaitable[Any]]]]:
    """Return tool definitions and handlers for both MCP and HTTP servers."""
    try:
        from mcp import types
    except ImportError as exc:
        raise RuntimeError("MCP SDK not installed. Add it to dependencies.") from exc

    comfy_tools, workflow_tools = build_tools(config)

    tool_defs = [
        types.Tool(
            name="comfy_nodes_list",
            description="Return ComfyUI node catalog.",
            inputSchema=_tool_schema(),
        ),
        types.Tool(
            name="comfy_queue_get",
            description="Return ComfyUI queue state.",
            inputSchema=_tool_schema(),
        ),
        types.Tool(
            name="comfy_history_get",
            description="Return ComfyUI history data.",
            inputSchema=_tool_schema(
                {"prompt_id": {"type": "string"}},
                [],
            ),
        ),
        types.Tool(
            name="comfy_models_list",
            description="Return available model folders.",
            inputSchema=_tool_schema(),
        ),
        types.Tool(
            name="comfy_models_get",
            description="Return available model files for a folder.",
            inputSchema=_tool_schema({"folder": {"type": "string"}}, ["folder"]),
        ),
        types.Tool(
            name="comfy_embeddings_list",
            description="Return available embedding names.",
            inputSchema=_tool_schema(),
        ),
        types.Tool(
            name="comfy_upload_image",
            description="Upload an image file into ComfyUI storage.",
            inputSchema=_tool_schema(
                {
                    "file_path": {"type": "string"},
                    "image_type": {"type": "string"},
                    "subfolder": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
                ["file_path"],
            ),
        ),
        types.Tool(
            name="comfy_download_image",
            description="Download an image from ComfyUI's output/input storage and save it locally. Use this after a workflow completes to retrieve the generated image. Returns the local file path.",
            inputSchema=_tool_schema(
                {
                    "filename": {"type": "string", "description": "The filename on the ComfyUI server (e.g. from output_images[].filename)."},
                    "image_type": {"type": "string", "description": "Storage type: 'output', 'input', or 'temp'. Default: 'output'."},
                    "subfolder": {"type": "string", "description": "Subfolder within the storage type, if any."},
                    "save_path": {"type": "string", "description": "Local directory or file path to save to. Default: /tmp/<filename>."},
                },
                ["filename"],
            ),
        ),
        types.Tool(
            name="list_tools",
            description="Return tool definitions for this MCP server. Supports optional filtering by exact name or prefix.",
            inputSchema=_tool_schema(
                {
                    "name": {"type": "string"},
                    "prefix": {"type": "string"},
                    "limit": {"type": "integer"},
                    "include_schema": {"type": "boolean"},
                },
                [],
            ),
        ),
        types.Tool(
            name="tool_schema_get",
            description="Return one tool definition and input schema by tool name.",
            inputSchema=_tool_schema(
                {"name": {"type": "string"}},
                ["name"],
            ),
        ),
        types.Tool(
            name="workflows_list",
            description="List workflows in the store, including source root and params count.",
            inputSchema=_tool_schema(),
        ),
        types.Tool(
            name="workflows_get",
            description="Return workflow JSON and metadata.",
            inputSchema=_tool_schema({"workflow_id": {"type": "string"}}, ["workflow_id"]),
        ),
        types.Tool(
            name="workflows_search",
            description="Search workflows by id, name, description, or tags.",
            inputSchema=_tool_schema(
                {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                ["query"],
            ),
        ),
        types.Tool(
            name="workflows_capabilities_list",
            description="List workflow capability cards (name, tags, required params, and optional heuristics).",
            inputSchema=_tool_schema(
                {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "include_params": {"type": "boolean"},
                },
                [],
            ),
        ),
        types.Tool(
            name="workflows_capabilities_get",
            description="Return one workflow capability card by workflow id.",
            inputSchema=_tool_schema(
                {
                    "workflow_id": {"type": "string"},
                    "include_params": {"type": "boolean"},
                },
                ["workflow_id"],
            ),
        ),
        types.Tool(
            name="workflows_params_get",
            description="Return params.json for a workflow id.",
            inputSchema=_tool_schema({"workflow_id": {"type": "string"}}, ["workflow_id"]),
        ),
        types.Tool(
            name="workflows_package",
            description="Regenerate params.json and metadata capability fields for one workflow.",
            inputSchema=_tool_schema(
                {
                    "workflow_id": {"type": "string"},
                    "hints": {"type": "object"},
                    "include_suggestions": {"type": "boolean"},
                    "token": {"type": "string"},
                },
                ["workflow_id"],
            ),
        ),
        types.Tool(
            name="workflows_package_many",
            description="Regenerate params.json and metadata capability fields for many workflows (or all when workflow_ids omitted).",
            inputSchema=_tool_schema(
                {
                    "workflow_ids": {"type": "array", "items": {"type": "string"}},
                    "hints_by_workflow": {"type": "object"},
                    "include_suggestions": {"type": "boolean"},
                    "token": {"type": "string"},
                },
                [],
            ),
        ),
        types.Tool(
            name="workflows_package_template_get",
            description="Return an editable metadata-hints template for one workflow.",
            inputSchema=_tool_schema(
                {
                    "workflow_id": {"type": "string"},
                },
                ["workflow_id"],
            ),
        ),
        types.Tool(
            name="workflows_folder_create",
            description="Create a folder under the primary workflows directory.",
            inputSchema=_tool_schema(
                {
                    "folder_path": {"type": "string"},
                    "token": {"type": "string"},
                },
                ["folder_path"],
            ),
        ),
        types.Tool(
            name="workflows_file_write",
            description="Write a JSON file under the primary workflows directory.",
            inputSchema=_tool_schema(
                {
                    "file_path": {"type": "string"},
                    "payload": {"type": "object"},
                    "overwrite": {"type": "boolean"},
                    "token": {"type": "string"},
                },
                ["file_path", "payload"],
            ),
        ),
        types.Tool(
            name="workflows_file_edit",
            description="Edit a JSON file under the primary workflows directory by merging top-level keys.",
            inputSchema=_tool_schema(
                {
                    "file_path": {"type": "string"},
                    "updates": {"type": "object"},
                    "token": {"type": "string"},
                },
                ["file_path", "updates"],
            ),
        ),
        types.Tool(
            name="workflows_file_delete",
            description="Delete a file under the primary workflows directory.",
            inputSchema=_tool_schema(
                {
                    "file_path": {"type": "string"},
                    "token": {"type": "string"},
                },
                ["file_path"],
            ),
        ),
        types.Tool(
            name="workflows_save",
            description="Save a workflow entry and always regenerate packaged meta.json + params.json.",
            inputSchema=_tool_schema(
                {
                    "workflow_id": {"type": "string"},
                    "workflow_json": {"type": "object"},
                    "meta": {"type": "object"},
                    "token": {"type": "string"},
                },
                ["workflow_id", "workflow_json"],
            ),
        ),
        types.Tool(
            name="workflows_delete",
            description="Delete a workflow entry from the store.",
            inputSchema=_tool_schema(
                {"workflow_id": {"type": "string"}, "token": {"type": "string"}},
                ["workflow_id"],
            ),
        ),
        types.Tool(
            name="workflows_run",
            description="Run a workflow with parameter overrides via ComfyUI.",
            inputSchema=_tool_schema(
                {
                    "workflow_id": {"type": "string"},
                    "overrides": {"type": "object"},
                    "client_id": {"type": "string"},
                    "token": {"type": "string"},
                    "force": {"type": "boolean"},
                    "wait_timeout_s": {"type": "number"},
                    "wait_poll_ms": {"type": "integer"},
                },
                ["workflow_id", "overrides"],
            ),
        ),
        types.Tool(
            name="workflows_wait",
            description="Wait for a prompt to appear in history.",
            inputSchema=_tool_schema(
                {
                    "prompt_id": {"type": "string"},
                    "timeout_s": {"type": "number"},
                    "poll_ms": {"type": "integer"},
                },
                ["prompt_id"],
            ),
        ),
        types.Tool(
            name="workflows_image_info",
            description="Inspect a local image file and return width/height/aspect-ratio details.",
            inputSchema=_tool_schema(
                {
                    "file_path": {"type": "string"},
                },
                ["file_path"],
            ),
        ),
        types.Tool(
            name="workflows_watch",
            description="Watch workflow execution status/progress until terminal state.",
            inputSchema=_tool_schema(
                {
                    "prompt_id": {"type": "string"},
                    "inactivity_timeout_s": {"type": "number"},
                    "max_wait_s": {"type": "number"},
                    "poll_ms": {"type": "integer"},
                    "include_history": {"type": "boolean"},
                },
                ["prompt_id"],
            ),
        ),
        types.Tool(
            name="workflows_status",
            description="Get current workflow queue status and optional progress snapshot.",
            inputSchema=_tool_schema(
                {
                    "prompt_id": {"type": "string"},
                    "include_progress": {"type": "boolean"},
                    "progress_timeout_s": {"type": "number"},
                },
                [],
            ),
        ),
        types.Tool(
            name="workflows_extract_from_artifact",
            description="Extract workflow from an image or video artifact.",
            inputSchema=_tool_schema(
                {"path": {"type": "string"}, "preserve_format": {"type": "boolean"}},
                ["path"],
            ),
        ),
        types.Tool(
            name="workflows_import_from_artifact",
            description="Extract workflow from artifact and save to the store.",
            inputSchema=_tool_schema(
                {
                    "path": {"type": "string"},
                    "workflow_id": {"type": "string"},
                    "name": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "token": {"type": "string"},
                },
                ["path", "workflow_id"],
            ),
        ),
        types.Tool(
            name="workflows_import_from_photarium",
            description="Extract workflow metadata from a Photarium image and save it as a reusable workflow entry.",
            inputSchema=_tool_schema(
                {
                    "image_id": {"type": "string"},
                    "workflow_id": {"type": "string"},
                    "photarium_mcp_url": {"type": "string"},
                    "name": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "hints": {"type": "object"},
                    "include_suggestions": {"type": "boolean"},
                    "prefer_prompt": {"type": "boolean"},
                    "include_raw_metadata": {"type": "boolean"},
                    "token": {"type": "string"},
                },
                ["image_id", "workflow_id"],
            ),
        ),
        types.Tool(
            name="workflows_run_aspect_ratio_adjustment",
            description="Upload an image, set aspect ratio, and run the aspect ratio adjustment workflow.",
            inputSchema=_tool_schema(
                {
                    "image_path": {"type": "string"},
                    "aspect_ratio": {"type": "string"},
                    "workflow_id": {"type": "string"},
                    "positive_prompt": {"type": "string"},
                    "negative_prompt": {"type": "string"},
                    "seed": {"type": "integer"},
                    "output_base_name": {"type": "string"},
                    "client_id": {"type": "string"},
                    "token": {"type": "string"},
                    "upload_subfolder": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                    "wait_timeout_s": {"type": "number"},
                    "wait_poll_ms": {"type": "integer"},
                },
                ["image_path", "aspect_ratio"],
            ),
        ),
    ]

    def _tool_schema_get(name: str) -> Dict[str, Any]:
        payload = _list_tool_payloads(tool_defs, name=name)
        if not payload["tools"]:
            return {"error": f"Unknown tool: {name}"}
        return {"tool": payload["tools"][0]}

    handlers: Dict[str, Callable[..., Any] | Callable[..., Awaitable[Any]]] = {
        "comfy_nodes_list": comfy_tools.nodes_list,
        "comfy_queue_get": comfy_tools.queue_get,
        "comfy_history_get": comfy_tools.history_get,
        "comfy_models_list": comfy_tools.models_list,
        "comfy_models_get": comfy_tools.models_get,
        "comfy_embeddings_list": comfy_tools.embeddings_list,
        "comfy_upload_image": comfy_tools.upload_image,
        "comfy_download_image": comfy_tools.download_image,
        "list_tools": lambda name=None, prefix=None, limit=None, include_schema=True: _list_tool_payloads(
            tool_defs,
            name=name,
            prefix=prefix,
            limit=limit,
            include_schema=include_schema,
        ),
        "tool_schema_get": _tool_schema_get,
        "workflows_list": workflow_tools.list,
        "workflows_get": workflow_tools.get,
        "workflows_search": workflow_tools.search,
        "workflows_capabilities_list": workflow_tools.capabilities_list,
        "workflows_capabilities_get": workflow_tools.capabilities_get,
        "workflows_params_get": workflow_tools.params_get,
        "workflows_package": workflow_tools.package,
        "workflows_package_many": workflow_tools.package_many,
        "workflows_package_template_get": workflow_tools.package_template_get,
        "workflows_folder_create": workflow_tools.folder_create,
        "workflows_file_write": workflow_tools.file_write,
        "workflows_file_edit": workflow_tools.file_edit,
        "workflows_file_delete": workflow_tools.file_delete,
        "workflows_save": workflow_tools.save,
        "workflows_delete": workflow_tools.delete,
        "workflows_run": workflow_tools.run,
        "workflows_wait": workflow_tools.wait,
        "workflows_image_info": workflow_tools.image_info,
        "workflows_watch": workflow_tools.watch,
        "workflows_status": workflow_tools.status,
        "workflows_extract_from_artifact": workflow_tools.extract_from_artifact,
        "workflows_import_from_artifact": workflow_tools.import_from_artifact,
        "workflows_import_from_photarium": workflow_tools.import_from_photarium,
        "workflows_run_aspect_ratio_adjustment": workflow_tools.run_aspect_ratio_adjustment,
    }

    return tool_defs, handlers


def create_server(config: AppConfig):
    """Create MCP server instance (requires MCP SDK installed)."""
    try:
        from mcp.server import Server
    except ImportError as exc:
        raise RuntimeError("MCP SDK not installed. Add it to dependencies.") from exc

    server = Server("comfy-mcp")
    tool_defs, handlers = build_tool_registry(config)

    @server.list_tools()
    async def _list_tools():
        return tool_defs

    @server.call_tool()
    async def _call_tool(name: str, arguments: Dict[str, Any] | None):
        handler = handlers.get(name)
        if handler is None:
            return {"error": f"Unknown tool: {name}"}
        kwargs = arguments or {}
        if inspect.iscoroutinefunction(handler):
            return await handler(**kwargs)
        return handler(**kwargs)

    return server
