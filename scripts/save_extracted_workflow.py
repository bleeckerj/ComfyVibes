"""Extract a workflow via MCP HTTP and save to the local workflow store."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen


def _request_json(url: str, payload: dict) -> dict:
    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _extract_workflow(base_url: str, image_path: str) -> dict:
    data = _request_json(f"{base_url}/tools/workflows_extract_from_artifact", {"path": image_path})
    result = data.get("result", {})
    if isinstance(result, dict) and "workflow" in result:
        return result["workflow"]
    content = result.get("content", []) if isinstance(result, dict) else []
    text = content[0].get("text") if content else None
    if text:
        workflow_payload = json.loads(text)
        workflow = workflow_payload.get("workflow")
        if workflow:
            return workflow
    raise RuntimeError("Extraction response missing workflow")


def _workflow_hash(workflow: dict) -> str:
    payload_bytes = json.dumps(workflow, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload_bytes).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Save extracted workflow into workflow store")
    parser.add_argument("--image", required=True, help="Path to workflow artifact image")
    parser.add_argument("--workflow-id", required=True, help="Workflow id for storage")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001", help="Comfy MCP HTTP base URL")
    args = parser.parse_args()

    workflow = _extract_workflow(args.base_url, args.image)
    workflow_hash = _workflow_hash(workflow)

    meta = {
        "id": args.workflow_id,
        "name": "Klein Flux 2 Text to Image",
        "description": "Flux2 text-to-image workflow extracted from img_00005_.png.",
        "tags": ["text-to-image", "flux2", "klein", "qwen"],
        "requires": {"inputs": [], "params": ["prompt", "seed", "steps", "cfg", "aspect_ratio", "megapixel"]},
    }

    params = {
        "schema_version": "1",
        "workflow_id": args.workflow_id,
        "workflow_hash": workflow_hash,
        "params": [
            {
                "name": "prompt",
                "type": "string",
                "default": "",
                "required": True,
                "description": "Positive prompt text.",
                "target": {"mode": "direct", "node_id": "6", "input": "text"},
            },
            {
                "name": "seed",
                "type": "int",
                "default": 567682720312625,
                "required": False,
                "description": "Seed for sampling.",
                "target": {"mode": "direct", "node_id": "163", "input": "seed"},
            },
            {
                "name": "steps",
                "type": "int",
                "default": 4,
                "required": False,
                "description": "Sampling steps.",
                "target": {"mode": "direct", "node_id": "163", "input": "steps"},
            },
            {
                "name": "cfg",
                "type": "float",
                "default": 1.0,
                "required": False,
                "description": "CFG guidance scale.",
                "target": {"mode": "direct", "node_id": "163", "input": "cfg"},
            },
            {
                "name": "aspect_ratio",
                "type": "string",
                "default": "3:2 (Golden Landscape)",
                "required": False,
                "description": "Flux resolution preset.",
                "target": {"mode": "direct", "node_id": "209", "input": "aspect_ratio"},
            },
            {
                "name": "megapixel",
                "type": "string",
                "default": "1.0",
                "required": False,
                "description": "Target megapixels for Flux resolution node.",
                "target": {"mode": "direct", "node_id": "209", "input": "megapixel"},
            },
        ],
    }

    root = Path("workflows") / args.workflow_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "workflow.json").write_text(json.dumps(workflow, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (root / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    (root / "params.json").write_text(json.dumps(params, indent=2) + "\n", encoding="utf-8")

    print(f"Saved workflow to {root}")


if __name__ == "__main__":
    main()
