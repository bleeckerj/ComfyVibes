"""ComfyUI MCP tool handlers."""

from __future__ import annotations

import re
from pathlib import Path
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

    async def upload_image(
        self,
        file_path: str,
        image_type: str = "input",
        subfolder: Optional[str] = None,
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Upload an image file into ComfyUI storage."""
        return await self._client.upload_image(
            file_path,
            image_type=image_type,
            subfolder=subfolder,
            overwrite=overwrite,
        )

    async def download_image(
        self,
        filename: str,
        image_type: str = "output",
        subfolder: str = "",
        save_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Download an image from ComfyUI and save it locally.

        If save_path is a directory, the original filename is used inside it.
        If save_path is omitted, saves to /tmp/<filename>.
        Returns the local file path and metadata.
        """
        raw_bytes, content_type = await self._client.download_image(
            filename, image_type=image_type, subfolder=subfolder
        )
        # Determine destination
        if save_path:
            dest = Path(save_path)
            if dest.is_dir():
                dest = dest / filename
        else:
            dest = Path("/tmp") / filename

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw_bytes)

        # Build the ComfyUI view URL for reference
        base = self._client._base_url.rstrip("/")
        params = f"filename={filename}&type={image_type}"
        if subfolder:
            params += f"&subfolder={subfolder}"
        view_url = f"{base}/view?{params}"

        return {
            "local_path": str(dest),
            "filename": filename,
            "size_bytes": len(raw_bytes),
            "content_type": content_type,
            "view_url": view_url,
        }
