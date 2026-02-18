"""Generate meta.json stubs for workflows missing metadata."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Any


def _load_workflow_id(path: Path) -> str:
    return path.name


def _build_meta(workflow_id: str) -> Dict[str, Any]:
    return {
        "id": workflow_id,
        "name": workflow_id.replace("_", " ").replace("-", " ").title(),
        "description": "TODO: describe what this workflow does.",
        "tags": [],
        "requires": {"inputs": [], "params": []},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate meta.json for workflows")
    parser.add_argument(
        "--root",
        default=str(Path("workflows").resolve()),
        help="Workflow root directory (default: ./workflows)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing meta.json files",
    )
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"Workflow root does not exist: {root}")

    written = 0
    skipped = 0
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        workflow_file = entry / "workflow.json"
        if not workflow_file.exists():
            continue
        meta_file = entry / "meta.json"
        if meta_file.exists() and not args.force:
            skipped += 1
            continue
        meta = _build_meta(_load_workflow_id(entry))
        meta_file.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        written += 1

    print(f"meta.json written: {written}, skipped: {skipped}")


if __name__ == "__main__":
    main()
