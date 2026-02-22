"""Runtime build/version metadata helpers for MCP HTTP surfaces."""

from __future__ import annotations

from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path
import platform
import subprocess
from typing import Any, Dict


def collect_runtime_info(*, service_name: str) -> Dict[str, Any]:
    """Collect stable runtime/build metadata for diagnostics endpoints."""
    root = Path(__file__).resolve().parents[3]
    commit = _git_output(root, "rev-parse", "--short=12", "HEAD")
    branch = _git_output(root, "rev-parse", "--abbrev-ref", "HEAD")

    dirty: bool | None = None
    if commit:
        dirty = _git_dirty(root)

    return {
        "service": service_name,
        "service_version": _package_version(),
        "git_commit": commit,
        "git_branch": branch,
        "git_dirty": dirty,
        "python_version": platform.python_version(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


def _package_version() -> str:
    try:
        return pkg_version("comfy-mcp")
    except PackageNotFoundError:
        return "0.0.0+unknown"


def _git_output(root: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    value = (proc.stdout or "").strip()
    return value or None


def _git_dirty(root: Path) -> bool | None:
    try:
        proc = subprocess.run(
            ["git", "diff", "--quiet", "--ignore-submodules", "HEAD", "--"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return None
    if proc.returncode == 0:
        return False
    if proc.returncode == 1:
        return True
    return None
