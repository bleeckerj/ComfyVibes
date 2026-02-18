"""Sanitize tool results for display and LLM context.

Detects base64 image data and other giant string blobs in tool results
and replaces them with concise placeholders. Optionally saves binary
payloads to disk so nothing is lost.
"""

from __future__ import annotations

import base64
import re
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Heuristic: strings longer than this that look like base64 get replaced.
_BASE64_MIN_LEN = 500
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/\n\r]+=*$")
_DATA_URI_RE = re.compile(r"^data:([^;]+);base64,(.+)$", re.DOTALL)

# Max length for any single string value kept in display/LLM context.
_MAX_STRING_LEN = 2000

# Directory for saved binary payloads.
_ARTIFACT_DIR = Path.cwd() / ".mcp_chat_artifacts"


def _looks_like_base64(value: str) -> bool:
    if len(value) < _BASE64_MIN_LEN:
        return False
    # Strip data URI prefix if present
    m = _DATA_URI_RE.match(value)
    sample = m.group(2)[:200] if m else value[:200]
    return bool(_BASE64_RE.match(sample.replace("\n", "").replace("\r", "")))


def _human_size(n_bytes: int) -> str:
    if n_bytes < 1024:
        return f"{n_bytes}B"
    for unit in ("KB", "MB", "GB"):
        n_bytes /= 1024
        if n_bytes < 1024:
            return f"{n_bytes:.1f}{unit}"
    return f"{n_bytes:.1f}TB"


def _save_artifact(data_bytes: bytes, mime: str, tool_name: str) -> Path:
    """Save binary data to disk and return the file path."""
    _ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    ext = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(mime, ".bin")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", tool_name)[:40]
    path = _ARTIFACT_DIR / f"{safe_name}_{ts}{ext}"
    path.write_bytes(data_bytes)
    return path


def _sanitize_base64_string(value: str, tool_name: str, save: bool) -> str:
    """Replace a base64 string with a placeholder, optionally saving to disk."""
    m = _DATA_URI_RE.match(value)
    if m:
        mime = m.group(1)
        raw_b64 = m.group(2)
    else:
        mime = "application/octet-stream"
        raw_b64 = value

    # Estimate decoded size
    clean = raw_b64.replace("\n", "").replace("\r", "")
    n_bytes = int(len(clean) * 3 / 4)
    size_str = _human_size(n_bytes)

    saved_path = None
    if save:
        try:
            data = base64.b64decode(raw_b64)
            saved_path = _save_artifact(data, mime, tool_name)
        except Exception:
            pass  # If decode fails, just show the placeholder

    if saved_path:
        return f"[base64 {mime} ~{size_str} → saved: {saved_path}]"
    return f"[base64 {mime} ~{size_str}]"


def sanitize_result(result: Any, tool_name: str = "", save_artifacts: bool = True) -> Any:
    """Deep-walk a tool result and replace base64 / huge strings with placeholders.

    Returns a new structure (does not mutate the original).
    """
    return _walk(result, tool_name, save_artifacts)


def _walk(obj: Any, tool_name: str, save: bool) -> Any:
    if isinstance(obj, str):
        if _looks_like_base64(obj):
            return _sanitize_base64_string(obj, tool_name, save)
        if len(obj) > _MAX_STRING_LEN:
            return obj[:_MAX_STRING_LEN] + f"… [truncated, {len(obj)} chars total]"
        return obj
    if isinstance(obj, dict):
        return {k: _walk(v, tool_name, save) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(item, tool_name, save) for item in obj]
    return obj
