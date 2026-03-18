from __future__ import annotations

from pathlib import Path

import pytest

from comfy_mcp.tui_client.config import ChatClientConfig, LLMConfig, ServerConfig
from comfy_mcp.tui_client.script_runner import _CheckResult, _doctor


class _FakeRouter:
    async def connect(self) -> None:
        return None

    def list_tool_specs(self):
        return []

    async def close(self) -> None:
        return None


def _cfg(*servers: ServerConfig) -> ChatClientConfig:
    return ChatClientConfig(
        llm=LLMConfig(
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
            model="gpt-4o-mini",
        ),
        servers=list(servers),
        system_prompt="system",
    )


@pytest.mark.asyncio
async def test_doctor_skips_http_probe_for_stdio_server(monkeypatch, tmp_path: Path) -> None:
    http_calls: list[str] = []

    async def _fake_http_probe(url: str, timeout_s: float = 2.0):  # noqa: ARG001
        http_calls.append(url)
        return _CheckResult(ok=True, name=url, detail="ok")

    monkeypatch.setattr(
        "comfy_mcp.tui_client.script_runner.load_config",
        lambda _path: _cfg(ServerConfig(name="editorial", transport="stdio", command="python", cwd=str(tmp_path))),
    )
    monkeypatch.setattr("comfy_mcp.tui_client.script_runner._http_probe", _fake_http_probe)
    monkeypatch.setattr("comfy_mcp.tui_client.script_runner._build_router", lambda _servers: _FakeRouter())

    result = await _doctor("cfg.json")
    assert result == 0
    assert http_calls == []


@pytest.mark.asyncio
async def test_doctor_runs_http_probe_for_http_server(monkeypatch) -> None:
    http_calls: list[str] = []

    async def _fake_http_probe(url: str, timeout_s: float = 2.0):  # noqa: ARG001
        http_calls.append(url)
        return _CheckResult(ok=True, name=url, detail="ok")

    monkeypatch.setattr(
        "comfy_mcp.tui_client.script_runner.load_config",
        lambda _path: _cfg(ServerConfig(name="photarium", transport="http", http_url="http://127.0.0.1:8787")),
    )
    monkeypatch.setattr("comfy_mcp.tui_client.script_runner._http_probe", _fake_http_probe)
    monkeypatch.setattr("comfy_mcp.tui_client.script_runner._build_router", lambda _servers: _FakeRouter())

    result = await _doctor("cfg.json")
    assert result == 0
    assert http_calls == ["http://127.0.0.1:8787/health"]


@pytest.mark.asyncio
async def test_doctor_validates_stdio_photarium_and_probes_backend(monkeypatch, tmp_path: Path) -> None:
    http_calls: list[str] = []

    async def _fake_http_probe(url: str, timeout_s: float = 2.0):  # noqa: ARG001
        http_calls.append(url)
        return _CheckResult(ok=True, name=url, detail="ok")

    monkeypatch.setattr(
        "comfy_mcp.tui_client.script_runner.load_config",
        lambda _path: _cfg(
            ServerConfig(
                name="photarium",
                transport="stdio",
                command="node",
                cwd=str(tmp_path),
                env={"PHOTARIUM_BASE_URL": "http://127.0.0.1:3000"},
            )
        ),
    )
    monkeypatch.setattr("comfy_mcp.tui_client.script_runner._http_probe", _fake_http_probe)
    monkeypatch.setattr("comfy_mcp.tui_client.script_runner._build_router", lambda _servers: _FakeRouter())

    result = await _doctor("cfg.json")
    assert result == 0
    assert http_calls == ["http://127.0.0.1:3000/api/images?limit=1"]
