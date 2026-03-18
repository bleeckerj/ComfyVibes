"""Binary transfer and auto-upload helpers for the orchestrator."""

from __future__ import annotations

import asyncio
import base64
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from comfy_mcp.tui_client.search_semantics import normalize_photarium_upload_arguments


class BinaryTransferAdapter:
    async def maybe_convert_upload_url_to_from_path(
        self,
        orchestrator: Any,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> tuple[str, Dict[str, Any], Path] | None:
        lowered = tool_name.lower()
        if "upload" not in lowered or ("photarium" not in lowered and "catalog" not in lowered) or "upload_from_path" in lowered:
            return None
        url_value = self.extract_upload_url_value(arguments)
        if not url_value:
            return None
        from_path_tool = self.paired_upload_from_path_tool(orchestrator, tool_name)
        if not from_path_tool:
            return None
        path_key = self.select_upload_path_key(orchestrator._tool_input_schema_by_name.get(from_path_tool))
        if not path_key:
            return None
        name_schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        seed_args = normalize_photarium_upload_arguments(tool_name, arguments, name_schema, fallback_text=user_text)
        seed_label = str(seed_args.get("name") or "UploadedImage")
        downloaded = await orchestrator._download_url_to_temp_file(url_value, seed_label)
        if downloaded is None:
            return None
        vision_label = await orchestrator._vision_semantic_label_from_image(downloaded)
        final_label = self.safe_filename_stem(vision_label or seed_label)
        renamed = downloaded
        target = downloaded.with_name(f"{final_label}{downloaded.suffix or '.png'}")
        if target != downloaded:
            if target.exists():
                target = downloaded.with_name(f"{final_label}_{uuid.uuid4().hex[:6]}{downloaded.suffix or '.png'}")
            downloaded.rename(target)
            renamed = target
        from_path_schema = orchestrator._tool_input_schema_by_name.get(from_path_tool)
        new_args = dict(arguments)
        for url_key in ("url", "imageUrl", "image_url", "view_url", "viewUrl"):
            new_args.pop(url_key, None)
        new_args[path_key] = str(renamed)
        new_args["name"] = final_label
        new_args = normalize_photarium_upload_arguments(from_path_tool, new_args, from_path_schema, fallback_text=user_text)
        return from_path_tool, new_args, renamed

    def apply_tanktracks_upload_conventions(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> Dict[str, Any]:
        if "TANK TRACKS FLOW REQUEST" not in str(user_text):
            return arguments
        if not is_photarium_like_upload_tool(tool_name):
            return arguments
        normalized = dict(arguments)
        normalized = self.ensure_upload_tags(normalized, required_tags=["tank tracks", "caterpillar tracks", "tracks"])
        self.drop_generic_tanktracks_display_name(normalized)
        return normalized

    @staticmethod
    def ensure_upload_tags(arguments: Dict[str, Any], required_tags: list[str]) -> Dict[str, Any]:
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
    def drop_generic_tanktracks_display_name(arguments: Dict[str, Any]) -> None:
        generic_labels = {"addtanktracks", "tanktracks", "tanktracksflowrequest", "tanktracksflow"}
        for key in ("name", "title"):
            raw = arguments.get(key)
            if not isinstance(raw, str):
                continue
            compact = re.sub(r"[^a-z0-9]+", "", raw.lower())
            if compact in generic_labels:
                arguments.pop(key, None)

    @staticmethod
    def paired_upload_from_path_tool(orchestrator: Any, upload_url_tool: str) -> str | None:
        candidates: list[str] = []
        if "upload_url" in upload_url_tool:
            candidates.append(upload_url_tool.replace("upload_url", "upload_from_path"))
        if "upload_image" in upload_url_tool:
            candidates.append(upload_url_tool.replace("upload_image", "upload_from_path"))
        if upload_url_tool.startswith("photarium_"):
            candidates.extend(["photarium_upload_from_path", "photarium_upload_image"])
        if upload_url_tool.startswith("catalog_"):
            candidates.extend(["catalog_upload_from_path", "catalog_upload_image"])
        for candidate in candidates:
            if candidate in orchestrator._tool_input_schema_by_name:
                return candidate
        return None

    @staticmethod
    def extract_upload_url_value(arguments: Dict[str, Any]) -> str | None:
        for key in ("url", "imageUrl", "image_url", "view_url", "viewUrl"):
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def select_upload_path_key(schema: Dict[str, Any] | None) -> str | None:
        if not isinstance(schema, dict):
            return None
        props = schema.get("properties")
        if not isinstance(props, dict):
            return None
        for key in ("filePath", "file_path", "path", "localPath", "local_path", "imagePath", "image_path"):
            if key in props:
                return key
        return None

    async def download_url_to_temp_file(self, url: str, label: str) -> Path | None:
        ext = self.guess_extension_from_url(url) or ".png"
        stem = self.safe_filename_stem(label)
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
    def guess_extension_from_url(url: str) -> str | None:
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
    def safe_filename_stem(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9]+", "", str(value or "")).strip()
        return cleaned[:64] or "UploadedImage"

    def normalize_photarium_upload_namespace(
        self,
        orchestrator: Any,
        tool_name: str,
        arguments: Dict[str, Any],
        input_schema: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        lowered = tool_name.lower()
        if "upload" not in lowered or ("photarium" not in lowered and "catalog" not in lowered):
            return arguments
        if not self.schema_supports_namespace(input_schema) and "namespace" not in arguments:
            return arguments
        normalized = dict(arguments)
        explicit = str(normalized.get("namespace") or "").strip()
        if explicit:
            return normalized
        inferred = self.infer_namespace_from_upload_arguments(orchestrator, normalized)
        normalized["namespace"] = inferred or "cf-default"
        return normalized

    @staticmethod
    def schema_supports_namespace(input_schema: Dict[str, Any] | None) -> bool:
        if not isinstance(input_schema, dict):
            return False
        properties = input_schema.get("properties")
        return isinstance(properties, dict) and "namespace" in properties

    @staticmethod
    def infer_namespace_from_upload_arguments(orchestrator: Any, arguments: Dict[str, Any]) -> str | None:
        for candidate_id in orchestrator._candidate_image_ids(arguments):
            namespace = orchestrator._image_namespaces.namespace_for(candidate_id)
            if namespace:
                return namespace
        return None

    async def maybe_auto_upload_workflow_outputs(
        self,
        orchestrator: Any,
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
        upload_tool = self.select_auto_upload_tool_name(orchestrator)
        if not upload_tool:
            return {"status": "failed", "reason": "photarium_upload_tool_unavailable"}
        upload_schema = orchestrator._tool_input_schema_by_name.get(upload_tool)
        upload_path_key = self.select_upload_local_path_key(upload_schema)
        if not upload_path_key:
            return {"status": "failed", "reason": "upload_tool_missing_path_parameter", "tool": upload_tool}
        items: list[Dict[str, Any]] = []
        uploaded_count = 0
        for image_payload in output_images:
            if not isinstance(image_payload, dict):
                continue
            filename = str(image_payload.get("filename") or "").strip()
            local_path, path_source = await self.resolve_output_image_local_path(orchestrator, image_payload)
            if not local_path:
                items.append({"filename": filename or None, "status": "failed", "error": "could_not_resolve_local_output_path"})
                continue
            upload_args = await self.build_auto_upload_arguments(
                orchestrator,
                upload_tool_name=upload_tool,
                upload_schema=upload_schema,
                upload_path_key=upload_path_key,
                local_path=local_path,
                image_payload=image_payload,
                tool_arguments=tool_arguments,
                user_text=user_text,
            )
            try:
                async with orchestrator._tool_execution_lock:
                    upload_result = await orchestrator._router.call_tool(upload_tool, upload_args)
                uploaded_count += 1
                orchestrator._record_image_namespaces_from_result(upload_result)
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
        return {"status": status, "mode": "automatic", "tool": upload_tool, "items": items}

    @staticmethod
    def select_auto_upload_tool_name(orchestrator: Any) -> str | None:
        for candidate in ("photarium_upload_from_path", "catalog_upload_from_path", "photarium_upload_image", "catalog_upload_image"):
            if candidate in orchestrator._tool_input_schema_by_name:
                return candidate
        return None

    @staticmethod
    def select_upload_local_path_key(schema: Dict[str, Any] | None) -> str | None:
        if not isinstance(schema, dict):
            return None
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return None
        for key in ("filePath", "file_path", "path", "localPath", "local_path", "imagePath", "image_path"):
            if key in properties:
                return key
        return None

    async def resolve_output_image_local_path(self, orchestrator: Any, image_payload: Dict[str, Any]) -> tuple[str | None, str | None]:
        local_path = str(image_payload.get("local_path") or "").strip()
        if local_path:
            return local_path, "local_path"
        if "comfy_download_image" not in orchestrator._tool_input_schema_by_name:
            return None, None
        filename = str(image_payload.get("filename") or "").strip()
        if not filename:
            return None, None
        schema = orchestrator._tool_input_schema_by_name.get("comfy_download_image")
        payload: Dict[str, Any] = {"filename": filename}
        if orchestrator._schema_has_property(schema, "image_type"):
            image_type = str(image_payload.get("type") or image_payload.get("image_type") or "output")
            payload["image_type"] = image_type
        if orchestrator._schema_has_property(schema, "subfolder"):
            subfolder = str(image_payload.get("subfolder") or "").strip()
            if subfolder:
                payload["subfolder"] = subfolder
        if orchestrator._schema_has_property(schema, "save_path"):
            payload["save_path"] = str(Path(tempfile.gettempdir()) / f"{uuid.uuid4().hex[:8]}_{filename}")
        try:
            async with orchestrator._tool_execution_lock:
                downloaded = await orchestrator._router.call_tool("comfy_download_image", payload)
        except Exception:
            return None, None
        if isinstance(downloaded, dict):
            for key in ("local_path", "savedPath", "savePath", "filePath", "path"):
                value = downloaded.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip(), "comfy_download_image"
        return None, None

    async def build_auto_upload_arguments(
        self,
        orchestrator: Any,
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
        stem = self.safe_filename_stem(Path(filename).stem if filename else Path(local_path).stem)
        if orchestrator._schema_has_property(upload_schema, "name"):
            args["name"] = stem
        if orchestrator._schema_has_property(upload_schema, "title"):
            args["title"] = stem
        if orchestrator._schema_has_property(upload_schema, "namespace"):
            args["namespace"] = "cf-default"
        overrides = tool_arguments.get("overrides") if isinstance(tool_arguments, dict) else None
        if isinstance(overrides, dict):
            prompt_value = overrides.get("positive_prompt") or overrides.get("prompt")
            if isinstance(prompt_value, str) and prompt_value.strip():
                if orchestrator._schema_has_property(upload_schema, "prompt"):
                    args["prompt"] = prompt_value.strip()
                if orchestrator._schema_has_property(upload_schema, "positive_prompt"):
                    args["positive_prompt"] = prompt_value.strip()
        source_context = await self.resolve_auto_upload_source_context(
            orchestrator,
            tool_arguments=tool_arguments,
            user_text=user_text,
        )
        parent_key = self.select_parent_id_key(upload_schema)
        if parent_key and source_context.get("effective_parent_id"):
            args[parent_key] = source_context["effective_parent_id"]
        if orchestrator._schema_has_property(upload_schema, "namespace") and source_context.get("namespace"):
            args["namespace"] = source_context["namespace"]
        return normalize_photarium_upload_arguments(upload_tool_name, args, upload_schema, fallback_text=user_text)

    async def resolve_auto_upload_source_context(
        self,
        orchestrator: Any,
        *,
        tool_arguments: Dict[str, Any],
        user_text: str,
    ) -> Dict[str, str]:
        source_image_id = self.infer_source_image_id_from_workflow_args(orchestrator, tool_arguments, user_text=user_text)
        if not source_image_id:
            return {}
        namespace = orchestrator._image_namespaces.namespace_for(source_image_id)
        effective_parent_id = source_image_id
        source_metadata = await self.fetch_source_image_metadata(orchestrator, source_image_id)
        if isinstance(source_metadata, dict):
            effective_parent_id = self.resolve_effective_parent_id(source_metadata, fallback_source_image_id=source_image_id)
            source_namespace = self._first_non_empty_string(source_metadata, "namespace")
            if source_namespace:
                namespace = source_namespace
        return {
            "source_image_id": source_image_id,
            "effective_parent_id": effective_parent_id,
            "namespace": namespace or "",
        }

    async def fetch_source_image_metadata(self, orchestrator: Any, source_image_id: str) -> Dict[str, Any] | None:
        for tool_name in ("photarium_get", "catalog_get"):
            if tool_name not in orchestrator._tool_input_schema_by_name:
                continue
            schema = orchestrator._tool_input_schema_by_name.get(tool_name)
            image_id_key = self.select_source_image_id_key(schema)
            if not image_id_key:
                continue
            try:
                async with orchestrator._tool_execution_lock:
                    payload = await orchestrator._router.call_tool(tool_name, {image_id_key: source_image_id})
            except Exception:
                continue
            if isinstance(payload, dict):
                orchestrator._record_image_namespaces_from_result(payload)
                return payload
        return None

    def infer_source_image_id_from_workflow_args(
        self,
        orchestrator: Any,
        tool_arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> str | None:
        overrides = tool_arguments.get("overrides") if isinstance(tool_arguments, dict) else None
        if isinstance(overrides, dict):
            for value in overrides.values():
                if not isinstance(value, str):
                    continue
                source_image_id = orchestrator._source_image_id_for_local_path(value)
                if source_image_id:
                    return source_image_id
        for candidate in (
            orchestrator._extract_flow_source_image_id(user_text),
            orchestrator._extract_variation_source_image_id(user_text),
            orchestrator._extract_shorthand_edit_image_id(user_text),
        ):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return None

    @staticmethod
    def select_parent_id_key(schema: Dict[str, Any] | None) -> str | None:
        if not isinstance(schema, dict):
            return None
        props = schema.get("properties")
        if not isinstance(props, dict):
            return None
        for key in ("parentId", "parent_id", "variantOf", "variant_of"):
            if key in props:
                return key
        return None

    @staticmethod
    def select_source_image_id_key(schema: Dict[str, Any] | None) -> str | None:
        if not isinstance(schema, dict):
            return "imageId"
        props = schema.get("properties")
        if not isinstance(props, dict):
            return "imageId"
        for key in ("imageId", "image_id", "id", "uuid"):
            if key in props:
                return key
        return None

    @staticmethod
    def _first_non_empty_string(payload: Dict[str, Any], *keys: str) -> str | None:
        for key in keys:
            value = payload.get(key)
            if not isinstance(value, str):
                continue
            text = value.strip()
            if text:
                return text
        return None

    @classmethod
    def resolve_effective_parent_id(cls, source_metadata: Dict[str, Any], *, fallback_source_image_id: str) -> str:
        parent_id = cls._first_non_empty_string(source_metadata, "parentId", "parent_id", "variantOf", "variant_of")
        if parent_id:
            return parent_id
        family_root_id = cls._first_non_empty_string(source_metadata, "familyRootId", "family_root_id")
        if family_root_id:
            return family_root_id
        return fallback_source_image_id

    async def vision_semantic_label_from_image(self, image_path: Path) -> str | None:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return None
        if os.environ.get("COMFY_MCP_DISABLE_VISION_NAMING", "").strip().lower() in {"1", "true", "yes"}:
            return None
        if not image_path.exists() or not image_path.is_file():
            return None
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            return None
        return await asyncio.to_thread(self.vision_semantic_label_from_image_sync, image_path, api_key)

    def vision_semantic_label_from_image_sync(self, image_path: Path, api_key: str) -> str | None:
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
        prompt = "Return a semantic filename label for this image as 2-6 CamelCase words. Return only the CamelCase label, no spaces or punctuation."
        client = OpenAI(api_key=api_key, timeout=12.0)
        for model in ("gpt-4o", "gpt-4o-mini"):
            try:
                response = client.chat.completions.create(
                    model=model,
                    temperature=0.1,
                    max_tokens=32,
                    messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": data_url}}]}],
                )
                content = ""
                if response.choices:
                    content = str(getattr(response.choices[0].message, "content", "") or "").strip()
                label = self.safe_filename_stem(content)
                if label and label != "UploadedImage":
                    return label
            except Exception:
                continue
        return None


def is_photarium_like_upload_tool(tool_name: str) -> bool:
    lowered = str(tool_name or "").lower()
    return "upload" in lowered and ("photarium" in lowered or "catalog" in lowered)
