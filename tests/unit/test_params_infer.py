"""Tests for inferred ParamSpec generation from workflow JSON."""

from __future__ import annotations

from comfy_mcp.params.infer import infer_params_spec, sync_meta_requires


def test_infer_params_spec_exposes_ksampler_fields() -> None:
    workflow = {
        "1": {
            "class_type": "KSampler",
            "_meta": {"title": "KSampler"},
            "inputs": {
                "model": ["2", 0],
                "seed": 123,
                "steps": 20,
                "cfg": 7.5,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
            },
        }
    }

    spec = infer_params_spec("demo", workflow)
    params = {item["name"]: item for item in spec["params"]}

    assert params["seed"]["type"] == "int"
    assert params["steps"]["type"] == "int"
    assert params["cfg"]["type"] == "float"
    assert params["sampler_name"]["type"] == "string"
    assert params["scheduler"]["type"] == "string"
    assert params["denoise"]["type"] == "float"


def test_sync_meta_requires_updates_param_list() -> None:
    workflow = {
        "1": {
            "class_type": "Foo",
            "_meta": {"title": "Foo"},
            "inputs": {"enabled": True, "strength": 0.5},
        }
    }
    spec = infer_params_spec("demo", workflow)
    meta = {
        "id": "demo",
        "name": "Demo",
        "description": "",
        "tags": [],
        "requires": {"inputs": ["image_filename"], "params": ["old"]},
    }

    merged = sync_meta_requires(meta, spec)

    assert merged["requires"]["inputs"] == ["image_filename"]
    assert "enabled" in merged["requires"]["params"]
    assert "strength" in merged["requires"]["params"]
