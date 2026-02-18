"""OpenAI LLM client for tool calling."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from openai import APITimeoutError, AsyncOpenAI

from comfy_mcp.tui_client.config import LLMConfig


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: List[ToolCall]


class OpenAIClient:
    def __init__(self, config: LLMConfig):
        self._config = config
        self._client = AsyncOpenAI(
            api_key=_get_api_key(self._config),
            base_url=self._config.base_url,
            timeout=self._config.timeout_s,
        )

    async def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> LLMResponse:
        try:
            response = await self._client.chat.completions.create(
                model=self._config.model,
                messages=messages,
                temperature=self._config.temperature,
                tools=tools,
                tool_choice="auto",
            )
        except APITimeoutError as exc:
            raise RuntimeError(
                f"LLM request timed out after {self._config.timeout_s}s. "
                "Increase llm.timeout_s in mcp_chat_config.json."
            ) from exc
        message = response.choices[0].message
        tool_calls = _parse_tool_calls(message)
        content = getattr(message, "content", None)
        return LLMResponse(content=content, tool_calls=tool_calls)

    async def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        on_token: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        """Streaming version of chat(). Calls *on_token* for each text chunk."""
        try:
            stream = await self._client.chat.completions.create(
                model=self._config.model,
                messages=messages,
                temperature=self._config.temperature,
                tools=tools,
                tool_choice="auto",
                stream=True,
            )
        except APITimeoutError as exc:
            raise RuntimeError(
                f"LLM request timed out after {self._config.timeout_s}s. "
                "Increase llm.timeout_s in mcp_chat_config.json."
            ) from exc

        content_parts: list[str] = []
        tc_accum: dict[int, dict[str, str]] = {}  # index -> {id, name, arguments}

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            # --- text tokens ---
            if delta.content:
                content_parts.append(delta.content)
                if on_token:
                    on_token(delta.content)

            # --- tool-call deltas ---
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tc_accum:
                        tc_accum[idx] = {"id": "", "name": "", "arguments": ""}
                    if tc_delta.id:
                        tc_accum[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tc_accum[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tc_accum[idx]["arguments"] += tc_delta.function.arguments

        content = "".join(content_parts) or None
        tool_calls: list[ToolCall] = []
        for tc in tc_accum.values():
            try:
                parsed_args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                parsed_args = {}
            tool_calls.append(ToolCall(call_id=tc["id"], name=tc["name"], arguments=parsed_args))
        return LLMResponse(content=content, tool_calls=tool_calls)


def _get_api_key(config: LLMConfig) -> Optional[str]:
    import os

    if config.api_key:
        return config.api_key
    return os.environ.get(config.api_key_env)


def _parse_tool_calls(message: Any) -> List[ToolCall]:
    calls = []
    raw_calls = getattr(message, "tool_calls", None)
    if raw_calls is None and isinstance(message, dict):
        raw_calls = message.get("tool_calls")
    if isinstance(raw_calls, list):
        for entry in raw_calls:
            func = getattr(entry, "function", None) or entry.get("function", {})
            func_name = getattr(func, "name", None) or func.get("name", "")
            raw_args = getattr(func, "arguments", None) or func.get("arguments", "{}")
            try:
                parsed_args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                parsed_args = {}
            calls.append(
                ToolCall(
                    call_id=getattr(entry, "id", None) or entry.get("id", ""),
                    name=func_name,
                    arguments=parsed_args,
                )
            )
        return calls

    legacy = getattr(message, "function_call", None)
    if legacy is None and isinstance(message, dict):
        legacy = message.get("function_call")
    if isinstance(legacy, dict):
        raw_args = legacy.get("arguments", "{}")
        try:
            parsed_args = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError:
            parsed_args = {}
        calls.append(
            ToolCall(
                call_id="legacy-call",
                name=legacy.get("name", ""),
                arguments=parsed_args,
            )
        )
    return calls
