from __future__ import annotations

import os

from comfy_mcp.tui_client.app import ChatApp
from comfy_mcp.tui_client.config import ChatClientConfig, LLMConfig, ServerConfig
from comfy_mcp.tui_client.mcp_router import ToolSpec


def _config():
    return ChatClientConfig(
        llm=LLMConfig(
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
            model="gpt-4o-mini",
        ),
        servers=[ServerConfig(name="comfy", command="python")],
        system_prompt="system",
        strict_tool_facts=True,
    )


def test_to_openai_tool(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    spec = ToolSpec(name="workflows.list", description="List workflows", input_schema={"type": "object"})
    tool = app._to_openai_tool(spec)
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "workflows.list"
    assert tool["function"]["description"] == "List workflows"
    assert tool["function"]["parameters"] == {"type": "object"}


def test_copy_actions(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._chat_history = ["You: hi", "Assistant: hello"]
    app._tool_history = ["Tool call: workflows.list", '{"ok": true}']
    copied = []
    monkeypatch.setattr(app, "copy_to_clipboard", lambda text: copied.append(text))
    monkeypatch.setattr(app, "notify", lambda *args, **kwargs: None)

    app.action_copy_chat()
    app.action_copy_tools()
    app.action_copy_all()

    assert "You: hi" in copied[0]
    assert "Tool call: workflows.list" in copied[1]
    assert "=== Chat ===" in copied[2]
    assert "=== Tools ===" in copied[2]


def test_config_parse_strict_tool_facts_default(tmp_path):
    from comfy_mcp.tui_client.config import load_config

    payload = """
{
  "llm": {"base_url":"https://api.openai.com/v1","api_key_env":"OPENAI_API_KEY","model":"gpt-4o-mini"},
  "servers": []
}
"""
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text(payload)
    cfg = load_config(cfg_file)
    assert cfg.strict_tool_facts is True


def test_build_transcript(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._chat_history = ["You: hi"]
    app._tool_history = ['{"ok": true}']
    text = app._build_transcript()
    assert "=== Chat ===" in text
    assert "You: hi" in text
    assert "=== Tools ===" in text
