"""Tool-calling loop orchestration."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Protocol

from comfy_mcp.tui_client.binary_transfer_adapter import BinaryTransferAdapter, is_photarium_like_upload_tool
from comfy_mcp.tui_client.tool_argument_preflight import ToolArgumentPreflight
from comfy_mcp.tui_client.llm_client import LLMResponse, ToolCall
from comfy_mcp.tui_client.image_namespace_registry import ImageNamespaceRegistry
from comfy_mcp.tui_client.sanitize import sanitize_result
from comfy_mcp.tui_client.search_semantics import (
    normalize_binary_transfer_arguments,
    normalize_photarium_upload_arguments,
    normalize_search_tool_arguments,
    normalize_search_tool_result,
)
from comfy_mcp.tui_client.tool_loop import ToolLoop
from comfy_mcp.tui_client.tool_selection import ToolSelector
from comfy_mcp.tui_client.workflow_tool_repairs import WorkflowToolRepairService
from comfy_mcp.tui_client.workflows_schema import ensure_workflows_run_schema


class LLMProtocol(Protocol):
    async def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> LLMResponse: ...
    async def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        on_token: Callable[[str], None] | None = None,
    ) -> LLMResponse: ...


class RouterProtocol(Protocol):
    async def call_tool(self, name: str, arguments: Dict[str, Any] | None) -> Any: ...


@dataclass
class ToolEvent:
    name: str
    arguments: Dict[str, Any]
    result: Any | None = None
    error: str | None = None


@dataclass(frozen=True)
class StrictCommand:
    kind: str
    target: str
    target_is_index: bool = False


class ChatOrchestrator:
    _MAX_TOOLS_PER_LLM_REQUEST = 128
    _STRICT_PREVIEW_AD_RE = re.compile(r"^\s*preview\s+ad\s+(.+?)\s*$", re.IGNORECASE)

    def __init__(
        self,
        system_prompt: str,
        llm: LLMProtocol,
        router: RouterProtocol,
        *,
        max_non_system_messages: int = 120,
        max_total_content_chars: int = 300_000,
        max_message_content_chars: int = 32_000,
        max_tool_content_chars: int = 48_000,
        max_rounds: int = 16,
        max_repeated_tool_rounds: int = 3,
    ):
        self._llm = llm
        self._router = router
        self._tools: List[Dict[str, Any]] = []
        self._messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        self._max_non_system_messages = max_non_system_messages
        self._max_total_content_chars = max_total_content_chars
        self._max_message_content_chars = max_message_content_chars
        self._max_tool_content_chars = max_tool_content_chars
        self._max_rounds = max_rounds
        self._max_repeated_tool_rounds = max_repeated_tool_rounds
        self._tool_input_schema_by_name: Dict[str, Dict[str, Any]] = {}
        self._image_namespaces = ImageNamespaceRegistry()
        self._source_image_id_by_local_path: Dict[str, str] = {}
        self._tool_execution_lock = asyncio.Lock()
        self._workflow_tool_repairs = WorkflowToolRepairService()
        self._binary_transfer_adapter = BinaryTransferAdapter()
        self._tool_argument_preflight = ToolArgumentPreflight(
            self._workflow_tool_repairs,
            self._binary_transfer_adapter,
        )
        self._last_tool_selection_debug: Dict[str, int] = {}

    @staticmethod
    def _compact_tool_result_for_llm(tool_name: str, result: Any) -> Any:
        """Return a compact, URL-first tool result for LLM context when needed.

        Some tools (notably `editorial_ads_preview`) can include large contextual blobs
        (e.g. preview framing paragraphs). Those can cause the tool message to be
        truncated, which can drop the actual preview URL from the LLM-visible context.
        This compactor preserves the "what you need next" fields at the top.
        """
        if tool_name not in {"editorial_ads_preview", "editorial_ads_preview_by_index"}:
            return result
        if not isinstance(result, dict):
            return result

        preview_url = result.get("previewUrl") or result.get("preview_url")
        preview_urls = result.get("previewUrls") or result.get("preview_urls")

        compact: Dict[str, Any] = {
            "mode": result.get("mode"),
            "previewUrl": preview_url,
            "previewUrls": preview_urls,
            "selectedIds": result.get("selectedIds"),
            "missingRequestedIds": result.get("missingRequestedIds"),
            "inventoryPath": result.get("inventoryPath"),
            "fit": result.get("fit"),
            "actualAspect": result.get("actualAspect"),
            "layoutPreview": result.get("layoutPreview"),
            "matched": result.get("matched"),
            "total": result.get("total"),
        }

        criteria = result.get("criteria")
        if isinstance(criteria, dict):
            criteria_compact: Dict[str, Any] = {
                "adIds": criteria.get("adIds"),
                "search": criteria.get("search"),
                "slot": criteria.get("slot"),
                "limit": criteria.get("limit"),
                "fit": criteria.get("fit"),
                "actualAspect": criteria.get("actualAspect"),
                "layoutPreview": criteria.get("layoutPreview"),
                "contextPath": criteria.get("contextPath"),
            }
            context_preview = criteria.get("contextPreview")
            if isinstance(context_preview, dict):
                criteria_compact["contextPreview"] = {
                    "resolvedPath": context_preview.get("resolvedPath"),
                    "title": context_preview.get("title"),
                    "dek": context_preview.get("dek"),
                }
            compact["criteria"] = criteria_compact

        items = result.get("items")
        if isinstance(items, list) and items:
            compact_items: List[Dict[str, Any]] = []
            for raw in items[:12]:
                if not isinstance(raw, dict):
                    continue
                compact_items.append(
                    {
                        "id": raw.get("id"),
                        "slot": raw.get("slot"),
                        "previewUrl": raw.get("previewUrl"),
                    }
                )
            if compact_items:
                compact["items"] = compact_items

        # Keep a small hint for debugging without bloating the window.
        compact["_llm_compacted"] = True
        return compact

    def reset_conversation(self) -> None:
        """Reset conversation to just the system prompt."""
        if not self._messages:
            return
        self._messages = [self._messages[0]]
        self._image_namespaces.reset()

    def set_system_prompt(self, system_prompt: str) -> None:
        """Update the active system prompt without clearing conversation history."""
        if self._messages:
            self._messages[0] = {"role": "system", "content": system_prompt}
            return
        self._messages = [{"role": "system", "content": system_prompt}]
        self._source_image_id_by_local_path = {}

    def status_snapshot(self) -> Dict[str, int]:
        """Return lightweight conversation stats for UI diagnostics."""
        return {
            "message_count": len(self._messages),
            "total_content_chars": self._total_content_chars(),
        }

    def set_tools(self, tools: List[Dict[str, Any]]) -> None:
        self._tools = list(tools)
        self._tool_input_schema_by_name = {}
        for tool in self._tools:
            if not isinstance(tool, dict):
                continue
            function = tool.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            parameters = function.get("parameters")
            if not isinstance(name, str) or not name:
                continue
            if isinstance(parameters, dict):
                if name == "workflows_run":
                    parameters = ensure_workflows_run_schema(parameters)
                    function["parameters"] = parameters
                self._tool_input_schema_by_name[name] = parameters

    def set_router(self, router: RouterProtocol) -> None:
        """Swap the active router implementation (e.g. after toggling servers)."""
        self._router = router

    async def process(
        self,
        user_text: str,
        on_progress: Callable[[str, Dict[str, Any]], None] | None = None,
        stop_after_tool_calls: bool = False,
    ) -> tuple[str | None, List[ToolEvent]]:
        loop = ToolLoop(self)
        return await loop.process(user_text=user_text, on_progress=on_progress, stop_after_tool_calls=stop_after_tool_calls)

    async def _process_loop(
        self,
        user_text: str,
        on_progress: Callable[[str, Dict[str, Any]], None] | None = None,
        stop_after_tool_calls: bool = False,
    ) -> tuple[str | None, List[ToolEvent]]:
        def _emit_progress(kind: str, payload: Dict[str, Any]) -> None:
            if not on_progress:
                return
            try:
                on_progress(kind, payload)
            except Exception:
                # Progress callbacks are best-effort UI updates; they must never
                # break the tool-call transcript state machine.
                return

        self._append_message({"role": "user", "content": user_text})
        tool_events: List[ToolEvent] = []
        strict_command = self._match_strict_command(user_text)
        if strict_command is not None:
            return await self._execute_strict_command(
                strict_command,
                user_text=user_text,
                tool_events=tool_events,
                on_progress=on_progress,
                stop_after_tool_calls=stop_after_tool_calls,
            )
        round_num = 0
        repeated_tool_rounds = 0
        previous_tool_signature: tuple[tuple[str, str], ...] | None = None
        repeated_signal_lookup_key: str | None = None
        repeated_signal_lookup_rounds = 0
        expanded_tool_retry_used = False
        force_full_tool_window_next_round = False
        expanded_retry_reason: str | None = None

        while True:
            round_num += 1
            if round_num > self._max_rounds:
                message = (
                    f"Stopped after {self._max_rounds} tool-calling rounds to avoid a loop. "
                    "Please refine the request or run the next step explicitly."
                )
                self._append_message({"role": "assistant", "content": message})
                _emit_progress("complete", {"assistant_text": message, "loop_guard": "max_rounds"})
                return message, tool_events
            _emit_progress("llm_request", {
                "message_count": len(self._messages),
                "round": round_num,
            })
            self._assert_no_unresolved_tool_calls(self._messages)

            # Use streaming so we can emit tokens in real-time
            def _on_token(text: str) -> None:
                _emit_progress("llm_token", {"text": text, "round": round_num})

            use_full_tool_window = force_full_tool_window_next_round
            force_full_tool_window_next_round = False
            tools_for_request = self._select_tools_for_user_text(
                user_text,
                force_all=use_full_tool_window,
            )
            if use_full_tool_window:
                _emit_progress(
                    "llm_tools_retry_expanded",
                    {
                        "reason": expanded_retry_reason or "unknown",
                        "tool_count": len(tools_for_request),
                    },
                )
                expanded_retry_reason = None
            if len(tools_for_request) < len(self._tools):
                selected_names = {self._tool_name(tool) for tool in tools_for_request}
                dropped_names = [
                    self._tool_name(tool)
                    for tool in self._tools
                    if self._tool_name(tool) not in selected_names
                ]
                _emit_progress(
                    "llm_tools_subset",
                    {
                        "selected_count": len(tools_for_request),
                        "total_count": len(self._tools),
                        "dropped_count": len(dropped_names),
                        "dropped_examples": [name for name in dropped_names[:8] if name],
                        "selection_debug": dict(self._last_tool_selection_debug),
                    },
                )
            response = await self._llm.chat_stream(
                self._messages, tools_for_request, on_token=_on_token,
            )

            _emit_progress(
                "llm_response",
                {
                    "has_content": bool(response.content),
                    "content": response.content,
                    "tool_call_count": len(response.tool_calls),
                    "tool_names": [c.name for c in response.tool_calls],
                    "round": round_num,
                },
            )
            if response.tool_calls:
                prepared_calls: list[tuple[ToolCall, Dict[str, Any]]] = []
                for call in response.tool_calls:
                    arguments = normalize_search_tool_arguments(call.name, call.arguments)
                    arguments = normalize_photarium_upload_arguments(
                        call.name,
                        arguments,
                        self._tool_input_schema_by_name.get(call.name),
                        fallback_text=user_text,
                    )
                    arguments = self._normalize_photarium_upload_namespace(
                        call.name,
                        arguments,
                        self._tool_input_schema_by_name.get(call.name),
                    )
                    arguments = normalize_binary_transfer_arguments(
                        call.name,
                        arguments,
                        fallback_text=user_text,
                    )
                    prepared_calls.append((call, arguments))
                tool_signature: tuple[tuple[str, str], ...] = tuple(
                    (call.name, self._safe_json_dumps(arguments, separators=(",", ":")))
                    for call, arguments in prepared_calls
                )
                if tool_signature == previous_tool_signature:
                    repeated_tool_rounds += 1
                else:
                    repeated_tool_rounds = 0
                    previous_tool_signature = tool_signature

                if repeated_tool_rounds >= self._max_repeated_tool_rounds:
                    if not expanded_tool_retry_used and len(self._tools) > self._MAX_TOOLS_PER_LLM_REQUEST:
                        expanded_tool_retry_used = True
                        force_full_tool_window_next_round = True
                        expanded_retry_reason = "repeated_tool_calls"
                        repeated_tool_rounds = 0
                        previous_tool_signature = None
                        continue
                    tool_names = ", ".join(call.name for call in response.tool_calls)
                    message = (
                        "Stopped repeated identical tool-call rounds to avoid a loop. "
                        f"Repeated tool set: {tool_names}."
                    )
                    self._append_message({"role": "assistant", "content": message})
                    _emit_progress(
                        "complete",
                        {"assistant_text": message, "loop_guard": "repeated_tool_calls"},
                    )
                    return message, tool_events

                self._append_message(
                    {
                        "role": "assistant",
                        "content": response.content,
                        "tool_calls": [
                            {
                                "id": call.call_id,
                                "type": "function",
                                "function": {
                                    "name": call.name,
                                    "arguments": self._safe_json_dumps(arguments),
                                },
                            }
                            for call, arguments in prepared_calls
                        ],
                    }
                )
                round_tool_events: list[ToolEvent] = []
                for call, arguments in prepared_calls:
                    event = ToolEvent(name=call.name, arguments=arguments)
                    execute_name = call.name
                    execute_arguments = arguments
                    temp_upload_file: Path | None = None
                    _emit_progress(
                        "tool_call_start",
                        {"name": call.name, "arguments": arguments},
                    )
                    try:
                        execute_name, execute_arguments, temp_upload_file = await self._prepare_tool_execution(
                            call.name,
                            arguments,
                            user_text=user_text,
                        )
                        event.arguments = execute_arguments
                        async with self._tool_execution_lock:
                            raw_result = await self._router.call_tool(execute_name, execute_arguments)
                        result = normalize_search_tool_result(call.name, raw_result)
                        self._record_image_namespaces_from_result(result)
                        auto_upload = await self._maybe_auto_upload_workflow_outputs(
                            execute_name,
                            execute_arguments,
                            result,
                            user_text=user_text,
                        )
                        if auto_upload is not None and isinstance(result, dict):
                            result = dict(result)
                            result["auto_upload"] = auto_upload
                        event.result = result
                        _emit_progress("tool_call_result", {"name": call.name, "result": result})
                    except Exception as exc:  # pragma: no cover
                        event.error = str(exc)
                        result = {"error": event.error}
                        _emit_progress("tool_call_error", {"name": call.name, "error": event.error})
                    finally:
                        if temp_upload_file is not None:
                            try:
                                temp_upload_file.unlink(missing_ok=True)
                            except Exception:
                                pass
                    tool_events.append(event)
                    round_tool_events.append(event)
                    # Sanitize result for LLM context — strip base64 blobs
                    # so they don't blow up the token window.
                    sanitized = sanitize_result(result, tool_name=call.name, save_artifacts=True)
                    sanitized = self._compact_tool_result_for_llm(call.name, sanitized)
                    self._append_message(
                        {
                            "role": "tool",
                            "tool_call_id": call.call_id,
                            "name": call.name,
                            "content": self._safe_json_dumps(sanitized, separators=(",", ":")),
                        }
                    )

                signal_lookup_key = self._signal_lookup_failure_round_key(round_tool_events)
                if signal_lookup_key:
                    if signal_lookup_key == repeated_signal_lookup_key:
                        repeated_signal_lookup_rounds += 1
                    else:
                        repeated_signal_lookup_key = signal_lookup_key
                        repeated_signal_lookup_rounds = 1

                    if repeated_signal_lookup_rounds >= 2:
                        if not expanded_tool_retry_used and len(self._tools) > self._MAX_TOOLS_PER_LLM_REQUEST:
                            expanded_tool_retry_used = True
                            force_full_tool_window_next_round = True
                            expanded_retry_reason = "signal_lookup_failures"
                            repeated_signal_lookup_key = None
                            repeated_signal_lookup_rounds = 0
                            continue
                        message = (
                            "Stopped repeated signal lookup failures for the same identifier. "
                            f"'{signal_lookup_key}' appears to be unresolved as a signal_id. "
                            "If this is a digest id/path, fetch the digest first and then create a signal "
                            "with digester_signals_create."
                        )
                        self._append_message({"role": "assistant", "content": message})
                        _emit_progress(
                            "complete",
                            {"assistant_text": message, "loop_guard": "signal_lookup_failures"},
                        )
                        return message, tool_events
                else:
                    repeated_signal_lookup_key = None
                    repeated_signal_lookup_rounds = 0

                if stop_after_tool_calls:
                    _emit_progress("complete", {"assistant_text": None, "stopped_after_tools": True})
                    return None, tool_events
                continue

            if response.content:
                self._append_message({"role": "assistant", "content": response.content})
                _emit_progress("complete", {"assistant_text": response.content})
                return response.content, tool_events

            if (
                not expanded_tool_retry_used
                and len(tools_for_request) < len(self._tools)
                and len(self._tools) > self._MAX_TOOLS_PER_LLM_REQUEST
            ):
                expanded_tool_retry_used = True
                force_full_tool_window_next_round = True
                expanded_retry_reason = "empty_response_after_trimmed_window"
                continue

            _emit_progress("complete", {"assistant_text": None})
            return None, tool_events

    @classmethod
    def _match_strict_command(cls, user_text: str) -> StrictCommand | None:
        match = cls._STRICT_PREVIEW_AD_RE.match(user_text or "")
        if not match:
            return None
        raw_target = (match.group(1) or "").strip()
        if not raw_target:
            return None
        normalized = raw_target
        if normalized.startswith("#"):
            normalized = normalized[1:].strip()
        if normalized.isdigit():
            return StrictCommand(kind="preview_ad", target=normalized, target_is_index=True)
        return StrictCommand(kind="preview_ad", target=normalized, target_is_index=False)

    async def _execute_strict_command(
        self,
        command: StrictCommand,
        *,
        user_text: str,
        tool_events: List[ToolEvent],
        on_progress: Callable[[str, Dict[str, Any]], None] | None,
        stop_after_tool_calls: bool,
    ) -> tuple[str | None, List[ToolEvent]]:
        if command.kind == "preview_ad":
            return await self._execute_strict_preview_ad(
                command,
                user_text=user_text,
                tool_events=tool_events,
                on_progress=on_progress,
                stop_after_tool_calls=stop_after_tool_calls,
            )
        message = f"Strict command not implemented: {command.kind}"
        self._append_message({"role": "assistant", "content": message})
        if on_progress:
            on_progress("complete", {"assistant_text": message, "strict_command": command.kind})
        return message, tool_events

    async def _execute_strict_preview_ad(
        self,
        command: StrictCommand,
        *,
        user_text: str,
        tool_events: List[ToolEvent],
        on_progress: Callable[[str, Dict[str, Any]], None] | None,
        stop_after_tool_calls: bool,
    ) -> tuple[str | None, List[ToolEvent]]:
        del user_text
        if command.target_is_index:
            preview_tool = "editorial_ads_preview_by_index"
            if preview_tool not in self._tool_input_schema_by_name:
                message = "Strict preview command unavailable: editorial_ads_preview_by_index is not exposed."
                self._append_message({"role": "assistant", "content": message})
                if on_progress:
                    on_progress("complete", {"assistant_text": message, "strict_command": "preview_ad"})
                return message, tool_events

            preview_result = await self._run_strict_tool_call(
                preview_tool,
                {"index": int(command.target)},
                tool_events=tool_events,
                on_progress=on_progress,
            )
            preview_url = self._extract_preview_url(preview_result)
            if preview_url:
                if on_progress:
                    on_progress("complete", {"assistant_text": None, "strict_command": "preview_ad"})
                return (None if stop_after_tool_calls else preview_url), tool_events

            inventory_tool = "editorial_ads_list_inventory"
            if inventory_tool in self._tool_input_schema_by_name:
                inventory_result = await self._run_strict_tool_call(
                    inventory_tool,
                    {"limit": int(command.target)},
                    tool_events=tool_events,
                    on_progress=on_progress,
                )
                if self._inventory_has_index(inventory_result, int(command.target)):
                    message = (
                        "Strict preview command failed: preview tool returned no preview URL "
                        f"for valid inventory index {command.target}."
                    )
                else:
                    message = f"Invalid ad index: {command.target}."
                self._append_message({"role": "assistant", "content": message})
                if on_progress:
                    on_progress("complete", {"assistant_text": message, "strict_command": "preview_ad"})
                return message, tool_events

            message = (
                "Strict preview command failed: preview tool returned no preview URL and "
                "inventory verification tool is unavailable."
            )
            self._append_message({"role": "assistant", "content": message})
            if on_progress:
                on_progress("complete", {"assistant_text": message, "strict_command": "preview_ad"})
            return message, tool_events

        preview_tool = "editorial_ads_preview"
        if preview_tool not in self._tool_input_schema_by_name:
            message = "Strict preview command unavailable: editorial_ads_preview is not exposed."
            self._append_message({"role": "assistant", "content": message})
            if on_progress:
                on_progress("complete", {"assistant_text": message, "strict_command": "preview_ad"})
            return message, tool_events
        preview_result = await self._run_strict_tool_call(
            preview_tool,
            {"adId": command.target},
            tool_events=tool_events,
            on_progress=on_progress,
        )
        preview_url = self._extract_preview_url(preview_result)
        if preview_url:
            if on_progress:
                on_progress("complete", {"assistant_text": None, "strict_command": "preview_ad"})
            return (None if stop_after_tool_calls else preview_url), tool_events

        verify_tool = "editorial_ads_get_json"
        if verify_tool in self._tool_input_schema_by_name:
            verify_result = await self._run_strict_tool_call(
                verify_tool,
                {"adId": command.target},
                tool_events=tool_events,
                on_progress=on_progress,
            )
            if isinstance(verify_result, dict) and verify_result:
                message = (
                    "Strict preview command failed: preview tool returned no preview URL "
                    f"for valid ad id {command.target}."
                )
            else:
                message = f"Unknown ad id: {command.target}."
            self._append_message({"role": "assistant", "content": message})
            if on_progress:
                on_progress("complete", {"assistant_text": message, "strict_command": "preview_ad"})
            return message, tool_events

        message = (
            "Strict preview command failed: preview tool returned no preview URL and "
            "ad verification tool is unavailable."
        )
        self._append_message({"role": "assistant", "content": message})
        if on_progress:
            on_progress("complete", {"assistant_text": message, "strict_command": "preview_ad"})
        return message, tool_events

    async def _run_strict_tool_call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        tool_events: List[ToolEvent],
        on_progress: Callable[[str, Dict[str, Any]], None] | None,
    ) -> Any:
        call_id = f"strict_{tool_name}_{len(tool_events) + 1}"
        self._append_message(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": self._safe_json_dumps(arguments),
                        },
                    }
                ],
            }
        )
        if on_progress:
            on_progress("tool_call_start", {"name": tool_name, "arguments": arguments})
        event = ToolEvent(name=tool_name, arguments=dict(arguments))
        try:
            async with self._tool_execution_lock:
                raw_result = await self._router.call_tool(tool_name, arguments)
            result = normalize_search_tool_result(tool_name, raw_result)
            self._record_image_namespaces_from_result(result)
            event.result = result
            if on_progress:
                on_progress("tool_call_result", {"name": tool_name, "result": result})
        except Exception as exc:  # pragma: no cover
            event.error = str(exc)
            result = {"error": event.error}
            if on_progress:
                on_progress("tool_call_error", {"name": tool_name, "error": event.error})
        tool_events.append(event)
        sanitized = sanitize_result(result, tool_name=tool_name, save_artifacts=True)
        sanitized = self._compact_tool_result_for_llm(tool_name, sanitized)
        self._append_message(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": tool_name,
                "content": self._safe_json_dumps(sanitized, separators=(",", ":")),
            }
        )
        return result

    @staticmethod
    def _extract_preview_url(result: Any) -> str | None:
        if not isinstance(result, dict):
            return None
        for key in ("previewUrl", "preview_url"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        preview_urls = result.get("previewUrls") or result.get("preview_urls")
        if isinstance(preview_urls, dict):
            for candidate in ("catalog", "single", "default"):
                value = preview_urls.get(candidate)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            for value in preview_urls.values():
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    @staticmethod
    def _inventory_has_index(result: Any, index: int) -> bool:
        if not isinstance(result, dict):
            return False
        items = result.get("items")
        if isinstance(items, list):
            return len(items) >= index
        ads = result.get("ads")
        if isinstance(ads, list):
            return len(ads) >= index
        return False

    def _select_tools_for_user_text(self, user_text: str, *, force_all: bool = False) -> List[Dict[str, Any]]:
        if force_all:
            self._last_tool_selection_debug = {
                "query_token_count": 0,
                "lexical_match_count": len(self._tools),
                "index_candidate_count": len(self._tools),
                "fallback_added_count": 0,
            }
            return list(self._tools)
        selector = ToolSelector(self._tools)
        selected = selector.select_tools_for_user_text(
            user_text=user_text,
            recent_tool_names=self._recent_tool_names(),
            max_tools_per_request=self._MAX_TOOLS_PER_LLM_REQUEST,
            context_text=self._recent_context_text(),
        )
        self._last_tool_selection_debug = dict(selector.last_selection_debug)
        return selected

    def _recent_tool_names(self) -> set[str]:
        names: set[str] = set()
        for message in self._messages[-40:]:
            if message.get("role") != "tool":
                continue
            name = message.get("name")
            if isinstance(name, str) and name:
                names.add(name)
        return names

    def _recent_context_text(self, *, max_messages: int = 10, max_chars: int = 8_000) -> str:
        snippets: list[str] = []
        for message in reversed(self._messages):
            if len(snippets) >= max_messages:
                break
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                snippets.append(content.strip())
        if not snippets:
            return ""
        snippets.reverse()
        merged = "\n".join(snippets)
        if len(merged) > max_chars:
            return merged[-max_chars:]
        return merged

    @classmethod
    def _signal_lookup_failure_round_key(cls, events: List[ToolEvent]) -> str | None:
        if not events:
            return None
        expected_tools = {"digester_get_signal", "editorial_signals_get", "editorial_signals_get_text"}
        if any(event.name not in expected_tools for event in events):
            return None
        missing_keys: set[str] = set()
        for event in events:
            signal_id = cls._extract_missing_signal_id(event.error)
            if not signal_id:
                return None
            normalized = signal_id.strip()
            if normalized.endswith(".json"):
                normalized = normalized[:-5]
            missing_keys.add(normalized)
        if len(missing_keys) != 1:
            return None
        return next(iter(missing_keys))

    @staticmethod
    def _extract_missing_signal_id(error_text: str | None) -> str | None:
        if not error_text:
            return None
        match = re.search(r"Signal not found:\s*([^\n]+)", error_text)
        if not match:
            return None
        return match.group(1).strip()

    @staticmethod
    def _tool_name(tool: Dict[str, Any]) -> str:
        return ToolSelector.tool_name(tool)

    @staticmethod
    def _tool_priority(name: str, text: str, recent_tool_names: set[str]) -> int:
        return ToolSelector.tool_priority(name=name, text=text, recent_tool_names=recent_tool_names)

    def _append_message(self, message: Dict[str, Any]) -> None:
        self._messages.append(message)
        self._apply_message_limits()

    def _apply_message_limits(self) -> None:
        for message in self._messages:
            content = message.get("content")
            if not isinstance(content, str):
                continue
            is_tool = message.get("role") == "tool"
            limit = self._max_tool_content_chars if is_tool else self._max_message_content_chars
            if len(content) > limit:
                message["content"] = f"{content[:limit]}\n...[truncated]"

        while True:
            changed = False

            while len(self._messages) - 1 > self._max_non_system_messages:
                del self._messages[1]
                changed = True

            while len(self._messages) > 1 and self._total_content_chars() > self._max_total_content_chars:
                del self._messages[1]
                changed = True

            repaired = self._sanitize_history_sequences(self._messages)
            if repaired != self._messages:
                self._messages = repaired
                changed = True

            if not changed:
                break

    @staticmethod
    def _safe_json_dumps(payload: Any, separators: tuple[str, str] | None = None) -> str:
        try:
            if separators is not None:
                return json.dumps(payload, separators=separators, default=str)
            return json.dumps(payload, default=str)
        except Exception:
            fallback = {"_serialization_error": str(payload)}
            if separators is not None:
                return json.dumps(fallback, separators=separators, default=str)
            return json.dumps(fallback, default=str)

    @staticmethod
    def _extract_tool_call_ids(message: Dict[str, Any]) -> list[str]:
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            return []
        call_ids: list[str] = []
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            call_id = call.get("id")
            if isinstance(call_id, str) and call_id:
                call_ids.append(call_id)
        return call_ids

    @classmethod
    def _sanitize_history_sequences(cls, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not messages:
            return []
        repaired: List[Dict[str, Any]] = [messages[0]]
        index = 1

        while index < len(messages):
            message = messages[index]
            role = message.get("role")

            if role == "assistant" and cls._extract_tool_call_ids(message):
                expected_ids = cls._extract_tool_call_ids(message)
                expected_set = set(expected_ids)
                index += 1

                grouped_tool_messages: Dict[str, Dict[str, Any]] = {}
                while index < len(messages) and messages[index].get("role") == "tool":
                    tool_message = messages[index]
                    tool_call_id = tool_message.get("tool_call_id")
                    if isinstance(tool_call_id, str) and tool_call_id and tool_call_id not in grouped_tool_messages:
                        grouped_tool_messages[tool_call_id] = tool_message
                    index += 1
                is_open_tail = index >= len(messages)

                if is_open_tail:
                    # Keep in-progress assistant/tool call blocks while a response is
                    # still being assembled in the current round.
                    repaired.append(message)
                    for call_id in expected_ids:
                        if call_id not in grouped_tool_messages:
                            continue
                        repaired.append(grouped_tool_messages[call_id])
                    continue

                if all(call_id in grouped_tool_messages for call_id in expected_set):
                    repaired.append(message)
                    for call_id in expected_ids:
                        repaired.append(grouped_tool_messages[call_id])
                continue

            if role == "tool":
                # Drop orphan tool messages that no longer have a paired assistant/tool_calls message.
                index += 1
                continue

            repaired.append(message)
            index += 1

        return repaired

    @classmethod
    def _assert_no_unresolved_tool_calls(cls, messages: List[Dict[str, Any]]) -> None:
        expected_order: list[str] = []
        resolved: set[str] = set()
        for message in messages:
            role = message.get("role")
            if role == "assistant":
                expected_order.extend(cls._extract_tool_call_ids(message))
                continue
            if role != "tool":
                continue
            tool_call_id = message.get("tool_call_id")
            if isinstance(tool_call_id, str) and tool_call_id:
                resolved.add(tool_call_id)
        unresolved = [call_id for call_id in expected_order if call_id not in resolved]
        if unresolved:
            raise RuntimeError(f"Unresolved tool_call_ids in transcript: {', '.join(unresolved)}")

    def _total_content_chars(self) -> int:
        return sum(len(content) for content in (m.get("content") for m in self._messages) if isinstance(content, str))

    async def _prepare_tool_execution(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> tuple[str, Dict[str, Any], Path | None]:
        return await self._tool_argument_preflight.prepare_tool_execution(
            self,
            tool_name,
            arguments,
            user_text=user_text,
        )

    async def _preflight_workflows_run_arguments(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> None:
        await self._workflow_tool_repairs.preflight_workflows_run_arguments(
            self,
            tool_name,
            arguments,
            user_text=user_text,
        )

    @staticmethod
    def _repair_workflows_run_arguments(arguments: Dict[str, Any]) -> Dict[str, Any]:
        return WorkflowToolRepairService.repair_workflows_run_arguments(arguments)

    async def _repair_stitching_workflows_run_arguments(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> Dict[str, Any]:
        return await self._workflow_tool_repairs.repair_stitching_workflows_run_arguments(
            self,
            tool_name,
            arguments,
            user_text=user_text,
        )

    async def _repair_variation_workflows_run_arguments(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> Dict[str, Any]:
        return await self._workflow_tool_repairs.repair_variation_workflows_run_arguments(
            self,
            tool_name,
            arguments,
            user_text=user_text,
        )

    async def _repair_tanktracks_workflows_run_arguments(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> Dict[str, Any]:
        return await self._workflow_tool_repairs.repair_tanktracks_workflows_run_arguments(
            self,
            tool_name,
            arguments,
            user_text=user_text,
        )

    @staticmethod
    def _extract_flow_source_image_id(user_text: str) -> str | None:
        return WorkflowToolRepairService.extract_flow_source_image_id(user_text)

    @staticmethod
    def _extract_flow_workflow_id(user_text: str) -> str | None:
        return WorkflowToolRepairService.extract_flow_workflow_id(user_text)

    async def _download_variation_source_image(self, source_image_id: str) -> str | None:
        return await self._workflow_tool_repairs.download_variation_source_image(self, source_image_id)

    def _select_variation_download_tool(self) -> str | None:
        return WorkflowToolRepairService.select_variation_download_tool(self)

    @staticmethod
    def _select_download_image_id_key(schema: Dict[str, Any] | None) -> str | None:
        return WorkflowToolRepairService.select_download_image_id_key(schema)

    @staticmethod
    def _select_download_save_path_key(schema: Dict[str, Any] | None) -> str | None:
        return WorkflowToolRepairService.select_download_save_path_key(schema)

    @staticmethod
    def _select_download_include_base64_key(schema: Dict[str, Any] | None) -> str | None:
        return WorkflowToolRepairService.select_download_include_base64_key(schema)

    @staticmethod
    def _resolve_downloaded_file_path(download_result: Any, *, requested_path: Path | None) -> str | None:
        return WorkflowToolRepairService.resolve_downloaded_file_path(download_result, requested_path=requested_path)

    @staticmethod
    def _is_image_variation_workflow(workflow_id: str) -> bool:
        return WorkflowToolRepairService.is_image_variation_workflow(workflow_id)

    @staticmethod
    def _is_stitching_workflow(workflow_id: str) -> bool:
        return WorkflowToolRepairService.is_stitching_workflow(workflow_id)

    @staticmethod
    def _stitch_workflow_requires_second_image(workflow_id: str) -> bool:
        return WorkflowToolRepairService.stitch_workflow_requires_second_image(workflow_id)

    @staticmethod
    def _extract_stitch_source_image_ids(user_text: str) -> list[str]:
        return WorkflowToolRepairService.extract_stitch_source_image_ids(user_text)

    @staticmethod
    def _extract_variation_source_image_id(user_text: str) -> str | None:
        return WorkflowToolRepairService.extract_variation_source_image_id(user_text)

    @classmethod
    def _repair_workflows_import_from_artifact_arguments(
        cls,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> Dict[str, Any]:
        return WorkflowToolRepairService.repair_workflows_import_from_artifact_arguments(
            tool_name,
            arguments,
            user_text=user_text,
        )

    @staticmethod
    def _preflight_workflows_import_from_artifact_arguments(tool_name: str, arguments: Dict[str, Any]) -> None:
        WorkflowToolRepairService.preflight_workflows_import_from_artifact_arguments(tool_name, arguments)

    @staticmethod
    def _extract_local_artifact_path_from_text(user_text: str) -> str | None:
        return WorkflowToolRepairService.extract_local_artifact_path_from_text(user_text)

    @staticmethod
    def _extract_requested_workflow_name_from_text(user_text: str) -> str | None:
        return WorkflowToolRepairService.extract_requested_workflow_name_from_text(user_text)

    @staticmethod
    def _extract_shorthand_edit_image_id(user_text: str) -> str | None:
        return WorkflowToolRepairService.extract_shorthand_edit_image_id(user_text)

    async def _verify_photarium_image_id_candidate(self, image_id: str) -> bool:
        return await self._workflow_tool_repairs.verify_photarium_image_id_candidate(self, image_id)

    async def _maybe_convert_upload_url_to_from_path(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> tuple[str, Dict[str, Any], Path] | None:
        return await self._binary_transfer_adapter.maybe_convert_upload_url_to_from_path(
            self,
            tool_name,
            arguments,
            user_text=user_text,
        )

    def _apply_tanktracks_upload_conventions(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> Dict[str, Any]:
        return self._binary_transfer_adapter.apply_tanktracks_upload_conventions(
            tool_name,
            arguments,
            user_text=user_text,
        )

    @staticmethod
    def _ensure_upload_tags(arguments: Dict[str, Any], required_tags: list[str]) -> Dict[str, Any]:
        return BinaryTransferAdapter.ensure_upload_tags(arguments, required_tags)

    @staticmethod
    def _drop_generic_tanktracks_display_name(arguments: Dict[str, Any]) -> None:
        BinaryTransferAdapter.drop_generic_tanktracks_display_name(arguments)

    def _paired_upload_from_path_tool(self, upload_url_tool: str) -> str | None:
        return BinaryTransferAdapter.paired_upload_from_path_tool(self, upload_url_tool)

    @staticmethod
    def _extract_upload_url_value(arguments: Dict[str, Any]) -> str | None:
        return BinaryTransferAdapter.extract_upload_url_value(arguments)

    @staticmethod
    def _select_upload_path_key(schema: Dict[str, Any] | None) -> str | None:
        return BinaryTransferAdapter.select_upload_path_key(schema)

    async def _download_url_to_temp_file(self, url: str, label: str) -> Path | None:
        return await self._binary_transfer_adapter.download_url_to_temp_file(url, label)

    @staticmethod
    def _guess_extension_from_url(url: str) -> str | None:
        return BinaryTransferAdapter.guess_extension_from_url(url)

    @staticmethod
    def _safe_filename_stem(value: str) -> str:
        return BinaryTransferAdapter.safe_filename_stem(value)

    def _normalize_photarium_upload_namespace(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        input_schema: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        return self._binary_transfer_adapter.normalize_photarium_upload_namespace(
            self,
            tool_name,
            arguments,
            input_schema,
        )

    @staticmethod
    def _schema_supports_namespace(input_schema: Dict[str, Any] | None) -> bool:
        return BinaryTransferAdapter.schema_supports_namespace(input_schema)

    def _infer_namespace_from_upload_arguments(self, arguments: Dict[str, Any]) -> str | None:
        return BinaryTransferAdapter.infer_namespace_from_upload_arguments(self, arguments)

    def _record_image_namespaces_from_result(self, payload: Any) -> None:
        self._image_namespaces.record_from_result(payload)

    def _record_source_image_local_path(self, local_path: str, source_image_id: str) -> None:
        path_text = str(local_path or "").strip()
        source_id = str(source_image_id or "").strip()
        if not path_text or not source_id:
            return
        try:
            resolved = str(Path(path_text).expanduser().resolve())
        except Exception:
            resolved = path_text
        self._source_image_id_by_local_path[resolved] = source_id
        self._source_image_id_by_local_path[path_text] = source_id

    def _source_image_id_for_local_path(self, local_path: str) -> str | None:
        path_text = str(local_path or "").strip()
        if not path_text:
            return None
        try:
            resolved = str(Path(path_text).expanduser().resolve())
        except Exception:
            resolved = path_text
        return self._source_image_id_by_local_path.get(resolved) or self._source_image_id_by_local_path.get(path_text)

    def _candidate_image_ids(self, payload: Dict[str, Any]) -> list[str]:
        return ImageNamespaceRegistry.candidate_image_ids(payload)

    @staticmethod
    def _extract_image_id(payload: Dict[str, Any]) -> str | None:
        return ImageNamespaceRegistry.extract_image_id(payload)

    async def _maybe_auto_upload_workflow_outputs(
        self,
        tool_name: str,
        tool_arguments: Dict[str, Any],
        tool_result: Any,
        *,
        user_text: str,
    ) -> Dict[str, Any] | None:
        return await self._binary_transfer_adapter.maybe_auto_upload_workflow_outputs(
            self,
            tool_name,
            tool_arguments,
            tool_result,
            user_text=user_text,
        )

    def _select_auto_upload_tool_name(self) -> str | None:
        return BinaryTransferAdapter.select_auto_upload_tool_name(self)

    @staticmethod
    def _select_upload_local_path_key(schema: Dict[str, Any] | None) -> str | None:
        return BinaryTransferAdapter.select_upload_local_path_key(schema)

    async def _resolve_output_image_local_path(self, image_payload: Dict[str, Any]) -> tuple[str | None, str | None]:
        return await self._binary_transfer_adapter.resolve_output_image_local_path(self, image_payload)

    @staticmethod
    def _schema_has_property(schema: Dict[str, Any] | None, key: str) -> bool:
        if not isinstance(schema, dict):
            return False
        properties = schema.get("properties")
        return isinstance(properties, dict) and key in properties

    async def _build_auto_upload_arguments(
        self,
        *,
        upload_tool_name: str,
        upload_schema: Dict[str, Any] | None,
        upload_path_key: str,
        local_path: str,
        image_payload: Dict[str, Any],
        tool_arguments: Dict[str, Any],
        user_text: str,
    ) -> Dict[str, Any]:
        return await self._binary_transfer_adapter.build_auto_upload_arguments(
            self,
            upload_tool_name=upload_tool_name,
            upload_schema=upload_schema,
            upload_path_key=upload_path_key,
            local_path=local_path,
            image_payload=image_payload,
            tool_arguments=tool_arguments,
            user_text=user_text,
        )

    async def _vision_semantic_label_from_image(self, image_path: Path) -> str | None:
        return await self._binary_transfer_adapter.vision_semantic_label_from_image(image_path)

    def _vision_semantic_label_from_image_sync(self, image_path: Path, api_key: str) -> str | None:
        return self._binary_transfer_adapter.vision_semantic_label_from_image_sync(image_path, api_key)
