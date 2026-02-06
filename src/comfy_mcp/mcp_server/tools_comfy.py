"""ComfyUI MCP tool handlers."""

from __future__ import annotations

from typing import Any, Dict, Optional

from comfy_mcp.comfy_client.client import ComfyClient


class ComfyTools:
    """Tool handlers for ComfyUI read operations."""

    def __init__(self, client: ComfyClient) -> None:
        self._client = client

    async def nodes_list(self) -> Dict[str, Any]:
        """Return ComfyUI node catalog."""
        return await self._client.get_object_info()

    async def queue_get(self) -> Dict[str, Any]:
        """Return ComfyUI queue state."""
        return await self._client.get_queue()

    async def history_get(self, prompt_id: Optional[str] = None) -> Dict[str, Any]:
        """Return ComfyUI history data."""
        return await self._client.get_history(prompt_id=prompt_id)

    async def models_list(self) -> list[str]:
        """Return available model folders."""
        return await self._client.get_model_types()

    async def models_get(self, folder: str) -> list[str]:
        """Return available model files for a folder."""
        return await self._client.get_models_in_folder(folder)

    async def embeddings_list(self) -> list[str]:
        """Return available embedding names."""
        return await self._client.get_embeddings()
