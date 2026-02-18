"""Tests for workflow packaging helpers."""

from __future__ import annotations

from comfy_mcp.workflow_store.packaging import build_hints_template, package_workflow


def _workflow() -> dict:
    return {
        "1": {
            "class_type": "LoadImage",
            "_meta": {"title": "Load Image"},
            "inputs": {"image": "input.png"},
        },
        "2": {
            "class_type": "KSampler",
            "_meta": {"title": "KSampler"},
            "inputs": {
                "model": ["5", 0],
                "seed": 1,
                "steps": 8,
                "cfg": 1.2,
                "sampler_name": "euler",
                "scheduler": "normal",
            },
        },
    }


def test_package_workflow_infers_params_and_requires() -> None:
    packaged = package_workflow("demo", _workflow())
    params = packaged["params"]["params"]
    names = [item["name"] for item in params]

    assert "seed" in names
    assert "steps" in names
    assert "cfg" in names
    assert "sampler_name" in names
    assert "scheduler" in names
    assert "image" in names

    meta = packaged["meta"]
    assert "requires" in meta
    assert "seed" in meta["requires"]["params"]
    assert "use_cases" in meta
    assert isinstance(meta["use_cases"], list)


def test_package_workflow_merges_hints() -> None:
    hints = {
        "name": "Qwen Fashion Edit",
        "description": "Editorial-grade fashion edits.",
        "tags": ["image-edit", "fashion", "editorial"],
        "use_cases": ["Change garment material while preserving pose."],
        "examples": [{"goal": "Convert silk dress to denim", "overrides": {"seed": 42}}],
    }
    packaged = package_workflow("demo", _workflow(), hints=hints)
    meta = packaged["meta"]

    assert meta["name"] == "Qwen Fashion Edit"
    assert meta["description"] == "Editorial-grade fashion edits."
    assert "fashion" in meta["tags"]
    assert meta["use_cases"] == ["Change garment material while preserving pose."]
    assert meta["examples"][0]["goal"] == "Convert silk dress to denim"


def test_build_hints_template_contains_capability_fields() -> None:
    template = build_hints_template("demo", _workflow())

    assert "name" in template
    assert "description" in template
    assert "tags" in template
    assert "use_cases" in template
    assert "io_contract" in template
    assert "examples" in template
