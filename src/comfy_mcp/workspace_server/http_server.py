"""HTTP MCP server for allowlisted filesystem tools.

This is meant to be a general-purpose "workspace" server that EDGAR can use to
move/copy/read/write files across repos, without being tied to a single repo's
tooling constraints.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from comfy_mcp.mcp_server.runtime_info import collect_runtime_info
from comfy_mcp.mcp_server.server import (
    _tool_to_payload,
    invoke_registered_tool,
    validate_tool_arguments,
)
from comfy_mcp.workspace_server.tool_registry import build_workspace_tool_registry


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

def create_http_app() -> FastAPI:
    tool_defs, handlers = build_workspace_tool_registry()
    runtime_info = collect_runtime_info(service_name="workspace-mcp-http")
    tool_lookup = {spec["name"]: spec for spec in tool_defs}

    app = FastAPI(
        title="Workspace MCP HTTP",
        version=str(runtime_info.get("service_version") or "0.0.0+unknown"),
    )

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        return {
            "status": "ok",
            **runtime_info,
            "tool_count": len(tool_defs),
            "roots": handlers["workspace_roots"]().get("roots"),
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
    async def call_tool(name: str, body: Any = Body(default=None)) -> JSONResponse:
        tool = tool_lookup.get(name)
        if tool is None:
            raise HTTPException(status_code=404, detail=f"Unknown tool: {name}")
        args = _extract_arguments(body)
        try:
            args = validate_tool_arguments(tool, args)
            result = await invoke_registered_tool(name, args, tool_defs, handlers)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
        except Exception as exc:  # pragma: no cover - surfaced to client
            return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
        return JSONResponse(content={"ok": True, "result": result})

    return app


def main() -> None:
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("uvicorn is required to run the HTTP server") from exc

    host = "127.0.0.1"
    port = int(__import__("os").environ.get("WORKSPACE_MCP_HTTP_BIND_PORT", "8777"))
    app = create_http_app()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
