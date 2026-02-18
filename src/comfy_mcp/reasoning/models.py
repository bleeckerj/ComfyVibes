"""Typed models for workflow capability reasoning."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class CapabilityParam:
    """A single parameter exposed by a workflow capability."""

    name: str
    param_type: str
    required: bool
    description: str = ""
    default: Any = None
    choices: List[Any] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "name": self.name,
            "type": self.param_type,
            "required": self.required,
            "description": self.description,
        }
        if self.default is not None:
            payload["default"] = self.default
        if self.choices:
            payload["choices"] = list(self.choices)
        return payload


@dataclass(frozen=True)
class CapabilityCard:
    """Human-readable + machine-usable summary of a workflow."""

    workflow_id: str
    name: str
    description: str
    tags: List[str]
    has_params: bool
    required_params: List[str]
    optional_params: List[str]
    params: List[CapabilityParam] = field(default_factory=list)
    heuristics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_params: bool = False) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "description": self.description,
            "tags": list(self.tags),
            "has_params": self.has_params,
            "required_params": list(self.required_params),
            "optional_params": list(self.optional_params),
            "heuristics": dict(self.heuristics),
        }
        if include_params:
            payload["params"] = [param.to_dict() for param in self.params]
        return payload

