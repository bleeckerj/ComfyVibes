"""Config loading for the MCP TUI chat client."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List


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
    command: str
    args: List[str] = field(default_factory=list)
    tool_prefixes: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    cwd: str | None = None


@dataclass
class ChatClientConfig:
    llm: LLMConfig
    servers: List[ServerConfig]
    system_prompt: str
    strict_tool_facts: bool = True


DEFAULT_SYSTEM_PROMPT = (
    "You are a terminal chat orchestrator. "
    "Decide when to call MCP tools, then summarize results for the user."
)


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
        command=raw["command"],
        args=list(raw.get("args", [])),
        tool_prefixes=list(raw.get("tool_prefixes", [])),
        env=dict(raw.get("env", {})),
        cwd=raw.get("cwd"),
    )


def load_config(path: str | Path) -> ChatClientConfig:
    config_path = Path(path)
    payload = json.loads(config_path.read_text())
    llm = _parse_llm(payload.get("llm", {}))
    servers = [_parse_server(item) for item in payload.get("servers", [])]
    system_prompt = payload.get("system_prompt", DEFAULT_SYSTEM_PROMPT)
    return ChatClientConfig(
        llm=llm,
        servers=servers,
        system_prompt=system_prompt,
        strict_tool_facts=bool(payload.get("strict_tool_facts", True)),
    )
