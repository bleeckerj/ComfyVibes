"""HTTP client wrapper for calling remote MCP HTTP tools."""

from __future__ import annotations

import json
from typing import Any

import httpx


class RemoteToolClient:
    """Thin wrapper around remote MCP HTTP tool calls."""

    async def call(self, base_url: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        url = f"{base_url.rstrip('/')}/tools/{tool_name}"
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(url, json=arguments)
            response.raise_for_status()
            payload: Any = response.json() if response.content else {}
        return self.normalize_payload(payload)

    @classmethod
    def normalize_payload(cls, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Remote tool returned non-object payload")

        if "ok" in payload:
            if not payload.get("ok"):
                raise RuntimeError(str(payload.get("error") or "Remote tool call failed"))
            return cls.normalize_payload(payload.get("result") or {})

        if "content" in payload:
            content = payload.get("content")
            if isinstance(content, list) and content:
                first = content[0]
                if isinstance(first, dict) and isinstance(first.get("text"), str):
                    text = first["text"]
                    try:
                        parsed = json.loads(text)
                    except json.JSONDecodeError:
                        return {"text": text}
                    if isinstance(parsed, dict):
                        return parsed
            return payload

        if "result" in payload and isinstance(payload.get("result"), dict):
            return cls.normalize_payload(payload["result"])

        return payload
