"""Workflow extraction/import services."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from comfy_mcp.extraction.adapter import WorkflowExtractor
from comfy_mcp.extraction.normalize import detect_workflow_format, ui_to_api_format
from comfy_mcp.mcp_server.policy import Policy
from comfy_mcp.mcp_server.remote_tool_client import RemoteToolClient
from comfy_mcp.workflow_store.errors import WorkflowNotFoundError
from comfy_mcp.workflow_store.meta import build_meta
from comfy_mcp.workflow_store.packaging import package_workflow
from comfy_mcp.workflow_store.store import WorkflowStore


class WorkflowArtifactService:
    def __init__(self, store: WorkflowStore, policy: Policy, extractor: WorkflowExtractor | None = None, remote_client: RemoteToolClient | None = None) -> None:
        self._store = store
        self._policy = policy
        self._extractor = extractor
        self._remote_client = remote_client or RemoteToolClient()

    def _get_extractor(self) -> WorkflowExtractor:
        if self._extractor is None:
            self._extractor = WorkflowExtractor()
        return self._extractor

    def extract_from_artifact(self, path: str, preserve_format: bool = False) -> dict[str, Any]:
        extractor = self._get_extractor()
        result = extractor.extract_from_path(path, preserve_format=preserve_format)
        return {"workflow": result.workflow, "workflow_format": result.workflow_format, "raw_metadata": result.raw_metadata}

    def import_from_artifact(self, path: str, workflow_id: str, name: str | None = None, tags: list[str] | None = None, token: str | None = None) -> dict[str, Any]:
        self._policy.enforce_mutation(token)
        extractor = self._get_extractor()
        result = extractor.extract_from_path(path, preserve_format=False)
        payload_bytes = len(json.dumps(result.workflow).encode("utf-8"))
        self._policy.enforce_payload_size(payload_bytes)
        try:
            existing_meta = self._store.read_meta(workflow_id)
        except WorkflowNotFoundError:
            existing_meta = None
        hints: dict[str, Any] = {"description": "Imported from artifact"}
        if name:
            hints["name"] = name
        if tags:
            hints["tags"] = tags
        packaged = package_workflow(workflow_id, result.workflow, existing_meta=existing_meta, hints=hints, include_suggestions=True)
        self._store.save_workflow(workflow_id, result.workflow, meta=packaged["meta"], params=packaged["params"])
        return {"id": workflow_id, "params_count": len(packaged["params"].get("params", []))}

    async def import_from_photarium(self, image_id: str, workflow_id: str, photarium_mcp_url: str = "http://127.0.0.1:8787", namespace: str | None = None, name: str | None = None, tags: list[str] | None = None, hints: dict[str, Any] | None = None, include_suggestions: bool = True, prefer_prompt: bool = True, include_raw_metadata: bool = False, token: str | None = None) -> dict[str, Any]:
        self._policy.enforce_mutation(token)
        remote_args: dict[str, Any] = {"imageId": image_id}
        if namespace:
            remote_args["namespace"] = namespace
        extraction = await self.extract_from_photarium(image_id=image_id, photarium_mcp_url=photarium_mcp_url, namespace=namespace, prefer_prompt=prefer_prompt, include_raw_metadata=include_raw_metadata, preserve_format=False, token=token)
        workflow_json = extraction["workflow"]
        workflow_format = str(extraction.get("workflow_format") or "api")
        extraction_source = str(extraction.get("extraction_source") or "unknown")
        selected_key = str(extraction.get("source_payload") or "workflow")
        source_meta = extraction.get("source_meta") or {}
        payload_bytes = len(json.dumps(workflow_json).encode("utf-8"))
        self._policy.enforce_payload_size(payload_bytes)
        if name is None or tags is None:
            if not source_meta:
                try:
                    source_meta = await self._remote_client.call(photarium_mcp_url, "photarium_get", remote_args)
                except Exception:
                    source_meta = {}
        source_tags = source_meta.get("tags")
        resolved_tags = tags if tags is not None else [str(t) for t in source_tags] if isinstance(source_tags, list) else []
        resolved_name = name or workflow_id
        try:
            existing_meta = self._store.read_meta(workflow_id)
        except WorkflowNotFoundError:
            existing_meta = None
        if existing_meta is None:
            existing_meta = build_meta(workflow_id=workflow_id, name=resolved_name, description=f"Imported from Photarium image {image_id}", tags=resolved_tags, workflow_json=workflow_json).to_dict()
        packaged = package_workflow(workflow_id, workflow_json, existing_meta=existing_meta, hints=hints or {}, include_suggestions=include_suggestions)
        self._store.save_workflow(workflow_id, workflow_json, meta=packaged["meta"], params=packaged["params"])
        return {"id": workflow_id, "image_id": image_id, "source_payload": selected_key, "extraction_source": extraction_source, "workflow_format": workflow_format, "params_count": len(packaged["params"].get("params", []))}

    async def extract_from_photarium(self, image_id: str, photarium_mcp_url: str = "http://127.0.0.1:8787", namespace: str | None = None, prefer_prompt: bool = True, include_raw_metadata: bool = False, preserve_format: bool = False, token: str | None = None) -> dict[str, Any]:
        self._policy.enforce_mutation(token)
        remote_args: dict[str, Any] = {"imageId": image_id}
        if namespace:
            remote_args["namespace"] = namespace
        source_meta: dict[str, Any] = {}
        extracted: dict[str, Any] = {}
        extraction_source = "photarium_extract_workflow"
        try:
            extracted = await self._remote_client.call(photarium_mcp_url, "photarium_extract_workflow", {**remote_args, "includeRawMetadata": include_raw_metadata})
        except Exception as exc:
            extracted = {"extracted": False, "message": str(exc)}
        if not extracted.get("extracted"):
            try:
                extras_payload = await self._remote_client.call(photarium_mcp_url, "photarium_extras_get", remote_args)
            except Exception:
                extras_payload = {}
            extras_workflow = self._extract_workflow_from_extras(extras_payload)
            if extras_workflow is not None:
                extracted = {"extracted": True, "workflow": extras_workflow}
                extraction_source = "photarium_extras_get"
        selected_workflow: dict[str, Any] | None = None
        selected_key: str | None = None
        if extracted.get("extracted"):
            ordered_keys = ["prompt", "workflow"] if prefer_prompt else ["workflow", "prompt"]
            for key in ordered_keys:
                payload = self._coerce_json_object(extracted.get(key))
                if payload is not None:
                    selected_key = key
                    selected_workflow = payload
                    break
            if selected_workflow is None:
                selected_key = "workflow"
                selected_workflow = self._coerce_json_object(extracted.get("workflow"))
        if selected_workflow is None:
            try:
                source_meta = await self._remote_client.call(photarium_mcp_url, "photarium_get", remote_args)
            except Exception:
                source_meta = {}
            with tempfile.TemporaryDirectory(prefix="comfy_mcp_photarium_extract_") as td:
                tmp_dir = Path(td)
                requested = tmp_dir / f"{image_id}.bin"
                download_args: dict[str, Any] = {"imageId": image_id, "savePath": str(requested), "includeBase64": False}
                download_tool = "photarium_download_original"
                try:
                    download_result = await self._remote_client.call(photarium_mcp_url, download_tool, {**download_args, **({"namespace": namespace} if namespace else {})})
                except Exception:
                    download_tool = "photarium_download_image"
                    download_result = await self._remote_client.call(photarium_mcp_url, download_tool, {**download_args, **({"namespace": namespace} if namespace else {})})
                artifact_path = self._normalize_downloaded_path(requested, download_result or {})
                extracted_local = self.extract_from_artifact(str(artifact_path), preserve_format=preserve_format)
                extracted_local["extraction_source"] = download_tool
                extracted_local["source_payload"] = "workflow"
                extracted_local["source_meta"] = source_meta
                return extracted_local
        if selected_workflow is None or selected_key is None:
            if not source_meta:
                try:
                    source_meta = await self._remote_client.call(photarium_mcp_url, "photarium_get", remote_args)
                except Exception:
                    source_meta = {}
            raise ValueError(self._missing_workflow_message(image_id, extracted, source_meta))
        workflow_format = detect_workflow_format(selected_workflow)
        workflow_json = selected_workflow
        if workflow_format == "ui" and not preserve_format:
            workflow_json = ui_to_api_format(selected_workflow)
            workflow_format = "api"
        return {"workflow": workflow_json, "workflow_format": workflow_format, "extraction_source": extraction_source, "source_payload": selected_key, "source_meta": source_meta}

    @staticmethod
    def _normalize_downloaded_path(requested_path: Path, download_result: dict[str, Any]) -> Path:
        saved_path = download_result.get("savedPath") if isinstance(download_result, dict) else None
        if isinstance(saved_path, str) and saved_path.strip():
            candidate = Path(saved_path)
            if candidate.exists():
                return candidate
        filename = download_result.get("filename") if isinstance(download_result, dict) else None
        if requested_path.exists() and requested_path.is_file():
            return requested_path
        if requested_path.exists() and requested_path.is_dir() and isinstance(filename, str) and filename:
            candidate = requested_path / filename
            if candidate.exists():
                return candidate
        parent = requested_path.parent
        if parent.exists():
            files = [p for p in parent.iterdir() if p.is_file()]
            if len(files) == 1:
                return files[0]
        return requested_path

    @staticmethod
    def _extract_workflow_from_extras(extras_payload: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(extras_payload, dict):
            return None
        record = extras_payload.get("record")
        if not isinstance(record, dict):
            return None
        comfy = record.get("comfyWorkflow")
        if not isinstance(comfy, dict):
            return None
        for key in ("workflowJson", "workflow", "promptJson", "prompt"):
            candidate = WorkflowArtifactService._coerce_json_object(comfy.get(key))
            if candidate is not None:
                return candidate
        return None

    @staticmethod
    def _coerce_json_object(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict) and value:
            return value
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict) and parsed:
            return parsed
        return None

    @staticmethod
    def _missing_workflow_message(image_id: str, extraction_payload: dict[str, Any], source_meta: dict[str, Any]) -> str:
        raw = source_meta.get("raw")
        raw = raw if isinstance(raw, dict) else {}
        generated_by = source_meta.get("generatedBy") or raw.get("generatedBy")
        comfy_detected = raw.get("comfyMetadataDetected")
        comfy_source = raw.get("comfyMetadataSource")
        namespace = source_meta.get("namespace") or raw.get("namespace")
        source_url = source_meta.get("sourceUrl") or raw.get("sourceUrl")
        original_url = source_meta.get("originalUrl") or raw.get("originalUrl")
        extract_message = extraction_payload.get("message")
        details = [
            f"extract_message={extract_message!r}",
            f"generatedBy={generated_by!r}",
            f"comfyMetadataDetected={comfy_detected!r}",
            f"comfyMetadataSource={comfy_source!r}",
            f"namespace={namespace!r}",
            f"sourceUrl={source_url!r}",
            f"originalUrl={original_url!r}",
        ]
        return f"No embedded workflow found for Photarium image: {image_id}. " + ", ".join(details)
