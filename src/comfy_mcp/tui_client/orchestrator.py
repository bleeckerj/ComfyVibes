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

            # Use streaming so we can emit tokens in real-time
            def _on_token(text: str) -> None:
                _emit_progress("llm_token", {"text": text, "round": round_num})

            response = await self._llm.chat_stream(
                self._messages, self._tools, on_token=_on_token,
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
                        raw_result = await self._router.call_tool(execute_name, execute_arguments)
                        result = normalize_search_tool_result(call.name, raw_result)
                        self._record_image_namespaces_from_result(result)
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

    def _total_content_chars(self) -> int:
        return sum(len(content) for content in (m.get("content") for m in self._messages) if isinstance(content, str))

    async def _prepare_tool_execution(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> tuple[str, Dict[str, Any], Path | None]:
        transformed = await self._maybe_convert_upload_url_to_from_path(
            tool_name,
            arguments,
            user_text=user_text,
        )
        if transformed is not None:
            return transformed
        return tool_name, arguments, None

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
