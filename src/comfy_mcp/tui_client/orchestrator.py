"""Tool-calling loop orchestration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Protocol

from comfy_mcp.tui_client.llm_client import LLMResponse, ToolCall


class LLMProtocol(Protocol):
    async def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> LLMResponse: ...


class RouterProtocol(Protocol):
    async def call_tool(self, name: str, arguments: Dict[str, Any] | None) -> Any: ...


@dataclass
class ToolEvent:
    name: str
    arguments: Dict[str, Any]
    result: Any | None = None
    error: str | None = None


class ChatOrchestrator:
    def __init__(self, system_prompt: str, llm: LLMProtocol, router: RouterProtocol):
        self._llm = llm
        self._router = router
        self._tools: List[Dict[str, Any]] = []
        self._messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    def set_tools(self, tools: List[Dict[str, Any]]) -> None:
        self._tools = list(tools)

    async def process(
        self,
        user_text: str,
        on_progress: Callable[[str, Dict[str, Any]], None] | None = None,
        stop_after_tool_calls: bool = False,
    ) -> tuple[str | None, List[ToolEvent]]:
        self._messages.append({"role": "user", "content": user_text})
        tool_events: List[ToolEvent] = []

        while True:
            if on_progress:
                on_progress("llm_request", {"message_count": len(self._messages)})
            response = await self._llm.chat(self._messages, self._tools)
            if on_progress:
                on_progress(
                    "llm_response",
                    {
                        "has_content": bool(response.content),
                        "tool_call_count": len(response.tool_calls),
                    },
                )
            if response.tool_calls:
                self._messages.append(
                    {
                        "role": "assistant",
                        "content": response.content,
                        "tool_calls": [
                            {
                                "id": call.call_id,
                                "type": "function",
                                "function": {
                                    "name": call.name,
                                    "arguments": json.dumps(call.arguments),
                                },
                            }
                            for call in response.tool_calls
                        ],
                    }
                )
                for call in response.tool_calls:
                    event = ToolEvent(name=call.name, arguments=call.arguments)
                    if on_progress:
                        on_progress(
                            "tool_call_start",
                            {"name": call.name, "arguments": call.arguments},
                        )
                    try:
                        result = await self._router.call_tool(call.name, call.arguments)
                        event.result = result
                        if on_progress:
                            on_progress("tool_call_result", {"name": call.name, "result": result})
                    except Exception as exc:  # pragma: no cover
                        event.error = str(exc)
                        result = {"error": event.error}
                        if on_progress:
                            on_progress("tool_call_error", {"name": call.name, "error": event.error})
                    tool_events.append(event)
                    self._messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.call_id,
                            "name": call.name,
                            "content": json.dumps(result, indent=2),
                        }
                    )
                if stop_after_tool_calls:
                    if on_progress:
                        on_progress("complete", {"assistant_text": None, "stopped_after_tools": True})
                    return None, tool_events
                continue

            if response.content:
                self._messages.append({"role": "assistant", "content": response.content})
                if on_progress:
                    on_progress("complete", {"assistant_text": response.content})
                return response.content, tool_events

            if on_progress:
                on_progress("complete", {"assistant_text": None})
            return None, tool_events
