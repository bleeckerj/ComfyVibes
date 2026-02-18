from __future__ import annotations

from comfy_mcp.tui_client.search_semantics import normalize_search_tool_result


def test_normalize_search_result_prefers_canonical_nested_uuid_over_display_id() -> None:
    payload = {
        "results": [
            {
                "id": "Editorial Sunset",
                "name": "Editorial Sunset",
                "image": {"uuid": "123e4567-e89b-12d3-a456-426614174000"},
            }
        ]
    }

    normalized = normalize_search_tool_result("photarium_search", payload)

    assert normalized["results"][0]["image_id"] == "123e4567-e89b-12d3-a456-426614174000"
    assert normalized["image_ids"] == ["123e4567-e89b-12d3-a456-426614174000"]
    assert normalized["primary_image_id"] == "123e4567-e89b-12d3-a456-426614174000"


def test_normalize_search_result_ignores_display_name_in_id_field() -> None:
    payload = {
        "results": [
            {
                "id": "Moodboard Hero",
                "name": "Moodboard Hero",
            }
        ]
    }

    normalized = normalize_search_tool_result("photarium_search", payload)

    assert "image_ids" not in normalized
    assert "image_id" not in normalized["results"][0]


def test_normalize_search_result_handles_catalog_semantic_search_tool_variant() -> None:
    payload = {
        "matches": [
            {"id": "abc", "imageId": "img_001"},
            {"id": "def", "imageId": "img_002"},
        ]
    }

    normalized = normalize_search_tool_result("catalog_semantic_search", payload)

    assert normalized["image_ids"] == ["img_001", "img_002"]
    assert normalized["primary_image_id"] == "img_001"
    assert normalized["matches"][0]["image_id"] == "img_001"
    assert normalized["matches"][1]["image_id"] == "img_002"
