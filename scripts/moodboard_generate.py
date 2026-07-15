"""Build mood-board candidates by chaining Photarium and Comfy MCP tools.

MVP pipeline:
1) Compile a style query from brief + phrases.
2) Retrieve reference image IDs via semantic/color/list tools (if available).
3) Select a workflow via capability search (unless explicit workflow_id passed).
4) Run workflow variants and emit a JSON manifest with provenance.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _request_json(method: str, url: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else str(exc)
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Connection failed: {exc.reason}") from exc


def _unwrap_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload

    if "ok" in payload:
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("error") or "Tool call failed"))
        return _unwrap_payload(payload.get("result"))

    if "result" in payload:
        return _unwrap_payload(payload.get("result"))

    content = payload.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and isinstance(first.get("text"), str):
            text = first["text"]
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}

    return payload


def _list_tools(base_url: str) -> Dict[str, Dict[str, Any]]:
    raw = _request_json("GET", f"{base_url.rstrip('/')}/tools")
    data = _unwrap_payload(raw)
    items = data.get("tools", []) if isinstance(data, dict) else []
    tools: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            tools[item["name"]] = item
    return tools


def _call_tool(base_url: str, tool_name: str, args: Dict[str, Any]) -> Any:
    raw = _request_json("POST", f"{base_url.rstrip('/')}/tools/{tool_name}", args)
    return _unwrap_payload(raw)


def _tool_schema_props(tool_def: Dict[str, Any]) -> Dict[str, Any]:
    schema = tool_def.get("inputSchema")
    if not isinstance(schema, dict):
        return {}
    props = schema.get("properties")
    if not isinstance(props, dict):
        return {}
    return props


def _find_tool(tools: Dict[str, Dict[str, Any]], candidates: List[str]) -> Optional[str]:
    for name in candidates:
        if name in tools:
            return name
    return None


def _pick_key(keys: Iterable[str], options: List[str]) -> Optional[str]:
    key_set = set(keys)
    for opt in options:
        if opt in key_set:
            return opt
    return None


def _build_search_args(tool_def: Dict[str, Any], query: str, limit: int, color_hex: Optional[str]) -> Dict[str, Any]:
    args: Dict[str, Any] = {}
    props = _tool_schema_props(tool_def)
    keys = props.keys()

    query_key = _pick_key(keys, ["query", "text", "q", "prompt", "term", "search"])
    if query_key:
        args[query_key] = query

    limit_key = _pick_key(keys, ["limit", "k", "top_k", "count", "n"])
    if limit_key:
        args[limit_key] = limit

    if color_hex:
        color_key = _pick_key(keys, ["color", "hex", "color_hex", "targetColor"])
        if color_key:
            args[color_key] = color_hex
        colors_key = _pick_key(keys, ["colors", "palette"])
        if colors_key:
            args[colors_key] = [color_hex]

    return args


def _iter_dicts(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _iter_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_dicts(item)


def _extract_image_ids(payload: Any) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for item in _iter_dicts(payload):
        for key in ("imageId", "image_id", "id", "uuid"):
            raw = item.get(key)
            if not isinstance(raw, str):
                continue
            candidate = raw.strip()
            if len(candidate) < 8:
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def _compile_query(brief: str, phrases: List[str]) -> str:
    parts = [brief.strip()] if brief.strip() else []
    parts.extend(p.strip() for p in phrases if p.strip())
    query = ", ".join(parts).strip().strip(",")
    return query or "brand mood board inspiration imagery"


def _retrieve_references(
    photarium_url: str,
    photarium_tools: Dict[str, Dict[str, Any]],
    query: str,
    starter_image_ids: List[str],
    color_hex: Optional[str],
    reference_limit: int,
) -> Dict[str, Any]:
    semantic_tool = _find_tool(
        photarium_tools,
        [
            "catalog_search_semantic",
            "photarium_search_semantic",
            "catalog_semantic_search",
            "photarium_semantic_search",
            "catalog_search",
            "photarium_search",
        ],
    )
    color_tool = _find_tool(
        photarium_tools,
        [
            "catalog_search_color",
            "photarium_search_color",
            "catalog_color_search",
            "photarium_color_search",
        ],
    )
    list_tool = _find_tool(photarium_tools, ["photarium_list", "catalog_list"])

    refs: List[str] = []
    for image_id in starter_image_ids:
        if image_id not in refs:
            refs.append(image_id)

    traces: List[Dict[str, Any]] = []

    def _merge_from_tool(tool_name: Optional[str], query_text: str, color: Optional[str], limit: int) -> None:
        if not tool_name:
            return
        tool_def = photarium_tools.get(tool_name, {})
        args = _build_search_args(tool_def, query_text, limit, color)
        if not args:
            args = {"limit": limit}
            if query_text:
                args["query"] = query_text
            if color:
                args["color"] = color
        try:
            payload = _call_tool(photarium_url, tool_name, args)
            ids = _extract_image_ids(payload)
            for image_id in ids:
                if image_id not in refs:
                    refs.append(image_id)
            traces.append({"tool": tool_name, "args": args, "hit_count": len(ids)})
        except Exception as exc:
            traces.append({"tool": tool_name, "args": args, "error": str(exc)})

    remaining = max(0, reference_limit - len(refs))
    if remaining > 0:
        _merge_from_tool(semantic_tool, query, None, remaining)

    remaining = max(0, reference_limit - len(refs))
    if remaining > 0 and color_hex:
        _merge_from_tool(color_tool, query, color_hex, remaining)

    remaining = max(0, reference_limit - len(refs))
    if remaining > 0:
        _merge_from_tool(list_tool, query, None, remaining)

    return {
        "reference_ids": refs[:reference_limit],
        "semantic_tool": semantic_tool,
        "color_tool": color_tool,
        "list_tool": list_tool,
        "trace": traces,
    }


def _select_workflow(
    comfy_url: str,
    explicit_workflow_id: Optional[str],
    query: str,
) -> Dict[str, Any]:
    if explicit_workflow_id:
        return {
            "workflow_id": explicit_workflow_id,
            "source": "explicit",
            "capability": None,
        }

    cap_payload = _call_tool(
        comfy_url,
        "workflows_capabilities_list",
        {"query": query, "limit": 15, "include_params": True},
    )
    capabilities = cap_payload.get("capabilities", []) if isinstance(cap_payload, dict) else []
    if isinstance(capabilities, list) and capabilities:
        best = None
        best_score = -1
        for item in capabilities:
            if not isinstance(item, dict):
                continue
            workflow_id = item.get("workflow_id")
            if not isinstance(workflow_id, str) or not workflow_id:
                continue
            required = [str(v) for v in (item.get("required_params") or [])]
            optional = [str(v) for v in (item.get("optional_params") or [])]
            all_params = {p.lower() for p in required + optional}
            score = 0
            if any("image" in p for p in all_params):
                score += 3
            if "positive_prompt" in all_params or "prompt" in all_params:
                score += 2
            if "filename_prefix" in all_params:
                score += 1
            if score > best_score:
                best = item
                best_score = score
        if isinstance(best, dict) and isinstance(best.get("workflow_id"), str):
            return {
                "workflow_id": best["workflow_id"],
                "source": "capabilities",
                "capability": best,
            }

    search_payload = _call_tool(comfy_url, "workflows_search", {"query": query, "limit": 5})
    workflows = search_payload.get("workflows", []) if isinstance(search_payload, dict) else []
    if isinstance(workflows, list) and workflows:
        first = workflows[0]
        if isinstance(first, dict) and isinstance(first.get("id"), str):
            return {
                "workflow_id": first["id"],
                "source": "search",
                "capability": None,
            }

    raise RuntimeError("Unable to select a workflow from capabilities/search results")


def _choose_param(param_names: List[str], candidates: List[str]) -> Optional[str]:
    lowered = {name.lower(): name for name in param_names}
    for key in candidates:
        if key in lowered:
            return lowered[key]
    for name in param_names:
        lname = name.lower()
        for key in candidates:
            if key in lname:
                return name
    return None


def _build_overrides(
    params_payload: Dict[str, Any],
    style_prompt: str,
    image_path: Optional[str],
    run_index: int,
    seed_base: Optional[int],
) -> Dict[str, Any]:
    params = params_payload.get("params", []) if isinstance(params_payload, dict) else []
    names: List[str] = []
    for item in params:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"])

    overrides: Dict[str, Any] = {}
    image_key = _choose_param(names, ["image_filename", "image", "input_image", "image_path"])
    prompt_key = _choose_param(names, ["positive_prompt", "prompt", "text"])
    negative_key = _choose_param(names, ["negative_prompt", "negative", "negative_text"])
    seed_key = _choose_param(names, ["seed"])
    prefix_key = _choose_param(names, ["filename_prefix"])

    if image_key and image_path:
        overrides[image_key] = image_path
    if prompt_key:
        overrides[prompt_key] = style_prompt
    if negative_key:
        overrides[negative_key] = "blurry, low quality, malformed anatomy, duplicate limbs, clutter"
    if seed_key and seed_base is not None:
        overrides[seed_key] = int(seed_base + run_index)
    if prefix_key:
        overrides[prefix_key] = f"moodboard_{run_index:03d}"

    return overrides


def _resolve_downloaded_path(save_path: Path, payload: Any) -> Path:
    if save_path.exists() and save_path.is_file():
        return save_path
    if isinstance(payload, dict):
        for key in ("savePath", "path", "filePath", "localPath"):
            raw = payload.get(key)
            if isinstance(raw, str):
                candidate = Path(raw)
                if candidate.exists() and candidate.is_file():
                    return candidate
    raise RuntimeError(f"Unable to resolve downloaded local file path from save path={save_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate mood-board candidates from brief + references")
    parser.add_argument("--brief", default="", help="Brand vibe brief (text)")
    parser.add_argument("--phrase", action="append", default=[], help="Additional style phrase (repeatable)")
    parser.add_argument("--starter-image-id", action="append", default=[], help="Starter Photarium image id (repeatable)")
    parser.add_argument("--palette-color", default=None, help="Optional hex color to bias reference search (e.g. #D6B26B)")
    parser.add_argument("--workflow-id", default=None, help="Explicit workflow_id to run (skips auto-selection)")
    parser.add_argument("--count", type=int, default=6, help="Number of generated candidates")
    parser.add_argument("--reference-limit", type=int, default=12, help="Max references to retrieve")
    parser.add_argument("--seed-base", type=int, default=None, help="Optional base seed for deterministic sweeps")
    parser.add_argument("--upload", action="store_true", help="Upload generated outputs back to Photarium when possible")
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8181", help="Comfy MCP HTTP base URL")
    parser.add_argument("--photarium-url", default="http://127.0.0.1:8787", help="Photarium MCP HTTP base URL")
    parser.add_argument("--manifest-path", default=None, help="Path to write JSON manifest (default: /tmp timestamp file)")
    args = parser.parse_args()

    query = _compile_query(args.brief, args.phrase)
    style_prompt = query

    comfy_tools = _list_tools(args.comfy_url)
    photarium_tools = _list_tools(args.photarium_url)

    retrieval = _retrieve_references(
        args.photarium_url,
        photarium_tools,
        query=query,
        starter_image_ids=list(args.starter_image_id),
        color_hex=args.palette_color,
        reference_limit=max(1, args.reference_limit),
    )
    reference_ids = retrieval["reference_ids"]

    selection = _select_workflow(
        args.comfy_url,
        explicit_workflow_id=args.workflow_id,
        query=query,
    )
    workflow_id = selection["workflow_id"]

    params_payload = _call_tool(args.comfy_url, "workflows_params_get", {"workflow_id": workflow_id})
    params_obj = params_payload.get("params", {}) if isinstance(params_payload, dict) else {}

    runs: List[Dict[str, Any]] = []
    upload_url_tool = _find_tool(photarium_tools, ["photarium_upload_url"])

    with tempfile.TemporaryDirectory(prefix="moodboard_refs_") as td:
        tmp_dir = Path(td)
        for run_index in range(max(1, args.count)):
            ref_id = reference_ids[run_index % len(reference_ids)] if reference_ids else None
            local_path: Optional[Path] = None

            if ref_id:
                save_path = tmp_dir / f"{ref_id}_{run_index:03d}.bin"
                download_payload = _call_tool(
                    args.photarium_url,
                    "photarium_download_image",
                    {"imageId": ref_id, "savePath": str(save_path), "includeBase64": False},
                )
                local_path = _resolve_downloaded_path(save_path, download_payload)

            overrides = _build_overrides(
                params_payload=params_obj if isinstance(params_obj, dict) else {},
                style_prompt=style_prompt,
                image_path=str(local_path) if local_path else None,
                run_index=run_index,
                seed_base=args.seed_base,
            )
            if not overrides:
                raise RuntimeError(
                    f"No usable overrides inferred for workflow '{workflow_id}'. "
                    "Inspect params and set --workflow-id to one with compatible params."
                )

            run_payload = _call_tool(
                args.comfy_url,
                "workflows_run",
                {"workflow_id": workflow_id, "overrides": overrides, "force": True},
            )
            output_images = run_payload.get("output_images", []) if isinstance(run_payload, dict) else []

            uploads: List[Dict[str, Any]] = []
            if args.upload and upload_url_tool and isinstance(output_images, list):
                for image in output_images:
                    if not isinstance(image, dict):
                        continue
                    view_url = image.get("view_url")
                    if not isinstance(view_url, str) or not view_url:
                        continue
                    upload_args: Dict[str, Any] = {"url": view_url}
                    if ref_id:
                        upload_args["parentId"] = ref_id
                    try:
                        uploaded = _call_tool(args.photarium_url, upload_url_tool, upload_args)
                        uploads.append({"ok": True, "result": uploaded, "args": upload_args})
                    except Exception as exc:
                        uploads.append({"ok": False, "error": str(exc), "args": upload_args})

            runs.append(
                {
                    "run_index": run_index,
                    "reference_image_id": ref_id,
                    "local_input_path": str(local_path) if local_path else None,
                    "workflow_id": workflow_id,
                    "overrides": overrides,
                    "result": run_payload,
                    "uploads": uploads,
                }
            )

    manifest = {
        "created_at_epoch_s": time.time(),
        "inputs": {
            "brief": args.brief,
            "phrases": args.phrase,
            "starter_image_ids": args.starter_image_id,
            "palette_color": args.palette_color,
            "query": query,
        },
        "retrieval": retrieval,
        "selection": selection,
        "run_count": len(runs),
        "runs": runs,
    }

    manifest_path = Path(
        args.manifest_path
        if args.manifest_path
        else f"/tmp/moodboard_manifest_{int(time.time())}.json"
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    summary = {
        "workflow_id": workflow_id,
        "reference_count": len(reference_ids),
        "run_count": len(runs),
        "manifest_path": str(manifest_path),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
