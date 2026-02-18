"""Simple HTTP client for Comfy MCP + Photarium MCP proxies."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


def _request_json(method: str, url: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else str(exc)
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        hint = "Check that the server is running and the URL/port are correct."
        raise RuntimeError(f"Connection failed: {reason}. {hint}") from exc


def _print_header(title: str) -> None:
    print("\n" + title)
    print("-" * len(title))


def _health(base_url: str) -> Dict[str, Any]:
    return _request_json("GET", f"{base_url}/health")


def _list_tools(base_url: str) -> Dict[str, Any]:
    return _request_json("GET", f"{base_url}/tools")


def _call_tool(base_url: str, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    return _request_json("POST", f"{base_url}/tools/{name}", args)


def _run_smoke(base_url: str, sample_tool: Optional[str] = None, sample_args: Optional[Dict[str, Any]] = None) -> None:
    _print_header(f"Health: {base_url}")
    try:
        print(json.dumps(_health(base_url), indent=2))
    except RuntimeError as exc:
        print(f"Error: {exc}")
        return

    _print_header(f"Tools: {base_url}")
    try:
        tools = _list_tools(base_url)
        print(json.dumps({"count": len(tools.get("tools", []))}, indent=2))
    except RuntimeError as exc:
        print(f"Error: {exc}")
        return

    if sample_tool:
        _print_header(f"Call: {sample_tool}")
        try:
            result = _call_tool(base_url, sample_tool, sample_args or {})
            print(json.dumps(result, indent=2))
        except RuntimeError as exc:
            print(f"Error: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="HTTP client for MCP proxy servers")
    parser.add_argument(
        "--comfy-url",
        default="http://127.0.0.1:8001",
        help="Comfy MCP HTTP base URL",
    )
    parser.add_argument(
        "--photarium-url",
        default="http://127.0.0.1:8787",
        help="Photarium MCP HTTP base URL",
    )
    parser.add_argument(
        "--server",
        choices=["comfy", "photarium", "both"],
        default="both",
        help="Which server to target",
    )
    parser.add_argument("--tool", help="Tool name to call")
    parser.add_argument("--args", help="JSON args for the tool call")
    parser.add_argument(
        "--sample",
        action="store_true",
        help="Run a sample tool call when no --tool is provided",
    )

    args = parser.parse_args()
    tool_args: Dict[str, Any] = {}
    if args.args:
        try:
            tool_args = json.loads(args.args)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSON for --args: {exc}") from exc

    if args.server in {"comfy", "both"}:
        comfy_tool = args.tool
        comfy_args = tool_args
        if args.sample and not args.tool:
            comfy_tool = "comfy_queue_get"
            comfy_args = {}
        _run_smoke(args.comfy_url, comfy_tool, comfy_args)

    if args.server in {"photarium", "both"}:
        photarium_tool = args.tool
        photarium_args = tool_args
        if args.sample and not args.tool:
            photarium_tool = "photarium_list"
            photarium_args = {"limit": 1}
        _run_smoke(args.photarium_url, photarium_tool, photarium_args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
