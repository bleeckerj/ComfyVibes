"""Hashing utilities for workflow metadata."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_bytes(payload: bytes) -> str:
    """Return sha256 hex digest for raw bytes."""
    digest = hashlib.sha256()
    digest.update(payload)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    """Return sha256 hex digest for a file path."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(data: Any) -> str:
    """Return sha256 hex digest for JSON-serializable data."""
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_bytes(payload.encode("utf-8"))
