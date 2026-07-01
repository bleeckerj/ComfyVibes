"""Workflow MCP tool handlers facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from comfy_mcp.comfy_client.client import ComfyClient
from comfy_mcp.extraction.adapter import WorkflowExtractor
from comfy_mcp.mcp_server.policy import Policy
from comfy_mcp.mcp_server.remote_tool_client import RemoteToolClient
from comfy_mcp.mcp_server.workflow_admin_service import WorkflowAdminService
from comfy_mcp.mcp_server.workflow_artifact_service import WorkflowArtifactService
from comfy_mcp.mcp_server.workflow_catalog_service import WorkflowCatalogService
from comfy_mcp.mcp_server.workflow_execution_service import WorkflowExecutionService
from comfy_mcp.mcp_server.workflow_image_service import WorkflowImageService
from comfy_mcp.mcp_server.workflow_lineage_service import WorkflowLineageService
from comfy_mcp.reasoning.service import WorkflowReasoningService
from comfy_mcp.workflow_store.store import WorkflowStore


class WorkflowTools:
    """Facade that preserves MCP tool method names while delegating behavior."""

    def __init__(
        self,
        store: WorkflowStore,
        client: ComfyClient,
        policy: Policy,
        extractor: WorkflowExtractor | None = None,
        reasoning: WorkflowReasoningService | None = None,
        comfy_output_dir: Path | None = None,
        run_store: WorkflowStore | None = None,
        comfy_org_extra_data: dict[str, str] | None = None,
    ) -> None:
        remote_client = RemoteToolClient()
        self._default_remote_call = remote_client.call
        image_service = WorkflowImageService()
        reasoning_service = reasoning or WorkflowReasoningService(store)
        self._catalog = WorkflowCatalogService(store, reasoning_service)
        self._admin = WorkflowAdminService(store, policy)
        self._artifact = WorkflowArtifactService(store, policy, extractor=extractor, remote_client=remote_client)
        self._execution = WorkflowExecutionService(
            store,
            client,
            policy,
            comfy_output_dir=comfy_output_dir,
            remote_client=remote_client,
            image_service=image_service,
            comfy_org_extra_data=comfy_org_extra_data,
        )
        lineage_store = run_store or WorkflowStore(Path.cwd() / "run_workflows")
        self._lineage_execution = WorkflowExecutionService(
            lineage_store,
            client,
            policy,
            comfy_output_dir=comfy_output_dir,
            remote_client=remote_client,
            image_service=image_service,
            comfy_org_extra_data=comfy_org_extra_data,
        )
        self._lineage = WorkflowLineageService(
            lineage_store,
            policy,
            self._artifact,
            self._lineage_execution,
            remote_client=remote_client,
        )
        self._default_wait_and_extract = self._execution._wait_and_extract
        self._default_lineage_wait_and_extract = self._lineage_execution._wait_and_extract
        self._image = image_service

    def list(self) -> dict[str, Any]:
        return self._catalog.list()

    def search(self, query: str, limit: int = 20, tags: list[str] | None = None) -> dict[str, Any]:
        return self._catalog.search(query, limit=limit, tags=tags)

    def capabilities_list(self, query: str = "", limit: int = 20, tags: list[str] | None = None, include_params: bool = False) -> dict[str, Any]:
        return self._catalog.capabilities_list(query=query, limit=limit, tags=tags, include_params=include_params)

    def capabilities_get(self, workflow_id: str, include_params: bool = True) -> dict[str, Any]:
        return self._catalog.capabilities_get(workflow_id, include_params=include_params)

    def get(self, workflow_id: str) -> dict[str, Any]:
        return self._catalog.get(workflow_id)

    def params_get(self, workflow_id: str) -> dict[str, Any]:
        return self._catalog.params_get(workflow_id)

    def package(self, workflow_id: str, hints: dict[str, Any] | None = None, include_suggestions: bool = True, token: str | None = None) -> dict[str, Any]:
        return self._admin.package(workflow_id, hints=hints, include_suggestions=include_suggestions, token=token)

    def package_many(self, workflow_ids: list[str] | None = None, hints_by_workflow: dict[str, dict[str, Any]] | None = None, include_suggestions: bool = True, token: str | None = None) -> dict[str, Any]:
        return self._admin.package_many(workflow_ids=workflow_ids, hints_by_workflow=hints_by_workflow, include_suggestions=include_suggestions, token=token)

    def recompile(self, workflow_id: str, workflow_input_updates: list[dict[str, Any]] | None = None, param_overrides: list[dict[str, Any]] | None = None, hints: dict[str, Any] | None = None, include_suggestions: bool = True, token: str | None = None) -> dict[str, Any]:
        return self._admin.recompile(workflow_id, workflow_input_updates=workflow_input_updates, param_overrides=param_overrides, hints=hints, include_suggestions=include_suggestions, token=token)

    def package_template_get(self, workflow_id: str) -> dict[str, Any]:
        return self._catalog.package_template_get(workflow_id)

    def folder_create(self, folder_path: str, token: str | None = None) -> dict[str, Any]:
        return self._admin.folder_create(folder_path, token=token)

    def file_write(self, file_path: str, payload: dict[str, Any], overwrite: bool = False, token: str | None = None) -> dict[str, Any]:
        return self._admin.file_write(file_path, payload, overwrite=overwrite, token=token)

    def file_edit(self, file_path: str, updates: dict[str, Any], token: str | None = None) -> dict[str, Any]:
        return self._admin.file_edit(file_path, updates, token=token)

    def file_delete(self, file_path: str, token: str | None = None) -> dict[str, Any]:
        return self._admin.file_delete(file_path, token=token)

    def file_read(self, file_path: str, encoding: str = "utf-8") -> dict[str, Any]:
        return self._admin.file_read(file_path, encoding=encoding)

    def file_copy(self, source_path: str, destination_path: str, overwrite: bool = False, token: str | None = None) -> dict[str, Any]:
        return self._admin.file_copy(source_path, destination_path, overwrite=overwrite, token=token)

    def save(self, workflow_id: str, workflow_json: dict[str, Any], meta: dict[str, Any] | None = None, params: dict[str, Any] | None = None, token: str | None = None) -> dict[str, Any]:
        return self._admin.save(workflow_id, workflow_json, meta=meta, params=params, token=token)

    def delete(self, workflow_id: str, token: str | None = None) -> dict[str, Any]:
        return self._admin.delete(workflow_id, token=token)

    def extract_from_artifact(self, path: str, preserve_format: bool = False) -> dict[str, Any]:
        return self._artifact.extract_from_artifact(path, preserve_format=preserve_format)

    def import_from_artifact(self, path: str, workflow_id: str, name: str | None = None, tags: list[str] | None = None, token: str | None = None) -> dict[str, Any]:
        return self._artifact.import_from_artifact(path, workflow_id, name=name, tags=tags, token=token)

    async def import_from_photarium(self, image_id: str, workflow_id: str, photarium_mcp_url: str = "http://127.0.0.1:8787", namespace: str | None = None, name: str | None = None, tags: list[str] | None = None, hints: dict[str, Any] | None = None, include_suggestions: bool = True, prefer_prompt: bool = True, include_raw_metadata: bool = False, token: str | None = None) -> dict[str, Any]:
        remote_call = self._artifact._remote_client.call
        self._artifact._remote_client.call = self._call_remote_tool
        try:
            return await self._artifact.import_from_photarium(image_id=image_id, workflow_id=workflow_id, photarium_mcp_url=photarium_mcp_url, namespace=namespace, name=name, tags=tags, hints=hints, include_suggestions=include_suggestions, prefer_prompt=prefer_prompt, include_raw_metadata=include_raw_metadata, token=token)
        finally:
            self._artifact._remote_client.call = remote_call

    async def extract_from_photarium(self, image_id: str, photarium_mcp_url: str = "http://127.0.0.1:8787", namespace: str | None = None, prefer_prompt: bool = True, include_raw_metadata: bool = False, preserve_format: bool = False, token: str | None = None) -> dict[str, Any]:
        remote_call = self._artifact._remote_client.call
        self._artifact._remote_client.call = self._call_remote_tool
        try:
            return await self._artifact.extract_from_photarium(image_id=image_id, photarium_mcp_url=photarium_mcp_url, namespace=namespace, prefer_prompt=prefer_prompt, include_raw_metadata=include_raw_metadata, preserve_format=preserve_format, token=token)
        finally:
            self._artifact._remote_client.call = remote_call

    async def run(self, workflow_id: str, overrides: dict[str, Any], client_id: str | None = None, token: str | None = None, force: bool = False, wait_timeout_s: float = 300.0, wait_poll_ms: int = 1000) -> dict[str, Any]:
        wait_impl = self._execution._wait_and_extract
        self._execution._wait_and_extract = self._wait_and_extract
        try:
            return await self._execution.run(workflow_id, overrides, client_id=client_id, token=token, force=force, wait_timeout_s=wait_timeout_s, wait_poll_ms=wait_poll_ms)
        finally:
            self._execution._wait_and_extract = wait_impl

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
        remote_call = self._artifact._remote_client.call
        wait_impl = self._lineage_execution._wait_and_extract
        self._artifact._remote_client.call = self._call_remote_tool
        self._lineage._remote_client.call = self._call_remote_tool
        self._lineage_execution._wait_and_extract = self._wait_and_extract
        try:
            return await self._lineage.run_from_source(
                source=source,
                source_kind=source_kind,
                overrides=overrides,
                resume_token=resume_token,
                namespace=namespace,
                photarium_mcp_url=photarium_mcp_url,
                wait_timeout_s=wait_timeout_s,
                wait_poll_ms=wait_poll_ms,
                force=force,
                token=token,
            )
        finally:
            self._artifact._remote_client.call = remote_call
            self._lineage._remote_client.call = remote_call
            self._lineage_execution._wait_and_extract = wait_impl

    def lineage_register_results(self, lineage_run_id: str, results: list[dict[str, Any]], token: str | None = None) -> dict[str, Any]:
        return self._lineage.register_results(lineage_run_id=lineage_run_id, results=results, token=token)

    def lineage_get(self, lineage_run_id: str | None = None, image_id: str | None = None) -> dict[str, Any]:
        return self._lineage.lineage_get(lineage_run_id=lineage_run_id, image_id=image_id)

    def image_info(self, file_path: str) -> dict[str, Any]:
        return self._image.image_info(file_path)

    async def status(self, prompt_id: str | None = None, include_progress: bool = True, progress_timeout_s: float = 0.75) -> dict[str, Any]:
        return await self._execution.status(prompt_id=prompt_id, include_progress=include_progress, progress_timeout_s=progress_timeout_s)

    async def watch(self, prompt_id: str, inactivity_timeout_s: float = 120.0, max_wait_s: float = 1800.0, poll_ms: int = 500, include_history: bool = True) -> dict[str, Any]:
        return await self._execution.watch(prompt_id, inactivity_timeout_s=inactivity_timeout_s, max_wait_s=max_wait_s, poll_ms=poll_ms, include_history=include_history)

    async def wait(self, prompt_id: str, timeout_s: float = 60.0, poll_ms: int = 500) -> dict[str, Any]:
        return await self._execution.wait(prompt_id, timeout_s=timeout_s, poll_ms=poll_ms)

    async def run_aspect_ratio_adjustment(self, image_path: str, aspect_ratio: str, workflow_id: str = "aspect_ratio_adjustment", positive_prompt: str | None = None, negative_prompt: str | None = None, seed: int | None = None, output_base_name: str | None = None, client_id: str | None = None, token: str | None = None, upload_subfolder: str | None = None, overwrite: bool = False, wait_timeout_s: float = 300.0, wait_poll_ms: int = 1000) -> dict[str, Any]:
        wait_impl = self._execution._wait_and_extract
        self._execution._wait_and_extract = self._wait_and_extract
        try:
            return await self._execution.run_aspect_ratio_adjustment(image_path=image_path, aspect_ratio=aspect_ratio, workflow_id=workflow_id, positive_prompt=positive_prompt, negative_prompt=negative_prompt, seed=seed, output_base_name=output_base_name, client_id=client_id, token=token, upload_subfolder=upload_subfolder, overwrite=overwrite, wait_timeout_s=wait_timeout_s, wait_poll_ms=wait_poll_ms)
        finally:
            self._execution._wait_and_extract = wait_impl

    async def _wait_and_extract(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._default_wait_and_extract(*args, **kwargs)

    async def _call_remote_tool(self, base_url: str, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        return await self._default_remote_call(base_url, tool_name, args)
