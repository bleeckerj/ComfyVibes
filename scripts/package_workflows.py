"""Package workflows into MCP-ready meta.json + params.json artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from comfy_mcp.workflow_store.packaging import build_hints_template, package_workflow
from comfy_mcp.workflow_store.store import WorkflowStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Package workflows for Comfy MCP.")
    parser.add_argument(
        "--root",
        default="workflows",
        help="Workflow store root directory (default: workflows)",
    )
    parser.add_argument(
        "--workflow-id",
        action="append",
        default=[],
        help="Specific workflow id(s) to package. Repeat flag to include multiple.",
    )
    parser.add_argument(
        "--hints",
        default=None,
        help="Path to JSON object of per-workflow metadata overrides.",
    )
    parser.add_argument(
        "--write-hints-template",
        default=None,
        help="Write a hints template JSON and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing files.",
    )
    parser.add_argument(
        "--no-suggestions",
        action="store_true",
        help="Disable automatic metadata suggestions for missing fields.",
    )
    args = parser.parse_args()

    store = WorkflowStore(Path(args.root).expanduser().resolve())
    selected_ids = _selected_ids(store, args.workflow_id)
    hints_map = _load_hints(Path(args.hints).expanduser().resolve()) if args.hints else {}

    if args.write_hints_template:
        out_path = Path(args.write_hints_template).expanduser().resolve()
        template: Dict[str, Any] = {}
        for workflow_id in selected_ids:
            workflow = store.read_workflow(workflow_id)
            meta = store.read_meta(workflow_id)
            template[workflow_id] = build_hints_template(workflow_id, workflow, existing_meta=meta)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(template, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"hints template written: {out_path}")
        return 0

    updated = 0
    for workflow_id in selected_ids:
        workflow = store.read_workflow(workflow_id)
        existing_meta = store.read_meta(workflow_id)
        packaged = package_workflow(
            workflow_id,
            workflow,
            existing_meta=existing_meta,
            hints=hints_map.get(workflow_id, {}),
            include_suggestions=not args.no_suggestions,
        )

        params_count = len(packaged["params"].get("params", []))
        if args.dry_run:
            print(json.dumps({"workflow_id": workflow_id, "params": params_count, "write": False}))
            continue

        store.save_workflow(
            workflow_id,
            workflow,
            meta=packaged["meta"],
            params=packaged["params"],
        )
        updated += 1
        print(json.dumps({"workflow_id": workflow_id, "params": params_count, "write": True}))

    if args.dry_run:
        print("dry-run complete: no files written.")
    else:
        print(f"packaged workflows: {updated}")
    return 0


def _selected_ids(store: WorkflowStore, requested: List[str]) -> List[str]:
    if requested:
        return requested
    return [entry.workflow_id for entry in store.list_entries()]


def _load_hints(path: Path) -> Dict[str, Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Hints file must be a JSON object keyed by workflow_id.")
    hints: Dict[str, Dict[str, Any]] = {}
    for key, value in payload.items():
        if not isinstance(value, dict):
            continue
        hints[str(key)] = value
    return hints


if __name__ == "__main__":
    raise SystemExit(main())
