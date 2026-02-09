"""Deterministic MCP tool runner (no LLM)."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from comfy_mcp.tui_client.config import load_config
from comfy_mcp.tui_client.mcp_router import MCPToolRouter


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MCP tools directly (no LLM).")
    parser.add_argument(
        "--config",
        default="mcp_chat_config.json",
        help="Path to MCP config JSON.",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Run connectivity diagnostics (no LLM).")

    sub.add_parser("list-tools", help="List all available MCP tools.")

    call = sub.add_parser("call", help="Call one MCP tool with JSON args.")
    call.add_argument("--tool", required=True, help="Tool name, e.g. photarium_get")
    call.add_argument(
        "--args",
        default="{}",
        help='JSON object string for tool args, e.g. \'{"id":"123"}\'',
    )

    batch = sub.add_parser("run-steps", help="Run a sequence of tool calls from a JSON file.")
    batch.add_argument(
        "--steps-file",
        required=True,
        help="Path to JSON file: [{\"tool\":\"...\",\"args\":{...}}, ...]",
    )

    return parser.parse_args()


async def _list_tools(router: MCPToolRouter) -> int:
    specs = router.list_tool_specs()
    payload = [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.input_schema,
        }
        for spec in specs
    ]
    print(json.dumps(payload, indent=2))
    return 0


@dataclass
class _CheckResult:
    ok: bool
    name: str
    detail: str


async def _http_probe(url: str, timeout_s: float = 2.0) -> _CheckResult:
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.get(url)
            return _CheckResult(
                ok=response.status_code < 500,
                name=url,
                detail=f"HTTP {response.status_code}",
            )
    except Exception as exc:
        return _CheckResult(ok=False, name=url, detail=str(exc))


def _tool_error_text(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    if not result.get("isError"):
        return None
    content = result.get("content") or []
    if content and isinstance(content, list):
        first = content[0]
        if isinstance(first, dict) and "text" in first:
            return str(first["text"])
    return "tool returned error"


async def _doctor(config_path: str) -> int:
    cfg = load_config(config_path)
    checks: list[_CheckResult] = []

    comfy_server = next((s for s in cfg.servers if s.name == "comfy"), None)
    if comfy_server:
        comfy_base = (comfy_server.env or {}).get("COMFY_MCP_COMFY_BASE_URL", "http://127.0.0.1:8188")
        checks.append(await _http_probe(f"{comfy_base.rstrip('/')}/queue"))

    photarium_server = next((s for s in cfg.servers if s.name == "photarium"), None)
    if photarium_server:
        photarium_base = (photarium_server.env or {}).get("PHOTARIUM_BASE_URL", "http://localhost:3000")
        checks.append(await _http_probe(f"{photarium_base.rstrip('/')}/api/images?limit=1"))

    router = MCPToolRouter(cfg.servers)
    tool_specs = []
    try:
        await router.connect()
        tool_specs = router.list_tool_specs()
        checks.append(_CheckResult(ok=True, name="mcp.connect", detail=f"{len(tool_specs)} tools available"))

        tool_names = {spec.name for spec in tool_specs}
        if "comfy_queue_get" in tool_names:
            result = await router.call_tool("comfy_queue_get", {})
            error = _tool_error_text(result)
            if error:
                checks.append(_CheckResult(ok=False, name="tool.comfy_queue_get", detail=error))
            else:
                checks.append(_CheckResult(ok=True, name="tool.comfy_queue_get", detail="ok"))

        if "photarium_list" in tool_names:
            result = await router.call_tool("photarium_list", {"limit": 1})
            error = _tool_error_text(result)
            if error:
                checks.append(_CheckResult(ok=False, name="tool.photarium_list", detail=error))
            else:
                checks.append(_CheckResult(ok=True, name="tool.photarium_list", detail="ok"))
    except Exception as exc:
        checks.append(_CheckResult(ok=False, name="mcp.connect", detail=str(exc)))
    finally:
        await router.close()

    for item in checks:
        state = "OK" if item.ok else "FAIL"
        print(f"[{state}] {item.name}: {item.detail}")

    if any(not item.ok for item in checks):
        print("\nHints:")
        print("- Set ComfyUI endpoint in mcp_chat_config.json -> servers[name=comfy].env.COMFY_MCP_COMFY_BASE_URL")
        print("- Set Photarium endpoint in mcp_chat_config.json -> servers[name=photarium].env.PHOTARIUM_BASE_URL")
        print("- Ensure those backend services are actually running before starting chat/TUI")
        return 1
    return 0


async def _call_tool(router: MCPToolRouter, tool: str, raw_args: str) -> int:
    try:
        args = json.loads(raw_args) if raw_args else {}
        if not isinstance(args, dict):
            raise ValueError("Args must be a JSON object.")
    except Exception as exc:
        print(json.dumps({"error": f"Invalid --args JSON: {exc}"}, indent=2))
        return 2

    result = await router.call_tool(tool, args)
    print(json.dumps(result, indent=2))
    return 0


async def _run_steps(router: MCPToolRouter, steps_file: str) -> int:
    try:
        steps_payload = json.loads(Path(steps_file).read_text(encoding="utf-8"))
        if not isinstance(steps_payload, list):
            raise ValueError("Steps file must be a JSON array.")
    except Exception as exc:
        print(json.dumps({"error": f"Invalid --steps-file JSON: {exc}"}, indent=2))
        return 2

    results: list[dict[str, Any]] = []
    for index, step in enumerate(steps_payload):
        if not isinstance(step, dict):
            print(json.dumps({"error": f"Step {index} is not an object."}, indent=2))
            return 2
        tool = step.get("tool")
        args = step.get("args", {})
        if not isinstance(tool, str):
            print(json.dumps({"error": f"Step {index} missing string field 'tool'."}, indent=2))
            return 2
        if not isinstance(args, dict):
            print(json.dumps({"error": f"Step {index} field 'args' must be an object."}, indent=2))
            return 2

        try:
            result = await router.call_tool(tool, args)
            results.append({"step": index, "tool": tool, "ok": True, "result": result})
        except Exception as exc:  # pragma: no cover
            results.append({"step": index, "tool": tool, "ok": False, "error": str(exc)})
            print(json.dumps(results, indent=2))
            return 1

    print(json.dumps(results, indent=2))
    return 0


async def _amain(args: argparse.Namespace) -> int:
    if args.command == "doctor":
        return await _doctor(args.config)

    cfg = load_config(args.config)
    router = MCPToolRouter(cfg.servers)
    await router.connect()
    try:
        if args.command == "list-tools":
            return await _list_tools(router)
        if args.command == "call":
            return await _call_tool(router, args.tool, args.args)
        if args.command == "run-steps":
            return await _run_steps(router, args.steps_file)
        print(json.dumps({"error": f"Unknown command: {args.command}"}, indent=2))
        return 2
    finally:
        await router.close()


def main() -> None:
    args = _parse_args()
    raise SystemExit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
