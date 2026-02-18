"""Reasoning service facade used by MCP tools."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from comfy_mcp.reasoning.capability_index import CapabilityIndex
from comfy_mcp.workflow_store.store import WorkflowStore


class WorkflowReasoningService:
    """Thin facade around reasoning components for workflow tools."""

    def __init__(self, store: WorkflowStore) -> None:
        self._capability_index = CapabilityIndex(store)

    def list_capabilities(
        self,
        query: str = "",
        limit: int = 20,
        tags: Optional[List[str]] = None,
        include_params: bool = False,
    ) -> Dict[str, Any]:
        capabilities = self._capability_index.list_cards(
            query=query,
            limit=limit,
            tags=tags,
            include_params=include_params,
        )
        return {
            "query": query,
            "count": len(capabilities),
            "capabilities": capabilities,
        }

    def get_capability(self, workflow_id: str, include_params: bool = True) -> Dict[str, Any]:
        return {
            "capability": self._capability_index.get_card(
                workflow_id,
                include_params=include_params,
            )
        }

