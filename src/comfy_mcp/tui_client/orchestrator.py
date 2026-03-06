"""Tool-calling loop orchestration."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Protocol
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from comfy_mcp.tui_client.llm_client import LLMResponse, ToolCall
from comfy_mcp.tui_client.sanitize import sanitize_result
from comfy_mcp.tui_client.search_semantics import (
    normalize_binary_transfer_arguments,
    normalize_photarium_upload_arguments,
    normalize_search_tool_arguments,
    normalize_search_tool_result,
)
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


class ChatOrchestrator:
    _MAX_TOOLS_PER_LLM_REQUEST = 128

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
        self._image_namespace_by_id: Dict[str, str] = {}
        self._tool_execution_lock = asyncio.Lock()

    def reset_conversation(self) -> None:
        """Reset conversation to just the system prompt."""
        if not self._messages:
            return
        self._messages = [self._messages[0]]
        self._image_namespace_by_id = {}

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

    async def process(
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
        round_num = 0
        repeated_tool_rounds = 0
        previous_tool_signature: tuple[tuple[str, str], ...] | None = None

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

            tools_for_request = self._select_tools_for_user_text(user_text)
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
                    # Sanitize result for LLM context — strip base64 blobs
                    # so they don't blow up the token window.
                    sanitized = sanitize_result(result, tool_name=call.name, save_artifacts=True)
                    self._append_message(
                        {
                            "role": "tool",
                            "tool_call_id": call.call_id,
                            "name": call.name,
                            "content": self._safe_json_dumps(sanitized, separators=(",", ":")),
                        }
                    )
                if stop_after_tool_calls:
                    _emit_progress("complete", {"assistant_text": None, "stopped_after_tools": True})
                    return None, tool_events
                continue

            if response.content:
                self._append_message({"role": "assistant", "content": response.content})
                _emit_progress("complete", {"assistant_text": response.content})
                return response.content, tool_events

            _emit_progress("complete", {"assistant_text": None})
            return None, tool_events

    def _select_tools_for_user_text(self, user_text: str) -> List[Dict[str, Any]]:
        if len(self._tools) <= self._MAX_TOOLS_PER_LLM_REQUEST:
            return self._tools

        text = (user_text or "").lower()
        recent_tool_names = self._recent_tool_names()
        ranked: list[tuple[int, int, Dict[str, Any]]] = []

        for index, tool in enumerate(self._tools):
            name = self._tool_name(tool)
            score = self._tool_priority(name=name, text=text, recent_tool_names=recent_tool_names)
            ranked.append((score, index, tool))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [tool for _, _, tool in ranked[: self._MAX_TOOLS_PER_LLM_REQUEST]]

    def _recent_tool_names(self) -> set[str]:
        names: set[str] = set()
        for message in self._messages[-40:]:
            if message.get("role") != "tool":
                continue
            name = message.get("name")
            if isinstance(name, str) and name:
                names.add(name)
        return names

    @staticmethod
    def _tool_name(tool: Dict[str, Any]) -> str:
        function = tool.get("function")
        if not isinstance(function, dict):
            return ""
        name = function.get("name")
        if isinstance(name, str):
            return name
        return ""

    @staticmethod
    def _tool_priority(name: str, text: str, recent_tool_names: set[str]) -> int:
        if not name:
            return -10_000

        score = 0
        if name in recent_tool_names:
            score += 120
        if name in {"list_tools", "tool_schema_get"}:
            score += 55
        if name in text:
            score += 300

        if name.startswith("workflows_"):
            score += 35
            if any(term in text for term in ("workflow", "corpus", "extract", "import", "package", "metadata")):
                score += 140
        elif name.startswith("comfy_"):
            score += 25
            if any(term in text for term in ("comfy", "comfyui", "queue", "history", "node", "model")):
                score += 120
        elif name.startswith("photarium_") or name.startswith("catalog_"):
            score += 20
            if any(term in text for term in ("photarium", "catalog", "image id", "namespace", "upload", "variant")):
                score += 120
        elif name.startswith("editorial_"):
            score += 10
            if any(term in text for term in ("editorial", "copy", "headline", "brief", "ad")):
                score += 120
        elif name.startswith("backoffice_"):
            score += 10
            if any(term in text for term in ("backoffice", "ticket", "crm", "finance")):
                score += 120

        return score

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
        if tool_name == "workflows_run":
            arguments = self._repair_workflows_run_arguments(arguments)
            arguments = await self._repair_stitching_workflows_run_arguments(
                tool_name,
                arguments,
                user_text=user_text,
            )
            arguments = await self._repair_tanktracks_workflows_run_arguments(
                tool_name,
                arguments,
                user_text=user_text,
            )
            arguments = await self._repair_variation_workflows_run_arguments(
                tool_name,
                arguments,
                user_text=user_text,
            )
        arguments = self._repair_workflows_import_from_artifact_arguments(tool_name, arguments, user_text=user_text)
        await self._preflight_workflows_run_arguments(tool_name, arguments, user_text=user_text)
        self._preflight_workflows_import_from_artifact_arguments(tool_name, arguments)
        transformed = await self._maybe_convert_upload_url_to_from_path(
            tool_name,
            arguments,
            user_text=user_text,
        )
        if transformed is not None:
            exec_name, exec_args, temp_file = transformed
            exec_args = self._apply_tanktracks_upload_conventions(exec_name, exec_args, user_text=user_text)
            return exec_name, exec_args, temp_file
        arguments = self._apply_tanktracks_upload_conventions(tool_name, arguments, user_text=user_text)
        return tool_name, arguments, None

    async def _preflight_workflows_run_arguments(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> None:
        if tool_name != "workflows_run":
            return
        if not isinstance(arguments, dict):
            raise RuntimeError("workflows_run preflight failed: arguments must be an object")
        overrides = arguments.get("overrides")
        if isinstance(overrides, dict):
            return
        if "overrides" in arguments:
            raise RuntimeError("workflows_run preflight blocked: 'overrides' must be an object.")

        shorthand_image_id = self._extract_shorthand_edit_image_id(user_text)
        verified_hint = ""
        if shorthand_image_id:
            verified = await self._verify_photarium_image_id_candidate(shorthand_image_id)
            if verified:
                verified_hint = (
                    f" The shorthand target '{shorthand_image_id}' appears to be a valid Photarium image ID "
                    "(verified via photarium_get)."
                )

        raise RuntimeError(
            "workflows_run preflight blocked: missing required 'overrides' object."
            f"{verified_hint} Convert shorthand intent into explicit tool args first "
            "(for example: photarium_get -> download image -> workflows_run with "
            '{"workflow_id":"...","overrides":{"image":"<local_path_or_comfy_filename>"}}).'
        )

    @staticmethod
    def _repair_workflows_run_arguments(arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Auto-wrap flattened workflows_run override keys into an explicit overrides object.

        Models sometimes produce a valid intent but omit the nested `overrides` wrapper,
        e.g. {"workflow_id":"x","image":"...","filename_prefix":"..."}.
        Repairing that locally avoids a wasted retry round.
        """
        if not isinstance(arguments, dict):
            return arguments
        repaired: Dict[str, Any] = dict(arguments)

        # Flatten nested wrappers that some model/tool bridges emit.
        # Some bridges have also been observed to stash the full payload under
        # `token`; only unwrap it when it is an object, never when it is the
        # normal auth token string.
        wrapper_keys = ("arguments", "args", "input", "payload", "params", "token")
        flattened_wrapper_keys: set[str] = set()
        for wrapper_key in wrapper_keys:
            wrapped = repaired.get(wrapper_key)
            if not isinstance(wrapped, dict):
                continue
            flattened_wrapper_keys.add(wrapper_key)
            for key, value in wrapped.items():
                repaired.setdefault(key, value)
        for wrapper_key in flattened_wrapper_keys:
            repaired.pop(wrapper_key, None)

        # Canonicalize common alias keys.
        aliases = (
            ("workflow", "workflow_id"),
            ("workflowId", "workflow_id"),
            ("id", "workflow_id"),
            ("clientId", "client_id"),
            ("waitTimeoutS", "wait_timeout_s"),
            ("waitPollMs", "wait_poll_ms"),
        )
        for alias, canonical in aliases:
            if canonical not in repaired and alias in repaired:
                repaired[canonical] = repaired[alias]
            if alias != canonical and canonical in repaired:
                repaired.pop(alias, None)

        # Best-effort parse when overrides arrives as a JSON string.
        overrides_value = repaired.get("overrides")
        if isinstance(overrides_value, str):
            parsed: Any = None
            try:
                parsed = json.loads(overrides_value)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                repaired["overrides"] = parsed

        if "workflow_id" not in repaired or "overrides" in repaired:
            return repaired

        control_keys = {
            "workflow_id",
            "client_id",
            "token",
            "force",
            "wait_timeout_s",
            "wait_poll_ms",
        }
        overrides: Dict[str, Any] = {}
        normalized: Dict[str, Any] = {}
        for key, value in repaired.items():
            if key in control_keys:
                normalized[key] = value
            else:
                overrides[key] = value

        if not overrides:
            return repaired
        normalized["overrides"] = overrides
        return normalized

    async def _repair_stitching_workflows_run_arguments(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> Dict[str, Any]:
        """Best-effort repair for stitch workflows missing required workflows_run overrides."""
        if tool_name != "workflows_run" or not isinstance(arguments, dict):
            return arguments

        workflow_id = str(arguments.get("workflow_id") or "").strip()
        if not self._is_stitching_workflow(workflow_id):
            return arguments

        if "overrides" in arguments and not isinstance(arguments.get("overrides"), dict):
            return arguments

        repaired = dict(arguments)
        existing_overrides = repaired.get("overrides")
        repaired_overrides = dict(existing_overrides) if isinstance(existing_overrides, dict) else {}

        # Normalize common alias keys into canonical stitch params.
        alias_map = (
            ("input_image", "image"),
            ("inputImage", "image"),
            ("input_image_1", "image"),
            ("inputImage1", "image"),
            ("image_1", "image"),
            ("image1", "image"),
            ("left_image", "image"),
            ("source_image", "image"),
            ("sourceImage", "image"),
            ("reference_image", "image"),
            ("reference_image_1", "image"),
            ("input_image_2", "image_2"),
            ("inputImage2", "image_2"),
            ("image2", "image_2"),
            ("right_image", "image_2"),
            ("second_image", "image_2"),
            ("source_image_2", "image_2"),
            ("sourceImage2", "image_2"),
            ("reference_image_2", "image_2"),
        )
        for alias, canonical in alias_map:
            canonical_value = repaired_overrides.get(canonical)
            if isinstance(canonical_value, str) and canonical_value.strip():
                continue
            value = repaired_overrides.get(alias)
            if not (isinstance(value, str) and value.strip()):
                value = repaired.get(alias)
            if isinstance(value, str) and value.strip():
                repaired_overrides[canonical] = value.strip()

        stitch_sources = self._extract_stitch_source_image_ids(user_text)
        needs_image = not (isinstance(repaired_overrides.get("image"), str) and repaired_overrides["image"].strip())
        needs_image_2 = (
            self._stitch_workflow_requires_second_image(workflow_id)
            and not (isinstance(repaired_overrides.get("image_2"), str) and repaired_overrides["image_2"].strip())
        )

        if needs_image and stitch_sources:
            local_path = await self._download_variation_source_image(stitch_sources[0])
            if isinstance(local_path, str) and local_path.strip():
                repaired_overrides["image"] = local_path
                needs_image = False

        if needs_image_2 and len(stitch_sources) >= 2:
            local_path = await self._download_variation_source_image(stitch_sources[1])
            if isinstance(local_path, str) and local_path.strip():
                repaired_overrides["image_2"] = local_path
                needs_image_2 = False

        # Remove alias keys from the top-level payload; workflows_run only expects control keys there.
        for alias, canonical in alias_map:
            repaired.pop(alias, None)
            if canonical not in {"image", "image_2"}:
                continue
            repaired.pop(canonical, None)
        repaired["overrides"] = repaired_overrides

        # Prefer robust wait defaults for heavier image jobs.
        if "wait_timeout_s" not in repaired:
            repaired["wait_timeout_s"] = 300
        if "wait_poll_ms" not in repaired:
            repaired["wait_poll_ms"] = 1000
        return repaired

    async def _repair_variation_workflows_run_arguments(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> Dict[str, Any]:
        """Best-effort repair for /vary flows missing an explicit source-image override."""
        if tool_name != "workflows_run" or not isinstance(arguments, dict):
            return arguments

        workflow_id = str(arguments.get("workflow_id") or "").strip()
        if not self._is_image_variation_workflow(workflow_id):
            return arguments

        existing_overrides = arguments.get("overrides")
        if isinstance(existing_overrides, dict):
            image_value = existing_overrides.get("image")
            if isinstance(image_value, str) and image_value.strip():
                return arguments

        source_image_id = self._extract_variation_source_image_id(user_text)
        if not source_image_id:
            return arguments

        local_source_path = await self._download_variation_source_image(source_image_id)
        if not local_source_path:
            return arguments

        repaired = dict(arguments)
        repaired_overrides = dict(existing_overrides) if isinstance(existing_overrides, dict) else {}
        repaired_overrides["image"] = local_source_path
        repaired["overrides"] = repaired_overrides
        return repaired

    async def _repair_tanktracks_workflows_run_arguments(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> Dict[str, Any]:
        """Best-effort repair for /tanktracks flows missing required workflows_run overrides."""
        if tool_name != "workflows_run" or not isinstance(arguments, dict):
            return arguments
        if "TANK TRACKS FLOW REQUEST" not in str(user_text or "").upper():
            return arguments

        repaired = dict(arguments)
        workflow_id = str(repaired.get("workflow_id") or "").strip()
        if not workflow_id:
            inferred_workflow = self._extract_flow_workflow_id(user_text)
            if inferred_workflow:
                workflow_id = inferred_workflow
                repaired["workflow_id"] = inferred_workflow
        if not workflow_id:
            workflow_id = "add_tank_tracks"
            repaired["workflow_id"] = workflow_id
        if workflow_id != "add_tank_tracks":
            return repaired

        existing_overrides = repaired.get("overrides")
        if not isinstance(existing_overrides, dict):
            existing_overrides = {}
        repaired_overrides = dict(existing_overrides)

        image_value = repaired_overrides.get("image")
        if not (isinstance(image_value, str) and image_value.strip()):
            source_image_id = self._extract_flow_source_image_id(user_text)
            if source_image_id:
                local_source_path = await self._download_variation_source_image(source_image_id)
                if local_source_path:
                    repaired_overrides["image"] = local_source_path

        prefix_value = repaired_overrides.get("filename_prefix")
        if not (isinstance(prefix_value, str) and prefix_value.strip()):
            repaired_overrides["filename_prefix"] = f"AddTankTracks_{uuid.uuid4().hex[:8]}"

        repaired["overrides"] = repaired_overrides

        # Prefer robust wait defaults for heavier image jobs.
        if "wait_timeout_s" not in repaired:
            repaired["wait_timeout_s"] = 300
        if "wait_poll_ms" not in repaired:
            repaired["wait_poll_ms"] = 1000

        return repaired

    @staticmethod
    def _extract_flow_source_image_id(user_text: str) -> str | None:
        if not isinstance(user_text, str):
            return None
        match = re.search(r"Source catalog image ID:\s*([A-Za-z0-9-]{8,})", user_text, re.I)
        if not match:
            return None
        return match.group(1).strip()

    @staticmethod
    def _extract_flow_workflow_id(user_text: str) -> str | None:
        if not isinstance(user_text, str):
            return None
        match = re.search(r"Workflow preference:\s*([A-Za-z0-9._-]+)", user_text, re.I)
        if not match:
            return None
        return match.group(1).strip()

    async def _download_variation_source_image(self, source_image_id: str) -> str | None:
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
            requested_path = Path(tempfile.gettempdir()) / f"{safe_stem}_{uuid.uuid4().hex[:8]}.png"
            payload[save_path_key] = str(requested_path)
        if include_base64_key:
            payload[include_base64_key] = False

        try:
            async with self._tool_execution_lock:
                result = await self._router.call_tool(tool_name, payload)
        except Exception:
            return None

        resolved = self._resolve_downloaded_file_path(result, requested_path=requested_path)
        return resolved

    def _select_variation_download_tool(self) -> str | None:
        for candidate in ("photarium_download_image", "catalog_download_image", "photarium_download_original"):
            if candidate in self._tool_input_schema_by_name:
                return candidate
        return None

    @staticmethod
    def _select_download_image_id_key(schema: Dict[str, Any] | None) -> str | None:
        if not isinstance(schema, dict):
            return "imageId"
        props = schema.get("properties")
        if not isinstance(props, dict):
            return "imageId"
        for key in ("imageId", "image_id", "id", "imageUUID", "image_uuid", "uuid"):
            if key in props:
                return key
        return "imageId"

    @staticmethod
    def _select_download_save_path_key(schema: Dict[str, Any] | None) -> str | None:
        if not isinstance(schema, dict):
            return "savePath"
        props = schema.get("properties")
        if not isinstance(props, dict):
            return "savePath"
        for key in ("savePath", "save_path", "filePath", "file_path", "path", "localPath", "local_path"):
            if key in props:
                return key
        return None

    @staticmethod
    def _select_download_include_base64_key(schema: Dict[str, Any] | None) -> str | None:
        if not isinstance(schema, dict):
            return None
        props = schema.get("properties")
        if not isinstance(props, dict):
            return None
        for key in ("includeBase64", "include_base64", "includeData", "include_data"):
            if key in props:
                return key
        return None

    @staticmethod
    def _resolve_downloaded_file_path(download_result: Any, *, requested_path: Path | None) -> str | None:
        if isinstance(download_result, dict):
            for key in ("savedPath", "savePath", "filePath", "file_path", "localPath", "local_path", "path"):
                value = download_result.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            filename = download_result.get("filename")
            if (
                isinstance(filename, str)
                and filename.strip()
                and requested_path is not None
                and requested_path.exists()
                and requested_path.is_dir()
            ):
                return str(requested_path / filename.strip())
        if requested_path is not None:
            return str(requested_path)
        return None

    @staticmethod
    def _is_image_variation_workflow(workflow_id: str) -> bool:
        return str(workflow_id or "").strip().lower() == "image_variation_maker"

    @staticmethod
    def _is_stitching_workflow(workflow_id: str) -> bool:
        normalized = str(workflow_id or "").strip().lower()
        if not normalized:
            return False
        if normalized in {"flux_kontext_image_stitch", "flux_kontext_multi_image_stitching"}:
            return True
        return normalized.startswith("flux_kontext_") and "stitch" in normalized

    @staticmethod
    def _stitch_workflow_requires_second_image(workflow_id: str) -> bool:
        normalized = str(workflow_id or "").strip().lower()
        return normalized in {
            "flux_kontext_multi_image_stitching",
            "flux_kontext_multi_image_chaining",
        }

    @staticmethod
    def _extract_stitch_source_image_ids(user_text: str) -> list[str]:
        if not isinstance(user_text, str):
            return []

        token = r"([A-Za-z0-9][A-Za-z0-9._:-]{1,127})"
        patterns = (
            rf"Source\s+catalog\s+image\s+IDs?\s*:\s*{token}\s*(?:,|and|\s)\s*{token}",
            rf"Stitch\s+Flow\s*:\s*source(?:_id)?1?\s*=\s*{token}\s+source(?:_id)?2?\s*=\s*{token}",
            rf"\b(?:/stitch|stitch)\s+{token}\s+(?:and\s+)?{token}\b",
        )
        for pattern in patterns:
            match = re.search(pattern, user_text, re.IGNORECASE)
            if not match:
                continue
            first = match.group(1).strip()
            second = match.group(2).strip()
            if first and second:
                return [first, second]

        # Fallback: take first two UUID-like tokens in order.
        ids: list[str] = []
        for match in re.finditer(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
            user_text,
        ):
            value = match.group(0).strip()
            if value in ids:
                continue
            ids.append(value)
            if len(ids) >= 2:
                break
        return ids

    @staticmethod
    def _extract_variation_source_image_id(user_text: str) -> str | None:
        if not isinstance(user_text, str):
            return None
        patterns = (
            r"Source catalog image ID:\s*([A-Za-z0-9][A-Za-z0-9._:-]{1,127})",
            r"Variation Flow:\s*source=([A-Za-z0-9][A-Za-z0-9._:-]{1,127})",
            r"\b(?:/vary|/variation|/variations)\s+(?:image\s+id\s+|image\s+)?([A-Za-z0-9][A-Za-z0-9._:-]{1,127})\b",
        )
        for pattern in patterns:
            match = re.search(pattern, user_text, re.IGNORECASE)
            if not match:
                continue
            value = match.group(1).strip()
            if value:
                return value
        return None

    @classmethod
    def _repair_workflows_import_from_artifact_arguments(
        cls,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> Dict[str, Any]:
        if tool_name != "workflows_import_from_artifact":
            return arguments
        if not isinstance(arguments, dict):
            return arguments

        repaired = dict(arguments)

        # Flatten nested wrappers that some model/tool bridges emit.
        wrapper_keys = ("arguments", "args", "input", "payload", "params", "token")
        flattened_wrapper_keys: set[str] = set()
        for wrapper_key in wrapper_keys:
            wrapped = repaired.get(wrapper_key)
            if not isinstance(wrapped, dict):
                continue
            flattened_wrapper_keys.add(wrapper_key)
            for key, value in wrapped.items():
                repaired.setdefault(key, value)
        for wrapper_key in flattened_wrapper_keys:
            repaired.pop(wrapper_key, None)

        # Canonicalize common alias keys models may produce.
        for alias in (
            "image_path",
            "artifact_path",
            "artifactPath",
            "file_path",
            "filePath",
            "source_path",
            "sourcePath",
            "local_path",
            "localPath",
        ):
            if "path" not in repaired and isinstance(repaired.get(alias), str):
                repaired["path"] = repaired[alias]
        for alias in ("id", "workflow", "workflow_name", "workflowName"):
            if "workflow_id" not in repaired and isinstance(repaired.get(alias), str):
                repaired["workflow_id"] = repaired[alias]

        inferred_path = cls._extract_local_artifact_path_from_text(user_text)
        if "path" not in repaired and inferred_path:
            repaired["path"] = inferred_path

        inferred_name = cls._extract_requested_workflow_name_from_text(user_text)
        if "workflow_id" not in repaired and inferred_name:
            repaired["workflow_id"] = inferred_name
        if "name" not in repaired and inferred_name:
            repaired["name"] = inferred_name

        return repaired

    @staticmethod
    def _preflight_workflows_import_from_artifact_arguments(tool_name: str, arguments: Dict[str, Any]) -> None:
        if tool_name != "workflows_import_from_artifact":
            return
        if not isinstance(arguments, dict):
            raise RuntimeError("workflows_import_from_artifact preflight failed: arguments must be an object")
        missing: list[str] = []
        for required in ("path", "workflow_id"):
            value = arguments.get(required)
            if not isinstance(value, str) or not value.strip():
                missing.append(required)
        if missing:
            missing_text = ", ".join(missing)
            raise RuntimeError(
                "workflows_import_from_artifact preflight blocked: missing required argument(s): "
                f"{missing_text}. Provide an explicit local artifact path and workflow_id."
            )

    @staticmethod
    def _extract_local_artifact_path_from_text(user_text: str) -> str | None:
        if not isinstance(user_text, str):
            return None
        # Local absolute paths with common artifact extensions.
        match = re.search(
            r"(/[^\"'\s]+\.(?:png|jpe?g|webp|mp4|mov|json))",
            user_text,
            re.IGNORECASE,
        )
        if not match:
            return None
        return match.group(1).strip()

    @staticmethod
    def _extract_requested_workflow_name_from_text(user_text: str) -> str | None:
        if not isinstance(user_text, str):
            return None
        patterns = (
            r'\bname\s+"([^"]{1,128})"',
            r"\bname\s+'([^']{1,128})'",
            r"\bworkflow(?:_id| id)?\s+([A-Za-z0-9._:-]{1,128})\b",
        )
        for pattern in patterns:
            match = re.search(pattern, user_text, re.IGNORECASE)
            if not match:
                continue
            value = match.group(1).strip()
            if value:
                return value
        return None

    @staticmethod
    def _extract_shorthand_edit_image_id(user_text: str) -> str | None:
        if not isinstance(user_text, str):
            return None
        match = re.search(r"\bedit\s+image(?:\s+id)?\s+([A-Za-z0-9][A-Za-z0-9._:-]{1,127})\b", user_text, re.I)
        if not match:
            return None
        return match.group(1).strip()

    async def _verify_photarium_image_id_candidate(self, image_id: str) -> bool:
        for tool_name, payload in (
            ("photarium_get", {"imageId": image_id}),
            ("catalog_get", {"imageId": image_id}),
            ("catalog_get", {"image_id": image_id}),
        ):
            if tool_name not in self._tool_input_schema_by_name:
                continue
            try:
                result = await self._router.call_tool(tool_name, payload)
            except Exception:
                continue
            if isinstance(result, dict) and result.get("error"):
                continue
            return True
        return False

    async def _maybe_convert_upload_url_to_from_path(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> tuple[str, Dict[str, Any], Path] | None:
        lowered = tool_name.lower()
        if "upload" not in lowered:
            return None
        if "photarium" not in lowered and "catalog" not in lowered:
            return None
        if "upload_from_path" in lowered:
            return None

        url_value = self._extract_upload_url_value(arguments)
        if not url_value:
            return None

        from_path_tool = self._paired_upload_from_path_tool(tool_name)
        if not from_path_tool:
            return None

        path_key = self._select_upload_path_key(self._tool_input_schema_by_name.get(from_path_tool))
        if not path_key:
            return None

        # Derive a clean label first from available semantic context.
        name_schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        seed_args = normalize_photarium_upload_arguments(
            tool_name,
            arguments,
            name_schema,
            fallback_text=user_text,
        )
        seed_label = str(seed_args.get("name") or "UploadedImage")

        downloaded = await self._download_url_to_temp_file(url_value, seed_label)
        if downloaded is None:
            return None

        # If vision naming is available, refine with image understanding.
        vision_label = await self._vision_semantic_label_from_image(downloaded)
        final_label = self._safe_filename_stem(vision_label or seed_label)

        renamed = downloaded
        target = downloaded.with_name(f"{final_label}{downloaded.suffix or '.png'}")
        if target != downloaded:
            if target.exists():
                target = downloaded.with_name(f"{final_label}_{uuid.uuid4().hex[:6]}{downloaded.suffix or '.png'}")
            downloaded.rename(target)
            renamed = target

        from_path_schema = self._tool_input_schema_by_name.get(from_path_tool)
        new_args = dict(arguments)
        for url_key in ("url", "imageUrl", "image_url", "view_url", "viewUrl"):
            new_args.pop(url_key, None)
        new_args[path_key] = str(renamed)
        # Seed name so immutable/filename fields can be synced.
        new_args["name"] = final_label
        new_args = normalize_photarium_upload_arguments(
            from_path_tool,
            new_args,
            from_path_schema,
            fallback_text=user_text,
        )
        return from_path_tool, new_args, renamed

    def _apply_tanktracks_upload_conventions(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> Dict[str, Any]:
        if "TANK TRACKS FLOW REQUEST" not in str(user_text):
            return arguments
        if not _is_photarium_like_upload_tool(tool_name):
            return arguments

        normalized = dict(arguments)
        normalized = self._ensure_upload_tags(
            normalized,
            required_tags=["tank tracks", "caterpillar tracks", "tracks"],
        )
        self._drop_generic_tanktracks_display_name(normalized)
        return normalized

    @staticmethod
    def _ensure_upload_tags(arguments: Dict[str, Any], required_tags: list[str]) -> Dict[str, Any]:
        payload = dict(arguments)
        for key in ("tags", "tag_names"):
            if key not in payload:
                continue
            value = payload.get(key)
            if isinstance(value, list):
                existing = [str(v).strip() for v in value if str(v).strip()]
                seen = {v.casefold() for v in existing}
                for tag in required_tags:
                    if tag.casefold() not in seen:
                        existing.append(tag)
                payload[key] = existing
                return payload
            if isinstance(value, str):
                parts = [p.strip() for p in value.split(",") if p.strip()]
                seen = {p.casefold() for p in parts}
                for tag in required_tags:
                    if tag.casefold() not in seen:
                        parts.append(tag)
                payload[key] = ", ".join(parts)
                return payload
        payload["tags"] = list(required_tags)
        return payload

    @staticmethod
    def _drop_generic_tanktracks_display_name(arguments: Dict[str, Any]) -> None:
        generic_labels = {
            "addtanktracks",
            "tanktracks",
            "tanktracksflowrequest",
            "tanktracksflow",
        }
        for key in ("name", "title"):
            raw = arguments.get(key)
            if not isinstance(raw, str):
                continue
            compact = re.sub(r"[^a-z0-9]+", "", raw.lower())
            if compact in generic_labels:
                arguments.pop(key, None)

    def _paired_upload_from_path_tool(self, upload_url_tool: str) -> str | None:
        candidates: list[str] = []
        if "upload_url" in upload_url_tool:
            candidates.append(upload_url_tool.replace("upload_url", "upload_from_path"))
        if "upload_image" in upload_url_tool:
            candidates.append(upload_url_tool.replace("upload_image", "upload_from_path"))
        if upload_url_tool.startswith("photarium_"):
            candidates.append("photarium_upload_from_path")
            candidates.append("photarium_upload_image")
        if upload_url_tool.startswith("catalog_"):
            candidates.append("catalog_upload_from_path")
            candidates.append("catalog_upload_image")
        for candidate in candidates:
            if candidate in self._tool_input_schema_by_name:
                return candidate
        return None

    @staticmethod
    def _extract_upload_url_value(arguments: Dict[str, Any]) -> str | None:
        for key in ("url", "imageUrl", "image_url", "view_url", "viewUrl"):
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _select_upload_path_key(schema: Dict[str, Any] | None) -> str | None:
        if not isinstance(schema, dict):
            return None
        props = schema.get("properties")
        if not isinstance(props, dict):
            return None
        for key in ("filePath", "file_path", "path", "localPath", "local_path", "imagePath", "image_path"):
            if key in props:
                return key
        return None

    async def _download_url_to_temp_file(self, url: str, label: str) -> Path | None:
        ext = self._guess_extension_from_url(url) or ".png"
        stem = self._safe_filename_stem(label)
        target = Path(tempfile.gettempdir()) / f"{stem}_{uuid.uuid4().hex[:8]}{ext}"
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                target.write_bytes(response.content)
            return target
        except Exception:
            try:
                target.unlink(missing_ok=True)
            except Exception:
                pass
            return None

    @staticmethod
    def _guess_extension_from_url(url: str) -> str | None:
        try:
            parsed = urlparse(url)
        except Exception:
            return None
        query = parse_qs(parsed.query or "")
        for key in ("view_filename", "filename", "file", "name", "image"):
            values = query.get(key)
            if not values:
                continue
            raw = unquote(str(values[0])).strip()
            if not raw:
                continue
            ext = Path(raw).suffix.lower()
            if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".avif"}:
                return ext
        return None

    @staticmethod
    def _safe_filename_stem(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9]+", "", str(value or "")).strip()
        return cleaned[:64] or "UploadedImage"

    def _normalize_photarium_upload_namespace(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        input_schema: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        lowered = tool_name.lower()
        if "upload" not in lowered:
            return arguments
        if "photarium" not in lowered and "catalog" not in lowered:
            return arguments
        if not self._schema_supports_namespace(input_schema) and "namespace" not in arguments:
            return arguments

        normalized = dict(arguments)
        explicit = str(normalized.get("namespace") or "").strip()
        if explicit:
            return normalized

        inferred = self._infer_namespace_from_upload_arguments(normalized)
        normalized["namespace"] = inferred or "cf-default"
        return normalized

    @staticmethod
    def _schema_supports_namespace(input_schema: Dict[str, Any] | None) -> bool:
        if not isinstance(input_schema, dict):
            return False
        properties = input_schema.get("properties")
        return isinstance(properties, dict) and "namespace" in properties

    def _infer_namespace_from_upload_arguments(self, arguments: Dict[str, Any]) -> str | None:
        for candidate_id in self._candidate_image_ids(arguments):
            namespace = self._image_namespace_by_id.get(candidate_id)
            if namespace:
                return namespace
        return None

    def _record_image_namespaces_from_result(self, payload: Any) -> None:
        stack: list[Any] = [payload]
        while stack:
            current = stack.pop()
            if isinstance(current, list):
                stack.extend(current)
                continue
            if not isinstance(current, dict):
                continue

            image_id = self._extract_image_id(current)
            namespace = str(current.get("namespace") or "").strip()
            if image_id and namespace:
                self._image_namespace_by_id[image_id] = namespace

            stack.extend(current.values())

    def _candidate_image_ids(self, payload: Dict[str, Any]) -> list[str]:
        seen: set[str] = set()
        candidates: list[str] = []
        for key in (
            "imageId",
            "image_id",
            "parentId",
            "parent_id",
            "variantOf",
            "variant_of",
            "sourceImageId",
            "source_image_id",
            "inputImageId",
            "input_image_id",
        ):
            value = payload.get(key)
            if not isinstance(value, str):
                continue
            candidate = value.strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)
        return candidates

    @staticmethod
    def _extract_image_id(payload: Dict[str, Any]) -> str | None:
        for key in (
            "image_id",
            "imageId",
            "catalog_image_id",
            "catalogImageId",
            "photarium_image_id",
            "photariumImageId",
            "image_uuid",
            "imageUuid",
            "uuid",
            "id",
        ):
            value = payload.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return None

    async def _maybe_auto_upload_workflow_outputs(
        self,
        tool_name: str,
        tool_arguments: Dict[str, Any],
        tool_result: Any,
        *,
        user_text: str,
    ) -> Dict[str, Any] | None:
        if tool_name not in {"workflows_run", "workflows_run_aspect_ratio_adjustment"}:
            return None
        if "TANK TRACKS FLOW REQUEST" in str(user_text or "").upper():
            return {"status": "skipped", "reason": "tanktracks_flow_handles_upload"}
        if not isinstance(tool_result, dict):
            return {"status": "skipped", "reason": "non_object_result"}

        output_images = tool_result.get("output_images")
        if not isinstance(output_images, list) or not output_images:
            return {"status": "skipped", "reason": "no_output_images"}

        upload_tool = self._select_auto_upload_tool_name()
        if not upload_tool:
            return {"status": "failed", "reason": "photarium_upload_tool_unavailable"}

        upload_schema = self._tool_input_schema_by_name.get(upload_tool)
        upload_path_key = self._select_upload_local_path_key(upload_schema)
        if not upload_path_key:
            return {"status": "failed", "reason": "upload_tool_missing_path_parameter", "tool": upload_tool}

        items: list[Dict[str, Any]] = []
        uploaded_count = 0
        for image_payload in output_images:
            if not isinstance(image_payload, dict):
                continue
            filename = str(image_payload.get("filename") or "").strip()
            local_path, path_source = await self._resolve_output_image_local_path(image_payload)
            if not local_path:
                items.append(
                    {
                        "filename": filename or None,
                        "status": "failed",
                        "error": "could_not_resolve_local_output_path",
                    }
                )
                continue
            upload_args = self._build_auto_upload_arguments(
                upload_tool_name=upload_tool,
                upload_schema=upload_schema,
                upload_path_key=upload_path_key,
                local_path=local_path,
                image_payload=image_payload,
                tool_arguments=tool_arguments,
                user_text=user_text,
            )
            try:
                async with self._tool_execution_lock:
                    upload_result = await self._router.call_tool(upload_tool, upload_args)
                uploaded_count += 1
                self._record_image_namespaces_from_result(upload_result)
                items.append(
                    {
                        "filename": filename or Path(local_path).name,
                        "status": "uploaded",
                        "tool": upload_tool,
                        "path_source": path_source,
                        "upload_result": upload_result,
                    }
                )
            except Exception as exc:
                items.append(
                    {
                        "filename": filename or Path(local_path).name,
                        "status": "failed",
                        "tool": upload_tool,
                        "path_source": path_source,
                        "error": str(exc),
                    }
                )

        if uploaded_count == len(items) and items:
            status = "uploaded"
        elif uploaded_count > 0:
            status = "partial"
        else:
            status = "failed"
        return {
            "status": status,
            "mode": "automatic",
            "tool": upload_tool,
            "items": items,
        }

    def _select_auto_upload_tool_name(self) -> str | None:
        for candidate in (
            "photarium_upload_from_path",
            "catalog_upload_from_path",
            "photarium_upload_image",
            "catalog_upload_image",
        ):
            if candidate in self._tool_input_schema_by_name:
                return candidate
        return None

    @staticmethod
    def _select_upload_local_path_key(schema: Dict[str, Any] | None) -> str | None:
        if not isinstance(schema, dict):
            return None
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return None
        for key in ("filePath", "file_path", "path", "localPath", "local_path", "imagePath", "image_path"):
            if key in properties:
                return key
        return None

    async def _resolve_output_image_local_path(self, image_payload: Dict[str, Any]) -> tuple[str | None, str | None]:
        local_path = str(image_payload.get("local_path") or "").strip()
        if local_path:
            return local_path, "local_path"

        if "comfy_download_image" not in self._tool_input_schema_by_name:
            return None, None

        filename = str(image_payload.get("filename") or "").strip()
        if not filename:
            return None, None

        schema = self._tool_input_schema_by_name.get("comfy_download_image")
        payload: Dict[str, Any] = {"filename": filename}
        if self._schema_has_property(schema, "image_type"):
            image_type = str(image_payload.get("type") or image_payload.get("image_type") or "output")
            payload["image_type"] = image_type
        if self._schema_has_property(schema, "subfolder"):
            subfolder = str(image_payload.get("subfolder") or "").strip()
            if subfolder:
                payload["subfolder"] = subfolder
        if self._schema_has_property(schema, "save_path"):
            payload["save_path"] = str(Path(tempfile.gettempdir()) / f"{uuid.uuid4().hex[:8]}_{filename}")

        try:
            async with self._tool_execution_lock:
                downloaded = await self._router.call_tool("comfy_download_image", payload)
        except Exception:
            return None, None

        if isinstance(downloaded, dict):
            for key in ("local_path", "savedPath", "savePath", "filePath", "path"):
                value = downloaded.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip(), "comfy_download_image"
        return None, None

    @staticmethod
    def _schema_has_property(schema: Dict[str, Any] | None, key: str) -> bool:
        if not isinstance(schema, dict):
            return False
        properties = schema.get("properties")
        return isinstance(properties, dict) and key in properties

    def _build_auto_upload_arguments(
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
        args: Dict[str, Any] = {upload_path_key: local_path}
        filename = str(image_payload.get("filename") or "").strip()
        stem = self._safe_filename_stem(Path(filename).stem if filename else Path(local_path).stem)
        if self._schema_has_property(upload_schema, "name"):
            args["name"] = stem
        if self._schema_has_property(upload_schema, "title"):
            args["title"] = stem
        if self._schema_has_property(upload_schema, "namespace"):
            args["namespace"] = "cf-default"

        overrides = tool_arguments.get("overrides") if isinstance(tool_arguments, dict) else None
        if isinstance(overrides, dict):
            prompt_value = overrides.get("positive_prompt") or overrides.get("prompt")
            if isinstance(prompt_value, str) and prompt_value.strip():
                if self._schema_has_property(upload_schema, "prompt"):
                    args["prompt"] = prompt_value.strip()
                if self._schema_has_property(upload_schema, "positive_prompt"):
                    args["positive_prompt"] = prompt_value.strip()
        return normalize_photarium_upload_arguments(
            upload_tool_name,
            args,
            upload_schema,
            fallback_text=user_text,
        )


def _is_photarium_like_upload_tool(tool_name: str) -> bool:
    lowered = str(tool_name or "").lower()
    return "upload" in lowered and ("photarium" in lowered or "catalog" in lowered)

    async def _vision_semantic_label_from_image(self, image_path: Path) -> str | None:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return None
        if os.environ.get("COMFY_MCP_DISABLE_VISION_NAMING", "").strip().lower() in {"1", "true", "yes"}:
            return None
        if not image_path.exists() or not image_path.is_file():
            return None
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            return None
        return await asyncio.to_thread(self._vision_semantic_label_from_image_sync, image_path, api_key)

    def _vision_semantic_label_from_image_sync(self, image_path: Path, api_key: str) -> str | None:
        mime = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
            ".tiff": "image/tiff",
            ".avif": "image/avif",
        }.get(image_path.suffix.lower())
        if not mime:
            return None
        try:
            from openai import OpenAI  # type: ignore
        except Exception:
            return None
        image_bytes = image_path.read_bytes()
        data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        prompt = (
            "Return a semantic filename label for this image as 2-6 CamelCase words. "
            "Return only the CamelCase label, no spaces or punctuation."
        )
        client = OpenAI(api_key=api_key, timeout=12.0)
        for model in ("gpt-4o", "gpt-4o-mini"):
            try:
                response = client.chat.completions.create(
                    model=model,
                    temperature=0.1,
                    max_tokens=32,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": data_url}},
                            ],
                        }
                    ],
                )
                content = ""
                if response.choices:
                    content = str(getattr(response.choices[0].message, "content", "") or "").strip()
                label = self._safe_filename_stem(content)
                if label and label != "UploadedImage":
                    return label
            except Exception:
                continue
        return None
