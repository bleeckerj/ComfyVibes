"""Filesystem-backed workflow store."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from comfy_mcp.workflow_store.errors import (
    InvalidWorkflowIdError,
    WorkflowNotFoundError,
)


WORKFLOW_FILE = "workflow.json"
META_FILE = "meta.json"
PARAMS_FILE = "params.json"


@dataclass(frozen=True)
class WorkflowEntry:
    """Summary of a workflow entry on disk."""

    workflow_id: str
    root: Path
    has_meta: bool
    has_params: bool


class WorkflowStore:
    """Store workflows in a filesystem sandbox."""

    _id_pattern = re.compile(r"^[A-Za-z0-9_-]+$")

    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        """Return the workflow library root path."""
        return self._root

    def list_entries(self) -> List[WorkflowEntry]:
        """List workflow entries present in the store."""
        entries: List[WorkflowEntry] = []
        for item in sorted(self._root.iterdir()):
            if not item.is_dir():
                continue
            workflow_path = item / WORKFLOW_FILE
            if not workflow_path.exists():
                continue
            entries.append(
                WorkflowEntry(
                    workflow_id=item.name,
                    root=item,
                    has_meta=(item / META_FILE).exists(),
                    has_params=(item / PARAMS_FILE).exists(),
                )
            )
        return entries

    def read_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Load workflow JSON for a given id."""
        workflow_path = self._resolve_workflow_path(workflow_id)
        if not workflow_path.exists():
            raise WorkflowNotFoundError(f"Workflow not found: {workflow_id}")
        return self._read_json(workflow_path)

    def read_meta(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """Load meta.json if present for a workflow id."""
        meta_path = self._resolve_entry_path(workflow_id) / META_FILE
        if not meta_path.exists():
            return None
        return self._read_json(meta_path)

    def read_params(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """Load params.json if present for a workflow id."""
        params_path = self._resolve_entry_path(workflow_id) / PARAMS_FILE
        if not params_path.exists():
            return None
        return self._read_json(params_path)

    def save_workflow(
        self,
        workflow_id: str,
        workflow_json: Dict[str, Any],
        meta: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """Save workflow JSON and optional meta/params."""
        entry_dir = self._resolve_entry_path(workflow_id)
        entry_dir.mkdir(parents=True, exist_ok=True)

        workflow_path = entry_dir / WORKFLOW_FILE
        self._write_json(workflow_path, workflow_json)

        if meta is not None:
            self._write_json(entry_dir / META_FILE, meta)
        if params is not None:
            self._write_json(entry_dir / PARAMS_FILE, params)

        return workflow_path

    def delete_workflow(self, workflow_id: str) -> None:
        """Delete a workflow entry directory."""
        entry_dir = self._resolve_entry_path(workflow_id)
        if not entry_dir.exists():
            raise WorkflowNotFoundError(f"Workflow not found: {workflow_id}")
        for child in entry_dir.iterdir():
            if child.is_file():
                child.unlink()
        entry_dir.rmdir()

    def _resolve_entry_path(self, workflow_id: str) -> Path:
        self._validate_id(workflow_id)
        entry_dir = (self._root / workflow_id).resolve()
        if self._root not in entry_dir.parents and entry_dir != self._root:
            raise InvalidWorkflowIdError("Workflow id escapes store root")
        return entry_dir

    def _resolve_workflow_path(self, workflow_id: str) -> Path:
        return self._resolve_entry_path(workflow_id) / WORKFLOW_FILE

    def _validate_id(self, workflow_id: str) -> None:
        if not self._id_pattern.match(workflow_id):
            raise InvalidWorkflowIdError("Workflow id must be alphanumeric, dash, or underscore")

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _write_json(path: Path, payload: Dict[str, Any]) -> None:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
