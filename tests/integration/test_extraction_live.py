"""Live extraction test using comfyui_workflow backend."""

import os
from pathlib import Path

import pytest

from comfy_mcp.extraction.adapter import WorkflowExtractor


def _get_artifact_path() -> str | None:
    return os.getenv("COMFY_MCP_EXTRACT_PATH")


def test_extract_from_artifact_live() -> None:
    """Live test: extract workflow JSON from a real ComfyUI artifact."""
    print("Test: WorkflowExtractor should extract workflow from artifact.")
    path = _get_artifact_path()
    if not path:
        pytest.skip("Set COMFY_MCP_EXTRACT_PATH to run live extraction test.")

    extractor = WorkflowExtractor()
    artifact_path = Path(path)
    outputs = extractor.extract_and_write(
        path,
        output_dir=artifact_path.parent,
        base_name=artifact_path.stem,
    )

    assert outputs["api"].exists(), "Expected API workflow JSON to be written"
    assert outputs["params"].exists(), "Expected params JSON to be written"
