"""Deterministic MCP tool runner (no LLM)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from comfy_mcp.tui_client.config import ServerConfig, load_config
from comfy_mcp.tui_client.http_router import HTTPToolRouter
from comfy_mcp.tui_client.hybrid_router import HybridToolRouter
from comfy_mcp.tui_client.mcp_router import MCPToolRouter
from comfy_mcp.tui_client.tool_exposure_diagnostics import build_zero_tool_warnings
from comfy_mcp.tui_client.transport_plan import build_transport_plan


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


def _build_router(servers: list[ServerConfig]):
    """Pick HTTP or stdio router based on server config."""
    plan = build_transport_plan(servers)
    if plan.http_servers and plan.stdio_servers:
        return HybridToolRouter(http_servers=plan.http_servers, stdio_servers=plan.stdio_servers)
    if plan.stdio_servers:
        return MCPToolRouter(plan.stdio_servers)
    return HTTPToolRouter(plan.http_servers)


async def _list_tools(router: Any) -> int:
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
    severity: str = "ok"


async def _http_probe(url: str, timeout_s: float = 2.0) -> _CheckResult:
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.get(url)
            detail = f"HTTP {response.status_code}"
            if response.status_code < 500:
                try:
                    payload = response.json()
                except Exception:
                    payload = None
                if isinstance(payload, dict):
                    version = payload.get("service_version")
                    commit = payload.get("git_commit")
                    if isinstance(version, str) and version:
                        detail += f", version={version}"
                    if isinstance(commit, str) and commit:
                        detail += f", commit={commit}"
            return _CheckResult(
                ok=response.status_code < 500,
                name=url,
                detail=detail,
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


async def _call_tool_check(
    router: Any,
    *,
    tool: str,
    args: dict[str, Any],
    timeout_s: float = 8.0,
) -> _CheckResult:
    try:
        result = await asyncio.wait_for(router.call_tool(tool, args), timeout=timeout_s)
        error = _tool_error_text(result)
        if error:
            return _CheckResult(ok=False, name=f"tool.{tool}", detail=error)
        return _CheckResult(ok=True, name=f"tool.{tool}", detail="ok")
    except asyncio.TimeoutError:
        return _CheckResult(
            ok=False,
            name=f"tool.{tool}",
            detail=f"timed out after {timeout_s:.1f}s",
        )
    except Exception as exc:
        return _CheckResult(ok=False, name=f"tool.{tool}", detail=str(exc))


async def _doctor(config_path: str) -> int:
    cfg = load_config(config_path)
    checks: list[_CheckResult] = []

    comfy_server = next((s for s in cfg.servers if s.name == "comfy"), None)
    if comfy_server:
        if comfy_server.transport == "http":
            base = (comfy_server.http_url or "").rstrip("/")
            if base:
                checks.append(await _http_probe(f"{base}/health"))
        comfy_base = (comfy_server.env or {}).get("COMFY_MCP_COMFY_BASE_URL")
        if comfy_base:
            checks.append(await _http_probe(f"{comfy_base.rstrip('/')}/queue"))
        if comfy_server.transport == "stdio":
            checks.extend(_validate_stdio_server(comfy_server))

    photarium_server = next((s for s in cfg.servers if s.name == "photarium"), None)
    if photarium_server:
        if photarium_server.transport == "http":
            base = (photarium_server.http_url or "").rstrip("/")
            if base:
                checks.append(await _http_probe(f"{base}/health"))
        if photarium_server.transport == "stdio":
            checks.extend(_validate_stdio_server(photarium_server))
        photarium_base = (photarium_server.env or {}).get("PHOTARIUM_BASE_URL")
        if photarium_base:
            checks.append(await _http_probe(f"{photarium_base.rstrip('/')}/api/images?limit=1"))

    for server in cfg.servers:
        if server.name == "comfy" or server.transport != "stdio":
            continue
        checks.extend(_validate_stdio_server(server))

    router = _build_router(cfg.servers)
    tool_specs = []
    try:
        await router.connect()
        tool_specs = router.list_tool_specs()
        checks.append(_CheckResult(ok=True, name="mcp.connect", detail=f"{len(tool_specs)} tools available"))
        for warning in build_zero_tool_warnings(cfg.servers, tool_specs):
            checks.append(
                _CheckResult(
                    ok=True,
                    name=f"mcp.tools.{warning.server_name}",
                    detail=warning.message,
                    severity="warn",
                )
            )

        tool_names = {spec.name for spec in tool_specs}
        if "comfy_queue_get" in tool_names:
            checks.append(
                await _call_tool_check(
                    router,
                    tool="comfy_queue_get",
                    args={},
                )
            )

        # Prefer a lightweight Photarium tool for connectivity checks.
        # `photarium_list` can be expensive because some backends return the
        # full catalog before local limiting.
        if "photarium_list_namespaces" in tool_names:
            checks.append(
                await _call_tool_check(
                    router,
                    tool="photarium_list_namespaces",
                    args={},
                )
            )
        elif "photarium_vector_status" in tool_names:
            checks.append(
                await _call_tool_check(
                    router,
                    tool="photarium_vector_status",
                    args={},
                )
            )
        elif "photarium_list_folders" in tool_names:
            checks.append(
                await _call_tool_check(
                    router,
                    tool="photarium_list_folders",
                    args={"namespace": "__all__"},
                )
            )
        elif "photarium_list" in tool_names:
            checks.append(
                await _call_tool_check(
                    router,
                    tool="photarium_list",
                    args={"limit": 1, "namespace": "__all__"},
                )
            )
    except Exception as exc:
        checks.append(_CheckResult(ok=False, name="mcp.connect", detail=str(exc)))
    finally:
        await router.close()

    for item in checks:
        state = "WARN" if item.severity == "warn" else ("OK" if item.ok else "FAIL")
        print(f"[{state}] {item.name}: {item.detail}")

    if any(not item.ok for item in checks):
        print("\nHints:")
        print("- Set ComfyUI endpoint in mcp_chat_config.json -> servers[name=comfy].env.COMFY_MCP_COMFY_BASE_URL")
        print("- Set Photarium endpoint in mcp_chat_config.json -> servers[name=photarium].env.PHOTARIUM_BASE_URL")
        print("- Ensure HTTP-configured backend services are running before starting chat/TUI")
        print("- Ensure stdio-configured servers have a valid command and cwd in mcp_chat_config.json")
        return 1
    return 0


def _validate_stdio_server(server: ServerConfig) -> list[_CheckResult]:
    checks: list[_CheckResult] = []
    command = server.command.strip()
    if not command:
        checks.append(
            _CheckResult(
                ok=False,
                name=f"stdio.{server.name}.command",
                detail="missing command",
            )
        )
    elif os.path.isabs(command):
        checks.append(
            _CheckResult(
                ok=Path(command).exists(),
                name=f"stdio.{server.name}.command",
                detail=command if Path(command).exists() else f"{command} not found",
            )
        )
    else:
        resolved = shutil.which(command)
        checks.append(
            _CheckResult(
                ok=resolved is not None,
                name=f"stdio.{server.name}.command",
                detail=resolved or f"{command} not on PATH",
            )
        )

    if server.cwd:
        cwd_path = Path(server.cwd)
        checks.append(
            _CheckResult(
                ok=cwd_path.is_dir(),
                name=f"stdio.{server.name}.cwd",
                detail=str(cwd_path) if cwd_path.is_dir() else f"{cwd_path} missing",
            )
        )
    return checks


async def _call_tool(router: Any, tool: str, raw_args: str) -> int:
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


def _load_steps_payload(steps_file: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    try:
        steps_payload = json.loads(Path(steps_file).read_text(encoding="utf-8"))
        if not isinstance(steps_payload, list):
            raise ValueError("Steps file must be a JSON array.")
    except Exception as exc:
        return None, f"Invalid --steps-file JSON: {exc}"

    for index, step in enumerate(steps_payload):
        if not isinstance(step, dict):
            return None, f"Step {index} is not an object."
        tool = step.get("tool")
        args = step.get("args", {})
        if not isinstance(tool, str):
            return None, f"Step {index} missing string field 'tool'."
        if not isinstance(args, dict):
            return None, f"Step {index} field 'args' must be an object."

    return steps_payload, None


def _servers_for_tools(
    servers: list[ServerConfig],
    tool_names: set[str],
) -> list[ServerConfig]:
    """Return the configured servers needed for a deterministic tool batch."""
    selected = [
        server
        for server in servers
        if any(
            tool_name.startswith(prefix)
            for tool_name in tool_names
            for prefix in server.tool_prefixes
        )
    ]
    return selected or servers


async def _run_steps(router: Any, steps_payload: list[dict[str, Any]]) -> int:
    results: list[dict[str, Any]] = []
    for index, step in enumerate(steps_payload):
        tool = step["tool"]
        args = step.get("args", {})

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
    steps_payload: list[dict[str, Any]] | None = None
    if args.command == "run-steps":
        steps_payload, error = _load_steps_payload(args.steps_file)
        if error:
            print(json.dumps({"error": error}, indent=2))
            return 2
        cfg.servers = _servers_for_tools(cfg.servers, {step["tool"] for step in steps_payload})

    router = _build_router(cfg.servers)
    await router.connect()
    try:
        if args.command == "list-tools":
            return await _list_tools(router)
        if args.command == "call":
            return await _call_tool(router, args.tool, args.args)
        if args.command == "run-steps":
            assert steps_payload is not None
            return await _run_steps(router, steps_payload)
        print(json.dumps({"error": f"Unknown command: {args.command}"}, indent=2))
        return 2
    finally:
        await router.close()


def main() -> None:
    args = _parse_args()
    raise SystemExit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
