"""MCP facade for remote ComfyUI Manager v4 operations."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from comfy_mcp.comfy_manager.client import SUPPORTED_OPERATIONS, ComfyManagerClient
from comfy_mcp.comfy_manager.errors import (
    ManagerAPIError,
    ManagerConnectionError,
    ManagerError,
    ManagerNotInstalledError,
)
from comfy_mcp.mcp_server.policy import Policy


class ManagerTools:
    """Policy-aware Manager tool handlers."""

    def __init__(self, client: ComfyManagerClient, policy: Policy) -> None:
        self._client = client
        self._policy = policy

    async def status(self, refresh: bool = False) -> Dict[str, Any]:
        result = await self._read(lambda: self._client.status(refresh=refresh))
        if result.get("status") == "available" and isinstance(result.get("result"), dict):
            if not result["result"].get("supports_v4", False):
                return {
                    "status": "unavailable",
                    "availability": "unsupported",
                    "result": result["result"],
                }
        return result

    async def packs_list(self, mode: str = "default", refresh: bool = False) -> Dict[str, Any]:
        return await self._read(lambda: self._client.list_installed_packs(mode=mode, refresh=refresh))

    async def packs_search(
        self,
        query: str | None = None,
        category: str | None = None,
        node_filter: str | None = None,
        installed_only: bool = False,
        max_results: int = 20,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        return await self._read(
            lambda: self._client.search_node_packs(
                query=query,
                category=category,
                node_filter=node_filter,
                installed_only=installed_only,
                max_results=max_results,
                refresh=refresh,
            )
        )

    async def node_mappings_get(self, mode: str = "local", refresh: bool = False) -> Dict[str, Any]:
        return await self._read(lambda: self._client.get_node_mappings(mode=mode, refresh=refresh))

    async def snapshots_list(self, refresh: bool = False) -> Dict[str, Any]:
        return await self._read(lambda: self._client.list_snapshots(refresh=refresh))

    async def models_search(
        self,
        query: str | None = None,
        base_filter: str | None = None,
        type_filter: str | None = None,
        installed_only: bool = False,
        uninstalled_only: bool = False,
        max_results: int = 20,
        mode: str = "cache",
        refresh: bool = False,
    ) -> Dict[str, Any]:
        return await self._read(
            lambda: self._client.search_external_models(
                query=query,
                base_filter=base_filter,
                type_filter=type_filter,
                installed_only=installed_only,
                uninstalled_only=uninstalled_only,
                max_results=max_results,
                mode=mode,
                refresh=refresh,
            )
        )

    async def queue_status(self, client_id: str | None = None, refresh: bool = False) -> Dict[str, Any]:
        return await self._read(lambda: self._client.queue_status(client_id=client_id, refresh=refresh))

    async def operation(
        self,
        operation: str,
        payload: dict[str, Any] | None = None,
        client_id: str = "comfy-mcp",
        ui_id: str | None = None,
        start_queue: bool = True,
        confirm: bool = False,
        token: str | None = None,
    ) -> Dict[str, Any]:
        """Execute a confirmation-gated Manager operation from the constrained enum."""
        if operation not in SUPPORTED_OPERATIONS:
            return {
                "status": "rejected",
                "operation": operation,
                "reason": "validation",
                "error": f"Unsupported Manager operation: {operation}",
            }

        try:
            self._policy.enforce_remote_mutation(
                token,
                confirmed=confirm,
                operation=f"comfy_manager_operation:{operation}",
            )
        except PermissionError as exc:
            return {
                "status": "rejected",
                "operation": operation,
                "reason": "policy",
                "error": str(exc),
            }

        try:
            result = await self._client.queue_action(
                operation,
                payload or {},
                client_id=client_id,
                ui_id=ui_id,
                start_queue=start_queue,
            )
        except ManagerNotInstalledError as exc:
            return {
                "status": "unavailable",
                "operation": operation,
                "availability": "unsupported",
                "error": str(exc),
            }
        except ManagerConnectionError as exc:
            return {
                "status": "unavailable",
                "operation": operation,
                "availability": "unreachable",
                "error": str(exc),
            }
        except ManagerAPIError as exc:
            if exc.category == "validation":
                return {
                    "status": "rejected",
                    "operation": operation,
                    "reason": "validation",
                    "error": str(exc),
                }
            return {"status": "error", "operation": operation, "error": str(exc)}
        return {"status": "executed", "operation": operation, "result": result}

    async def _read(self, operation: Callable[[], Awaitable[Any]]) -> Dict[str, Any]:
        try:
            result = await operation()
        except ManagerNotInstalledError as exc:
            return {
                "status": "unavailable",
                "availability": "unsupported",
                "error": str(exc),
            }
        except ManagerConnectionError as exc:
            return {
                "status": "unavailable",
                "availability": "unreachable",
                "error": str(exc),
            }
        except ManagerError as exc:
            return {"status": "error", "error": str(exc)}
        return {"status": "available", "result": result}
