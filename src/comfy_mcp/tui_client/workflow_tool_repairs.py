"""Workflow-specific tool argument repair and preflight helpers."""

from __future__ import annotations

import json
import random
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict


class WorkflowToolRepairService:
    async def prepare_workflows_run_arguments(
        self,
        orchestrator: Any,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> Dict[str, Any]:
        if tool_name != "workflows_run":
            return arguments
        arguments = self.repair_workflows_run_arguments(arguments)
        arguments = await self.repair_aspect_ratio_workflows_run_arguments(
            orchestrator,
            tool_name,
            arguments,
            user_text=user_text,
        )
        arguments = await self.repair_stitching_workflows_run_arguments(
            orchestrator,
            tool_name,
            arguments,
            user_text=user_text,
        )
        arguments = await self.repair_tanktracks_workflows_run_arguments(
            orchestrator,
            tool_name,
            arguments,
            user_text=user_text,
        )
        arguments = await self.repair_variation_workflows_run_arguments(
            orchestrator,
            tool_name,
            arguments,
            user_text=user_text,
        )
        arguments = await self.repair_missing_overrides_with_defaults(
            orchestrator,
            tool_name,
            arguments,
            user_text=user_text,
        )
        return arguments

    async def preflight_workflows_run_arguments(
        self,
        orchestrator: Any,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> None:
        if tool_name != "workflows_run":
            return
        if not isinstance(arguments, dict):
            raise RuntimeError("workflows_run preflight failed: arguments must be an object")
        overrides = arguments.get("overrides")
        if isinstance(overrides, dict):
            return
        if "overrides" in arguments:
            raise RuntimeError("workflows_run preflight blocked: 'overrides' must be an object.")

        shorthand_image_id = self.extract_shorthand_edit_image_id(user_text)
        verified_hint = ""
        if shorthand_image_id:
            verified = await self.verify_photarium_image_id_candidate(orchestrator, shorthand_image_id)
            if verified:
                verified_hint = (
                    f" The shorthand target '{shorthand_image_id}' appears to be a valid Photarium image ID "
                    "(verified via photarium_get)."
                )

        raise RuntimeError(
            "workflows_run preflight blocked: missing required 'overrides' object."
            f"{verified_hint} Convert shorthand intent into explicit tool args first "
            '(for example: photarium_get -> download image -> workflows_run with '
            '{"workflow_id":"...","overrides":{"image":"<local_path_or_comfy_filename>"}}).'
        )

    @staticmethod
    def repair_workflows_run_arguments(arguments: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(arguments, dict):
            return arguments
        repaired: Dict[str, Any] = dict(arguments)
        wrapper_keys = ("arguments", "args", "input", "payload", "params", "token")
        flattened_wrapper_keys: set[str] = set()
        for wrapper_key in wrapper_keys:
            wrapped = repaired.get(wrapper_key)
            if not isinstance(wrapped, dict):
                continue
            flattened_wrapper_keys.add(wrapper_key)
            for key, value in wrapped.items():
                repaired.setdefault(key, value)
        for wrapper_key in flattened_wrapper_keys:
            repaired.pop(wrapper_key, None)

        aliases = (
            ("workflow", "workflow_id"),
            ("workflowId", "workflow_id"),
            ("id", "workflow_id"),
            ("clientId", "client_id"),
            ("waitTimeoutS", "wait_timeout_s"),
            ("waitPollMs", "wait_poll_ms"),
        )
        for alias, canonical in aliases:
            if canonical not in repaired and alias in repaired:
                repaired[canonical] = repaired[alias]
            if alias != canonical and canonical in repaired:
                repaired.pop(alias, None)

        overrides_value = repaired.get("overrides")
        if isinstance(overrides_value, str):
            parsed: Any = None
            try:
                parsed = json.loads(overrides_value)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                repaired["overrides"] = parsed

        if "workflow_id" not in repaired or "overrides" in repaired:
            return repaired

        control_keys = {"workflow_id", "client_id", "token", "force", "wait_timeout_s", "wait_poll_ms"}
        overrides: Dict[str, Any] = {}
        normalized: Dict[str, Any] = {}
        for key, value in repaired.items():
            if key in control_keys:
                normalized[key] = value
            else:
                overrides[key] = value
        if not overrides:
            return repaired
        normalized["overrides"] = overrides
        return normalized

    async def repair_aspect_ratio_workflows_run_arguments(
        self,
        orchestrator: Any,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> Dict[str, Any]:
        del orchestrator
        if tool_name != "workflows_run" or not isinstance(arguments, dict):
            return arguments
        repaired = dict(arguments)
        workflow_id = str(repaired.get("workflow_id") or "").strip()
        is_aspect_flow_request = "ASPECT RATIO FLOW REQUEST" in str(user_text or "").upper()
        if not workflow_id and is_aspect_flow_request:
            inferred = self.extract_flow_workflow_id(user_text) or "aspect_ratio_adjustment"
            repaired["workflow_id"] = inferred
            workflow_id = inferred
        if workflow_id != "aspect_ratio_adjustment":
            return repaired

        existing_overrides = repaired.get("overrides")
        if not isinstance(existing_overrides, dict):
            return repaired
        repaired_overrides = dict(existing_overrides)

        for alias, canonical in (
            ("aspectRatio", "aspect_ratio"),
            ("customRatio", "custom_ratio"),
            ("customAspectRatio", "custom_aspect_ratio"),
        ):
            if canonical not in repaired_overrides and alias in repaired_overrides:
                repaired_overrides[canonical] = repaired_overrides.get(alias)
            repaired_overrides.pop(alias, None)

        aspect_value = repaired_overrides.get("aspect_ratio")
        custom_aspect_value = repaired_overrides.get("custom_aspect_ratio")
        custom_ratio_value = repaired_overrides.get("custom_ratio")

        normalized_aspect = self.normalize_ratio_token(aspect_value) if isinstance(aspect_value, str) else None
        normalized_custom_aspect = (
            self.normalize_ratio_token(custom_aspect_value) if isinstance(custom_aspect_value, str) else None
        )
        custom_mode_requested = self._is_truthy_custom_ratio(custom_ratio_value) or normalized_custom_aspect is not None

        if normalized_custom_aspect:
            repaired_overrides["custom_aspect_ratio"] = normalized_custom_aspect
        elif custom_mode_requested and normalized_aspect:
            repaired_overrides["custom_aspect_ratio"] = normalized_aspect

        if custom_mode_requested:
            repaired_overrides["custom_ratio"] = True

        if normalized_custom_aspect and not (isinstance(aspect_value, str) and aspect_value.strip()):
            repaired_overrides["aspect_ratio"] = normalized_custom_aspect
        elif normalized_aspect and isinstance(aspect_value, str) and re.fullmatch(r"\d+\s*[:xX]\s*\d+", aspect_value.strip()):
            repaired_overrides["aspect_ratio"] = normalized_aspect

        repaired["overrides"] = repaired_overrides
        return repaired

    async def repair_stitching_workflows_run_arguments(
        self,
        orchestrator: Any,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> Dict[str, Any]:
        if tool_name != "workflows_run" or not isinstance(arguments, dict):
            return arguments
        workflow_id = str(arguments.get("workflow_id") or "").strip()
        if not self.is_stitching_workflow(workflow_id):
            return arguments
        if "overrides" in arguments and not isinstance(arguments.get("overrides"), dict):
            return arguments

        repaired = dict(arguments)
        existing_overrides = repaired.get("overrides")
        repaired_overrides = dict(existing_overrides) if isinstance(existing_overrides, dict) else {}
        alias_map = (
            ("input_image", "image"), ("inputImage", "image"), ("input_image_1", "image"),
            ("inputImage1", "image"), ("image_1", "image"), ("image1", "image"),
            ("left_image", "image"), ("source_image", "image"), ("sourceImage", "image"),
            ("reference_image", "image"), ("reference_image_1", "image"),
            ("input_image_2", "image_2"), ("inputImage2", "image_2"), ("image2", "image_2"),
            ("right_image", "image_2"), ("second_image", "image_2"), ("source_image_2", "image_2"),
            ("sourceImage2", "image_2"), ("reference_image_2", "image_2"),
        )
        for alias, canonical in alias_map:
            canonical_value = repaired_overrides.get(canonical)
            if isinstance(canonical_value, str) and canonical_value.strip():
                continue
            value = repaired_overrides.get(alias)
            if not (isinstance(value, str) and value.strip()):
                value = repaired.get(alias)
            if isinstance(value, str) and value.strip():
                repaired_overrides[canonical] = value.strip()

        stitch_sources = self.extract_stitch_source_image_ids(user_text)
        needs_image = not (isinstance(repaired_overrides.get("image"), str) and repaired_overrides["image"].strip())
        needs_image_2 = (
            self.stitch_workflow_requires_second_image(workflow_id)
            and not (isinstance(repaired_overrides.get("image_2"), str) and repaired_overrides["image_2"].strip())
        )
        if needs_image and stitch_sources:
            local_path = await self.download_variation_source_image(orchestrator, stitch_sources[0])
            if isinstance(local_path, str) and local_path.strip():
                repaired_overrides["image"] = local_path
        if needs_image_2 and len(stitch_sources) >= 2:
            local_path = await self.download_variation_source_image(orchestrator, stitch_sources[1])
            if isinstance(local_path, str) and local_path.strip():
                repaired_overrides["image_2"] = local_path
        for alias, canonical in alias_map:
            repaired.pop(alias, None)
            if canonical in {"image", "image_2"}:
                repaired.pop(canonical, None)
        repaired["overrides"] = repaired_overrides
        if "wait_timeout_s" not in repaired:
            repaired["wait_timeout_s"] = 300
        if "wait_poll_ms" not in repaired:
            repaired["wait_poll_ms"] = 1000
        return repaired

    async def repair_variation_workflows_run_arguments(
        self,
        orchestrator: Any,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> Dict[str, Any]:
        if tool_name != "workflows_run" or not isinstance(arguments, dict):
            return arguments
        workflow_id = str(arguments.get("workflow_id") or "").strip()
        if not self.is_image_variation_workflow(workflow_id):
            return arguments
        existing_overrides = arguments.get("overrides")
        if isinstance(existing_overrides, dict):
            image_value = existing_overrides.get("image")
            if isinstance(image_value, str) and image_value.strip():
                return arguments
        source_image_id = self.extract_variation_source_image_id(user_text)
        if not source_image_id:
            return arguments
        local_source_path = await self.download_variation_source_image(orchestrator, source_image_id)
        if not local_source_path:
            return arguments
        repaired = dict(arguments)
        repaired_overrides = dict(existing_overrides) if isinstance(existing_overrides, dict) else {}
        repaired_overrides["image"] = local_source_path
        repaired["overrides"] = repaired_overrides
        return repaired

    async def repair_tanktracks_workflows_run_arguments(
        self,
        orchestrator: Any,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> Dict[str, Any]:
        if tool_name != "workflows_run" or not isinstance(arguments, dict):
            return arguments
        repaired = dict(arguments)
        workflow_id = str(repaired.get("workflow_id") or "").strip()
        marker_present = "TANK TRACKS FLOW REQUEST" in str(user_text or "").upper()
        if not workflow_id:
            inferred_workflow = self.extract_flow_workflow_id(user_text)
            if inferred_workflow:
                workflow_id = inferred_workflow
                repaired["workflow_id"] = inferred_workflow
        if not workflow_id and self.is_tanktracks_command_text(user_text):
            workflow_id = "add_tank_tracks"
            repaired["workflow_id"] = workflow_id
        if not workflow_id:
            workflow_id = "add_tank_tracks"
            repaired["workflow_id"] = workflow_id
        if workflow_id != "add_tank_tracks" and not marker_present:
            return repaired
        if workflow_id != "add_tank_tracks":
            return repaired
        existing_overrides = repaired.get("overrides")
        repaired_overrides = dict(existing_overrides) if isinstance(existing_overrides, dict) else {}
        image_value = repaired_overrides.get("image")
        if not (isinstance(image_value, str) and image_value.strip()):
            source_image_id = self.extract_flow_source_image_id(user_text)
            if source_image_id:
                local_source_path = await self.download_variation_source_image(orchestrator, source_image_id)
                if local_source_path:
                    repaired_overrides["image"] = local_source_path
        prefix_value = repaired_overrides.get("filename_prefix")
        if not (isinstance(prefix_value, str) and prefix_value.strip()):
            repaired_overrides["filename_prefix"] = f"AddTankTracks_{uuid.uuid4().hex[:8]}"
        repaired["overrides"] = repaired_overrides
        if "wait_timeout_s" not in repaired:
            repaired["wait_timeout_s"] = 300
        if "wait_poll_ms" not in repaired:
            repaired["wait_poll_ms"] = 1000
        return repaired

    async def repair_missing_overrides_with_defaults(
        self,
        orchestrator: Any,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> Dict[str, Any]:
        if tool_name != "workflows_run" or not isinstance(arguments, dict):
            return arguments
        if "overrides" in arguments:
            return arguments
        workflow_id = str(arguments.get("workflow_id") or "").strip()
        if not workflow_id:
            return arguments

        param_names = await self.fetch_workflow_param_names(orchestrator, workflow_id)
        # Keep strict behavior for image-driven workflows where an input image path is required.
        if self.workflow_likely_requires_image_input(param_names):
            return arguments

        repaired = dict(arguments)
        overrides: Dict[str, Any] = {}

        prompt_key = self.select_prompt_override_key(param_names)
        prompt_value = self.extract_prompt_from_user_text(user_text)
        if prompt_key and isinstance(prompt_value, str) and prompt_value.strip():
            overrides[prompt_key] = prompt_value.strip()

        seed_key = self.select_seed_override_key(param_names)
        if seed_key:
            overrides[seed_key] = self.generate_random_seed()

        filename_prefix_key = self.select_filename_prefix_override_key(param_names)
        if filename_prefix_key:
            overrides[filename_prefix_key] = self.default_filename_prefix(workflow_id)

        repaired["overrides"] = overrides
        return repaired

    async def fetch_workflow_param_names(self, orchestrator: Any, workflow_id: str) -> list[str]:
        if "workflows_params_get" not in orchestrator._tool_input_schema_by_name:
            return []
        schema = orchestrator._tool_input_schema_by_name.get("workflows_params_get")
        key = self.select_workflow_id_key(schema)
        if not key:
            return []
        payload = {key: workflow_id}
        try:
            async with orchestrator._tool_execution_lock:
                result = await orchestrator._router.call_tool("workflows_params_get", payload)
        except Exception:
            return []
        return self.extract_param_names(result)

    @staticmethod
    def select_workflow_id_key(schema: Dict[str, Any] | None) -> str | None:
        if not isinstance(schema, dict):
            return "workflow_id"
        props = schema.get("properties")
        if not isinstance(props, dict):
            return "workflow_id"
        for key in ("workflow_id", "workflowId", "id", "workflow"):
            if key in props:
                return key
        return "workflow_id"

    @staticmethod
    def extract_param_names(payload: Any) -> list[str]:
        if not isinstance(payload, dict):
            return []
        params_obj = payload.get("params")
        if isinstance(params_obj, dict):
            params_list = params_obj.get("params")
            if isinstance(params_list, list):
                return WorkflowToolRepairService.extract_param_names_from_list(params_list)
            if isinstance(params_obj.get("required"), list):
                return [str(item).strip() for item in params_obj.get("required", []) if str(item).strip()]
            return [str(key).strip() for key in params_obj.keys() if str(key).strip()]
        if isinstance(params_obj, list):
            return WorkflowToolRepairService.extract_param_names_from_list(params_obj)
        return []

    @staticmethod
    def extract_param_names_from_list(items: list[Any]) -> list[str]:
        names: list[str] = []
        for item in items:
            if isinstance(item, dict):
                value = item.get("name")
                if isinstance(value, str) and value.strip():
                    names.append(value.strip())
                    continue
            if isinstance(item, str) and item.strip():
                names.append(item.strip())
        return names

    @staticmethod
    def workflow_likely_requires_image_input(param_names: list[str]) -> bool:
        lowered = {name.strip().lower() for name in param_names if isinstance(name, str)}
        if not lowered:
            return True
        image_keys = {
            "image",
            "image_2",
            "input_image",
            "input_image_1",
            "input_image_2",
            "source_image",
            "reference_image",
            "mask_image",
        }
        if lowered.intersection(image_keys):
            return True
        return any(name.endswith("_image") or name.startswith("image_") for name in lowered)

    @staticmethod
    def select_prompt_override_key(param_names: list[str]) -> str | None:
        lowered_map = {name.strip().lower(): name for name in param_names if isinstance(name, str) and name.strip()}
        for preferred in ("prompt", "positive_prompt", "text_prompt"):
            if preferred in lowered_map:
                return lowered_map[preferred]
        return None

    @staticmethod
    def select_seed_override_key(param_names: list[str]) -> str | None:
        lowered_map = {name.strip().lower(): name for name in param_names if isinstance(name, str) and name.strip()}
        for preferred in ("seed", "noise_seed", "random_seed"):
            if preferred in lowered_map:
                return lowered_map[preferred]
        return None

    @staticmethod
    def select_filename_prefix_override_key(param_names: list[str]) -> str | None:
        lowered_map = {name.strip().lower(): name for name in param_names if isinstance(name, str) and name.strip()}
        for preferred in ("filename_prefix", "output_base_name"):
            if preferred in lowered_map:
                return lowered_map[preferred]
        return None

    @staticmethod
    def generate_random_seed() -> int:
        return random.SystemRandom().randint(1, 2_147_483_647)

    @staticmethod
    def default_filename_prefix(workflow_id: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9]+", "_", str(workflow_id or "")).strip("_") or "WorkflowRun"
        return f"{slug}_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def normalize_ratio_token(value: str | None) -> str | None:
        if not isinstance(value, str):
            return None
        match = re.search(r"(\d+)\s*[:xX]\s*(\d+)", value)
        if not match:
            return None
        width = int(match.group(1))
        height = int(match.group(2))
        if width <= 0 or height <= 0:
            return None
        return f"{width}:{height}"

    @staticmethod
    def _is_truthy_custom_ratio(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return False

    @staticmethod
    def extract_prompt_from_user_text(user_text: str) -> str | None:
        if not isinstance(user_text, str):
            return None
        patterns = (
            r'\b(?:using\s+)?prompt\s*[:=]\s*"([^"]{3,4000})"',
            r"\b(?:using\s+)?prompt\s*[:=]\s*'([^']{3,4000})'",
            r'\busing\s+the\s+prompt\s*:\s*"([^"]{3,4000})"',
            r"\busing\s+the\s+prompt\s*:\s*'([^']{3,4000})'",
        )
        for pattern in patterns:
            match = re.search(pattern, user_text, re.IGNORECASE)
            if match:
                value = match.group(1).strip()
                if value:
                    return value
        fallback = user_text.strip()
        if not fallback:
            return None
        return fallback[:4000]

    async def download_variation_source_image(self, orchestrator: Any, source_image_id: str) -> str | None:
        tool_name = self.select_variation_download_tool(orchestrator)
        if not tool_name:
            return None
        schema = orchestrator._tool_input_schema_by_name.get(tool_name)
        image_id_key = self.select_download_image_id_key(schema)
        if not image_id_key:
            return None
        save_path_key = self.select_download_save_path_key(schema)
        include_base64_key = self.select_download_include_base64_key(schema)
        safe_stem = re.sub(r"[^A-Za-z0-9]+", "", source_image_id)[:24] or "VariationSource"
        requested_path: Path | None = None
        payload: Dict[str, Any] = {image_id_key: source_image_id}
        if save_path_key:
            requested_path = Path(tempfile.gettempdir()) / f"{safe_stem}_{uuid.uuid4().hex[:8]}.png"
            payload[save_path_key] = str(requested_path)
        if include_base64_key:
            payload[include_base64_key] = False
        try:
            async with orchestrator._tool_execution_lock:
                result = await orchestrator._router.call_tool(tool_name, payload)
        except Exception:
            return None
        resolved = self.resolve_downloaded_file_path(result, requested_path=requested_path)
        if resolved:
            orchestrator._record_source_image_local_path(resolved, source_image_id)
        return resolved

    @staticmethod
    def select_variation_download_tool(orchestrator: Any) -> str | None:
        for candidate in ("photarium_download_image", "catalog_download_image", "photarium_download_original"):
            if candidate in orchestrator._tool_input_schema_by_name:
                return candidate
        return None

    @staticmethod
    def select_download_image_id_key(schema: Dict[str, Any] | None) -> str | None:
        if not isinstance(schema, dict):
            return "imageId"
        props = schema.get("properties")
        if not isinstance(props, dict):
            return "imageId"
        for key in ("imageId", "image_id", "id", "imageUUID", "image_uuid", "uuid"):
            if key in props:
                return key
        return "imageId"

    @staticmethod
    def select_download_save_path_key(schema: Dict[str, Any] | None) -> str | None:
        if not isinstance(schema, dict):
            return "savePath"
        props = schema.get("properties")
        if not isinstance(props, dict):
            return "savePath"
        for key in ("savePath", "save_path", "filePath", "file_path", "path", "localPath", "local_path"):
            if key in props:
                return key
        return None

    @staticmethod
    def select_download_include_base64_key(schema: Dict[str, Any] | None) -> str | None:
        if not isinstance(schema, dict):
            return None
        props = schema.get("properties")
        if not isinstance(props, dict):
            return None
        for key in ("includeBase64", "include_base64", "includeData", "include_data"):
            if key in props:
                return key
        return None

    @staticmethod
    def resolve_downloaded_file_path(download_result: Any, *, requested_path: Path | None) -> str | None:
        if isinstance(download_result, dict):
            for key in ("savedPath", "savePath", "filePath", "file_path", "localPath", "local_path", "path"):
                value = download_result.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            filename = download_result.get("filename")
            if isinstance(filename, str) and filename.strip() and requested_path is not None and requested_path.exists() and requested_path.is_dir():
                return str(requested_path / filename.strip())
        if requested_path is not None:
            return str(requested_path)
        return None

    @staticmethod
    def is_image_variation_workflow(workflow_id: str) -> bool:
        return str(workflow_id or "").strip().lower() == "image_variation_maker"

    @staticmethod
    def is_stitching_workflow(workflow_id: str) -> bool:
        normalized = str(workflow_id or "").strip().lower()
        if not normalized:
            return False
        if normalized in {"flux_kontext_image_stitch", "flux_kontext_multi_image_stitching"}:
            return True
        return normalized.startswith("flux_kontext_") and "stitch" in normalized

    @staticmethod
    def stitch_workflow_requires_second_image(workflow_id: str) -> bool:
        normalized = str(workflow_id or "").strip().lower()
        return normalized in {"flux_kontext_multi_image_stitching", "flux_kontext_multi_image_chaining"}

    @staticmethod
    def extract_stitch_source_image_ids(user_text: str) -> list[str]:
        if not isinstance(user_text, str):
            return []
        token = r"([A-Za-z0-9][A-Za-z0-9._:-]{1,127})"
        patterns = (
            rf"Source\s+catalog\s+image\s+IDs?\s*:\s*{token}\s*(?:,|and|\s)\s*{token}",
            rf"Stitch\s+Flow\s*:\s*source(?:_id)?1?\s*=\s*{token}\s+source(?:_id)?2?\s*=\s*{token}",
            rf"\b(?:/stitch|stitch)\s+{token}\s+(?:and\s+)?{token}\b",
        )
        for pattern in patterns:
            match = re.search(pattern, user_text, re.IGNORECASE)
            if not match:
                continue
            first = match.group(1).strip()
            second = match.group(2).strip()
            if first and second:
                return [first, second]
        ids: list[str] = []
        for match in re.finditer(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", user_text):
            value = match.group(0).strip()
            if value in ids:
                continue
            ids.append(value)
            if len(ids) >= 2:
                break
        return ids

    @staticmethod
    def extract_variation_source_image_id(user_text: str) -> str | None:
        if not isinstance(user_text, str):
            return None
        patterns = (
            r"Source catalog image ID:\s*([A-Za-z0-9][A-Za-z0-9._:-]{1,127})",
            r"Variation Flow:\s*source=([A-Za-z0-9][A-Za-z0-9._:-]{1,127})",
            r"\b(?:/vary|/variation|/variations)\s+(?:image\s+id\s+|image\s+)?([A-Za-z0-9][A-Za-z0-9._:-]{1,127})\b",
        )
        for pattern in patterns:
            match = re.search(pattern, user_text, re.IGNORECASE)
            if not match:
                continue
            value = match.group(1).strip()
            if value:
                return value
        return None

    @classmethod
    def repair_workflows_import_from_artifact_arguments(
        cls,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> Dict[str, Any]:
        if tool_name != "workflows_import_from_artifact" or not isinstance(arguments, dict):
            return arguments
        repaired = dict(arguments)
        wrapper_keys = ("arguments", "args", "input", "payload", "params", "token")
        flattened_wrapper_keys: set[str] = set()
        for wrapper_key in wrapper_keys:
            wrapped = repaired.get(wrapper_key)
            if not isinstance(wrapped, dict):
                continue
            flattened_wrapper_keys.add(wrapper_key)
            for key, value in wrapped.items():
                repaired.setdefault(key, value)
        for wrapper_key in flattened_wrapper_keys:
            repaired.pop(wrapper_key, None)
        for alias in ("image_path", "artifact_path", "artifactPath", "file_path", "filePath", "source_path", "sourcePath", "local_path", "localPath"):
            if "path" not in repaired and isinstance(repaired.get(alias), str):
                repaired["path"] = repaired[alias]
        for alias in ("id", "workflow", "workflow_name", "workflowName"):
            if "workflow_id" not in repaired and isinstance(repaired.get(alias), str):
                repaired["workflow_id"] = repaired[alias]
        inferred_path = cls.extract_local_artifact_path_from_text(user_text)
        if "path" not in repaired and inferred_path:
            repaired["path"] = inferred_path
        inferred_name = cls.extract_requested_workflow_name_from_text(user_text)
        if "workflow_id" not in repaired and inferred_name:
            repaired["workflow_id"] = inferred_name
        if "name" not in repaired and inferred_name:
            repaired["name"] = inferred_name
        return repaired

    @staticmethod
    def preflight_workflows_import_from_artifact_arguments(tool_name: str, arguments: Dict[str, Any]) -> None:
        if tool_name != "workflows_import_from_artifact":
            return
        if not isinstance(arguments, dict):
            raise RuntimeError("workflows_import_from_artifact preflight failed: arguments must be an object")
        missing: list[str] = []
        for required in ("path", "workflow_id"):
            value = arguments.get(required)
            if not isinstance(value, str) or not value.strip():
                missing.append(required)
        if missing:
            missing_text = ", ".join(missing)
            raise RuntimeError(
                "workflows_import_from_artifact preflight blocked: missing required argument(s): "
                f"{missing_text}. Provide an explicit local artifact path and workflow_id."
            )

    @staticmethod
    def extract_local_artifact_path_from_text(user_text: str) -> str | None:
        if not isinstance(user_text, str):
            return None
        match = re.search(r"(/[^\"'\s]+\.(?:png|jpe?g|webp|mp4|mov|json))", user_text, re.IGNORECASE)
        if not match:
            return None
        return match.group(1).strip()

    @staticmethod
    def extract_requested_workflow_name_from_text(user_text: str) -> str | None:
        if not isinstance(user_text, str):
            return None
        patterns = (
            r'\bname\s+"([^"]{1,128})"',
            r"\bname\s+'([^']{1,128})'",
            r"\bworkflow(?:_id| id)?\s+([A-Za-z0-9._:-]{1,128})\b",
        )
        for pattern in patterns:
            match = re.search(pattern, user_text, re.IGNORECASE)
            if not match:
                continue
            value = match.group(1).strip()
            if value:
                return value
        return None

    @staticmethod
    def extract_shorthand_edit_image_id(user_text: str) -> str | None:
        if not isinstance(user_text, str):
            return None
        match = re.search(r"\bedit\s+image(?:\s+id)?\s+([A-Za-z0-9][A-Za-z0-9._:-]{1,127})\b", user_text, re.I)
        if not match:
            return None
        return match.group(1).strip()

    async def verify_photarium_image_id_candidate(self, orchestrator: Any, image_id: str) -> bool:
        for tool_name, payload in (
            ("photarium_get", {"imageId": image_id}),
            ("catalog_get", {"imageId": image_id}),
            ("catalog_get", {"image_id": image_id}),
        ):
            if tool_name not in orchestrator._tool_input_schema_by_name:
                continue
            try:
                result = await orchestrator._router.call_tool(tool_name, payload)
            except Exception:
                continue
            if isinstance(result, dict) and result.get("error"):
                continue
            return True
        return False

    @staticmethod
    def extract_flow_source_image_id(user_text: str) -> str | None:
        if not isinstance(user_text, str):
            return None
        match = re.search(r"Source catalog image ID:\s*([A-Za-z0-9-]{8,})", user_text, re.I)
        if match:
            return match.group(1).strip()
        command_match = re.search(r"(?:^|\n)\s*/tanktracks?\s+([A-Za-z0-9-]{8,})", user_text, re.I)
        if command_match:
            return command_match.group(1).strip()
        uuid_match = re.search(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", user_text, re.I)
        if uuid_match:
            return uuid_match.group(0).strip()
        return None

    @staticmethod
    def extract_flow_workflow_id(user_text: str) -> str | None:
        if not isinstance(user_text, str):
            return None
        match = re.search(r"Workflow preference:\s*([A-Za-z0-9._-]+)", user_text, re.I)
        if not match:
            match = re.search(r"(?:^|\s)workflow\s*=\s*([A-Za-z0-9._-]+)(?=\s|$)", user_text, re.I)
        if not match:
            return None
        return match.group(1).strip()

    @staticmethod
    def is_tanktracks_command_text(user_text: str) -> bool:
        if not isinstance(user_text, str):
            return False
        return bool(re.search(r"(?:^|\n)\s*/tanktracks?\b", user_text, re.I))
