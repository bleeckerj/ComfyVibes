"""Tests for workflow store operations."""

from pathlib import Path

import pytest

from comfy_mcp.workflow_store.errors import InvalidWorkflowIdError, WorkflowNotFoundError
from comfy_mcp.workflow_store.store import WORKFLOW_FILE, WorkflowStore


def test_save_and_read_workflow_round_trip(tmp_path: Path) -> None:
    """Workflow store should persist and load workflow JSON unchanged."""
    print("Test: saving and reading workflow JSON should return identical data.")
    store = WorkflowStore(tmp_path)
    workflow_id = "demo_workflow"
    payload = {"1": {"class_type": "KSampler", "inputs": {"seed": 1}}}

    store.save_workflow(workflow_id, payload)
    loaded = store.read_workflow(workflow_id)

    assert loaded == payload, "Expected loaded workflow JSON to match saved payload"


def test_list_entries_includes_saved_workflow(tmp_path: Path) -> None:
    """Workflow store should list entries with workflow.json present."""
    print("Test: list_entries should include saved workflows in store root.")
    store = WorkflowStore(tmp_path)
    store.save_workflow("first", {"1": {"class_type": "A", "inputs": {}}})

    entries = store.list_entries()

    assert len(entries) == 1, "Expected exactly one workflow entry"
    assert entries[0].workflow_id == "first", "Expected entry id to match saved workflow"
    assert (entries[0].root / WORKFLOW_FILE).exists(), "Expected workflow.json to exist on disk"


def test_delete_workflow_removes_entry(tmp_path: Path) -> None:
    """Workflow store should delete entry directory and metadata."""
    print("Test: delete_workflow should remove workflow entry directory.")
    store = WorkflowStore(tmp_path)
    store.save_workflow("to_delete", {"1": {"class_type": "A", "inputs": {}}})

    store.delete_workflow("to_delete")

    with pytest.raises(WorkflowNotFoundError):
        store.read_workflow("to_delete")


def test_invalid_workflow_id_rejected(tmp_path: Path) -> None:
    """Workflow store should reject unsafe workflow ids."""
    print("Test: invalid workflow id should raise InvalidWorkflowIdError.")
    store = WorkflowStore(tmp_path)

    with pytest.raises(InvalidWorkflowIdError):
        store.save_workflow("../escape", {"1": {"class_type": "A", "inputs": {}}})


def test_read_workflow_from_extra_root(tmp_path: Path) -> None:
    """WorkflowStore should read workflows from extra roots when missing in primary."""
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()

    secondary_store = WorkflowStore(secondary)
    secondary_store.save_workflow("from_secondary", {"1": {"class_type": "A", "inputs": {"x": 1}}})

    store = WorkflowStore(primary, extra_roots=[secondary])
    loaded = store.read_workflow("from_secondary")
    assert loaded == {"1": {"class_type": "A", "inputs": {"x": 1}}}


def test_list_entries_merges_roots_preferring_primary(tmp_path: Path) -> None:
    """list_entries should union entries across roots, preferring primary on id collisions."""
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()

    WorkflowStore(primary).save_workflow("shared", {"1": {"class_type": "P", "inputs": {}}})
    WorkflowStore(secondary).save_workflow("shared", {"1": {"class_type": "S", "inputs": {}}})
    WorkflowStore(secondary).save_workflow("only_secondary", {"1": {"class_type": "S", "inputs": {}}})

    store = WorkflowStore(primary, extra_roots=[secondary])
    entries = store.list_entries()
    ids = [e.workflow_id for e in entries]
    assert ids == ["only_secondary", "shared"]
    shared = next(e for e in entries if e.workflow_id == "shared")
    assert shared.root.parent == primary
