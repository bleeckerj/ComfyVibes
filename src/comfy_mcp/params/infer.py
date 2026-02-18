"""Infer ParamSpec payloads from ComfyUI workflow JSON."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, Iterable, Iterator, List, Tuple

from comfy_mcp.workflow_store.hashing import sha256_json


_PRIORITY_FIELDS = {
    "seed": 100,
    "steps": 90,
    "cfg": 80,
    "denoise": 70,
    "width": 60,
    "height": 60,
    "batch_size": 50,
    "text": 40,
    "prompt": 40,
    "negative": 30,
    "sampler_name": 25,
    "scheduler": 25,
}


def infer_params_spec(workflow_id: str, workflow_json: Dict[str, Any]) -> Dict[str, Any]:
    """Infer a ParamSpec payload from workflow node scalar inputs."""
    name_counts: Dict[str, int] = defaultdict(int)
    params: List[Dict[str, Any]] = []

    for node_id, node in _sorted_nodes(workflow_json):
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue

        class_type = str(node.get("class_type") or "node")
        title = _node_title(node, class_type)

        for input_name, input_value in inputs.items():
            for leaf_path, leaf_value in _iter_scalar_leaves(input_value):
                inferred = _infer_param_type(leaf_value)
                if inferred is None:
                    continue

                base_name = _base_param_name(input_name, class_type, title, leaf_path)
                unique_name = _unique_name(base_name, name_counts)
                description = _description(title, input_name, leaf_path)
                target: Dict[str, Any] = {
                    "mode": "direct",
                    "node_id": str(node_id),
                    "input": str(input_name),
                }
                if leaf_path:
                    target["path"] = [str(part) for part in leaf_path]

                item: Dict[str, Any] = {
                    "name": unique_name,
                    "type": inferred,
                    "default": leaf_value,
                    "required": False,
                    "description": description,
                    "target": target,
                }
                params.append(item)

    params.sort(key=_param_sort_key)
    return {
        "schema_version": "1",
        "workflow_id": workflow_id,
        "workflow_hash": sha256_json(workflow_json),
        "params": params,
    }


def param_names_from_spec(params_spec: Dict[str, Any]) -> List[str]:
    """Return ordered param names from a params payload."""
    names: List[str] = []
    for item in params_spec.get("params", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def sync_meta_requires(meta: Dict[str, Any], params_spec: Dict[str, Any]) -> Dict[str, Any]:
    """Return meta with requires.params synchronized from params spec."""
    merged = dict(meta)
    requires = merged.get("requires")
    if not isinstance(requires, dict):
        requires = {}

    existing_inputs = requires.get("inputs")
    if isinstance(existing_inputs, list):
        inputs = [str(item) for item in existing_inputs]
    else:
        inputs = []

    requires["inputs"] = inputs
    requires["params"] = param_names_from_spec(params_spec)
    merged["requires"] = requires
    return merged


def _sorted_nodes(workflow_json: Dict[str, Any]) -> Iterable[Tuple[str, Any]]:
    def _key(item: Tuple[str, Any]) -> Tuple[int, int | str]:
        node_id = str(item[0])
        if node_id.isdigit():
            return (0, int(node_id))
        return (1, node_id)

    return sorted(workflow_json.items(), key=_key)


def _node_title(node: Dict[str, Any], class_type: str) -> str:
    meta = node.get("_meta")
    if isinstance(meta, dict):
        title = meta.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return class_type


def _iter_scalar_leaves(value: Any, path: Tuple[str, ...] = ()) -> Iterator[Tuple[Tuple[str, ...], Any]]:
    if _is_link(value):
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                continue
            yield from _iter_scalar_leaves(child, path + (key,))
        return
    if isinstance(value, list):
        return
    yield path, value


def _is_link(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    if len(value) != 2:
        return False
    first, second = value
    return isinstance(first, (str, int)) and isinstance(second, int)


def _infer_param_type(value: Any) -> str | None:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    return None


def _base_param_name(
    input_name: str,
    class_type: str,
    title: str,
    path: Tuple[str, ...],
) -> str:
    class_blob = f"{class_type} {title}".lower()
    if input_name in {"text", "prompt"}:
        if "negative" in class_blob:
            base = "negative_prompt"
        elif "positive" in class_blob:
            base = "positive_prompt"
        else:
            base = "prompt"
    else:
        base = _snake_case(input_name)

    if path:
        path_part = "_".join(_snake_case(part) for part in path if _snake_case(part))
        if path_part:
            base = f"{base}_{path_part}"

    return base or "param"


def _snake_case(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"[^a-zA-Z0-9]+", "_", value)
    return value.strip("_").lower()


def _unique_name(base_name: str, counts: Dict[str, int]) -> str:
    counts[base_name] += 1
    index = counts[base_name]
    if index == 1:
        return base_name
    return f"{base_name}_{index}"


def _description(title: str, input_name: str, path: Tuple[str, ...]) -> str:
    field = str(input_name)
    if path:
        field = ".".join([field, *path])
    return f"{title}.{field}"


def _param_sort_key(item: Dict[str, Any]) -> Tuple[int, str]:
    target = item.get("target")
    input_name = ""
    if isinstance(target, dict):
        input_name = str(target.get("input") or "")
    return (-_PRIORITY_FIELDS.get(input_name, 0), str(item.get("name") or ""))

