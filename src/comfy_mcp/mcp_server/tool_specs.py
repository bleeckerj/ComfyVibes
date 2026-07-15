"""Ordered MCP tool specification definitions."""

from __future__ import annotations

from typing import Any, Dict


def tool_schema(properties: Dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


def build_tool_specs(types: Any) -> list[Any]:
    return [
        types.Tool(name="comfy_nodes_list", description="Return ComfyUI node catalog.", inputSchema=tool_schema()),
        types.Tool(name="comfy_queue_get", description="Return ComfyUI queue state.", inputSchema=tool_schema()),
        types.Tool(
            name="comfy_server_info",
            description="Return ComfyUI target diagnostics (configured URL, resolved IPs, and queue probe status).",
            inputSchema=tool_schema({"probe_queue": {"type": "boolean"}, "resolve_dns": {"type": "boolean"}}, []),
        ),
        types.Tool(name="comfy_history_get", description="Return ComfyUI history data.", inputSchema=tool_schema({"prompt_id": {"type": "string"}}, [])),
        types.Tool(name="comfy_models_list", description="Return available model folders.", inputSchema=tool_schema()),
        types.Tool(name="comfy_models_get", description="Return available model files for a folder.", inputSchema=tool_schema({"folder": {"type": "string"}}, ["folder"])),
        types.Tool(name="comfy_embeddings_list", description="Return available embedding names.", inputSchema=tool_schema()),
        types.Tool(
            name="comfy_capabilities_get",
            description="Audit optional ComfyUI and Manager capabilities with partial available, unsupported, or unreachable results.",
            inputSchema=tool_schema({"refresh": {"type": "boolean"}, "probe_queue": {"type": "boolean"}, "resolve_dns": {"type": "boolean"}}, []),
        ),
        types.Tool(
            name="comfy_jobs_list",
            description="List remote ComfyUI jobs with optional status, workflow, sorting, and pagination filters.",
            inputSchema=tool_schema(
                {
                    "status": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                    "workflow_id": {"type": "string"},
                    "sort_by": {"type": "string"},
                    "sort_order": {"type": "string"},
                    "limit": {"type": "integer"},
                    "offset": {"type": "integer"},
                },
                [],
            ),
        ),
        types.Tool(name="comfy_job_get", description="Return one remote ComfyUI job.", inputSchema=tool_schema({"job_id": {"type": "string"}}, ["job_id"])),
        types.Tool(name="comfy_workflow_templates_list", description="List workflow templates exposed by remote custom nodes.", inputSchema=tool_schema()),
        types.Tool(
            name="comfy_workflow_template_get",
            description="Return one remote workflow template by pack and filename.",
            inputSchema=tool_schema({"pack": {"type": "string"}, "filename": {"type": "string"}}, ["pack", "filename"]),
        ),
        types.Tool(name="comfy_global_subgraphs_list", description="List global subgraph metadata from ComfyUI.", inputSchema=tool_schema()),
        types.Tool(name="comfy_global_subgraph_get", description="Return one global subgraph by id.", inputSchema=tool_schema({"subgraph_id": {"type": "string"}}, ["subgraph_id"])),
        types.Tool(name="comfy_node_replacements_get", description="Return optional node replacement metadata.", inputSchema=tool_schema()),
        types.Tool(
            name="comfy_assets_list",
            description="List remote ComfyUI assets with tag, metadata, sorting, and pagination filters.",
            inputSchema=tool_schema(
                {
                    "include_tags": {"type": "array", "items": {"type": "string"}},
                    "exclude_tags": {"type": "array", "items": {"type": "string"}},
                    "name_contains": {"type": "string"},
                    "metadata_filter": {"type": "object"},
                    "limit": {"type": "integer"},
                    "offset": {"type": "integer"},
                    "sort": {"type": "string"},
                    "order": {"type": "string"},
                },
                [],
            ),
        ),
        types.Tool(name="comfy_asset_get", description="Return one remote ComfyUI asset by id.", inputSchema=tool_schema({"asset_id": {"type": "string"}}, ["asset_id"])),
        types.Tool(
            name="comfy_tags_list",
            description="List remote ComfyUI asset tags.",
            inputSchema=tool_schema({"prefix": {"type": "string"}, "limit": {"type": "integer"}, "offset": {"type": "integer"}, "order": {"type": "string"}, "include_zero": {"type": "boolean"}}, []),
        ),
        types.Tool(
            name="comfy_nodes_search",
            description="Search remote /object_info node metadata by query, class, category, input, and output filters.",
            inputSchema=tool_schema(
                {
                    "query": {"type": "string"},
                    "class_type": {"type": "string"},
                    "category": {"type": "string"},
                    "input_name": {"type": "string"},
                    "output_type": {"type": "string"},
                    "limit": {"type": "integer"},
                    "refresh": {"type": "boolean"},
                },
                [],
            ),
        ),
        types.Tool(
            name="comfy_queue_interrupt",
            description="Interrupt remote ComfyUI execution after confirmation and policy checks.",
            inputSchema=tool_schema({"prompt_id": {"type": "string"}, "confirm": {"type": "boolean"}, "token": {"type": "string"}}, []),
        ),
        types.Tool(
            name="comfy_queue_delete",
            description="Delete selected or all pending remote queue entries after confirmation and policy checks.",
            inputSchema=tool_schema({"clear_all": {"type": "boolean"}, "prompt_ids": {"type": "array", "items": {"type": "string"}}, "confirm": {"type": "boolean"}, "token": {"type": "string"}}, []),
        ),
        types.Tool(
            name="comfy_memory_free",
            description="Release remote ComfyUI memory and optionally unload models after confirmation and policy checks.",
            inputSchema=tool_schema({"unload_models": {"type": "boolean"}, "free_memory": {"type": "boolean"}, "confirm": {"type": "boolean"}, "token": {"type": "string"}}, []),
        ),
        types.Tool(
            name="comfy_history_delete",
            description="Delete selected or all remote history entries after confirmation and policy checks.",
            inputSchema=tool_schema({"clear_all": {"type": "boolean"}, "prompt_ids": {"type": "array", "items": {"type": "string"}}, "confirm": {"type": "boolean"}, "token": {"type": "string"}}, []),
        ),
        types.Tool(
            name="comfy_settings_get",
            description="Read all remote ComfyUI settings or one setting by id.",
            inputSchema=tool_schema({"setting_id": {"type": "string"}}, []),
        ),
        types.Tool(
            name="comfy_settings_set",
            description="Set remote ComfyUI settings after confirmation and policy checks.",
            inputSchema=tool_schema({"settings": {"type": "object"}, "setting_id": {"type": "string"}, "value": {}, "confirm": {"type": "boolean"}, "token": {"type": "string"}}, []),
        ),
        types.Tool(
            name="comfy_upload_image",
            description="Upload an image file into ComfyUI storage.",
            inputSchema=tool_schema(
                {"file_path": {"type": "string"}, "image_type": {"type": "string"}, "subfolder": {"type": "string"}, "overwrite": {"type": "boolean"}, "confirm": {"type": "boolean"}, "token": {"type": "string"}},
                ["file_path"],
            ),
        ),
        types.Tool(
            name="comfy_upload_mask",
            description="Upload an alpha mask file into ComfyUI storage after confirmation and policy checks.",
            inputSchema=tool_schema(
                {"file_path": {"type": "string"}, "original_ref": {"type": "object"}, "image_type": {"type": "string"}, "subfolder": {"type": "string"}, "overwrite": {"type": "boolean"}, "confirm": {"type": "boolean"}, "token": {"type": "string"}},
                ["file_path", "original_ref"],
            ),
        ),
        types.Tool(
            name="comfy_upload_asset",
            description="Upload an asset file with tags into remote ComfyUI storage after confirmation and policy checks.",
            inputSchema=tool_schema(
                {"file_path": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}, "name": {"type": "string"}, "user_metadata": {"type": "object"}, "content_hash": {"type": "string"}, "confirm": {"type": "boolean"}, "token": {"type": "string"}},
                ["file_path", "tags"],
            ),
        ),
        types.Tool(
            name="comfy_download_image",
            description="Download an image from ComfyUI's output/input storage and save it locally. Use this after a workflow completes to retrieve the generated image. Returns the local file path.",
            inputSchema=tool_schema(
                {
                    "filename": {"type": "string", "description": "The filename on the ComfyUI server (e.g. from output_images[].filename)."},
                    "image_type": {"type": "string", "description": "Storage type: 'output', 'input', or 'temp'. Default: 'output'."},
                    "subfolder": {"type": "string", "description": "Subfolder within the storage type, if any."},
                    "save_path": {"type": "string", "description": "Local directory or file path to save to. Default: /tmp/<filename>."},
                },
                ["filename"],
            ),
        ),
        types.Tool(name="list_tools", description="Return tool definitions for this MCP server. Supports optional filtering by exact name or prefix.", inputSchema=tool_schema({"name": {"type": "string"}, "prefix": {"type": "string"}, "limit": {"type": "integer"}, "include_schema": {"type": "boolean"}}, [])),
        types.Tool(name="tool_schema_get", description="Return one tool definition and input schema by tool name.", inputSchema=tool_schema({"name": {"type": "string"}}, ["name"])),
        types.Tool(name="comfy_manager_status", description="Return remote ComfyUI Manager v4 availability and queue status.", inputSchema=tool_schema({"refresh": {"type": "boolean"}}, [])),
        types.Tool(name="comfy_manager_packs_list", description="List installed ComfyUI Manager node packs.", inputSchema=tool_schema({"mode": {"type": "string"}, "refresh": {"type": "boolean"}}, [])),
        types.Tool(
            name="comfy_manager_packs_search",
            description="Search installed ComfyUI Manager node packs and optional node mappings.",
            inputSchema=tool_schema({"query": {"type": "string"}, "category": {"type": "string"}, "node_filter": {"type": "string"}, "installed_only": {"type": "boolean"}, "max_results": {"type": "integer"}, "refresh": {"type": "boolean"}}, []),
        ),
        types.Tool(name="comfy_manager_node_mappings_get", description="Return Manager node-to-pack mappings.", inputSchema=tool_schema({"mode": {"type": "string"}, "refresh": {"type": "boolean"}}, [])),
        types.Tool(name="comfy_manager_snapshots_list", description="List ComfyUI Manager snapshots.", inputSchema=tool_schema({"refresh": {"type": "boolean"}}, [])),
        types.Tool(
            name="comfy_manager_models_search",
            description="Search external models through ComfyUI Manager.",
            inputSchema=tool_schema({"query": {"type": "string"}, "base_filter": {"type": "string"}, "type_filter": {"type": "string"}, "installed_only": {"type": "boolean"}, "uninstalled_only": {"type": "boolean"}, "max_results": {"type": "integer"}, "mode": {"type": "string"}, "refresh": {"type": "boolean"}}, []),
        ),
        types.Tool(name="comfy_manager_queue_status", description="Return ComfyUI Manager queue status.", inputSchema=tool_schema({"client_id": {"type": "string"}, "refresh": {"type": "boolean"}}, [])),
        types.Tool(
            name="comfy_manager_operation",
            description="Queue one constrained ComfyUI Manager operation after confirmation and policy checks.",
            inputSchema=tool_schema(
                {"operation": {"type": "string", "enum": ["install_node_pack", "update_node_pack", "uninstall_node_pack", "disable_node_pack", "install_model", "update_comfyui", "update_all"]}, "payload": {"type": "object"}, "client_id": {"type": "string"}, "ui_id": {"type": "string"}, "start_queue": {"type": "boolean"}, "confirm": {"type": "boolean"}, "token": {"type": "string"}},
                ["operation"],
            ),
        ),
        types.Tool(name="workflows_list", description="List workflows in the store, including source root and params count.", inputSchema=tool_schema()),
        types.Tool(name="workflows_get", description="Return workflow JSON and metadata.", inputSchema=tool_schema({"workflow_id": {"type": "string"}}, ["workflow_id"])),
        types.Tool(name="workflows_search", description="Search workflows by id, name, description, or tags.", inputSchema=tool_schema({"query": {"type": "string"}, "limit": {"type": "integer"}, "tags": {"type": "array", "items": {"type": "string"}}}, ["query"])),
        types.Tool(name="workflows_capabilities_list", description="List workflow capability cards (name, tags, required params, and optional heuristics).", inputSchema=tool_schema({"query": {"type": "string"}, "limit": {"type": "integer"}, "tags": {"type": "array", "items": {"type": "string"}}, "include_params": {"type": "boolean"}}, [])),
        types.Tool(name="workflows_capabilities_get", description="Return one workflow capability card by workflow id.", inputSchema=tool_schema({"workflow_id": {"type": "string"}, "include_params": {"type": "boolean"}}, ["workflow_id"])),
        types.Tool(name="workflows_params_get", description="Return params.json for a workflow id.", inputSchema=tool_schema({"workflow_id": {"type": "string"}}, ["workflow_id"])),
        types.Tool(name="workflows_package", description="Regenerate params.json and metadata capability fields for one workflow.", inputSchema=tool_schema({"workflow_id": {"type": "string"}, "hints": {"type": "object"}, "include_suggestions": {"type": "boolean"}, "token": {"type": "string"}}, ["workflow_id"])),
        types.Tool(name="workflows_recompile", description="Apply workflow input edits and/or params overrides by workflow id, then regenerate and persist synchronized workflow.json + meta.json + params.json artifacts.", inputSchema=tool_schema({"workflow_id": {"type": "string"}, "workflow_input_updates": {"type": "array", "items": {"type": "object"}}, "param_overrides": {"type": "array", "items": {"type": "object"}}, "hints": {"type": "object"}, "include_suggestions": {"type": "boolean"}, "token": {"type": "string"}}, ["workflow_id"])),
        types.Tool(name="workflows_package_many", description="Regenerate params.json and metadata capability fields for many workflows (or all when workflow_ids omitted).", inputSchema=tool_schema({"workflow_ids": {"type": "array", "items": {"type": "string"}}, "hints_by_workflow": {"type": "object"}, "include_suggestions": {"type": "boolean"}, "token": {"type": "string"}}, [])),
        types.Tool(name="workflows_package_template_get", description="Return an editable metadata-hints template for one workflow.", inputSchema=tool_schema({"workflow_id": {"type": "string"}}, ["workflow_id"])),
        types.Tool(name="workflows_folder_create", description="Create a folder under the primary workflows directory.", inputSchema=tool_schema({"folder_path": {"type": "string"}, "token": {"type": "string"}}, ["folder_path"])),
        types.Tool(name="workflows_file_write", description="Write a JSON file under the primary workflows directory.", inputSchema=tool_schema({"file_path": {"type": "string"}, "payload": {"type": "object"}, "overwrite": {"type": "boolean"}, "token": {"type": "string"}}, ["file_path", "payload"])),
        types.Tool(name="workflows_file_edit", description="Edit a JSON file under the primary workflows directory by merging top-level keys.", inputSchema=tool_schema({"file_path": {"type": "string"}, "updates": {"type": "object"}, "token": {"type": "string"}}, ["file_path", "updates"])),
        types.Tool(name="workflows_file_read", description="Read a text file under the primary workflows directory.", inputSchema=tool_schema({"file_path": {"type": "string"}, "encoding": {"type": "string"}}, ["file_path"])),
        types.Tool(name="workflows_file_copy", description="Copy a file under the primary workflows directory.", inputSchema=tool_schema({"source_path": {"type": "string"}, "destination_path": {"type": "string"}, "overwrite": {"type": "boolean"}, "token": {"type": "string"}}, ["source_path", "destination_path"])),
        types.Tool(name="workflows_file_delete", description="Delete a file under the primary workflows directory.", inputSchema=tool_schema({"file_path": {"type": "string"}, "token": {"type": "string"}}, ["file_path"])),
        types.Tool(name="workflows_save", description="Save a workflow entry and always regenerate packaged meta.json + params.json.", inputSchema=tool_schema({"workflow_id": {"type": "string"}, "workflow_json": {"type": "object"}, "meta": {"type": "object"}, "token": {"type": "string"}}, ["workflow_id", "workflow_json"])),
        types.Tool(name="workflows_delete", description="Delete a workflow entry from the store.", inputSchema=tool_schema({"workflow_id": {"type": "string"}, "token": {"type": "string"}}, ["workflow_id"])),
        types.Tool(name="workflows_run", description="Run a workflow with parameter overrides via ComfyUI. Requires explicit overrides object; for LoadImage.image, pass local file path or Comfy input filename.", inputSchema=tool_schema({"workflow_id": {"type": "string"}, "overrides": {"type": "object"}, "client_id": {"type": "string"}, "token": {"type": "string"}, "force": {"type": "boolean"}, "wait_timeout_s": {"type": "number"}, "wait_poll_ms": {"type": "integer"}}, ["workflow_id", "overrides"])),
        types.Tool(name="workflows_wait", description="Wait for a prompt to appear in history.", inputSchema=tool_schema({"prompt_id": {"type": "string"}, "timeout_s": {"type": "number"}, "poll_ms": {"type": "integer"}}, ["prompt_id"])),
        types.Tool(name="workflows_image_info", description="Inspect a local image file and return width/height/aspect-ratio details.", inputSchema=tool_schema({"file_path": {"type": "string"}}, ["file_path"])),
        types.Tool(name="workflows_watch", description="Watch workflow execution status/progress until terminal state.", inputSchema=tool_schema({"prompt_id": {"type": "string"}, "inactivity_timeout_s": {"type": "number"}, "max_wait_s": {"type": "number"}, "poll_ms": {"type": "integer"}, "include_history": {"type": "boolean"}}, ["prompt_id"])),
        types.Tool(name="workflows_status", description="Get current workflow queue status and optional progress snapshot.", inputSchema=tool_schema({"prompt_id": {"type": "string"}, "include_progress": {"type": "boolean"}, "progress_timeout_s": {"type": "number"}}, [])),
        types.Tool(name="workflows_extract_from_artifact", description="Extract workflow from an image or video artifact.", inputSchema=tool_schema({"path": {"type": "string"}, "preserve_format": {"type": "boolean"}}, ["path"])),
        types.Tool(name="workflows_extract_from_photarium", description="Extract embedded ComfyUI workflow metadata for a Photarium image id. Uses Photarium extras when available; otherwise downloads the original artifact (not derived JPEG variants) and extracts locally.", inputSchema=tool_schema({"image_id": {"type": "string"}, "photarium_mcp_url": {"type": "string"}, "namespace": {"type": "string"}, "prefer_prompt": {"type": "boolean"}, "include_raw_metadata": {"type": "boolean"}, "preserve_format": {"type": "boolean"}}, ["image_id"])),
        types.Tool(name="workflows_import_from_artifact", description="Extract workflow from artifact and save to the store.", inputSchema=tool_schema({"path": {"type": "string"}, "workflow_id": {"type": "string"}, "name": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}, "token": {"type": "string"}}, ["path", "workflow_id"])),
        types.Tool(name="workflows_import_from_photarium", description="Extract workflow metadata from a Photarium image and save it as a reusable workflow entry.", inputSchema=tool_schema({"image_id": {"type": "string"}, "workflow_id": {"type": "string"}, "photarium_mcp_url": {"type": "string"}, "namespace": {"type": "string"}, "name": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}, "hints": {"type": "object"}, "include_suggestions": {"type": "boolean"}, "prefer_prompt": {"type": "boolean"}, "include_raw_metadata": {"type": "boolean"}, "token": {"type": "string"}}, ["image_id", "workflow_id"])),
        types.Tool(name="workflows_run_from_source", description="Resolve an image-like source, extract or reuse its workflow lineage, and run the packaged workflow snapshot. Returns needs_input with a resumable token when required runtime image bindings are missing.", inputSchema=tool_schema({"source": {"type": "string"}, "source_kind": {"type": "string"}, "overrides": {"type": "object"}, "resume_token": {"type": "string"}, "namespace": {"type": "string"}, "photarium_mcp_url": {"type": "string"}, "wait_timeout_s": {"type": "number"}, "wait_poll_ms": {"type": "integer"}, "force": {"type": "boolean"}, "token": {"type": "string"}}, [])),
        types.Tool(name="workflows_lineage_register_results", description="Attach uploaded Photarium result image ids to a runtime workflow lineage record for later reuse by result id.", inputSchema=tool_schema({"lineage_run_id": {"type": "string"}, "results": {"type": "array", "items": {"type": "object"}}, "token": {"type": "string"}}, ["lineage_run_id", "results"])),
        types.Tool(name="workflows_lineage_get", description="Return one runtime workflow lineage record by lineage_run_id or by resolving a source/result image id through lineage indexes.", inputSchema=tool_schema({"lineage_run_id": {"type": "string"}, "image_id": {"type": "string"}}, [])),
        types.Tool(name="workflows_run_aspect_ratio_adjustment", description="Upload an image, set aspect ratio, and run the aspect ratio adjustment workflow.", inputSchema=tool_schema({"image_path": {"type": "string"}, "aspect_ratio": {"type": "string"}, "workflow_id": {"type": "string"}, "positive_prompt": {"type": "string"}, "negative_prompt": {"type": "string"}, "seed": {"type": "integer"}, "output_base_name": {"type": "string"}, "client_id": {"type": "string"}, "token": {"type": "string"}, "upload_subfolder": {"type": "string"}, "overwrite": {"type": "boolean"}, "wait_timeout_s": {"type": "number"}, "wait_poll_ms": {"type": "integer"}}, ["image_path", "aspect_ratio"])),
    ]
