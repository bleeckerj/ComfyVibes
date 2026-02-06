"""Selector-based targeting helpers (reserved for MVP+)."""

from __future__ import annotations

from typing import Any, Dict


def resolve_selector(workflow: Dict[str, Any], selector: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve selector targets to direct node/input paths.

    This is a placeholder for selector-based targeting in MVP+.
    """
    raise NotImplementedError("Selector-based targeting is not implemented in MVP")
