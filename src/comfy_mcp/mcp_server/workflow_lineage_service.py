"""Runtime workflow lineage caching and execution from image-like sources."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from comfy_mcp.mcp_server.policy import Policy
from comfy_mcp.mcp_server.remote_tool_client import RemoteToolClient
from comfy_mcp.mcp_server.workflow_artifact_service import WorkflowArtifactService
from comfy_mcp.mcp_server.workflow_execution_service import WorkflowExecutionService
from comfy_mcp.params.schema import ParamSpec
from comfy_mcp.workflow_store.hashing import sha256_json
from comfy_mcp.workflow_store.packaging import package_workflow
from comfy_mcp.workflow_store.store import WorkflowStore


class WorkflowLineageService:
    """Prepare and run packaged workflow snapshots from source artifacts."""

    _PROMPT_PARAM_NAMES = {"prompt", "positive_prompt", "negative_prompt"}
    _SCALAR_PARAM_NAMES = {"seed", "denoise"}
    _RESOLUTION_PARAM_NAMES = {
        "width",
        "height",
        "aspect_ratio",
        "custom_ratio",
        "custom_aspect_ratio",
    }

    def __init__(
        self,
        run_store: WorkflowStore,
        policy: Policy,
        artifact_service: WorkflowArtifactService,
        execution_service: WorkflowExecutionService,
        remote_client: RemoteToolClient | None = None,
    ) -> None:
        self._run_store = run_store
        self._policy = policy
        self._artifact_service = artifact_service
        self._execution_service = execution_service
        self._remote_client = remote_client or RemoteToolClient()
        self._index_root = self._run_store.root / "index"

    async def run_from_source(
        self,
        source: str | None = None,
        source_kind: str = "auto",
        overrides: dict[str, Any] | None = None,
        resume_token: str | None = None,
        namespace: str | None = None,
        photarium_mcp_url: str = "http://127.0.0.1:8787",
        wait_timeout_s: float = 300.0,
        wait_poll_ms: int = 1000,
        force: bool = False,
        token: str | None = None,
    ) -> dict[str, Any]:
        self._policy.enforce_mutation(token)
        user_overrides = dict(overrides or {})
        lineage_run_id: str | None = None
        try:
            if resume_token:
                lineage_run_id = str(resume_token).strip()
                if not lineage_run_id:
                    raise ValueError("resume_token must be non-empty")
                return await self._continue_prepared(
                    lineage_run_id=lineage_run_id,
                    user_overrides=user_overrides,
                    wait_timeout_s=wait_timeout_s,
                    wait_poll_ms=wait_poll_ms,
                    force=force,
                )

            if not isinstance(source, str) or not source.strip():
                raise ValueError("Either source or resume_token is required.")
            lineage_run_id = self._new_run_id()
            lookup = self._normalize_source_lookup(source=source, source_kind=source_kind)
            cached = self._resolve_cached_lineage(lookup)
            if cached is not None:
                lineage = self._prepare_from_cached_lineage(
                    lineage_run_id=lineage_run_id,
                    lookup=lookup,
                    cached_lineage_run_id=cached["lineage_run_id"],
                    cached_via=cached["matched_via"],
                )
            else:
                lineage = await self._prepare_from_source(
                    lineage_run_id=lineage_run_id,
                    lookup=lookup,
                    namespace=namespace,
                    photarium_mcp_url=photarium_mcp_url,
                )
            self._write_lineage(lineage_run_id, lineage)
            return await self._continue_prepared(
                lineage_run_id=lineage_run_id,
                user_overrides=user_overrides,
                wait_timeout_s=wait_timeout_s,
                wait_poll_ms=wait_poll_ms,
                force=force,
            )
        except Exception as exc:
            payload: dict[str, Any] = {"status": "failed", "error": str(exc)}
            if lineage_run_id:
                payload["lineage_run_id"] = lineage_run_id
                self._write_execution(
                    lineage_run_id,
                    {
                        "status": "failed",
                        "error": str(exc),
                        "updated_at": self._now_iso(),
                    },
                )
                try:
                    lineage = self._read_lineage(lineage_run_id)
                except Exception:
                    lineage = None
                if isinstance(lineage, dict):
                    lineage["status"] = "failed"
                    lineage["updated_at"] = self._now_iso()
                    self._write_lineage(lineage_run_id, lineage)
            return payload

    def lineage_get(self, lineage_run_id: str | None = None, image_id: str | None = None) -> dict[str, Any]:
        resolved_run_id = str(lineage_run_id or "").strip()
        if not resolved_run_id:
            resolved_image_id = str(image_id or "").strip()
            if not resolved_image_id:
                raise ValueError("Provide either lineage_run_id or image_id.")
            resolved = self._resolve_lineage_run_id_by_image_id(resolved_image_id)
            if resolved is None:
                raise ValueError(f"No lineage found for image_id: {resolved_image_id}")
            resolved_run_id = resolved["lineage_run_id"]
        lineage = self._read_lineage(resolved_run_id)
        execution = self._read_optional_json(self._execution_path(resolved_run_id)) or {}
        params = self._run_store.read_params(resolved_run_id) or {}
        meta = self._run_store.read_meta(resolved_run_id) or {}
        return {
            "lineage_run_id": resolved_run_id,
            "lineage": lineage,
            "execution": execution,
            "meta": meta,
            "params": params,
        }

    def register_results(
        self,
        lineage_run_id: str,
        results: list[dict[str, Any]],
        token: str | None = None,
    ) -> dict[str, Any]:
        self._policy.enforce_mutation(token)
        run_id = str(lineage_run_id or "").strip()
        if not run_id:
            raise ValueError("lineage_run_id is required")
        lineage = self._read_lineage(run_id)
        execution = self._read_optional_json(self._execution_path(run_id)) or {}
        normalized_results: list[dict[str, Any]] = []
        result_image_ids: list[str] = []
        for item in results or []:
            if not isinstance(item, dict):
                continue
            image_id = str(item.get("image_id") or item.get("imageId") or "").strip()
            if not image_id:
                continue
            normalized = {
                "image_id": image_id,
                "namespace": str(item.get("namespace") or "").strip() or None,
                "filename": str(item.get("filename") or "").strip() or None,
                "local_path": str(item.get("local_path") or item.get("localPath") or "").strip() or None,
            }
            normalized_results.append(normalized)
            result_image_ids.append(image_id)
            self._append_index("by_result_image_id", image_id, run_id)
        execution["registered_results"] = normalized_results
        execution["result_image_ids"] = self._unique_strings(result_image_ids)
        execution["updated_at"] = self._now_iso()
        self._write_execution(run_id, execution)
        lineage["result_image_ids"] = self._unique_strings(
            [*lineage.get("result_image_ids", []), *result_image_ids]
        )
        lineage["updated_at"] = self._now_iso()
        self._write_lineage(run_id, lineage)
        return {
            "lineage_run_id": run_id,
            "registered_results": normalized_results,
            "result_image_ids": execution["result_image_ids"],
        }

    async def _continue_prepared(
        self,
        *,
        lineage_run_id: str,
        user_overrides: dict[str, Any],
        wait_timeout_s: float,
        wait_poll_ms: int,
        force: bool,
    ) -> dict[str, Any]:
        lineage = self._read_lineage(lineage_run_id)
        params_payload = self._run_store.read_params(lineage_run_id)
        if not params_payload:
            raise ValueError(f"Params missing for lineage run: {lineage_run_id}")
        spec = ParamSpec.model_validate(params_payload)
        applied_defaults = self._applied_defaults(lineage=lineage, spec=spec, user_overrides=user_overrides)
        final_overrides = dict(applied_defaults)
        final_overrides.update(user_overrides)
        missing_required = self._missing_required_inputs(spec=spec, overrides=final_overrides)
        if missing_required:
            lineage["status"] = "needs_input"
            lineage["applied_defaults"] = applied_defaults
            lineage["missing_required_inputs"] = missing_required
            lineage["updated_at"] = self._now_iso()
            self._write_lineage(lineage_run_id, lineage)
            self._write_execution(
                lineage_run_id,
                {
                    "status": "needs_input",
                    "applied_defaults": applied_defaults,
                    "missing_required_inputs": missing_required,
                    "updated_at": self._now_iso(),
                },
            )
            return {
                "status": "needs_input",
                "lineage_run_id": lineage_run_id,
                "resume_token": lineage_run_id,
                "workflow_family": lineage.get("workflow_family"),
                "resolved_source": lineage.get("resolved_source"),
                "cache_hit": bool(lineage.get("cache_hit")),
                "effective_params": params_payload.get("params", []),
                "applied_defaults": applied_defaults,
                "missing_required_inputs": missing_required,
            }
        run_result = await self._execution_service.run(
            lineage_run_id,
            final_overrides,
            force=force,
            wait_timeout_s=wait_timeout_s,
            wait_poll_ms=wait_poll_ms,
        )
        input_bindings = lineage.get("input_bindings", {})
        if not isinstance(input_bindings, dict):
            input_bindings = {}
        for name in lineage.get("load_image_params", []):
            value = final_overrides.get(name)
            if not isinstance(value, str) or not value.strip():
                continue
            input_bindings[name] = {
                "local_path": value,
                "source_image_id": input_bindings.get(name, {}).get("source_image_id")
                if isinstance(input_bindings.get(name), dict)
                else None,
            }
        lineage["input_bindings"] = input_bindings
        execution_payload = {
            "status": "complete",
            "prompt_id": run_result.get("prompt_id"),
            "applied_defaults": applied_defaults,
            "user_overrides": user_overrides,
            "final_overrides": final_overrides,
            "result": run_result,
            "workflow_hash": params_payload.get("workflow_hash"),
            "updated_at": self._now_iso(),
        }
        self._write_execution(lineage_run_id, execution_payload)
        lineage["status"] = "complete"
        lineage["applied_defaults"] = applied_defaults
        lineage["missing_required_inputs"] = []
        lineage["updated_at"] = self._now_iso()
        self._write_lineage(lineage_run_id, lineage)
        return {
            "status": "complete",
            "lineage_run_id": lineage_run_id,
            "workflow_family": lineage.get("workflow_family"),
            "resolved_source": lineage.get("resolved_source"),
            "cache_hit": bool(lineage.get("cache_hit")),
            "effective_params": params_payload.get("params", []),
            "applied_defaults": applied_defaults,
            "missing_required_inputs": [],
            **run_result,
        }

    async def _prepare_from_source(
        self,
        *,
        lineage_run_id: str,
        lookup: dict[str, Any],
        namespace: str | None,
        photarium_mcp_url: str,
    ) -> dict[str, Any]:
        run_dir = self._run_store.root / lineage_run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir = run_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        requested_source = dict(lookup)
        requested_source["namespace"] = namespace

        workflow_json: dict[str, Any]
        extraction_source = "artifact_file"
        source_payload = "workflow"
        source_image_ids: list[str] = []
        source_artifact_path: str | None = None
        artifact_sha256: str | None = None

        normalized_kind = str(lookup["normalized_kind"])
        normalized_source = str(lookup["normalized_source"])
        if normalized_kind == "photarium_id":
            source_image_ids = [normalized_source]
            source_artifact_path = await self._download_photarium_source(
                image_id=normalized_source,
                target_dir=artifacts_dir,
                photarium_mcp_url=photarium_mcp_url,
                namespace=namespace,
            )
            artifact_sha256 = self._sha256_file(Path(source_artifact_path))
            extracted = await self._artifact_service.extract_from_photarium(
                image_id=normalized_source,
                photarium_mcp_url=photarium_mcp_url,
                namespace=namespace,
                prefer_prompt=True,
                include_raw_metadata=False,
                preserve_format=False,
            )
            workflow_json = dict(extracted.get("workflow") or {})
            extraction_source = str(extracted.get("extraction_source") or "photarium_extract")
            source_payload = str(extracted.get("source_payload") or "workflow")
        elif normalized_kind == "url":
            downloaded = await self._download_url_artifact(normalized_source, artifacts_dir)
            source_artifact_path = str(downloaded)
            artifact_sha256 = self._sha256_file(downloaded)
            extracted = self._artifact_service.extract_from_artifact(str(downloaded), preserve_format=False)
            workflow_json = dict(extracted.get("workflow") or {})
        else:
            original = Path(normalized_source).expanduser().resolve()
            if not original.exists() or not original.is_file():
                raise ValueError(f"Source file not found: {normalized_source}")
            copied = self._copy_file_to_dir(original, artifacts_dir, preferred_name="source" + original.suffix)
            source_artifact_path = str(copied)
            artifact_sha256 = self._sha256_file(copied)
            extracted = self._artifact_service.extract_from_artifact(str(copied), preserve_format=False)
            workflow_json = dict(extracted.get("workflow") or {})

        if not workflow_json:
            raise ValueError("No workflow could be extracted from source.")

        packaged = package_workflow(lineage_run_id, workflow_json, include_suggestions=True)
        curated_params, workflow_family, load_image_params = self._curate_params(
            workflow_id=lineage_run_id,
            workflow_json=workflow_json,
            params_payload=packaged["params"],
            cached_lineage=None,
        )
        meta = packaged["meta"]
        meta["id"] = lineage_run_id
        meta["name"] = lineage_run_id
        meta["requires"] = {
            "inputs": list(meta.get("requires", {}).get("inputs", [])) if isinstance(meta.get("requires"), dict) else [],
            "params": [item.get("name") for item in curated_params.get("params", []) if isinstance(item, dict)],
        }
        self._run_store.save_workflow(lineage_run_id, workflow_json, meta=meta, params=curated_params)
        self._append_index("by_workflow_hash", curated_params["workflow_hash"], lineage_run_id)
        if artifact_sha256:
            self._append_index("by_artifact_sha256", artifact_sha256, lineage_run_id)
        for image_id in source_image_ids:
            self._append_index("by_source_image_id", image_id, lineage_run_id)
        input_bindings: dict[str, dict[str, Any]] = {}
        if workflow_family == "image_to_image" and load_image_params and source_artifact_path:
            input_bindings[load_image_params[0]] = {
                "local_path": source_artifact_path,
                "source_image_id": source_image_ids[0] if source_image_ids else None,
            }
        return {
            "lineage_run_id": lineage_run_id,
            "status": "prepared",
            "created_at": self._now_iso(),
            "updated_at": self._now_iso(),
            "cache_hit": False,
            "cache_parent_run_id": None,
            "cache_matched_via": None,
            "requested_source": requested_source,
            "resolved_source": {
                "kind": normalized_kind,
                "value": normalized_source,
                "namespace": namespace,
                "local_artifact_path": source_artifact_path,
                "artifact_sha256": artifact_sha256,
                "source_image_ids": source_image_ids,
            },
            "workflow_family": workflow_family,
            "workflow_hash": curated_params["workflow_hash"],
            "extraction_source": extraction_source,
            "source_payload": source_payload,
            "effective_params": curated_params.get("params", []),
            "load_image_params": load_image_params,
            "input_bindings": input_bindings,
            "source_image_ids": source_image_ids,
            "result_image_ids": [],
            "missing_required_inputs": [],
            "applied_defaults": {},
        }

    def _prepare_from_cached_lineage(
        self,
        *,
        lineage_run_id: str,
        lookup: dict[str, Any],
        cached_lineage_run_id: str,
        cached_via: str,
    ) -> dict[str, Any]:
        workflow_json = self._run_store.read_workflow(cached_lineage_run_id)
        params_payload = copy.deepcopy(self._run_store.read_params(cached_lineage_run_id) or {})
        meta_payload = copy.deepcopy(self._run_store.read_meta(cached_lineage_run_id) or {})
        cached_lineage = self._read_lineage(cached_lineage_run_id)

        params_payload["workflow_id"] = lineage_run_id
        meta_payload["id"] = lineage_run_id
        meta_payload["name"] = lineage_run_id
        self._run_store.save_workflow(lineage_run_id, workflow_json, meta=meta_payload, params=params_payload)
        self._append_index("by_workflow_hash", params_payload["workflow_hash"], lineage_run_id)
        artifact_sha256 = (
            cached_lineage.get("resolved_source", {}).get("artifact_sha256")
            if isinstance(cached_lineage.get("resolved_source"), dict)
            else None
        )
        if isinstance(artifact_sha256, str) and artifact_sha256:
            self._append_index("by_artifact_sha256", artifact_sha256, lineage_run_id)
        for image_id in cached_lineage.get("source_image_ids", []):
            self._append_index("by_source_image_id", image_id, lineage_run_id)
        return {
            "lineage_run_id": lineage_run_id,
            "status": "prepared",
            "created_at": self._now_iso(),
            "updated_at": self._now_iso(),
            "cache_hit": True,
            "cache_parent_run_id": cached_lineage_run_id,
            "cache_matched_via": cached_via,
            "requested_source": {
                "kind": lookup["normalized_kind"],
                "value": lookup["normalized_source"],
            },
            "resolved_source": copy.deepcopy(cached_lineage.get("resolved_source", {})),
            "workflow_family": cached_lineage.get("workflow_family"),
            "workflow_hash": params_payload.get("workflow_hash"),
            "extraction_source": "lineage_cache",
            "source_payload": "workflow",
            "effective_params": params_payload.get("params", []),
            "load_image_params": list(cached_lineage.get("load_image_params", [])),
            "input_bindings": copy.deepcopy(cached_lineage.get("input_bindings", {})),
            "source_image_ids": list(cached_lineage.get("source_image_ids", [])),
            "result_image_ids": [],
            "missing_required_inputs": [],
            "applied_defaults": {},
        }

    def _resolve_cached_lineage(self, lookup: dict[str, Any]) -> dict[str, Any] | None:
        normalized_kind = str(lookup["normalized_kind"])
        normalized_source = str(lookup["normalized_source"])
        if normalized_kind == "photarium_id":
            for matched_via in ("by_result_image_id", "by_source_image_id"):
                hit = self._read_index_entry(matched_via, normalized_source)
                if hit is not None:
                    return {"lineage_run_id": hit["latest_run_id"], "matched_via": matched_via}
            return None
        if normalized_kind in {"file_path", "url"}:
            artifact_hash = self._lookup_artifact_hash_for_source(lookup)
            if artifact_hash:
                hit = self._read_index_entry("by_artifact_sha256", artifact_hash)
                if hit is not None:
                    return {"lineage_run_id": hit["latest_run_id"], "matched_via": "by_artifact_sha256"}
        return None

    def _lookup_artifact_hash_for_source(self, lookup: dict[str, Any]) -> str | None:
        normalized_kind = str(lookup["normalized_kind"])
        normalized_source = str(lookup["normalized_source"])
        if normalized_kind == "file_path":
            path = Path(normalized_source).expanduser().resolve()
            if path.exists() and path.is_file():
                return self._sha256_file(path)
        return None

    def _resolve_lineage_run_id_by_image_id(self, image_id: str) -> dict[str, Any] | None:
        for matched_via in ("by_result_image_id", "by_source_image_id"):
            hit = self._read_index_entry(matched_via, image_id)
            if hit is not None:
                return {"lineage_run_id": hit["latest_run_id"], "matched_via": matched_via}
        return None

    def _curate_params(
        self,
        *,
        workflow_id: str,
        workflow_json: dict[str, Any],
        params_payload: dict[str, Any],
        cached_lineage: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], str, list[str]]:
        spec = ParamSpec.model_validate(params_payload)
        family, load_image_params = self._classify_workflow(workflow_json=workflow_json, spec=spec)
        param_map = spec.param_map()
        selected_names: list[str] = []
        for name in load_image_params:
            if name in param_map:
                selected_names.append(name)
        for name in self._PROMPT_PARAM_NAMES:
            if name in param_map:
                selected_names.append(name)
        for name in self._SCALAR_PARAM_NAMES:
            if name in param_map:
                selected_names.append(name)
        for name in self._RESOLUTION_PARAM_NAMES:
            if name in param_map:
                selected_names.append(name)

        selected_items = []
        cached_bindings = cached_lineage.get("input_bindings", {}) if isinstance(cached_lineage, dict) else {}
        for name in self._unique_strings(selected_names):
            item = copy.deepcopy(param_map[name])
            if family == "image_stitch" and name in load_image_params:
                item.required = name not in cached_bindings
            else:
                item.required = False
            selected_items.append(item.model_dump())
        curated = {
            "schema_version": "1",
            "workflow_id": workflow_id,
            "workflow_hash": sha256_json(workflow_json),
            "params": selected_items,
        }
        return curated, family, load_image_params

    def _classify_workflow(self, *, workflow_json: dict[str, Any], spec: ParamSpec) -> tuple[str, list[str]]:
        load_image_params: list[tuple[int, str]] = []
        for item in spec.params:
            target = item.target
            if target.mode != "direct" or not target.node_id or target.input != "image":
                continue
            node = workflow_json.get(str(target.node_id))
            if not isinstance(node, dict):
                continue
            if str(node.get("class_type") or "").lower() != "loadimage":
                continue
            node_id = int(target.node_id) if str(target.node_id).isdigit() else 0
            load_image_params.append((node_id, item.name))
        sorted_names = [name for _, name in sorted(load_image_params, key=lambda item: item[0])]
        load_count = len(sorted_names)
        if load_count == 0:
            return "text_to_image", []
        if load_count == 1:
            return "image_to_image", sorted_names
        return "image_stitch", sorted_names

    def _applied_defaults(self, *, lineage: dict[str, Any], spec: ParamSpec, user_overrides: dict[str, Any]) -> dict[str, Any]:
        applied: dict[str, Any] = {}
        family = str(lineage.get("workflow_family") or "")
        input_bindings = lineage.get("input_bindings", {})
        if not isinstance(input_bindings, dict):
            input_bindings = {}
        if family == "image_to_image":
            for name in lineage.get("load_image_params", [])[:1]:
                if name in user_overrides:
                    continue
                binding = input_bindings.get(name)
                if isinstance(binding, dict):
                    local_path = str(binding.get("local_path") or "").strip()
                    if local_path:
                        applied[name] = local_path
        elif family == "image_stitch":
            for name in lineage.get("load_image_params", []):
                if name in user_overrides:
                    continue
                binding = input_bindings.get(name)
                if isinstance(binding, dict):
                    local_path = str(binding.get("local_path") or "").strip()
                    if local_path:
                        applied[name] = local_path
        return applied

    def _missing_required_inputs(self, *, spec: ParamSpec, overrides: dict[str, Any]) -> list[dict[str, Any]]:
        missing: list[dict[str, Any]] = []
        for item in spec.params:
            if not item.required:
                continue
            if item.name in overrides:
                continue
            missing.append({"name": item.name, "description": item.description})
        return missing

    def _normalize_source_lookup(self, *, source: str, source_kind: str) -> dict[str, Any]:
        normalized_kind = str(source_kind or "auto").strip().lower() or "auto"
        raw = str(source).strip()
        if not raw:
            raise ValueError("source must be non-empty")
        if normalized_kind == "auto":
            if raw.startswith("http://") or raw.startswith("https://"):
                normalized_kind = "url"
            else:
                candidate = Path(raw).expanduser()
                if candidate.exists():
                    normalized_kind = "file_path"
                else:
                    normalized_kind = "photarium_id"
        if normalized_kind not in {"photarium_id", "url", "file_path"}:
            raise ValueError(f"Unsupported source_kind: {source_kind}")
        if normalized_kind == "file_path":
            normalized_source = str(Path(raw).expanduser().resolve())
        else:
            normalized_source = raw
        return {
            "source_kind": source_kind,
            "normalized_kind": normalized_kind,
            "normalized_source": normalized_source,
        }

    async def _download_url_artifact(self, url: str, target_dir: Path) -> Path:
        parsed = urlparse(url)
        suffix = Path(parsed.path).suffix or ".bin"
        target = target_dir / f"source{suffix}"
        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            target.write_bytes(response.content)
        return target

    async def _download_photarium_source(
        self,
        *,
        image_id: str,
        target_dir: Path,
        photarium_mcp_url: str,
        namespace: str | None,
    ) -> str:
        target = target_dir / f"source_{image_id}.bin"
        args: dict[str, Any] = {"imageId": image_id, "savePath": str(target), "includeBase64": False}
        if namespace:
            args["namespace"] = namespace
        try:
            result = await self._remote_client.call(photarium_mcp_url, "photarium_download_original", args)
        except Exception:
            result = await self._remote_client.call(photarium_mcp_url, "photarium_download_image", args)
        saved_path = result.get("savedPath") if isinstance(result, dict) else None
        candidate = Path(saved_path).expanduser().resolve() if isinstance(saved_path, str) and saved_path.strip() else target
        if candidate.is_dir():
            filename = str(result.get("filename") or "").strip() if isinstance(result, dict) else ""
            if filename:
                candidate = candidate / filename
        if not candidate.exists():
            raise ValueError(f"Downloaded Photarium source missing on disk for image_id={image_id}")
        return str(candidate)

    def _copy_file_to_dir(self, source: Path, target_dir: Path, preferred_name: str | None = None) -> Path:
        target_name = preferred_name or source.name
        target = target_dir / target_name
        shutil.copy2(source, target)
        return target

    def _write_lineage(self, lineage_run_id: str, payload: dict[str, Any]) -> None:
        self._write_json(self._lineage_path(lineage_run_id), payload)

    def _read_lineage(self, lineage_run_id: str) -> dict[str, Any]:
        path = self._lineage_path(lineage_run_id)
        if not path.exists():
            raise ValueError(f"Lineage not found: {lineage_run_id}")
        return self._read_json(path)

    def _write_execution(self, lineage_run_id: str, payload: dict[str, Any]) -> None:
        self._write_json(self._execution_path(lineage_run_id), payload)

    def _lineage_path(self, lineage_run_id: str) -> Path:
        return self._run_store.root / lineage_run_id / "lineage.json"

    def _execution_path(self, lineage_run_id: str) -> Path:
        return self._run_store.root / lineage_run_id / "execution.json"

    def _append_index(self, index_name: str, key: str, lineage_run_id: str) -> None:
        path = self._index_path(index_name, key)
        payload = self._read_optional_json(path) or {"key": key, "run_ids": [], "latest_run_id": None}
        run_ids = [str(item) for item in payload.get("run_ids", []) if str(item).strip()]
        if lineage_run_id not in run_ids:
            run_ids.append(lineage_run_id)
        payload["run_ids"] = run_ids
        payload["latest_run_id"] = lineage_run_id
        self._write_json(path, payload)

    def _read_index_entry(self, index_name: str, key: str) -> dict[str, Any] | None:
        return self._read_optional_json(self._index_path(index_name, key))

    def _index_path(self, index_name: str, key: str) -> Path:
        path = self._index_root / index_name / f"{quote(str(key), safe='')}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _read_optional_json(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True)
            handle.write("\n")

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _new_run_id() -> str:
        return f"run_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _unique_strings(values: list[Any]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in values:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
        return out
