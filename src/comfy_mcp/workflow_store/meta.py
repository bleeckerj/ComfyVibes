"""Metadata helpers for workflow library entries."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from comfy_mcp.workflow_store.hashing import sha256_json


@dataclass(frozen=True)
class WorkflowMeta:
    """Metadata record for a workflow entry."""

    id: str
    name: str
    description: str
    tags: List[str]
    created_at: str
    updated_at: str
    workflow_hash: str
    requires: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return a dict representation suitable for JSON serialization."""
        return asdict(self)


def now_iso() -> str:
    """Return current UTC time as ISO8601 string."""
    return datetime.now(timezone.utc).isoformat()


def build_meta(
    workflow_id: str,
    name: str,
    description: str,
    tags: Optional[List[str]],
    workflow_json: Dict[str, Any],
    requires: Optional[Dict[str, Any]] = None,
    created_at: Optional[str] = None,
) -> WorkflowMeta:
    """Construct a WorkflowMeta record from inputs and workflow JSON."""
    workflow_hash = sha256_json(workflow_json)
    timestamp = created_at or now_iso()
    return WorkflowMeta(
        id=workflow_id,
        name=name,
        description=description,
        tags=tags or [],
        created_at=timestamp,
        updated_at=now_iso(),
        workflow_hash=workflow_hash,
        requires=requires,
    )
