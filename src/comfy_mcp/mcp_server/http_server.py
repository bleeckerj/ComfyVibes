"""HTTP proxy for Comfy MCP tools."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from comfy_mcp.config.load_config import load_config
from comfy_mcp.config.models import AppConfig
from comfy_mcp.mcp_server.runtime_info import collect_runtime_info
from comfy_mcp.mcp_server.server import (
    _tool_input_schema,
    _tool_to_payload,
    build_tool_registry,
    invoke_registered_tool,
    validate_tool_arguments,
)


def _extract_token(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip() or None
    return request.headers.get("x-mcp-token") or request.headers.get("X-MCP-Token")


def _inject_token(handler: Any, args: Dict[str, Any], request: Request) -> Dict[str, Any]:
    schema = _tool_input_schema(handler)
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    if "token" not in args and "token" not in properties:
        return args
    if "token" in args:
        return args
    token = _extract_token(request)
    if token:
        updated = dict(args)
        updated["token"] = token
        return updated
    return args


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
    tool_lookup = {str(_tool_to_payload(tool).get("name")): tool for tool in tool_defs}
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
        tool = tool_lookup.get(name)
        if tool is None:
            raise HTTPException(status_code=404, detail=f"Unknown tool: {name}")
        try:
            args = _extract_arguments(body)
            args = validate_tool_arguments(tool, args)
            args = _inject_token(tool, args, request)
            result = await invoke_registered_tool(name, args, tool_defs, handlers)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
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
        tool = tool_lookup.get(name)
        if tool is None:
            raise HTTPException(status_code=404, detail=f"Unknown tool: {name}")
        try:
            args = _extract_arguments(body.get("arguments"))
            args = validate_tool_arguments(tool, args)
            args = _inject_token(tool, args, request)
            result = await invoke_registered_tool(name, args, tool_defs, handlers)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
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
