"""Typed contracts for ComfyUI responses."""

from __future__ import annotations

from typing import Any, Dict, List, NotRequired, TypedDict


class ComfyJobPagination(TypedDict, total=False):
    offset: int
    limit: int | None
    total: int
    has_more: bool


class ComfyJobList(TypedDict, total=False):
    jobs: list[dict[str, Any]]
    pagination: ComfyJobPagination


class ComfyOperationResult(TypedDict, total=False):
    status: str
    operation: str
    result: Any
    availability: NotRequired[str]
    error: NotRequired[str]

ComfyObjectInfo = Dict[str, Any]
ComfyQueue = Dict[str, Any]
ComfyHistory = Dict[str, Any]
ComfyFeatures = Dict[str, Any]
ComfySystemStats = Dict[str, Any]
ComfyWorkflowTemplates = Dict[str, Any]
ComfyAssets = Dict[str, Any]
ComfyTags = Dict[str, Any]
ComfyModelTypes = List[str]
ComfyModelFiles = List[str]
ComfyEmbeddings = List[str]
ComfyPromptResult = Dict[str, Any]
