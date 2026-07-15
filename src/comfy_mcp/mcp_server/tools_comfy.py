"""ComfyUI MCP tool handlers."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib.parse import urlparse

from comfy_mcp.comfy_client.client import ComfyClient
from comfy_mcp.comfy_client.errors import ComfyClientError
from comfy_mcp.mcp_server.policy import Policy


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float


class _TTLCache:
    """Small in-process TTL cache for remote discovery payloads."""

    def __init__(self, ttl_s: float) -> None:
        self._ttl_s = ttl_s
        self._entries: dict[str, _CacheEntry] = {}

    def get(self, key: str) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if time.monotonic() >= entry.expires_at:
            self._entries.pop(key, None)
            return None
        return entry.value

    def set(self, key: str, value: Any) -> None:
        self._entries[key] = _CacheEntry(value=value, expires_at=time.monotonic() + self._ttl_s)

    def clear(self) -> None:
        self._entries.clear()


class ComfyTools:
    """Tool handlers for remote ComfyUI discovery and controlled operations."""

    def __init__(
        self,
        client: ComfyClient,
        policy: Policy | None = None,
        manager_client: Any | None = None,
    ) -> None:
        self._client = client
        self._policy = policy or Policy(api_token=None, readonly_mode=False, max_workflow_bytes=5_000_000)
        self._manager_client = manager_client
        self._discovery_cache = _TTLCache(ttl_s=60.0)

    async def nodes_list(self) -> Dict[str, Any]:
        """Return ComfyUI node catalog."""
        return await self._client.get_object_info()

    async def queue_get(self) -> Dict[str, Any]:
        """Return ComfyUI queue state."""
        return await self._client.get_queue()

    async def capabilities_get(
        self,
        refresh: bool = False,
        probe_queue: bool = True,
        resolve_dns: bool = True,
    ) -> Dict[str, Any]:
        """Return a best-effort, cacheable audit of the remote ComfyUI target."""
        if not refresh:
            cached = self._discovery_cache.get("capabilities")
            if cached is not None:
                return cached

        diagnostics = await self.server_info(probe_queue=False, resolve_dns=resolve_dns)
        capabilities: Dict[str, Any] = {
            "configured_base_url": diagnostics["configured_base_url"],
            "target": diagnostics["target"],
            "mutation_policy": {
                "readonly_mode": self._policy.readonly_mode,
                "token_required": self._policy.api_token is not None,
                "confirmation_required": True,
            },
            "features": await self._probe("features", self._client.get_features),
            "system_stats": await self._probe("system_stats", self._client.get_system_stats),
            "queue": await self._probe("queue", self._client.get_queue) if probe_queue else {"status": "skipped"},
            "models": await self._probe("models", self._client.get_model_types),
            "embeddings": await self._probe("embeddings", self._client.get_embeddings),
        }
        if self._manager_client is None:
            capabilities["manager"] = {
                "status": "unsupported",
                "error": "Manager client is not configured",
            }
        else:
            manager_probe = await self._probe(
                "manager",
                lambda: self._manager_client.status(refresh=refresh),
            )
            manager_value = manager_probe.get("value") if isinstance(manager_probe, dict) else None
            if (
                manager_probe.get("status") == "available"
                and isinstance(manager_value, dict)
                and not manager_value.get("supports_v4", False)
            ):
                manager_probe["status"] = "unsupported"
            capabilities["manager"] = manager_probe

        self._discovery_cache.set("capabilities", capabilities)
        return capabilities

    async def jobs_list(
        self,
        status: str | list[str] | None = None,
        workflow_id: str | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Dict[str, Any]:
        """Return filtered and paginated remote jobs."""
        return await self._client.list_jobs(
            status=status,
            workflow_id=workflow_id,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )

    async def job_get(self, job_id: str) -> Dict[str, Any]:
        """Return one remote job."""
        return await self._client.get_job(job_id)

    async def workflow_templates_list(self) -> Dict[str, Any]:
        """Return available remote workflow templates."""
        return await self._client.get_workflow_templates()

    async def workflow_template_get(self, pack: str, filename: str) -> Any:
        """Return one remote workflow template."""
        return await self._client.get_workflow_template(pack, filename)

    async def global_subgraphs_list(self) -> Dict[str, Any]:
        """Return global subgraph metadata."""
        return await self._client.get_global_subgraphs()

    async def global_subgraph_get(self, subgraph_id: str) -> Dict[str, Any]:
        """Return one global subgraph."""
        return await self._client.get_global_subgraph(subgraph_id)

    async def node_replacements_get(self) -> Any:
        """Return optional node replacement metadata."""
        return await self._client.get_node_replacements()

    async def assets_list(
        self,
        include_tags: list[str] | None = None,
        exclude_tags: list[str] | None = None,
        name_contains: str | None = None,
        metadata_filter: dict[str, Any] | None = None,
        limit: int = 20,
        offset: int = 0,
        sort: str = "created_at",
        order: str = "desc",
    ) -> Dict[str, Any]:
        """List remote assets."""
        return await self._client.list_assets(
            include_tags=include_tags,
            exclude_tags=exclude_tags,
            name_contains=name_contains,
            metadata_filter=metadata_filter,
            limit=limit,
            offset=offset,
            sort=sort,
            order=order,
        )

    async def asset_get(self, asset_id: str) -> Dict[str, Any]:
        """Return one remote asset."""
        return await self._client.get_asset(asset_id)

    async def tags_list(
        self,
        prefix: str | None = None,
        limit: int = 100,
        offset: int = 0,
        order: str = "count_desc",
        include_zero: bool = True,
    ) -> Dict[str, Any]:
        """Return remote asset tags."""
        return await self._client.get_tags(
            prefix=prefix,
            limit=limit,
            offset=offset,
            order=order,
            include_zero=include_zero,
        )

    async def nodes_search(
        self,
        query: str | None = None,
        class_type: str | None = None,
        category: str | None = None,
        input_name: str | None = None,
        output_type: str | None = None,
        limit: int = 50,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        """Search the remote object-info catalog without returning the full catalog."""
        catalog = None if refresh else self._discovery_cache.get("object_info")
        if catalog is None:
            catalog = await self._client.get_object_info()
            self._discovery_cache.set("object_info", catalog)

        query_lower = query.strip().lower() if query else None
        class_lower = class_type.strip().lower() if class_type else None
        category_lower = category.strip().lower() if category else None
        input_lower = input_name.strip().lower() if input_name else None
        output_lower = output_type.strip().lower() if output_type else None
        results: list[dict[str, Any]] = []
        for node_name, raw_info in (catalog.items() if isinstance(catalog, dict) else []):
            if not isinstance(raw_info, dict):
                continue
            info = dict(raw_info)
            node_category = str(info.get("category") or "")
            display_name = str(info.get("display_name") or "")
            description = str(info.get("description") or "")
            aliases = info.get("search_aliases") or []
            searchable = " ".join(
                [str(node_name), display_name, description, node_category]
                + [str(alias) for alias in aliases if isinstance(alias, str)]
            ).lower()
            if query_lower and query_lower not in searchable:
                continue
            if class_lower and class_lower not in str(node_name).lower():
                continue
            if category_lower and category_lower not in node_category.lower():
                continue
            inputs = info.get("input") if isinstance(info.get("input"), dict) else {}
            input_names = {
                str(name).lower()
                for group in ("required", "optional", "hidden")
                for name in (inputs.get(group) or {})
            }
            if input_lower and not any(input_lower in name for name in input_names):
                continue
            outputs = info.get("output") or []
            if output_lower and not any(output_lower in str(value).lower() for value in outputs):
                continue
            results.append({"class_type": node_name, **info})
            if len(results) >= max(1, min(int(limit), 200)):
                break
        return {
            "query": query,
            "filters": {
                "class_type": class_type,
                "category": category,
                "input_name": input_name,
                "output_type": output_type,
            },
            "count": len(results),
            "nodes": results,
        }

    async def queue_interrupt(
        self,
        prompt_id: str | None = None,
        confirm: bool = False,
        token: str | None = None,
    ) -> Dict[str, Any]:
        """Interrupt remote execution behind the mutation policy gate."""
        return await self._execute_mutation(
            "comfy_queue_interrupt",
            token=token,
            confirm=confirm,
            operation=lambda: self._client.interrupt(prompt_id),
        )

    async def queue_delete(
        self,
        clear_all: bool = False,
        prompt_ids: list[str] | None = None,
        confirm: bool = False,
        token: str | None = None,
    ) -> Dict[str, Any]:
        """Delete selected or all pending remote queue items."""
        return await self._execute_mutation(
            "comfy_queue_delete",
            token=token,
            confirm=confirm,
            operation=lambda: self._client.delete_queue(clear_all=clear_all, prompt_ids=prompt_ids),
        )

    async def memory_free(
        self,
        unload_models: bool = False,
        free_memory: bool = False,
        confirm: bool = False,
        token: str | None = None,
    ) -> Dict[str, Any]:
        """Request remote model unloading and memory release."""
        return await self._execute_mutation(
            "comfy_memory_free",
            token=token,
            confirm=confirm,
            operation=lambda: self._client.free_memory(
                unload_models=unload_models,
                free_memory=free_memory,
            ),
        )

    async def history_delete(
        self,
        clear_all: bool = False,
        prompt_ids: list[str] | None = None,
        confirm: bool = False,
        token: str | None = None,
    ) -> Dict[str, Any]:
        """Delete selected or all remote history entries."""
        return await self._execute_mutation(
            "comfy_history_delete",
            token=token,
            confirm=confirm,
            operation=lambda: self._client.delete_history(clear_all=clear_all, prompt_ids=prompt_ids),
        )

    async def settings_get(self, setting_id: str | None = None) -> Any:
        """Read all remote settings or one setting."""
        return await self._client.get_settings(setting_id)

    async def settings_set(
        self,
        settings: dict[str, Any] | None = None,
        setting_id: str | None = None,
        value: Any = None,
        confirm: bool = False,
        token: str | None = None,
    ) -> Dict[str, Any]:
        """Write remote settings behind the mutation policy gate."""
        if setting_id is not None:
            operation: Callable[[], Awaitable[Any]] = lambda: self._client.set_settings(
                setting_id=setting_id,
                value=value,
            )
        else:
            operation = lambda: self._client.set_settings(settings=settings)
        return await self._execute_mutation(
            "comfy_settings_set",
            token=token,
            confirm=confirm,
            operation=operation,
        )

    async def upload_image(
        self,
        file_path: str,
        image_type: str = "input",
        subfolder: str | None = None,
        overwrite: bool = False,
        confirm: bool = False,
        token: str | None = None,
    ) -> Dict[str, Any]:
        """Upload an image behind the remote mutation policy gate."""
        return await self._execute_mutation(
            "comfy_upload_image",
            token=token,
            confirm=confirm,
            operation=lambda: self._client.upload_image(
                file_path,
                image_type=image_type,
                subfolder=subfolder,
                overwrite=overwrite,
            ),
        )

    async def upload_mask(
        self,
        file_path: str,
        original_ref: dict[str, Any],
        image_type: str = "input",
        subfolder: str | None = None,
        overwrite: bool = False,
        confirm: bool = False,
        token: str | None = None,
    ) -> Dict[str, Any]:
        """Upload a mask behind the remote mutation policy gate."""
        return await self._execute_mutation(
            "comfy_upload_mask",
            token=token,
            confirm=confirm,
            operation=lambda: self._client.upload_mask(
                file_path,
                original_ref,
                image_type=image_type,
                subfolder=subfolder,
                overwrite=overwrite,
            ),
        )

    async def upload_asset(
        self,
        file_path: str,
        tags: list[str],
        name: str | None = None,
        user_metadata: dict[str, Any] | None = None,
        content_hash: str | None = None,
        confirm: bool = False,
        token: str | None = None,
    ) -> Dict[str, Any]:
        """Upload an asset behind the remote mutation policy gate."""
        return await self._execute_mutation(
            "comfy_upload_asset",
            token=token,
            confirm=confirm,
            operation=lambda: self._client.upload_asset(
                file_path,
                tags=tags,
                name=name,
                user_metadata=user_metadata,
                content_hash=content_hash,
            ),
        )

    def invalidate_cache(self) -> None:
        """Invalidate cached remote discovery payloads."""
        self._discovery_cache.clear()

    async def _probe(self, name: str, operation: Callable[[], Awaitable[Any]]) -> Dict[str, Any]:
        try:
            value = await operation()
            return {"status": "available", "value": value}
        except ComfyClientError as exc:
            status = "unsupported" if exc.category == "unsupported" or exc.status_code in {404, 405, 501} else "unreachable"
            return {"status": status, "error": str(exc), "endpoint": exc.endpoint, "name": name}
        except Exception as exc:
            if exc.__class__.__name__ == "ManagerNotInstalledError":
                status = "unsupported"
            else:
                status = "unreachable"
            return {"status": status, "error": str(exc), "name": name}

    async def _execute_mutation(
        self,
        name: str,
        *,
        token: str | None,
        confirm: bool,
        operation: Callable[[], Awaitable[Any]],
    ) -> Dict[str, Any]:
        try:
            self._policy.enforce_remote_mutation(
                token,
                confirmed=confirm,
                operation=name,
            )
        except PermissionError as exc:
            return {
                "status": "rejected",
                "operation": name,
                "reason": "policy",
                "error": str(exc),
            }

        try:
            result = await operation()
        except ComfyClientError as exc:
            if exc.category == "validation":
                return {
                    "status": "rejected",
                    "operation": name,
                    "reason": "validation",
                    "error": str(exc),
                }
            if exc.category in {"unsupported", "unreachable"} or exc.status_code in {404, 405, 501}:
                availability = "unsupported" if exc.category == "unsupported" or exc.status_code in {404, 405, 501} else "unreachable"
                return {
                    "status": "unavailable",
                    "operation": name,
                    "availability": availability,
                    "error": str(exc),
                }
            return {"status": "error", "operation": name, "error": str(exc)}
        self._discovery_cache.clear()
        return {"status": "executed", "operation": name, "result": result}

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
