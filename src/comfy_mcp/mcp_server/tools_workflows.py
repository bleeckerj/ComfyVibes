"""Workflow MCP tool handlers."""

from __future__ import annotations

import json
import asyncio
import re
import time
import httpx
import uuid
from math import gcd
from math import log
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from pathlib import Path

from comfy_mcp.comfy_client.client import ComfyClient
from comfy_mcp.extraction.adapter import WorkflowExtractor
from comfy_mcp.extraction.normalize import detect_workflow_format, ui_to_api_format
from comfy_mcp.mcp_server.policy import Policy
from comfy_mcp.params.patch import patch_workflow
from comfy_mcp.params.schema import ParamSpec
from comfy_mcp.reasoning.service import WorkflowReasoningService
from comfy_mcp.workflow_store.meta import build_meta
from comfy_mcp.workflow_store.errors import WorkflowNotFoundError
from comfy_mcp.workflow_store.packaging import build_hints_template, package_workflow
from comfy_mcp.workflow_store.store import WorkflowStore


class WorkflowTools:
    """Tool handlers for workflow library operations."""

    _SEARCH_STOP_WORDS = {
        "a",
        "an",
        "and",
        "can",
        "for",
        "i",
        "is",
        "me",
        "must",
        "my",
        "of",
        "params",
        "parameter",
        "parameters",
        "please",
        "provide",
        "show",
        "the",
        "what",
        "workflow",
        "workflows",
        "with",
        "you",
    }
    _IMAGE_EDIT_QUERY_TERMS = {
        "edit",
        "editing",
        "img2img",
        "inpaint",
        "mask",
        "modify",
        "replace",
        "retouch",
        "restyle",
        "variant",
    }
    _IMAGE_EDIT_TAGS = {"image-edit", "img2img"}
    _IMAGE_EDIT_PREFERRED_WORKFLOWS = {
        "flux_2_klein_4B": 2.5,
    }

    def __init__(
        self,
        store: WorkflowStore,
        client: ComfyClient,
        policy: Policy,
        extractor: Optional[WorkflowExtractor] = None,
        reasoning: Optional[WorkflowReasoningService] = None,
        comfy_output_dir: Optional[Path] = None,
    ) -> None:
        self._store = store
        self._client = client
        self._policy = policy
        self._extractor = extractor
        self._reasoning = reasoning or WorkflowReasoningService(store)
        self._comfy_output_dir = comfy_output_dir.expanduser().resolve() if comfy_output_dir else None

    def extract_from_artifact(self, path: str, preserve_format: bool = False) -> Dict[str, Any]:
        """Extract workflow from an image or video artifact."""
        extractor = self._get_extractor()
        result = extractor.extract_from_path(path, preserve_format=preserve_format)
        return {
            "workflow": result.workflow,
            "workflow_format": result.workflow_format,
            "raw_metadata": result.raw_metadata,
        }

    def import_from_artifact(
        self,
        path: str,
        workflow_id: str,
        name: Optional[str] = None,
        tags: Optional[list[str]] = None,
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Extract workflow from artifact and save to the store."""
        self._policy.enforce_mutation(token)
        extractor = self._get_extractor()
        result = extractor.extract_from_path(path, preserve_format=False)
        payload_bytes = len(json.dumps(result.workflow).encode("utf-8"))
        self._policy.enforce_payload_size(payload_bytes)

        try:
            existing_meta = self._store.read_meta(workflow_id)
        except WorkflowNotFoundError:
            existing_meta = None

        hints: Dict[str, Any] = {"description": "Imported from artifact"}
        if name:
            hints["name"] = name
        if tags:
            hints["tags"] = tags

        packaged = package_workflow(
            workflow_id,
            result.workflow,
            existing_meta=existing_meta,
            hints=hints,
            include_suggestions=True,
        )
        self._store.save_workflow(
            workflow_id,
            result.workflow,
            meta=packaged["meta"],
            params=packaged["params"],
        )
        return {
            "id": workflow_id,
            "params_count": len(packaged["params"].get("params", [])),
        }

    def _get_extractor(self) -> WorkflowExtractor:
        if self._extractor is None:
            self._extractor = WorkflowExtractor()
        return self._extractor

    def _resolve_under_root(self, relative_path: str) -> Path:
        root = self._store.root.resolve()
        target = (root / relative_path).resolve()
        if root not in target.parents and target != root:
            raise ValueError("Path escapes workflows root")
        return target

    @classmethod
    def _normalize_search_text(cls, value: str) -> str:
        text = value.lower()
        text = re.sub(r"\btext\s*[-_\s]?(?:to|2)\s*[-_\s]?image\b", " txt2img text2image ", text)
        text = re.sub(r"\btxt\s*[-_\s]?2\s*[-_\s]?img\b", " txt2img text2image ", text)
        text = re.sub(r"\btxt2img\b", " txt2img text2image ", text)
        text = re.sub(r"\bt2i\b", " txt2img text2image ", text)
        text = re.sub(r"[^a-z0-9]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @classmethod
    def _search_tokens(cls, value: str) -> list[str]:
        normalized = cls._normalize_search_text(value)
        raw_tokens = [token for token in normalized.split(" ") if token]
        filtered_tokens = [token for token in raw_tokens if token not in cls._SEARCH_STOP_WORDS]
        return filtered_tokens or raw_tokens

    @classmethod
    def _has_image_edit_intent(cls, query: str) -> bool:
        tokens = set(cls._search_tokens(query))
        if any(token in tokens for token in cls._IMAGE_EDIT_QUERY_TERMS):
            return True
        query_text = query.lower()
        return "image edit" in query_text or "edit image" in query_text

    @classmethod
    def _image_edit_priority_boost(
        cls,
        *,
        workflow_id: str,
        name: str,
        description: str,
        tags: list[str],
    ) -> float:
        boost = 0.0
        tag_set = {str(tag).lower() for tag in tags}
        if tag_set.intersection(cls._IMAGE_EDIT_TAGS):
            boost += 1.5
        text_blob = f"{workflow_id} {name} {description}".lower()
        if "edit" in text_blob:
            boost += 0.5
        boost += cls._IMAGE_EDIT_PREFERRED_WORKFLOWS.get(workflow_id, 0.0)
        return boost

    @staticmethod
    def _token_matches(query_token: str, hay_tokens: set[str]) -> bool:
        if query_token in hay_tokens:
            return True
        if len(query_token) < 3:
            return False
        return any(
            token.startswith(query_token) or query_token.startswith(token)
            for token in hay_tokens
        )

    def list(self) -> Dict[str, Any]:
        """List workflows in the store with descriptions."""
        entries = self._store.list_entries()
        primary_root = self._store.root.resolve()
        items = []
        for entry in entries:
            meta = self._store.read_meta(entry.workflow_id) or {}
            params_count = 0
            if entry.has_params:
                params_payload = self._store.read_params(entry.workflow_id) or {}
                param_items = params_payload.get("params")
                if isinstance(param_items, list):
                    params_count = len(param_items)
            source_root = entry.root.parent.resolve()
            items.append({
                "id": entry.workflow_id,
                "name": str(meta.get("name") or entry.workflow_id),
                "description": str(meta.get("description") or ""),
                "tags": [str(t) for t in (meta.get("tags") or [])],
                "has_meta": entry.has_meta,
                "has_params": entry.has_params,
                "params_count": params_count,
                "source_root": str(source_root),
                "source_path": str(entry.root),
                "is_primary_root": source_root == primary_root,
            })
        return {
            "primary_root": str(primary_root),
            "extra_roots": [str(root) for root in self._store.roots[1:]],
            "workflows": items,
        }

    def search(
        self,
        query: str,
        limit: int = 20,
        tags: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        """Search workflows by id, name, description, or tags."""
        entries = self._store.list_entries()
        normalized_query = self._normalize_search_text(query)
        query_tokens = self._search_tokens(query)
        image_edit_intent = self._has_image_edit_intent(query)
        tag_filter = [tag.lower() for tag in (tags or [])]
        ranked_results: list[tuple[float, Dict[str, Any]]] = []

        for entry in entries:
            meta = self._store.read_meta(entry.workflow_id) or {}
            name = str(meta.get("name") or entry.workflow_id)
            description = str(meta.get("description") or "")
            meta_tags = [str(tag) for tag in (meta.get("tags") or [])]

            if tag_filter and not any(tag.lower() in tag_filter for tag in meta_tags):
                continue

            haystack = " ".join([entry.workflow_id, name, description, " ".join(meta_tags)])
            normalized_haystack = self._normalize_search_text(haystack)
            haystack_tokens = set(normalized_haystack.split(" "))

            exact_match = bool(normalized_query) and normalized_query in normalized_haystack
            matched_tokens = sum(
                1 for token in query_tokens
                if self._token_matches(token, haystack_tokens)
            )
            required_matches = 1 if len(query_tokens) <= 2 else max(2, (len(query_tokens) + 1) // 2)
            intent_boost = (
                self._image_edit_priority_boost(
                    workflow_id=entry.workflow_id,
                    name=name,
                    description=description,
                    tags=meta_tags,
                )
                if image_edit_intent
                else 0.0
            )

            if not exact_match and matched_tokens < required_matches:
                if not (image_edit_intent and intent_boost > 0):
                    continue

            score = 1.0 if exact_match else (matched_tokens / max(1, len(query_tokens)))
            if image_edit_intent:
                score += intent_boost
            ranked_results.append(
                (
                    score,
                    {
                        "id": entry.workflow_id,
                        "name": name,
                        "description": description,
                        "tags": meta_tags,
                        "has_meta": entry.has_meta,
                        "has_params": entry.has_params,
                    },
                )
            )

        ranked_results.sort(key=lambda item: (-item[0], item[1]["id"]))
        results = [item[1] for item in ranked_results[: max(1, limit)]]

        return {
            "query": query,
            "count": len(results),
            "workflows": results,
        }

    def capabilities_list(
        self,
        query: str = "",
        limit: int = 20,
        tags: Optional[list[str]] = None,
        include_params: bool = False,
    ) -> Dict[str, Any]:
        """List workflow capability cards for LLM/tool discovery."""
        return self._reasoning.list_capabilities(
            query=query,
            limit=limit,
            tags=tags,
            include_params=include_params,
        )

    def capabilities_get(self, workflow_id: str, include_params: bool = True) -> Dict[str, Any]:
        """Return one workflow capability card by workflow id."""
        return self._reasoning.get_capability(workflow_id, include_params=include_params)

    def get(self, workflow_id: str) -> Dict[str, Any]:
        """Return workflow JSON and metadata."""
        workflow = self._store.read_workflow(workflow_id)
        return {
            "workflow": workflow,
            "meta": self._store.read_meta(workflow_id),
            "params": self._store.read_params(workflow_id),
        }

    def params_get(self, workflow_id: str) -> Dict[str, Any]:
        """Return params.json for a workflow id."""
        params = self._store.read_params(workflow_id)
        return {"params": params}

    def package(
        self,
        workflow_id: str,
        hints: Optional[Dict[str, Any]] = None,
        include_suggestions: bool = True,
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Regenerate params + metadata for one stored workflow."""
        self._policy.enforce_mutation(token)
        workflow = self._store.read_workflow(workflow_id)
        existing_meta = self._store.read_meta(workflow_id)
        packaged = package_workflow(
            workflow_id,
            workflow,
            existing_meta=existing_meta,
            hints=hints or {},
            include_suggestions=include_suggestions,
        )
        self._store.save_workflow(
            workflow_id,
            workflow,
            meta=packaged["meta"],
            params=packaged["params"],
        )
        return {
            "workflow_id": workflow_id,
            "params_count": len(packaged["params"].get("params", [])),
            "packaged": True,
        }

    def package_many(
        self,
        workflow_ids: Optional[list[str]] = None,
        hints_by_workflow: Optional[Dict[str, Dict[str, Any]]] = None,
        include_suggestions: bool = True,
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Regenerate params + metadata for many workflows."""
        self._policy.enforce_mutation(token)
        target_ids = workflow_ids or [entry.workflow_id for entry in self._store.list_entries()]
        hints_map = hints_by_workflow or {}
        packaged: list[Dict[str, Any]] = []
        errors: list[Dict[str, str]] = []
        for workflow_id in target_ids:
            try:
                workflow = self._store.read_workflow(workflow_id)
                existing_meta = self._store.read_meta(workflow_id)
                item = package_workflow(
                    workflow_id,
                    workflow,
                    existing_meta=existing_meta,
                    hints=hints_map.get(workflow_id, {}),
                    include_suggestions=include_suggestions,
                )
                self._store.save_workflow(
                    workflow_id,
                    workflow,
                    meta=item["meta"],
                    params=item["params"],
                )
                packaged.append(
                    {
                        "workflow_id": workflow_id,
                        "params_count": len(item["params"].get("params", [])),
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive aggregation
                errors.append({"workflow_id": workflow_id, "error": str(exc)})

        return {
            "requested": len(target_ids),
            "packaged_count": len(packaged),
            "packaged": packaged,
            "errors": errors,
        }

    def package_template_get(self, workflow_id: str) -> Dict[str, Any]:
        """Return an editable metadata-hints template for one workflow."""
        workflow = self._store.read_workflow(workflow_id)
        existing_meta = self._store.read_meta(workflow_id)
        template = build_hints_template(workflow_id, workflow, existing_meta=existing_meta)
        return {"workflow_id": workflow_id, "hints_template": template}

    def folder_create(self, folder_path: str, token: Optional[str] = None) -> Dict[str, Any]:
        """Create a folder under the primary workflows directory."""
        self._policy.enforce_mutation(token)
        target = self._resolve_under_root(folder_path)
        target.mkdir(parents=True, exist_ok=True)
        return {"created": True, "path": str(target)}

    def file_write(
        self,
        file_path: str,
        payload: Dict[str, Any],
        overwrite: bool = False,
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Write a JSON file under the primary workflows directory."""
        self._policy.enforce_mutation(token)
        target = self._resolve_under_root(file_path)
        if target.exists() and not overwrite:
            raise ValueError(f"File already exists: {file_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        payload_bytes = len(json.dumps(payload).encode("utf-8"))
        self._policy.enforce_payload_size(payload_bytes)
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return {"written": True, "path": str(target)}

    def file_edit(
        self,
        file_path: str,
        updates: Dict[str, Any],
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Edit a JSON file under the primary workflows directory by merging top-level keys."""
        self._policy.enforce_mutation(token)
        target = self._resolve_under_root(file_path)
        if not target.exists() or not target.is_file():
            raise ValueError(f"File not found: {file_path}")
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Existing JSON payload must be an object")
        payload.update(updates)
        payload_bytes = len(json.dumps(payload).encode("utf-8"))
        self._policy.enforce_payload_size(payload_bytes)
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return {"edited": True, "path": str(target)}

    def file_delete(self, file_path: str, token: Optional[str] = None) -> Dict[str, Any]:
        """Delete a file under the primary workflows directory."""
        self._policy.enforce_mutation(token)
        target = self._resolve_under_root(file_path)
        if not target.exists() or not target.is_file():
            raise ValueError(f"File not found: {file_path}")
        target.unlink()
        return {"deleted": True, "path": str(target)}

    def save(
        self,
        workflow_id: str,
        workflow_json: Dict[str, Any],
        meta: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Save a workflow entry to the store."""
        self._policy.enforce_mutation(token)
        payload_bytes = len(json.dumps(workflow_json).encode("utf-8"))
        self._policy.enforce_payload_size(payload_bytes)
        try:
            existing_meta = self._store.read_meta(workflow_id)
        except WorkflowNotFoundError:
            existing_meta = None

        packaged = package_workflow(
            workflow_id,
            workflow_json,
            existing_meta=existing_meta,
            hints=meta or {},
            include_suggestions=True,
        )
        self._store.save_workflow(
            workflow_id,
            workflow_json,
            meta=packaged["meta"],
            params=packaged["params"],
        )
        return {
            "id": workflow_id,
            "packaged": True,
            "params_count": len(packaged["params"].get("params", [])),
            "ignored_explicit_params": params is not None,
        }

    async def import_from_photarium(
        self,
        image_id: str,
        workflow_id: str,
        photarium_mcp_url: str = "http://127.0.0.1:8787",
        name: Optional[str] = None,
        tags: Optional[list[str]] = None,
        hints: Optional[Dict[str, Any]] = None,
        include_suggestions: bool = True,
        prefer_prompt: bool = True,
        include_raw_metadata: bool = False,
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Import a workflow from a Photarium image and save it in the workflow store."""
        self._policy.enforce_mutation(token)

        extracted = await self._call_remote_tool(
            photarium_mcp_url,
            "photarium_extract_workflow",
            {"imageId": image_id, "includeRawMetadata": include_raw_metadata},
        )
        if not extracted.get("extracted"):
            raise ValueError(f"No embedded workflow found for Photarium image: {image_id}")

        ordered_keys = ["prompt", "workflow"] if prefer_prompt else ["workflow", "prompt"]
        selected_key = None
        selected_workflow: Optional[Dict[str, Any]] = None
        for key in ordered_keys:
            payload = extracted.get(key)
            if isinstance(payload, dict) and payload:
                selected_key = key
                selected_workflow = payload
                break
        if selected_workflow is None or selected_key is None:
            raise ValueError("Photarium extract result did not include a usable workflow payload")

        workflow_format = detect_workflow_format(selected_workflow)
        if workflow_format == "ui":
            workflow_json = ui_to_api_format(selected_workflow)
            workflow_format = "api"
        else:
            workflow_json = selected_workflow

        payload_bytes = len(json.dumps(workflow_json).encode("utf-8"))
        self._policy.enforce_payload_size(payload_bytes)

        source_meta: Dict[str, Any] = {}
        if name is None or tags is None:
            try:
                source_meta = await self._call_remote_tool(
                    photarium_mcp_url,
                    "photarium_get",
                    {"imageId": image_id},
                )
            except Exception:
                source_meta = {}

        source_tags = source_meta.get("tags")
        resolved_tags = tags if tags is not None else [str(t) for t in source_tags] if isinstance(source_tags, list) else []
        resolved_name = name or workflow_id

        existing_meta: Optional[Dict[str, Any]]
        try:
            existing_meta = self._store.read_meta(workflow_id)
        except WorkflowNotFoundError:
            existing_meta = None

        if existing_meta is None:
            existing_meta = build_meta(
                workflow_id=workflow_id,
                name=resolved_name,
                description=f"Imported from Photarium image {image_id}",
                tags=resolved_tags,
                workflow_json=workflow_json,
            ).to_dict()

        packaged = package_workflow(
            workflow_id,
            workflow_json,
            existing_meta=existing_meta,
            hints=hints or {},
            include_suggestions=include_suggestions,
        )
        self._store.save_workflow(
            workflow_id,
            workflow_json,
            meta=packaged["meta"],
            params=packaged["params"],
        )

        return {
            "id": workflow_id,
            "image_id": image_id,
            "source_payload": selected_key,
            "workflow_format": workflow_format,
            "params_count": len(packaged["params"].get("params", [])),
        }

    def delete(self, workflow_id: str, token: Optional[str] = None) -> Dict[str, Any]:
        """Delete a workflow entry from the store."""
        self._policy.enforce_mutation(token)
        self._store.delete_workflow(workflow_id)
        return {"deleted": True}

    async def run(
        self,
        workflow_id: str,
        overrides: Dict[str, Any],
        client_id: Optional[str] = None,
        token: Optional[str] = None,
        force: bool = False,
        wait_timeout_s: float = 300.0,
        wait_poll_ms: int = 1000,
    ) -> Dict[str, Any]:
        """Run a workflow with parameter overrides via ComfyUI."""
        self._policy.enforce_mutation(token)
        workflow = self._store.read_workflow(workflow_id)
        params = self._store.read_params(workflow_id)
        if not params:
            raise ValueError("Params schema missing for workflow")
        spec = ParamSpec.model_validate(params)
        normalized_overrides = await self._normalize_file_overrides(workflow, spec, overrides)
        normalized_overrides = await self._normalize_aspect_overrides(
            spec=spec,
            overrides=normalized_overrides,
        )
        auto_aspect = await self._auto_aspect_overrides_from_input(
            workflow=workflow,
            spec=spec,
            raw_overrides=overrides,
        )
        if auto_aspect:
            normalized_overrides.update(auto_aspect.get("overrides", {}))
        patched = patch_workflow(workflow, spec, normalized_overrides, force=force)
        filename_prefix_used = self._ensure_unique_filename_prefix(
            workflow_id,
            patched,
            spec,
            normalized_overrides,
        )
        payload_bytes = len(json.dumps(patched).encode("utf-8"))
        self._policy.enforce_payload_size(payload_bytes)
        queued_at = time.time()
        queue_result = await self._client.queue_prompt(patched, client_id=client_id)
        prompt_id = queue_result.get("prompt_id")
        if not prompt_id:
            return queue_result
        result = await self._wait_and_extract(
            prompt_id,
            timeout_s=float(wait_timeout_s),
            poll_ms=max(100, int(wait_poll_ms)),
            queued_workflow=patched,
            queued_at=queued_at,
        )
        if auto_aspect:
            result["auto_aspect_ratio_source"] = auto_aspect.get("source_ratio")
            result["auto_aspect_ratio_applied"] = auto_aspect.get("applied_ratio")
            result["auto_aspect_ratio_anchor"] = auto_aspect.get("applied_anchor")
            result["auto_aspect_ratio_reason"] = auto_aspect.get("reason")
        if filename_prefix_used:
            result["filename_prefix_used"] = filename_prefix_used
        return result

    async def _normalize_aspect_overrides(
        self,
        *,
        spec: ParamSpec,
        overrides: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Normalize aspect overrides so plain ratio tokens use custom-ratio mode."""
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

        requested_ratio = self._normalize_ratio_token(requested_clean)
        is_plain_ratio_token = bool(requested_ratio and re.fullmatch(r"\d+\s*:\s*\d+", requested_clean))

        object_info = await self._client.get_object_info()
        flux_node = object_info.get("FluxResolutionNode") or {}
        flux_inputs = flux_node.get("input", {})
        aspect_entry = flux_inputs.get("required", {}).get("aspect_ratio") or flux_inputs.get("optional", {}).get("aspect_ratio")
        allowed_aspects: list[str] = []
        if isinstance(aspect_entry, list) and aspect_entry and isinstance(aspect_entry[0], list):
            allowed_aspects = [str(item) for item in aspect_entry[0] if isinstance(item, str)]
        matched_allowed_aspect = self._match_allowed_aspect_choice(requested_clean, allowed_aspects)

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

    async def _normalize_file_overrides(
        self,
        workflow: Dict[str, Any],
        spec: ParamSpec,
        overrides: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Upload local file-path overrides for LoadImage nodes.

        Generic workflows_run callers often pass local paths from tools like
        photarium_download_image. ComfyUI LoadImage expects a filename present
        in Comfy input storage, so we transparently upload and rewrite those
        overrides to the uploaded filename.
        """
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

            upload = await self._client.upload_image(
                str(local_path),
                image_type="input",
                overwrite=False,
            )
            uploaded_name = upload.get("name")
            if not uploaded_name:
                raise ValueError(f"Failed to upload input image for override '{key}'")
            uploaded_subfolder = str(upload.get("subfolder") or "").strip("/")
            normalized[key] = f"{uploaded_subfolder}/{uploaded_name}" if uploaded_subfolder else uploaded_name

        return normalized

    def _ensure_unique_filename_prefix(
        self,
        workflow_id: str,
        workflow: Dict[str, Any],
        spec: ParamSpec,
        overrides: Dict[str, Any],
    ) -> Optional[str]:
        """Set a unique SaveImage filename_prefix unless caller already provided one."""
        prefix_param_names = self._saveimage_prefix_param_names(workflow, spec)
        user_set_prefix = any(
            isinstance(overrides.get(name), str) and str(overrides.get(name)).strip()
            for name in prefix_param_names
        )
        if user_set_prefix:
            return None

        prefix = self._make_runtime_filename_prefix(workflow_id)
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

    async def _auto_aspect_overrides_from_input(
        self,
        *,
        workflow: Dict[str, Any],
        spec: ParamSpec,
        raw_overrides: Dict[str, Any],
    ) -> Dict[str, Any] | None:
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

        width, height, _fmt = self._probe_image_dimensions(input_path)
        source_ratio = self._normalize_ratio_token(f"{width}:{height}")
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

        applied = self._nearest_allowed_aspect_choice(source_ratio, allowed_aspects)
        if not applied:
            return None

        overrides: Dict[str, Any] = {aspect_param_name: applied}
        if custom_ratio_param_name:
            overrides[custom_ratio_param_name] = True
        if custom_aspect_param_name:
            overrides[custom_aspect_param_name] = source_ratio
        return {
            "overrides": overrides,
            "source_ratio": source_ratio,
            "applied_ratio": source_ratio,
            "applied_anchor": applied,
            "reason": "auto_preserve_from_input_image",
        }

    @staticmethod
    def _find_param_name(spec: ParamSpec, expected_name: str) -> str | None:
        for item in spec.params:
            if item.name == expected_name:
                return item.name
        return None

    def _has_explicit_aspect_override(self, spec: ParamSpec, raw_overrides: Dict[str, Any]) -> bool:
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
                value_norm = self._normalize_ratio_token(str(value)) if value is not None else None
                default_norm = self._normalize_ratio_token(str(default)) if default is not None else None
                if value_norm and default_norm and value_norm == default_norm:
                    continue
                if value == default:
                    continue
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                return True

            # aspect_ratio
            if isinstance(value, str):
                value_clean = value.strip()
                if not value_clean:
                    continue
            else:
                value_clean = str(value)
            if value == default:
                continue
            if isinstance(default, str):
                value_norm = self._normalize_ratio_token(value_clean)
                default_norm = self._normalize_ratio_token(default)
                if value_norm and default_norm and value_norm == default_norm:
                    continue
            return True
        return False

    @staticmethod
    def _find_local_input_image_path(
        *,
        workflow: Dict[str, Any],
        spec: ParamSpec,
        raw_overrides: Dict[str, Any],
    ) -> Path | None:
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
    def _saveimage_prefix_param_names(workflow: Dict[str, Any], spec: ParamSpec) -> set[str]:
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
    def _make_runtime_filename_prefix(workflow_id: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", workflow_id).strip("_")
        if not slug:
            slug = "workflow"
        stamp_ms = int(time.time() * 1000)
        suffix = uuid.uuid4().hex[:8]
        return f"mcp_{slug[:36]}_{stamp_ms}_{suffix}"

    def image_info(self, file_path: str) -> Dict[str, Any]:
        """Inspect a local image and return dimensions + aspect-ratio details."""
        path = Path(file_path).expanduser()
        if not path.exists() or not path.is_file():
            raise ValueError(f"Image file not found: {file_path}")

        width, height, fmt = self._probe_image_dimensions(path)
        if width <= 0 or height <= 0:
            raise ValueError(f"Could not determine image dimensions: {file_path}")

        ratio_float = width / height
        divisor = gcd(width, height) or 1
        ratio_simple = f"{width // divisor}:{height // divisor}"
        orientation = "square"
        if width > height:
            orientation = "landscape"
        elif height > width:
            orientation = "portrait"

        return {
            "file_path": str(path),
            "format": fmt,
            "width": width,
            "height": height,
            "aspect_ratio_float": ratio_float,
            "aspect_ratio_simple": ratio_simple,
            "orientation": orientation,
        }

    @staticmethod
    def _probe_image_dimensions(path: Path) -> tuple[int, int, str]:
        # Prefer Pillow when available for broad format support.
        try:
            from PIL import Image  # type: ignore

            with Image.open(path) as img:
                width, height = img.size
                fmt = str(getattr(img, "format", "") or "").upper() or "UNKNOWN"
                return int(width), int(height), fmt
        except Exception:
            pass

        raw = path.read_bytes()
        if len(raw) >= 24 and raw.startswith(b"\x89PNG\r\n\x1a\n"):
            width = int.from_bytes(raw[16:20], "big")
            height = int.from_bytes(raw[20:24], "big")
            return width, height, "PNG"

        if len(raw) >= 10 and raw[:6] in {b"GIF87a", b"GIF89a"}:
            width = int.from_bytes(raw[6:8], "little")
            height = int.from_bytes(raw[8:10], "little")
            return width, height, "GIF"

        if len(raw) >= 4 and raw[:2] == b"\xff\xd8":
            width, height = WorkflowTools._probe_jpeg_dimensions(raw)
            return width, height, "JPEG"

        raise ValueError("Unsupported image format (install Pillow for broader support).")

    @staticmethod
    def _probe_jpeg_dimensions(raw: bytes) -> tuple[int, int]:
        # Parse JPEG segments until a SOF marker with dimensions is found.
        sof_markers = {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }
        idx = 2
        size = len(raw)
        while idx < size:
            while idx < size and raw[idx] == 0xFF:
                idx += 1
            if idx >= size:
                break
            marker = raw[idx]
            idx += 1

            if marker in {0xD8, 0xD9}:  # SOI/EOI
                continue
            if idx + 2 > size:
                break
            seg_len = int.from_bytes(raw[idx:idx + 2], "big")
            if seg_len < 2 or idx + seg_len > size:
                break
            if marker in sof_markers:
                if idx + 7 > size:
                    break
                height = int.from_bytes(raw[idx + 3:idx + 5], "big")
                width = int.from_bytes(raw[idx + 5:idx + 7], "big")
                if width > 0 and height > 0:
                    return width, height
                break
            idx += seg_len
        raise ValueError("JPEG dimensions not found")

    async def status(
        self,
        prompt_id: Optional[str] = None,
        include_progress: bool = True,
        progress_timeout_s: float = 0.75,
    ) -> Dict[str, Any]:
        """Return queue/history status and an optional quick websocket progress snapshot."""
        queue = await self._client.get_queue()
        history = await self._client.get_history(prompt_id=prompt_id) if prompt_id else {}
        running_ids, pending_ids = self._queue_prompt_ids(queue)
        state = self._derive_prompt_state(prompt_id, history, running_ids, pending_ids)
        response: Dict[str, Any] = {
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

    async def watch(
        self,
        prompt_id: str,
        inactivity_timeout_s: float = 120.0,
        max_wait_s: float = 1800.0,
        poll_ms: int = 500,
        include_history: bool = True,
    ) -> Dict[str, Any]:
        """Watch a prompt until terminal state, preferring websocket progress updates."""
        if hasattr(self._client, "watch_prompt"):
            try:
                watched = await self._client.watch_prompt(
                    prompt_id=prompt_id,
                    inactivity_timeout_s=inactivity_timeout_s,
                    max_wait_s=max_wait_s,
                )
                response: Dict[str, Any] = {
                    "status": watched.get("status", "timeout"),
                    "prompt_id": prompt_id,
                    "events": watched.get("events", []),
                    "reason": watched.get("reason"),
                    "source": watched.get("source", "websocket"),
                }
                history = watched.get("history", {}) if include_history else {}
                if include_history and not history and response["status"] == "complete":
                    history = await self._client.get_history(prompt_id=prompt_id)
                if include_history:
                    response["history"] = history
                return response
            except Exception:
                # Fall back to history polling for compatibility if websocket watch fails.
                pass

        polled = await self.wait(prompt_id, timeout_s=inactivity_timeout_s, poll_ms=poll_ms)
        polled["prompt_id"] = prompt_id
        polled["source"] = "history_polling"
        if not include_history and "history" in polled:
            polled = dict(polled)
            polled.pop("history", None)
        return polled

    async def wait(
        self,
        prompt_id: str,
        timeout_s: float = 60.0,
        poll_ms: int = 500,
    ) -> Dict[str, Any]:
        """Wait for a prompt to appear in history.

        Returns status and history data if found.
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            history = await self._client.get_history(prompt_id=prompt_id)
            if history:
                return {"status": "complete", "history": history}
            await asyncio.sleep(poll_ms / 1000.0)
        return {"status": "timeout", "history": {}}

    async def _wait_and_extract(
        self,
        prompt_id: str,
        timeout_s: float = 120.0,
        poll_ms: int = 1000,
        queued_workflow: Optional[Dict[str, Any]] = None,
        queued_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Wait for a prompt to complete and extract output image info.

        Returns a dict with status, output_images list, and the raw prompt_id.
        Each output image has {filename, subfolder, type} plus a view_url for
        direct download from ComfyUI.
        """
        max_wait_s = max(timeout_s + 30.0, timeout_s * 8.0)
        watch_result = await self.watch(
            prompt_id,
            inactivity_timeout_s=timeout_s,
            max_wait_s=max_wait_s,
            poll_ms=poll_ms,
            include_history=True,
        )
        if watch_result["status"] != "complete":
            return {
                "status": "timeout",
                "prompt_id": prompt_id,
                "output_images": [],
                "message": (
                    f"Workflow did not complete after {timeout_s}s without progress. "
                    f"You can continue watching with workflows_watch(prompt_id='{prompt_id}')."
                ),
            }
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
                suffix = (
                    "Output records were inferred from local COMFY_MCP_COMFY_OUTPUT_DIR; "
                    "verify prompt-to-file mapping if filename_prefix is generic."
                )
                response["message"] = f"{existing_message} {suffix}".strip()
        return response

    @staticmethod
    def _queue_prompt_ids(queue_payload: Dict[str, Any]) -> tuple[list[str], list[str]]:
        running: list[str] = []
        pending: list[str] = []

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

        running = _collect_prompt_ids(queue_payload.get("queue_running"))
        pending = _collect_prompt_ids(queue_payload.get("queue_pending"))
        return running, pending

    @staticmethod
    def _derive_prompt_state(
        prompt_id: Optional[str],
        history: Dict[str, Any],
        running_ids: list[str],
        pending_ids: list[str],
    ) -> str:
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

    def _completion_from_history(self, prompt_id: str, history: Dict[str, Any]) -> Dict[str, Any]:
        # Extract output images from history
        output_images = []
        # history is usually {prompt_id: {"outputs": {node_id: {"images": [...]}}}}
        prompt_data = history.get(prompt_id, history)
        outputs = prompt_data.get("outputs", {})
        for node_id, node_out in outputs.items():
            for img in node_out.get("images", []):
                filename = img.get("filename", "")
                subfolder = img.get("subfolder", "")
                img_type = img.get("type", "output")
                # Build a ComfyUI view URL for this image
                base = self._client._base_url.rstrip("/")
                query = {"filename": filename, "type": img_type}
                if subfolder:
                    query["subfolder"] = subfolder
                view_url = f"{base}/view?{urlencode(query)}"
                output_images.append({
                    "filename": filename,
                    "subfolder": subfolder,
                    "type": img_type,
                    "view_url": view_url,
                })
        response: Dict[str, Any] = {
            "status": "complete",
            "prompt_id": prompt_id,
            "output_images": output_images,
        }
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
                    response["message"] = (
                        "Workflow completed, but ComfyUI reported no output image records "
                        "(likely cached execution). Retry with force=true and/or change seed."
                    )
                else:
                    response["message"] = (
                        "Workflow completed, but ComfyUI returned no output image records."
                    )
        return response

    @staticmethod
    def _collect_saveimage_prefixes(workflow: Dict[str, Any]) -> list[str]:
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

    def _infer_output_files(
        self,
        filename_prefixes: list[str],
        queued_at: Optional[float],
        limit: int = 5,
    ) -> list[Dict[str, Any]]:
        if self._comfy_output_dir is None:
            return []
        output_dir = self._comfy_output_dir
        if not output_dir.exists() or not output_dir.is_dir():
            return []
        if not filename_prefixes:
            return []

        patterns: list[str] = []
        for prefix in filename_prefixes:
            clean = prefix.strip()
            if not clean:
                continue
            patterns.extend(
                [
                    f"{clean}*.png",
                    f"{clean}*.jpg",
                    f"{clean}*.jpeg",
                    f"{clean}*.webp",
                    f"{clean}*.PNG",
                    f"{clean}*.JPG",
                    f"{clean}*.JPEG",
                    f"{clean}*.WEBP",
                ]
            )
        if not patterns:
            return []

        unique: dict[str, tuple[Path, str]] = {}
        for pattern in patterns:
            for candidate in output_dir.rglob(pattern):
                if candidate.is_file():
                    rel_parent = candidate.parent.relative_to(output_dir)
                    subfolder = "" if rel_parent == Path(".") else rel_parent.as_posix()
                    unique[str(candidate.resolve())] = (candidate, subfolder)

        files_with_subfolders = list(unique.values())
        if queued_at is not None:
            files_with_subfolders = [
                item for item in files_with_subfolders if item[0].stat().st_mtime >= (queued_at - 2.0)
            ]
        if not files_with_subfolders:
            return []

        files_with_subfolders.sort(key=lambda item: item[0].stat().st_mtime, reverse=True)
        selected = files_with_subfolders[: max(1, limit)]
        base = self._client._base_url.rstrip("/")
        inferred: list[Dict[str, Any]] = []
        for path, subfolder in selected:
            filename = path.name
            query = {"filename": filename, "type": "output"}
            if subfolder:
                query["subfolder"] = subfolder
            view_url = f"{base}/view?{urlencode(query)}"
            inferred.append(
                {
                    "filename": filename,
                    "subfolder": subfolder,
                    "type": "output",
                    "view_url": view_url,
                    "local_path": str(path),
                    "inferred": True,
                }
            )
        return inferred

    async def run_aspect_ratio_adjustment(
        self,
        image_path: str,
        aspect_ratio: str,
        workflow_id: str = "aspect_ratio_adjustment",
        positive_prompt: Optional[str] = None,
        negative_prompt: Optional[str] = None,
        seed: Optional[int] = None,
        output_base_name: Optional[str] = None,
        client_id: Optional[str] = None,
        token: Optional[str] = None,
        upload_subfolder: Optional[str] = None,
        overwrite: bool = False,
        wait_timeout_s: float = 300.0,
        wait_poll_ms: int = 1000,
    ) -> Dict[str, Any]:
        """Run the aspect ratio adjustment workflow with an uploaded image."""
        self._policy.enforce_mutation(token)

        workflow = self._store.read_workflow(workflow_id)
        if not workflow:
            if workflow_id != "aspect_comfyui_01077":
                workflow = self._store.read_workflow("aspect_comfyui_01077")
                workflow_id = "aspect_comfyui_01077"
            if not workflow:
                raise ValueError("Workflow not found: aspect_ratio_adjustment or aspect_comfyui_01077")

        image_file = Path(image_path)
        if not image_file.exists():
            raise ValueError(f"Image file not found: {image_path}")

        upload_result = await self._client.upload_image(
            str(image_file),
            image_type="input",
            subfolder=upload_subfolder,
            overwrite=overwrite,
        )
        uploaded_name = upload_result.get("name")
        uploaded_subfolder = upload_result.get("subfolder", "")
        if not uploaded_name:
            raise ValueError("Image upload did not return a filename")

        object_info = await self._client.get_object_info()
        flux_node = object_info.get("FluxResolutionNode") or {}
        flux_inputs = flux_node.get("input", {})
        aspect_entry = flux_inputs.get("required", {}).get("aspect_ratio") or flux_inputs.get("optional", {}).get("aspect_ratio")
        allowed_aspects = []
        if isinstance(aspect_entry, list) and aspect_entry:
            allowed_aspects = aspect_entry[0]
        ratio_value = aspect_ratio.split(" ")[0]
        normalized_ratio_value = self._normalize_ratio_token(ratio_value) or ratio_value.replace(" ", "")
        is_plain_ratio_token = bool(re.fullmatch(r"\d+\s*:\s*\d+", aspect_ratio.strip()))
        is_custom_ratio = bool(re.fullmatch(r"\d+\s*:\s*\d+", ratio_value))
        normalized_custom_ratio = normalized_ratio_value
        matched_allowed_aspect = self._match_allowed_aspect_choice(aspect_ratio, allowed_aspects)
        if allowed_aspects and not matched_allowed_aspect and not is_custom_ratio:
            raise ValueError(
                f"aspect_ratio '{aspect_ratio}' not in allowed list: {allowed_aspects}"
            )

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
            base_raw = output_base_name if output_base_name is not None else Path(uploaded_name).stem
            base_clean = _sanitize_base(_strip_comfy_counter_suffixes(base_raw))
            prefix = f"{base_clean}__{ratio_slug}"
            if seed is not None:
                prefix = f"{prefix}__s{int(seed)}"
            prefix = f"{prefix}__{uuid.uuid4().hex[:8]}"
            # Keep prefixes bounded; ComfyUI adds its own numeric counter suffix.
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
                # Keep a valid enum value while using a custom ratio.
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
            return {
                "workflow_id": workflow_id,
                "error": "Failed to queue prompt",
                "queue_result": queue_result,
            }
        # Wait for ComfyUI to actually finish and return output image info
        completion = await self._wait_and_extract(
            prompt_id,
            timeout_s=float(wait_timeout_s),
            poll_ms=max(100, int(wait_poll_ms)),
            queued_workflow=patched,
            queued_at=queued_at,
        )
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

    @staticmethod
    def _normalize_ratio_token(value: str) -> str | None:
        match = re.search(r"(\d+)\s*:\s*(\d+)", value)
        if not match:
            return None
        width = int(match.group(1))
        height = int(match.group(2))
        if width <= 0 or height <= 0:
            return None
        return f"{width}:{height}"

    @classmethod
    def _match_allowed_aspect_choice(cls, requested: str, allowed_aspects: list[str]) -> str | None:
        if not requested:
            return None
        requested_clean = requested.strip()
        if not requested_clean:
            return None
        for allowed in allowed_aspects:
            if requested_clean == str(allowed).strip():
                return str(allowed)
        requested_ratio = cls._normalize_ratio_token(requested_clean)
        if not requested_ratio:
            return None
        for allowed in allowed_aspects:
            allowed_ratio = cls._normalize_ratio_token(str(allowed))
            if allowed_ratio and allowed_ratio == requested_ratio:
                return str(allowed)
        return None

    @classmethod
    def _nearest_allowed_aspect_choice(cls, source_ratio: str, allowed_aspects: list[str]) -> str | None:
        exact = cls._match_allowed_aspect_choice(source_ratio, allowed_aspects)
        if exact:
            return exact
        source_float = cls._ratio_to_float(source_ratio)
        if source_float is None:
            return None

        best_choice: str | None = None
        best_distance: float | None = None
        for allowed in allowed_aspects:
            allowed_ratio = cls._normalize_ratio_token(str(allowed))
            allowed_float = cls._ratio_to_float(allowed_ratio or "")
            if allowed_float is None or allowed_float <= 0:
                continue
            distance = abs(log(source_float) - log(allowed_float))
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_choice = str(allowed)
        return best_choice

    @classmethod
    def _ratio_to_float(cls, ratio_text: str) -> float | None:
        token = cls._normalize_ratio_token(ratio_text)
        if not token:
            return None
        width_text, height_text = token.split(":")
        width = int(width_text)
        height = int(height_text)
        if width <= 0 or height <= 0:
            return None
        return width / height

    async def _call_remote_tool(
        self,
        base_url: str,
        tool_name: str,
        args: Dict[str, Any],
    ) -> Dict[str, Any]:
        url = f"{base_url.rstrip('/')}/tools/{tool_name}"
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(url, json=args)
            resp.raise_for_status()
            payload: Any = resp.json() if resp.content else {}
        return self._normalize_remote_tool_payload(payload)

    @staticmethod
    def _normalize_remote_tool_payload(payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Remote tool returned non-object payload")

        if "ok" in payload:
            if not payload.get("ok"):
                raise RuntimeError(str(payload.get("error") or "Remote tool call failed"))
            return WorkflowTools._normalize_remote_tool_payload(payload.get("result") or {})

        if "content" in payload:
            content = payload.get("content")
            if isinstance(content, list) and content:
                first = content[0]
                if isinstance(first, dict) and isinstance(first.get("text"), str):
                    text = first["text"]
                    try:
                        parsed = json.loads(text)
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        return {"text": text}
            return payload

        if "result" in payload and isinstance(payload.get("result"), dict):
            return WorkflowTools._normalize_remote_tool_payload(payload["result"])

        return payload
