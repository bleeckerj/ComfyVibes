"""Configuration loader for ComfyMCP."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from comfy_mcp.config.models import AppConfig


def _repo_workflows_root() -> Optional[Path]:
    """Return repo-local workflows root when running from source."""
    repo_root = Path(__file__).resolve().parents[3]
    candidate = repo_root / "workflows"
    if candidate.exists() and candidate.is_dir():
        return candidate
    return None


def load_config(overrides: Optional[Dict[str, Any]] = None) -> AppConfig:
    """Load configuration from env and optional overrides."""
    provided = overrides or {}
    if "workflow_library_root" not in provided and not os.environ.get("COMFY_MCP_WORKFLOW_LIBRARY_ROOT"):
        # Prefer the repository workflows root when running from source.
        repo_root = _repo_workflows_root()
        if repo_root is not None:
            provided = dict(provided)
            provided["workflow_library_root"] = repo_root
    return AppConfig(**provided)
