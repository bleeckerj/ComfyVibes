from __future__ import annotations

import pytest

from comfy_mcp.tui_client.config import ServerConfig
from comfy_mcp.tui_client.startup_diagnostics import StartupDiagnosticsRenderer


class _FakeRouter:
    def list_tool_specs(self):
        return []


class _FakeApp:
    def __init__(self) -> None:
        self._router = _FakeRouter()
        self._all_servers = [
            ServerConfig(
                name="backoffice",
                transport="stdio",
                command="python",
                tool_prefixes=["backoffice_"],
            )
        ]
        self.chat_lines: list[str] = []
        self.tool_lines: list[str] = []

    def _write_chat(self, _markup_text: str, plain_text: str) -> None:
        self.chat_lines.append(plain_text)

    def _write_tools(self, _markup_text: str, plain_text: str) -> None:
        self.tool_lines.append(plain_text)


@pytest.mark.asyncio
async def test_tool_exposure_warnings_are_persisted_to_chat_and_tools_panes() -> None:
    app = _FakeApp()

    await StartupDiagnosticsRenderer().render_tool_exposure_warnings(app)

    assert any("Startup warnings:" in line for line in app.chat_lines)
    assert any("Startup warnings:" in line for line in app.tool_lines)
    assert any("zero tools after prefix filtering" in line for line in app.chat_lines)
    assert any("zero tools after prefix filtering" in line for line in app.tool_lines)
    assert any("Tools pane and session log" in line for line in app.chat_lines)
