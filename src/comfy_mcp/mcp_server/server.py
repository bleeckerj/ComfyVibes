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

    tool_defs = [
        types.Tool(
            name="comfy.nodes.list",
            description="Return ComfyUI node catalog.",
            inputSchema=_tool_schema(),
        ),
        types.Tool(
            name="comfy.queue.get",
            description="Return ComfyUI queue state.",
            inputSchema=_tool_schema(),
        ),
        types.Tool(
            name="comfy.history.get",
            description="Return ComfyUI history data.",
            inputSchema=_tool_schema(
                {"prompt_id": {"type": "string"}},
                [],
            ),
        ),
        types.Tool(
            name="comfy.models.list",
            description="Return available model folders.",
            inputSchema=_tool_schema(),
        ),
        types.Tool(
            name="comfy.models.get",
            description="Return available model files for a folder.",
            inputSchema=_tool_schema({"folder": {"type": "string"}}, ["folder"]),
        ),
        types.Tool(
            name="comfy.embeddings.list",
            description="Return available embedding names.",
            inputSchema=_tool_schema(),
        ),
        types.Tool(
            name="workflows.list",
            description="List workflows in the store.",
            inputSchema=_tool_schema(),
        ),
        types.Tool(
            name="workflows.get",
            description="Return workflow JSON and metadata.",
            inputSchema=_tool_schema({"workflow_id": {"type": "string"}}, ["workflow_id"]),
        ),
        types.Tool(
            name="workflows.params.get",
            description="Return params.json for a workflow id.",
            inputSchema=_tool_schema({"workflow_id": {"type": "string"}}, ["workflow_id"]),
        ),
        types.Tool(
            name="workflows.save",
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
            name="workflows.delete",
            description="Delete a workflow entry from the store.",
            inputSchema=_tool_schema(
                {"workflow_id": {"type": "string"}, "token": {"type": "string"}},
                ["workflow_id"],
            ),
        ),
        types.Tool(
            name="workflows.run",
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
            name="workflows.wait",
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
            name="workflows.extract_from_artifact",
            description="Extract workflow from an image or video artifact.",
            inputSchema=_tool_schema(
                {"path": {"type": "string"}, "preserve_format": {"type": "boolean"}},
                ["path"],
            ),
        ),
        types.Tool(
            name="workflows.import_from_artifact",
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
    ]

    handlers: Dict[str, Callable[..., Any] | Callable[..., Awaitable[Any]]] = {
        "comfy.nodes.list": comfy_tools.nodes_list,
        "comfy.queue.get": comfy_tools.queue_get,
        "comfy.history.get": comfy_tools.history_get,
        "comfy.models.list": comfy_tools.models_list,
        "comfy.models.get": comfy_tools.models_get,
        "comfy.embeddings.list": comfy_tools.embeddings_list,
        "workflows.list": workflow_tools.list,
        "workflows.get": workflow_tools.get,
        "workflows.params.get": workflow_tools.params_get,
        "workflows.save": workflow_tools.save,
        "workflows.delete": workflow_tools.delete,
        "workflows.run": workflow_tools.run,
        "workflows.wait": workflow_tools.wait,
        "workflows.extract_from_artifact": workflow_tools.extract_from_artifact,
        "workflows.import_from_artifact": workflow_tools.import_from_artifact,
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
