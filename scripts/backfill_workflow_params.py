"""Backfill params.json and meta.requires.params for all stored workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from comfy_mcp.params.infer import infer_params_spec, sync_meta_requires
from comfy_mcp.workflow_store.store import WorkflowStore


def _default_meta(workflow_id: str) -> Dict[str, Any]:
    return {
        "id": workflow_id,
        "name": workflow_id.replace("_", " ").replace("-", " ").title(),
        "description": "",
        "tags": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill workflow params/meta from workflow.json")
    parser.add_argument(
        "--root",
        default="workflows",
        help="Workflow store root directory (default: workflows)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned updates without writing files",
    )
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    store = WorkflowStore(root)
    updated = 0

    for entry in store.list_entries():
        workflow_id = entry.workflow_id
        workflow = store.read_workflow(workflow_id)
        params = infer_params_spec(workflow_id, workflow)
        meta = store.read_meta(workflow_id) or _default_meta(workflow_id)
        meta = sync_meta_requires(meta, params)

        if args.dry_run:
            print(f"[dry-run] {workflow_id}: {len(params['params'])} params")
            continue

        store.save_workflow(workflow_id, workflow, meta=meta, params=params)
        updated += 1
        print(
            json.dumps(
                {"workflow_id": workflow_id, "params": len(params["params"]), "updated": True},
                ensure_ascii=False,
            )
        )

    if args.dry_run:
        print("No files written (dry run).")
    else:
        print(f"Updated workflows: {updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
