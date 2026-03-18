from __future__ import annotations

from comfy_mcp.tui_client.search_semantics import (
    normalize_binary_transfer_arguments,
    normalize_photarium_upload_arguments,
    normalize_search_tool_result,
)


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


def test_normalize_photarium_upload_arguments_sets_camelcase_name_from_description() -> None:
    schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
        },
    }
    args = {
        "url": "http://127.0.0.1:8188/view?filename=ComfyUI_00123_.png&type=output",
        "description": "editorial leather jacket campaign image",
    }

    normalized = normalize_photarium_upload_arguments("photarium_upload_url", args, schema)

    assert normalized["name"] == "EditorialLeatherJacketCampaign"


def test_normalize_photarium_upload_arguments_does_not_use_view_url_filename_for_name() -> None:
    schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "title": {"type": "string"},
        },
    }
    args = {
        "url": (
            "http://127.0.0.1:8188/view?"
            "filename=NeonCityStreetRainReflections__4x5__s7__a1b2c3d4_00001_.png&type=output"
        )
    }

    normalized = normalize_photarium_upload_arguments("photarium_upload_url", args, schema)

    assert normalized["title"] == "NeonCityStreetRainReflections"


def test_normalize_photarium_upload_arguments_sanitizes_existing_non_blob_name() -> None:
    args = {
        "name": "  my cool-image!!! (v2)  ",
        "description": "shiny chrome robot portrait",
    }
    schema = {"type": "object", "properties": {"name": {"type": "string"}, "description": {"type": "string"}}}

    normalized = normalize_photarium_upload_arguments("photarium_upload_from_path", args, schema)

    assert normalized["name"] == "MyCoolImageV2"


def test_normalize_photarium_upload_arguments_does_not_inject_unknown_name_field() -> None:
    args = {"url": "http://127.0.0.1:8188/view?filename=DesertEditorialScene.png&type=output"}
    schema = {"type": "object", "properties": {"url": {"type": "string"}}}

    normalized = normalize_photarium_upload_arguments("photarium_upload_url", args, schema)

    assert "name" not in normalized
    assert "title" not in normalized


def test_normalize_photarium_upload_arguments_sanitizes_query_blob_name_value() -> None:
    args = {
        "name": "view_filename=edited_transl.png&type=output&subfolder=2026-02-20",
    }
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}

    normalized = normalize_photarium_upload_arguments("photarium_upload_from_path", args, schema)

    assert normalized["name"] == "EditedTransl"


def test_normalize_photarium_upload_arguments_can_use_fallback_text_when_no_semantic_fields() -> None:
    args = {"name": "type=output&subfolder=2026-02-20"}
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}

    normalized = normalize_photarium_upload_arguments(
        "photarium_upload_from_path",
        args,
        schema,
        fallback_text="editorial portrait in neon rain",
    )

    assert normalized["name"] == "EditorialPortraitNeonRain"


def test_normalize_photarium_upload_arguments_rewrites_blob_in_immutable_filename_field() -> None:
    args = {"immutable_filename": "view_filename=futuristic_of.png&type=output&subfolder=2026-02-20"}
    schema = {"type": "object", "properties": {"immutable_filename": {"type": "string"}}}

    normalized = normalize_photarium_upload_arguments("photarium_upload_from_path", args, schema)

    assert normalized["immutable_filename"] == "FuturisticOf"


def test_normalize_photarium_upload_arguments_populates_name_and_immutable_when_schema_supports_both() -> None:
    schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "name": {"type": "string"},
            "immutable_filename": {"type": "string"},
        },
    }
    args = {
        "url": "http://127.0.0.1:8188/view?filename=CleanBlueVehicle_00001_.png&type=output&subfolder=2026-02-20"
    }

    normalized = normalize_photarium_upload_arguments("photarium_upload_url", args, schema)

    assert normalized["name"] == "CleanBlueVehicle"
    assert normalized["immutable_filename"] == "CleanBlueVehicle"


def test_normalize_photarium_upload_arguments_overwrites_existing_immutable_filename() -> None:
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "immutable_filename": {"type": "string"},
            "description": {"type": "string"},
        },
    }
    args = {
        "name": "ValidDisplayName",
        "immutable_filename": "legacy_bad_value",
        "description": "electric blue vehicle concept",
    }

    normalized = normalize_photarium_upload_arguments("photarium_upload_from_path", args, schema)

    assert normalized["name"] == "ValidDisplayName"
    assert normalized["immutable_filename"] == "ValidDisplayName"


def test_normalize_photarium_upload_arguments_strips_non_semantic_generation_tags() -> None:
    args = {
        "name": "EditorialPortrait",
        "tags": ["fashion", "denoise-0.7", "cfg-1", "steps-8", "image-edit", "editorial"],
    }
    schema = {"type": "object", "properties": {"name": {"type": "string"}, "tags": {"type": "array"}}}

    normalized = normalize_photarium_upload_arguments("photarium_upload_from_path", args, schema)

    assert normalized["tags"] == ["fashion", "editorial"]


def test_normalize_photarium_upload_arguments_removes_string_tag_field_when_only_generation_tags_exist() -> None:
    args = {
        "title": "EditorialPortrait",
        "tag_names": "denoise-0.7, seed-1234, workflow",
    }
    schema = {"type": "object", "properties": {"title": {"type": "string"}, "tag_names": {"type": "string"}}}

    normalized = normalize_photarium_upload_arguments("photarium_upload_from_path", args, schema)

    assert "tag_names" not in normalized


def test_normalize_binary_transfer_arguments_disables_import_include_data_by_default() -> None:
    args = {
        "url": "http://127.0.0.1:8188/view?filename=Sample_00001_.png&type=output",
        "includeData": True,
    }

    normalized = normalize_binary_transfer_arguments("photarium_import_url", args, fallback_text="upload this image")

    assert normalized["includeData"] is False


def test_normalize_binary_transfer_arguments_keeps_import_include_data_when_explicitly_requested() -> None:
    args = {
        "url": "http://127.0.0.1:8188/view?filename=Sample_00001_.png&type=output",
        "includeData": True,
    }

    normalized = normalize_binary_transfer_arguments(
        "photarium_import_url",
        args,
        fallback_text="Use includeData=true and return base64 data URL",
    )

    assert normalized["includeData"] is True


def test_normalize_binary_transfer_arguments_disables_download_include_base64_by_default() -> None:
    args = {"imageId": "abc123", "includeBase64": True}

    normalized = normalize_binary_transfer_arguments("photarium_download_image", args, fallback_text="download file")

    assert normalized["includeBase64"] is False
