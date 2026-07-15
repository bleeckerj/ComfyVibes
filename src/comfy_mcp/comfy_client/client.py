"""Async ComfyUI HTTP client."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

import httpx

from comfy_mcp.comfy_client.contracts import (
    ComfyAssets,
    ComfyEmbeddings,
    ComfyFeatures,
    ComfyHistory,
    ComfyJobList,
    ComfyModelFiles,
    ComfyModelTypes,
    ComfyObjectInfo,
    ComfyPromptResult,
    ComfyQueue,
    ComfySystemStats,
    ComfyTags,
    ComfyWorkflowTemplates,
)
from comfy_mcp.comfy_client.errors import ComfyClientError


_MISSING = object()


class ComfyClient:
    """Minimal async client for ComfyUI API endpoints used by ComfyMCP."""

    def __init__(
        self,
        base_url: str,
        timeout_s: float = 30.0,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._base_url = base_url
        self._timeout_s = timeout_s
        self._client = http_client or httpx.AsyncClient(base_url=base_url, timeout=timeout_s)

    async def get_object_info(self) -> ComfyObjectInfo:
        """Fetch ComfyUI node catalog from `/object_info`."""
        return await self._get_json("/object_info")

    async def get_queue(self) -> ComfyQueue:
        """Fetch ComfyUI queue state from `/queue`."""
        return await self._get_json("/queue")

    async def get_history(self, prompt_id: Optional[str] = None) -> ComfyHistory:
        """Fetch ComfyUI history, optionally filtered by `prompt_id`."""
        path = f"/history/{self._quote_path_segment(prompt_id)}" if prompt_id else "/history"
        return await self._get_json(path)

    async def get_model_types(self) -> ComfyModelTypes:
        """Fetch available model type folders from `/models`."""
        return await self._get_json("/models")

    async def get_models_in_folder(self, folder: str) -> ComfyModelFiles:
        """Fetch model filenames for a given folder from `/models/{folder}`."""
        return await self._get_json(f"/models/{self._quote_path_segment(folder)}")

    async def get_embeddings(self) -> ComfyEmbeddings:
        """Fetch available embeddings from `/embeddings`."""
        return await self._get_json("/embeddings")

    async def get_features(self) -> ComfyFeatures:
        """Fetch ComfyUI feature flags from `/features`."""
        return await self._get_json("/features")

    async def get_system_stats(self) -> ComfySystemStats:
        """Fetch remote runtime and device statistics from `/system_stats`."""
        return await self._get_json("/system_stats")

    async def list_jobs(
        self,
        *,
        status: str | list[str] | None = None,
        workflow_id: str | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> ComfyJobList:
        """List normalized jobs from ComfyUI's `/api/jobs` endpoint."""
        params: dict[str, Any] = {}
        if status:
            params["status"] = ",".join(status) if isinstance(status, list) else status
        if workflow_id:
            params["workflow_id"] = workflow_id
        if sort_by:
            params["sort_by"] = sort_by
        if sort_order:
            params["sort_order"] = sort_order
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        return await self._get_json("/api/jobs", params=params)

    async def get_job(self, job_id: str) -> dict[str, Any]:
        """Fetch one normalized job by id."""
        return await self._get_json(f"/api/jobs/{self._quote_path_segment(job_id)}")

    async def get_workflow_templates(self) -> ComfyWorkflowTemplates:
        """List remote custom-node workflow templates."""
        return await self._get_json("/workflow_templates")

    async def get_workflow_template(self, pack: str, filename: str) -> Any:
        """Fetch one custom-node workflow template."""
        path = (
            f"/api/workflow_templates/{self._quote_path_segment(pack)}"
            f"/{self._quote_path_segment(filename)}"
        )
        return await self._get_json(path)

    async def get_global_subgraphs(self) -> dict[str, Any]:
        """List global subgraph metadata."""
        return await self._get_json("/global_subgraphs")

    async def get_global_subgraph(self, subgraph_id: str) -> dict[str, Any]:
        """Fetch one global subgraph by id."""
        return await self._get_json(f"/global_subgraphs/{self._quote_path_segment(subgraph_id)}")

    async def get_node_replacements(self) -> Any:
        """Fetch optional node replacement metadata."""
        return await self._get_json("/node_replacements")

    async def list_assets(
        self,
        *,
        include_tags: list[str] | None = None,
        exclude_tags: list[str] | None = None,
        name_contains: str | None = None,
        metadata_filter: dict[str, Any] | None = None,
        limit: int | None = None,
        offset: int | None = None,
        sort: str | None = None,
        order: str | None = None,
    ) -> ComfyAssets:
        """List ComfyUI assets through the optional assets subsystem."""
        params: list[tuple[str, Any]] = []
        for tag in include_tags or []:
            params.append(("include_tags", tag))
        for tag in exclude_tags or []:
            params.append(("exclude_tags", tag))
        if name_contains:
            params.append(("name_contains", name_contains))
        if metadata_filter is not None:
            params.append(("metadata_filter", json.dumps(metadata_filter, separators=(",", ":"))))
        if limit is not None:
            params.append(("limit", limit))
        if offset is not None:
            params.append(("offset", offset))
        if sort:
            params.append(("sort", sort))
        if order:
            params.append(("order", order))
        return await self._get_json("/api/assets", params=params)

    async def get_asset(self, asset_id: str) -> dict[str, Any]:
        """Fetch one ComfyUI asset record by id."""
        return await self._get_json(f"/api/assets/{self._quote_path_segment(asset_id)}")

    async def get_tags(
        self,
        *,
        prefix: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        order: str | None = None,
        include_zero: bool | None = None,
    ) -> ComfyTags:
        """List tags from the optional ComfyUI assets subsystem."""
        params: dict[str, Any] = {}
        if prefix:
            params["prefix"] = prefix
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        if order:
            params["order"] = order
        if include_zero is not None:
            params["include_zero"] = str(include_zero).lower()
        return await self._get_json("/api/tags", params=params)

    async def interrupt(self, prompt_id: str | None = None) -> dict[str, Any]:
        """Interrupt the current execution or one running prompt."""
        payload = {"prompt_id": prompt_id} if prompt_id else {}
        return await self._post_json("/interrupt", payload)

    async def delete_queue(
        self,
        *,
        clear_all: bool = False,
        prompt_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Clear all pending queue entries or delete selected prompt ids."""
        if clear_all and prompt_ids:
            raise ComfyClientError("clear_all and prompt_ids cannot be used together", category="validation")
        if not clear_all and not prompt_ids:
            raise ComfyClientError("one of clear_all or prompt_ids is required", category="validation")
        payload: dict[str, Any] = {"clear": True} if clear_all else {"delete": list(prompt_ids or [])}
        return await self._post_json("/queue", payload)

    async def free_memory(
        self,
        *,
        unload_models: bool = False,
        free_memory: bool = False,
    ) -> dict[str, Any]:
        """Request remote model unloading and/or memory release."""
        if not unload_models and not free_memory:
            raise ComfyClientError("one of unload_models or free_memory is required", category="validation")
        return await self._post_json(
            "/free",
            {"unload_models": unload_models, "free_memory": free_memory},
        )

    async def delete_history(
        self,
        *,
        clear_all: bool = False,
        prompt_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Clear all history entries or delete selected prompt ids."""
        if clear_all and prompt_ids:
            raise ComfyClientError("clear_all and prompt_ids cannot be used together", category="validation")
        if not clear_all and not prompt_ids:
            raise ComfyClientError("one of clear_all or prompt_ids is required", category="validation")
        payload: dict[str, Any] = {"clear": True} if clear_all else {"delete": list(prompt_ids or [])}
        return await self._post_json("/history", payload)

    async def get_settings(self, setting_id: str | None = None) -> Any:
        """Fetch all settings or one setting value."""
        path = "/settings"
        if setting_id:
            path = f"/settings/{self._quote_path_segment(setting_id)}"
        return await self._get_json(path)

    async def set_settings(
        self,
        settings: dict[str, Any] | None = None,
        *,
        setting_id: str | None = None,
        value: Any = _MISSING,
    ) -> dict[str, Any]:
        """Write all settings or one setting value."""
        if setting_id:
            if value is _MISSING:
                raise ComfyClientError("value is required when setting_id is provided", category="validation")
            path = f"/settings/{self._quote_path_segment(setting_id)}"
            return await self._post_json(path, value)
        if settings is None:
            raise ComfyClientError("settings is required when setting_id is omitted", category="validation")
        return await self._post_json("/settings", settings)

    async def queue_prompt(
        self,
        prompt: dict,
        client_id: Optional[str] = None,
        extra_data: Optional[dict[str, Any]] = None,
    ) -> ComfyPromptResult:
        """Submit a prompt graph to `/prompt`."""
        payload = {"prompt": prompt}
        if extra_data:
            payload["extra_data"] = dict(extra_data)
        if client_id:
            payload["client_id"] = client_id
        return await self._post_json("/prompt", payload)

    async def watch_prompt(
        self,
        prompt_id: str,
        *,
        inactivity_timeout_s: float = 120.0,
        max_wait_s: float = 1800.0,
        max_events: int = 200,
        client_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Watch prompt execution via websocket until terminal state or inactivity timeout.

        Returns structured status with recent normalized websocket events.
        """
        if not prompt_id:
            raise ComfyClientError("prompt_id is required")

        history = await self.get_history(prompt_id=prompt_id)
        if history:
            return {
                "status": "complete",
                "prompt_id": prompt_id,
                "events": [],
                "history": history,
                "source": "history",
            }

        ws_url = self._build_ws_url(client_id=client_id)
        events: list[Dict[str, Any]] = []
        terminal_status: str | None = None
        terminal_reason: str | None = None
        started_at = time.monotonic()
        last_activity_at = started_at

        try:
            async with self._ws_connect(ws_url) as websocket:
                while True:
                    now = time.monotonic()
                    if now - started_at >= max_wait_s:
                        terminal_status = "timeout"
                        terminal_reason = "max_wait_exceeded"
                        break
                    if now - last_activity_at >= inactivity_timeout_s:
                        terminal_status = "timeout"
                        terminal_reason = "no_progress"
                        break

                    remaining = min(
                        2.0,
                        max_wait_s - (now - started_at),
                        inactivity_timeout_s - (now - last_activity_at),
                    )
                    if remaining <= 0:
                        continue

                    try:
                        raw_event = await asyncio.wait_for(websocket.recv(), timeout=remaining)
                    except asyncio.TimeoutError:
                        history = await self.get_history(prompt_id=prompt_id)
                        if history:
                            terminal_status = "complete"
                            terminal_reason = "history_available"
                            break
                        queue = await self.get_queue()
                        if self._prompt_in_queue(prompt_id=prompt_id, queue_payload=queue):
                            last_activity_at = time.monotonic()
                        continue

                    event = self._normalize_ws_event(raw_event)
                    if event is None:
                        continue

                    event_prompt_id = event.get("prompt_id")
                    if event_prompt_id != prompt_id:
                        continue

                    last_activity_at = time.monotonic()
                    events.append(event)
                    if len(events) > max(1, int(max_events)):
                        events = events[-max_events:]

                    event_type = str(event.get("type") or "").lower()
                    node_value = event.get("node")
                    if event_type == "executing" and node_value is None:
                        terminal_status = "complete"
                        terminal_reason = "executing_node_none"
                        break
                    if event_type in {"execution_error", "execution_interrupted"}:
                        terminal_status = "error"
                        terminal_reason = event_type
                        break
                    if event_type in {"execution_success", "execution_done", "execution_complete"}:
                        terminal_status = "complete"
                        terminal_reason = event_type
                        break
        except ComfyClientError:
            raise
        except Exception as exc:
            raise ComfyClientError(f"ComfyUI websocket watch failed: {exc}") from exc

        history = await self._history_when_available(prompt_id=prompt_id, timeout_s=3.0, poll_ms=250)
        if history:
            terminal_status = terminal_status or "complete"

        return {
            "status": terminal_status or "timeout",
            "prompt_id": prompt_id,
            "reason": terminal_reason,
            "events": events,
            "history": history,
            "source": "websocket",
        }

    async def peek_progress(
        self,
        *,
        prompt_id: Optional[str] = None,
        timeout_s: float = 1.0,
        client_id: Optional[str] = None,
    ) -> Dict[str, Any] | None:
        """Return one normalized websocket progress event, if available quickly."""
        ws_url = self._build_ws_url(client_id=client_id)
        deadline = time.monotonic() + max(0.1, timeout_s)
        try:
            async with self._ws_connect(ws_url) as websocket:
                while time.monotonic() < deadline:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        raw_event = await asyncio.wait_for(websocket.recv(), timeout=remaining)
                    except asyncio.TimeoutError:
                        break
                    event = self._normalize_ws_event(raw_event)
                    if event is None:
                        continue
                    if prompt_id and event.get("prompt_id") != prompt_id:
                        continue
                    return event
        except ComfyClientError:
            raise
        except Exception as exc:
            raise ComfyClientError(f"ComfyUI websocket progress check failed: {exc}") from exc
        return None

    async def upload_image(
        self,
        file_path: str,
        image_type: str = "input",
        subfolder: Optional[str] = None,
        overwrite: bool = False,
    ) -> dict:
        """Upload an image to ComfyUI via `/upload/image`."""
        data = {"type": image_type, "overwrite": str(overwrite).lower()}
        if subfolder:
            data["subfolder"] = subfolder

        try:
            file_bytes = Path(file_path).read_bytes()
            files = {"image": (Path(file_path).name, file_bytes, "application/octet-stream")}
            response = await self._request("POST", "/upload/image", data=data, files=files)
        except ComfyClientError:
            raise
        except OSError as exc:
            raise ComfyClientError(f"Image upload failed: {exc}", category="local") from exc

        return self._decode_json(response)

    async def upload_mask(
        self,
        file_path: str,
        original_ref: dict[str, Any],
        image_type: str = "input",
        subfolder: Optional[str] = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Upload an alpha mask through ComfyUI's `/upload/mask` endpoint."""
        data: dict[str, Any] = {
            "original_ref": json.dumps(original_ref, separators=(",", ":")),
            "type": image_type,
            "overwrite": str(overwrite).lower(),
        }
        if subfolder:
            data["subfolder"] = subfolder
        try:
            file_bytes = Path(file_path).read_bytes()
            files = {"image": (Path(file_path).name, file_bytes, "application/octet-stream")}
            response = await self._request("POST", "/upload/mask", data=data, files=files)
        except ComfyClientError:
            raise
        except OSError as exc:
            raise ComfyClientError(f"Mask upload failed: {exc}", category="local") from exc
        return self._decode_json(response)

    async def upload_asset(
        self,
        file_path: str,
        *,
        tags: list[str],
        name: str | None = None,
        user_metadata: dict[str, Any] | None = None,
        content_hash: str | None = None,
    ) -> dict[str, Any]:
        """Upload a file to ComfyUI's `/api/assets` endpoint."""
        data: dict[str, str] = {"tags": json.dumps(tags, separators=(",", ":"))}
        if name:
            data["name"] = name
        if user_metadata is not None:
            data["user_metadata"] = json.dumps(user_metadata, separators=(",", ":"))
        if content_hash:
            data["hash"] = content_hash
        try:
            file_bytes = Path(file_path).read_bytes()
            files = {"file": (Path(file_path).name, file_bytes, "application/octet-stream")}
            response = await self._request("POST", "/api/assets", data=data, files=files)
        except ComfyClientError:
            raise
        except OSError as exc:
            raise ComfyClientError(f"Asset upload failed: {exc}", category="local") from exc
        return self._decode_json(response)

    async def download_image(
        self,
        filename: str,
        image_type: str = "output",
        subfolder: str = "",
    ) -> tuple[bytes, str]:
        """Download an image from ComfyUI's /view endpoint.

        Returns (raw_bytes, content_type).
        """
        params = {"filename": filename, "type": image_type}
        if subfolder:
            params["subfolder"] = subfolder
        try:
            response = await self._request("GET", "/view", params=params)
        except ComfyClientError as exc:
            raise ComfyClientError(
                f"ComfyUI download failed: {exc}",
                status_code=exc.status_code,
                endpoint=exc.endpoint,
                category=exc.category,
            ) from exc
        content_type = response.headers.get("content-type", "application/octet-stream")
        return response.content, content_type

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> "ComfyClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def _get_json(self, path: str, *, params: Any = None) -> Any:
        response = await self._request("GET", path, params=params)
        return self._decode_json(response)

    async def _post_json(self, path: str, payload: Any) -> dict[str, Any]:
        response = await self._request("POST", path, json_payload=payload)
        return self._decode_json(response)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Any = None,
        json_payload: Any = _MISSING,
        data: Any = None,
        files: Any = None,
    ) -> httpx.Response:
        request_kwargs: dict[str, Any] = {"params": params}
        if json_payload is not _MISSING:
            request_kwargs["json"] = json_payload
        if data is not None:
            request_kwargs["data"] = data
        if files is not None:
            request_kwargs["files"] = files
        try:
            response = await self._client.request(method, path, **request_kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            raise ComfyClientError(
                self._format_http_error(exc),
                status_code=exc.response.status_code,
                endpoint=path,
                category="unsupported" if exc.response.status_code in {404, 405, 501} else "remote",
            ) from exc
        except httpx.RequestError as exc:
            raise ComfyClientError(
                f"ComfyUI request failed: {exc}",
                endpoint=path,
                category="unreachable",
            ) from exc

    @staticmethod
    def _decode_json(response: httpx.Response) -> Any:
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise ComfyClientError(
                "ComfyUI returned a non-JSON response",
                status_code=response.status_code,
                category="remote",
            ) from exc

    @staticmethod
    def _format_http_error(exc: httpx.HTTPStatusError) -> str:
        response = exc.response
        detail = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                error_obj = payload.get("error")
                if isinstance(error_obj, dict):
                    parts = [
                        str(error_obj.get("type") or "").strip(),
                        str(error_obj.get("message") or "").strip(),
                        str(error_obj.get("details") or "").strip(),
                    ]
                    detail = " | ".join(part for part in parts if part)
                elif isinstance(error_obj, str):
                    detail = error_obj.strip()
                if not detail:
                    detail = json.dumps(payload, ensure_ascii=True)
        except Exception:
            body = response.text or ""
            detail = body.strip()
        base = f"ComfyUI request failed: {exc}"
        return f"{base} | body={detail}" if detail else base

    @staticmethod
    def _quote_path_segment(value: str) -> str:
        if not value:
            raise ComfyClientError("Path identifier is required", category="validation")
        return quote(str(value), safe="")

    def _build_ws_url(self, client_id: Optional[str] = None) -> str:
        parsed = urlparse(self._base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        path = parsed.path.rstrip("/")
        ws_path = f"{path}/ws" if path else "/ws"
        query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
        query_dict = {key: value for key, value in query_pairs}
        query_dict["clientId"] = client_id or uuid.uuid4().hex
        query = urlencode(query_dict)
        return urlunparse((scheme, parsed.netloc, ws_path, "", query, ""))

    async def _history_when_available(
        self,
        *,
        prompt_id: str,
        timeout_s: float,
        poll_ms: int = 250,
    ) -> Dict[str, Any]:
        deadline = time.monotonic() + max(0.1, timeout_s)
        while time.monotonic() < deadline:
            history = await self.get_history(prompt_id=prompt_id)
            if history:
                return history
            await asyncio.sleep(max(0.05, poll_ms / 1000.0))
        return {}

    @staticmethod
    def _normalize_ws_event(raw_event: Any) -> Dict[str, Any] | None:
        if isinstance(raw_event, bytes):
            return None
        if not isinstance(raw_event, str):
            return None
        try:
            payload = json.loads(raw_event)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None

        event_type = str(payload.get("type") or "").strip()
        if not event_type:
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            data = {}
        prompt_id = data.get("prompt_id") or payload.get("prompt_id")
        node = data.get("node")
        value = data.get("value")
        max_value = data.get("max")
        percent: float | None = None
        if isinstance(value, (int, float)) and isinstance(max_value, (int, float)) and float(max_value) > 0:
            percent = round((float(value) / float(max_value)) * 100.0, 2)

        event: Dict[str, Any] = {
            "type": event_type,
            "prompt_id": prompt_id,
            "node": node,
            "value": value,
            "max": max_value,
            "percent": percent,
            "data": data,
        }
        return event

    @staticmethod
    def _prompt_in_queue(prompt_id: str, queue_payload: Dict[str, Any]) -> bool:
        if not prompt_id:
            return False
        for key in ("queue_running", "queue_pending"):
            entries = queue_payload.get(key)
            if not isinstance(entries, list):
                continue
            for item in entries:
                if isinstance(item, dict):
                    value = item.get("prompt_id")
                    if isinstance(value, str) and value == prompt_id:
                        return True
                    continue
                if isinstance(item, list):
                    for sub in item:
                        if isinstance(sub, str) and sub == prompt_id:
                            return True
        return False

    @staticmethod
    def _ws_connect(url: str):
        try:
            import websockets
        except Exception as exc:
            raise ComfyClientError("websockets dependency not installed; cannot use ComfyUI progress websocket") from exc
        return websockets.connect(url, max_size=2_000_000)
