"""Config loading for the MCP TUI chat client."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent
from typing import Any, Dict, List, Literal, Tuple

ResponseVerbosity = Literal["loud", "lowkey", "quiet"]
ServerTransport = Literal["stdio", "http"]


@dataclass
class LLMConfig:
    base_url: str
    api_key_env: str
    model: str
    api_key: str | None = None
    temperature: float = 0.2
    timeout_s: float = 60.0


@dataclass
class ServerConfig:
    name: str
    command: str = ""
    args: List[str] = field(default_factory=list)
    tool_prefixes: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    # HTTP transport fields.
    http_url: str | None = None
    transport: ServerTransport = "stdio"


@dataclass
class ChatClientConfig:
    llm: LLMConfig
    servers: List[ServerConfig]
    system_prompt: str
    strict_tool_facts: bool = True
    tool_description_hints: Dict[str, str] = field(default_factory=dict)


DEFAULT_SYSTEM_PROMPT = (
    "You are a terminal chat orchestrator. "
    "Decide when to call MCP tools, then summarize results for the user."
)

@dataclass(frozen=True)
class PromptPolicy:
    policy_id: str
    title: str
    rules: Tuple[str, ...]

    @property
    def marker(self) -> str:
        return f"POLICY::{self.policy_id}"

    def render(self) -> str:
        lines = [f"[{self.marker}]", f"{self.title}"]
        lines.extend(f"- {rule}" for rule in self.rules)
        return "\n\n" + "\n".join(lines) + "\n"


WORKFLOW_OUTPUT_RELIABILITY_POLICY = PromptPolicy(
    policy_id="workflow_output_reliability",
    title="WORKFLOW OUTPUT RELIABILITY:",
    rules=(
        "Before tool calls, keep the user-facing plan to one short sentence naming the tool and the main intent.",
        "Do not narrate internal JSON/schema plumbing to the user. Avoid mentioning wrapper names like overrides, token, payload, or raw JSON unless the user is explicitly debugging a failed tool call.",
        "Before running a workflow, state the workflow_id and the parameter plan, including both explicit overrides and key defaults that will remain in effect.",
        "When using workflows_run/workflows_run_aspect_ratio_adjustment, explicitly state workflow_id and override parameters.",
        "Before calling workflows_run, perform a preflight argument check in your reasoning: confirm the outgoing tool-call JSON includes both workflow_id and an explicit overrides object (use {} if intentionally empty). If overrides is missing, do not call the tool yet; fix the arguments first.",
        "Do not silently switch workflow_id; if the user names a workflow, keep that exact workflow_id unless the user approves a change.",
        "For workflows_run, always pass an explicit overrides object; never omit it.",
        "Treat shorthand requests like 'edit image xyz' or 'edit image id xyz' as intent that still requires argument synthesis before tool execution: infer/confirm workflow_id, resolve whether xyz is a Photarium image ID vs local path vs Comfy filename, and then construct explicit overrides.",
        "When the user asks to rerun or recover the workflow from an existing image, prefer workflows_run_from_source over manually chaining extract/import/run steps.",
        "Treat 'catalog' and 'photo catalog' as semantically equivalent to Photarium / Photarium catalog.",
        "Unless user says otherwise, treat 'image' or 'image id' as a Photarium catalog image id.",
        "If workflows_run_from_source returns needs_input, ask only for the listed missing inputs and resume with resume_token.",
        "If a shorthand image token likely refers to a Photarium image ID, verify it first with photarium_get (or equivalent catalog get tool) before committing to a workflow execution plan; if valid, download/resolve to a local path (or Comfy input filename) before passing it into workflows_run overrides.",
        "For workflows_run image overrides targeting LoadImage.image, use a local file path or Comfy input filename, not a Photarium UUID; if starting from a Photarium UUID, download first.",
        "If a workflow exposes filename_prefix (SaveImage), set a unique value before the first run.",
        "When user asks for a sweep/range (for example denoise 0.5->1.0 step 0.1), run one job per value, keep non-swept overrides fixed, set unique filename_prefix per run, and report value->output mapping.",
        "For seed sweeps, generate non-deterministic random seeds programmatically for every run (fresh each request); never use fixed/counting seed patterns (for example 111111, 222222, or seed_start + index).",
        "Sweep integrity check (before execution): if you say you are sweeping X (for example seed/denoise/steps/prompt), verify each outgoing workflows_run call actually includes an explicit override for X and that X differs across runs as intended. Changing only filename_prefix does not count as a sweep.",
        "Sweep integrity check (after execution): report the actual per-run override values used (read from the tool-call payloads you sent), not just the intended plan.",
        "After workflows_run or workflows_run_aspect_ratio_adjustment, verify output_images.",
        "For heavier jobs, prefer workflows_run/workflows_run_aspect_ratio_adjustment with wait_timeout_s=300 and wait_poll_ms=1000 on the first attempt.",
        "If output_images is empty but prompt_id exists, call workflows_watch(prompt_id=..., inactivity_timeout_s=300, include_history=true) once before concluding failure.",
        "If workflows_watch is unavailable, fall back to workflows_wait(prompt_id=..., timeout_s=300, poll_ms=1000).",
        "If still empty and the tool reports likely_cached or completed-without-images, retry once with force=true.",
        "Keep filename_prefix unique across retries to improve output attribution.",
        "For photarium_upload* tools, if name/title is a transport/query blob (for example filename=...&type=...), replace it with a semantic CamelCase name/title using only letters and digits (no spaces or punctuation).",
        "For photarium_upload* tools, tags are semantic image-content labels only (subject/style/scene/object); never add workflow/instrumentation tags (for example image-edit, img2img, denoise-*, cfg-*, steps-*).",
        "Default upload behavior: for workflows_run/workflows_run_aspect_ratio_adjustment results with output_images, upload generated images to Photarium automatically without asking for extra confirmation or a target parent id; only skip when Photarium catalog tools are unavailable or offline.",
        "Default upload behavior: omit tags unless the user explicitly asks for tags or there are clear image-content tags already provided by the user.",
        "When auto-uploading generated outputs without an explicit source/parent image id, upload as new catalog images (no parent linkage).",
        "When auto-uploading from a source catalog image, deterministically resolve the upload parent in code: if the source image already has parentId/variantOf use that parent; otherwise if familyRootId exists use that root; otherwise use the source image id itself. Never upload a new result as a child of a variant when its family parent/root is known.",
        "After auto-uploading outputs from workflows_run_from_source, call workflows_lineage_register_results with the returned lineage_run_id and uploaded result image ids.",
        "Do not ask the user whether to upload workflow outputs unless upload fails because Photarium is unavailable/offline.",
        "When uploading generated outputs to Photarium and a prompt is known, set the image metadata prompt (pass prompt/positive_prompt on upload if supported, otherwise call a Photarium metadata update tool after upload). For image_variation_maker, prefer the resolved positive prompt; if unavailable, fall back to image_analysis_instructions.",
        "For image transfer between ComfyUI and Photarium, avoid inline base64 payload flows by default; prefer comfy_download_image + photarium_upload_from_path/upload_url and only request includeData/includeBase64 when the user explicitly asks for raw data.",
        "If output_images_source is filesystem_fallback, treat those outputs as usable and proceed with upload if the user asked.",
        "Do not claim workflow failure until these checks/retries have been attempted.",
    ),
)

ASPECT_RATIO_PRESERVATION_POLICY = PromptPolicy(
    policy_id="aspect_ratio_preservation",
    title="WORKFLOW ASPECT RATIO PRESERVATION:",
    rules=(
        "If the user explicitly requests an aspect ratio or dimensions, follow that request.",
        "For Photarium catalog images, prefer source width/height metadata to infer input aspect ratio; if missing, probe the downloaded file dimensions (for example via workflows_image_info).",
        "Otherwise, when a workflow has overridable resolution controls (for example aspect_ratio fields or width/height pairs such as FluxResolutionNode, LatentImage-style nodes, EmptyLatentImage, or similar), preserve the input image aspect ratio.",
        "If a node only allows fixed aspect-ratio choices, pick the nearest allowed ratio to the input image ratio and state which ratio was chosen.",
        "Keep all overridden resolution/aspect-ratio nodes consistent to one target ratio in the same run.",
        "For multi-target aspect-ratio requests, run each target as an independent branch from the same source image; do not chain one target output into another target unless the user explicitly asks for progressive chaining.",
        "When available, keep seed or stochastic controls fixed across multi-target branches to maximize composition consistency.",
        "If no suitable/overridable aspect-ratio controls exist in the workflow, do not force this heuristic.",
    ),
)

TOOL_DISCOVERY_POLICY = PromptPolicy(
    policy_id="tool_discovery",
    title="TOOL DISCOVERY AND CAPABILITY CHECK:",
    rules=(
        "Before claiming a capability does not exist, review available tool names/descriptions for likely matches.",
        "For image-variation requests (variants/variations), use workflows_capabilities_list/workflows_search first and prioritize workflow_id 'image_variation_maker' unless the user explicitly requests a different workflow.",
        "For other image-edit requests, use workflows_capabilities_list/workflows_search first and prioritize workflow_id 'flux_2_klein_4B' unless the user explicitly requests a different workflow.",
        "For retrieval tasks (search/find/filter/match by concept, style, color, or keyword), prefer *_search-style tools before concluding no results.",
        "For semantic image search results, always surface image IDs clearly (prefer image_id/ids over only names) so downstream tool calls can reference stable identifiers.",
        "For color-based search tools, convert natural color language to canonical RGB hex (for example 'sky blue' -> '#87ceeb') when the tool expects a color value.",
        "If unsure which tool can satisfy a request, state uncertainty and attempt the closest discovery tool rather than asserting impossibility.",
    ),
)

SYSTEM_PROMPT_POLICIES: Tuple[PromptPolicy, ...] = (
    WORKFLOW_OUTPUT_RELIABILITY_POLICY,
    ASPECT_RATIO_PRESERVATION_POLICY,
    TOOL_DISCOVERY_POLICY,
)

EDGAR_EDITORIAL_ROUTING_POLICY_APPENDIX = dedent(
    """\
    # EDGAR Orchestrator Routing Policy (Editorial vs Workflow Tools)

    Use this text inside EDGAR's orchestrator/system prompt.

    ## Tool Routing Rules (Explicit)

    - When a user asks to create, move, open, edit, or draft an **article/content file** for `nfl-editorial` (keywords include: article, editorial, MDX, frontmatter, issue, section, stub, draft, review, feature), use `editorial_*` tools only.
    - Never use `workflows_*` file tools (`workflows_file_write`, `workflows_file_delete`, etc.) for any path under `/Users/julian/Code/nfl-editorial` or `src/content/editorial/...`.
    - Treat `workflows_*` tools as scoped to the workflows repository only (`nfl-comfymcp/workflows`) unless the user explicitly asks to work there.

    ## Required Preflight for New Editorial Content Files

    - Before writing a new article file, call `editorial_content_create_stub` with `dryRun: true`.
    - Verify the returned `path` starts with `src/content/editorial/`.
    - Verify the returned `section`, `hierarchyPath`, and `issueNumber` match the user's intent.
    - Only then call `editorial_content_create_stub` again with `write: true` and `dryRun: false`.

    ## Draft Generation Rules

    - For draft text generation from a brief, use `editorial_content_draft_from_brief`.
    - If final placement is not yet decided (for example, still deciding between `nfl-backoffice` and `nfl-editorial`), call `editorial_content_draft_from_brief` with `writeTemp: true`.
    - Prefer temp draft output over writing directly into an article file when the destination hierarchy/issue is ambiguous.

    ## Cross-Repo Safety Rules

    - If the source material is in another repo (e.g. `nfl-backoffice`) and the target is `nfl-editorial`, do not use a generic write tool tied to the source repo.
    - Use source-repo read tools (or read-only file access) to gather content, then use `editorial_*` tools to create/write content in `nfl-editorial`.
    - If no tool with write scope to the intended repo is available, stop and say so explicitly before writing anywhere else.

    ## Preferred Editorial Creation Workflow

    1. `editorial_content_create_stub` (`dryRun: true`)
    2. `editorial_content_create_stub` (`write: true`, `dryRun: false`)
    3. `editorial_content_draft_from_brief` (`writeTemp: true`)
    4. (Optional follow-up) open the stub with `editorial_content_open_for_editing` and merge/paste draft text

    ## Ambiguity Handling

    - If the user gives only a topic/brief and no section hierarchy or issue number, ask for:
      - `hierarchyPath` (for example `fashion` or `columns/reviews`)
      - `issueNumber`
    - Do not guess a filesystem destination and write a file in another repo as a fallback.
    """
)


def _with_system_guardrails(system_prompt: str) -> str:
    composed = system_prompt.rstrip()
    for policy in SYSTEM_PROMPT_POLICIES:
        if policy.marker in composed or policy.title in composed:
            continue
        composed = f"{composed}{policy.render()}"
    editorial_routing_policy_heading = "# EDGAR Orchestrator Routing Policy (Editorial vs Workflow Tools)"
    if editorial_routing_policy_heading not in composed:
        composed = f"{composed}\n\n{EDGAR_EDITORIAL_ROUTING_POLICY_APPENDIX.rstrip()}\n"
    return composed


def render_response_verbosity_appendix(mode: ResponseVerbosity) -> str:
    normalized: ResponseVerbosity = mode if mode in {"loud", "lowkey", "quiet"} else "lowkey"
    if normalized == "loud":
        rules = (
            "Default to fuller user-facing explanations and higher-context summaries.",
            "Include brief reasoning and next steps proactively when useful.",
            "Do not be terse unless the user explicitly asks for brevity.",
        )
    elif normalized == "quiet":
        rules = (
            "Default to terse replies to save output tokens.",
            "Use the minimum user-facing text that still communicates outcome, blockers, or next action.",
            "Avoid extra explanation unless the user explicitly asks for more detail.",
        )
    else:
        rules = (
            "Default to concise, calm replies with moderate detail.",
            "Explain decisions briefly, but do not over-elaborate.",
            "Prefer compact summaries unless more depth is clearly useful.",
        )
    lines = [
        "[POLICY::response_verbosity]",
        "EDGAR RESPONSE VERBOSITY:",
        f"- Active mode: {normalized}.",
    ]
    lines.extend(f"- {rule}" for rule in rules)
    return "\n\n" + "\n".join(lines) + "\n"


def compose_runtime_system_prompt(base_system_prompt: str, response_verbosity: ResponseVerbosity) -> str:
    composed = _with_system_guardrails(base_system_prompt)
    marker = "[POLICY::response_verbosity]"
    if marker in composed:
        before, _marker, remainder = composed.partition(marker)
        remainder_text = remainder
        next_marker = remainder_text.find("\n\n[POLICY::")
        if next_marker != -1:
            composed = before.rstrip() + remainder_text[next_marker:]
        else:
            composed = before.rstrip()
    return f"{composed.rstrip()}{render_response_verbosity_appendix(response_verbosity)}"


def _parse_llm(raw: Dict[str, Any]) -> LLMConfig:
    return LLMConfig(
        base_url=raw.get("base_url", "https://api.openai.com/v1"),
        api_key_env=raw.get("api_key_env", "OPENAI_API_KEY"),
        api_key=raw.get("api_key"),
        model=raw.get("model", "gpt-4o-mini"),
        temperature=float(raw.get("temperature", 0.2)),
        timeout_s=float(raw.get("timeout_s", 60.0)),
    )


def _normalize_server_transport(raw: Dict[str, Any]) -> ServerTransport:
    explicit = raw.get("transport")
    if explicit is None:
        return "http" if raw.get("http_url") else "stdio"
    if explicit in {"stdio", "http"}:
        return explicit
    raise ValueError(f"Invalid transport {explicit!r}; expected 'stdio' or 'http'.")


def _parse_server(raw: Dict[str, Any]) -> ServerConfig:
    transport = _normalize_server_transport(raw)
    return ServerConfig(
        name=raw["name"],
        command=raw.get("command", ""),
        args=list(raw.get("args", [])),
        tool_prefixes=list(raw.get("tool_prefixes", [])),
        env=dict(raw.get("env", {})),
        cwd=raw.get("cwd"),
        http_url=raw.get("http_url"),
        transport=transport,
    )


def _validate_server(server: ServerConfig) -> None:
    if server.transport == "stdio":
        if not server.command.strip():
            raise ValueError(
                f"Server '{server.name}' uses stdio transport but has no command configured."
            )
        return
    if not (server.http_url or "").strip():
        raise ValueError(
            f"Server '{server.name}' uses HTTP transport but has no http_url configured."
        )


def load_config(path: str | Path, model_override: str | None = None) -> ChatClientConfig:
    config_path = Path(path)
    payload = json.loads(config_path.read_text())
    llm = _parse_llm(payload.get("llm", {}))
    if model_override:
        llm.model = model_override
    servers = [_parse_server(item) for item in payload.get("servers", [])]
    for server in servers:
        _validate_server(server)
    system_prompt = compose_runtime_system_prompt(payload.get("system_prompt", DEFAULT_SYSTEM_PROMPT), "lowkey")
    return ChatClientConfig(
        llm=llm,
        servers=servers,
        system_prompt=system_prompt,
        strict_tool_facts=bool(payload.get("strict_tool_facts", True)),
        tool_description_hints=dict(payload.get("tool_description_hints", {})),
    )
