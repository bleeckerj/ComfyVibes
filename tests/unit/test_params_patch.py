"""Tests for workflow patching using ParamSpec."""

import pytest

from comfy_mcp.params.errors import ParamPatchError
from comfy_mcp.params.patch import patch_workflow
from comfy_mcp.params.schema import ParamSpec, ParamSpecItem, ParamTarget
from comfy_mcp.workflow_store.hashing import sha256_json


def _workflow() -> dict:
    return {
        "1": {"class_type": "KSampler", "inputs": {"seed": 1, "cfg": 2.0}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "old"}},
    }


def _spec(workflow_hash: str) -> ParamSpec:
    return ParamSpec(
        schema_version="1",
        workflow_id="demo",
        workflow_hash=workflow_hash,
        params=[
            ParamSpecItem(
                name="seed",
                type="int",
                required=True,
                target=ParamTarget(mode="direct", node_id="1", input="seed"),
            ),
            ParamSpecItem(
                name="prompt",
                type="string",
                required=False,
                target=ParamTarget(mode="direct", node_id="2", input="text"),
            ),
            ParamSpecItem(
                name="nested",
                type="string",
                required=False,
                target=ParamTarget(
                    mode="direct",
                    node_id="1",
                    input="extra",
                    path=["nested", "value"],
                ),
            ),
        ],
    )


def test_patch_workflow_updates_inputs() -> None:
    """Patching should update direct inputs in the workflow graph."""
    print("Test: patch_workflow should update direct input values.")
    workflow = _workflow()
    spec = _spec(sha256_json(workflow))

    patched = patch_workflow(workflow, spec, {"seed": 42, "prompt": "new"})

    assert patched["1"]["inputs"]["seed"] == 42
    assert patched["2"]["inputs"]["text"] == "new"


def test_patch_workflow_nested_path() -> None:
    """Patching should set nested paths when provided."""
    print("Test: patch_workflow should set nested values for inputs.")
    workflow = _workflow()
    spec = _spec(sha256_json(workflow))

    patched = patch_workflow(workflow, spec, {"seed": 1, "nested": "value"})

    assert patched["1"]["inputs"]["extra"]["nested"]["value"] == "value"


def test_patch_workflow_hash_mismatch() -> None:
    """Patching should refuse when workflow hash mismatches."""
    print("Test: patch_workflow should reject hash mismatches by default.")
    workflow = _workflow()
    spec = _spec("bad-hash")

    with pytest.raises(ParamPatchError):
        patch_workflow(workflow, spec, {"seed": 1})
