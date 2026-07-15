"""Async HTTP client for ComfyUI Manager v4."""

from __future__ import annotations

import time
import uuid
from typing import Any, Optional

import httpx

from comfy_mcp.comfy_manager.errors import (
    ManagerAPIError,
    ManagerConnectionError,
    ManagerError,
    ManagerNotInstalledError,
)
from comfy_mcp.comfy_manager.models import (
    ExternalModelInfo,
    ManagerVersion,
    NodeMapping,
    NodePackInfo,
)


SUPPORTED_OPERATIONS = {
    "install_node_pack",
    "update_node_pack",
    "uninstall_node_pack",
    "disable_node_pack",
    "install_model",
    "update_comfyui",
    "update_all",
}


class _TTLCache:
    def __init__(self, ttl_s: float) -> None:
        self._ttl_s = ttl_s
        self._values: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._values.get(key)
        if entry is None:
            return None
        created_at, value = entry
        if time.monotonic() - created_at >= self._ttl_s:
            self._values.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._values[key] = (time.monotonic(), value)

    def clear(self) -> None:
        self._values.clear()


class ComfyManagerClient:
    """Remote-only ComfyUI Manager v4 client."""

    def __init__(
        self,
        base_url: str,
        timeout_s: float = 10.0,
        http_client: Optional[httpx.AsyncClient] = None,
        cache_ttl_s: float = 300.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._client = http_client or httpx.AsyncClient(base_url=base_url, timeout=timeout_s)
        self._cache = _TTLCache(cache_ttl_s)

    async def check_installed(self, *, refresh: bool = False) -> ManagerVersion:
        """Detect Manager v4 using ComfyUI's feature flags."""
        if not refresh:
            cached = self._cache.get("version")
            if isinstance(cached, ManagerVersion):
                return cached

        try:
            features = await self._request("GET", "/features")
        except ManagerAPIError as exc:
            if exc.status_code in {404, 405, 501}:
                version = ManagerVersion(version="", installed=False, supports_v4=False)
                self._cache.set("version", version)
                return version
            raise

        if not isinstance(features, dict):
            raise ManagerAPIError(
                "GET /features returned an invalid JSON shape",
                endpoint="/features",
                category="remote",
            )
        manager_features = ((features.get("extension") or {}).get("manager") or {})
        supports_v4 = bool(manager_features.get("supports_v4"))
        version = ManagerVersion(
            version="v4" if supports_v4 else "unknown",
            installed=bool(manager_features),
            supports_v4=supports_v4,
        )
        self._cache.set("version", version)
        return version

    async def status(self, *, refresh: bool = False) -> dict[str, Any]:
        """Return Manager availability and queue status."""
        version = await self.check_installed(refresh=refresh)
        if not version.supports_v4:
            return {
                "installed": version.installed,
                "supports_v4": False,
                "version": version.version,
                "queue": None,
            }
        try:
            queue = await self.queue_status(refresh=refresh)
        except ManagerError as exc:
            queue = {"status": "unavailable", "error": str(exc)}
        return {
            "installed": version.installed,
            "supports_v4": True,
            "version": version.version,
            "queue": queue,
        }

    async def list_installed_packs(self, mode: str = "default", *, refresh: bool = False) -> dict[str, Any]:
        await self._ensure_installed()
        cache_key = f"packs:{mode}"
        if not refresh:
            cached = self._cache.get(cache_key)
            if isinstance(cached, dict):
                return cached
        result = await self._request("GET", "/v2/customnode/installed", params={"mode": mode})
        self._cache.set(cache_key, result)
        return result

    async def queue_status(self, client_id: str | None = None, *, refresh: bool = False) -> dict[str, Any]:
        await self._ensure_installed()
        cache_key = f"queue:{client_id or ''}"
        if not refresh:
            cached = self._cache.get(cache_key)
            if isinstance(cached, dict):
                return cached
        params = {"client_id": client_id} if client_id else None
        result = await self._request("GET", "/v2/manager/queue/status", params=params)
        self._cache.set(cache_key, result)
        return result

    async def list_snapshots(self, *, refresh: bool = False) -> dict[str, Any]:
        await self._ensure_installed()
        if not refresh:
            cached = self._cache.get("snapshots")
            if isinstance(cached, dict):
                return cached
        result = await self._request("GET", "/v2/snapshot/getlist")
        self._cache.set("snapshots", result)
        return result

    async def get_node_mappings(self, mode: str = "local", *, refresh: bool = False) -> dict[str, dict[str, str]]:
        await self._ensure_installed()
        cache_key = f"mappings:{mode}"
        if not refresh:
            cached = self._cache.get(cache_key)
            if isinstance(cached, dict):
                return cached
        data = await self._request("GET", "/v2/customnode/getmappings", params={"mode": mode})
        mappings: dict[str, dict[str, str]] = {}
        for pack_id, pack_data in (data or {}).items():
            if not isinstance(pack_data, list) or not pack_data:
                continue
            node_list = pack_data[0]
            metadata = pack_data[1] if len(pack_data) > 1 and isinstance(pack_data[1], dict) else {}
            pack_name = metadata.get("title_aux") or metadata.get("title") or pack_id
            if isinstance(node_list, list):
                for node_type in node_list:
                    mapping = NodeMapping(str(node_type), str(pack_id), str(pack_name))
                    mappings[mapping.node_type] = {
                        "node_type": mapping.node_type,
                        "node_pack_id": mapping.node_pack_id,
                        "node_pack_name": mapping.node_pack_name,
                    }
        self._cache.set(cache_key, mappings)
        return mappings

    async def search_node_packs(
        self,
        query: str | None = None,
        category: str | None = None,
        node_filter: str | None = None,
        installed_only: bool = False,
        max_results: int = 20,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        installed = await self.list_installed_packs(refresh=refresh)
        mappings = await self.get_node_mappings(refresh=refresh) if node_filter else {}
        matched_by_pack: dict[str, list[str]] = {}
        if node_filter:
            needle = node_filter.lower()
            for node_type, mapping in mappings.items():
                if needle in node_type.lower():
                    matched_by_pack.setdefault(mapping["node_pack_id"], []).append(node_type)

        results: list[dict[str, Any]] = []
        entries = installed.items() if isinstance(installed, dict) else []
        for name, info in entries:
            info = info if isinstance(info, dict) else {}
            aux_id = str(info.get("aux_id") or "")
            pack_id = str(info.get("cnr_id") or aux_id or name)
            pack = NodePackInfo(
                id=pack_id,
                name=str(name),
                installed="True" if info.get("enabled", True) else "Disabled",
                author=aux_id.split("/", 1)[0] if "/" in aux_id else "",
                repository=f"https://github.com/{aux_id}" if "/" in aux_id else "",
                matched_nodes=matched_by_pack.get(pack_id, []),
            )
            if installed_only and pack.installed != "True":
                continue
            if category and category.lower() != pack.category.lower():
                continue
            haystack = " ".join((pack.id, pack.name, pack.author, pack.repository)).lower()
            if query and query.lower() not in haystack:
                continue
            if node_filter and not pack.matched_nodes:
                continue
            results.append(
                {
                    "id": pack.id,
                    "name": pack.name,
                    "installed": pack.installed,
                    "category": pack.category,
                    "author": pack.author,
                    "repository": pack.repository,
                    "matched_nodes": pack.matched_nodes,
                }
            )
            if len(results) >= max(1, min(int(max_results), 100)):
                break
        return results

    async def search_external_models(
        self,
        query: str | None = None,
        base_filter: str | None = None,
        type_filter: str | None = None,
        installed_only: bool = False,
        uninstalled_only: bool = False,
        max_results: int = 20,
        mode: str = "cache",
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        await self._ensure_installed()
        cache_key = f"external_models:{mode}"
        if not refresh:
            cached = self._cache.get(cache_key)
            if isinstance(cached, dict):
                data = cached
            else:
                data = await self._request("GET", "/v2/externalmodel/getlist", params={"mode": mode})
                self._cache.set(cache_key, data)
        else:
            data = await self._request("GET", "/v2/externalmodel/getlist", params={"mode": mode})
            self._cache.set(cache_key, data)
        raw_models = data.get("models", []) if isinstance(data, dict) else []
        results: list[dict[str, Any]] = []
        for raw in raw_models:
            if not isinstance(raw, dict):
                continue
            model = ExternalModelInfo(
                name=str(raw.get("name") or ""),
                filename=str(raw.get("filename") or ""),
                model_type=str(raw.get("type") or ""),
                base=str(raw.get("base") or ""),
                description=str(raw.get("description") or ""),
                url=str(raw.get("url") or ""),
                installed=raw.get("installed") is True or str(raw.get("installed") or "").lower() == "true",
            )
            if installed_only and not model.installed:
                continue
            if uninstalled_only and model.installed:
                continue
            if query and query.lower() not in f"{model.name} {model.filename} {model.description}".lower():
                continue
            if base_filter and base_filter.lower() not in model.base.lower():
                continue
            if type_filter and type_filter.lower() not in model.model_type.lower():
                continue
            results.append({**raw, "installed": model.installed})
            if len(results) >= max(1, min(int(max_results), 100)):
                break
        return results

    async def queue_action(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        client_id: str = "comfy-mcp",
        ui_id: str | None = None,
        start_queue: bool = True,
    ) -> dict[str, Any]:
        """Queue one constrained Manager v4 operation."""
        if operation not in SUPPORTED_OPERATIONS:
            raise ManagerAPIError(f"Unsupported Manager operation: {operation}", category="validation")
        await self._ensure_installed()
        ui_id = ui_id or f"comfy_mcp_{operation}_{uuid.uuid4().hex[:10]}"
        kind_by_operation = {
            "install_node_pack": "install",
            "update_node_pack": "update",
            "uninstall_node_pack": "uninstall",
            "disable_node_pack": "disable",
        }
        try:
            if operation in kind_by_operation:
                body = {
                    "ui_id": ui_id,
                    "client_id": client_id,
                    "kind": kind_by_operation[operation],
                    "params": dict(payload),
                }
                result = await self._request("POST", "/v2/manager/queue/task", json_payload=body)
            elif operation == "install_model":
                body = dict(payload)
                body.update({"client_id": client_id, "ui_id": ui_id})
                result = await self._request("POST", "/v2/manager/queue/install_model", json_payload=body)
            elif operation == "update_comfyui":
                params = {"client_id": client_id, "ui_id": ui_id}
                if "stable" in payload:
                    params["stable"] = payload["stable"]
                result = await self._request("POST", "/v2/manager/queue/update_comfyui", params=params, json_payload={})
            else:
                params = {"client_id": client_id, "ui_id": ui_id}
                if payload.get("mode"):
                    params["mode"] = payload["mode"]
                result = await self._request("POST", "/v2/manager/queue/update_all", params=params, json_payload={})

            queue_start = await self.queue_start() if start_queue else None
            return {
                "success": True,
                "queued": True,
                "operation": operation,
                "client_id": client_id,
                "ui_id": ui_id,
                "result": result,
                "queue_start": queue_start,
            }
        finally:
            self._cache.clear()

    async def queue_start(self) -> dict[str, Any]:
        await self._ensure_installed()
        return await self._request("POST", "/v2/manager/queue/start", json_payload={})

    async def close(self) -> None:
        await self._client.aclose()

    async def _ensure_installed(self) -> None:
        version = await self.check_installed()
        if not version.installed or not version.supports_v4:
            raise ManagerNotInstalledError(
                "ComfyUI Manager v4 is not available on this ComfyUI server",
                category="unsupported",
            )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Any = None,
        json_payload: Any = None,
    ) -> Any:
        try:
            response = await self._client.request(
                method,
                path,
                params=params,
                json=json_payload,
            )
        except httpx.TimeoutException as exc:
            raise ManagerConnectionError(
                f"Timeout accessing Manager API: {path}",
                endpoint=path,
                category="unreachable",
            ) from exc
        except httpx.RequestError as exc:
            raise ManagerConnectionError(
                f"Failed to connect to Manager API: {exc}",
                endpoint=path,
                category="unreachable",
            ) from exc

        if not 200 <= response.status_code < 300:
            try:
                detail: Any = response.json()
            except ValueError:
                detail = response.text
            raise ManagerAPIError(
                f"{method} {path} failed with {response.status_code}: {detail}",
                status_code=response.status_code,
                endpoint=path,
                category="unsupported" if response.status_code in {404, 405, 501} else "remote",
            )
        if not response.content:
            return {"success": True, "status": response.status_code}
        try:
            return response.json()
        except ValueError:
            return {"success": True, "status": response.status_code, "data": response.text}
