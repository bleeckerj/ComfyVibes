"""MCP server wiring (requires MCP Python SDK at runtime)."""

from __future__ import annotations

import asyncio
import inspect
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
    store = WorkflowStore(config.resolved_workflow_root())
    policy = Policy(
        api_token=config.api_token,
        readonly_mode=config.readonly_mode,
        max_workflow_bytes=config.max_workflow_bytes,
    )
    return ComfyTools(client), WorkflowTools(store, client, policy)


def _tool_schema(properties: Dict[str, Any] | None = None, required: list[str] | None = None):
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


def create_server(config: AppConfig):
    """Create MCP server instance (requires MCP SDK installed)."""
    try:
        from mcp.server import Server
        from mcp import types
    except ImportError as exc:
        raise RuntimeError("MCP SDK not installed. Add it to dependencies.") from exc

    server = Server("comfy-mcp")
    comfy_tools, workflow_tools = build_tools(config)

    def _tool_to_payload(tool: Any) -> Dict[str, Any]:
        if hasattr(tool, "model_dump"):
            return tool.model_dump()
        if hasattr(tool, "dict"):
            return tool.dict()
        return {
            "name": getattr(tool, "name", None),
            "description": getattr(tool, "description", None),
            "inputSchema": getattr(tool, "inputSchema", None),
        }

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
            name="list_tools",
            description="Return tool definitions for this MCP server.",
            inputSchema=_tool_schema(),
        ),
        types.Tool(
            name="workflows_list",
            description="List workflows in the store.",
            inputSchema=_tool_schema(),
        ),
        types.Tool(
            name="workflows_get",
            description="Return workflow JSON and metadata.",
            inputSchema=_tool_schema({"workflow_id": {"type": "string"}}, ["workflow_id"]),
        ),
        types.Tool(
            name="workflows_params_get",
            description="Return params.json for a workflow id.",
            inputSchema=_tool_schema({"workflow_id": {"type": "string"}}, ["workflow_id"]),
        ),
        types.Tool(
            name="workflows_save",
            description="Save a workflow entry to the store.",
            inputSchema=_tool_schema(
                {
                    "workflow_id": {"type": "string"},
                    "workflow_json": {"type": "object"},
                    "meta": {"type": "object"},
                    "params": {"type": "object"},
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
                },
                ["image_path", "aspect_ratio"],
            ),
        ),
    ]

    handlers: Dict[str, Callable[..., Any] | Callable[..., Awaitable[Any]]] = {
        "comfy_nodes_list": comfy_tools.nodes_list,
        "comfy_queue_get": comfy_tools.queue_get,
        "comfy_history_get": comfy_tools.history_get,
        "comfy_models_list": comfy_tools.models_list,
        "comfy_models_get": comfy_tools.models_get,
        "comfy_embeddings_list": comfy_tools.embeddings_list,
        "list_tools": lambda: {"tools": [_tool_to_payload(tool) for tool in tool_defs]},
        "workflows_list": workflow_tools.list,
        "workflows_get": workflow_tools.get,
        "workflows_params_get": workflow_tools.params_get,
        "workflows_save": workflow_tools.save,
        "workflows_delete": workflow_tools.delete,
        "workflows_run": workflow_tools.run,
        "workflows_wait": workflow_tools.wait,
        "workflows_extract_from_artifact": workflow_tools.extract_from_artifact,
        "workflows_import_from_artifact": workflow_tools.import_from_artifact,
        "workflows_run_aspect_ratio_adjustment": workflow_tools.run_aspect_ratio_adjustment,
    }

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
