"""Tests for workflow capability indexing."""

from __future__ import annotations

from pathlib import Path

from comfy_mcp.reasoning.capability_index import CapabilityIndex
from comfy_mcp.workflow_store.store import WorkflowStore


def _seed_workflow(store: WorkflowStore) -> None:
    workflow = {"1": {"class_type": "A", "inputs": {"x": 1}}}
    meta = {
        "id": "image_edit",
        "name": "Image Edit",
        "description": "Prompt-guided image editing.",
        "tags": ["image-edit", "fashion"],
        "use_cases": ["swap fabric material", "restyle clothing"],
        "style": "editorial realism",
    }
    params = {
        "schema_version": "1",
        "workflow_id": "image_edit",
        "workflow_hash": "abc",
        "params": [
            {
                "name": "image_filename",
                "type": "string",
                "required": True,
                "description": "Input image filename",
            },
            {
                "name": "positive_prompt",
                "type": "string",
                "required": False,
                "description": "Edit prompt",
            },
        ],
    }
    store.save_workflow("image_edit", workflow, meta=meta, params=params)


def test_capability_index_list_supports_query_and_tags(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path)
    _seed_workflow(store)
    index = CapabilityIndex(store)

    cards = index.list_cards(query="editorial", tags=["fashion"], include_params=False)

    assert len(cards) == 1
    assert cards[0]["workflow_id"] == "image_edit"
    assert cards[0]["required_params"] == ["image_filename"]
    assert cards[0]["optional_params"] == ["positive_prompt"]
    assert "params" not in cards[0]


def test_capability_index_get_includes_param_details(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path)
    _seed_workflow(store)
    index = CapabilityIndex(store)

    card = index.get_card("image_edit", include_params=True)

    assert card["workflow_id"] == "image_edit"
    assert card["heuristics"]["style"] == "editorial realism"
    assert len(card["params"]) == 2
    assert card["params"][0]["name"] == "image_filename"


def test_capability_index_prioritizes_flux_klein_for_image_edit_intent(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path)
    workflow = {"1": {"class_type": "A", "inputs": {"x": 1}}}

    store.save_workflow(
        "flux_2_klein_4B",
        workflow,
        meta={
            "id": "flux_2_klein_4B",
            "name": "Flux 2 Klein 4B",
            "description": "Edit existing images with prompt-guided controls.",
            "tags": ["image-edit", "img2img"],
        },
        params={
            "schema_version": "1",
            "workflow_id": "flux_2_klein_4B",
            "workflow_hash": "abc",
            "params": [
                {"name": "image", "type": "string", "required": False},
                {"name": "positive_prompt", "type": "string", "required": False},
            ],
        },
    )
    store.save_workflow(
        "image_edit_other",
        workflow,
        meta={
            "id": "image_edit_other",
            "name": "Image Edit Other",
            "description": "Edit images with prompts.",
            "tags": ["image-edit"],
        },
        params={
            "schema_version": "1",
            "workflow_id": "image_edit_other",
            "workflow_hash": "def",
            "params": [
                {"name": "image", "type": "string", "required": False},
                {"name": "prompt", "type": "string", "required": False},
            ],
        },
    )

    index = CapabilityIndex(store)
    cards = index.list_cards(query="edit this image background", include_params=False)

    assert cards
    assert cards[0]["workflow_id"] == "flux_2_klein_4B"
