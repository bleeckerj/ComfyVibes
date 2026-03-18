"""Tool handler registry wiring."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict


def build_handler_registry(
    comfy_tools: Any,
    workflow_tools: Any,
    *,
    list_tools_handler: Callable[..., Dict[str, Any]],
    tool_schema_get_handler: Callable[[str], Dict[str, Any]],
) -> Dict[str, Callable[..., Any] | Callable[..., Awaitable[Any]]]:
    return {
        "comfy_nodes_list": comfy_tools.nodes_list,
        "comfy_queue_get": comfy_tools.queue_get,
        "comfy_server_info": comfy_tools.server_info,
        "comfy_history_get": comfy_tools.history_get,
        "comfy_models_list": comfy_tools.models_list,
        "comfy_models_get": comfy_tools.models_get,
        "comfy_embeddings_list": comfy_tools.embeddings_list,
        "comfy_upload_image": comfy_tools.upload_image,
        "comfy_download_image": comfy_tools.download_image,
        "list_tools": list_tools_handler,
        "tool_schema_get": tool_schema_get_handler,
        "workflows_list": workflow_tools.list,
        "workflows_get": workflow_tools.get,
        "workflows_search": workflow_tools.search,
        "workflows_capabilities_list": workflow_tools.capabilities_list,
        "workflows_capabilities_get": workflow_tools.capabilities_get,
        "workflows_params_get": workflow_tools.params_get,
        "workflows_package": workflow_tools.package,
        "workflows_recompile": workflow_tools.recompile,
        "workflows_package_many": workflow_tools.package_many,
        "workflows_package_template_get": workflow_tools.package_template_get,
        "workflows_folder_create": workflow_tools.folder_create,
        "workflows_file_write": workflow_tools.file_write,
        "workflows_file_edit": workflow_tools.file_edit,
        "workflows_file_read": workflow_tools.file_read,
        "workflows_file_copy": workflow_tools.file_copy,
        "workflows_file_delete": workflow_tools.file_delete,
        "workflows_save": workflow_tools.save,
        "workflows_delete": workflow_tools.delete,
        "workflows_run": workflow_tools.run,
        "workflows_wait": workflow_tools.wait,
        "workflows_image_info": workflow_tools.image_info,
        "workflows_watch": workflow_tools.watch,
        "workflows_status": workflow_tools.status,
        "workflows_extract_from_artifact": workflow_tools.extract_from_artifact,
        "workflows_extract_from_photarium": workflow_tools.extract_from_photarium,
        "workflows_import_from_artifact": workflow_tools.import_from_artifact,
        "workflows_import_from_photarium": workflow_tools.import_from_photarium,
        "workflows_run_aspect_ratio_adjustment": workflow_tools.run_aspect_ratio_adjustment,
        "workflows_run_from_source": workflow_tools.run_from_source,
        "workflows_lineage_register_results": workflow_tools.lineage_register_results,
        "workflows_lineage_get": workflow_tools.lineage_get,
    }
