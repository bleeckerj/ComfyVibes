"""Prompt-building and flow parsing helpers for local TUI commands."""

from __future__ import annotations

import re
import secrets
from typing import Dict, List


class FlowPromptBuilder:
    def __init__(self, number_words: Dict[str, int] | None = None):
        self._number_words = number_words or {}

    @staticmethod
    def build_moodboard_flow_prompt(
        *,
        brief: str,
        count: int,
        palette: str | None,
        refs: List[str],
        workflow_id: str | None,
        novelty: str,
    ) -> str:
        refs_text = ", ".join(refs) if refs else "none provided"
        workflow_text = workflow_id or "auto-select via capabilities"
        brief_text = brief or "Build a brand-inspiration mood board from available references."
        palette_text = palette or "not specified"
        novelty_text = novelty.lower().strip() or "high"
        return (
            "MOODBOARD FLOW REQUEST\n"
            "Run this as an agentic multi-step flow inside the TUI.\n\n"
            f"Creative brief: {brief_text}\n"
            f"Target candidate count: {count}\n"
            f"Palette hint: {palette_text}\n"
            f"Starter reference image IDs: {refs_text}\n"
            f"Workflow preference: {workflow_text}\n\n"
            f"Novelty target: {novelty_text}\n\n"
            "Execution rules:\n"
            "1. Do retrieval first: use semantic search and color-oriented search tools when available.\n"
            "2. Keep discovery lightweight: at most 2 discovery/introspection tool calls before first generation run.\n"
            "3. Draft at least 3 distinct creative directions from the brief before generation.\n"
            "4. Rewrite prompts so they are not copied from source captions/text and explicitly remove any source text/logo artifacts.\n"
            "5. Enforce generation-mode mix: at least 70% text-to-image or weak-reference generation, at most 30% direct img2img/reference edits.\n"
            "6. If using image-conditioned runs, keep transformation strong enough to avoid near-duplicate copies of source images.\n"
            "7. Vary at least 3 axes per direction: reference subset, prompt framing, and seed/variation controls.\n"
            "8. Apply anti-clone gates: reject outputs that are obvious duplicates or retain original text overlays/watermarks.\n"
            "9. For workflows_run/workflows_run_aspect_ratio_adjustment use wait_timeout_s=300 and wait_poll_ms=1000 by default.\n"
            "10. If output is delayed and prompt_id exists, use workflows_watch(prompt_id=..., inactivity_timeout_s=300, include_history=true) before retrying.\n"
            "11. Prefer uploading or linking outputs so the board is browseable.\n"
            "12. Curate final results into grouped directions (e.g., 3-5 clusters) and explain differences.\n"
            "13. Keep narration concise; prioritize executing tool calls over long planning prose.\n"
            "14. Ask at most one clarifying question only if strictly required to proceed.\n"
            "15. Do not ask for CLI commands or scripts; complete this with available MCP tools.\n"
        )

    @staticmethod
    def build_aspect_flow_prompt(
        *,
        source_id: str,
        target_ratios: List[str],
        variant_of: str,
        workflow_id: str,
        source_ratio_hint: str | None,
        max_delta: float,
        denoise_override: float | None,
        seed_sweep_count: int,
        seed_values: List[int],
        preserve_guidance: str | None,
        negative_guidance: str | None,
    ) -> str:
        target_text = ", ".join(target_ratios)
        source_hint_text = source_ratio_hint or "none provided; infer from image dimensions"
        preserve_text = preserve_guidance or (
            "Keep the same subject, scene, composition intent, lighting, and style. Keep the main subject completely in the frame and in full view. Avoid excessive cropping of the main subject. "
            "Only adjust framing/outpaint while keeping the main subject completely in the frame and in full view while reaching the target ratio."
        )
        negative_text = negative_guidance or (
            "Do not change subject identity or key scene elements. "
            "Avoid ghosting, duplicate limbs, warped geometry, or scene drift."
        )
        denoise_text = f"{denoise_override:.3f}" if denoise_override is not None else "workflow default"
        if seed_values:
            seed_sweep_text = ", ".join(str(value) for value in seed_values)
        elif seed_sweep_count > 0:
            seed_sweep_text = f"{seed_sweep_count} distinct seeds (agent selects values)"
        else:
            seed_sweep_text = "none requested"
        if workflow_id == "aspect_ratio_adjustment":
            return (
                "ASPECT RATIO FLOW REQUEST\n"
                "Run this as a deterministic, minimal-branch flow inside the TUI.\n\n"
                f"Source catalog image ID: {source_id}\n"
                f"Requested target aspect ratios: {target_text}\n"
                f"Source ratio hint: {source_hint_text}\n"
                f"Requested upload target image ID: {variant_of}\n"
                f"Workflow preference: {workflow_id}\n"
                f"Max safe per-step ratio delta (log-space): {max_delta:.2f}\n"
                f"Denoise override: {denoise_text}\n"
                f"Seed sweep request: {seed_sweep_text}\n"
                f"Positive preservation guidance: {preserve_text}\n"
                f"Negative guidance: {negative_text}\n\n"
                "Execution rules (aspect_ratio_adjustment fast path):\n"
                "1. Download source image from Photarium by canonical image ID (never by display name).\n"
                "2. Determine true source aspect ratio from Photarium metadata width/height when available; otherwise use workflows_image_info on the downloaded file. If a hint is provided, validate it.\n"
                "3. Treat each requested target ratio as an independent branch from the same original source image (never use one target branch output as another target's input).\n"
                "4. For plain ratio requests like 3:2 or 4:5, do not inspect workflow enums. Use custom ratio mode directly by setting all three fields together: aspect_ratio=<W:H>, custom_ratio=true, custom_aspect_ratio=<W:H>; or use the specialized aspect-ratio tool with the plain ratio token.\n"
                "5. Do not call workflows_params_get, workflows_get, or tool_schema_get just to inspect aspect_ratio enum labels for this flow. Only introspect if an actual tool error says a specific override/field is unsupported.\n"
                "6. If Denoise override is not provided, prefer workflows_run_aspect_ratio_adjustment first (image_path + aspect_ratio + optional prompts/seed/output_base_name).\n"
                "7. If Denoise override is provided, go straight to workflows_run for workflow_id aspect_ratio_adjustment with an explicit overrides object (include image, aspect_ratio, custom_ratio/custom_aspect_ratio, denoise, filename_prefix, plus seed when sweeping).\n"
                "8. If workflows_run_aspect_ratio_adjustment fails with a tool-specific internal error, fall back once to workflows_run with explicit overrides instead of repeating introspection.\n"
                "9. If source->target exceeds max delta, insert one or more intermediate ratios only within that branch; keep all branches rooted at the original source image.\n"
                "10. Include positive and negative guidance each step to preserve subject and scene integrity.\n"
                "11. After each run, verify output_images. If comfy_download_image fails for an output, call comfy_history_get for the prompt_id and retry comfy_download_image with exact filename/subfolder/type. Do not use photarium_import_url(includeData=true) -> photarium_upload_image as an image transport workaround.\n"
                "12. Resolve effective upload parent: call photarium_get on the source image; if source has parent_id, use that parent_id, otherwise use source image ID.\n"
                "13. If upload to requested target fails parent/variant validation, retry once using the resolved effective parent from step 12.\n"
                "14. Upload final outputs as Photarium variants under the effective parent image ID and return a concise ratio->image_id mapping.\n"
                "15. Include branch traces (source->...->target) for each requested ratio and confirm every branch started from the same source image.\n"
                "16. If seed sweep is requested, run one output per seed with all non-seed overrides fixed; report seed->image_id mapping.\n"
                "17. Do not ask for CLI commands or scripts; complete with available MCP tools.\n"
            )
        return (
            "ASPECT RATIO FLOW REQUEST\n"
            "Run this as an agentic multi-step flow inside the TUI.\n\n"
            f"Source catalog image ID: {source_id}\n"
            f"Requested target aspect ratios: {target_text}\n"
            f"Source ratio hint: {source_hint_text}\n"
            f"Requested upload target image ID: {variant_of}\n"
            f"Workflow preference: {workflow_id}\n"
            f"Max safe per-step ratio delta (log-space): {max_delta:.2f}\n"
            f"Denoise override: {denoise_text}\n"
            f"Seed sweep request: {seed_sweep_text}\n"
            f"Positive preservation guidance: {preserve_text}\n"
            f"Negative guidance: {negative_text}\n\n"
            "Execution rules:\n"
            "1. Download source image from Photarium by canonical image ID (never by display name).\n"
            "2. Determine true source aspect ratio from Photarium metadata width/height when available; otherwise use workflows_image_info on the downloaded file. If a hint is provided, validate it.\n"
            "3. Discover allowed aspect_ratio choices from workflow/node capabilities (FluxResolutionNode) and restrict runs to that set.\n"
            "4. Normalize all ratios to W:H. Pass exact allowed enum strings to workflow calls (for example '4:5 (Artistic Frame)'). If a requested ratio is unavailable, map to nearest allowed ratio and report substitutions.\n"
            "5. Treat each requested target ratio as an independent branch from the same original source image.\n"
            "6. For each branch, if source->target exceeds max delta, insert one or more allowed intermediate ratios only within that branch.\n"
            "7. Reuse one deterministic seed across branches when the workflow exposes seed control.\n"
            "8. Execute each branch with workflows_run_aspect_ratio_adjustment using workflow_id and source image_path as the branch root (never use one target branch output as another target's input).\n"
            "9. Include positive and negative guidance each step to preserve subject and scene integrity.\n"
            "10. After each run, verify output_images and do sanity checks for subject retention; if drift/ghosting appears, retry that same branch with closer intermediate and stronger guidance.\n"
            "11. If comfy_download_image fails for an output, call comfy_history_get for the prompt_id and retry comfy_download_image with exact filename/subfolder/type. Do not use photarium_import_url(includeData=true) -> photarium_upload_image as an image transport workaround.\n"
            "12. Resolve effective upload parent before uploading: call photarium_get on the source image; if source has parent_id, use that parent_id, otherwise use source image ID.\n"
            "13. If upload to requested target fails parent/variant validation, retry once using the resolved effective parent from step 12.\n"
            "14. Upload final outputs as Photarium variants under the effective parent image ID and return a concise ratio->image_id mapping.\n"
            "15. Include branch traces (source->...->target) for each requested ratio and confirm every branch started from the same source image.\n"
            "16. If denoise override is provided, set denoise to that exact value for each run.\n"
            "17. If seed sweep is requested, run one output per seed with all non-seed overrides fixed; report seed->image_id mapping.\n"
            "18. Do not ask for CLI commands or scripts; complete with available MCP tools.\n"
        )

    @staticmethod
    def parse_seed_values(raw: str | None) -> List[int]:
        if not raw:
            return []
        values: List[int] = []
        for chunk in raw.split(","):
            text = chunk.strip()
            if not text:
                continue
            try:
                values.append(int(text))
            except ValueError:
                continue
        return values[:24]

    @staticmethod
    def generate_random_seed_values(count: int) -> List[int]:
        capped = max(0, min(24, int(count)))
        if capped <= 0:
            return []
        values: List[int] = []
        seen: set[int] = set()
        while len(values) < capped:
            candidate = secrets.randbelow(2_147_483_647) + 1
            if candidate in seen:
                continue
            seen.add(candidate)
            values.append(candidate)
        return values

    def parse_run_count_from_text(self, text: str) -> int | None:
        if not text:
            return None
        lowered = text.lower()
        match = re.search(r"\brun(?:\s+it)?\s+(\d{1,2})\s+times?\b", lowered)
        if not match:
            match = re.search(r"\b(\d{1,2})\s+times?\b", lowered)
        if not match:
            match = re.search(r"\b(?:across|over|for)?\s*(\d{1,2})\s+seeds?\b", lowered)
        if match:
            try:
                return max(1, min(24, int(match.group(1))))
            except ValueError:
                return None
        match = re.search(r"\brun(?:\s+it)?\s+([a-z]+)\s+times?\b", lowered)
        if not match:
            match = re.search(r"\b([a-z]+)\s+times?\b", lowered)
        if not match:
            match = re.search(r"\b(?:across|over|for)?\s*([a-z]+)\s+seeds?\b", lowered)
        if match:
            word = match.group(1).strip().lower()
            value = self._number_words.get(word)
            if value is not None:
                return max(1, min(24, value))
        return None

    @staticmethod
    def normalize_sweep_target(value: str | None) -> str | None:
        if not value:
            return None
        token = value.strip().lower()
        aliases = {
            "seed": "seed",
            "seeds": "seed",
            "prompt": "prompt",
            "analysis": "prompt",
            "analysis_prompt": "prompt",
            "analysis_instructions": "prompt",
            "image_analysis_prompt": "prompt",
            "image_analysis_instructions": "prompt",
            "denoise": "denoise",
            "cfg": "cfg",
            "guidance": "guidance",
            "strength": "strength",
            "steps": "steps",
        }
        return aliases.get(token)

    def infer_variation_sweep(self, text: str) -> str | None:
        if not text:
            return None
        lowered = text.lower()
        if re.search(r"\b(prompt|analysis|instruction)s?\b", lowered) and re.search(r"\b(vary|varying|sweep|variation)\b", lowered):
            return "prompt"
        if re.search(r"\bdenoise\b", lowered) and re.search(r"\b(vary|varying|sweep|variation)\b", lowered):
            return "denoise"
        if re.search(r"\bseeds?\b", lowered):
            return "seed"
        match = re.search(r"\b(?:sweep|vary|varying)\s+(?:the\s+)?([a-z_][a-z0-9_-]*)", lowered)
        if match:
            return self.normalize_sweep_target(match.group(1))
        return None

    def parse_sweep_values(self, text: str) -> tuple[str | None, List[float]]:
        if not text:
            return None, []
        param_pattern = r"(denoise|cfg|guidance|strength|steps)"
        range_match = re.search(
            rf"\b{param_pattern}\s*=?\s*(\d*\.?\d+)\s*(?:->|-)\s*(\d*\.?\d+)\s*(?:step|by)\s*(\d*\.?\d+)",
            text,
            flags=re.IGNORECASE,
        )
        if range_match:
            param = range_match.group(1).lower()
            start = float(range_match.group(2))
            end = float(range_match.group(3))
            step = float(range_match.group(4))
            values = self.build_range_values(start, end, step)
            return param, self.normalize_sweep_values(param, values)
        list_match = re.search(rf"\b{param_pattern}\s*=\s*([0-9.,\s]+)", text, flags=re.IGNORECASE)
        if not list_match:
            list_match = re.search(rf"\b{param_pattern}\s+([0-9.,\s]+)", text, flags=re.IGNORECASE)
        if list_match:
            param = list_match.group(1).lower()
            values = self.parse_float_values(list_match.group(2))
            return param, self.normalize_sweep_values(param, values)
        return None, []

    @staticmethod
    def infer_aspect_targets_from_text(text: str) -> str | None:
        if not text:
            return None
        lowered = text.lower()
        patterns = [
            r"(?:targets?|ratios?)\D{0,24}([0-9xX:]+(?:\s*,\s*[0-9xX:]+)*)",
            r"(?:output\s+aspect(?:\s+ratio)?(?:\s+adjustment)?(?:\s+of|\s+to)?)\D{0,24}([0-9xX:]+)",
            r"(?:adjust(?:ment)?\s+to)\D{0,16}([0-9xX:]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                candidate = (match.group(1) or "").strip()
                if candidate:
                    return candidate
        if any(token in lowered for token in ("target", "aspect", "ratio")):
            ratios = re.findall(r"\b\d+\s*(?::|x|X)\s*\d+\b", text)
            unique: list[str] = []
            for ratio in ratios:
                normalized = FlowPromptBuilder.normalize_ratio_token(ratio)
                if normalized and normalized not in unique:
                    unique.append(normalized)
            if len(unique) == 1:
                return unique[0]
        return None

    @staticmethod
    def parse_float_values(raw: str) -> List[float]:
        if not raw:
            return []
        values: List[float] = []
        for token in re.findall(r"\d*\.?\d+", raw):
            try:
                values.append(float(token))
            except ValueError:
                continue
        return values

    @staticmethod
    def build_range_values(start: float, end: float, step: float) -> List[float]:
        if step == 0:
            return []
        direction = 1.0 if end >= start else -1.0
        step = abs(step) * direction
        values: List[float] = []
        current = start
        for _ in range(24):
            if (direction > 0 and current > end + 1e-9) or (direction < 0 and current < end - 1e-9):
                break
            values.append(current)
            current += step
        return values

    @staticmethod
    def normalize_sweep_values(param: str, values: List[float]) -> List[float]:
        if not values:
            return []
        if param == "steps":
            normalized = [float(max(1, int(round(value)))) for value in values]
        elif param == "denoise":
            normalized = [max(0.0, min(1.0, value)) for value in values]
        else:
            normalized = values
        return normalized[:24]

    @staticmethod
    def build_tanktracks_flow_prompt(
        *,
        source_id: str,
        variant_of: str,
        workflow_id: str,
        seed_override: int | None,
        denoise_override: float | None,
        post_aspect_ratio: str | None,
        run_index: int | None,
        run_count: int | None,
    ) -> str:
        seed_text = str(seed_override) if seed_override is not None else "use workflow default seed"
        denoise_text = f"{denoise_override:.4g}" if denoise_override is not None else "use workflow default denoise"
        post_aspect_text = post_aspect_ratio or "none"
        run_header = f"Run index: {run_index} of {run_count}\n" if run_index is not None and run_count is not None else ""
        if workflow_id == "add_tank_tracks":
            return (
                "TANK TRACKS FLOW REQUEST\n"
                "Run this as a deterministic, minimal-branch flow inside the TUI.\n\n"
                f"Source catalog image ID: {source_id}\n"
                f"Requested upload target image ID: {variant_of}\n"
                f"Workflow preference: {workflow_id}\n"
                "Prompt behavior: use fixed workflow default prompt (no override)\n\n"
                + run_header
                + f"Seed override: {seed_text}\n"
                + f"Denoise override: {denoise_text}\n"
                + f"Post aspect ratio adjustment: {post_aspect_text}\n\n"
                "Execution rules (add_tank_tracks fast path):\n"
                "1. This is a fresh execution request. Always run now; never reuse prior results or previously uploaded image IDs as a substitute.\n"
                "2. Resolve effective upload parent: call photarium_get on the source image; if source has parent_id, use that parent_id, otherwise use source image ID.\n"
                "3. Download the source image from Photarium by canonical image ID (not display name).\n"
                "4. Run workflows_run with an explicit overrides object and wait settings. Use only these override keys:\n"
                "   - image: <local_path>\n"
                "   - filename_prefix: <unique>\n"
                "   - seed: <seed_override> (include only when Seed override is specified)\n"
                "   - denoise: <denoise_override> (include only when Denoise override is specified)\n"
                "   Preflight-check the tool-call JSON before sending: include both workflow_id and overrides.\n"
                "   Do not set aspect_ratio/custom_* manually; let workflows_run auto-preserve aspect ratio from the input image.\n"
                "5. Verify output_images; if empty but prompt_id exists, call workflows_watch once before retrying.\n"
                "6. If Post aspect ratio adjustment is set (for example 4:5), run workflows_run_aspect_ratio_adjustment on the tank-tracks output using a local file path and use that adjusted result as the upload artifact.\n"
                "7. Download the final artifact (tank-tracks output or post-aspect output) via comfy_download_image and upload to Photarium as a variant of the effective parent image ID.\n"
                "   On upload, add tags exactly: 'tank tracks', 'caterpillar tracks', 'tracks'. Do not change the image display name (preserve the existing/source-derived display name; do not set it to 'AddTankTracks').\n"
                "8. If upload to requested target fails parent/variant validation, retry once using the resolved effective parent from step 2.\n"
                "9. Report the uploaded catalog image ID, effective parent ID, seed used (if any), and auto_aspect_ratio_source/applied_anchor if present (otherwise report the input image ratio from workflows_image_info).\n"
                "10. Do not call workflows_capabilities_get, workflows_params_get, or tool_schema_get for this flow unless seed override support is unknown and must be verified.\n"
                "11. Do not ask for CLI scripts or manual user steps; complete with available MCP tools.\n"
            )
        return (
            "TANK TRACKS FLOW REQUEST\n"
            "Run this as an agentic multi-step flow inside the TUI.\n\n"
            f"Source catalog image ID: {source_id}\n"
            f"Requested upload target image ID: {variant_of}\n"
            f"Workflow preference: {workflow_id}\n"
            "Prompt behavior: use fixed workflow default prompt (no override)\n\n"
            + run_header
            + f"Seed override: {seed_text}\n"
            + f"Denoise override: {denoise_text}\n"
            + f"Post aspect ratio adjustment: {post_aspect_text}\n\n"
            "Execution rules:\n"
            "1. This is a fresh execution request. Always run now; never reuse prior results or previously uploaded image IDs as a substitute.\n"
            "2. Retrieve the source image from Photarium by canonical image ID (not display name).\n"
            "3. Confirm workflow capability and parameters before execution.\n"
            "4. Determine source dimensions before running: prefer Photarium width/height metadata; otherwise call workflows_image_info on the downloaded file.\n"
            "5. Run the selected image-edit workflow against the downloaded image.\n"
            "6. Preserve source aspect ratio by setting workflow aspect controls to match source ratio (custom_ratio=true, custom_aspect_ratio=W:H, and nearest valid aspect_ratio anchor if required).\n"
            "7. Use workflow default prompt (do not send prompt override).\n"
            "8. Resolve effective upload parent before uploading: call photarium_get on the source image; if source has parent_id, use that parent_id, otherwise use source image ID.\n"
            "9. If upload to requested target fails parent/variant validation, retry once using the resolved effective parent from step 8.\n"
            "10. Upload the best output image as a variant of the effective parent image ID.\n"
            "   On upload, add tags exactly: 'tank tracks', 'caterpillar tracks', 'tracks'. Do not change the image display name (preserve the existing/source-derived display name; do not set it to 'AddTankTracks').\n"
            "11. Report the uploaded catalog image ID, effective parent ID, source ratio used, and concise tool-call trace.\n"
            "12. If Seed override is specified and the workflow supports a seed parameter, include it explicitly in overrides.\n"
            "13. If Denoise override is specified and the workflow supports a denoise parameter, include it explicitly in overrides.\n"
            "14. If Post aspect ratio adjustment is specified, apply workflows_run_aspect_ratio_adjustment after the image-edit workflow and upload the adjusted output.\n"
            "15. Do not ask for CLI scripts or manual user steps; complete with available MCP tools.\n"
        )

    @staticmethod
    def build_variation_flow_prompt(
        *,
        source_id: str,
        variant_of: str,
        workflow_id: str,
        analysis_prompt: str | None,
        upscale: bool | None,
        run_count: int | None,
        sweep_target: str | None,
        sweep_values: List[float] | None,
        seed_values: List[int] | None,
        extra_instructions: str | None,
    ) -> str:
        analysis_text = analysis_prompt or "use workflow default image_analysis_instructions"
        if upscale is None:
            upscale_text = "not specified"
        else:
            upscale_text = "enabled" if upscale else "disabled"
        if run_count:
            run_count_text = str(run_count)
        else:
            run_count_text = "not specified"
        if sweep_target:
            sweep_text = sweep_target
        elif run_count and run_count > 1:
            sweep_text = "seed (default)"
        else:
            sweep_text = "none requested"
        if sweep_values:
            sweep_values_text = ", ".join(f"{value:g}" for value in sweep_values)
        else:
            sweep_values_text = "none provided"
        if seed_values:
            seed_values_text = ", ".join(str(value) for value in seed_values)
        else:
            seed_values_text = "none provided"
        extra_text = extra_instructions or "none"
        return (
            "IMAGE VARIATION FLOW REQUEST\n"
            "Run this as an agentic multi-step flow inside the TUI.\n\n"
            f"Source catalog image ID: {source_id}\n"
            f"Requested upload target image ID: {variant_of}\n"
            f"Workflow preference: {workflow_id}\n"
            f"Image analysis prompt: {analysis_text}\n"
            f"Upscale request: {upscale_text}\n"
            f"Requested run count: {run_count_text}\n"
            f"Sweep target: {sweep_text}\n"
            f"Sweep values: {sweep_values_text}\n"
            f"Seed sweep values (randomized): {seed_values_text}\n"
            f"Additional variation instructions: {extra_text}\n\n"
            "Execution rules:\n"
            "1. Retrieve the source image from Photarium by canonical image ID (not display name).\n"
            "2. Confirm workflow capability and parameters with workflows_params_get before execution.\n"
            "3. Download the image locally and pass the local path to the workflow image input.\n"
            "4. If an image analysis prompt is provided, map it to the workflow's image_analysis_instructions (or the Griptape STRING input) and keep all other defaults.\n"
            "5. If upscale is requested and the workflow exposes an upscale toggle or method (for example upscale, upscale_method, megapixels, resolution_steps), enable it and state the chosen values. If no upscale controls exist, proceed without upscaling and report that limitation.\n"
            "6. If run count >1, run multiple jobs. Default sweep is different seeds unless a sweep target is specified; keep non-swept overrides fixed.\n"
            "   For seed sweeps, use non-deterministic random seeds per run (never fixed sequences like 111111, 222222). If randomized seed values are provided in this request, use them as authoritative.\n"
            "7. If sweep values are provided, run once per value in order (or truncate to run_count if smaller); treat them as authoritative for the sweep target.\n"
            "8. If sweep target is prompt/image_analysis_instructions, generate one augmented prompt per run from the base analysis prompt (or workflow default) and keep other overrides fixed.\n"
            "9. If sweep target is denoise/cfg/guidance/strength/steps, vary only that parameter across runs; keep prompts and seeds fixed unless seeds are the sweep target.\n"
            "10. Run workflows_run with an explicit overrides object and a unique filename_prefix per run.\n"
            "    Preflight-check each workflows_run call payload before sending: it must include workflow_id and overrides (never omit overrides).\n"
            "    If doing a sweep (especially seed sweep), verify the swept parameter is explicitly present in each run's overrides and actually differs across runs; varying only filename_prefix is a mistake.\n"
            "11. Resolve effective upload parent before uploading: call photarium_get on the source image; if source has parent_id, use that parent_id, otherwise use source image ID.\n"
            "12. Upload all successful outputs as Photarium variants of the effective parent image ID (do not choose a single 'best' output).\n"
            "13. Update Photarium prompt metadata for each uploaded image: prefer the resolved positive prompt for that run; if unavailable, use the image analysis prompt. If the upload tool accepts a prompt field, include it; otherwise call a metadata update tool after upload.\n"
            "14. Report uploaded catalog image IDs, effective parent ID, prompt used, and concise tool-call trace. Include run index / actual swept override value -> image_id mapping for multi-run requests.\n"
            "15. Do not ask for CLI scripts or manual user steps; complete with available MCP tools.\n"
        )

    @staticmethod
    def build_import_workflow_flow_prompt(
        *,
        image_id: str,
        workflow_id: str,
        workflow_name: str,
        tags: List[str],
        description: str | None,
        photarium_mcp_url: str,
    ) -> str:
        desc_text = description or f"Imported from Photarium image {image_id}"
        tags_text = ", ".join(tags) if tags else "photarium, imported"
        return (
            "IMPORT WORKFLOW FLOW REQUEST\n"
            "Run this as an agentic shortcut flow inside the TUI.\n\n"
            f"Source Photarium image ID: {image_id}\n"
            f"Target workflow_id: {workflow_id}\n"
            f"Workflow display name: {workflow_name}\n"
            f"Workflow tags: {tags_text}\n"
            f"Workflow description hint: {desc_text}\n"
            f"Photarium MCP URL: {photarium_mcp_url}\n\n"
            "Execution rules:\n"
            "1. Call workflows_import_from_photarium with image_id, workflow_id, photarium_mcp_url, name, tags, and hints.description.\n"
            "2. Keep include_suggestions=true and prefer_prompt=true unless a hard failure requires retry.\n"
            "3. If import fails because embedded workflow is missing, report that clearly and stop.\n"
            "4. On success, verify with workflows_get and workflows_params_get for the new workflow_id.\n"
            "5. Confirm packaged sidecars exist (workflow.json, meta.json, params.json) via the workflow tool results.\n"
            "6. Return concise summary: workflow_id, source image_id, params_count, and any notable packaging warnings.\n"
            "7. Do not ask for CLI scripts or manual user steps; complete with available MCP tools.\n"
        )

    @staticmethod
    def build_imageedit_flow_prompt(
        *,
        source_id: str,
        variant_of: str,
        workflow_id: str,
        edit_request: str,
        analysis_prompt: str | None,
        seed_override: int | None,
        post_aspect_ratio: str | None,
        run_index: int | None,
        run_count: int | None,
    ) -> str:
        analysis_text = analysis_prompt or "infer image-analysis/conditioning guidance from the edit request if the workflow supports it"
        seed_text = str(seed_override) if seed_override is not None else "use workflow default seed"
        post_aspect_text = post_aspect_ratio or "none"
        run_header = f"Run index: {run_index} of {run_count}\n" if run_index is not None and run_count is not None else ""
        return (
            "IMAGE EDIT FLOW REQUEST\n"
            "Run this as an agentic image-edit flow inside the TUI.\n\n"
            f"Source catalog image ID: {source_id}\n"
            f"Requested upload target image ID: {variant_of}\n"
            f"Workflow preference: {workflow_id}\n"
            f"Edit request: {edit_request}\n"
            f"Analysis prompt (optional): {analysis_text}\n"
            + run_header
            + f"Seed override: {seed_text}\n"
            + f"Post aspect ratio adjustment: {post_aspect_text}\n\n"
            "Execution rules:\n"
            "1. Resolve effective upload parent: call photarium_get on the source image; if source has parent_id, use that parent_id, otherwise use source image ID.\n"
            "2. Download the source image from Photarium by canonical image ID.\n"
            "3. Inspect workflow parameters with workflows_params_get (and workflows_capabilities_get if needed) to map the edit request to valid overrides.\n"
            "4. Run workflows_run with an explicit overrides object. Always include the image input as a local file path and preserve aspect ratio unless the user explicitly asks to change framing.\n"
            "   If Seed override is specified and the workflow supports a seed parameter, include it explicitly in overrides.\n"
            "5. Map the natural-language edit request to the best available prompt/instruction fields for the selected workflow.\n"
            "6. If the workflow has image-analysis or conditioning text fields, use the provided analysis prompt when present; otherwise derive concise guidance from the edit request.\n"
            "7. Verify output_images; if empty but prompt_id exists, call workflows_watch once before retrying download.\n"
            "8. If Post aspect ratio adjustment is specified, run workflows_run_aspect_ratio_adjustment on the image-edit output using a local file path and use the adjusted result as the upload artifact.\n"
            "9. Download the final artifact (image-edit output or post-aspect output) via comfy_download_image and upload it to Photarium as a variant of the effective parent image ID.\n"
            "10. If upload to requested target fails parent/variant validation, retry once using the resolved effective parent from step 1.\n"
            "11. Report the uploaded catalog image ID, effective parent ID, workflow used, seed used (if any), and the exact overrides sent to workflows_run (sanitized paths are okay).\n"
            "12. Do not ask for CLI scripts or manual user steps; complete with available MCP tools.\n"
        )

    @staticmethod
    def normalize_ratio_token(value: str) -> str | None:
        match = re.search(r"(\d+)\s*[:xX]\s*(\d+)", value)
        if not match:
            return None
        width = int(match.group(1))
        height = int(match.group(2))
        if width <= 0 or height <= 0:
            return None
        return f"{width}:{height}"
