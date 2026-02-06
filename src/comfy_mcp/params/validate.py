"""Validation for ParamSpec overrides."""

from __future__ import annotations

import re
from typing import Any, Dict, Tuple

from comfy_mcp.params.errors import ParamValidationError
from comfy_mcp.params.schema import ParamSpec, ParamSpecItem


def validate_overrides(
    spec: ParamSpec,
    overrides: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, ParamSpecItem]]:
    """Validate overrides against a ParamSpec.

    Returns normalized overrides and the param map.
    """
    param_map = spec.param_map()
    unknown = set(overrides.keys()) - set(param_map.keys())
    if unknown:
        raise ParamValidationError(f"Unknown params: {sorted(unknown)}")

    missing_required = [
        item.name for item in param_map.values() if item.required and item.name not in overrides
    ]
    if missing_required:
        raise ParamValidationError(f"Missing required params: {missing_required}")

    normalized: Dict[str, Any] = {}
    for name, value in overrides.items():
        item = param_map[name]
        _validate_type(item, value)
        _validate_constraints(item, value)
        normalized[name] = value

    return normalized, param_map


def _validate_type(item: ParamSpecItem, value: Any) -> None:
    if item.type == "string":
        if not isinstance(value, str):
            raise ParamValidationError(f"{item.name} must be a string")
    elif item.type == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ParamValidationError(f"{item.name} must be an int")
    elif item.type == "float":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ParamValidationError(f"{item.name} must be a float")
    elif item.type == "bool":
        if not isinstance(value, bool):
            raise ParamValidationError(f"{item.name} must be a bool")
    elif item.type == "enum":
        if item.constraints and item.constraints.choices:
            if value not in item.constraints.choices:
                raise ParamValidationError(f"{item.name} must be one of {item.constraints.choices}")
    else:
        raise ParamValidationError(f"Unsupported type: {item.type}")


def _validate_constraints(item: ParamSpecItem, value: Any) -> None:
    constraints = item.constraints
    if not constraints:
        return

    if constraints.min is not None:
        if isinstance(value, (int, float)) and value < constraints.min:
            raise ParamValidationError(f"{item.name} must be >= {constraints.min}")
    if constraints.max is not None:
        if isinstance(value, (int, float)) and value > constraints.max:
            raise ParamValidationError(f"{item.name} must be <= {constraints.max}")
    if constraints.regex is not None:
        if not isinstance(value, str) or re.search(constraints.regex, value) is None:
            raise ParamValidationError(f"{item.name} must match {constraints.regex}")
