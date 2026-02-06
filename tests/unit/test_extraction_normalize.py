"""Tests for extraction normalization helpers."""

from comfy_mcp.extraction.normalize import detect_workflow_format, ui_to_api_format


def test_detect_workflow_format_ui() -> None:
    """Detect UI format workflows based on nodes list."""
    print("Test: detect_workflow_format should return 'ui' for UI workflows.")
    workflow = {"nodes": [{"id": 1, "type": "KSampler"}], "links": []}
    assert detect_workflow_format(workflow) == "ui"


def test_ui_to_api_format_minimal() -> None:
    """Convert UI workflow to minimal API format."""
    print("Test: ui_to_api_format should convert nodes to API dict.")
    workflow = {"nodes": [{"id": 1, "type": "KSampler", "widgets_values": [1, 2]}]}
    api = ui_to_api_format(workflow)
    assert api["1"]["class_type"] == "KSampler"
    assert api["1"]["inputs"]["values"] == [1, 2]
