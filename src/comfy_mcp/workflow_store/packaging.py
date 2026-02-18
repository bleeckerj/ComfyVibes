"""Helpers for packaging workflows into MCP-ready meta + params artifacts."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from comfy_mcp.params.infer import infer_params_spec, sync_meta_requires


_HEURISTIC_FIELDS = (
    "use_cases",
    "strengths",
    "tradeoffs",
    "style",
    "mood",
    "io_contract",
    "examples",
    "notes",
)


def package_workflow(
    workflow_id: str,
    workflow_json: Dict[str, Any],
    existing_meta: Optional[Dict[str, Any]] = None,
    hints: Optional[Dict[str, Any]] = None,
    include_suggestions: bool = True,
) -> Dict[str, Dict[str, Any]]:
    """Build packaged `meta` and `params` payloads for a workflow."""
    params = infer_params_spec(workflow_id, workflow_json)
    meta = _merge_meta(_default_meta(workflow_id), existing_meta or {})
    meta = _merge_meta(meta, hints or {})

    if include_suggestions:
        _apply_suggestions(meta, workflow_json, params)

    meta = sync_meta_requires(meta, params)
    return {"meta": meta, "params": params}


def build_hints_template(
    workflow_id: str,
    workflow_json: Dict[str, Any],
    existing_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build an editable hints template for one workflow."""
    packaged = package_workflow(
        workflow_id,
        workflow_json,
        existing_meta=existing_meta,
        hints=None,
        include_suggestions=True,
    )
    meta = dict(packaged["meta"])
    template: Dict[str, Any] = {
        "name": meta.get("name", workflow_id),
        "description": meta.get("description", ""),
        "tags": meta.get("tags", []),
    }
    for key in _HEURISTIC_FIELDS:
        template[key] = meta.get(key, _heuristic_default_value(key))
    return template


def _default_meta(workflow_id: str) -> Dict[str, Any]:
    return {
        "id": workflow_id,
        "name": workflow_id.replace("_", " ").replace("-", " ").title(),
        "description": "",
        "tags": [],
    }


def _merge_meta(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(base)
    for key, value in updates.items():
        if key in {"id", "name", "description", "notes"}:
            if isinstance(value, str) and value.strip():
                merged[key] = value.strip()
            elif key not in merged:
                merged[key] = ""
            continue

        if key in {"tags", "use_cases", "strengths", "tradeoffs", "style", "mood"}:
            merged[key] = _string_list(value)
            continue

        if key == "examples":
            merged[key] = _examples_list(value)
            continue

        if key == "io_contract":
            current = merged.get("io_contract")
            if not isinstance(current, dict):
                current = {}
            incoming = value if isinstance(value, dict) else {}
            merged_io = dict(current)
            if "inputs" in incoming:
                merged_io["inputs"] = _string_list(incoming.get("inputs", []))
            if "outputs" in incoming:
                merged_io["outputs"] = _string_list(incoming.get("outputs", []))
            merged["io_contract"] = merged_io
            continue

        if key == "requires":
            if isinstance(value, dict):
                merged["requires"] = dict(value)
            continue

        merged[key] = value
    return merged


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def _examples_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        example: Dict[str, Any] = {}
        goal = str(item.get("goal") or "").strip()
        if goal:
            example["goal"] = goal
        overrides = item.get("overrides")
        if isinstance(overrides, dict):
            example["overrides"] = overrides
        if example:
            out.append(example)
    return out


def _apply_suggestions(meta: Dict[str, Any], workflow_json: Dict[str, Any], params: Dict[str, Any]) -> None:
    tags = _string_list(meta.get("tags", []))
    if not tags:
        tags = _infer_tags(workflow_json, params)
    meta["tags"] = tags

    description = str(meta.get("description") or "").strip()
    if not description:
        meta["description"] = _infer_description(tags)

    use_cases = _string_list(meta.get("use_cases", []))
    if not use_cases:
        meta["use_cases"] = _infer_use_cases(tags)

    for key in ("strengths", "tradeoffs", "style", "mood"):
        if key not in meta:
            meta[key] = []
        elif not isinstance(meta[key], list):
            meta[key] = []

    if "notes" not in meta:
        meta["notes"] = ""
    elif not isinstance(meta["notes"], str):
        meta["notes"] = str(meta["notes"])

    io_contract = meta.get("io_contract")
    if not isinstance(io_contract, dict):
        io_contract = {}
    io_contract.setdefault("inputs", _infer_io_inputs(params))
    io_contract.setdefault("outputs", ["output_images[]"])
    io_contract["inputs"] = _string_list(io_contract.get("inputs", []))
    io_contract["outputs"] = _string_list(io_contract.get("outputs", []))
    meta["io_contract"] = io_contract

    if "examples" not in meta or not isinstance(meta["examples"], list):
        meta["examples"] = []


def _infer_tags(workflow_json: Dict[str, Any], params: Dict[str, Any]) -> List[str]:
    class_blob = " ".join(
        str((node or {}).get("class_type", "")).lower()
        for node in workflow_json.values()
        if isinstance(node, dict)
    )
    param_names = {str(item.get("name") or "") for item in params.get("params", []) if isinstance(item, dict)}

    tags: List[str] = []
    if "loadimage" in class_blob or "image" in param_names or "image_filename" in param_names:
        tags.append("image-edit")
        tags.append("img2img")
    if "textencode" in class_blob and "image-edit" not in tags:
        tags.append("text-to-image")
    if "ksampler" in class_blob:
        tags.append("sampler")
    if "qwen" in class_blob:
        tags.append("qwen")
    if "flux" in class_blob:
        tags.append("flux")
    if "aspect_ratio" in param_names:
        tags.append("aspect-ratio")
    if "upscale" in class_blob or "upscale_method" in param_names:
        tags.append("upscaling")
    if not tags:
        tags.append("workflow")
    return _unique(tags)


def _infer_description(tags: List[str]) -> str:
    if "image-edit" in tags:
        return "Edit existing images with prompt-guided controls."
    if "text-to-image" in tags:
        return "Generate images from text prompts."
    if "aspect-ratio" in tags:
        return "Generate aspect-ratio variants from source content."
    return "ComfyUI workflow packaged for MCP execution."


def _infer_use_cases(tags: List[str]) -> List[str]:
    use_cases: List[str] = []
    if "image-edit" in tags:
        use_cases.append("Modify an existing image while preserving composition.")
    if "text-to-image" in tags:
        use_cases.append("Generate a fresh image from a text brief.")
    if "aspect-ratio" in tags:
        use_cases.append("Create 1:1, 4:5, or 9:16 variants for publishing surfaces.")
    if "upscaling" in tags:
        use_cases.append("Increase output resolution while retaining style.")
    if not use_cases:
        use_cases.append("Run this workflow with parameter overrides via MCP.")
    return use_cases


def _infer_io_inputs(params: Dict[str, Any]) -> List[str]:
    names = [str(item.get("name") or "") for item in params.get("params", []) if isinstance(item, dict)]
    inputs: List[str] = []
    if any(name in {"image", "image_filename"} for name in names):
        inputs.append("input_image")
    if any("prompt" in name for name in names):
        inputs.append("prompt")
    if "negative_prompt" in names:
        inputs.append("negative_prompt")
    inputs.append("overrides")
    return _unique(inputs)


def _unique(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _heuristic_default_value(key: str) -> Any:
    if key == "io_contract":
        return {"inputs": [], "outputs": ["output_images[]"]}
    if key == "notes":
        return ""
    return []
