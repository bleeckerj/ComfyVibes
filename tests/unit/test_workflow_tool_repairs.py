from __future__ import annotations

import pytest

from comfy_mcp.tui_client.workflow_tool_repairs import WorkflowToolRepairService


@pytest.mark.asyncio
async def test_prepare_workflows_run_repairs_aspect_ratio_custom_mode_payload() -> None:
    service = WorkflowToolRepairService()
    arguments = {
        "workflow_id": "aspect_ratio_adjustment",
        "overrides": {
            "image": "/tmp/source.png",
            "custom_ratio": True,
            "custom_aspect_ratio": "4 : 5",
            "denoise": 0.8,
        },
    }

    repaired = await service.prepare_workflows_run_arguments(
        orchestrator=object(),
        tool_name="workflows_run",
        arguments=arguments,
        user_text="ASPECT RATIO FLOW REQUEST",
    )

    assert repaired["workflow_id"] == "aspect_ratio_adjustment"
    assert repaired["overrides"]["aspect_ratio"] == "4:5"
    assert repaired["overrides"]["custom_ratio"] is True
    assert repaired["overrides"]["custom_aspect_ratio"] == "4:5"
    assert repaired["overrides"]["denoise"] == 0.8


@pytest.mark.asyncio
async def test_prepare_workflows_run_repairs_aspect_ratio_alias_fields_and_infers_workflow() -> None:
    service = WorkflowToolRepairService()
    arguments = {
        "overrides": {
            "image": "/tmp/source.png",
            "customRatio": "true",
            "customAspectRatio": "3 x 2",
        }
    }

    repaired = await service.prepare_workflows_run_arguments(
        orchestrator=object(),
        tool_name="workflows_run",
        arguments=arguments,
        user_text="ASPECT RATIO FLOW REQUEST\nWorkflow preference: aspect_ratio_adjustment",
    )

    assert repaired["workflow_id"] == "aspect_ratio_adjustment"
    assert repaired["overrides"]["aspect_ratio"] == "3:2"
    assert repaired["overrides"]["custom_ratio"] is True
    assert repaired["overrides"]["custom_aspect_ratio"] == "3:2"
    assert "customRatio" not in repaired["overrides"]
    assert "customAspectRatio" not in repaired["overrides"]


@pytest.mark.asyncio
async def test_prepare_workflows_run_does_not_apply_aspect_repairs_to_other_workflows() -> None:
    service = WorkflowToolRepairService()
    arguments = {
        "workflow_id": "image_edit",
        "overrides": {
            "custom_ratio": True,
            "custom_aspect_ratio": "4:5",
        },
    }

    repaired = await service.prepare_workflows_run_arguments(
        orchestrator=object(),
        tool_name="workflows_run",
        arguments=arguments,
        user_text="regular image edit request",
    )

    assert repaired["workflow_id"] == "image_edit"
    assert repaired["overrides"] == arguments["overrides"]
    assert "aspect_ratio" not in repaired["overrides"]


@pytest.mark.asyncio
async def test_prepare_workflows_run_backfills_custom_aspect_from_plain_aspect_ratio() -> None:
    service = WorkflowToolRepairService()
    arguments = {
        "workflow_id": "aspect_ratio_adjustment",
        "overrides": {
            "aspect_ratio": "16 : 9",
            "custom_ratio": True,
            "image": "/tmp/source.png",
        },
    }

    repaired = await service.prepare_workflows_run_arguments(
        orchestrator=object(),
        tool_name="workflows_run",
        arguments=arguments,
        user_text="ASPECT RATIO FLOW REQUEST",
    )

    assert repaired["overrides"]["aspect_ratio"] == "16:9"
    assert repaired["overrides"]["custom_ratio"] is True
    assert repaired["overrides"]["custom_aspect_ratio"] == "16:9"
