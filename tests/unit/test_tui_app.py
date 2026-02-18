from __future__ import annotations

import asyncio
import os
from pathlib import Path

from comfy_mcp.tui_client.app import ChatApp
from comfy_mcp.tui_client.config import ChatClientConfig, LLMConfig, ServerConfig
from comfy_mcp.tui_client.mcp_router import ToolSpec


class _FakeDoc:
    def __init__(self, text: str) -> None:
        self.line_count = len(text.splitlines()) if text else 1


class _FakeInput:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.document = _FakeDoc(text)
        self.cursor_location = (0, 0)

    def load_text(self, value: str) -> None:
        self.text = value
        self.document = _FakeDoc(value)

    def move_cursor(self, location):  # noqa: ANN001
        self.cursor_location = location

    def clear(self) -> None:
        self.load_text("")


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
    # Intercept the native clipboard helper so it captures text instead of calling pbcopy
    import comfy_mcp.tui_client.app as app_mod
    monkeypatch.setattr(app_mod, "_clipboard_copy", lambda text: (copied.append(text) or True))
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
    assert cfg.tool_description_hints == {}


def test_config_appends_prompt_policies(tmp_path):
    from comfy_mcp.tui_client.config import load_config

    payload = """
{
  "llm": {"base_url":"https://api.openai.com/v1","api_key_env":"OPENAI_API_KEY","model":"gpt-4o-mini"},
  "servers": [],
  "system_prompt": "custom prompt"
}
"""
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text(payload)
    cfg = load_config(cfg_file)
    assert "POLICY::workflow_output_reliability" in cfg.system_prompt
    assert "WORKFLOW OUTPUT RELIABILITY:" in cfg.system_prompt
    assert "POLICY::aspect_ratio_preservation" in cfg.system_prompt
    assert "WORKFLOW ASPECT RATIO PRESERVATION:" in cfg.system_prompt
    assert "POLICY::tool_discovery" in cfg.system_prompt
    assert "TOOL DISCOVERY AND CAPABILITY CHECK:" in cfg.system_prompt
    assert "custom prompt" in cfg.system_prompt
    assert "Treat 'catalog' and 'photo catalog'" in cfg.system_prompt
    assert "treat 'image' or 'image id' as a Photarium catalog image id" in cfg.system_prompt
    assert "including both explicit overrides and key defaults" in cfg.system_prompt
    assert "preserve the input image aspect ratio" in cfg.system_prompt
    assert "nearest allowed ratio to the input image ratio" in cfg.system_prompt
    assert "Before claiming a capability does not exist" in cfg.system_prompt
    assert "always surface image IDs clearly" in cfg.system_prompt
    assert "convert natural color language to canonical RGB hex" in cfg.system_prompt


def test_config_does_not_duplicate_existing_policy_and_keeps_order(tmp_path):
    from comfy_mcp.tui_client.config import load_config

    payload = """
{
  "llm": {"base_url":"https://api.openai.com/v1","api_key_env":"OPENAI_API_KEY","model":"gpt-4o-mini"},
  "servers": [],
  "system_prompt": "custom prompt\\n\\n[POLICY::workflow_output_reliability]\\nWORKFLOW OUTPUT RELIABILITY:\\n- existing guidance."
}
"""
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text(payload)
    cfg = load_config(cfg_file)
    assert cfg.system_prompt.count("POLICY::workflow_output_reliability") == 1
    assert cfg.system_prompt.count("WORKFLOW OUTPUT RELIABILITY:") == 1
    assert cfg.system_prompt.count("POLICY::aspect_ratio_preservation") == 1
    assert cfg.system_prompt.count("WORKFLOW ASPECT RATIO PRESERVATION:") == 1
    assert cfg.system_prompt.count("POLICY::tool_discovery") == 1
    assert cfg.system_prompt.count("TOOL DISCOVERY AND CAPABILITY CHECK:") == 1
    assert cfg.system_prompt.index("WORKFLOW OUTPUT RELIABILITY:") < cfg.system_prompt.index(
        "WORKFLOW ASPECT RATIO PRESERVATION:"
    )
    assert cfg.system_prompt.index("WORKFLOW ASPECT RATIO PRESERVATION:") < cfg.system_prompt.index(
        "TOOL DISCOVERY AND CAPABILITY CHECK:"
    )


def test_to_openai_tool_appends_tool_description_hint(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    config = _config()
    config.tool_description_hints = {
        "photarium_search": "Use for semantic and keyword image retrieval.",
        "photarium_*": "Photarium tools can query the image catalog.",
    }
    app = ChatApp(config)
    spec = ToolSpec(name="photarium_search", description="Search Photarium catalog.", input_schema={"type": "object"})
    tool = app._to_openai_tool(spec)
    description = tool["function"]["description"]
    assert "Search Photarium catalog." in description
    assert "Use for semantic and keyword image retrieval." in description
    assert "Photarium tools can query the image catalog." in description


def test_build_transcript(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._chat_history = ["You: hi"]
    app._tool_history = ['{"ok": true}']
    text = app._build_transcript()
    assert "=== Chat ===" in text
    assert "You: hi" in text
    assert "=== Tools ===" in text


def test_process_message_does_not_duplicate_assistant_content(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    chat_lines: list[str] = []
    tool_lines: list[str] = []

    app._write_chat = lambda _markup, plain: chat_lines.append(plain)
    app._write_tools = lambda _markup, plain: tool_lines.append(plain)

    async def _fake_process(user_text, on_progress=None, stop_after_tool_calls=False):  # noqa: ANN001
        assert user_text == "hi"
        assert stop_after_tool_calls is True
        if on_progress:
            on_progress("llm_request", {"round": 1})
            on_progress(
                "llm_response",
                {
                    "content": "hello from model",
                    "tool_names": [],
                    "tool_call_count": 0,
                },
            )
        return "hello from model", []

    app._orchestrator.process = _fake_process

    asyncio.run(app._process_message("hi"))

    assert chat_lines.count("Assistant: hello from model") == 1


def test_reset_command_clears_context(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._chat_history = ["You: hi", "Assistant: hello"]
    app._tool_history = ["Tool call: workflows.list"]
    app._orchestrator._messages.extend(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
    )

    chat_lines: list[str] = []
    monkeypatch.setattr(app, "_clear_logs_widgets", lambda: None)
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    handled = app._handle_local_command("/reset")

    assert handled is True
    assert len(app._orchestrator._messages) == 1
    assert app._tool_history == []
    assert chat_lines[-1] == "Conversation reset. Context cleared."


def test_help_command_outputs_command_list(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    handled = app._handle_local_command("/help")

    assert handled is True
    assert any("/help" in line for line in chat_lines)
    assert any("/status" in line for line in chat_lines)
    assert any("/reset" in line for line in chat_lines)
    assert any("/aspect" in line for line in chat_lines)
    assert any("/tanktracks" in line for line in chat_lines)


def test_status_command_outputs_summary(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    app._tools = [{"type": "function", "function": {"name": "workflows.list"}}]
    app._chat_history = ["You: hi"]
    app._tool_history = ["Tool call: workflows.list"]
    app._orchestrator._messages.extend(
        [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
    )
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    handled = app._handle_local_command("/status")

    assert handled is True
    assert any("Session status:" in line for line in chat_lines)
    assert any("ready: True" in line for line in chat_lines)
    assert any("model:" in line for line in chat_lines)
    assert any("discovered tools: 1" in line for line in chat_lines)


def test_moodboard_command_shows_usage_without_brief(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    handled = app._handle_local_command("/moodboard")

    assert handled is True
    assert any("Moodboard Flow Usage" in line for line in chat_lines)
    assert any("/moodboard <brief text>" in line for line in chat_lines)


def test_moodboard_command_explicit_help_variants_show_usage(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    commands = ["/moodboard help", "/moodboard -h", "/moodboard --help"]
    for command in commands:
        chat_lines: list[str] = []
        app._write_chat = lambda _markup, plain: chat_lines.append(plain)
        handled = app._handle_local_command(command)
        assert handled is True
        assert any("Moodboard Flow Usage" in line for line in chat_lines)
        assert any("/moodboard <brief text>" in line for line in chat_lines)


def test_moodboard_command_builds_agentic_flow_prompt(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    captured: list[str] = []
    app._process_message = lambda user_text: captured.append(user_text) or None  # type: ignore[method-assign]
    app.run_worker = lambda _task, exclusive=False: None

    handled = app._handle_local_command(
        "/moodboard editorial denim campaign count=16 palette=#87CEEB refs=id1,id2 workflow=image_edit"
    )

    assert handled is True
    assert any("Moodboard Flow:" in line for line in chat_lines)
    assert len(captured) == 1
    prompt = captured[0]
    assert "MOODBOARD FLOW REQUEST" in prompt
    assert "Target candidate count: 16" in prompt
    assert "Palette hint: #87CEEB" in prompt
    assert "Starter reference image IDs: id1, id2" in prompt
    assert "Workflow preference: image_edit" in prompt
    assert "Novelty target: high" in prompt


def test_aspect_command_shows_usage_without_required_fields(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    handled = app._handle_local_command("/aspect")

    assert handled is True
    assert any("Aspect Flow Usage" in line for line in chat_lines)
    assert any("/aspect <image_id> targets=" in line for line in chat_lines)


def test_aspect_command_explicit_help_variants_show_usage(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    commands = ["/aspect help", "/aspect -h", "/aspect --help"]
    for command in commands:
        chat_lines: list[str] = []
        app._write_chat = lambda _markup, plain: chat_lines.append(plain)
        handled = app._handle_local_command(command)
        assert handled is True
        assert any("Aspect Flow Usage" in line for line in chat_lines)
        assert any("/aspect <image_id> targets=" in line for line in chat_lines)


def test_aspect_command_builds_agentic_flow_prompt(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    captured: list[str] = []
    app._process_message = lambda user_text: captured.append(user_text) or None  # type: ignore[method-assign]
    app.run_worker = lambda _task, exclusive=False: None

    source = "75e92a7e-2838-45a7-6f2c-32a5fde6c300"
    handled = app._handle_local_command(
        f'/aspect {source} targets=16:9,4:5,3:2,9:16 source=1:1 max_delta=0.4 preserve="Keep scene fixed"'
    )

    assert handled is True
    assert any("Aspect Flow:" in line for line in chat_lines)
    assert len(captured) == 1
    prompt = captured[0]
    assert "ASPECT RATIO FLOW REQUEST" in prompt
    assert f"Source catalog image ID: {source}" in prompt
    assert "Requested target aspect ratios: 16:9, 4:5, 3:2, 9:16" in prompt
    assert "Source ratio hint: 1:1" in prompt
    assert "Workflow preference: aspect_ratio_adjustment" in prompt
    assert "Max safe per-step ratio delta (log-space): 0.40" in prompt
    assert "Positive preservation guidance: Keep scene fixed" in prompt


def test_tanktracks_command_shows_usage_without_image_id(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    handled = app._handle_local_command("/tanktracks")

    assert handled is True
    assert any("Tank Tracks Flow Usage" in line for line in chat_lines)
    assert any("/tanktracks <image_id>" in line for line in chat_lines)


def test_tanktracks_command_explicit_help_variants_show_usage(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    commands = ["/tanktracks help", "/tanktracks -h", "/tanktracks --help"]
    for command in commands:
        chat_lines: list[str] = []
        app._write_chat = lambda _markup, plain: chat_lines.append(plain)
        handled = app._handle_local_command(command)
        assert handled is True
        assert any("Tank Tracks Flow Usage" in line for line in chat_lines)
        assert any("/tanktracks <image_id>" in line for line in chat_lines)


def test_tanktracks_command_builds_agentic_flow_prompt(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    captured: list[str] = []
    app._process_message = lambda user_text: captured.append(user_text) or None  # type: ignore[method-assign]
    app.run_worker = lambda _task, exclusive=False: None

    source = "1cc224eb-022b-4ce9-0dd8-3f274f4f4300"
    handled = app._handle_local_command(f'/tanktracks {source} prompt="Replace wheels with tank tracks"')

    assert handled is True
    assert any("Tank Tracks Flow:" in line for line in chat_lines)
    assert len(captured) == 1
    prompt = captured[0]
    assert "TANK TRACKS FLOW REQUEST" in prompt
    assert f"Source catalog image ID: {source}" in prompt
    assert f"Upload variant under image ID: {source}" in prompt
    assert "Workflow preference: add_tank_tracks" in prompt
    assert "Prompt override: Replace wheels with tank tracks" in prompt


def test_session_log_file_is_date_stamped_and_appendable(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.chdir(tmp_path)
    app = ChatApp(_config())

    app._init_session_log()
    assert app._session_log_path is not None
    assert app._session_log_path.exists()
    assert app._session_log_path.parent == (tmp_path / ".mcp_chat_logs")
    assert app._session_log_path.name.startswith("chat_")
    assert app._session_log_path.name.endswith(".log")

    app._append_session_log("CHAT", "hello world")
    payload = app._session_log_path.read_text(encoding="utf-8")
    assert "Session started:" in payload
    assert "CHAT: hello world" in payload


def test_prompt_history_navigation_prev_next(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    fake_input = _FakeInput("draft")
    monkeypatch.setattr(app, "query_one", lambda *_args, **_kwargs: fake_input)

    app._record_prompt_history("first")
    app._record_prompt_history("second")
    app._record_prompt_history("third")

    app._history_prev()
    assert fake_input.text == "third"
    app._history_prev()
    assert fake_input.text == "second"
    app._history_prev()
    assert fake_input.text == "first"
    app._history_prev()
    assert fake_input.text == "first"

    app._history_next()
    assert fake_input.text == "second"
    app._history_next()
    assert fake_input.text == "third"
    app._history_next()
    assert fake_input.text == "draft"
    app._history_next()
    assert fake_input.text == "draft"


def test_submit_records_prompt_history_and_resets_navigation(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    fake_input = _FakeInput("repeat me")
    monkeypatch.setattr(app, "query_one", lambda *_args, **_kwargs: fake_input)

    submitted: list[str] = []
    monkeypatch.setattr(app, "_submit_user_text", lambda text: submitted.append(text))

    app.action_submit_prompt()
    app.action_submit_prompt()  # empty after clear -> ignored
    fake_input.load_text("repeat me")
    app.action_submit_prompt()  # duplicate should not be re-added
    fake_input.load_text("another")
    app.action_submit_prompt()

    assert submitted == ["repeat me", "repeat me", "another"]
    assert app._prompt_history == ["repeat me", "another"]
    assert app._prompt_history_cursor is None
    assert app._prompt_history_draft == ""
