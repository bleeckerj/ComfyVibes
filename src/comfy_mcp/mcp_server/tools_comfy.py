"""ComfyUI MCP tool handlers."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

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

    async def server_info(
        self,
        probe_queue: bool = True,
        resolve_dns: bool = True,
    ) -> Dict[str, Any]:
        """Return Comfy target diagnostics including resolved IPs and probe status."""
        base_url = str(getattr(self._client, "_base_url", "") or "").rstrip("/")
        parsed = urlparse(base_url) if base_url else None
        scheme = parsed.scheme if parsed else ""
        host = parsed.hostname if parsed else None
        port = parsed.port if parsed else None
        if port is None and scheme:
            port = 443 if scheme == "https" else 80

        host_is_ip = False
        if isinstance(host, str) and host:
            try:
                ipaddress.ip_address(host)
                host_is_ip = True
            except ValueError:
                host_is_ip = False

        resolved_ips: list[str] = []
        dns_error: str | None = None
        if resolve_dns and isinstance(host, str) and host:
            try:
                addrinfos = await asyncio.to_thread(
                    socket.getaddrinfo,
                    host,
                    port,
                    socket.AF_UNSPEC,
                    socket.SOCK_STREAM,
                )
                for info in addrinfos:
                    sockaddr = info[4]
                    ip = sockaddr[0] if isinstance(sockaddr, tuple) and sockaddr else None
                    if isinstance(ip, str) and ip and ip not in resolved_ips:
                        resolved_ips.append(ip)
            except Exception as exc:
                dns_error = str(exc)

        probe: Dict[str, Any] | None = None
        if probe_queue:
            endpoint = f"{base_url}/queue" if base_url else "/queue"
            started = time.monotonic()
            try:
                queue = await self._client.get_queue()
                elapsed_ms = round((time.monotonic() - started) * 1000, 1)
                running = queue.get("queue_running") if isinstance(queue, dict) else None
                pending = queue.get("queue_pending") if isinstance(queue, dict) else None
                probe = {
                    "ok": True,
                    "endpoint": endpoint,
                    "latency_ms": elapsed_ms,
                    "queue_running_count": len(running) if isinstance(running, list) else None,
                    "queue_pending_count": len(pending) if isinstance(pending, list) else None,
                }
            except Exception as exc:
                elapsed_ms = round((time.monotonic() - started) * 1000, 1)
                probe = {
                    "ok": False,
                    "endpoint": endpoint,
                    "latency_ms": elapsed_ms,
                    "error": str(exc),
                }

        return {
            "configured_base_url": base_url,
            "target": {
                "scheme": scheme,
                "host": host,
                "port": port,
                "host_is_ip": host_is_ip,
                "resolved_ips": resolved_ips,
                "dns_error": dns_error,
            },
            "probe": probe,
        }

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
