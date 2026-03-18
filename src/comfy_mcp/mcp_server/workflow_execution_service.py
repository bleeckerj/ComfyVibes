"""Workflow execution, waiting, and runtime helpers."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from comfy_mcp.comfy_client.client import ComfyClient
from comfy_mcp.mcp_server.policy import Policy
from comfy_mcp.mcp_server.remote_tool_client import RemoteToolClient
from comfy_mcp.mcp_server.workflow_image_service import WorkflowImageService
from comfy_mcp.params.errors import ParamPatchError
from comfy_mcp.params.patch import patch_workflow
from comfy_mcp.params.schema import ParamSpec
from comfy_mcp.workflow_store.hashing import sha256_json
from comfy_mcp.workflow_store.store import WorkflowStore


class WorkflowExecutionService:
    _FLOAT_FRIENDLY_NUMERIC_FIELDS = {"cfg", "denoise", "guidance", "scale", "shift", "strength"}
    _SEMANTIC_NAME_STOP_WORDS = {
        "a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "of", "on", "or",
        "the", "to", "with", "image", "images", "photo", "photos", "picture", "render", "rendering",
    }
    _SEMANTIC_HINT_KEYWORDS = ("prompt", "caption", "description", "subject", "title", "concept", "theme", "style", "scene")

    def __init__(self, store: WorkflowStore, client: ComfyClient, policy: Policy, comfy_output_dir: Path | None = None, remote_client: RemoteToolClient | None = None, image_service: WorkflowImageService | None = None) -> None:
        self._store = store
        self._client = client
        self._policy = policy
        self._comfy_output_dir = comfy_output_dir.expanduser().resolve() if comfy_output_dir else None
        self._remote_client = remote_client or RemoteToolClient()
        self._image_service = image_service or WorkflowImageService()

    async def run(self, workflow_id: str, overrides: dict[str, Any], client_id: str | None = None, token: str | None = None, force: bool = False, wait_timeout_s: float = 300.0, wait_poll_ms: int = 1000) -> dict[str, Any]:
        self._policy.enforce_mutation(token)
        workflow = self._store.read_workflow(workflow_id)
        params = self._store.read_params(workflow_id)
        if not params:
            raise ValueError("Params schema missing for workflow")
        spec = ParamSpec.model_validate(params)
        promoted_params = self._auto_promote_numeric_param_types(spec, overrides)
        if promoted_params:
            try:
                existing_meta = self._store.read_meta(workflow_id)
                self._store.save_workflow(workflow_id, workflow, meta=existing_meta, params=spec.model_dump())
            except Exception:
                pass
        normalized_overrides = await self._normalize_file_overrides(workflow, spec, overrides)
        normalized_overrides = await self._normalize_aspect_overrides(spec=spec, overrides=normalized_overrides)
        auto_aspect = await self._auto_aspect_overrides_from_input(workflow=workflow, spec=spec, raw_overrides=overrides)
        if auto_aspect:
            normalized_overrides.update(auto_aspect.get("overrides", {}))
        expected_hash = str(spec.workflow_hash)
        actual_hash = sha256_json(workflow)
        hash_mismatch_auto_forced = False
        try:
            patched = patch_workflow(workflow, spec, normalized_overrides, force=force)
        except ParamPatchError as exc:
            if force or "hash mismatch" not in str(exc).lower():
                raise
            patched = patch_workflow(workflow, spec, normalized_overrides, force=True)
            hash_mismatch_auto_forced = True
        filename_prefix_used = await self._ensure_unique_filename_prefix(workflow_id, patched, spec, normalized_overrides, raw_overrides=overrides)
        payload_bytes = len(json.dumps(patched).encode("utf-8"))
        self._policy.enforce_payload_size(payload_bytes)
        queued_at = time.time()
        queue_result = await self._client.queue_prompt(patched, client_id=client_id)
        prompt_id = queue_result.get("prompt_id")
        if not prompt_id:
            if hash_mismatch_auto_forced:
                queue_result["workflow_hash_mismatch_recovered"] = True
                queue_result["workflow_hash_expected"] = expected_hash
                queue_result["workflow_hash_actual"] = actual_hash
                queue_result["workflow_force_applied"] = True
            return queue_result
        result = await self._wait_and_extract(prompt_id, timeout_s=float(wait_timeout_s), poll_ms=max(100, int(wait_poll_ms)), queued_workflow=patched, queued_at=queued_at)
        if auto_aspect:
            result["auto_aspect_ratio_source"] = auto_aspect.get("source_ratio")
            result["auto_aspect_ratio_applied"] = auto_aspect.get("applied_ratio")
            result["auto_aspect_ratio_anchor"] = auto_aspect.get("applied_anchor")
            result["auto_aspect_ratio_reason"] = auto_aspect.get("reason")
        if hash_mismatch_auto_forced:
            result["workflow_hash_mismatch_recovered"] = True
            result["workflow_hash_expected"] = expected_hash
            result["workflow_hash_actual"] = actual_hash
            result["workflow_force_applied"] = True
        if filename_prefix_used:
            result["filename_prefix_used"] = filename_prefix_used
        if promoted_params:
            result["param_type_auto_promoted"] = promoted_params
        return result

    def _auto_promote_numeric_param_types(self, spec: ParamSpec, overrides: dict[str, Any]) -> list[str]:
        promoted: list[str] = []
        param_map = spec.param_map()
        for name, value in overrides.items():
            if isinstance(value, bool) or not isinstance(value, float):
                continue
            item = param_map.get(name)
            if item is None or item.type != "int":
                continue
            name_key = name.lower()
            input_key = str(item.target.input or "").lower()
            if name_key not in self._FLOAT_FRIENDLY_NUMERIC_FIELDS and input_key not in self._FLOAT_FRIENDLY_NUMERIC_FIELDS:
                continue
            item.type = "float"
            promoted.append(name)
        return promoted

    async def _normalize_aspect_overrides(self, *, spec: ParamSpec, overrides: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(overrides)
        aspect_param_name = self._find_param_name(spec, "aspect_ratio")
        custom_ratio_param_name = self._find_param_name(spec, "custom_ratio")
        custom_aspect_param_name = self._find_param_name(spec, "custom_aspect_ratio")
        if not aspect_param_name or not custom_ratio_param_name or not custom_aspect_param_name:
            return normalized
        aspect_value = normalized.get(aspect_param_name)
        if not isinstance(aspect_value, str):
            return normalized
        requested_clean = aspect_value.strip()
        if not requested_clean:
            return normalized
        requested_ratio = self._image_service.normalize_ratio_token(requested_clean)
        is_plain_ratio_token = bool(requested_ratio and re.fullmatch(r"\d+\s*:\s*\d+", requested_clean))
        object_info = await self._client.get_object_info()
        flux_node = object_info.get("FluxResolutionNode") or {}
        flux_inputs = flux_node.get("input", {})
        aspect_entry = flux_inputs.get("required", {}).get("aspect_ratio") or flux_inputs.get("optional", {}).get("aspect_ratio")
        allowed_aspects: list[str] = []
        if isinstance(aspect_entry, list) and aspect_entry and isinstance(aspect_entry[0], list):
            allowed_aspects = [str(item) for item in aspect_entry[0] if isinstance(item, str)]
        matched_allowed_aspect = self._image_service.match_allowed_aspect_choice(requested_clean, allowed_aspects)
        if is_plain_ratio_token:
            normalized[aspect_param_name] = matched_allowed_aspect or (allowed_aspects[0] if allowed_aspects else requested_clean)
            normalized[custom_ratio_param_name] = True
            normalized[custom_aspect_param_name] = requested_ratio
            return normalized
        if matched_allowed_aspect:
            normalized[aspect_param_name] = matched_allowed_aspect
            normalized[custom_ratio_param_name] = True
            normalized[custom_aspect_param_name] = requested_ratio or matched_allowed_aspect.split(" ")[0]
        return normalized

    async def _normalize_file_overrides(self, workflow: dict[str, Any], spec: ParamSpec, overrides: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(overrides)
        param_map = {item.name: item for item in spec.params}
        for key, value in list(normalized.items()):
            if not isinstance(value, str):
                continue
            local_path = Path(value).expanduser()
            if not local_path.exists() or not local_path.is_file():
                continue
            param = param_map.get(key)
            if param is None:
                continue
            target = param.target
            if target.mode != "direct" or not target.node_id or not target.input:
                continue
            node = workflow.get(str(target.node_id))
            if not isinstance(node, dict):
                continue
            if str(node.get("class_type") or "").lower() != "loadimage":
                continue
            if target.input != "image":
                continue
            upload = await self._client.upload_image(str(local_path), image_type="input", overwrite=False)
            uploaded_name = upload.get("name")
            if not uploaded_name:
                raise ValueError(f"Failed to upload input image for override '{key}'")
            uploaded_subfolder = str(upload.get("subfolder") or "").strip("/")
            normalized[key] = f"{uploaded_subfolder}/{uploaded_name}" if uploaded_subfolder else uploaded_name
        return normalized

    async def _ensure_unique_filename_prefix(self, workflow_id: str, workflow: dict[str, Any], spec: ParamSpec, overrides: dict[str, Any], raw_overrides: dict[str, Any]) -> str | None:
        prefix_param_names = self._saveimage_prefix_param_names(workflow, spec)
        user_set_prefix = any(isinstance(overrides.get(name), str) and str(overrides.get(name)).strip() for name in prefix_param_names)
        if user_set_prefix:
            return None
        semantic_label = await self._derive_semantic_filename_label(workflow=workflow, spec=spec, raw_overrides=raw_overrides)
        prefix = self._make_runtime_filename_prefix(workflow_id, semantic_label=semantic_label)
        updated = False
        for node in workflow.values():
            if not isinstance(node, dict):
                continue
            if str(node.get("class_type") or "").lower() != "saveimage":
                continue
            inputs = node.setdefault("inputs", {})
            if isinstance(inputs, dict):
                inputs["filename_prefix"] = prefix
                updated = True
        return prefix if updated else None

    async def _auto_aspect_overrides_from_input(self, *, workflow: dict[str, Any], spec: ParamSpec, raw_overrides: dict[str, Any]) -> dict[str, Any] | None:
        if self._has_explicit_aspect_override(spec, raw_overrides):
            return None
        aspect_param_name = self._find_param_name(spec, "aspect_ratio")
        if not aspect_param_name:
            return None
        custom_ratio_param_name = self._find_param_name(spec, "custom_ratio")
        custom_aspect_param_name = self._find_param_name(spec, "custom_aspect_ratio")
        input_path = self._find_local_input_image_path(workflow=workflow, spec=spec, raw_overrides=raw_overrides)
        if input_path is None:
            return None
        width, height, _fmt = self._image_service.probe_image_dimensions(input_path)
        source_ratio = self._image_service.normalize_ratio_token(f"{width}:{height}")
        if not source_ratio:
            return None
        object_info = await self._client.get_object_info()
        flux_node = object_info.get("FluxResolutionNode") or {}
        flux_inputs = flux_node.get("input", {})
        aspect_entry = flux_inputs.get("required", {}).get("aspect_ratio") or flux_inputs.get("optional", {}).get("aspect_ratio")
        allowed_aspects: list[str] = []
        if isinstance(aspect_entry, list) and aspect_entry and isinstance(aspect_entry[0], list):
            allowed_aspects = [str(item) for item in aspect_entry[0] if isinstance(item, str)]
        if not allowed_aspects:
            return None
        applied = self._image_service.nearest_allowed_aspect_choice(source_ratio, allowed_aspects)
        if not applied:
            return None
        overrides: dict[str, Any] = {aspect_param_name: applied}
        if custom_ratio_param_name:
            overrides[custom_ratio_param_name] = True
        if custom_aspect_param_name:
            overrides[custom_aspect_param_name] = source_ratio
        return {"overrides": overrides, "source_ratio": source_ratio, "applied_ratio": source_ratio, "applied_anchor": applied, "reason": "auto_preserve_from_input_image"}

    @staticmethod
    def _find_param_name(spec: ParamSpec, expected_name: str) -> str | None:
        for item in spec.params:
            if item.name == expected_name:
                return item.name
        return None

    def _has_explicit_aspect_override(self, spec: ParamSpec, raw_overrides: dict[str, Any]) -> bool:
        param_map = spec.param_map()
        for key in ("aspect_ratio", "custom_ratio", "custom_aspect_ratio"):
            if key not in raw_overrides:
                continue
            value = raw_overrides.get(key)
            default = param_map.get(key).default if key in param_map else None
            if key == "custom_ratio":
                if isinstance(value, bool):
                    if value is True:
                        return True
                    if default not in (False, None):
                        return True
                    continue
                if isinstance(value, str):
                    lowered = value.strip().lower()
                    if lowered in {"", "false", "0", "no", "off"} and default in (False, None):
                        continue
                    if lowered in {"true", "1", "yes", "on"}:
                        return True
                if value != default:
                    return True
                continue
            if key == "custom_aspect_ratio":
                value_norm = self._image_service.normalize_ratio_token(str(value)) if value is not None else None
                default_norm = self._image_service.normalize_ratio_token(str(default)) if default is not None else None
                if value_norm and default_norm and value_norm == default_norm:
                    continue
                if value == default or value is None or (isinstance(value, str) and not value.strip()):
                    continue
                return True
            if isinstance(value, str):
                value_clean = value.strip()
                if not value_clean:
                    continue
            else:
                value_clean = str(value)
            if value == default:
                continue
            if isinstance(default, str):
                value_norm = self._image_service.normalize_ratio_token(value_clean)
                default_norm = self._image_service.normalize_ratio_token(default)
                if value_norm and default_norm and value_norm == default_norm:
                    continue
            return True
        return False

    @staticmethod
    def _find_local_input_image_path(*, workflow: dict[str, Any], spec: ParamSpec, raw_overrides: dict[str, Any]) -> Path | None:
        for item in spec.params:
            target = item.target
            if target.mode != "direct" or not target.node_id or target.input != "image":
                continue
            value = raw_overrides.get(item.name)
            if not isinstance(value, str):
                continue
            path = Path(value).expanduser()
            if not path.exists() or not path.is_file():
                continue
            node = workflow.get(str(target.node_id))
            if not isinstance(node, dict):
                continue
            if str(node.get("class_type") or "").lower() != "loadimage":
                continue
            return path
        return None

    @staticmethod
    def _saveimage_prefix_param_names(workflow: dict[str, Any], spec: ParamSpec) -> set[str]:
        names: set[str] = set()
        for item in spec.params:
            target = item.target
            if target.mode != "direct" or not target.node_id or target.input != "filename_prefix":
                continue
            node = workflow.get(str(target.node_id))
            if not isinstance(node, dict):
                continue
            if str(node.get("class_type") or "").lower() != "saveimage":
                continue
            names.add(item.name)
        return names

    @staticmethod
    def _make_runtime_filename_prefix(workflow_id: str, semantic_label: str | None = None) -> str:
        if semantic_label:
            cleaned = re.sub(r"[^A-Za-z0-9]+", "", semantic_label).strip()
            if cleaned:
                stamp_ms = int(time.time() * 1000)
                suffix = uuid.uuid4().hex[:8]
                return f"{cleaned[:60]}_{stamp_ms}_{suffix}"
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", workflow_id).strip("_") or "workflow"
        stamp_ms = int(time.time() * 1000)
        suffix = uuid.uuid4().hex[:8]
        return f"mcp_{slug[:36]}_{stamp_ms}_{suffix}"

    async def _derive_semantic_filename_label(self, *, workflow: dict[str, Any], spec: ParamSpec, raw_overrides: dict[str, Any]) -> str | None:
        input_image = self._find_local_input_image_path(workflow=workflow, spec=spec, raw_overrides=raw_overrides)
        if input_image is not None:
            image_label = await self._semantic_name_from_image(input_image)
            if image_label:
                return image_label
        return self._semantic_name_from_overrides(raw_overrides)

    async def _semantic_name_from_image(self, image_path: Path) -> str | None:
        if not image_path.exists() or not image_path.is_file() or os.environ.get("PYTEST_CURRENT_TEST"):
            return None
        return await asyncio.to_thread(self._semantic_name_from_image_sync, image_path)

    def _semantic_name_from_image_sync(self, image_path: Path) -> str | None:
        if os.environ.get("COMFY_MCP_DISABLE_VISION_NAMING", "").strip().lower() in {"1", "true", "yes"}:
            return None
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            return None
        mime = self._mime_from_image_path(image_path)
        if mime is None:
            return None
        try:
            from openai import OpenAI  # type: ignore
        except Exception:
            return None
        image_bytes = image_path.read_bytes()
        data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        prompt = "Return a semantic filename label for this image as 2-6 CamelCase words. Return only the CamelCase label, no spaces/punctuation/quotes."
        client = OpenAI(api_key=api_key, timeout=12.0)
        for model in ("gpt-4o", "gpt-4o-mini"):
            try:
                response = client.chat.completions.create(model=model, temperature=0.1, max_tokens=32, messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": data_url}}]}])
                content = ""
                if response.choices:
                    message = response.choices[0].message
                    content = str(getattr(message, "content", "") or "").strip()
                label = self._to_camel_case_label(content)
                if label:
                    return label
            except Exception:
                continue
        return None

    @staticmethod
    def _mime_from_image_path(path: Path) -> str | None:
        return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}.get(path.suffix.lower())

    def _semantic_name_from_overrides(self, overrides: dict[str, Any]) -> str | None:
        candidates: list[tuple[int, int, str]] = []
        for key, value in overrides.items():
            if not isinstance(value, str):
                continue
            text = value.strip()
            if not text:
                continue
            lowered_key = key.lower()
            priority = 0
            if any(token in lowered_key for token in self._SEMANTIC_HINT_KEYWORDS):
                priority = 2
            elif len(text) <= 140 and (" " in text or "-" in text or "_" in text):
                priority = 1
            if priority > 0:
                candidates.append((priority, len(text), text))
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        for _, _, text in candidates:
            label = self._to_camel_case_label(text)
            if label:
                return label
        return None

    def _to_camel_case_label(self, value: str) -> str | None:
        tokens = re.findall(r"[A-Za-z0-9]+", value)
        if not tokens:
            return None
        filtered: list[str] = []
        for token in tokens:
            lower = token.lower()
            if lower in self._SEMANTIC_NAME_STOP_WORDS or lower in {"jpg", "jpeg", "png", "webp", "gif", "bmp"}:
                continue
            if len(token) == 1 and not token.isdigit():
                continue
            filtered.append(token)
        if not filtered:
            return None
        selected = filtered[:6]
        words = [token if token.isdigit() else token[0].upper() + token[1:].lower() for token in selected]
        label = "".join(words)
        return label[:60] if label else None

    async def status(self, prompt_id: str | None = None, include_progress: bool = True, progress_timeout_s: float = 0.75) -> dict[str, Any]:
        queue = await self._client.get_queue()
        history = await self._client.get_history(prompt_id=prompt_id) if prompt_id else {}
        running_ids, pending_ids = self._queue_prompt_ids(queue)
        state = self._derive_prompt_state(prompt_id, history, running_ids, pending_ids)
        response: dict[str, Any] = {
            "status": state,
            "prompt_id": prompt_id,
            "queue_running_count": len(running_ids),
            "queue_pending_count": len(pending_ids),
            "queue_running_prompt_ids": running_ids[:5],
            "queue_pending_prompt_ids": pending_ids[:5],
            "history_available": bool(history),
        }
        if include_progress and hasattr(self._client, "peek_progress"):
            try:
                progress = await self._client.peek_progress(prompt_id=prompt_id, timeout_s=progress_timeout_s)
                if progress:
                    response["progress"] = progress
            except Exception as exc:
                response["progress_error"] = str(exc)
        return response

    async def watch(self, prompt_id: str, inactivity_timeout_s: float = 120.0, max_wait_s: float = 1800.0, poll_ms: int = 500, include_history: bool = True) -> dict[str, Any]:
        if hasattr(self._client, "watch_prompt"):
            try:
                watched = await self._client.watch_prompt(prompt_id=prompt_id, inactivity_timeout_s=inactivity_timeout_s, max_wait_s=max_wait_s)
                response: dict[str, Any] = {"status": watched.get("status", "timeout"), "prompt_id": prompt_id, "events": watched.get("events", []), "reason": watched.get("reason"), "source": watched.get("source", "websocket")}
                history = watched.get("history", {}) if include_history else {}
                if include_history and not history and response["status"] == "complete":
                    history = await self._client.get_history(prompt_id=prompt_id)
                if include_history:
                    response["history"] = history
                return response
            except Exception:
                pass
        polled = await self.wait(prompt_id, timeout_s=inactivity_timeout_s, poll_ms=poll_ms)
        polled["prompt_id"] = prompt_id
        polled["source"] = "history_polling"
        if not include_history and "history" in polled:
            polled = dict(polled)
            polled.pop("history", None)
        return polled

    async def wait(self, prompt_id: str, timeout_s: float = 60.0, poll_ms: int = 500) -> dict[str, Any]:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            history = await self._client.get_history(prompt_id=prompt_id)
            if history:
                return {"status": "complete", "history": history}
            await asyncio.sleep(poll_ms / 1000.0)
        return {"status": "timeout", "history": {}}

    async def _wait_and_extract(self, prompt_id: str, timeout_s: float = 120.0, poll_ms: int = 1000, queued_workflow: dict[str, Any] | None = None, queued_at: float | None = None) -> dict[str, Any]:
        max_wait_s = max(timeout_s + 30.0, timeout_s * 8.0)
        watch_result = await self.watch(prompt_id, inactivity_timeout_s=timeout_s, max_wait_s=max_wait_s, poll_ms=poll_ms, include_history=True)
        if watch_result["status"] != "complete":
            return {"status": "timeout", "prompt_id": prompt_id, "output_images": [], "message": f"Workflow did not complete after {timeout_s}s without progress. You can continue watching with workflows_watch(prompt_id='{prompt_id}')."}
        history = watch_result.get("history", {})
        response = self._completion_from_history(prompt_id=prompt_id, history=history)
        progress_events = watch_result.get("events")
        if isinstance(progress_events, list) and progress_events:
            response["progress_events"] = progress_events[-50:]
            latest = progress_events[-1]
            if isinstance(latest, dict):
                response["latest_progress"] = latest
        if not response["output_images"] and queued_workflow:
            prefixes = self._collect_saveimage_prefixes(queued_workflow)
            inferred = self._infer_output_files(prefixes, queued_at=queued_at)
            if inferred:
                response["output_images"] = inferred
                response["output_images_source"] = "filesystem_fallback"
                existing_message = str(response.get("message", "")).strip()
                suffix = "Output records were inferred from local COMFY_MCP_COMFY_OUTPUT_DIR; verify prompt-to-file mapping if filename_prefix is generic."
                response["message"] = f"{existing_message} {suffix}".strip()
        return response

    @staticmethod
    def _queue_prompt_ids(queue_payload: dict[str, Any]) -> tuple[list[str], list[str]]:
        def _collect_prompt_ids(entries: Any) -> list[str]:
            ids: list[str] = []
            if not isinstance(entries, list):
                return ids
            for item in entries:
                if isinstance(item, dict):
                    prompt_id = item.get("prompt_id")
                    if isinstance(prompt_id, str) and prompt_id:
                        ids.append(prompt_id)
                elif isinstance(item, list):
                    for sub in item:
                        if isinstance(sub, str) and sub:
                            ids.append(sub)
                            break
            return ids
        return _collect_prompt_ids(queue_payload.get("queue_running")), _collect_prompt_ids(queue_payload.get("queue_pending"))

    @staticmethod
    def _derive_prompt_state(prompt_id: str | None, history: dict[str, Any], running_ids: list[str], pending_ids: list[str]) -> str:
        if prompt_id:
            if history:
                return "complete"
            if prompt_id in running_ids:
                return "running"
            if prompt_id in pending_ids:
                return "queued"
            return "unknown"
        if running_ids:
            return "running"
        if pending_ids:
            return "queued"
        return "idle"

    def _completion_from_history(self, prompt_id: str, history: dict[str, Any]) -> dict[str, Any]:
        output_images = []
        prompt_data = history.get(prompt_id, history)
        outputs = prompt_data.get("outputs", {})
        for _node_id, node_out in outputs.items():
            for img in node_out.get("images", []):
                filename = img.get("filename", "")
                subfolder = img.get("subfolder", "")
                img_type = img.get("type", "output")
                base = self._client._base_url.rstrip("/")
                query = {"filename": filename, "type": img_type}
                if subfolder:
                    query["subfolder"] = subfolder
                view_url = f"{base}/view?{urlencode(query)}"
                output_images.append({"filename": filename, "subfolder": subfolder, "type": img_type, "view_url": view_url})
        response: dict[str, Any] = {"status": "complete", "prompt_id": prompt_id, "output_images": output_images}
        if output_images:
            return response
        status_data = prompt_data.get("status", {})
        if isinstance(status_data, dict):
            status_str = str(status_data.get("status_str", "")).lower()
            completed = bool(status_data.get("completed"))
            cached_nodes = status_data.get("execution_cached")
            cached_count = len(cached_nodes) if isinstance(cached_nodes, list) else 0
            if completed or status_str in {"success", "complete", "completed"}:
                if cached_count > 0:
                    response["likely_cached"] = True
                    response["execution_cached_nodes"] = cached_nodes
                    response["message"] = "Workflow completed, but ComfyUI reported no output image records (likely cached execution). Retry with force=true and/or change seed."
                else:
                    response["message"] = "Workflow completed, but ComfyUI returned no output image records."
        return response

    @staticmethod
    def _collect_saveimage_prefixes(workflow: dict[str, Any]) -> list[str]:
        prefixes: list[str] = []
        for node in workflow.values():
            if not isinstance(node, dict):
                continue
            if str(node.get("class_type") or "").lower() != "saveimage":
                continue
            inputs = node.get("inputs")
            if not isinstance(inputs, dict):
                continue
            prefix = str(inputs.get("filename_prefix") or "").strip()
            if prefix:
                prefixes.append(prefix)
        return prefixes

    def _infer_output_files(self, filename_prefixes: list[str], queued_at: float | None, limit: int = 5) -> list[dict[str, Any]]:
        if self._comfy_output_dir is None or not self._comfy_output_dir.exists() or not self._comfy_output_dir.is_dir() or not filename_prefixes:
            return []
        patterns: list[str] = []
        for prefix in filename_prefixes:
            clean = prefix.strip()
            if clean:
                patterns.extend([f"{clean}*.png", f"{clean}*.jpg", f"{clean}*.jpeg", f"{clean}*.webp", f"{clean}*.PNG", f"{clean}*.JPG", f"{clean}*.JPEG", f"{clean}*.WEBP"])
        unique: dict[str, tuple[Path, str]] = {}
        for pattern in patterns:
            for candidate in self._comfy_output_dir.rglob(pattern):
                if candidate.is_file():
                    rel_parent = candidate.parent.relative_to(self._comfy_output_dir)
                    subfolder = "" if rel_parent == Path(".") else rel_parent.as_posix()
                    unique[str(candidate.resolve())] = (candidate, subfolder)
        files_with_subfolders = list(unique.values())
        if queued_at is not None:
            files_with_subfolders = [item for item in files_with_subfolders if item[0].stat().st_mtime >= (queued_at - 2.0)]
        if not files_with_subfolders:
            return []
        files_with_subfolders.sort(key=lambda item: item[0].stat().st_mtime, reverse=True)
        selected = files_with_subfolders[: max(1, limit)]
        base = self._client._base_url.rstrip("/")
        inferred: list[dict[str, Any]] = []
        for path, subfolder in selected:
            filename = path.name
            query = {"filename": filename, "type": "output"}
            if subfolder:
                query["subfolder"] = subfolder
            view_url = f"{base}/view?{urlencode(query)}"
            inferred.append({"filename": filename, "subfolder": subfolder, "type": "output", "view_url": view_url, "local_path": str(path), "inferred": True})
        return inferred

    async def run_aspect_ratio_adjustment(self, image_path: str, aspect_ratio: str, workflow_id: str = "aspect_ratio_adjustment", positive_prompt: str | None = None, negative_prompt: str | None = None, seed: int | None = None, output_base_name: str | None = None, client_id: str | None = None, token: str | None = None, upload_subfolder: str | None = None, overwrite: bool = False, wait_timeout_s: float = 300.0, wait_poll_ms: int = 1000) -> dict[str, Any]:
        self._policy.enforce_mutation(token)
        workflow = self._store.read_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow not found: {workflow_id}")
        image_file = Path(image_path)
        if not image_file.exists():
            raise ValueError(f"Image file not found: {image_path}")
        upload_result = await self._client.upload_image(str(image_file), image_type="input", subfolder=upload_subfolder, overwrite=overwrite)
        uploaded_name = upload_result.get("name")
        uploaded_subfolder = upload_result.get("subfolder", "")
        if not uploaded_name:
            raise ValueError("Image upload did not return a filename")
        object_info = await self._client.get_object_info()
        flux_node = object_info.get("FluxResolutionNode") or {}
        flux_inputs = flux_node.get("input", {})
        aspect_entry = flux_inputs.get("required", {}).get("aspect_ratio") or flux_inputs.get("optional", {}).get("aspect_ratio")
        allowed_aspects = aspect_entry[0] if isinstance(aspect_entry, list) and aspect_entry else []
        ratio_value = aspect_ratio.split(" ")[0]
        normalized_ratio_value = self._image_service.normalize_ratio_token(ratio_value) or ratio_value.replace(" ", "")
        is_plain_ratio_token = bool(re.fullmatch(r"\d+\s*:\s*\d+", aspect_ratio.strip()))
        is_custom_ratio = bool(re.fullmatch(r"\d+\s*:\s*\d+", ratio_value))
        normalized_custom_ratio = normalized_ratio_value
        matched_allowed_aspect = self._image_service.match_allowed_aspect_choice(aspect_ratio, allowed_aspects)
        if allowed_aspects and not matched_allowed_aspect and not is_custom_ratio:
            raise ValueError(f"aspect_ratio '{aspect_ratio}' not in allowed list: {allowed_aspects}")
        patched = json.loads(json.dumps(workflow))
        def _sanitize_base(value: str) -> str:
            cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
            return cleaned or "ComfyUI"
        def _strip_comfy_counter_suffixes(value: str) -> str:
            trimmed = value
            while True:
                candidate = re.sub(r"(?:__|_)\d{5}_$", "", trimmed)
                if candidate == trimmed:
                    break
                trimmed = candidate.rstrip("._-")
            return trimmed or value
        if "109" in patched and isinstance(patched["109"], dict):
            patched["109"].setdefault("inputs", {})["image"] = uploaded_name
            if uploaded_subfolder:
                patched["109"]["inputs"]["subfolder"] = uploaded_subfolder
        if "79" in patched and isinstance(patched["79"], dict):
            inputs = patched["79"].setdefault("inputs", {})
            ratio_slug = normalized_custom_ratio.replace(":", "x").lower()
            semantic_base = None
            if output_base_name is None:
                semantic_base = await self._semantic_name_from_image(image_file)
                if not semantic_base:
                    semantic_base = self._semantic_name_from_overrides({"positive_prompt": positive_prompt or "", "negative_prompt": negative_prompt or ""})
            base_raw = output_base_name if output_base_name is not None else (semantic_base or Path(uploaded_name).stem)
            base_clean = _sanitize_base(_strip_comfy_counter_suffixes(base_raw))
            prefix = f"{base_clean}__{ratio_slug}"
            if seed is not None:
                prefix = f"{prefix}__s{int(seed)}"
            prefix = f"{prefix}__{uuid.uuid4().hex[:8]}"
            inputs["filename_prefix"] = prefix[:120].rstrip("._-") or "ComfyUI"
        if "115" in patched and isinstance(patched["115"], dict):
            inputs = patched["115"].setdefault("inputs", {})
            if is_plain_ratio_token:
                inputs["aspect_ratio"] = matched_allowed_aspect or (allowed_aspects[0] if allowed_aspects else "1:1 (Perfect Square)")
                inputs["custom_ratio"] = True
                inputs["custom_aspect_ratio"] = normalized_custom_ratio
            elif matched_allowed_aspect:
                inputs["aspect_ratio"] = matched_allowed_aspect
                inputs["custom_ratio"] = True
                inputs["custom_aspect_ratio"] = normalized_custom_ratio or ratio_value
            elif is_custom_ratio:
                inputs["aspect_ratio"] = allowed_aspects[0] if allowed_aspects else "1:1 (Perfect Square)"
                inputs["custom_ratio"] = True
                inputs["custom_aspect_ratio"] = normalized_custom_ratio
        if positive_prompt is not None and "113" in patched:
            patched["113"].setdefault("inputs", {})["prompt"] = positive_prompt
        if negative_prompt is not None and "114" in patched:
            patched["114"].setdefault("inputs", {})["prompt"] = negative_prompt
        if seed is not None and "3" in patched:
            patched["3"].setdefault("inputs", {})["seed"] = int(seed)
        queued_at = time.time()
        queue_result = await self._client.queue_prompt(patched, client_id=client_id)
        prompt_id = queue_result.get("prompt_id")
        if not prompt_id:
            return {"workflow_id": workflow_id, "error": "Failed to queue prompt", "queue_result": queue_result}
        completion = await self._wait_and_extract(prompt_id, timeout_s=float(wait_timeout_s), poll_ms=max(100, int(wait_poll_ms)), queued_workflow=patched, queued_at=queued_at)
        completion["workflow_id"] = workflow_id
        completion["aspect_ratio"] = aspect_ratio
        completion["aspect_ratio_applied"] = normalized_custom_ratio if is_plain_ratio_token else (matched_allowed_aspect or normalized_custom_ratio)
        if is_plain_ratio_token:
            completion["aspect_ratio_anchor"] = matched_allowed_aspect or (allowed_aspects[0] if allowed_aspects else "")
        if "79" in patched and isinstance(patched["79"], dict):
            save_inputs = patched["79"].get("inputs", {})
            if isinstance(save_inputs, dict):
                completion["filename_prefix_used"] = str(save_inputs.get("filename_prefix") or "")
        return completion
