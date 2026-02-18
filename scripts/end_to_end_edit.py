"""End-to-end test: Photarium image -> Comfy edit -> Photarium variant."""

from __future__ import annotations

import argparse
import base64
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional


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
        reason = exc.reason
        raise RuntimeError(f"Connection failed: {reason}") from exc


def _unwrap_text_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    if "content" in result:
        content = result.get("content") or []
        if content and isinstance(content, list):
            text = content[0].get("text")
            if text:
                return json.loads(text)
    return result


def _download_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url) as resp:
        return resp.read()


def _write_temp_bytes(data: bytes, filename: str) -> Path:
    suffix = Path(filename).suffix or ".bin"
    handle, path = tempfile.mkstemp(suffix=suffix)
    os.close(handle)
    temp_path = Path(path)
    temp_path.write_bytes(data)
    return temp_path


def _extract_first_image(history: Dict[str, Any]) -> Dict[str, Any]:
    if not history:
        raise RuntimeError("Empty history payload")
    prompt_id, entry = next(iter(history.items()))
    outputs = entry.get("outputs", {}) if isinstance(entry, dict) else {}
    for node in outputs.values():
        images = node.get("images") if isinstance(node, dict) else None
        if images:
            return images[0]
    raise RuntimeError("No images found in history outputs")


def _comfy_view_url(base_url: str, image: Dict[str, Any]) -> str:
    filename = image.get("filename")
    if not filename:
        raise RuntimeError("History image missing filename")
    subfolder = image.get("subfolder", "")
    image_type = image.get("type", "output")
    params = [f"filename={urllib.parse.quote(filename)}", f"type={urllib.parse.quote(image_type)}"]
    if subfolder:
        params.append(f"subfolder={urllib.parse.quote(subfolder)}")
    return f"{base_url}/view?{'&'.join(params)}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Photarium -> Comfy -> Photarium variant test")
    parser.add_argument("--image-id", required=True, help="Photarium image id")
    parser.add_argument("--prompt", required=True, help="Positive prompt")
    parser.add_argument("--workflow-id", default="image_edit", help="Workflow id to run")
    parser.add_argument("--negative", default="", help="Negative prompt")
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8001", help="Comfy MCP HTTP base URL")
    parser.add_argument("--photarium-url", default="http://127.0.0.1:8787", help="Photarium MCP HTTP base URL")
    parser.add_argument(
        "--comfy-base-url",
        default=os.environ.get("COMFY_MCP_COMFY_BASE_URL", "http://127.0.0.1:8188"),
        help="ComfyUI base URL for /view downloads",
    )
    parser.add_argument("--parent", action="store_true", help="Upload as variant of the source image")
    args = parser.parse_args()

    print("Fetching Photarium image...")
    photarium_get = _request_json(
        "POST",
        f"{args.photarium_url}/tools/photarium_get",
        {"imageId": args.image_id},
    )
    payload = _unwrap_text_payload(photarium_get.get("result", {}))
    image_payload = payload.get("image") if isinstance(payload, dict) else None
    if not image_payload and isinstance(payload, dict):
        image_payload = payload
    if not image_payload:
        raise RuntimeError(f"Photarium get did not return image: {payload}")
    source_url = image_payload.get("url")

    print("Downloading source image via Photarium...")
    download_payload = _request_json(
        "POST",
        f"{args.photarium_url}/tools/photarium_download_image",
        {"imageId": args.image_id},
    )
    download_result = _unwrap_text_payload(download_payload.get("result", {}))
    base64_data = download_result.get("base64") if isinstance(download_result, dict) else None
    filename = download_result.get("filename", "image.bin") if isinstance(download_result, dict) else "image.bin"
    if not base64_data:
        raise RuntimeError(f"Photarium download did not return base64 data: {download_result}")
    temp_path = _write_temp_bytes(base64.b64decode(base64_data), filename)

    print("Uploading image to ComfyUI...")
    upload = _request_json(
        "POST",
        f"{args.comfy_url}/tools/comfy_upload_image",
        {"file_path": str(temp_path)},
    ).get("result", {})
    uploaded_name = upload.get("name")
    uploaded_subfolder = upload.get("subfolder", "")
    if not uploaded_name:
        raise RuntimeError("Comfy upload did not return a filename")

    if uploaded_subfolder:
        image_filename = f"{uploaded_subfolder}/{uploaded_name}"
    else:
        image_filename = uploaded_name

    print("Running workflow...")
    overrides = {
        "image_filename": image_filename,
        "positive_prompt": args.prompt,
        "negative_prompt": args.negative,
    }
    run_payload = _request_json(
        "POST",
        f"{args.comfy_url}/tools/workflows_run",
        {"workflow_id": args.workflow_id, "overrides": overrides, "force": True},
    ).get("result", {})
    prompt_id = run_payload.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"Workflow run failed: {run_payload}")

    print("Waiting for completion...")
    wait_payload = _request_json(
        "POST",
        f"{args.comfy_url}/tools/workflows_wait",
        {"prompt_id": prompt_id, "timeout_s": 180, "poll_ms": 500},
    ).get("result", {})
    status = wait_payload.get("status")
    if status != "complete":
        raise RuntimeError(f"Workflow did not complete: {wait_payload}")

    history = wait_payload.get("history", {})
    image_info = _extract_first_image(history)
    output_url = _comfy_view_url(args.comfy_base_url, image_info)

    print("Downloading workflow output...")
    output_bytes = _download_bytes(output_url)
    encoded = base64.b64encode(output_bytes).decode("utf-8")
    output_filename = image_info.get("filename") or "output.png"

    print("Uploading result to Photarium...")
    upload_args = {
        "base64": encoded,
        "filename": output_filename,
        "parentId": args.image_id if args.parent else None,
        "sourceUrl": source_url,
    }
    if upload_args["parentId"] is None:
        upload_args.pop("parentId")

    photarium_upload = _request_json(
        "POST",
        f"{args.photarium_url}/tools/photarium_upload_image",
        upload_args,
    )

    print(json.dumps(photarium_upload, indent=2))


if __name__ == "__main__":
    main()
