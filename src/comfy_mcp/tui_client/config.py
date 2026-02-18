"""Config loading for the MCP TUI chat client."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple


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
    # HTTP transport fields (when set, the router uses HTTP instead of stdio)
    http_url: str | None = None
    transport: str = "stdio"  # "stdio" or "http"


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
        "Be explicit before tool calls: name the tool, why it is needed, and the key arguments you will pass.",
        "Before running a workflow, state the workflow_id and the parameter plan, including both explicit overrides and key defaults that will remain in effect.",
        "When using workflows_run/workflows_run_aspect_ratio_adjustment, explicitly state workflow_id and override parameters.",
        "Treat 'catalog' and 'photo catalog' as semantically equivalent to Photarium / Photarium catalog.",
        "Unless user says otherwise, treat 'image' or 'image id' as a Photarium catalog image id.",
        "If a workflow exposes filename_prefix (SaveImage), set a unique value before the first run.",
        "After workflows_run or workflows_run_aspect_ratio_adjustment, verify output_images.",
        "For heavier jobs, prefer workflows_run/workflows_run_aspect_ratio_adjustment with wait_timeout_s=300 and wait_poll_ms=1000 on the first attempt.",
        "If output_images is empty but prompt_id exists, call workflows_watch(prompt_id=..., inactivity_timeout_s=300, include_history=true) once before concluding failure.",
        "If workflows_watch is unavailable, fall back to workflows_wait(prompt_id=..., timeout_s=300, poll_ms=1000).",
        "If still empty and the tool reports likely_cached or completed-without-images, retry once with force=true.",
        "Keep filename_prefix unique across retries to improve output attribution.",
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
        "If no suitable/overridable aspect-ratio controls exist in the workflow, do not force this heuristic.",
    ),
)

TOOL_DISCOVERY_POLICY = PromptPolicy(
    policy_id="tool_discovery",
    title="TOOL DISCOVERY AND CAPABILITY CHECK:",
    rules=(
        "Before claiming a capability does not exist, review available tool names/descriptions for likely matches.",
        "For image-edit requests, use workflows_capabilities_list/workflows_search first and prioritize workflow_id 'flux_2_klein_4B' unless the user explicitly requests a different workflow.",
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


def _with_system_guardrails(system_prompt: str) -> str:
    composed = system_prompt.rstrip()
    for policy in SYSTEM_PROMPT_POLICIES:
        if policy.marker in composed or policy.title in composed:
            continue
        composed = f"{composed}{policy.render()}"
    return composed


def _parse_llm(raw: Dict[str, Any]) -> LLMConfig:
    return LLMConfig(
        base_url=raw.get("base_url", "https://api.openai.com/v1"),
        api_key_env=raw.get("api_key_env", "OPENAI_API_KEY"),
        api_key=raw.get("api_key"),
        model=raw.get("model", "gpt-4o-mini"),
        temperature=float(raw.get("temperature", 0.2)),
        timeout_s=float(raw.get("timeout_s", 60.0)),
    )


def _parse_server(raw: Dict[str, Any]) -> ServerConfig:
    return ServerConfig(
        name=raw["name"],
        command=raw.get("command", ""),
        args=list(raw.get("args", [])),
        tool_prefixes=list(raw.get("tool_prefixes", [])),
        env=dict(raw.get("env", {})),
        cwd=raw.get("cwd"),
        http_url=raw.get("http_url"),
        transport=raw.get("transport", "stdio"),
    )


def load_config(path: str | Path, model_override: str | None = None) -> ChatClientConfig:
    config_path = Path(path)
    payload = json.loads(config_path.read_text())
    llm = _parse_llm(payload.get("llm", {}))
    if model_override:
        llm.model = model_override
    servers = [_parse_server(item) for item in payload.get("servers", [])]
    system_prompt = _with_system_guardrails(payload.get("system_prompt", DEFAULT_SYSTEM_PROMPT))
    return ChatClientConfig(
        llm=llm,
        servers=servers,
        system_prompt=system_prompt,
        strict_tool_facts=bool(payload.get("strict_tool_facts", True)),
        tool_description_hints=dict(payload.get("tool_description_hints", {})),
    )
