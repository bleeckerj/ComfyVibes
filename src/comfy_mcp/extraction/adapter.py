"""Workflow extraction adapter for ComfyUI artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from comfy_mcp.extraction.normalize import detect_workflow_format, ui_to_api_format
from comfy_mcp.workflow_store.hashing import sha256_json


@dataclass(frozen=True)
class ExtractionResult:
    """Result of extracting a workflow from an artifact."""

    workflow: Dict[str, Any]
    workflow_format: str
    raw_metadata: Optional[Dict[str, Any]] = None


class WorkflowExtractor:
    """Adapter around a workflow extraction backend."""

    def __init__(self, backend: Optional[Any] = None) -> None:
        self._backend = backend or self._load_backend()

    def extract_from_path(self, path: str, preserve_format: bool = False) -> ExtractionResult:
        """Extract workflow metadata from an image or video file path."""
        result = self._backend.extract_from_media(path)
        if not result or not isinstance(result, dict):
            raise ValueError("No workflow found in artifact")

        workflow = result
        workflow_format = detect_workflow_format(workflow)
        if not preserve_format and workflow_format == "ui":
            workflow = ui_to_api_format(workflow)
            workflow_format = "api"

        return ExtractionResult(
            workflow=workflow,
            workflow_format=workflow_format,
            raw_metadata=result,
        )

    def extract_and_write(
        self,
        path: str,
        output_dir: Optional[Path] = None,
        base_name: Optional[str] = None,
    ) -> Dict[str, Path]:
        """Extract workflow and write API JSON + params.json files."""
        artifact_path = Path(path)
        target_dir = (output_dir or artifact_path.parent).expanduser().resolve()
        name = base_name or artifact_path.stem

        extracted = self.extract_from_path(path, preserve_format=True)
        ui_workflow = extracted.workflow
        api_workflow = ui_to_api_format(ui_workflow)

        api_path = target_dir / f"{name}_api.json"
        params_path = target_dir / f"{name}_params.json"

        api_path.write_text(
            _to_json(api_workflow),
            encoding="utf-8",
        )
        params_path.write_text(
            _to_json(_build_params_spec(name, api_workflow)),
            encoding="utf-8",
        )

        return {"api": api_path, "params": params_path}

    @staticmethod
    def _load_backend() -> Any:
        try:
            from comfyui_workflow import WorkflowManager
        except ImportError as exc:
            raise RuntimeError(
                "comfyui_workflow is required for extraction. Install the extractor package."
            ) from exc

        return WorkflowManager()


def _build_params_spec(workflow_id: str, workflow_json: Dict[str, Any]) -> Dict[str, Any]:
    """Build a minimal ParamSpec with no overrides."""
    return {
        "schema_version": "1",
        "workflow_id": workflow_id,
        "workflow_hash": sha256_json(workflow_json),
        "params": [],
    }


def _to_json(payload: Dict[str, Any]) -> str:
    import json

    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
