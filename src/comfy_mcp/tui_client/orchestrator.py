"""Tool-calling loop orchestration."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
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
        self._last_tool_selection_debug: Dict[str, Any] = {}

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

    def last_tool_selection_debug(self) -> Dict[str, Any]:
        """Return the most recent selector diagnostics for UI/status commands."""
        return dict(self._last_tool_selection_debug)

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
        domain_guardrail_retry_used = False
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

                guardrail_payload = self._domain_guardrail_retry_payload(
                    prepared_calls=prepared_calls,
                    user_text=user_text,
                )
                if guardrail_payload and not domain_guardrail_retry_used:
                    domain_guardrail_retry_used = True
                    repeated_tool_rounds = 0
                    previous_tool_signature = None
                    self._append_message(
                        {
                            "role": "system",
                            "content": self._domain_guardrail_corrective_note(guardrail_payload),
                        }
                    )
                    _emit_progress("llm_tools_retry_domain_guardrail", guardrail_payload)
                    continue

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
                        self._workflow_tool_repairs.maybe_record_downloaded_source_image(
                            self,
                            execute_name,
                            execute_arguments,
                            result,
                        )
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
        normalized_text = str(user_text or "")
        if cls._should_use_strict_variation_flow(normalized_text):
            return StrictCommand(kind="variation_flow", target=user_text)
        if (
            "TANK TRACKS FLOW REQUEST" in normalized_text.upper()
            and "Run this as a deterministic, minimal-branch flow inside the TUI." in normalized_text
        ):
            return StrictCommand(kind="tanktracks_flow", target=user_text)
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
        if command.kind == "variation_flow":
            return await self._execute_strict_variation_flow(
                command,
                user_text=user_text,
                tool_events=tool_events,
                on_progress=on_progress,
                stop_after_tool_calls=stop_after_tool_calls,
            )
        if command.kind == "tanktracks_flow":
            return await self._execute_strict_tanktracks_flow(
                command,
                user_text=user_text,
                tool_events=tool_events,
                on_progress=on_progress,
                stop_after_tool_calls=stop_after_tool_calls,
            )
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

    async def _execute_strict_variation_flow(
        self,
        command: StrictCommand,
        *,
        user_text: str,
        tool_events: List[ToolEvent],
        on_progress: Callable[[str, Dict[str, Any]], None] | None,
        stop_after_tool_calls: bool,
    ) -> tuple[str | None, List[ToolEvent]]:
        del user_text
        request_text = command.target or ""
        source_image_id = self._extract_variation_source_image_id(request_text)
        if not source_image_id:
            message = "Variation flow failed: missing source image ID."
            self._append_message({"role": "assistant", "content": message})
            if on_progress:
                on_progress("complete", {"assistant_text": message, "strict_command": "variation_flow"})
            return message, tool_events

        workflow_id = self._extract_variation_workflow_id(request_text) or "image_variation_maker"
        run_count = self._extract_variation_run_count(request_text) or 1
        sweep_target = self._extract_variation_sweep_target(request_text)
        if sweep_target is None and run_count > 1:
            sweep_target = "seed"
        if sweep_target == "prompt":
            message = "Variation flow failed: strict prompt sweeps are not supported."
            self._append_message({"role": "assistant", "content": message})
            if on_progress:
                on_progress("complete", {"assistant_text": message, "strict_command": "variation_flow"})
            return message, tool_events

        source_metadata = await self._fetch_source_image_metadata_strict(
            source_image_id,
            tool_events=tool_events,
            on_progress=on_progress,
        )
        effective_parent_id = source_image_id
        source_namespace = ""
        if isinstance(source_metadata, dict):
            self._record_image_namespaces_from_result(source_metadata)
            effective_parent_id = self._binary_transfer_adapter.resolve_effective_parent_id(
                source_metadata,
                fallback_source_image_id=source_image_id,
            )
            source_namespace = (
                self._binary_transfer_adapter._first_non_empty_string(source_metadata, "namespace") or ""
            )

        local_source_path = await self._download_source_image_strict(
            source_image_id,
            tool_events=tool_events,
            on_progress=on_progress,
        )
        if not local_source_path:
            message = f"Variation flow failed: could not download source image {source_image_id}."
            self._append_message({"role": "assistant", "content": message})
            if on_progress:
                on_progress("complete", {"assistant_text": message, "strict_command": "variation_flow"})
            return message, tool_events
        self._record_source_image_local_path(local_source_path, source_image_id)

        param_names = await self._fetch_workflow_param_names_strict(
            workflow_id,
            tool_events=tool_events,
            on_progress=on_progress,
        )
        if not param_names and self._is_image_variation_workflow(workflow_id):
            param_names = ["seed", "filename_prefix", "image", "string"]

        image_key = self._select_variation_image_key(param_names) or "image"
        prompt_key = self._select_variation_prompt_key(param_names)
        seed_key = self._workflow_tool_repairs.select_seed_override_key(param_names)
        filename_prefix_key = self._workflow_tool_repairs.select_filename_prefix_override_key(param_names) or "filename_prefix"
        sweep_key = self._select_variation_sweep_key(sweep_target, param_names) if sweep_target else None

        analysis_prompt = self._extract_variation_analysis_prompt(request_text)
        extra_instructions = self._extract_variation_extra_instructions(request_text)
        base_prompt = self._build_variation_base_prompt(
            analysis_prompt=analysis_prompt,
            extra_instructions=extra_instructions,
        )

        numeric_sweep_values = self._extract_variation_sweep_values(request_text)
        seed_values = self._extract_variation_seed_values(request_text)
        if sweep_target == "seed":
            desired_runs = min(max(1, run_count), 24)
            actual_seed_values = self._normalize_variation_seed_values(seed_values, desired_runs)
            run_count = len(actual_seed_values)
        else:
            actual_seed_values = []
            if sweep_target and numeric_sweep_values:
                if run_count > 0:
                    numeric_sweep_values = numeric_sweep_values[:run_count]
                run_count = len(numeric_sweep_values)
            else:
                run_count = max(1, min(24, run_count))

        fixed_seed: int | None = None
        if seed_key and sweep_target != "seed":
            fixed_seed = self._workflow_tool_repairs.generate_random_seed()

        upload_tool = self._select_auto_upload_tool_name()
        upload_schema = self._tool_input_schema_by_name.get(upload_tool) if upload_tool else None
        upload_path_key = self._select_upload_local_path_key(upload_schema) if upload_tool else None
        parent_key = self._binary_transfer_adapter.select_parent_id_key(upload_schema)

        run_records: list[Dict[str, Any]] = []
        successful_runs = 0

        for run_index in range(run_count):
            overrides: Dict[str, Any] = {
                image_key: local_source_path,
                filename_prefix_key: f"{self._workflow_tool_repairs.default_filename_prefix(workflow_id)}_{run_index + 1:02d}",
            }
            if prompt_key and base_prompt:
                overrides[prompt_key] = base_prompt
            if seed_key and fixed_seed is not None:
                overrides[seed_key] = fixed_seed

            sweep_value: Any = None
            if sweep_target == "seed" and seed_key:
                sweep_value = actual_seed_values[run_index]
                overrides[seed_key] = sweep_value
            elif sweep_target and sweep_key and numeric_sweep_values:
                raw_value = numeric_sweep_values[run_index]
                sweep_value = self._coerce_variation_sweep_value(sweep_target, raw_value)
                overrides[sweep_key] = sweep_value

            workflow_arguments: Dict[str, Any] = {
                "workflow_id": workflow_id,
                "overrides": overrides,
                "wait_timeout_s": 300,
                "wait_poll_ms": 1000,
            }
            workflow_result = await self._run_prepared_strict_tool_call(
                "workflows_run",
                workflow_arguments,
                user_text=request_text,
                tool_events=tool_events,
                on_progress=on_progress,
            )

            output_images = workflow_result.get("output_images") if isinstance(workflow_result, dict) else None
            if (not isinstance(output_images, list) or not output_images) and isinstance(workflow_result, dict):
                watched = await self._watch_workflow_outputs_if_needed(
                    workflow_result,
                    tool_events=tool_events,
                    on_progress=on_progress,
                )
                if isinstance(watched, dict):
                    workflow_result = watched
                    output_images = workflow_result.get("output_images")
            if (
                isinstance(workflow_result, dict)
                and (not isinstance(output_images, list) or not output_images)
                and self._should_retry_workflow_without_outputs(workflow_result)
            ):
                retry_arguments = dict(workflow_arguments)
                retry_arguments["force"] = True
                retry_result = await self._run_prepared_strict_tool_call(
                    "workflows_run",
                    retry_arguments,
                    user_text=request_text,
                    tool_events=tool_events,
                    on_progress=on_progress,
                )
                retry_output_images = retry_result.get("output_images") if isinstance(retry_result, dict) else None
                if (not isinstance(retry_output_images, list) or not retry_output_images) and isinstance(retry_result, dict):
                    watched_retry = await self._watch_workflow_outputs_if_needed(
                        retry_result,
                        tool_events=tool_events,
                        on_progress=on_progress,
                    )
                    if isinstance(watched_retry, dict):
                        retry_result = watched_retry
                        retry_output_images = retry_result.get("output_images")
                workflow_result = retry_result
                output_images = retry_output_images

            record: Dict[str, Any] = {
                "run_index": run_index + 1,
                "sweep_target": sweep_target or "seed",
                "sweep_value": sweep_value,
                "uploaded_image_ids": [],
            }
            if not isinstance(output_images, list) or not output_images:
                record["status"] = "failed"
                run_records.append(record)
                continue

            successful_runs += 1
            record["status"] = "generated"

            if upload_tool and upload_schema and upload_path_key:
                for image_payload in output_images:
                    if not isinstance(image_payload, dict):
                        continue
                    local_output_path, _path_source = await self._resolve_output_image_local_path(image_payload)
                    if not local_output_path:
                        continue
                    upload_arguments: Dict[str, Any] = {upload_path_key: local_output_path}
                    filename = str(image_payload.get("filename") or "").strip()
                    stem = self._safe_filename_stem(Path(filename).stem if filename else Path(local_output_path).stem)
                    if self._schema_has_property(upload_schema, "name"):
                        upload_arguments["name"] = stem
                    if self._schema_has_property(upload_schema, "title"):
                        upload_arguments["title"] = stem
                    if self._schema_has_property(upload_schema, "namespace"):
                        upload_arguments["namespace"] = source_namespace or "cf-default"
                    if parent_key and effective_parent_id:
                        upload_arguments[parent_key] = effective_parent_id
                    prompt_value = None
                    if prompt_key:
                        prompt_value = overrides.get(prompt_key)
                    if not isinstance(prompt_value, str) or not prompt_value.strip():
                        prompt_value = base_prompt
                    if isinstance(prompt_value, str) and prompt_value.strip():
                        if self._schema_has_property(upload_schema, "prompt"):
                            upload_arguments["prompt"] = prompt_value.strip()
                        if self._schema_has_property(upload_schema, "positive_prompt"):
                            upload_arguments["positive_prompt"] = prompt_value.strip()
                    upload_arguments = normalize_photarium_upload_arguments(
                        upload_tool,
                        upload_arguments,
                        upload_schema,
                        fallback_text=request_text,
                    )
                    upload_result = await self._run_strict_tool_call(
                        upload_tool,
                        upload_arguments,
                        tool_events=tool_events,
                        on_progress=on_progress,
                    )
                    uploaded_image_id = self._extract_image_id(upload_result) if isinstance(upload_result, dict) else None
                    if uploaded_image_id:
                        record["uploaded_image_ids"].append(uploaded_image_id)
                if record["uploaded_image_ids"]:
                    record["status"] = "uploaded"
            run_records.append(record)

        if successful_runs == 0:
            message = "Variation flow failed: no runs produced output_images."
            self._append_message({"role": "assistant", "content": message})
            if on_progress:
                on_progress("complete", {"assistant_text": message, "strict_command": "variation_flow"})
            return message, tool_events

        mapping_parts: list[str] = []
        for record in run_records:
            if record.get("status") == "failed":
                mapping_parts.append(f"run {record['run_index']}: failed")
                continue
            value = record.get("sweep_value")
            value_text = str(value) if value is not None else "default"
            uploaded_ids = record.get("uploaded_image_ids") or []
            if uploaded_ids:
                mapping_parts.append(f"run {record['run_index']} ({value_text}) -> {', '.join(uploaded_ids)}")
            else:
                mapping_parts.append(f"run {record['run_index']} ({value_text}) -> generated")
        message = (
            f"Variation flow completed: {successful_runs}/{len(run_records)} runs produced outputs. "
            f"Effective parent ID: {effective_parent_id}. "
            + " | ".join(mapping_parts)
        )
        self._append_message({"role": "assistant", "content": message})
        if on_progress:
            on_progress("complete", {"assistant_text": None if stop_after_tool_calls else message, "strict_command": "variation_flow"})
        return (None if stop_after_tool_calls else message), tool_events

    async def _execute_strict_tanktracks_flow(
        self,
        command: StrictCommand,
        *,
        user_text: str,
        tool_events: List[ToolEvent],
        on_progress: Callable[[str, Dict[str, Any]], None] | None,
        stop_after_tool_calls: bool,
    ) -> tuple[str | None, List[ToolEvent]]:
        del user_text
        request_text = command.target or ""
        source_image_id = self._extract_flow_source_image_id(request_text)
        if not source_image_id:
            message = "Tanktracks flow failed: missing source image ID."
            self._append_message({"role": "assistant", "content": message})
            if on_progress:
                on_progress("complete", {"assistant_text": message, "strict_command": "tanktracks_flow"})
            return message, tool_events

        workflow_id = self._extract_flow_workflow_id(request_text) or "add_tank_tracks"
        upload_target_id = self._extract_tanktracks_upload_target_id(request_text) or source_image_id
        seed_override = self._extract_tanktracks_seed_override(request_text)
        denoise_override = self._extract_tanktracks_denoise_override(request_text)

        if "workflows_run" not in self._tool_input_schema_by_name:
            message = "Tanktracks flow failed: workflows_run is not exposed."
            self._append_message({"role": "assistant", "content": message})
            if on_progress:
                on_progress("complete", {"assistant_text": message, "strict_command": "tanktracks_flow"})
            return message, tool_events

        source_metadata = await self._fetch_source_image_metadata_strict(
            source_image_id,
            tool_events=tool_events,
            on_progress=on_progress,
        )
        effective_parent_id = upload_target_id
        if isinstance(source_metadata, dict):
            self._record_image_namespaces_from_result(source_metadata)
            effective_parent_id = self._binary_transfer_adapter.resolve_effective_parent_id(
                source_metadata,
                fallback_source_image_id=source_image_id,
            )
        source_namespace = ""
        if isinstance(source_metadata, dict):
            source_namespace = (
                self._binary_transfer_adapter._first_non_empty_string(source_metadata, "namespace") or ""
            )

        local_source_path = await self._download_source_image_strict(
            source_image_id,
            tool_events=tool_events,
            on_progress=on_progress,
        )
        if not local_source_path:
            message = f"Tanktracks flow failed: could not download source image {source_image_id}."
            self._append_message({"role": "assistant", "content": message})
            if on_progress:
                on_progress("complete", {"assistant_text": message, "strict_command": "tanktracks_flow"})
            return message, tool_events
        self._record_source_image_local_path(local_source_path, source_image_id)

        workflow_arguments: Dict[str, Any] = {
            "workflow_id": workflow_id,
            "overrides": {
                "image": local_source_path,
                "filename_prefix": f"AddTankTracks_{uuid.uuid4().hex[:8]}",
            },
            "wait_timeout_s": 300,
            "wait_poll_ms": 1000,
        }
        if seed_override is not None:
            workflow_arguments["overrides"]["seed"] = seed_override
        if denoise_override is not None:
            workflow_arguments["overrides"]["denoise"] = denoise_override

        workflow_result = await self._run_prepared_strict_tool_call(
            "workflows_run",
            workflow_arguments,
            user_text=request_text,
            tool_events=tool_events,
            on_progress=on_progress,
        )
        output_images = workflow_result.get("output_images") if isinstance(workflow_result, dict) else None
        if (not isinstance(output_images, list) or not output_images) and isinstance(workflow_result, dict):
            watched = await self._watch_workflow_outputs_if_needed(
                workflow_result,
                tool_events=tool_events,
                on_progress=on_progress,
            )
            if isinstance(watched, dict):
                workflow_result = watched
                output_images = workflow_result.get("output_images")
        if (
            isinstance(workflow_result, dict)
            and (not isinstance(output_images, list) or not output_images)
            and self._should_retry_workflow_without_outputs(workflow_result)
        ):
            retry_arguments = dict(workflow_arguments)
            retry_arguments["force"] = True
            retry_result = await self._run_prepared_strict_tool_call(
                "workflows_run",
                retry_arguments,
                user_text=request_text,
                tool_events=tool_events,
                on_progress=on_progress,
            )
            retry_output_images = retry_result.get("output_images") if isinstance(retry_result, dict) else None
            if (not isinstance(retry_output_images, list) or not retry_output_images) and isinstance(retry_result, dict):
                watched_retry = await self._watch_workflow_outputs_if_needed(
                    retry_result,
                    tool_events=tool_events,
                    on_progress=on_progress,
                )
                if isinstance(watched_retry, dict):
                    retry_result = watched_retry
                    retry_output_images = retry_result.get("output_images")
            workflow_result = retry_result
            output_images = retry_output_images
        if not isinstance(output_images, list) or not output_images:
            message = "Tanktracks flow failed: workflows_run returned no output_images."
            self._append_message({"role": "assistant", "content": message})
            if on_progress:
                on_progress("complete", {"assistant_text": message, "strict_command": "tanktracks_flow"})
            return message, tool_events

        final_image_payload = output_images[0] if isinstance(output_images[0], dict) else None
        if not isinstance(final_image_payload, dict):
            message = "Tanktracks flow failed: invalid output image payload."
            self._append_message({"role": "assistant", "content": message})
            if on_progress:
                on_progress("complete", {"assistant_text": message, "strict_command": "tanktracks_flow"})
            return message, tool_events

        upload_tool = self._select_auto_upload_tool_name()
        if not upload_tool:
            message = "Tanktracks flow failed: Photarium upload tool is unavailable."
            self._append_message({"role": "assistant", "content": message})
            if on_progress:
                on_progress("complete", {"assistant_text": message, "strict_command": "tanktracks_flow"})
            return message, tool_events
        upload_schema = self._tool_input_schema_by_name.get(upload_tool)
        upload_path_key = self._select_upload_local_path_key(upload_schema)
        if not upload_path_key:
            message = f"Tanktracks flow failed: upload tool {upload_tool} has no local-path parameter."
            self._append_message({"role": "assistant", "content": message})
            if on_progress:
                on_progress("complete", {"assistant_text": message, "strict_command": "tanktracks_flow"})
            return message, tool_events
        local_output_path, _path_source = await self._resolve_output_image_local_path(final_image_payload)
        if not local_output_path:
            message = "Tanktracks flow failed: could not resolve the generated output file."
            self._append_message({"role": "assistant", "content": message})
            if on_progress:
                on_progress("complete", {"assistant_text": message, "strict_command": "tanktracks_flow"})
            return message, tool_events

        upload_arguments: Dict[str, Any] = {upload_path_key: local_output_path}
        filename = str(final_image_payload.get("filename") or "").strip()
        stem = self._safe_filename_stem(Path(filename).stem if filename else Path(local_output_path).stem)
        if self._schema_has_property(upload_schema, "name"):
            upload_arguments["name"] = stem
        if self._schema_has_property(upload_schema, "title"):
            upload_arguments["title"] = stem
        if self._schema_has_property(upload_schema, "namespace"):
            upload_arguments["namespace"] = source_namespace or "cf-default"
        parent_key = self._binary_transfer_adapter.select_parent_id_key(upload_schema)
        if parent_key and effective_parent_id:
            upload_arguments[parent_key] = effective_parent_id
        upload_arguments = self._binary_transfer_adapter.apply_tanktracks_upload_conventions(
            upload_tool,
            upload_arguments,
            user_text=request_text,
        )
        upload_result = await self._run_strict_tool_call(
            upload_tool,
            upload_arguments,
            tool_events=tool_events,
            on_progress=on_progress,
        )
        uploaded_image_id = None
        if isinstance(upload_result, dict):
            uploaded_image_id = self._extract_image_id(upload_result)

        message = (
            f"Tanktracks flow completed. Uploaded image ID: {uploaded_image_id or 'unknown'}. "
            f"Effective parent ID: {effective_parent_id}."
        )
        self._append_message({"role": "assistant", "content": message})
        if on_progress:
            on_progress("complete", {"assistant_text": None if stop_after_tool_calls else message, "strict_command": "tanktracks_flow"})
        return (None if stop_after_tool_calls else message), tool_events

    async def _fetch_source_image_metadata_strict(
        self,
        source_image_id: str,
        *,
        tool_events: List[ToolEvent],
        on_progress: Callable[[str, Dict[str, Any]], None] | None,
    ) -> Dict[str, Any] | None:
        for tool_name in ("photarium_get", "catalog_get"):
            if tool_name not in self._tool_input_schema_by_name:
                continue
            schema = self._tool_input_schema_by_name.get(tool_name)
            image_id_key = self._binary_transfer_adapter.select_source_image_id_key(schema)
            if not image_id_key:
                continue
            result = await self._run_strict_tool_call(
                tool_name,
                {image_id_key: source_image_id},
                tool_events=tool_events,
                on_progress=on_progress,
            )
            if isinstance(result, dict) and not result.get("error"):
                return result
        return None

    async def _download_source_image_strict(
        self,
        source_image_id: str,
        *,
        tool_events: List[ToolEvent],
        on_progress: Callable[[str, Dict[str, Any]], None] | None,
    ) -> str | None:
        tool_name = self._select_variation_download_tool()
        if not tool_name:
            return None
        schema = self._tool_input_schema_by_name.get(tool_name)
        image_id_key = self._select_download_image_id_key(schema)
        if not image_id_key:
            return None
        save_path_key = self._select_download_save_path_key(schema)
        include_base64_key = self._select_download_include_base64_key(schema)
        safe_stem = re.sub(r"[^A-Za-z0-9]+", "", source_image_id)[:24] or "VariationSource"
        requested_path: Path | None = None
        payload: Dict[str, Any] = {image_id_key: source_image_id}
        if save_path_key:
            requested_path = Path("/tmp") / f"{safe_stem}_{uuid.uuid4().hex[:8]}.png"
            payload[save_path_key] = str(requested_path)
        if include_base64_key:
            payload[include_base64_key] = False
        result = await self._run_strict_tool_call(
            tool_name,
            payload,
            tool_events=tool_events,
            on_progress=on_progress,
        )
        resolved = self._resolve_downloaded_file_path(result, requested_path=requested_path)
        if resolved:
            self._record_source_image_local_path(resolved, source_image_id)
        return resolved

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

    async def _run_prepared_strict_tool_call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
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
        temp_upload_file: Path | None = None
        try:
            execute_name, execute_arguments, temp_upload_file = await self._prepare_tool_execution(
                tool_name,
                arguments,
                user_text=user_text,
            )
            event.arguments = execute_arguments
            async with self._tool_execution_lock:
                raw_result = await self._router.call_tool(execute_name, execute_arguments)
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
        finally:
            if temp_upload_file is not None:
                try:
                    temp_upload_file.unlink(missing_ok=True)
                except Exception:
                    pass
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

    async def _watch_workflow_outputs_if_needed(
        self,
        workflow_result: Dict[str, Any],
        *,
        tool_events: List[ToolEvent],
        on_progress: Callable[[str, Dict[str, Any]], None] | None,
    ) -> Dict[str, Any] | None:
        prompt_id = str(workflow_result.get("prompt_id") or "").strip()
        if not prompt_id:
            return None
        if "workflows_watch" in self._tool_input_schema_by_name:
            watched = await self._run_strict_tool_call(
                "workflows_watch",
                {"prompt_id": prompt_id, "inactivity_timeout_s": 300, "include_history": True},
                tool_events=tool_events,
                on_progress=on_progress,
            )
            extracted = self._extract_workflow_outputs_from_watch_payload(watched, prompt_id=prompt_id)
            if isinstance(extracted, dict) and isinstance(extracted.get("output_images"), list) and extracted.get("output_images"):
                return extracted
        if "workflows_wait" in self._tool_input_schema_by_name:
            waited = await self._run_strict_tool_call(
                "workflows_wait",
                {"prompt_id": prompt_id, "timeout_s": 300, "poll_ms": 1000},
                tool_events=tool_events,
                on_progress=on_progress,
            )
            extracted = self._extract_workflow_outputs_from_watch_payload(waited, prompt_id=prompt_id)
            if isinstance(extracted, dict) and isinstance(extracted.get("output_images"), list) and extracted.get("output_images"):
                return extracted
        return None

    @staticmethod
    def _should_retry_workflow_without_outputs(workflow_result: Dict[str, Any]) -> bool:
        if not isinstance(workflow_result, dict):
            return False
        if workflow_result.get("likely_cached") is True:
            return True
        status = str(workflow_result.get("status") or "").strip().lower()
        message = str(workflow_result.get("message") or "").strip().lower()
        if status == "complete" and ("no output image records" in message or "no output images" in message):
            return True
        return False

    @staticmethod
    def _extract_workflow_outputs_from_watch_payload(payload: Any, *, prompt_id: str) -> Dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        if isinstance(payload.get("output_images"), list) and payload.get("output_images"):
            return payload
        history = payload.get("history")
        if not isinstance(history, dict):
            return None
        prompt_data = history.get(prompt_id, history)
        if not isinstance(prompt_data, dict):
            return None
        outputs = prompt_data.get("outputs")
        if not isinstance(outputs, dict):
            return None
        output_images: list[Dict[str, Any]] = []
        for node_out in outputs.values():
            if not isinstance(node_out, dict):
                continue
            images = node_out.get("images")
            if not isinstance(images, list):
                continue
            for image in images:
                if not isinstance(image, dict):
                    continue
                filename = str(image.get("filename") or "").strip()
                if not filename:
                    continue
                entry: Dict[str, Any] = {
                    "filename": filename,
                    "subfolder": str(image.get("subfolder") or "").strip(),
                    "type": str(image.get("type") or "output").strip() or "output",
                }
                output_images.append(entry)
        if not output_images:
            return None
        merged = dict(payload)
        merged["prompt_id"] = prompt_id
        merged["output_images"] = output_images
        return merged

    @staticmethod
    def _extract_tanktracks_upload_target_id(request_text: str) -> str | None:
        match = re.search(r"Requested upload target image ID:\s*([A-Za-z0-9-]{8,})", request_text, re.I)
        if not match:
            return None
        return match.group(1).strip()

    @classmethod
    def _should_use_strict_variation_flow(cls, request_text: str) -> bool:
        if "IMAGE VARIATION FLOW REQUEST" not in str(request_text or "").upper():
            return False
        sweep_target = cls._extract_variation_sweep_target(request_text)
        run_count = cls._extract_variation_run_count(request_text) or 1
        if sweep_target == "prompt":
            return False
        if sweep_target in {"seed", "denoise", "cfg", "guidance", "strength", "steps"}:
            if sweep_target == "seed":
                return run_count > 1 or bool(cls._extract_variation_seed_values(request_text))
            return bool(cls._extract_variation_sweep_values(request_text))
        return run_count > 1

    @staticmethod
    def _extract_variation_workflow_id(request_text: str) -> str | None:
        match = re.search(r"Workflow preference:\s*([A-Za-z0-9_.:-]+)", request_text, re.I)
        if not match:
            return None
        value = match.group(1).strip()
        return value or None

    @staticmethod
    def _extract_variation_analysis_prompt(request_text: str) -> str | None:
        match = re.search(r"Image analysis prompt:\s*(.+)", request_text, re.I)
        if not match:
            return None
        value = match.group(1).strip()
        if not value or value.lower().startswith("use workflow default"):
            return None
        return value

    @staticmethod
    def _extract_variation_extra_instructions(request_text: str) -> str | None:
        match = re.search(r"Additional variation instructions:\s*(.+)", request_text, re.I)
        if not match:
            return None
        value = match.group(1).strip()
        if not value or value.lower() == "none":
            return None
        return value

    @staticmethod
    def _build_variation_base_prompt(*, analysis_prompt: str | None, extra_instructions: str | None) -> str | None:
        if analysis_prompt and extra_instructions:
            return f"{analysis_prompt}\n\nAdditional variation instructions: {extra_instructions}"
        if analysis_prompt:
            return analysis_prompt
        if extra_instructions:
            return extra_instructions
        return None

    @staticmethod
    def _extract_variation_run_count(request_text: str) -> int | None:
        match = re.search(r"Requested run count:\s*(\d{1,2})", request_text, re.I)
        if not match:
            return None
        try:
            return max(1, min(24, int(match.group(1))))
        except ValueError:
            return None

    @staticmethod
    def _extract_variation_sweep_target(request_text: str) -> str | None:
        match = re.search(r"Sweep target:\s*([A-Za-z0-9_ ()-]+)", request_text, re.I)
        if not match:
            return None
        raw = match.group(1).strip().lower()
        aliases = {
            "seed": "seed",
            "seed (default)": "seed",
            "denoise": "denoise",
            "cfg": "cfg",
            "guidance": "guidance",
            "strength": "strength",
            "steps": "steps",
            "prompt": "prompt",
        }
        return aliases.get(raw)

    @staticmethod
    def _extract_variation_sweep_values(request_text: str) -> list[float]:
        match = re.search(r"Sweep values:\s*(.+)", request_text, re.I)
        if not match:
            return []
        raw = match.group(1).strip()
        if not raw or raw.lower() == "none provided":
            return []
        values: list[float] = []
        for token in re.findall(r"-?\d*\.?\d+", raw):
            try:
                values.append(float(token))
            except ValueError:
                continue
        return values[:24]

    @staticmethod
    def _extract_variation_seed_values(request_text: str) -> list[int]:
        match = re.search(r"Seed sweep values \(randomized\):\s*(.+)", request_text, re.I)
        if not match:
            return []
        raw = match.group(1).strip()
        if not raw or raw.lower() == "none provided":
            return []
        values: list[int] = []
        for token in re.findall(r"\d+", raw):
            try:
                values.append(int(token))
            except ValueError:
                continue
        return values[:24]

    async def _fetch_workflow_param_names_strict(
        self,
        workflow_id: str,
        *,
        tool_events: List[ToolEvent],
        on_progress: Callable[[str, Dict[str, Any]], None] | None,
    ) -> list[str]:
        if "workflows_params_get" not in self._tool_input_schema_by_name:
            return []
        schema = self._tool_input_schema_by_name.get("workflows_params_get")
        workflow_id_key = self._workflow_tool_repairs.select_workflow_id_key(schema)
        if not workflow_id_key:
            return []
        result = await self._run_strict_tool_call(
            "workflows_params_get",
            {workflow_id_key: workflow_id},
            tool_events=tool_events,
            on_progress=on_progress,
        )
        return self._workflow_tool_repairs.extract_param_names(result)

    @staticmethod
    def _select_variation_image_key(param_names: list[str]) -> str | None:
        lowered_map = {name.strip().lower(): name for name in param_names if isinstance(name, str) and name.strip()}
        for preferred in ("image", "input_image", "source_image"):
            if preferred in lowered_map:
                return lowered_map[preferred]
        return None

    @staticmethod
    def _select_variation_prompt_key(param_names: list[str]) -> str | None:
        lowered_map = {name.strip().lower(): name for name in param_names if isinstance(name, str) and name.strip()}
        for preferred in ("image_analysis_instructions", "positive_prompt", "prompt", "text_prompt", "string"):
            if preferred in lowered_map:
                return lowered_map[preferred]
        return None

    @staticmethod
    def _select_variation_sweep_key(sweep_target: str | None, param_names: list[str]) -> str | None:
        if not sweep_target:
            return None
        lowered_map = {name.strip().lower(): name for name in param_names if isinstance(name, str) and name.strip()}
        if sweep_target == "denoise":
            return lowered_map.get("denoise")
        if sweep_target in {"cfg", "guidance"}:
            return lowered_map.get("cfg") or lowered_map.get("guidance")
        if sweep_target == "strength":
            return lowered_map.get("strength") or lowered_map.get("denoise")
        if sweep_target == "steps":
            return lowered_map.get("steps")
        return None

    def _normalize_variation_seed_values(self, seed_values: list[int], desired_runs: int) -> list[int]:
        normalized: list[int] = []
        seen: set[int] = set()
        for raw in seed_values[:desired_runs]:
            value = int(raw)
            if value <= 0 or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        while len(normalized) < desired_runs:
            candidate = self._workflow_tool_repairs.generate_random_seed()
            if candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized

    @staticmethod
    def _coerce_variation_sweep_value(sweep_target: str, raw_value: float) -> Any:
        if sweep_target in {"seed", "steps", "cfg"}:
            return max(1, int(round(raw_value)))
        return float(raw_value)

    @staticmethod
    def _extract_tanktracks_seed_override(request_text: str) -> int | None:
        match = re.search(r"Seed override:\s*(.+)", request_text, re.I)
        if not match:
            return None
        value = match.group(1).strip()
        if not value or value.lower().startswith("use workflow default"):
            return None
        try:
            return int(value)
        except ValueError:
            return None

    @staticmethod
    def _extract_tanktracks_denoise_override(request_text: str) -> float | None:
        match = re.search(r"Denoise override:\s*(.+)", request_text, re.I)
        if not match:
            return None
        value = match.group(1).strip()
        if not value or value.lower().startswith("use workflow default"):
            return None
        try:
            return float(value)
        except ValueError:
            return None

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
            selector = ToolSelector(self._tools)
            selector.select_tools_for_user_text(
                user_text=user_text,
                recent_tool_names=self._recent_tool_names(),
                max_tools_per_request=max(1, len(self._tools)),
                context_text=self._recent_context_text(),
            )
            self._last_tool_selection_debug = dict(selector.last_selection_debug)
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

    def _domain_guardrail_retry_payload(
        self,
        *,
        prepared_calls: list[tuple[ToolCall, Dict[str, Any]]],
        user_text: str,
    ) -> dict[str, Any] | None:
        if self._allows_generic_mutating_file_tool(user_text):
            return None
        generic_calls = [
            call.name
            for call, _arguments in prepared_calls
            if ToolSelector.is_generic_mutating_file_tool(call.name)
        ]
        if not generic_calls:
            return None

        debug = self._last_tool_selection_debug or {}
        protected_domains = [
            str(domain)
            for domain in debug.get("protected_domains", [])
            if str(domain) in ToolSelector.protected_domain_names()
        ]
        if not protected_domains:
            return None

        pinned_tools = [
            str(name)
            for name in debug.get("pinned_tools", [])
            if str(name) and not ToolSelector.is_generic_mutating_file_tool(str(name))
        ]
        omitted_critical_tools = [
            str(name)
            for name in debug.get("omitted_critical_tools", [])
            if str(name) and not ToolSelector.is_generic_mutating_file_tool(str(name))
        ]
        domain_tools = pinned_tools or omitted_critical_tools
        if not domain_tools:
            return None

        return {
            "domains": protected_domains,
            "generic_tool_names": generic_calls,
            "preferred_tool_names": domain_tools[:12],
            "omitted_critical_tools": omitted_critical_tools[:12],
        }

    @staticmethod
    def _allows_generic_mutating_file_tool(user_text: str) -> bool:
        lowered = (user_text or "").lower()
        return any(
            marker in lowered
            for marker in (
                "workspace_file_write",
                "workspace_file_copy",
                "workspace_file_move",
                "workspace_file_delete",
                "use the workspace file tool",
                "raw file edit",
                "manual file edit",
                "edit the file directly",
            )
        )

    @staticmethod
    def _domain_guardrail_corrective_note(payload: dict[str, Any]) -> str:
        domains = ", ".join(str(item) for item in payload.get("domains", [])) or "protected domain"
        generic_tools = ", ".join(str(item) for item in payload.get("generic_tool_names", [])) or "generic file tool"
        preferred_tools = ", ".join(str(item) for item in payload.get("preferred_tool_names", [])[:8])
        if preferred_tools:
            return (
                f"The prior tool choice used {generic_tools}, but this request matches {domains}. "
                f"Retry using the domain MCP tools that are available in this tool window first: {preferred_tools}. "
                "Only use generic mutating workspace file tools if the user explicitly asks for direct file editing."
            )
        return (
            f"The prior tool choice used {generic_tools}, but this request matches {domains}. "
            "Retry with domain-specific MCP tools first and avoid generic mutating workspace file tools unless explicitly requested."
        )

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
