"""Async ComfyUI HTTP client."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from comfy_mcp.comfy_client.contracts import (
    ComfyEmbeddings,
    ComfyHistory,
    ComfyModelFiles,
    ComfyModelTypes,
    ComfyObjectInfo,
    ComfyPromptResult,
    ComfyQueue,
)
from comfy_mcp.comfy_client.errors import ComfyClientError


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
        path = f"/history/{prompt_id}" if prompt_id else "/history"
        return await self._get_json(path)

    async def get_model_types(self) -> ComfyModelTypes:
        """Fetch available model type folders from `/models`."""
        return await self._get_json("/models")

    async def get_models_in_folder(self, folder: str) -> ComfyModelFiles:
        """Fetch model filenames for a given folder from `/models/{folder}`."""
        return await self._get_json(f"/models/{folder}")

    async def get_embeddings(self) -> ComfyEmbeddings:
        """Fetch available embeddings from `/embeddings`."""
        return await self._get_json("/embeddings")

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
            with open(file_path, "rb") as handle:
                files = {"image": (file_path.split("/")[-1], handle, "application/octet-stream")}
                response = await self._client.post("/upload/image", data=data, files=files)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ComfyClientError(f"ComfyUI request failed: {exc}") from exc
        except OSError as exc:
            raise ComfyClientError(f"Image upload failed: {exc}") from exc

        return response.json()

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
            response = await self._client.get("/view", params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ComfyClientError(f"ComfyUI download failed: {exc}") from exc
        content_type = response.headers.get("content-type", "application/octet-stream")
        return response.content, content_type

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> "ComfyClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def _get_json(self, path: str) -> dict:
        try:
            response = await self._client.get(path)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ComfyClientError(f"ComfyUI request failed: {exc}") from exc
        return response.json()

    async def _post_json(self, path: str, payload: dict) -> dict:
        try:
            response = await self._client.post(path, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ComfyClientError(self._format_http_error(exc)) from exc
        except httpx.HTTPError as exc:
            raise ComfyClientError(f"ComfyUI request failed: {exc}") from exc
        return response.json()

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
