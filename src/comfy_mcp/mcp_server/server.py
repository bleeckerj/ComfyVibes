"""MCP server wiring (requires MCP Python SDK at runtime)."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict

from comfy_mcp.comfy_client.client import ComfyClient
from comfy_mcp.config.models import AppConfig
from comfy_mcp.mcp_server.comfy_org_auth import build_comfy_org_extra_data
from comfy_mcp.mcp_server.policy import Policy
from comfy_mcp.mcp_server.tool_registry import build_handler_registry
from comfy_mcp.mcp_server.tool_specs import build_tool_specs
from comfy_mcp.mcp_server.tools_comfy import ComfyTools
from comfy_mcp.mcp_server.tools_workflows import WorkflowTools
from comfy_mcp.workflow_store.store import WorkflowStore


def build_tools(config: AppConfig) -> tuple[ComfyTools, WorkflowTools]:
    """Build tool handlers for MCP server wiring."""
    client = ComfyClient(str(config.comfy_base_url))
    primary_root = config.resolved_workflow_root()
    run_root = config.resolved_run_workflow_root()
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
    comfy_org_extra_data = build_comfy_org_extra_data(
        auth_token=config.comfy_org_auth_token,
        auth_token_file=config.comfy_org_auth_token_file,
        api_key=config.comfy_org_api_key,
        api_key_file=config.comfy_org_api_key_file,
    )
    return ComfyTools(client), WorkflowTools(
        store,
        client,
        policy,
        comfy_output_dir=config.comfy_output_dir,
        run_store=WorkflowStore(run_root),
        comfy_org_extra_data=comfy_org_extra_data,
    )

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


def _tool_input_schema(tool: Any) -> Dict[str, Any]:
    payload = _tool_to_payload(tool)
    schema = payload.get("inputSchema")
    return schema if isinstance(schema, dict) else {}


def _tool_lookup(tool_defs: list[Any]) -> Dict[str, Any]:
    return {str(_tool_to_payload(tool).get("name")): tool for tool in tool_defs}


def _normalize_args_with_schema(raw_args: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    allowed = set(properties)
    camel_aliases = {
        "".join(part.capitalize() if index else part for index, part in enumerate(name.split("_"))): name
        for name in allowed
        if "_" in name
    }

    def _merge_supported_values(source: Dict[str, Any], target: Dict[str, Any]) -> None:
        for key, value in source.items():
            if key in allowed and key not in target:
                target[key] = value
                continue
            alias = camel_aliases.get(key)
            if alias and alias not in target:
                target[alias] = value

    normalized: Dict[str, Any] = {}
    _merge_supported_values(raw_args, normalized)
    for wrapper_key in ("arguments", "args", "input", "payload", "params"):
        wrapped = raw_args.get(wrapper_key)
        if isinstance(wrapped, dict):
            _merge_supported_values(wrapped, normalized)
    return normalized


def validate_tool_arguments(tool: Any, raw_args: Dict[str, Any] | None) -> Dict[str, Any]:
    schema = _tool_input_schema(tool)
    if raw_args is None:
        raw_args = {}
    normalized = _normalize_args_with_schema(raw_args, schema)
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    camel_aliases = {
        "".join(part.capitalize() if index else part for index, part in enumerate(name.split("_"))): name
        for name in properties
        if "_" in name
    }
    required = set(schema.get("required", []) if isinstance(schema, dict) else [])
    unknown = sorted(
        set(raw_args)
        - set(properties)
        - set(camel_aliases)
        - {"arguments", "args", "input", "payload", "params"}
    )
    if unknown:
        raise ValueError(f"Unknown parameter(s): {', '.join(unknown)}")
    missing = [name for name in required if name not in normalized]
    if missing:
        raise ValueError(f"Missing required parameter(s): {', '.join(missing)}")
    return normalized


def assert_tool_contracts(tool_defs: list[Any], handlers: Dict[str, Callable[..., Any] | Callable[..., Awaitable[Any]]]) -> None:
    tool_lookup = _tool_lookup(tool_defs)
    errors: list[str] = []
    for name, tool in tool_lookup.items():
        handler = handlers.get(name)
        if handler is None:
            errors.append(f"{name}: missing handler")
            continue
        signature = inspect.signature(handler)
        parameters = signature.parameters.values()
        if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters):
            continue
        allowed = {
            param_name
            for param_name, parameter in signature.parameters.items()
            if parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        }
        required = set(_tool_input_schema(tool).get("required", []))
        missing = sorted(required - allowed)
        if missing:
            errors.append(f"{name}: handler missing required params: {', '.join(missing)}")
    if errors:
        raise RuntimeError("Tool contract validation failed:\n- " + "\n- ".join(errors))


async def invoke_registered_tool(
    name: str,
    raw_args: Dict[str, Any] | None,
    tool_defs: list[Any],
    handlers: Dict[str, Callable[..., Any] | Callable[..., Awaitable[Any]]],
) -> Any:
    tool = _tool_lookup(tool_defs).get(name)
    if tool is None:
        raise KeyError(name)
    handler = handlers.get(name)
    if handler is None:
        raise KeyError(name)
    kwargs = validate_tool_arguments(tool, raw_args)
    if inspect.iscoroutinefunction(handler):
        return await handler(**kwargs)
    return handler(**kwargs)


def build_tool_registry(config: AppConfig) -> tuple[list[Any], Dict[str, Callable[..., Any] | Callable[..., Awaitable[Any]]]]:
    """Return tool definitions and handlers for both MCP and HTTP servers."""
    try:
        from mcp import types
    except ImportError as exc:
        raise RuntimeError("MCP SDK not installed. Add it to dependencies.") from exc

    comfy_tools, workflow_tools = build_tools(config)

    tool_defs = build_tool_specs(types)

    def _tool_schema_get(name: str) -> Dict[str, Any]:
        payload = _list_tool_payloads(tool_defs, name=name)
        if not payload["tools"]:
            return {"error": f"Unknown tool: {name}"}
        return {"tool": payload["tools"][0]}

    handlers = build_handler_registry(
        comfy_tools,
        workflow_tools,
        list_tools_handler=lambda name=None, prefix=None, limit=None, include_schema=True: _list_tool_payloads(
            tool_defs,
            name=name,
            prefix=prefix,
            limit=limit,
            include_schema=include_schema,
        ),
        tool_schema_get_handler=_tool_schema_get,
    )
    assert_tool_contracts(tool_defs, handlers)

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
        try:
            return await invoke_registered_tool(name, arguments or {}, tool_defs, handlers)
        except KeyError:
            return {"error": f"Unknown tool: {name}"}
        except Exception as exc:
            return {"error": str(exc)}

    return server
