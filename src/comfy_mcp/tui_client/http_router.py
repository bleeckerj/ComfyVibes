"""HTTP-based MCP tool router for multi-server tool calls.

Talks to MCP HTTP proxy servers (like comfy_mcp.mcp_server.http_server
and the Photarium MCP HTTP proxy) instead of using stdio subprocesses.

This is the recommended transport when the MCP servers are already running.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from comfy_mcp.tui_client.config import ServerConfig
from comfy_mcp.tui_client.mcp_router import ToolSpec


@dataclass
class _HTTPServerState:
    name: str
    base_url: str
    prefixes: List[str]
    tools: List[ToolSpec]


class HTTPToolRouter:
    """Tool router that calls MCP HTTP proxy servers directly.

    No subprocesses, no stdio, no MCP SDK needed.
    Just HTTP POST to /tools/<name> on each server.
    """

    def __init__(self, servers: List[ServerConfig]):
        self._servers = servers
        self._server_states: List[_HTTPServerState] = []
        self._tool_map: Dict[str, _HTTPServerState] = {}
        self._tool_specs: List[ToolSpec] = []
        self._client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(timeout=300.0)

        for server in self._servers:
            base_url = server.http_url
            if not base_url:
                raise RuntimeError(
                    f"Server '{server.name}' has no http_url configured. "
                    "Set 'http_url' in config or use the stdio router."
                )

            # Health check
            try:
                resp = await self._client.get(f"{base_url}/health")
                resp.raise_for_status()
            except Exception as exc:
                raise RuntimeError(
                    f"Server '{server.name}' health check failed at {base_url}/health: {exc}"
                ) from exc

            # Fetch tool definitions
            try:
                resp = await self._client.get(f"{base_url}/tools")
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                raise RuntimeError(
                    f"Server '{server.name}' tool listing failed at {base_url}/tools: {exc}"
                ) from exc

            raw_tools = data.get("tools", [])
            tools: List[ToolSpec] = []
            for raw in raw_tools:
                spec = ToolSpec(
                    name=raw.get("name", ""),
                    description=raw.get("description", ""),
                    input_schema=raw.get("inputSchema", {}) or {},
                    server=server.name,
                )

                # Honor per-server prefix filters from config so shared helper
                # tool names (e.g. list_tools) don't collide across servers.
                if server.tool_prefixes:
                    if not any(spec.name.startswith(prefix) for prefix in server.tool_prefixes):
                        continue
                tools.append(spec)

            state = _HTTPServerState(
                name=server.name,
                base_url=base_url,
                prefixes=server.tool_prefixes,
                tools=tools,
            )
            self._server_states.append(state)

            for spec in tools:
                existing_state = self._tool_map.get(spec.name)
                if existing_state is None:
                    self._tool_specs.append(spec)
                    self._tool_map[spec.name] = state
                    continue

                # Avoid silent overwrite when HTTP servers expose duplicate tool
                # names. Keep the first canonical name and auto-namespace the
                # colliding tool so both remain callable.
                alias_name = f"{state.name}__{spec.name}"
                suffix = 2
                while alias_name in self._tool_map:
                    alias_name = f"{state.name}__{spec.name}_{suffix}"
                    suffix += 1
                aliased_spec = ToolSpec(
                    name=alias_name,
                    description=(
                        f"[alias:{server.name}] {spec.description}".strip()
                        if spec.description
                        else f"[alias:{server.name}] {spec.name}"
                    ),
                    input_schema=spec.input_schema,
                    server=server.name,
                )
                self._tool_specs.append(aliased_spec)
                self._tool_map[alias_name] = state

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def list_tool_specs(self) -> List[ToolSpec]:
        return list(self._tool_specs)

    async def get_server_health_statuses(self) -> List[Dict[str, Any]]:
        """Return per-server health payloads for startup diagnostics display."""
        statuses: List[Dict[str, Any]] = []
        if self._client is None:
            return statuses

        for state in self._server_states:
            url = f"{state.base_url}/health"
            entry: Dict[str, Any] = {
                "name": state.name,
                "base_url": state.base_url,
                "health_url": url,
                "ok": False,
                "status_code": None,
                "payload": None,
                "error": None,
            }
            try:
                resp = await self._client.get(url)
                entry["status_code"] = resp.status_code
                entry["ok"] = resp.status_code < 400
                try:
                    payload = resp.json()
                except Exception:
                    payload = {"raw_text": resp.text} if resp.text else {}
                entry["payload"] = payload if isinstance(payload, dict) else {"payload": payload}
            except Exception as exc:
                entry["error"] = str(exc)
            statuses.append(entry)

        return statuses

    async def get_server_connection_statuses(self) -> List[Dict[str, Any]]:
        statuses = await self.get_server_health_statuses()
        for entry in statuses:
            if isinstance(entry, dict):
                entry["transport"] = "http"
        return statuses

    async def call_tool(self, name: str, arguments: Dict[str, Any] | None) -> Any:
        state = self._tool_map.get(name)
        if state is None:
            raise RuntimeError(f"Tool not found: {name}")
        if self._client is None:
            raise RuntimeError("Router not connected")

        url = f"{state.base_url}/tools/{name}"
        resp = await self._client.post(url, json=arguments or {})
        if resp.status_code >= 400:
            message = f"HTTP {resp.status_code} calling {name}"
            try:
                payload = resp.json()
                if isinstance(payload, dict):
                    if "error" in payload and payload.get("error"):
                        message = str(payload["error"])
                    elif "detail" in payload and payload.get("detail"):
                        message = str(payload["detail"])
                    elif "result" in payload and isinstance(payload["result"], dict):
                        nested_error = payload["result"].get("error")
                        if nested_error:
                            message = str(nested_error)
            except Exception:
                if resp.text:
                    message = f"{message}: {resp.text}"
            raise RuntimeError(self._enrich_tool_error(name=name, arguments=arguments, message=message))
        payload = resp.json() if resp.content else {}

        # Unwrap the {ok, result} envelope from the HTTP proxy
        if isinstance(payload, dict) and "ok" in payload:
            if not payload.get("ok"):
                message = str(payload.get("error") or payload.get("detail") or "").strip()
                if not message:
                    result_payload = payload.get("result")
                    if isinstance(result_payload, dict):
                        nested_error = result_payload.get("error")
                        if isinstance(nested_error, str) and nested_error.strip():
                            message = nested_error.strip()
                        elif result_payload.get("isError"):
                            content = result_payload.get("content")
                            if isinstance(content, list) and content:
                                first = content[0]
                                if isinstance(first, dict):
                                    text = first.get("text")
                                    if isinstance(text, str) and text.strip():
                                        message = text.strip()
                raise RuntimeError(message or "Tool call failed")
            return _maybe_parse_json_text_content(payload.get("result"))

        return _maybe_parse_json_text_content(payload)

    @staticmethod
    def _enrich_tool_error(name: str, arguments: Dict[str, Any] | None, message: str) -> str:
        if name != "workflows_run":
            return message
        if not isinstance(arguments, dict) or "overrides" in arguments:
            return message
        lower = message.lower()
        if "override" not in lower and "required" not in lower and "missing" not in lower:
            return message
        return (
            f"{message}. Fix: call workflows_run with an explicit overrides object, "
            'for example {"workflow_id":"<id>","overrides":{...}}'
        )


def _maybe_parse_json_text_content(result: Any) -> Any:
    if isinstance(result, dict):
        content = result.get("content")
        parsed = _parse_json_from_content(content)
        return parsed if parsed is not None else result
    if isinstance(result, list):
        parsed = _parse_json_from_content(result)
        return parsed if parsed is not None else result
    return result


def _parse_json_from_content(content: Any) -> Any | None:
    if not isinstance(content, list) or not content:
        return None
    first = content[0]
    if not isinstance(first, dict):
        return None
    if first.get("type") != "text":
        return None
    text = first.get("text")
    if not isinstance(text, str):
        return None
    candidate = text.strip()
    if not candidate or not (candidate.startswith("{") or candidate.startswith("[")):
        return None
    try:
        return json.loads(candidate)
    except Exception:
        return None
