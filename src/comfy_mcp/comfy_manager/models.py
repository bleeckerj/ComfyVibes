"""Stable Manager response models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ManagerVersion:
    version: str
    installed: bool
    supports_v4: bool = False


@dataclass(frozen=True)
class NodePackInfo:
    id: str
    name: str
    installed: str
    category: str = "installed"
    author: str = ""
    repository: str = ""
    matched_nodes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NodeMapping:
    node_type: str
    node_pack_id: str
    node_pack_name: str


@dataclass(frozen=True)
class ExternalModelInfo:
    name: str
    filename: str
    model_type: str
    base: str
    description: str
    url: str
    installed: bool
