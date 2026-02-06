"""Async ComfyUI HTTP client."""

from __future__ import annotations

from typing import Optional

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
    ) -> ComfyPromptResult:
        """Submit a prompt graph to `/prompt`."""
        payload = {"prompt": prompt}
        if client_id:
            payload["client_id"] = client_id
        return await self._post_json("/prompt", payload)

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
        except httpx.HTTPError as exc:
            raise ComfyClientError(f"ComfyUI request failed: {exc}") from exc
        return response.json()
