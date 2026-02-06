"""Typed contracts for ComfyUI responses."""

from __future__ import annotations

from typing import Any, Dict, List

ComfyObjectInfo = Dict[str, Any]
ComfyQueue = Dict[str, Any]
ComfyHistory = Dict[str, Any]
ComfyModelTypes = List[str]
ComfyModelFiles = List[str]
ComfyEmbeddings = List[str]
ComfyPromptResult = Dict[str, Any]
