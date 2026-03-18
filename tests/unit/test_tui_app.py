from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from comfy_mcp.tui_client.app import (
    ChatApp,
    _is_known_stdio_asyncgen_shutdown_context,
    _is_known_stdio_asyncgen_unraisable,
    _normalize_openai_tool_schema,
    _strip_border_glyphs,
    _suppress_known_stdio_asyncgen_unraisables,
)
from comfy_mcp.tui_client.config import ChatClientConfig, LLMConfig, ServerConfig, compose_runtime_system_prompt
from comfy_mcp.tui_client.mcp_router import ToolSpec
from comfy_mcp.tui_client.orchestrator import ToolEvent


class _FakeDoc:
    def __init__(self, text: str) -> None:
        self.line_count = len(text.splitlines()) if text else 1


class _FakeStyles:
    def __init__(self, height: int = 6) -> None:
        self.height = height


class _FakeInput:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.document = _FakeDoc(text)
        self.cursor_location = (0, 0)
        self.styles = _FakeStyles()
        self.word_left_calls = 0
        self.word_right_calls = 0
        self.delete_word_left_calls = 0
        self.delete_word_right_calls = 0

    def load_text(self, value: str) -> None:
        self.text = value
        self.document = _FakeDoc(value)

    def move_cursor(self, location):  # noqa: ANN001
        self.cursor_location = location

    def clear(self) -> None:
        self.load_text("")

    def action_cursor_word_left(self) -> None:
        self.word_left_calls += 1

    def action_cursor_word_right(self) -> None:
        self.word_right_calls += 1

    def action_delete_word_left(self) -> None:
        self.delete_word_left_calls += 1

    def action_delete_word_right(self) -> None:
        self.delete_word_right_calls += 1


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


def test_chat_app_keeps_orchestrator_router_in_sync_after_router_rebuild(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    class _Router:
        pass

    routers = [_Router(), _Router()]

    import comfy_mcp.tui_client.app as app_mod

    def _fake_build_router(_config):  # noqa: ANN001
        return routers.pop(0)

    monkeypatch.setattr(app_mod, "_build_router", _fake_build_router)

    app = ChatApp(_config())

    assert app._router is app._orchestrator._router


@pytest.mark.asyncio
async def test_on_shutdown_waits_for_router_close_even_if_cancelled(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())

    started = asyncio.Event()
    allow_finish = asyncio.Event()
    finished = asyncio.Event()

    class _Router:
        async def close(self) -> None:
            started.set()
            await allow_finish.wait()
            finished.set()

    app._router = _Router()
    task = asyncio.create_task(app.on_shutdown())
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    allow_finish.set()
    await task
    assert finished.is_set()


def test_is_known_stdio_asyncgen_unraisable_matches_expected_shape():
    unraisable = SimpleNamespace(
        err_msg="an error occurred during closing of asynchronous generator",
        object="<async_generator object stdio_client at 0x1234>",
        exc_value=RuntimeError("Attempted to exit cancel scope in a different task than it was entered in"),
    )
    assert _is_known_stdio_asyncgen_unraisable(unraisable) is True


def test_is_known_stdio_asyncgen_unraisable_rejects_other_unraisables():
    unraisable = SimpleNamespace(
        err_msg="some other error",
        object="<async_generator object something_else at 0x1234>",
        exc_value=RuntimeError("boom"),
    )
    assert _is_known_stdio_asyncgen_unraisable(unraisable) is False


def test_is_known_stdio_asyncgen_shutdown_context_matches_expected_shape():
    context = {
        "message": "an error occurred during closing of asynchronous generator",
        "asyncgen": "<async_generator object stdio_client at 0x1234>",
        "exception": RuntimeError("Attempted to exit cancel scope in a different task than it was entered in"),
    }
    assert _is_known_stdio_asyncgen_shutdown_context(context) is True


def test_suppress_known_stdio_asyncgen_unraisables_restores_previous_hook(monkeypatch):
    calls: list[object] = []

    def _previous(unraisable):  # noqa: ANN001
        calls.append(unraisable)

    monkeypatch.setattr("sys.unraisablehook", _previous)

    suppressed = SimpleNamespace(
        err_msg="an error occurred during closing of asynchronous generator",
        object="<async_generator object stdio_client at 0x1234>",
        exc_value=RuntimeError("Attempted to exit cancel scope in a different task than it was entered in"),
    )
    forwarded = SimpleNamespace(
        err_msg="other unraisable",
        object="<object object at 0x5678>",
        exc_value=RuntimeError("boom"),
    )

    with _suppress_known_stdio_asyncgen_unraisables():
        hook = sys.unraisablehook
        hook(suppressed)
        hook(forwarded)

    assert calls == [forwarded]
    assert sys.unraisablehook is _previous


@pytest.mark.asyncio
async def test_install_loop_exception_handler_suppresses_known_stdio_asyncgen_context(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    loop = asyncio.get_running_loop()
    forwarded: list[dict[str, object]] = []

    def _previous(_loop, context):  # noqa: ANN001
        forwarded.append(context)

    loop.set_exception_handler(_previous)
    app._install_loop_exception_handler()
    handler = loop.get_exception_handler()
    assert handler is not None

    suppressed = {
        "message": "an error occurred during closing of asynchronous generator",
        "asyncgen": "<async_generator object stdio_client at 0x1234>",
        "exception": RuntimeError("Attempted to exit cancel scope in a different task than it was entered in"),
    }
    forwarded_context = {
        "message": "ordinary loop error",
        "exception": RuntimeError("boom"),
    }

    handler(loop, suppressed)
    handler(loop, forwarded_context)

    assert forwarded == [forwarded_context]
    loop.set_exception_handler(None)


def test_to_openai_tool_normalizes_array_items(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    spec = ToolSpec(
        name="backoffice_build_content_source_pack",
        description="Build source pack",
        input_schema={
            "type": "object",
            "properties": {
                "collections": {
                    "type": "array",
                }
            },
        },
    )
    tool = app._to_openai_tool(spec)
    collections = tool["function"]["parameters"]["properties"]["collections"]
    assert collections["type"] == "array"
    assert collections["items"] == {}


def test_to_openai_tool_repairs_workflows_run_overrides_schema(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    spec = ToolSpec(
        name="workflows_run",
        description="Run workflow",
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "overrides": {"type": "object", "additionalProperties": False},
            },
            "required": ["workflow_id"],
        },
    )
    tool = app._to_openai_tool(spec)
    parameters = tool["function"]["parameters"]
    overrides = parameters["properties"]["overrides"]
    assert overrides["type"] == "object"
    assert overrides["additionalProperties"] is True
    assert "workflow_id" in parameters["required"]
    assert "overrides" in parameters["required"]


def test_normalize_openai_tool_schema_falls_back_for_invalid_root():
    normalized = _normalize_openai_tool_schema(None)
    assert normalized == {"type": "object", "properties": {}}


def test_copy_actions(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._chat_history = ["You: hi", "EDGAR: hello"]
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


def test_strip_border_glyphs_removes_textual_frame_chars():
    bordered = "\n".join(
        [
            "│ You: hi │",
            "│ EDGAR: Generated output ▆│",
            "  │ Tool call: editorial_ads_create_from_image │",
        ]
    )
    cleaned = _strip_border_glyphs(bordered)
    assert cleaned.splitlines() == [
        "You: hi",
        "EDGAR: Generated output",
        "Tool call: editorial_ads_create_from_image",
    ]


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


def test_config_parse_infers_http_transport_from_http_url(tmp_path):
    from comfy_mcp.tui_client.config import load_config

    payload = """
{
  "llm": {"base_url":"https://api.openai.com/v1","api_key_env":"OPENAI_API_KEY","model":"gpt-4o-mini"},
  "servers": [
    {"name":"photarium","http_url":"http://127.0.0.1:8787"}
  ]
}
"""
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text(payload)
    cfg = load_config(cfg_file)
    assert cfg.servers[0].transport == "http"


def test_config_parse_infers_stdio_transport_from_command_only(tmp_path):
    from comfy_mcp.tui_client.config import load_config

    payload = """
{
  "llm": {"base_url":"https://api.openai.com/v1","api_key_env":"OPENAI_API_KEY","model":"gpt-4o-mini"},
  "servers": [
    {"name":"digester","command":"python","args":["mcp_digester_server.py"]}
  ]
}
"""
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text(payload)
    cfg = load_config(cfg_file)
    assert cfg.servers[0].transport == "stdio"


def test_config_parse_rejects_http_server_without_url(tmp_path):
    from comfy_mcp.tui_client.config import load_config

    payload = """
{
  "llm": {"base_url":"https://api.openai.com/v1","api_key_env":"OPENAI_API_KEY","model":"gpt-4o-mini"},
  "servers": [
    {"name":"photarium","transport":"http"}
  ]
}
"""
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text(payload)
    with pytest.raises(ValueError, match="http_url"):
        load_config(cfg_file)


def test_config_parse_rejects_stdio_server_without_command(tmp_path):
    from comfy_mcp.tui_client.config import load_config

    payload = """
{
  "llm": {"base_url":"https://api.openai.com/v1","api_key_env":"OPENAI_API_KEY","model":"gpt-4o-mini"},
  "servers": [
    {"name":"backoffice","transport":"stdio"}
  ]
}
"""
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text(payload)
    with pytest.raises(ValueError, match="no command"):
        load_config(cfg_file)


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
    assert "# EDGAR Orchestrator Routing Policy (Editorial vs Workflow Tools)" in cfg.system_prompt
    assert "Use this text inside EDGAR's orchestrator/system prompt." in cfg.system_prompt
    assert "custom prompt" in cfg.system_prompt
    assert "Treat 'catalog' and 'photo catalog'" in cfg.system_prompt
    assert "treat 'image' or 'image id' as a Photarium catalog image id" in cfg.system_prompt
    assert "For workflows_run, always pass an explicit overrides object" in cfg.system_prompt
    assert "use a local file path or Comfy input filename, not a Photarium UUID" in cfg.system_prompt
    assert "When user asks for a sweep/range" in cfg.system_prompt
    assert "including both explicit overrides and key defaults" in cfg.system_prompt
    assert "tags are semantic image-content labels only" in cfg.system_prompt
    assert "Default upload behavior: omit tags unless the user explicitly asks for tags" in cfg.system_prompt
    assert "preserve the input image aspect ratio" in cfg.system_prompt
    assert "nearest allowed ratio to the input image ratio" in cfg.system_prompt
    assert "Before claiming a capability does not exist" in cfg.system_prompt
    assert "always surface image IDs clearly" in cfg.system_prompt
    assert "convert natural color language to canonical RGB hex" in cfg.system_prompt
    assert "editorial_content_create_stub" in cfg.system_prompt
    assert "Use source-repo read tools (or read-only file access)" in cfg.system_prompt


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
    assert cfg.system_prompt.count("# EDGAR Orchestrator Routing Policy (Editorial vs Workflow Tools)") == 1
    assert cfg.system_prompt.index("WORKFLOW OUTPUT RELIABILITY:") < cfg.system_prompt.index(
        "WORKFLOW ASPECT RATIO PRESERVATION:"
    )
    assert cfg.system_prompt.index("WORKFLOW ASPECT RATIO PRESERVATION:") < cfg.system_prompt.index(
        "TOOL DISCOVERY AND CAPABILITY CHECK:"
    )
    assert cfg.system_prompt.index("TOOL DISCOVERY AND CAPABILITY CHECK:") < cfg.system_prompt.index(
        "# EDGAR Orchestrator Routing Policy (Editorial vs Workflow Tools)"
    )


def test_compose_runtime_system_prompt_replaces_verbosity_policy_without_duplication():
    prompt = compose_runtime_system_prompt("custom prompt", "quiet")
    assert prompt.count("[POLICY::response_verbosity]") == 1
    assert "Active mode: quiet." in prompt

    updated = compose_runtime_system_prompt(prompt, "loud")
    assert updated.count("[POLICY::response_verbosity]") == 1
    assert "Active mode: loud." in updated
    assert "Active mode: quiet." not in updated


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


def test_write_tools_clips_oversized_payload_for_widget(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())

    class _FakeLog:
        def __init__(self) -> None:
            self.lines: list[str] = []

        def write(self, text: str) -> None:
            self.lines.append(text)

    fake_log = _FakeLog()
    app._tool_log_widget = fake_log  # type: ignore[assignment]

    oversized = "x" * (app._LOG_RENDER_MAX_CHARS + 200)
    app._write_tools(oversized, oversized)

    assert len(fake_log.lines) == 1
    assert "truncated for UI" in fake_log.lines[0]
    assert "truncated for UI" in app._tool_history[-1]
    assert len(app._tool_history[-1]) < len(oversized)


def test_low_churn_mode_defaults_on(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("EDGAR_TUI_LOW_CHURN", raising=False)
    app = ChatApp(_config())
    assert app._low_churn_mode is True


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

    assert chat_lines.count("EDGAR: hello from model") == 1


def test_process_message_all_tool_failures_reports_failure(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    config = _config()
    config.strict_tool_facts = False
    app = ChatApp(config)
    chat_lines: list[str] = []
    tool_lines: list[str] = []

    app._write_chat = lambda _markup, plain: chat_lines.append(plain)
    app._write_tools = lambda _markup, plain: tool_lines.append(plain)

    async def _fake_process(user_text, on_progress=None, stop_after_tool_calls=False):  # noqa: ANN001
        assert user_text == "run it"
        assert stop_after_tool_calls is False
        return (
            "Looks good, completed.",
            [
                ToolEvent(
                    name="workflows_run_aspect_ratio_adjustment",
                    arguments={"workflow_id": "aspect_ratio_adjustment"},
                    result=None,
                    error="HTTP 503: ComfyUI unavailable",
                )
            ],
        )

    app._orchestrator.process = _fake_process

    asyncio.run(app._process_message("run it"))

    assert any("Tool execution failed: no successful tool results were produced." in line for line in chat_lines)
    assert any("workflows_run_aspect_ratio_adjustment: HTTP 503: ComfyUI unavailable" in line for line in chat_lines)
    assert not any(line.startswith("EDGAR: Looks good, completed.") for line in chat_lines)


def test_process_message_editorial_list_inventory_uses_tool_grounded_lines(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    config = _config()
    config.strict_tool_facts = False
    app = ChatApp(config)
    chat_lines: list[str] = []
    tool_lines: list[str] = []

    app._write_chat = lambda _markup, plain: chat_lines.append(plain)
    app._write_tools = lambda _markup, plain: tool_lines.append(plain)

    async def _fake_process(user_text, on_progress=None, stop_after_tool_calls=False):  # noqa: ANN001
        assert user_text == "list ads"
        assert stop_after_tool_calls is False
        return (
            "Here are some ads including made-up ones",
            [
                ToolEvent(
                    name="editorial_ads_list_inventory",
                    arguments={"limit": 100},
                    result={
                        "inventoryPath": "src/content/ads.json",
                        "total": 2,
                        "matched": 2,
                        "returned": 2,
                        "items": [
                            {"id": "future-newspapers", "slot": "interstitial", "title": "Subscribe to The Manual", "visibility": "always"},
                            {"id": "heirloom-llms-ad", "slot": "interstitial", "title": "Vintage, Estate, Heirloom LLMs", "sponsor": "Birnbaum Salvage & Reclamation", "visibility": "always"},
                        ],
                    },
                    error=None,
                )
            ],
        )

    app._orchestrator.process = _fake_process

    asyncio.run(app._process_message("list ads"))

    assert any("Tool-grounded editorial inventory listing" in line for line in chat_lines)
    assert any("`future-newspapers`" in line for line in chat_lines)
    assert any("`heirloom-llms-ad`" in line for line in chat_lines)
    assert not any(line.startswith("EDGAR: Here are some ads including made-up ones") for line in chat_lines)


def test_process_message_editorial_preview_uses_tool_grounded_urls(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    config = _config()
    config.strict_tool_facts = False
    app = ChatApp(config)
    chat_lines: list[str] = []
    tool_lines: list[str] = []

    app._write_chat = lambda _markup, plain: chat_lines.append(plain)
    app._write_tools = lambda _markup, plain: tool_lines.append(plain)

    async def _fake_process(user_text, on_progress=None, stop_after_tool_calls=False):  # noqa: ANN001
        assert user_text == "preview two ads"
        assert stop_after_tool_calls is False
        return (
            "These URLs might be guessed",
            [
                ToolEvent(
                    name="editorial_ads_preview",
                    arguments={"adIds": ["a", "b"]},
                    result={
                        "mode": "catalog",
                        "previewUrl": "http://127.0.0.1:8788/ads/preview?ids=a,b&limit=2",
                        "previewUrls": {
                            "catalog": "http://127.0.0.1:8788/ads/preview?ids=a,b&limit=2",
                            "single": "http://127.0.0.1:8788/ads/preview/a",
                        },
                        "selectedIds": ["a", "b"],
                        "missingRequestedIds": [],
                        "fit": "cover",
                        "actualAspect": False,
                    },
                    error=None,
                )
            ],
        )

    app._orchestrator.process = _fake_process

    asyncio.run(app._process_message("preview two ads"))

    assert any("Tool-grounded editorial preview response" in line for line in chat_lines)
    assert any(line == "Catalog URL: http://127.0.0.1:8788/ads/preview?ids=a,b&limit=2" for line in chat_lines)
    assert any(line == "2. `b`" for line in chat_lines)
    assert not any(line.startswith("EDGAR: These URLs might be guessed") for line in chat_lines)


def test_process_message_recent_signals_uses_tool_grounded_rows(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    config = _config()
    config.strict_tool_facts = False
    app = ChatApp(config)
    chat_lines: list[str] = []
    tool_lines: list[str] = []

    app._write_chat = lambda _markup, plain: chat_lines.append(plain)
    app._write_tools = lambda _markup, plain: tool_lines.append(plain)

    async def _fake_process(user_text, on_progress=None, stop_after_tool_calls=False):  # noqa: ANN001
        assert user_text == "give me the last 3 signals with summary"
        assert stop_after_tool_calls is False
        return (
            "Only got two signals.",
            [
                ToolEvent(
                    name="digester_signals_list_summaries",
                    arguments={"limit": 3},
                    result={
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "count": 3,
                                        "items": [
                                            {
                                                "signal_id": "sig-001",
                                                "headline": "First signal",
                                                "hook_preview": "First summary",
                                            },
                                            {
                                                "signal_id": "sig-002",
                                                "headline": "Second signal",
                                                "hook_preview": "Second summary",
                                            },
                                            {
                                                "signal_id": "sig-003",
                                                "headline": "Third signal",
                                                "hook_preview": "Third summary",
                                            },
                                        ],
                                    }
                                ),
                            }
                        ]
                    },
                    error=None,
                )
            ],
        )

    app._orchestrator.process = _fake_process

    asyncio.run(app._process_message("give me the last 3 signals with summary"))

    assert any("Tool-grounded recent signals" in line for line in chat_lines)
    assert any(line.startswith("1) sig-001 — First signal — First summary") for line in chat_lines)
    assert any(line.startswith("2) sig-002 — Second signal — Second summary") for line in chat_lines)
    assert any(line.startswith("3) sig-003 — Third signal — Third summary") for line in chat_lines)
    assert not any(line.startswith("EDGAR: Only got two signals.") for line in chat_lines)


def test_request_queue_serializes_prompts(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    started: list[str] = []
    release = asyncio.Event()

    async def _fake_process(user_text: str) -> None:
        started.append(user_text)
        if user_text == "first":
            await release.wait()

    app._process_message = _fake_process  # type: ignore[method-assign]

    workers: list[asyncio.Task[None]] = []

    def _run_worker(task, exclusive=False):  # noqa: ANN001
        workers.append(asyncio.create_task(task))
        return None

    app.run_worker = _run_worker  # type: ignore[method-assign]

    async def _run() -> None:
        app._enqueue_request("first")
        app._enqueue_request("second")
        await asyncio.sleep(0)
        assert started == ["first"]
        assert app._request_queue.qsize() == 1
        assert any("Prompt queued (1 ahead)." in line for line in chat_lines)
        release.set()
        await asyncio.gather(*workers)
        assert started == ["first", "second"]
        assert app._turn_state == "IDLE"

    asyncio.run(_run())


def test_reset_command_clears_context(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._chat_history = ["You: hi", "EDGAR: hello"]
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
    assert any("/showtoolstate" in line for line in chat_lines)
    assert any("/turnon" in line for line in chat_lines)
    assert any("/turnoff" in line for line in chat_lines)
    assert any("/verbosity loud|lowkey|quiet" in line for line in chat_lines)
    assert any("/aspect" in line for line in chat_lines)
    assert any("/ar" in line for line in chat_lines)
    assert any("/importwf" in line for line in chat_lines)
    assert any("/importworkflow" in line for line in chat_lines)
    assert any("/tanktracks" in line for line in chat_lines)
    assert any("/imageedit" in line for line in chat_lines)
    assert any("/vary" in line for line in chat_lines)
    assert any("Keyboard shortcuts:" in line for line in chat_lines)
    assert any("F1 / F2 focus Chat / Tools pane" in line for line in chat_lines)
    assert any("Opt+Left / Opt+Right" in line for line in chat_lines)
    assert any("Opt+Backspace / Opt+D" in line for line in chat_lines)
    assert any("Ctrl+Shift+Up / Ctrl+Shift+Down" in line for line in chat_lines)
    assert any("F6 / F7 / F8 copy Chat / Tools / All" in line for line in chat_lines)


def test_toolstate_command_outputs_server_states(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    handled = app._handle_local_command("/showtoolstate")

    assert handled is True
    assert any("Tool state (servers)" in line for line in chat_lines)
    assert any("comfy" in line for line in chat_lines)


def test_turnoff_command_disables_server_and_triggers_reconnect(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)
    reconnect_calls: list[bool] = []

    async def _fake_reconnect():
        reconnect_calls.append(True)

    app._reconnect_tools = _fake_reconnect

    def _run_worker(coro, **_kwargs):  # noqa: ANN001
        asyncio.run(coro)

    app.run_worker = _run_worker  # type: ignore[assignment]

    handled = app._handle_local_command("/turnoff comfy")

    assert handled is True
    assert app._server_enabled["comfy"] is False
    assert any("Server comfy: OFF." in line for line in chat_lines)
    assert reconnect_calls == [True]


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
    assert any("response_verbosity: lowkey" in line for line in chat_lines)
    assert any("discovered tools: 1" in line for line in chat_lines)


def test_verbosity_command_updates_runtime_system_prompt(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    handled = app._handle_local_command("/verbosity quiet")

    assert handled is True
    assert app._response_verbosity == "quiet"
    assert chat_lines[-1] == "EDGAR response mode set to quiet."
    assert "Active mode: quiet." in app._orchestrator._messages[0]["content"]


def test_natural_language_verbosity_alias_updates_runtime_system_prompt(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    handled = app._handle_local_command("EDGAR just whisper")

    assert handled is True
    assert app._response_verbosity == "quiet"
    assert chat_lines[-1] == "EDGAR response mode set to quiet."


def test_load_ui_preferences_restores_response_verbosity(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._ui_prefs_path = tmp_path / "prefs.json"
    app._ui_prefs_path.write_text(
        json.dumps(
            {
                "input_height_lines": 7,
                "prompt_history": ["first", "second"],
                "response_verbosity": "loud",
            }
        ),
        encoding="utf-8",
    )

    app._response_verbosity = "lowkey"
    app._load_ui_preferences()
    app._apply_runtime_system_prompt()

    assert app._response_verbosity == "loud"
    assert "Active mode: loud." in app._orchestrator._messages[0]["content"]


def test_startup_shows_edgar_banner(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    chat_lines: list[str] = []
    tool_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)
    app._write_tools = lambda _markup, plain: tool_lines.append(plain)

    class _Router:
        async def connect(self):
            return None

        def list_tool_specs(self):
            return []

    app._router = _Router()
    app._orchestrator.set_tools = lambda tools: None

    asyncio.run(app._startup())

    assert any("Everyday Digital Graphics Assistant Robot" in line for line in chat_lines)
    assert any("Near Future Laboratory" in line for line in chat_lines)
    assert any("Ready. Type a request below." in line for line in chat_lines)
    assert any("Tools ready:" in line for line in tool_lines)


def test_startup_renders_stdio_server_diagnostics(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    chat_lines: list[str] = []
    tool_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)
    app._write_tools = lambda _markup, plain: tool_lines.append(plain)

    class _Router:
        async def connect(self):
            return None

        def list_tool_specs(self):
            return []

        async def get_server_connection_statuses(self):
            return [
                {
                    "name": "editorial",
                    "transport": "stdio",
                    "connected": True,
                    "command": "/tmp/run_editorial_mcp_server.sh",
                    "args": [],
                    "cwd": "/tmp/editorial",
                }
            ]

    app._router = _Router()
    app._orchestrator.set_tools = lambda tools: None

    asyncio.run(app._startup())

    assert any(
        "editorial (stdio) connected=yes command=/tmp/run_editorial_mcp_server.sh, cwd=/tmp/editorial" in line
        for line in chat_lines
    )


def test_startup_renders_comfy_server_info_diagnostics(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    chat_lines: list[str] = []
    tool_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)
    app._write_tools = lambda _markup, plain: tool_lines.append(plain)

    class _Router:
        async def connect(self):
            return None

        def list_tool_specs(self):
            return [
                ToolSpec(
                    name="comfy_server_info",
                    description="Server info",
                    input_schema={"type": "object"},
                )
            ]

        async def call_tool(self, name, arguments):
            assert name == "comfy_server_info"
            return {
                "configured_base_url": "http://192.168.15.54:8188",
                "target": {
                    "host": "192.168.15.54",
                    "port": 8188,
                    "resolved_ips": ["192.168.15.54"],
                },
                "probe": {
                    "ok": False,
                    "endpoint": "http://192.168.15.54:8188/queue",
                    "error": "All connection attempts failed",
                    "latency_ms": 12.5,
                },
            }

    app._router = _Router()
    app._orchestrator.set_tools = lambda tools: None

    asyncio.run(app._startup())

    assert any("Comfy server-info:" in line for line in chat_lines)
    assert any("configured_base_url=http://192.168.15.54:8188" in line for line in chat_lines)
    assert any("resolved_ips=192.168.15.54" in line for line in chat_lines)
    assert any("probe_ok=false" in line and "All connection attempts failed" in line for line in chat_lines)
    assert any("Tools ready:" in line for line in tool_lines)


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
    app._enqueue_request = lambda user_text: captured.append(user_text)  # type: ignore[method-assign]

    handled = app._handle_local_command(
        "/moodboard editorial denim campaign count=16 palette=#87CEEB refs=id1,id2 workflow=image_edit"
    )

    assert handled is True
    assert any(
        line == "Command: /moodboard editorial denim campaign count=16 palette=#87CEEB refs=id1,id2 workflow=image_edit"
        for line in chat_lines
    )
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

    handled = app._handle_local_command("/ar")

    assert handled is True
    assert any("Aspect Flow Usage" in line for line in chat_lines)
    assert any("/aspect|/ar <image_id> targets=" in line for line in chat_lines)


def test_aspect_command_explicit_help_variants_show_usage(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    commands = [
        "/aspect help",
        "/aspect -h",
        "/aspect --help",
        "/ar help",
        "/ar -h",
        "/ar --help",
        "/aspectratio help",
        "/aspectratio -h",
        "/aspectratio --help",
    ]
    for command in commands:
        chat_lines: list[str] = []
        app._write_chat = lambda _markup, plain: chat_lines.append(plain)
        handled = app._handle_local_command(command)
        assert handled is True
        assert any("Aspect Flow Usage" in line for line in chat_lines)
        assert any("/aspect|/ar <image_id> targets=" in line for line in chat_lines)


def test_aspect_command_builds_agentic_flow_prompt(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    captured: list[str] = []
    app._enqueue_request = lambda user_text: captured.append(user_text)  # type: ignore[method-assign]

    source = "75e92a7e-2838-45a7-6f2c-32a5fde6c300"
    handled = app._handle_local_command(
        f'/ar {source} targets=16:9,4:5,3:2,9:16 source=1:1 max_delta=0.4 preserve="Keep scene fixed"'
    )

    assert handled is True
    assert any("Aspect Flow:" in line for line in chat_lines)
    assert len(captured) == 1
    prompt = captured[0]
    assert "ASPECT RATIO FLOW REQUEST" in prompt
    assert f"Source catalog image ID: {source}" in prompt
    assert "Requested target aspect ratios: 16:9, 4:5, 3:2, 9:16" in prompt
    assert "Source ratio hint: 1:1" in prompt
    assert f"Requested upload target image ID: {source}" in prompt
    assert "Workflow preference: aspect_ratio_adjustment" in prompt
    assert "Max safe per-step ratio delta (log-space): 0.40" in prompt
    assert "Positive preservation guidance: Keep scene fixed" in prompt
    assert "Resolve effective upload parent:" in prompt
    assert "If upload to requested target fails parent/variant validation" in prompt
    assert "independent branch from the same original source image" in prompt
    assert "never use one target branch output as another target's input" in prompt
    assert "Do not call workflows_params_get, workflows_get, or tool_schema_get" in prompt
    assert "Do not use photarium_import_url(includeData=true) -> photarium_upload_image" in prompt


def test_aspectratio_command_parses_target_denoise_and_seed_sweep_words(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    captured: list[str] = []
    app._enqueue_request = lambda user_text: captured.append(user_text)  # type: ignore[method-assign]
    monkeypatch.setattr(app, "_generate_random_seed_values", lambda count: [910001, 910002, 910003])

    source = "72494487-4a12-45fb-4084-260e16125000"
    handled = app._handle_local_command(
        f"/aspectratio {source} target=9:16 denoise 1 sweep three different seed values for KSampler"
    )

    assert handled is True
    assert any("Aspect Flow:" in line for line in chat_lines)
    assert len(captured) == 1
    prompt = captured[0]
    assert f"Source catalog image ID: {source}" in prompt
    assert "Requested target aspect ratios: 9:16" in prompt
    assert "Denoise override: 1.000" in prompt
    assert "Seed sweep request: 910001, 910002, 910003" in prompt


def test_aspect_command_accepts_target_without_equals(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    captured: list[str] = []
    app._enqueue_request = lambda user_text: captured.append(user_text)  # type: ignore[method-assign]

    source = "75e92a7e-2838-45a7-6f2c-32a5fde6c300"
    handled = app._handle_local_command(f"/ar {source} target 4:5")

    assert handled is True
    assert any("Aspect Flow:" in line for line in chat_lines)
    assert len(captured) == 1
    prompt = captured[0]
    assert f"Source catalog image ID: {source}" in prompt
    assert "Requested target aspect ratios: 4:5" in prompt


def test_aspect_command_parses_id_label_prefix(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    captured: list[str] = []
    app._enqueue_request = lambda user_text: captured.append(user_text)  # type: ignore[method-assign]

    source = "b2ae12bc-5419-42bf-ef0f-d8bb83c4d400"
    handled = app._handle_local_command(f"/ar id {source} target=4:5")

    assert handled is True
    assert any(f"Aspect Flow: source={source} targets=4:5" == line for line in chat_lines)
    assert len(captured) == 1
    prompt = captured[0]
    assert f"Source catalog image ID: {source}" in prompt
    assert f"Requested upload target image ID: {source}" in prompt
    assert "Requested target aspect ratios: 4:5" in prompt


def test_aspect_command_infers_uuid_from_noisy_prefix(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    captured: list[str] = []
    app._enqueue_request = lambda user_text: captured.append(user_text)  # type: ignore[method-assign]

    source = "09af3c36-0fa7-4ed5-8fd4-f04b9ecf3bc1"
    handled = app._handle_local_command(f"/ar source id {source} target=4:5")

    assert handled is True
    assert any(f"Aspect Flow: source={source} targets=4:5" == line for line in chat_lines)
    assert len(captured) == 1
    prompt = captured[0]
    assert f"Source catalog image ID: {source}" in prompt
    assert "Requested target aspect ratios: 4:5" in prompt


def test_aspect_command_infers_uuid_when_not_first_token(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    captured: list[str] = []
    app._enqueue_request = lambda user_text: captured.append(user_text)  # type: ignore[method-assign]

    source = "91a23f25-1f08-43d7-922d-13385139f8fd"
    handled = app._handle_local_command(
        f"/ar please use source image {source} and keep framing stable target=4:5"
    )

    assert handled is True
    assert any(f"Aspect Flow: source={source} targets=4:5" == line for line in chat_lines)
    assert len(captured) == 1
    prompt = captured[0]
    assert f"Source catalog image ID: {source}" in prompt
    assert "Requested target aspect ratios: 4:5" in prompt


def test_aspect_command_infers_target_from_natural_language_phrase(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    captured: list[str] = []
    app._enqueue_request = lambda user_text: captured.append(user_text)  # type: ignore[method-assign]

    source = "32cc1cbc-141d-47e3-8373-566c796e6000"
    handled = app._handle_local_command(
        f"/ar {source} do an output aspect ratio adjustment of 4:5 and keep the subject centered"
    )

    assert handled is True
    assert any("Aspect Flow:" in line for line in chat_lines)
    assert len(captured) == 1
    prompt = captured[0]
    assert f"Source catalog image ID: {source}" in prompt
    assert "Requested target aspect ratios: 4:5" in prompt


def test_aspect_command_tolerates_missing_closing_quote_on_targets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    captured: list[str] = []
    app._enqueue_request = lambda user_text: captured.append(user_text)  # type: ignore[method-assign]

    source = "3f542eab-ad75-4354-6454-d4f045ce2d00"
    handled = app._handle_local_command(f'/ar {source} targets="4:5 preserve="keep composition stable"')

    assert handled is True
    assert any("Aspect Flow:" in line for line in chat_lines)
    assert len(captured) == 1
    prompt = captured[0]
    assert f"Source catalog image ID: {source}" in prompt
    assert "Requested target aspect ratios: 4:5" in prompt


def test_tanktracks_command_shows_usage_without_image_id(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    handled = app._handle_local_command("/tanktracks")

    assert handled is True
    assert any("Tank Tracks Flow Usage" in line for line in chat_lines)
    assert any("/tanktracks|/tanktrack <image_id>" in line for line in chat_lines)


def test_tanktrack_singular_alias_shows_usage_without_image_id(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    handled = app._handle_local_command("/tanktrack")

    assert handled is True
    assert any("Tank Tracks Flow Usage" in line for line in chat_lines)
    assert any("/tanktracks|/tanktrack <image_id>" in line for line in chat_lines)


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
        assert any("/tanktracks|/tanktrack <image_id>" in line for line in chat_lines)


def test_tanktracks_command_builds_agentic_flow_prompt(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    captured: list[str] = []
    app._enqueue_request = lambda user_text: captured.append(user_text)  # type: ignore[method-assign]

    source = "1cc224eb-022b-4ce9-0dd8-3f274f4f4300"
    handled = app._handle_local_command(f"/tanktracks {source}")

    assert handled is True
    assert any("Tank Tracks Flow:" in line for line in chat_lines)
    assert len(captured) == 1
    prompt = captured[0]
    assert "TANK TRACKS FLOW REQUEST" in prompt
    assert f"Source catalog image ID: {source}" in prompt
    assert f"Requested upload target image ID: {source}" in prompt
    assert "Workflow preference: add_tank_tracks" in prompt
    assert "Prompt behavior: use fixed workflow default prompt (no override)" in prompt
    assert "Execution rules (add_tank_tracks fast path)" in prompt
    assert "explicit overrides object" in prompt
    assert "Do not set aspect_ratio/custom_*" in prompt
    assert "auto_aspect_ratio_source" in prompt
    assert "Do not call workflows_capabilities_get" in prompt
    assert "Resolve effective upload parent: call photarium_get" in prompt
    assert "If upload to requested target fails parent/variant validation" in prompt
    assert "Post aspect ratio adjustment: none" in prompt


def test_tanktracks_command_seed_sweep_enqueues_multiple_runs_with_post_aspect(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)
    monkeypatch.setattr(app, "_generate_random_seed_values", lambda count: [610001, 610002, 610003, 610004])

    captured: list[str] = []
    app._enqueue_request = lambda user_text: captured.append(user_text)  # type: ignore[method-assign]

    source = "1cc224eb-022b-4ce9-0dd8-3f274f4f4300"
    handled = app._handle_local_command(
        f"/tanktracks {source} runs=4 sweep=seed seed_start=100 post_aspect=4:5"
    )

    assert handled is True
    assert any("runs=4 sweep=seed post_aspect=4:5" in line for line in chat_lines)
    assert len(captured) == 4
    assert "Run index: 1 of 4" in captured[0]
    assert "Run index: 4 of 4" in captured[3]
    assert "Seed override: 610001" in captured[0]
    assert "Seed override: 610004" in captured[3]
    assert "Post aspect ratio adjustment: 4:5" in captured[0]
    assert "workflows_run_aspect_ratio_adjustment" in captured[0]
    assert any("ignoring seed_start/seed for sweeps" in line for line in chat_lines)


def test_tanktracks_rejects_prompt_override(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    captured: list[str] = []
    app._enqueue_request = lambda user_text: captured.append(user_text)  # type: ignore[method-assign]

    source = "1cc224eb-022b-4ce9-0dd8-3f274f4f4300"
    handled = app._handle_local_command(f'/tanktracks {source} prompt="Do not allow this"')

    assert handled is True
    assert any("fixed workflow prompt" in line for line in chat_lines)
    assert captured == []


def test_tanktracks_natural_language_seed_sweep_with_aspect_ratio(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)
    monkeypatch.setattr(app, "_generate_random_seed_values", lambda count: [620001, 620002, 620003])

    captured: list[str] = []
    app._enqueue_request = lambda user_text: captured.append(user_text)  # type: ignore[method-assign]

    source = "bf694d5f-f7dc-4716-9ff7-c59847bf0f00"
    handled = app._handle_local_command(
        f"/tanktracks {source} sweep across 3 seeds at an aspect ratio of 4:5"
    )

    assert handled is True
    assert any("runs=3 sweep=seed post_aspect=4:5" in line for line in chat_lines)
    assert len(captured) == 3
    assert "Run index: 1 of 3" in captured[0]
    assert "Run index: 3 of 3" in captured[2]
    assert "Seed override: 620001" in captured[0]
    assert "Seed override: 620003" in captured[2]
    assert "Post aspect ratio adjustment: 4:5" in captured[0]
    assert "This is a fresh execution request. Always run now" in captured[0]


def test_variation_command_shows_usage_without_image_id(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    handled = app._handle_local_command("/vary")

    assert handled is True
    assert any("Image Variation Flow Usage" in line for line in chat_lines)
    assert any("/vary|/variation|/variations <image_id>" in line for line in chat_lines)


def test_variation_command_explicit_help_variants_show_usage(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    commands = ["/vary help", "/vary -h", "/variation --help", "/variations -h"]
    for command in commands:
        chat_lines: list[str] = []
        app._write_chat = lambda _markup, plain: chat_lines.append(plain)
        handled = app._handle_local_command(command)
        assert handled is True
        assert any("Image Variation Flow Usage" in line for line in chat_lines)
        assert any("/vary|/variation|/variations <image_id>" in line for line in chat_lines)


def test_variation_command_builds_agentic_flow_prompt(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    captured: list[str] = []
    app._enqueue_request = lambda user_text: captured.append(user_text)  # type: ignore[method-assign]

    source = "9e9227cd-2838-45a7-6f2c-32a5fde6c300"
    handled = app._handle_local_command(
        f'/vary {source} analysis="Describe materials and lighting" upscale=true'
    )

    assert handled is True
    assert any("Variation Flow:" in line for line in chat_lines)
    assert len(captured) == 1
    prompt = captured[0]
    assert "IMAGE VARIATION FLOW REQUEST" in prompt
    assert f"Source catalog image ID: {source}" in prompt
    assert f"Requested upload target image ID: {source}" in prompt
    assert "Workflow preference: image_variation_maker" in prompt
    assert "Image analysis prompt: Describe materials and lighting" in prompt
    assert "Upscale request: enabled" in prompt
    assert "Update Photarium prompt metadata" in prompt


def test_variation_command_parses_run_count_and_prompt_sweep(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    captured: list[str] = []
    app._enqueue_request = lambda user_text: captured.append(user_text)  # type: ignore[method-assign]

    source = "0ee227cd-2838-45a7-6f2c-32a5fde6c300"
    handled = app._handle_local_command(f"/vary {source} run it 3 times varying the prompt")

    assert handled is True
    assert any("Variation Flow:" in line for line in chat_lines)
    assert len(captured) == 1
    prompt = captured[0]
    assert "Requested run count: 3" in prompt
    assert "Sweep target: prompt" in prompt
    assert "Additional variation instructions: run it 3 times varying the prompt" in prompt
    assert "do not choose a single 'best' output" in prompt


def test_variation_command_parses_denoise_sweep_values(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    captured: list[str] = []
    app._enqueue_request = lambda user_text: captured.append(user_text)  # type: ignore[method-assign]

    source = "1ae227cd-2838-45a7-6f2c-32a5fde6c300"
    handled = app._handle_local_command(f"/vary {source} denoise 0.4->0.8 step 0.2")

    assert handled is True
    assert any("Variation Flow:" in line for line in chat_lines)
    assert len(captured) == 1
    prompt = captured[0]
    assert "Requested run count: 3" in prompt
    assert "Sweep target: denoise" in prompt
    assert "Sweep values: 0.4, 0.6, 0.8" in prompt
    assert "Upload all successful outputs as Photarium variants" in prompt


def test_variation_command_parses_image_id_shorthand_and_numeric_sweep_runs(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)
    monkeypatch.setattr(app, "_generate_random_seed_values", lambda count: [810001, 810002, 810003, 810004])

    captured: list[str] = []
    app._enqueue_request = lambda user_text: captured.append(user_text)  # type: ignore[method-assign]

    source = "7e278170-a1df-4884-a9ad-5dfdb71e5700"
    handled = app._handle_local_command(
        f"/vary image id {source} change the keycaps colors sweep 4 runs with different prompts and different seeds"
    )

    assert handled is True
    assert any("Variation Flow:" in line for line in chat_lines)
    assert len(captured) == 1
    prompt = captured[0]
    assert f"Source catalog image ID: {source}" in prompt
    assert f"Requested upload target image ID: {source}" in prompt
    assert "Requested run count: 4" in prompt
    assert "Sweep target: seed" in prompt
    assert "Seed sweep values (randomized): 810001, 810002, 810003, 810004" in prompt
    assert "Sweep target: 4" not in prompt


def test_imageedit_command_shows_usage_without_args(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    handled = app._handle_local_command("/imageedit")

    assert handled is True
    assert any("Image Edit Flow Usage" in line for line in chat_lines)
    assert any("/imageedit|/imgedit <image_id>" in line for line in chat_lines)


def test_imageedit_command_builds_flow_prompt_with_natural_language(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)
    captured: list[str] = []
    app._enqueue_request = lambda user_text: captured.append(user_text)  # type: ignore[method-assign]

    source = "75e92a7e-2838-45a7-6f2c-32a5fde6c300"
    command = f"/imageedit {source} make it look like polished brass with softer studio lighting"
    handled = app._handle_local_command(command)

    assert handled is True
    assert any(line == f"Command: {command}" for line in chat_lines)
    assert any("Image Edit Flow:" in line for line in chat_lines)
    assert len(captured) == 1
    prompt = captured[0]
    assert "IMAGE EDIT FLOW REQUEST" in prompt
    assert f"Source catalog image ID: {source}" in prompt
    assert f"Requested upload target image ID: {source}" in prompt
    assert "Workflow preference: image_edit" in prompt
    assert "Edit request: make it look like polished brass with softer studio lighting" in prompt
    assert "Post aspect ratio adjustment: none" in prompt


def test_imageedit_command_parses_workflow_and_image_id_shorthand(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)
    captured: list[str] = []
    app._enqueue_request = lambda user_text: captured.append(user_text)  # type: ignore[method-assign]

    source = "7e278170-a1df-4884-a9ad-5dfdb71e5700"
    handled = app._handle_local_command(
        f"/imgedit image id {source} workflow=flux_2_klein_4B_prompt_guided_image_edit add graffiti decals and weathering"
    )

    assert handled is True
    assert any("Image Edit Flow:" in line for line in chat_lines)
    assert len(captured) == 1
    prompt = captured[0]
    assert f"Source catalog image ID: {source}" in prompt
    assert "Workflow preference: flux_2_klein_4B_prompt_guided_image_edit" in prompt
    assert "Edit request: add graffiti decals and weathering" in prompt


def test_imageedit_command_seed_sweep_enqueues_multiple_runs_with_post_aspect(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)
    monkeypatch.setattr(app, "_generate_random_seed_values", lambda count: [710001, 710002, 710003, 710004])
    captured: list[str] = []
    app._enqueue_request = lambda user_text: captured.append(user_text)  # type: ignore[method-assign]

    source = "75e92a7e-2838-45a7-6f2c-32a5fde6c300"
    handled = app._handle_local_command(
        f"/imageedit {source} workflow=image_edit runs=4 sweep=seed seed_start=500 post_aspect=4:5 make the tracks more industrial and varied"
    )

    assert handled is True
    assert any("runs=4 sweep=seed post_aspect=4:5" in line for line in chat_lines)
    assert len(captured) == 4
    assert "Run index: 1 of 4" in captured[0]
    assert "Run index: 4 of 4" in captured[3]
    assert "Seed override: 710001" in captured[0]
    assert "Seed override: 710004" in captured[3]
    assert "Post aspect ratio adjustment: 4:5" in captured[0]
    assert "workflows_run_aspect_ratio_adjustment" in captured[0]
    assert any("ignoring seed_start/seed for sweeps" in line for line in chat_lines)


def test_importworkflow_command_shows_usage_without_image_id(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    handled = app._handle_local_command("/importwf")

    assert handled is True
    assert any("Import Workflow Flow Usage" in line for line in chat_lines)
    assert any("/importwf|/importworkflow <image_id>" in line for line in chat_lines)


def test_importworkflow_command_explicit_help_variants_show_usage(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    commands = [
        "/importwf help",
        "/importwf -h",
        "/importwf --help",
        "/importworkflow help",
        "/importworkflow -h",
        "/importworkflow --help",
    ]
    for command in commands:
        chat_lines: list[str] = []
        app._write_chat = lambda _markup, plain: chat_lines.append(plain)
        handled = app._handle_local_command(command)
        assert handled is True
        assert any("Import Workflow Flow Usage" in line for line in chat_lines)
        assert any("/importwf|/importworkflow <image_id>" in line for line in chat_lines)


def test_importworkflow_command_builds_agentic_flow_prompt(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    app._is_ready = True
    chat_lines: list[str] = []
    app._write_chat = lambda _markup, plain: chat_lines.append(plain)

    captured: list[str] = []
    app._enqueue_request = lambda user_text: captured.append(user_text)  # type: ignore[method-assign]

    image_id = "b287f5ef-2901-4e27-f6b4-b483fc4a7e00"
    handled = app._handle_local_command(
        f'/importwf {image_id} id=tank_tracks_v1 tags=photarium,imported desc="Tank tracks edit workflow"'
    )

    assert handled is True
    assert any("Import Workflow Flow:" in line for line in chat_lines)
    assert len(captured) == 1
    prompt = captured[0]
    assert "IMPORT WORKFLOW FLOW REQUEST" in prompt
    assert f"Source Photarium image ID: {image_id}" in prompt
    assert "Target workflow_id: tank_tracks_v1" in prompt
    assert "Workflow display name: tank_tracks_v1" in prompt
    assert "Workflow tags: photarium, imported" in prompt
    assert "Workflow description hint: Tank tracks edit workflow" in prompt
    assert "Call workflows_import_from_photarium" in prompt
    assert "verify with workflows_get and workflows_params_get" in prompt


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


def test_input_height_resize_persists_between_sessions(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    app = ChatApp(_config())
    fake_input = _FakeInput()
    monkeypatch.setattr(app, "query_one", lambda *_args, **_kwargs: fake_input)
    monkeypatch.setattr(app, "notify", lambda *args, **kwargs: None)

    app.action_input_height_increase()
    app.action_input_height_increase()
    assert app._input_height_lines == 8
    assert fake_input.styles.height == 8

    prefs_path = tmp_path / ".mcp_chat_ui_prefs.json"
    assert prefs_path.exists()
    payload = json.loads(prefs_path.read_text(encoding="utf-8"))
    assert payload.get("input_height_lines") == 8
    assert payload.get("prompt_history") == []

    app2 = ChatApp(_config())
    app2._load_ui_preferences()
    assert app2._input_height_lines == 8


def test_prompt_history_persists_between_sessions(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    app = ChatApp(_config())
    app._record_prompt_history("first command")
    app._record_prompt_history("second command")

    prefs_path = tmp_path / ".mcp_chat_ui_prefs.json"
    payload = json.loads(prefs_path.read_text(encoding="utf-8"))
    assert payload.get("prompt_history") == ["first command", "second command"]

    app2 = ChatApp(_config())
    app2._load_ui_preferences()
    assert app2._prompt_history == ["first command", "second command"]


def test_prompt_history_fifo_cap_50(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    app = ChatApp(_config())
    for index in range(60):
        app._record_prompt_history(f"cmd-{index:02d}")

    assert len(app._prompt_history) == 50
    assert app._prompt_history[0] == "cmd-10"
    assert app._prompt_history[-1] == "cmd-59"

    prefs_path = tmp_path / ".mcp_chat_ui_prefs.json"
    payload = json.loads(prefs_path.read_text(encoding="utf-8"))
    saved = payload.get("prompt_history")
    assert isinstance(saved, list)
    assert len(saved) == 50
    assert saved[0] == "cmd-10"
    assert saved[-1] == "cmd-59"


def test_input_word_actions_delegate_to_text_area(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = ChatApp(_config())
    fake_input = _FakeInput("alpha beta")
    monkeypatch.setattr(app, "query_one", lambda *_args, **_kwargs: fake_input)

    app.action_input_word_left()
    app.action_input_word_right()
    app.action_input_delete_word_left()
    app.action_input_delete_word_right()

    assert fake_input.word_left_calls == 1
    assert fake_input.word_right_calls == 1
    assert fake_input.delete_word_left_calls == 1
    assert fake_input.delete_word_right_calls == 1


def test_mac_safe_bindings_and_conflicts() -> None:
    app_binding_map = {key: action for key, action, _desc in ChatApp.BINDINGS}
    assert app_binding_map["alt+shift+up"] == "pane_page_up"
    assert app_binding_map["alt+shift+down"] == "pane_page_down"
    assert app_binding_map["ctrl+shift+up"] == "input_height_increase"
    assert app_binding_map["ctrl+shift+down"] == "input_height_decrease"
    assert app_binding_map["ctrl+alt+up"] == "input_height_increase"
    assert app_binding_map["ctrl+alt+down"] == "input_height_decrease"
    assert app_binding_map["alt+left"] == "input_word_left"
    assert app_binding_map["alt+right"] == "input_word_right"
    assert app_binding_map["alt+backspace"] == "input_delete_word_left"
    assert app_binding_map["alt+d"] == "input_delete_word_right"

    app_keys = [key for key, _action, _desc in ChatApp.BINDINGS]
    assert len(app_keys) == len(set(app_keys))
