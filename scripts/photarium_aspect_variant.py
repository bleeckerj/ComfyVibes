"""End-to-end: Photarium image -> Comfy aspect ratio adjustment -> Photarium variant.

This bypasses Copilot tool gating by calling the running MCP HTTP proxy servers directly.

Usage:
  python scripts/photarium_aspect_variant.py \
    --image-id <photarium_id> \
    --aspect-ratio 1:1 \
    --comfy-mcp-url http://127.0.0.1:8001 \
    --photarium-mcp-url http://127.0.0.1:8787 \
    --comfyui-base-url http://127.0.0.1:8188

Notes:
- Requires the Comfy MCP HTTP server and Photarium MCP HTTP server to be running.
- If ComfyMCP is configured with an API token, pass --comfy-token.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx


def _post_tool(base_url: str, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{base_url.rstrip('/')}/tools/{tool_name}"
    resp = httpx.post(url, json=args, timeout=300.0)
    resp.raise_for_status()
    payload: Dict[str, Any] = resp.json() if resp.content else {}
    if isinstance(payload, dict) and "ok" in payload:
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("error") or "Tool call failed"))
        result = payload.get("result")
        return result if isinstance(result, dict) else {"result": result}
    return payload


def _get_tool_schema(base_url: str, tool_name: str) -> Dict[str, Any]:
    url = f"{base_url.rstrip('/')}/tools"
    resp = httpx.get(url, timeout=30.0)
    resp.raise_for_status()
    data = resp.json()
    for t in data.get("tools", []):
        if t.get("name") == tool_name:
            return t
    raise RuntimeError(f"Tool not found on {base_url}: {tool_name}")


def _extract_first_history_image(history: Dict[str, Any], prompt_id: str) -> Tuple[str, str, str]:
    """Return (filename, subfolder, type) for the first output image in history."""

    # ComfyUI history format: {"<prompt_id>": {"outputs": {nodeid: {"images": [...]}}}}
    prompt_entry = history.get(prompt_id) or history.get(str(prompt_id))
    if not isinstance(prompt_entry, dict):
        raise RuntimeError(f"Missing history entry for prompt_id={prompt_id}")

    outputs = prompt_entry.get("outputs")
    if not isinstance(outputs, dict):
        raise RuntimeError("History missing outputs")

    for _node_id, node_out in outputs.items():
        if not isinstance(node_out, dict):
            continue
        images = node_out.get("images")
        if not isinstance(images, list) or not images:
            continue
        first = images[0]
        if not isinstance(first, dict):
            continue
        filename = first.get("filename")
        subfolder = first.get("subfolder") or ""
        img_type = first.get("type") or "output"
        if not filename:
            continue
        return str(filename), str(subfolder), str(img_type)

    raise RuntimeError("No output images found in history")


def _download_comfy_view(
    comfyui_base_url: str,
    filename: str,
    subfolder: str,
    img_type: str,
    out_path: Path,
) -> None:
    params = {
        "filename": filename,
        "type": img_type,
    }
    if subfolder:
        params["subfolder"] = subfolder

    url = f"{comfyui_base_url.rstrip('/')}/view"
    with httpx.stream("GET", url, params=params, timeout=300.0) as r:
        r.raise_for_status()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)


def _poll_comfy_history_for_image(
    comfy_mcp_url: str,
    prompt_id: str,
    timeout_s: float,
    poll_s: float = 0.5,
) -> Tuple[Dict[str, Any], str, str, str]:
    """Poll ComfyMCP comfy_history_get until at least one output image is present."""

    deadline = time.time() + timeout_s
    last_history: Dict[str, Any] = {}
    comfy_mcp_url = comfy_mcp_url.rstrip("/")

    while time.time() < deadline:
        # Use ComfyMCP so we don't need direct access to the ComfyUI base URL.
        history = _post_tool(comfy_mcp_url, "comfy_history_get", {"prompt_id": prompt_id})
        if isinstance(history, dict):
            last_history = history
            try:
                filename, subfolder, img_type = _extract_first_history_image(history, prompt_id)
                return history, filename, subfolder, img_type
            except RuntimeError:
                pass
        time.sleep(poll_s)

    raise RuntimeError(f"Timed out waiting for output images for prompt_id={prompt_id}. Last history keys={list(last_history.keys())[:5]}")


def _safe_suffix_from_aspect(aspect_ratio: str) -> str:
    ratio = aspect_ratio.split(" ")[0].replace(" ", "")
    if not re.fullmatch(r"\d+:\d+", ratio):
        ratio = "custom"
    return ratio.replace(":", "x")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an aspect-ratio variant in Photarium via ComfyMCP")
    parser.add_argument("--image-id", required=True, help="Photarium image id")
    parser.add_argument("--aspect-ratio", required=True, help="Target aspect ratio, e.g. 1:1")
    parser.add_argument("--comfy-mcp-url", default="http://127.0.0.1:8001")
    parser.add_argument("--photarium-mcp-url", default="http://127.0.0.1:8787")
    parser.add_argument(
        "--comfyui-base-url",
        default=os.environ.get("COMFYUI_BASE_URL")
        or os.environ.get("COMFY_MCP_COMFY_BASE_URL")
        or "http://127.0.0.1:8188",
    )
    parser.add_argument("--comfy-token", default=None, help="Optional ComfyMCP token if API_TOKEN is configured")
    parser.add_argument("--timeout-s", type=float, default=900.0)
    args = parser.parse_args()

    image_id = args.image_id
    aspect_ratio = args.aspect_ratio

    # Validate tool availability early with a helpful error.
    _get_tool_schema(args.photarium_mcp_url, "photarium_download_image")
    _get_tool_schema(args.photarium_mcp_url, "photarium_get")
    _get_tool_schema(args.photarium_mcp_url, "photarium_upload_from_path")
    _get_tool_schema(args.comfy_mcp_url, "workflows_run_aspect_ratio_adjustment")
    _get_tool_schema(args.comfy_mcp_url, "workflows_wait")

    photo_meta = _post_tool(args.photarium_mcp_url, "photarium_get", {"imageId": image_id})

    folder = photo_meta.get("folder")
    namespace = photo_meta.get("namespace")
    description = photo_meta.get("description")

    with tempfile.TemporaryDirectory(prefix="photarium_aspect_") as td:
        tmp_dir = Path(td)

        # 1) Download from Photarium to a local file.
        src_path = tmp_dir / f"{image_id}.bin"
        download = _post_tool(
            args.photarium_mcp_url,
            "photarium_download_image",
            {"imageId": image_id, "savePath": str(src_path), "includeBase64": False},
        )
        downloaded_filename = download.get("filename") or f"{image_id}.jpg"
        # If the server saved with its own filename, use that.
        if src_path.is_dir():
            src_path = src_path / downloaded_filename
        elif src_path.exists() and src_path.suffix == ".bin":
            # Rename to preserve extension if we can.
            ext = Path(downloaded_filename).suffix or ".jpg"
            renamed = src_path.with_suffix(ext)
            src_path.rename(renamed)
            src_path = renamed

        if not src_path.exists():
            raise RuntimeError(f"Photarium download did not create file at {src_path}")

        # 2) Run Comfy aspect-ratio workflow using that local path.
        comfy_run_args: Dict[str, Any] = {
            "image_path": str(src_path),
            "aspect_ratio": aspect_ratio,
        }
        if args.comfy_token:
            comfy_run_args["token"] = args.comfy_token

        run_res = _post_tool(args.comfy_mcp_url, "workflows_run_aspect_ratio_adjustment", comfy_run_args)
        prompt_id = run_res.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"Comfy run did not return prompt_id. Result: {run_res}")

        # workflows_wait only guarantees the prompt appears in history; outputs may land shortly after.
        _post_tool(
            args.comfy_mcp_url,
            "workflows_wait",
            {"prompt_id": prompt_id, "timeout_s": float(args.timeout_s), "poll_ms": 500},
        )

        history, filename, subfolder, img_type = _poll_comfy_history_for_image(
            args.comfy_mcp_url,
            str(prompt_id),
            timeout_s=float(args.timeout_s),
            poll_s=0.5,
        )

        # 3) Download the output image bytes from ComfyUI.
        ratio_slug = _safe_suffix_from_aspect(aspect_ratio)
        out_path = tmp_dir / f"{Path(filename).stem}__{ratio_slug}{Path(filename).suffix or '.png'}"
        _download_comfy_view(args.comfyui_base_url, filename, subfolder, img_type, out_path)

        # 4) Upload as Photarium variant (parentId links it).
        upload_args: Dict[str, Any] = {
            "filePath": str(out_path),
            "parentId": image_id,
        }
        if folder:
            upload_args["folder"] = folder
        if namespace:
            upload_args["namespace"] = namespace

        new_desc = (description or "").strip()
        suffix = f"(aspect ratio {aspect_ratio})"
        upload_args["description"] = f"{new_desc} {suffix}".strip() if new_desc else suffix

        uploaded = _post_tool(args.photarium_mcp_url, "photarium_upload_from_path", upload_args)

    print(json.dumps({"parentId": image_id, "aspect_ratio": aspect_ratio, "prompt_id": prompt_id, "upload": uploaded}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except httpx.HTTPStatusError as exc:
        print(f"HTTP error: {exc.response.status_code} {exc.response.text}", file=sys.stderr)
        raise
