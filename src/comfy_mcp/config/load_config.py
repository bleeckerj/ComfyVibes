"""Configuration loader for ComfyMCP."""

from __future__ import annotations

from typing import Any, Dict, Optional

from comfy_mcp.config.models import AppConfig


def load_config(overrides: Optional[Dict[str, Any]] = None) -> AppConfig:
    """Load configuration from env and optional overrides."""
    return AppConfig(**(overrides or {}))
