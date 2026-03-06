"""HTTP proxy for Comfy MCP tools."""

from __future__ import annotations

import inspect
from typing import Any, Dict, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from comfy_mcp.config.load_config import load_config
from comfy_mcp.config.models import AppConfig
from comfy_mcp.mcp_server.runtime_info import collect_runtime_info
from comfy_mcp.mcp_server.server import build_tool_registry, _tool_to_payload


def _extract_token(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip() or None
    return request.headers.get("x-mcp-token") or request.headers.get("X-MCP-Token")


def _inject_token(handler: Any, args: Dict[str, Any], request: Request) -> Dict[str, Any]:
    signature = inspect.signature(handler)
    if "token" not in signature.parameters or "token" in args:
        return args
    token = _extract_token(request)
    if token:
        updated = dict(args)
        updated["token"] = token
        return updated
    return args


def _filter_supported_kwargs(handler: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Drop unexpected kwargs unless handler explicitly accepts **kwargs."""
    signature = inspect.signature(handler)
    parameters = signature.parameters.values()
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters):
        return kwargs
    allowed = {
        name
        for name, parameter in signature.parameters.items()
        if parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
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

    normalized = dict(kwargs)
    _merge_supported_values(kwargs, normalized)
    for wrapper_key in ("arguments", "args", "input", "payload", "params"):
        wrapped = kwargs.get(wrapper_key)
        if not isinstance(wrapped, dict):
            continue
        _merge_supported_values(wrapped, normalized)
    return {key: value for key, value in normalized.items() if key in allowed}


def _extract_arguments(payload: Any) -> Dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, dict):
        if "arguments" in payload and isinstance(payload["arguments"], dict):
            return payload["arguments"]
        if "args" in payload and isinstance(payload["args"], dict):
            return payload["args"]
        return payload
    raise HTTPException(status_code=400, detail="Request body must be a JSON object")


def create_http_app(config: AppConfig) -> FastAPI:
    tool_defs, handlers = build_tool_registry(config)
    tool_lookup = {getattr(tool, "name", None): tool for tool in tool_defs}
    runtime_info = collect_runtime_info(service_name="comfy-mcp-http")

    app = FastAPI(
        title="Comfy MCP HTTP Proxy",
        version=str(runtime_info.get("service_version") or "0.0.0+unknown"),
    )

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        return {
            "status": "ok",
            **runtime_info,
            "tool_count": len(tool_defs),
        }

    @app.get("/version")
    async def version() -> Dict[str, Any]:
        return {
            **runtime_info,
            "tool_count": len(tool_defs),
        }

    @app.get("/tools")
    async def list_tools() -> Dict[str, Any]:
        return {"tools": [_tool_to_payload(tool) for tool in tool_defs]}

    @app.get("/tools/{name}")
    async def get_tool(name: str) -> Dict[str, Any]:
        tool = tool_lookup.get(name)
        if tool is None:
            raise HTTPException(status_code=404, detail=f"Unknown tool: {name}")
        return {"tool": _tool_to_payload(tool)}

    @app.post("/tools/{name}")
    async def call_tool(name: str, request: Request, body: Any = Body(default=None)) -> JSONResponse:
        handler = handlers.get(name)
        if handler is None:
            raise HTTPException(status_code=404, detail=f"Unknown tool: {name}")
        args = _extract_arguments(body)
        args = _inject_token(handler, args, request)
        args = _filter_supported_kwargs(handler, args)
        try:
            if inspect.iscoroutinefunction(handler):
                result = await handler(**args)
            else:
                result = handler(**args)
        except Exception as exc:  # pragma: no cover - surfaced to client
            return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
        return JSONResponse(content={"ok": True, "result": result})

    @app.post("/tools/call")
    async def call_tool_generic(request: Request, body: Any = Body(...)) -> JSONResponse:
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Request body must be a JSON object")
        name = body.get("name")
        if not name:
            raise HTTPException(status_code=400, detail="Missing tool name")
        handler = handlers.get(name)
        if handler is None:
            raise HTTPException(status_code=404, detail=f"Unknown tool: {name}")
        args = _extract_arguments(body.get("arguments"))
        args = _inject_token(handler, args, request)
        args = _filter_supported_kwargs(handler, args)
        try:
            if inspect.iscoroutinefunction(handler):
                result = await handler(**args)
            else:
                result = handler(**args)
        except Exception as exc:  # pragma: no cover - surfaced to client
            return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
        return JSONResponse(content={"ok": True, "result": result})

    return app


def main() -> None:
    """Run the HTTP proxy server."""
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - runtime guard
        raise RuntimeError("uvicorn is required to run the HTTP server") from exc

    config = load_config()
    app = create_http_app(config)
    uvicorn.run(
        app,
        host=config.http_bind_host,
        port=config.http_bind_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
