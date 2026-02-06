"""ParamSpec schema models."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


ParamType = Literal["string", "int", "float", "bool", "enum"]
TargetMode = Literal["direct", "selector"]


class ParamConstraints(BaseModel):
    """Optional constraints for parameter values."""

    min: Optional[float] = None
    max: Optional[float] = None
    regex: Optional[str] = None
    choices: Optional[List[Any]] = None


class ParamTarget(BaseModel):
    """Target definition for patching workflow inputs."""

    model_config = ConfigDict(extra="allow")

    mode: TargetMode = Field(default="direct")
    node_id: Optional[str] = None
    input: Optional[str] = None
    path: Optional[List[str]] = None


class ParamSpecItem(BaseModel):
    """ParamSpec parameter item definition."""

    name: str
    type: ParamType
    default: Optional[Any] = None
    required: bool = False
    description: str = ""
    constraints: Optional[ParamConstraints] = None
    target: ParamTarget

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Param name must be non-empty")
        return value


class ParamSpec(BaseModel):
    """ParamSpec root schema."""

    schema_version: Literal["1"]
    workflow_id: str
    workflow_hash: str
    params: List[ParamSpecItem]

    def param_map(self) -> Dict[str, ParamSpecItem]:
        """Return params keyed by name."""
        return {param.name: param for param in self.params}
