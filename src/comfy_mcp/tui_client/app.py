"""Textual TUI for MCP tool orchestration."""

from __future__ import annotations

import asyncio
import argparse
import json
import os
import platform
import re
import secrets
import subprocess
import shutil
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal

from rich.markup import escape as rich_markup_escape
_TEXTUAL_AVAILABLE = True
try:
    from textual.app import App, ComposeResult
    from textual import events
    from textual.driver import Driver
    from textual.widgets import Footer, Header, RichLog, TextArea
except ModuleNotFoundError:  # pragma: no cover - optional dependency for non-TUI unit tests
    # The TUI depends on Textual, but most of our orchestration logic and unit tests do not.
    # Provide minimal stubs so importing this module doesn't hard-require Textual.
    from types import SimpleNamespace

    _TEXTUAL_AVAILABLE = False

    class App:  # type: ignore[override]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            return None

        def notify(self, *args: Any, **kwargs: Any) -> None:
            return None

        def query_one(self, *args: Any, **kwargs: Any) -> Any:
            raise LookupError("Textual is not installed; query_one() is unavailable.")

    class ComposeResult:  # type: ignore[override]
        pass

    class Driver:  # type: ignore[override]
        pass

    class _WidgetStub:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            return None

    events = SimpleNamespace()  # type: ignore[assignment]
    Footer = Header = RichLog = TextArea = _WidgetStub  # type: ignore[misc]

from comfy_mcp.tui_client.config import (
    DEFAULT_SYSTEM_PROMPT,
    ChatClientConfig,
    ResponseVerbosity,
    compose_runtime_system_prompt,
    load_config,
)
from comfy_mcp.tui_client.llm_client import OpenAIClient
from comfy_mcp.tui_client.orchestrator import ChatOrchestrator
from comfy_mcp.tui_client.mcp_router import MCPToolRouter, _is_ignorable_stdio_close_error
from comfy_mcp.tui_client.http_router import HTTPToolRouter
from comfy_mcp.tui_client.hybrid_router import HybridToolRouter
from comfy_mcp.tui_client.sanitize import sanitize_result
from comfy_mcp.tui_client.workflows_schema import ensure_workflows_run_schema
from comfy_mcp.tui_client.app_support import (
    build_router as _build_router,
    clipboard_copy as _clipboard_copy,
    normalize_openai_tool_schema as _normalize_openai_tool_schema,
    strip_border_glyphs as _strip_border_glyphs,
)
from comfy_mcp.tui_client.commands import LocalCommandHandler
from comfy_mcp.tui_client.flow_prompts import FlowPromptBuilder
from comfy_mcp.tui_client.session_logging import SessionLogManager

from comfy_mcp.tui_client.startup_diagnostics import StartupDiagnosticsRenderer

if _TEXTUAL_AVAILABLE:  # pragma: no cover - import depends on optional dependency
    from comfy_mcp.tui_client.widgets import HistoryTextArea, PassiveRichLog
else:  # pragma: no cover - test fallback when Textual isn't installed
    HistoryTextArea = TextArea  # type: ignore[assignment]
    PassiveRichLog = RichLog  # type: ignore[assignment]

try:
    from textual.drivers.linux_driver import LinuxDriver as _TextualLinuxDriver
except Exception:  # pragma: no cover - platform/import dependent
    _TextualLinuxDriver = None

def _should_disable_kitty_keyboard_protocol() -> bool:
    """Work around iTerm2 + Textual keyboard repeat issues by skipping kitty keyboard mode."""
    if os.environ.get("EDGAR_TUI_DISABLE_KITTY_KEYBOARD", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if os.environ.get("EDGAR_TUI_FORCE_KITTY_KEYBOARD", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    term_program = os.environ.get("TERM_PROGRAM", "")
    return term_program == "iTerm.app"


if _TextualLinuxDriver is not None:
    class _EDGARLinuxDriver(_TextualLinuxDriver):
        """LinuxDriver wrapper that can suppress kitty keyboard protocol negotiation."""

        def write(self, data: str) -> None:
            if _should_disable_kitty_keyboard_protocol() and data:
                data = data.replace("\x1b[>1u", "").replace("\x1b[<u", "")
                if not data:
                    return
            super().write(data)
else:  # pragma: no cover - fallback on unsupported platforms
    _EDGARLinuxDriver = None



def _format_editorial_ads_inventory_lines(result: Any) -> List[str] | None:
    """Return deterministic ad inventory lines from editorial_ads_list_inventory output."""
    if not isinstance(result, dict):
        return None

    items = result.get("items")
    if not isinstance(items, list):
        return None

    inventory_path = result.get("inventoryPath")
    total = result.get("total")
    matched = result.get("matched")
    returned = result.get("returned")

    lines: List[str] = []
    header = "Editorial ad inventory (authoritative tool output)"
    if isinstance(inventory_path, str) and inventory_path:
        header += f" from {inventory_path}"
    lines.append(header)

    stat_parts: List[str] = []
    if isinstance(total, int):
        stat_parts.append(f"total={total}")
    if isinstance(matched, int):
        stat_parts.append(f"matched={matched}")
    if isinstance(returned, int):
        stat_parts.append(f"returned={returned}")
    if stat_parts:
        lines.append(", ".join(stat_parts))

    for index, raw_item in enumerate(items, start=1):
        if not isinstance(raw_item, dict):
            continue
        ad_id = str(raw_item.get("id") or "<missing-id>")
        slot = str(raw_item.get("slot") or "unknown-slot")
        title = raw_item.get("title")
        sponsor = raw_item.get("sponsor")
        visibility = raw_item.get("visibility")

        line = f"{index}. `{ad_id}` — {slot}"
        if isinstance(title, str) and title.strip():
            line += f" — \"{title.strip()}\""
        if isinstance(sponsor, str) and sponsor.strip():
            line += f" — sponsor: {sponsor.strip()}"
        if isinstance(visibility, str) and visibility.strip():
            line += f" — visibility: {visibility.strip()}"
        lines.append(line)

    return lines


def _format_editorial_ads_preview_lines(result: Any) -> List[str] | None:
    """Return deterministic preview lines from editorial_ads_preview output."""
    if not isinstance(result, dict):
        return None

    mode = result.get("mode")
    preview_url = result.get("previewUrl")
    preview_urls = result.get("previewUrls")
    selected_ids = result.get("selectedIds")
    missing_ids = result.get("missingRequestedIds")
    fit = result.get("fit")
    actual_aspect = result.get("actualAspect")

    lines: List[str] = []
    lines.append(f"Editorial ad preview (mode={mode if isinstance(mode, str) else 'catalog'})")
    if isinstance(preview_url, str) and preview_url.strip():
        lines.append(f"Preview URL: {preview_url.strip()}")

    if isinstance(preview_urls, dict):
        catalog = preview_urls.get("catalog")
        single = preview_urls.get("single")
        if isinstance(catalog, str) and catalog.strip():
            lines.append(f"Catalog URL: {catalog.strip()}")
        if isinstance(single, str) and single.strip():
            lines.append(f"Primary single URL: {single.strip()}")

    if isinstance(fit, str):
        lines.append(f"fit={fit}")
    if isinstance(actual_aspect, bool):
        lines.append(f"actualAspect={str(actual_aspect).lower()}")

    if isinstance(selected_ids, list) and selected_ids:
        lines.append("Selected ads:")
        for index, ad_id in enumerate(selected_ids, start=1):
            if isinstance(ad_id, str):
                lines.append(f"{index}. `{ad_id}`")

    if isinstance(missing_ids, list) and missing_ids:
        missing = [ad_id for ad_id in missing_ids if isinstance(ad_id, str) and ad_id.strip()]
        if missing:
            lines.append(f"Missing requested IDs: {', '.join(missing)}")

    return lines


_RECENT_SIGNAL_TERMS_RE = re.compile(r"\b(last|latest|most\s+recent|newest)\b", re.IGNORECASE)
_SIGNAL_TOKEN_RE = re.compile(r"\bsign[a-z]*\b", re.IGNORECASE)


def _parse_recent_signal_request_count(user_text: str, number_words: Dict[str, int]) -> int | None:
    text = (user_text or "").strip().lower()
    if not text or "digest" in text:
        return None
    if not _RECENT_SIGNAL_TERMS_RE.search(text):
        return None
    if not _SIGNAL_TOKEN_RE.search(text):
        return None

    for match in re.finditer(
        r"\b(last|latest|most\s+recent|newest)\s+([a-z]+|\d{1,3})\b",
        text,
        flags=re.IGNORECASE,
    ):
        raw = match.group(2).lower()
        if raw.isdigit():
            return max(1, min(int(raw), 25))
        if raw in number_words:
            return max(1, min(int(number_words[raw]), 25))

    for match in re.finditer(r"\b(\d{1,3})\s+sign[a-z]*\b", text, flags=re.IGNORECASE):
        return max(1, min(int(match.group(1)), 25))

    if re.search(r"\blast\s+signal\b", text, flags=re.IGNORECASE):
        return 1
    return 3


def _extract_signal_items_from_tool_result(result: Any) -> List[Dict[str, Any]] | None:
    visited: set[int] = set()
    queue: list[Any] = [result]

    while queue:
        current = queue.pop(0)
        current_id = id(current)
        if current_id in visited:
            continue
        visited.add(current_id)

        if isinstance(current, dict):
            for key in ("items", "signals", "results", "matches", "queue"):
                value = current.get(key)
                if isinstance(value, list):
                    items = [item for item in value if isinstance(item, dict)]
                    if items:
                        return items

            content = current.get("content")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    text = block.get("text")
                    if isinstance(text, str) and text.strip():
                        queue.append(text)

            for key in ("data", "result", "payload"):
                nested = current.get(key)
                if nested is not None:
                    queue.append(nested)
            continue

        if isinstance(current, str):
            raw = current.strip()
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            queue.append(parsed)

    return None


def _format_recent_signal_lines(items: List[Dict[str, Any]], requested_count: int) -> List[str]:
    lines: List[str] = []
    selected = items[:requested_count]
    for index, signal in enumerate(selected, start=1):
        signal_id = str(signal.get("signal_id") or "unknown")
        headline = str(signal.get("headline") or "Untitled Signal").strip()
        summary = (
            signal.get("hook_preview")
            or signal.get("hook")
            or signal.get("summary")
            or signal.get("gpt_summary")
            or signal.get("gpt_logline_summary")
            or signal.get("digest_summary")
            or signal.get("novelty_note")
            or "(no summary provided)"
        )
        summary_text = str(summary).strip()
        if len(summary_text) > 260:
            summary_text = summary_text[:257].rstrip() + "..."
        lines.append(f"{index}) {signal_id} — {headline} — {summary_text}")

    if len(selected) < requested_count:
        lines.append(
            f"Tool returned {len(selected)} signal(s), fewer than requested ({requested_count})."
        )
    return lines


class ChatApp(App):
    TITLE = "EDGAR NFL OS"
    _DEFAULT_INPUT_HEIGHT = 6
    _MIN_INPUT_HEIGHT = 4
    _MAX_INPUT_HEIGHT = 18
    _LOG_MAX_LINES = 2000
    _LOG_RENDER_MAX_LINES = 160
    _LOG_RENDER_MAX_CHARS = 12000
    _SESSION_LOG_MAX_CHARS = 16000
    _SESSION_LOG_FLUSH_DELAY_S = 0.35
    _UI_PREFS_FILENAME = ".mcp_chat_ui_prefs.json"
    _PROMPT_HISTORY_MAX = 50
    _NUMBER_WORDS: Dict[str, int] = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
    }

    _EDGAR_ASCII: tuple[str, ...] = (
        r" _____ ____   ____    _    ____  ",
        r"| ____|  _ \ / ___|  / \  |  _ \ ",
        r"|  _| | | | | |  _  / _ \ | |_) |",
        r"| |___| |_| | |_| |/ ___ \|  _ < ",
        r"|_____|____/ \____/_/   \_\_| \_\\",
    )
    _NFL_ASCII: tuple[str, ...] = (
        "▖ ▖        ▄▖  ▗       ",
        "▛▖▌█▌▀▌▛▘  ▙▖▌▌▜▘▌▌▛▘█▌",
        "▌▝▌▙▖█▌▌   ▌ ▙▌▐▖▙▌▌ ▙▖",
        "                       ",
        "▖   ▌       ▗          ",
        "▌ ▀▌▛▌▛▌▛▘▀▌▜▘▛▌▛▘▌▌   ",
        "▙▖█▌▙▌▙▌▌ █▌▐▖▙▌▌ ▙▌   ",
        "                  ▄▌   ",
    )

    CSS = """
    Screen {
        layout: vertical;
    }

    #chat_log {
        width: 1fr;
        height: 1fr;
        border: round #4455aa;
    }
    #chat_log.pane-active {
        border: round #88aaff;
    }

    #tool_log {
        width: 1fr;
        height: 1fr;
        border: round #2a8a4a;
        display: none;
    }
    #tool_log.pane-active {
        border: round #6bf08f;
    }

    #input {
        border: round #666666;
        height: 6;
    }

    .pane-label {
        dock: top;
        width: 100%;
        height: 1;
        background: #333333;
        color: #aaaaaa;
        text-align: center;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+c", "quit", "Quit"),
        ("f10", "quit", "Quit"),
        ("escape", "quit", "Quit"),
        ("tab", "toggle_pane", "Toggle Pane"),
        ("shift+tab", "toggle_pane", "Toggle Pane"),
        ("f1", "focus_chat", "Chat"),
        ("f2", "focus_tools", "Tools"),
        ("alt+up", "pane_scroll_up", "Scroll Up"),
        ("alt+down", "pane_scroll_down", "Scroll Down"),
        ("alt+shift+up", "pane_page_up", "Page Up"),
        ("alt+shift+down", "pane_page_down", "Page Down"),
        ("alt+pageup", "pane_page_up", "Page Up"),
        ("alt+pagedown", "pane_page_down", "Page Down"),
        ("super+up", "pane_home", "Top"),
        ("super+down", "pane_end", "Bottom"),
        ("alt+home", "pane_home", "Top"),
        ("alt+end", "pane_end", "Bottom"),
        ("alt+left", "input_word_left", "Word Left"),
        ("alt+right", "input_word_right", "Word Right"),
        ("alt+b", "input_word_left", "Word Left"),
        ("alt+f", "input_word_right", "Word Right"),
        ("alt+backspace", "input_delete_word_left", "Del Word Left"),
        ("alt+d", "input_delete_word_right", "Del Word Right"),
        ("ctrl+shift+up", "input_height_increase", "Input +"),
        ("ctrl+shift+down", "input_height_decrease", "Input -"),
        ("ctrl+alt+up", "input_height_increase", "Input +"),
        ("ctrl+alt+down", "input_height_decrease", "Input -"),
        ("f6", "copy_chat", "Copy Chat"),
        ("f7", "copy_tools", "Copy Tools"),
        ("f8", "copy_all", "Copy All"),
        ("f9", "export_transcript", "Export"),
        ("f5", "submit_prompt", "Send"),
        ("ctrl+s", "submit_prompt", "Send"),
        ("f12", "toggle_key_debug", "Key Debug"),
        ("ctrl+enter", "submit_prompt", "Send"),
    ]

    def __init__(self, config: ChatClientConfig):
        super().__init__()
        self._config = config
        self._all_servers = list(config.servers)
        self._server_enabled = {server.name: True for server in self._all_servers}
        self._router = _build_router(self._build_active_config())
        self._llm = OpenAIClient(config.llm)
        self._startup_diagnostics = StartupDiagnosticsRenderer()
        self._command_handler = LocalCommandHandler()
        self._flow_prompt_builder = FlowPromptBuilder(self._NUMBER_WORDS)
        self._session_log_manager = SessionLogManager()
        self._tools: List[Dict[str, Any]] = []
        self._available_tool_names: set[str] = set()
        self._orchestrator = ChatOrchestrator(
            config.system_prompt or DEFAULT_SYSTEM_PROMPT,
            self._llm,
            self._router,
        )
        self._is_ready = False
        self._chat_history: List[str] = []
        self._tool_history: List[str] = []
        self._last_tool_selection_debug: Dict[str, Any] = {}
        self._prompt_history: List[str] = []
        self._prompt_history_cursor: int | None = None
        self._prompt_history_draft: str = ""
        self._active_pane: Literal["chat", "tools"] = "chat"
        self._session_log_path: Path | None = None
        self._workflow_progress_active_calls: int = 0
        self._workflow_progress_monitor_generation: int = 0
        self._request_queue: asyncio.Queue[str] = asyncio.Queue()
        self._request_queue_worker_running = False
        self._request_inflight = 0
        self._reconnect_lock = asyncio.Lock()
        self._turn_state: Literal["IDLE", "RUNNING_LLM", "RUNNING_TOOLS"] = "IDLE"
        self._chat_log_widget: RichLog | None = None
        self._tool_log_widget: RichLog | None = None
        self._session_log_buffer: List[str] = []
        self._session_log_flush_handle: asyncio.Handle | None = None
        self._base_system_prompt = config.system_prompt or DEFAULT_SYSTEM_PROMPT
        self._response_verbosity: ResponseVerbosity = "lowkey"
        low_churn_raw = os.environ.get("EDGAR_TUI_LOW_CHURN", "1").strip().lower()
        self._low_churn_mode = low_churn_raw not in {"0", "false", "no", "off"}
        self._ui_prefs_path = Path.cwd() / self._UI_PREFS_FILENAME
        self._input_height_lines: int = self._DEFAULT_INPUT_HEIGHT
        self._key_debug_enabled = os.environ.get("EDGAR_TUI_KEY_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
        self._key_debug_log_path = Path.cwd() / ".mcp_chat_logs" / "edgar_key_debug.jsonl"
        self._previous_loop_exception_handler: Callable[[asyncio.AbstractEventLoop, Dict[str, Any]], None] | None = None
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            self._load_ui_preferences()
        self._router = _build_router(self._build_active_config())
        self._orchestrator.set_router(self._router)
        self._apply_runtime_system_prompt()

    def _build_active_config(self) -> ChatClientConfig:
        active_servers = [server for server in self._all_servers if self._server_enabled.get(server.name, True)]
        return ChatClientConfig(
            llm=self._config.llm,
            servers=active_servers,
            system_prompt=self._config.system_prompt,
            strict_tool_facts=self._config.strict_tool_facts,
            tool_description_hints=self._config.tool_description_hints,
        )

    def get_driver_class(self) -> type[Driver]:
        driver_class = super().get_driver_class()
        if _EDGARLinuxDriver is None:
            return driver_class
        if driver_class is _TextualLinuxDriver and _should_disable_kitty_keyboard_protocol():
            return _EDGARLinuxDriver
        return driver_class

    def on_key(self, event: events.Key) -> None:
        if not self._key_debug_enabled:
            return
        focused = getattr(self, "focused", None)
        focused_id = getattr(focused, "id", None)
        focused_type = focused.__class__.__name__ if focused is not None else None
        self._key_debug_write(
            "key",
            key=event.key,
            name=event.name,
            character=event.character,
            is_printable=bool(getattr(event, "is_printable", False)),
            is_repeat=getattr(event, "is_repeat", None),
            focused_id=focused_id,
            focused_type=focused_type,
        )

    def compose(self) -> ComposeResult:
        yield Header()
        yield PassiveRichLog(id="chat_log", wrap=True, markup=True, max_lines=self._LOG_MAX_LINES)
        yield PassiveRichLog(id="tool_log", wrap=True, markup=True, max_lines=self._LOG_MAX_LINES)
        yield HistoryTextArea(
            "",
            id="input",
            soft_wrap=True,
            show_line_numbers=False,
            placeholder="Ask for a workflow, search, or tool call... (F5/Ctrl+S send; Ctrl+Enter when supported, Opt+←/→ words, Ctrl+Shift+↑/↓ resize)",
            on_history_prev=self._history_prev,
            on_history_next=self._history_next,
        )
        yield Footer()

    async def on_mount(self) -> None:
        self._install_loop_exception_handler()
        self._init_session_log()
        self._chat_log_widget = self.query_one("#chat_log", RichLog)
        self._tool_log_widget = self.query_one("#tool_log", RichLog)
        self._set_active_pane("chat")
        self._set_input_height(self._input_height_lines, announce=False, persist=False)
        self.query_one("#input", TextArea).focus()
        if self._key_debug_enabled:
            self._key_debug_write("mount")
            self.notify(f"Key debug ON: {self._key_debug_log_path}")
        # Connect in the app task so teardown happens in the same task context.
        await self._startup()

    def key_ctrl_q(self) -> None:
        """Hard quit fallback if a focused widget swallows bindings."""
        self.exit()

    def key_ctrl_c(self) -> None:
        """Hard quit fallback if a focused widget swallows bindings."""
        self.exit()

    def key_f10(self) -> None:
        self.exit()

    def action_toggle_key_debug(self) -> None:
        self._key_debug_enabled = not self._key_debug_enabled
        state = "ON" if self._key_debug_enabled else "OFF"
        self._key_debug_write("toggle", enabled=self._key_debug_enabled)
        self.notify(f"Key debug {state}")

    def _key_debug_write(self, kind: str, **payload: Any) -> None:
        if not self._key_debug_enabled:
            return
        try:
            self._key_debug_log_path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": datetime.now().isoformat(timespec="milliseconds"),
                "kind": kind,
                **payload,
            }
            with self._key_debug_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=True) + "\n")
        except Exception:
            # Debug logging must never impact input handling.
            return

    def _install_loop_exception_handler(self) -> None:
        loop = asyncio.get_running_loop()
        if self._previous_loop_exception_handler is not None:
            return
        self._previous_loop_exception_handler = loop.get_exception_handler()

        def _handler(inner_loop: asyncio.AbstractEventLoop, context: Dict[str, Any]) -> None:
            if _is_known_stdio_asyncgen_shutdown_context(context):
                return
            if self._previous_loop_exception_handler is not None:
                self._previous_loop_exception_handler(inner_loop, context)
                return
            inner_loop.default_exception_handler(context)

        loop.set_exception_handler(_handler)

    async def _startup(self) -> None:
        self._render_startup_banner()
        self._write_chat("[bold]Connecting to MCP servers...[/bold]", "Connecting to MCP servers...")
        try:
            await self._router.connect()
        except Exception as exc:
            self._write_chat(f"[red]Failed to connect: {exc}[/red]", f"Failed to connect: {exc}")
            return
        await self._startup_diagnostics.render_connected_servers(self)

        self._tools = [self._to_openai_tool(spec) for spec in self._router.list_tool_specs()]
        self._available_tool_names = {
            str(tool.get("function", {}).get("name", ""))
            for tool in self._tools
            if isinstance(tool, dict)
        }
        self._orchestrator.set_tools(self._tools)
        await self._startup_diagnostics.render_tool_exposure_warnings(self)
        self._write_tools("[bold]Tools ready:[/bold]", "Tools ready:")
        for tool in self._tools:
            self._write_tools(f"- {tool['function']['name']}", f"- {tool['function']['name']}")
        await self._startup_diagnostics.render_comfy_server_info(self)
        self._is_ready = True
        self._write_chat("[green]Ready. Type a request below.[/green]", "Ready. Type a request below.")
        self._write_chat(
            "[dim]Tip: Opt+Left/Right moves by word. Ctrl+Shift+Up/Down resizes the input box.[/dim]",
            "Tip: Opt+Left/Right moves by word. Ctrl+Shift+Up/Down resizes the input box.",
        )

    async def _reconnect_tools(self, *, allow_when_busy: bool = False) -> bool:
        async with self._reconnect_lock:
            if not allow_when_busy and self._turn_state != "IDLE":
                self._write_chat(
                    "[yellow]Busy. Wait for the current request to finish, then try again.[/yellow]",
                    "Busy. Wait for the current request to finish, then try again.",
                )
                return False

            self._is_ready = False
            self._write_chat("[bold]Reconnecting MCP tools...[/bold]", "Reconnecting MCP tools...")
            old_router = self._router
            new_router = _build_router(self._build_active_config())
            try:
                await new_router.connect()
            except Exception as exc:
                try:
                    await new_router.close()
                except Exception:
                    pass
                self._is_ready = True
                self._write_chat(f"[red]Reconnect failed: {exc}[/red]", f"Reconnect failed: {exc}")
                return False

            try:
                await old_router.close()
            except Exception:
                pass

            self._router = new_router
            self._orchestrator.set_router(self._router)
            self._tools = [self._to_openai_tool(spec) for spec in self._router.list_tool_specs()]
            self._available_tool_names = {
                str(tool.get("function", {}).get("name", ""))
                for tool in self._tools
                if isinstance(tool, dict)
            }
            self._orchestrator.set_tools(self._tools)
            self._orchestrator.reset_conversation()
            self._apply_runtime_system_prompt()
            await self._startup_diagnostics.render_connected_servers(self)
            await self._startup_diagnostics.render_tool_exposure_warnings(self)
            self._is_ready = True
            self._write_chat("[green]Tools updated.[/green]", "Tools updated.")
            return True

    def _resolve_server_token(self, token: str) -> str | None:
        lowered = token.strip().lower()
        if not lowered:
            return None
        alias_map = {
            "workflows": "comfy",
            "workflow": "comfy",
            "wf": "comfy",
        }
        lowered = alias_map.get(lowered, lowered)
        for server in self._all_servers:
            if server.name.lower() == lowered:
                return server.name
        return None

    def _set_server_enabled(self, name: str, enabled: bool) -> bool:
        if name not in self._server_enabled:
            return False
        self._server_enabled[name] = enabled
        self._save_ui_preferences()
        return True

    def _show_tool_state(self) -> None:
        self._write_chat("[bold]Tool state (servers)[/bold]", "Tool state (servers)")
        tool_count_by_server: dict[str, int] = {}
        if self._is_ready:
            for spec in self._router.list_tool_specs():
                server_name = getattr(spec, "server", "") or ""
                tool_count_by_server[server_name] = tool_count_by_server.get(server_name, 0) + 1
        for server in self._all_servers:
            enabled = self._server_enabled.get(server.name, True)
            state_markup = "[green]ON[/green]" if enabled else "[red]OFF[/red]"
            state_plain = "ON" if enabled else "OFF"
            prefixes = ", ".join(server.tool_prefixes) if server.tool_prefixes else "(no prefixes)"
            tool_count = tool_count_by_server.get(server.name, 0) if self._is_ready and enabled else 0
            self._write_chat(
                f"- {state_markup} [bold]{server.name}[/bold] [dim]tools:[/dim] {tool_count} [dim]prefixes:[/dim] {prefixes}",
                f"- {state_plain} {server.name} tools: {tool_count} prefixes: {prefixes}",
            )
        if self._is_ready:
            self._write_chat(
                f"[dim]Active tools: {len(self._available_tool_names)} (LLM max per request: {self._orchestrator._MAX_TOOLS_PER_LLM_REQUEST}).[/dim]",
                f"Active tools: {len(self._available_tool_names)} (LLM max per request: {self._orchestrator._MAX_TOOLS_PER_LLM_REQUEST}).",
            )

    def _show_last_tool_selection(self) -> None:
        debug = self._orchestrator.last_tool_selection_debug() or self._last_tool_selection_debug
        self._last_tool_selection_debug = dict(debug)
        self._write_chat("[bold]Last tool selection:[/bold]", "Last tool selection:")
        if not debug:
            self._write_chat("- no tool selection has run yet", "- no tool selection has run yet")
            return
        active_domains = debug.get("active_domains") or []
        protected_domains = debug.get("protected_domains") or []
        selected_tools = debug.get("selected_tools") or []
        pinned_tools = debug.get("pinned_tools") or []
        suppressed_tools = debug.get("suppressed_tools") or []
        suppressed_selected = debug.get("suppressed_selected_tools") or []
        omitted_critical = debug.get("omitted_critical_tools") or []

        self._write_chat(
            f"- active domains: {self._format_debug_list(active_domains)}",
            f"- active domains: {self._format_debug_list(active_domains)}",
        )
        self._write_chat(
            f"- protected domains: {self._format_debug_list(protected_domains)}",
            f"- protected domains: {self._format_debug_list(protected_domains)}",
        )
        self._write_chat(
            f"- selected tools: {self._format_debug_list(selected_tools, limit=12)}",
            f"- selected tools: {self._format_debug_list(selected_tools, limit=12)}",
        )
        self._write_chat(
            f"- pinned tools: {self._format_debug_list(pinned_tools, limit=12)}",
            f"- pinned tools: {self._format_debug_list(pinned_tools, limit=12)}",
        )
        self._write_chat(
            f"- suppressed tools: {self._format_debug_list(suppressed_tools, limit=12)}",
            f"- suppressed tools: {self._format_debug_list(suppressed_tools, limit=12)}",
        )
        self._write_chat(
            f"- suppressed tools still selected: {self._format_debug_list(suppressed_selected, limit=8)}",
            f"- suppressed tools still selected: {self._format_debug_list(suppressed_selected, limit=8)}",
        )
        self._write_chat(
            f"- omitted critical tools: {self._format_debug_list(omitted_critical, limit=12)}",
            f"- omitted critical tools: {self._format_debug_list(omitted_critical, limit=12)}",
        )
        self._write_chat(
            (
                "- selector stats: "
                f"candidates={debug.get('index_candidate_count', 0)}, "
                f"lexical_matches={debug.get('lexical_match_count', 0)}, "
                f"fallback_added={debug.get('fallback_added_count', 0)}"
            ),
            (
                "- selector stats: "
                f"candidates={debug.get('index_candidate_count', 0)}, "
                f"lexical_matches={debug.get('lexical_match_count', 0)}, "
                f"fallback_added={debug.get('fallback_added_count', 0)}"
            ),
        )

    @staticmethod
    def _format_debug_list(values: Any, *, limit: int = 8) -> str:
        if not isinstance(values, list) or not values:
            return "(none)"
        rendered = [str(item) for item in values[:limit]]
        if len(values) > limit:
            rendered.append(f"+{len(values) - limit} more")
        return ", ".join(rendered)

    async def _render_connected_servers(self) -> None:
        await self._startup_diagnostics.render_connected_servers(self)

    async def _render_comfy_server_info(self) -> None:
        await self._startup_diagnostics.render_comfy_server_info(self)

    def _render_startup_banner(self) -> None:
        for line in self._EDGAR_ASCII:
            self._write_chat(f"[bold green]{line}[/bold green]", line)
        self._write_chat(
            "[bold bright_green]Everyday Digital Graphics Assistant Robot[/bold bright_green]",
            "Everyday Digital Graphics Assistant Robot",
        )
        self._write_chat("", "")
        self._write_chat("[bold blink bright_blue]Near Future Laboratory[/bold blink bright_blue]", "Near Future Laboratory")
        for line in self._NFL_ASCII:
            self._write_chat(f"[bold blink bright_blue]{line}[/bold blink bright_blue]", line)
        self._write_chat("", "")

    def action_submit_prompt(self) -> None:
        input_widget = self.query_one("#input", TextArea)
        user_text = input_widget.text.strip()
        if not user_text:
            return
        self._record_prompt_history(user_text)
        input_widget.clear()
        self._reset_prompt_history_navigation()

        self._submit_user_text(user_text)

    def _submit_user_text(self, user_text: str) -> None:
        if not user_text:
            return

        if self._handle_local_command(user_text):
            return

        self._write_chat(f"[bold cyan]You:[/bold cyan] {user_text}", f"You: {user_text}")

        if not self._is_ready:
            self._write_chat("[yellow]Still connecting. Please wait.[/yellow]", "Still connecting. Please wait.")
            return

        self._enqueue_request(user_text)

    def _enqueue_request(self, user_text: str) -> None:
        self._request_queue.put_nowait(user_text)
        queued_ahead = max(0, self._request_queue.qsize() - 1 + self._request_inflight)
        if queued_ahead > 0:
            self._write_chat(
                f"[dim]Prompt queued ({queued_ahead} ahead).[/dim]",
                f"Prompt queued ({queued_ahead} ahead).",
            )
        if self._request_queue_worker_running:
            return
        self._request_queue_worker_running = True
        self.run_worker(self._drain_request_queue(), exclusive=False)

    async def _drain_request_queue(self) -> None:
        try:
            while not self._request_queue.empty():
                user_text = await self._request_queue.get()
                self._request_inflight = 1
                try:
                    await self._process_message(user_text)
                finally:
                    self._request_inflight = 0
                    self._turn_state = "IDLE"
                    self._request_queue.task_done()
        finally:
            self._request_queue_worker_running = False
            if not self._request_queue.empty():
                self._request_queue_worker_running = True
                self.run_worker(self._drain_request_queue(), exclusive=False)

    def _handle_local_command(self, user_text: str) -> bool:
        return self._command_handler.handle(self, user_text)

    def _show_help(self) -> None:
        self._command_handler.show_help(self)

    @staticmethod
    def _extract_local_field(text: str, pattern: str) -> tuple[str | None, str]:
        return LocalCommandHandler.extract_local_field(text, pattern)

    @staticmethod
    def _is_local_help_request(text: str) -> bool:
        return LocalCommandHandler.is_local_help_request(text)

    def _show_moodboard_usage(self) -> None:
        self._command_handler.show_moodboard_usage(self)

    def _show_aspect_usage(self) -> None:
        self._command_handler.show_aspect_usage(self)

    def _show_tanktracks_usage(self) -> None:
        self._command_handler.show_tanktracks_usage(self)

    def _show_variation_usage(self) -> None:
        self._command_handler.show_variation_usage(self)

    def _show_imageedit_usage(self) -> None:
        self._command_handler.show_imageedit_usage(self)

    def _echo_local_command(self, user_text: str) -> None:
        self._command_handler.echo_local_command(self, user_text)

    def _show_import_workflow_usage(self) -> None:
        self._command_handler.show_import_workflow_usage(self)

    @staticmethod
    def _normalize_ratio_token(value: str) -> str | None:
        return LocalCommandHandler.normalize_ratio_token(value)

    @staticmethod
    def _normalize_ratio_list(value: str | None) -> List[str]:
        return LocalCommandHandler().normalize_ratio_list(value)

    @staticmethod
    def _find_uuid_token(text: str) -> str | None:
        return LocalCommandHandler.find_uuid_token(text)

    def _run_moodboard_flow(self, user_text: str) -> None:
        self._command_handler.run_moodboard_flow(self, user_text)

    def _run_aspect_flow(self, user_text: str) -> None:
        self._command_handler.run_aspect_flow(self, user_text)

    def _run_tanktracks_flow(self, user_text: str) -> None:
        self._command_handler.run_tanktracks_flow(self, user_text)

    def _run_variation_flow(self, user_text: str) -> None:
        self._command_handler.run_variation_flow(self, user_text)

    @staticmethod
    def _strip_variation_source_prefix(text: str) -> str:
        return LocalCommandHandler.strip_variation_source_prefix(text)

    def _run_import_workflow_flow(self, user_text: str) -> None:
        self._command_handler.run_import_workflow_flow(self, user_text)

    def _run_imageedit_flow(self, user_text: str) -> None:
        self._command_handler.run_imageedit_flow(self, user_text)

    def _build_moodboard_flow_prompt(
        self,
        *,
        brief: str,
        count: int,
        palette: str | None,
        refs: List[str],
        workflow_id: str | None,
        novelty: str,
    ) -> str:
        return self._flow_prompt_builder.build_moodboard_flow_prompt(
            brief=brief,
            count=count,
            palette=palette,
            refs=refs,
            workflow_id=workflow_id,
            novelty=novelty,
        )

    def _build_aspect_flow_prompt(
        self,
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
        return self._flow_prompt_builder.build_aspect_flow_prompt(
            source_id=source_id,
            target_ratios=target_ratios,
            variant_of=variant_of,
            workflow_id=workflow_id,
            source_ratio_hint=source_ratio_hint,
            max_delta=max_delta,
            denoise_override=denoise_override,
            seed_sweep_count=seed_sweep_count,
            seed_values=seed_values,
            preserve_guidance=preserve_guidance,
            negative_guidance=negative_guidance,
        )

    @staticmethod
    def _parse_seed_values(raw: str | None) -> List[int]:
        return FlowPromptBuilder.parse_seed_values(raw)

    def _generate_random_seed_values(self, count: int) -> List[int]:
        return FlowPromptBuilder.generate_random_seed_values(count)

    def _parse_run_count_from_text(self, text: str) -> int | None:
        return self._flow_prompt_builder.parse_run_count_from_text(text)

    def _build_recent_signals_tool_grounded_lines(
        self,
        user_text: str,
        tool_events: List[Any],
    ) -> List[str] | None:
        requested_count = _parse_recent_signal_request_count(user_text, self._NUMBER_WORDS)
        if requested_count is None:
            return None

        preferred_tools = (
            # Canonical Digester tools (preferred).
            "digester_signals_list_summaries",
            "digester_get_story_queue",
            # Legacy/alternate tool surfaces.
            "editorial_signals_list_summaries",
            "editorial_signals_list",
            "editorial_get_story_queue",
            "get_story_queue",
        )

        for tool_name in preferred_tools:
            for event in reversed(tool_events):
                if event.name != tool_name or event.error:
                    continue
                items = _extract_signal_items_from_tool_result(event.result)
                if not items:
                    continue
                return _format_recent_signal_lines(items, requested_count)
        return None

    def _normalize_sweep_target(self, value: str | None) -> str | None:
        return FlowPromptBuilder.normalize_sweep_target(value)

    def _infer_variation_sweep(self, text: str) -> str | None:
        return self._flow_prompt_builder.infer_variation_sweep(text)

    def _parse_sweep_values(self, text: str) -> tuple[str | None, List[float]]:
        return self._flow_prompt_builder.parse_sweep_values(text)

    def _infer_aspect_targets_from_text(self, text: str) -> str | None:
        return self._flow_prompt_builder.infer_aspect_targets_from_text(text)

    @staticmethod
    def _parse_float_values(raw: str) -> List[float]:
        return FlowPromptBuilder.parse_float_values(raw)

    @staticmethod
    def _build_range_values(start: float, end: float, step: float) -> List[float]:
        return FlowPromptBuilder.build_range_values(start, end, step)

    @staticmethod
    def _normalize_sweep_values(param: str, values: List[float]) -> List[float]:
        return FlowPromptBuilder.normalize_sweep_values(param, values)

    def _build_tanktracks_flow_prompt(
        self,
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
        return self._flow_prompt_builder.build_tanktracks_flow_prompt(
            source_id=source_id,
            variant_of=variant_of,
            workflow_id=workflow_id,
            seed_override=seed_override,
            denoise_override=denoise_override,
            post_aspect_ratio=post_aspect_ratio,
            run_index=run_index,
            run_count=run_count,
        )

    def _build_variation_flow_prompt(
        self,
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
        return self._flow_prompt_builder.build_variation_flow_prompt(
            source_id=source_id,
            variant_of=variant_of,
            workflow_id=workflow_id,
            analysis_prompt=analysis_prompt,
            upscale=upscale,
            run_count=run_count,
            sweep_target=sweep_target,
            sweep_values=sweep_values,
            seed_values=seed_values,
            extra_instructions=extra_instructions,
        )

    def _build_import_workflow_flow_prompt(
        self,
        *,
        image_id: str,
        workflow_id: str,
        workflow_name: str,
        tags: List[str],
        description: str | None,
        photarium_mcp_url: str,
    ) -> str:
        return self._flow_prompt_builder.build_import_workflow_flow_prompt(
            image_id=image_id,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            tags=tags,
            description=description,
            photarium_mcp_url=photarium_mcp_url,
        )

    def _build_imageedit_flow_prompt(
        self,
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
        return self._flow_prompt_builder.build_imageedit_flow_prompt(
            source_id=source_id,
            variant_of=variant_of,
            workflow_id=workflow_id,
            edit_request=edit_request,
            analysis_prompt=analysis_prompt,
            seed_override=seed_override,
            post_aspect_ratio=post_aspect_ratio,
            run_index=run_index,
            run_count=run_count,
        )

    def _record_prompt_history(self, prompt: str) -> None:
        value = prompt.strip()
        if not value:
            return
        if self._prompt_history and self._prompt_history[-1] == value:
            return
        self._prompt_history.append(value)
        if len(self._prompt_history) > self._PROMPT_HISTORY_MAX:
            self._prompt_history = self._prompt_history[-self._PROMPT_HISTORY_MAX :]
        self._save_ui_preferences()

    def _reset_prompt_history_navigation(self) -> None:
        self._prompt_history_cursor = None
        self._prompt_history_draft = ""

    def _set_input_text(self, value: str) -> None:
        input_widget = self.query_one("#input", TextArea)
        input_widget.load_text(value)
        lines = value.splitlines() or [""]
        input_widget.move_cursor((len(lines) - 1, len(lines[-1])))

    def _history_prev(self) -> None:
        if not self._prompt_history:
            return
        input_widget = self.query_one("#input", TextArea)
        if self._prompt_history_cursor is None:
            self._prompt_history_draft = input_widget.text
            self._prompt_history_cursor = len(self._prompt_history) - 1
        elif self._prompt_history_cursor > 0:
            self._prompt_history_cursor -= 1
        self._set_input_text(self._prompt_history[self._prompt_history_cursor])

    def _history_next(self) -> None:
        if self._prompt_history_cursor is None:
            return
        if self._prompt_history_cursor < len(self._prompt_history) - 1:
            self._prompt_history_cursor += 1
            self._set_input_text(self._prompt_history[self._prompt_history_cursor])
            return
        self._prompt_history_cursor = None
        self._set_input_text(self._prompt_history_draft)

    def _show_status(self) -> None:
        stats = self._orchestrator.status_snapshot()
        self._write_chat("[bold]Session status:[/bold]", "Session status:")
        self._write_chat(f"- ready: {self._is_ready}", f"- ready: {self._is_ready}")
        self._write_chat(
            f"- model: {self._config.llm.model} (timeout={self._config.llm.timeout_s}s)",
            f"- model: {self._config.llm.model} (timeout={self._config.llm.timeout_s}s)",
        )
        self._write_chat(
            f"- strict_tool_facts: {self._config.strict_tool_facts}",
            f"- strict_tool_facts: {self._config.strict_tool_facts}",
        )
        self._write_chat(
            f"- response_verbosity: {self._response_verbosity}",
            f"- response_verbosity: {self._response_verbosity}",
        )
        self._write_chat(f"- discovered tools: {len(self._tools)}", f"- discovered tools: {len(self._tools)}")
        self._write_chat(
            f"- conversation messages: {stats['message_count']} (chars={stats['total_content_chars']})",
            f"- conversation messages: {stats['message_count']} (chars={stats['total_content_chars']})",
        )
        self._write_chat(f"- chat lines: {len(self._chat_history)}", f"- chat lines: {len(self._chat_history)}")
        self._write_chat(f"- tool lines: {len(self._tool_history)}", f"- tool lines: {len(self._tool_history)}")
        self._write_chat(f"- tui state: {self._turn_state}", f"- tui state: {self._turn_state}")
        self._write_chat(f"- queued prompts: {self._request_queue.qsize()}", f"- queued prompts: {self._request_queue.qsize()}")
        if self._session_log_path is not None:
            self._write_chat(
                f"- session log: {self._session_log_path}",
                f"- session log: {self._session_log_path}",
            )
        for server in self._config.servers:
            if server.transport == "http" or server.http_url:
                endpoint = server.http_url or ""
                self._write_chat(
                    f"- server {server.name}: http {endpoint}",
                    f"- server {server.name}: http {endpoint}",
                )
            else:
                self._write_chat(
                    f"- server {server.name}: stdio {server.command}",
                    f"- server {server.name}: stdio {server.command}",
                )

    def _clear_logs_widgets(self) -> None:
        try:
            self.query_one("#chat_log", RichLog).clear()
            self.query_one("#tool_log", RichLog).clear()
        except Exception:
            pass

    def _reset_conversation(self) -> None:
        self._clear_logs_widgets()
        self._chat_history.clear()
        self._tool_history.clear()
        self._orchestrator.reset_conversation()
        self._apply_runtime_system_prompt()
        self._write_chat(
            "[yellow]Conversation reset. Context cleared.[/yellow]",
            "Conversation reset. Context cleared.",
        )

    def _apply_runtime_system_prompt(self) -> None:
        system_prompt = compose_runtime_system_prompt(self._base_system_prompt, self._response_verbosity)
        self._orchestrator.set_system_prompt(system_prompt)

    def _set_response_verbosity(self, mode: ResponseVerbosity) -> None:
        if mode == self._response_verbosity:
            self._write_chat(
                f"[dim]EDGAR response mode unchanged: {mode}.[/dim]",
                f"EDGAR response mode unchanged: {mode}.",
            )
            return
        self._response_verbosity = mode
        self._apply_runtime_system_prompt()
        self._save_ui_preferences()
        self._write_chat(
            f"[green]EDGAR response mode set to {mode}.[/green]",
            f"EDGAR response mode set to {mode}.",
        )

    async def on_shutdown(self) -> None:
        self._flush_session_log_buffer()
        self._append_session_log("SYSTEM", "Session shutdown.")
        self._flush_session_log_buffer()
        close_task = asyncio.create_task(self._router.close())
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            # Finish router teardown even if shutdown cancellation is already in
            # flight, otherwise stdio async-generator finalizers can emit noisy
            # unraisable exceptions during loop teardown.
            try:
                await close_task
            except Exception:
                pass
        except Exception:
            pass

    async def _process_message(self, user_text: str, *, retried_after_tool_not_found: bool = False) -> None:
        self._turn_state = "RUNNING_LLM"
        self._write_chat("[dim]Processing request...[/dim]", "Processing request...")
        strict_tool_facts_for_turn = (
            self._config.strict_tool_facts
            and not self._should_complete_multistep_tool_flow(user_text)
        )
        if self._config.strict_tool_facts and not strict_tool_facts_for_turn:
            self._write_chat(
                "[dim]Completing multi-step write flow before showing the result.[/dim]",
                "Completing multi-step write flow before showing the result.",
            )

        # Accumulator for streamed LLM tokens (reset each round)
        _stream_buf: list[str] = []
        _stream_shown = False  # has the prefix been written for this round?
        _assistant_content_emitted = False

        def _on_progress(kind: str, payload: Dict[str, Any]) -> None:
            nonlocal _stream_buf, _stream_shown, _assistant_content_emitted

            if kind == "llm_request":
                self._turn_state = "RUNNING_LLM"
                # Reset stream buffer for each new LLM call
                _stream_buf.clear()
                _stream_shown = False
                _assistant_content_emitted = False
                round_num = payload.get("round", "?")
                self._write_chat(
                    f"[dim italic]Processing (round {round_num})...[/dim italic]",
                    f"Processing (round {round_num})...",
                )

            elif kind == "llm_token":
                # Real-time token from the streaming response
                _stream_buf.append(payload.get("text", ""))

            elif kind == "llm_response":
                # Stream finished — show the accumulated content
                content = payload.get("content")
                tool_names = payload.get("tool_names", [])
                tool_count = payload.get("tool_call_count", 0)

                if content:
                    self._write_chat(
                        f"[bold green]EDGAR:[/bold green] {content}",
                        f"EDGAR: {content}",
                    )
                    _assistant_content_emitted = True

                if tool_count > 0:
                    # Show tool calls even when reasoning text is present.
                    tool_list = ", ".join(tool_names[:5])
                    if tool_count > 5:
                        tool_list += f" (+{tool_count - 5} more)"
                    self._write_chat(
                        f"[dim]Calling: {tool_list}[/dim]",
                        f"Calling: {tool_list}",
                    )
            elif kind == "llm_tools_subset":
                selected_count = payload.get("selected_count")
                total_count = payload.get("total_count")
                dropped_count = payload.get("dropped_count")
                dropped_examples = payload.get("dropped_examples") or []
                selection_debug = payload.get("selection_debug") or {}
                if isinstance(selection_debug, dict):
                    self._last_tool_selection_debug = dict(selection_debug)
                examples_text = ""
                if isinstance(dropped_examples, list) and dropped_examples:
                    preview = ", ".join(str(item) for item in dropped_examples[:4])
                    examples_text = f" dropped examples: {preview}"
                debug_text = ""
                domain_text = ""
                critical_text = ""
                if isinstance(selection_debug, dict) and selection_debug:
                    candidates = selection_debug.get("index_candidate_count")
                    lexical_matches = selection_debug.get("lexical_match_count")
                    fallback_added = selection_debug.get("fallback_added_count")
                    if candidates is not None and lexical_matches is not None and fallback_added is not None:
                        debug_text = (
                            f" candidates={candidates}, lexical_matches={lexical_matches},"
                            f" fallback_added={fallback_added}."
                        )
                    active_domains = selection_debug.get("active_domains")
                    if isinstance(active_domains, list) and active_domains:
                        domain_text = " domains=" + ",".join(str(item) for item in active_domains[:5]) + "."
                    pinned_tools = selection_debug.get("pinned_tools")
                    if isinstance(pinned_tools, list) and pinned_tools:
                        preview = ", ".join(str(item) for item in pinned_tools[:4])
                        domain_text += f" pinned: {preview}."
                    omitted_critical = selection_debug.get("omitted_critical_tools")
                    if isinstance(omitted_critical, list) and omitted_critical:
                        preview = ", ".join(str(item) for item in omitted_critical[:4])
                        critical_text = f" omitted critical: {preview}."
                    suppressed_tools = selection_debug.get("suppressed_tools")
                    if isinstance(suppressed_tools, list) and suppressed_tools:
                        preview = ", ".join(str(item) for item in suppressed_tools[:3])
                        domain_text += f" suppressed: {preview}."
                self._write_chat(
                    (
                        "[dim]Tool window trimmed for API limits: "
                        f"{selected_count}/{total_count} sent "
                        f"({dropped_count} omitted).{debug_text}{domain_text}{critical_text}{examples_text}[/dim]"
                    ),
                    (
                        "Tool window trimmed for API limits: "
                        f"{selected_count}/{total_count} sent "
                        f"({dropped_count} omitted).{debug_text}{domain_text}{critical_text}{examples_text}"
                    ),
                )
            elif kind == "llm_tools_retry_expanded":
                reason = payload.get("reason") or "unknown"
                tool_count = payload.get("tool_count")
                self._write_chat(
                    (
                        "[dim]Retrying with expanded tool window "
                        f"(reason={reason}, tools={tool_count}).[/dim]"
                    ),
                    f"Retrying with expanded tool window (reason={reason}, tools={tool_count}).",
                )
            elif kind == "llm_tools_retry_domain_guardrail":
                domains = payload.get("domains") or []
                generic_tools = payload.get("generic_tool_names") or []
                preferred_tools = payload.get("preferred_tool_names") or []
                domain_text = ", ".join(str(item) for item in domains[:4]) if isinstance(domains, list) else str(domains)
                generic_text = ", ".join(str(item) for item in generic_tools[:3]) if isinstance(generic_tools, list) else str(generic_tools)
                preferred_text = ", ".join(str(item) for item in preferred_tools[:4]) if isinstance(preferred_tools, list) else str(preferred_tools)
                self._write_chat(
                    (
                        "[yellow]Retrying tool choice:[/yellow] "
                        f"{generic_text} is generic for {domain_text}; preferring {preferred_text}."
                    ),
                    (
                        "Retrying tool choice: "
                        f"{generic_text} is generic for {domain_text}; preferring {preferred_text}."
                    ),
                )

            elif kind == "tool_call_start":
                self._turn_state = "RUNNING_TOOLS"
                name = payload.get("name", "unknown_tool")
                args = payload.get("arguments", {})
                # Show a compact summary of args (first 100 chars)
                args_summary = json.dumps(args)
                if len(args_summary) > 100:
                    args_summary = args_summary[:97] + "..."
                self._write_tools(
                    f"[dim]Running {name}({args_summary})...[/dim]",
                    f"Running {name}({args_summary})...",
                )
                if self._is_progress_monitored_tool(name):
                    self._start_workflow_progress_monitor()
            elif kind == "tool_call_result":
                name = payload.get("name", "unknown_tool")
                self._write_tools(f"[dim]{name} completed.[/dim]", f"{name} completed.")
                if self._is_progress_monitored_tool(name):
                    self._stop_workflow_progress_monitor()
            elif kind == "tool_call_error":
                name = payload.get("name", "unknown_tool")
                error = str(payload.get("error") or "").strip()
                self._write_tools(f"[red]{name} failed.[/red]", f"{name} failed.")
                if error:
                    summary = self._summarize_tool_error(error)
                    self._write_tools(f"[red]Reason: {summary}[/red]", f"Reason: {summary}")
                    self._write_chat(
                        f"[red]{name} failed:[/red] {summary}",
                        f"{name} failed: {summary}",
                    )
                if self._is_progress_monitored_tool(name):
                    self._stop_workflow_progress_monitor()

        try:
            assistant_text, tool_events = await self._orchestrator.process(
                user_text,
                on_progress=_on_progress,
                stop_after_tool_calls=strict_tool_facts_for_turn,
            )
        except Exception as exc:
            self._write_chat(f"[red]LLM error: {exc}[/red]", f"LLM error: {exc}")
            return

        for event in tool_events:
            self._write_tools(f"[bold]Tool call:[/bold] {event.name}", f"Tool call: {event.name}")
            args_text = json.dumps(event.arguments, indent=2)
            self._write_tools(args_text, args_text)
            if event.error:
                self._write_tools("[red]Tool error:[/red]", "Tool error:")
                error_text = json.dumps({"error": event.error}, indent=2)
                self._write_tools(error_text, error_text)
            else:
                self._write_tools("[green]Result:[/green]", "Result:")
                display_result = sanitize_result(event.result, tool_name=event.name, save_artifacts=False)
                result_text = json.dumps(display_result, indent=2)
                self._write_tools(result_text, result_text)

        editorial_inventory_events = [
            event
            for event in tool_events
            if event.name == "editorial_ads_list_inventory" and not event.error
        ]
        recent_signal_lines = self._build_recent_signals_tool_grounded_lines(user_text, tool_events)
        if recent_signal_lines and not strict_tool_facts_for_turn:
            self._write_chat(
                "[bold yellow]Tool-grounded recent signals:[/bold yellow] rendering exact rows from tool JSON.",
                "Tool-grounded recent signals: rendering exact rows from tool JSON.",
            )
            for line in recent_signal_lines:
                self._write_chat(line, line)
            return

        if editorial_inventory_events and not strict_tool_facts_for_turn:
            self._write_chat(
                "[bold yellow]Tool-grounded editorial inventory listing:[/bold yellow] showing only extant entries from tool output.",
                "Tool-grounded editorial inventory listing: showing only extant entries from tool output.",
            )
            for event in editorial_inventory_events:
                lines = _format_editorial_ads_inventory_lines(event.result)
                if not lines:
                    continue
                for line in lines:
                    self._write_chat(line, line)
            return

        editorial_preview_events = [
            event
            for event in tool_events
            if event.name == "editorial_ads_preview" and not event.error
        ]
        if editorial_preview_events and not strict_tool_facts_for_turn:
            self._write_chat(
                "[bold yellow]Tool-grounded editorial preview response:[/bold yellow] showing exact preview URLs from tool output.",
                "Tool-grounded editorial preview response: showing exact preview URLs from tool output.",
            )
            for event in editorial_preview_events:
                lines = _format_editorial_ads_preview_lines(event.result)
                if not lines:
                    continue
                for line in lines:
                    self._write_chat(line, line)
            return

        if tool_events and strict_tool_facts_for_turn:
            self._write_chat(
                "[bold yellow]Tool-grounded response mode:[/bold yellow] showing exact tool outputs (no model interpretation).",
                "Tool-grounded response mode: showing exact tool outputs (no model interpretation).",
            )
            for event in tool_events:
                self._write_chat(f"[bold]{event.name}[/bold]", event.name)
                if event.error:
                    error_text = json.dumps({"error": event.error}, indent=2)
                    self._write_chat(error_text, error_text)
                else:
                    display_result = sanitize_result(event.result, tool_name=event.name, save_artifacts=False)
                    result_text = json.dumps(display_result, indent=2)
                    self._write_chat(result_text, result_text)
            return

        tool_failures = [event for event in tool_events if event.error]
        tool_successes = [event for event in tool_events if not event.error]
        if (
            tool_failures
            and not tool_successes
            and not retried_after_tool_not_found
            and any("Tool not found:" in str(event.error or "") for event in tool_failures)
        ):
            self._write_chat(
                "[yellow]Detected tool registry mismatch. Reconnecting tools and retrying once...[/yellow]",
                "Detected tool registry mismatch. Reconnecting tools and retrying once...",
            )
            reconnected = await self._reconnect_tools(allow_when_busy=True)
            if reconnected:
                await self._process_message(user_text, retried_after_tool_not_found=True)
                return

        if tool_failures and not tool_successes:
            self._write_chat(
                "[red]Tool execution failed:[/red] no successful tool results were produced.",
                "Tool execution failed: no successful tool results were produced.",
            )
            for event in tool_failures[:3]:
                self._write_chat(
                    f"[red]- {event.name}: {event.error}[/red]",
                    f"- {event.name}: {event.error}",
                )
            if len(tool_failures) > 3:
                remaining = len(tool_failures) - 3
                self._write_chat(
                    f"[red]- ... and {remaining} more failures[/red]",
                    f"- ... and {remaining} more failures",
                )
            return

        if tool_failures:
            self._write_chat(
                "[yellow]Completed with tool errors.[/yellow] Verify outputs and check tool log details.",
                "Completed with tool errors. Verify outputs and check tool log details.",
            )

        if assistant_text and not _assistant_content_emitted:
            self._write_chat(
                f"[bold green]EDGAR:[/bold green] {assistant_text}",
                f"EDGAR: {assistant_text}",
            )
            return

        self._write_chat("[yellow]No response content returned.[/yellow]", "No response content returned.")

    @staticmethod
    def _should_complete_multistep_tool_flow(user_text: str) -> bool:
        text = (user_text or "").lower()
        mutation_terms = (
            "add",
            "append",
            "create",
            "draft",
            "edit",
            "generate",
            "insert",
            "materialize",
            "move",
            "save",
            "stub",
            "update",
            "write",
            "written",
        )
        if not any(term in text for term in mutation_terms):
            return False

        editorial_terms = (
            "article",
            "content file",
            "dek",
            "editorial",
            "feature",
            "frontmatter",
            "hierarchy",
            "issue",
            "mdx",
            "rubric",
            "section",
            "src/content/editorial",
        )
        newsletter_terms = (
            "newsletter",
            "food-for-thought",
            "food for thought",
            "dense-discovery",
            "section schema",
            "outbox",
        )
        return any(term in text for term in editorial_terms) or any(term in text for term in newsletter_terms)

    @staticmethod
    def _is_progress_monitored_tool(tool_name: str) -> bool:
        return tool_name in {"workflows_run", "workflows_run_aspect_ratio_adjustment"}

    @staticmethod
    def _summarize_tool_error(error: str, max_len: int = 320) -> str:
        text = " ".join(str(error).split())
        marker = "body="
        if marker in text:
            text = text.split(marker, 1)[1].strip() or text
        if len(text) > max_len:
            text = text[: max_len - 1] + "…"
        return text

    def _start_workflow_progress_monitor(self) -> None:
        if not isinstance(self._router, HTTPToolRouter):
            return
        if "workflows_status" not in self._available_tool_names:
            return
        self._workflow_progress_active_calls += 1
        if self._workflow_progress_active_calls > 1:
            return
        self._workflow_progress_monitor_generation += 1
        generation = self._workflow_progress_monitor_generation
        self.run_worker(self._monitor_workflow_progress(generation), exclusive=False)

    def _stop_workflow_progress_monitor(self) -> None:
        if self._workflow_progress_active_calls <= 0:
            return
        self._workflow_progress_active_calls -= 1

    async def _monitor_workflow_progress(self, generation: int) -> None:
        last_progress_signature: tuple[Any, ...] | None = None
        last_progress_log_at: float = 0.0
        last_percent_logged: float | None = None
        percent_step = 5.0 if self._low_churn_mode else 2.0
        min_log_interval_s = 1.2 if self._low_churn_mode else 0.5
        poll_sleep_s = 0.9 if self._low_churn_mode else 0.5
        self._write_tools("[dim]ComfyUI progress monitor started.[/dim]", "ComfyUI progress monitor started.")
        while generation == self._workflow_progress_monitor_generation and self._workflow_progress_active_calls > 0:
            try:
                payload = await self._router.call_tool(
                    "workflows_status",
                    {"include_progress": True, "progress_timeout_s": 0.9},
                )
            except Exception:
                await asyncio.sleep(1.0)
                continue

            if not isinstance(payload, dict):
                await asyncio.sleep(0.6)
                continue

            progress = payload.get("progress")
            queue_running = payload.get("queue_running_count")
            queue_pending = payload.get("queue_pending_count")
            state = payload.get("status")
            if isinstance(progress, dict):
                prompt_id = progress.get("prompt_id")
                node = progress.get("node")
                percent = progress.get("percent")
                event_type = progress.get("type")
                now = asyncio.get_running_loop().time()
                structural_signature = (prompt_id, node, event_type, queue_running, queue_pending, state)
                percent_value: float | None = None
                if isinstance(percent, (int, float)):
                    try:
                        percent_value = float(percent)
                    except Exception:
                        percent_value = None

                should_log = False
                if structural_signature != last_progress_signature:
                    should_log = True
                elif percent_value is not None:
                    if last_percent_logged is None:
                        should_log = True
                    elif abs(percent_value - last_percent_logged) >= percent_step and (now - last_progress_log_at) >= min_log_interval_s:
                        should_log = True

                if should_log:
                    percent_text = f"{percent_value:.1f}%" if percent_value is not None else "n/a"
                    prompt_short = str(prompt_id)[:8] if isinstance(prompt_id, str) else "n/a"
                    node_text = str(node) if node is not None else "n/a"
                    message = (
                        f"Comfy progress: state={state} prompt={prompt_short} "
                        f"node={node_text} progress={percent_text} "
                        f"queue(running={queue_running}, pending={queue_pending})"
                    )
                    self._write_tools(f"[dim]{message}[/dim]", message)
                    last_progress_signature = structural_signature
                    last_progress_log_at = now
                    last_percent_logged = percent_value
            await asyncio.sleep(poll_sleep_s)
        self._write_tools("[dim]ComfyUI progress monitor stopped.[/dim]", "ComfyUI progress monitor stopped.")

    def _write_chat(self, markup_text: str, plain_text: str) -> None:
        chat_log = self._chat_log_widget or self.query_one("#chat_log", RichLog)
        render_markup, render_plain = self._prepare_log_entry(markup_text, plain_text)
        chat_log.write(render_markup)
        self._chat_history.append(render_plain)
        if len(self._chat_history) > self._LOG_MAX_LINES:
            self._chat_history = self._chat_history[-self._LOG_MAX_LINES :]
        self._append_session_log("CHAT", render_plain)

    def _write_tools(self, markup_text: str, plain_text: str) -> None:
        tool_log = self._tool_log_widget or self.query_one("#tool_log", RichLog)
        render_markup, render_plain = self._prepare_log_entry(markup_text, plain_text)
        tool_log.write(render_markup)
        self._tool_history.append(render_plain)
        if len(self._tool_history) > self._LOG_MAX_LINES:
            self._tool_history = self._tool_history[-self._LOG_MAX_LINES :]
        self._append_session_log("TOOL", render_plain)

    def _prepare_log_entry(self, markup_text: str, plain_text: str) -> tuple[str, str]:
        clipped_plain = self._clip_text_for_widget(plain_text)
        if clipped_plain == plain_text:
            return markup_text, plain_text
        # Avoid emitting malformed Rich markup when clipping in the middle of tags.
        return rich_markup_escape(clipped_plain), clipped_plain

    def _clip_text_for_widget(self, text: str) -> str:
        if not text:
            return text
        original_lines = text.splitlines()
        clipped_lines: List[str] = []
        max_lines = self._LOG_RENDER_MAX_LINES
        max_chars = self._LOG_RENDER_MAX_CHARS
        consumed_chars = 0
        truncated = False

        for index, line in enumerate(original_lines):
            if len(clipped_lines) >= max_lines:
                truncated = True
                break
            remaining_chars = max_chars - consumed_chars
            if remaining_chars <= 0:
                truncated = True
                break

            if len(line) > remaining_chars:
                clipped_lines.append(line[:remaining_chars])
                truncated = True
                break

            clipped_lines.append(line)
            consumed_chars += len(line) + 1
            if index < len(original_lines) - 1 and consumed_chars >= max_chars:
                truncated = True
                break

        clipped = "\n".join(clipped_lines)
        if not truncated:
            return text

        omitted_lines = max(0, len(original_lines) - len(clipped_lines))
        clipped_chars = len(clipped)
        summary = (
            f"[truncated for UI: {clipped_chars}/{len(text)} chars"
            f", omitted_lines={omitted_lines}]"
        )
        if clipped:
            return f"{clipped}\n{summary}"
        return summary

    def _clip_text_for_session_log(self, text: str) -> str:
        if len(text) <= self._SESSION_LOG_MAX_CHARS:
            return text
        kept = text[: self._SESSION_LOG_MAX_CHARS]
        return (
            f"{kept}\n"
            f"[truncated for session log: {self._SESSION_LOG_MAX_CHARS}/{len(text)} chars]"
        )

    def _init_session_log(self) -> None:
        self._session_log_manager.init_session_log(self)

    def _append_session_log(self, channel: str, text: str) -> None:
        self._session_log_manager.append_session_log(self, channel, text)

    def _flush_session_log_buffer(self) -> None:
        self._session_log_manager.flush_session_log_buffer(self)

    def _load_ui_preferences(self) -> None:
        self._session_log_manager.load_ui_preferences(self)

    def _save_ui_preferences(self) -> None:
        self._session_log_manager.save_ui_preferences(self)

    def _copy_text(self, text: str, label: str) -> None:
        cleaned = _strip_border_glyphs(text)
        if not cleaned.strip():
            self.notify(f"No {label} content to copy.", severity="warning")
            return
        if _clipboard_copy(cleaned):
            self.notify(f"Copied {label} to clipboard.")
        else:
            # Fall back to Textual's OSC 52 (works in some terminals)
            self.copy_to_clipboard(cleaned)
            self.notify(f"Copied {label} to clipboard (OSC 52).")

    def action_copy_chat(self) -> None:
        self._copy_text("\n".join(self._chat_history), "chat")

    def action_copy_tools(self) -> None:
        self._copy_text("\n".join(self._tool_history), "tools")

    def action_copy_all(self) -> None:
        self._copy_text(self._build_transcript(), "chat + tools")

    def action_export_transcript(self) -> None:
        transcript = self._build_transcript()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path.cwd() / f"mcp_chat_transcript_{timestamp}.txt"
        path.write_text(transcript, encoding="utf-8")
        self.notify(f"Saved transcript: {path}")

    def _build_transcript(self) -> str:
        return "\n".join(
            [
                "=== Chat ===",
                "\n".join(self._chat_history),
                "",
                "=== Tools ===",
                "\n".join(self._tool_history),
            ]
        )

    def _active_log(self) -> RichLog:
        if self._active_pane == "chat":
            return self.query_one("#chat_log", RichLog)
        return self.query_one("#tool_log", RichLog)

    def _set_active_pane(self, pane: Literal["chat", "tools"]) -> None:
        self._active_pane = pane
        chat_log = self.query_one("#chat_log", RichLog)
        tool_log = self.query_one("#tool_log", RichLog)
        if pane == "chat":
            chat_log.add_class("pane-active")
            chat_log.styles.display = "block"
            tool_log.remove_class("pane-active")
            tool_log.styles.display = "none"
        else:
            tool_log.add_class("pane-active")
            tool_log.styles.display = "block"
            chat_log.remove_class("pane-active")
            chat_log.styles.display = "none"

    def action_focus_chat(self) -> None:
        self._set_active_pane("chat")
        self._focus_input()
        self.notify("[F1] Chat  |  F2 Tools")

    def action_focus_tools(self) -> None:
        self._set_active_pane("tools")
        self._focus_input()
        self.notify("F1 Chat  |  [F2] Tools")

    def action_toggle_pane(self) -> None:
        if self._active_pane == "chat":
            self.action_focus_tools()
        else:
            self.action_focus_chat()

    def action_pane_scroll_up(self) -> None:
        self._active_log().action_scroll_up()
        self._focus_input()

    def action_pane_scroll_down(self) -> None:
        self._active_log().action_scroll_down()
        self._focus_input()

    def action_pane_page_up(self) -> None:
        self._active_log().action_page_up()
        self._focus_input()

    def action_pane_page_down(self) -> None:
        self._active_log().action_page_down()
        self._focus_input()

    def action_pane_home(self) -> None:
        self._active_log().action_scroll_home()
        self._focus_input()

    def action_pane_end(self) -> None:
        self._active_log().action_scroll_end()
        self._focus_input()

    def action_input_height_increase(self) -> None:
        self._set_input_height(self._input_height_lines + 1)

    def action_input_height_decrease(self) -> None:
        self._set_input_height(self._input_height_lines - 1)

    def action_input_word_left(self) -> None:
        self.query_one("#input", TextArea).action_cursor_word_left()

    def action_input_word_right(self) -> None:
        self.query_one("#input", TextArea).action_cursor_word_right()

    def action_input_delete_word_left(self) -> None:
        self.query_one("#input", TextArea).action_delete_word_left()

    def action_input_delete_word_right(self) -> None:
        self.query_one("#input", TextArea).action_delete_word_right()

    def _set_input_height(
        self,
        height_lines: int,
        *,
        announce: bool = True,
        persist: bool = True,
    ) -> None:
        clamped = max(self._MIN_INPUT_HEIGHT, min(self._MAX_INPUT_HEIGHT, int(height_lines)))
        self._input_height_lines = clamped
        input_widget = self.query_one("#input", TextArea)
        input_widget.styles.height = clamped
        if persist:
            self._save_ui_preferences()
        if announce:
            self.notify(f"Input height: {clamped} lines")

    def _focus_input(self) -> None:
        try:
            self.query_one("#input", TextArea).focus()
        except Exception:
            return

    def _to_openai_tool(self, spec) -> Dict[str, Any]:
        description = self._augment_tool_description(spec.name, spec.description)
        parameters = _normalize_openai_tool_schema(spec.input_schema)
        if spec.name == "workflows_run":
            parameters = ensure_workflows_run_schema(parameters)
        return {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": description,
                "parameters": parameters,
            },
        }

    def _augment_tool_description(self, tool_name: str, base_description: str) -> str:
        hints = self._matching_tool_hints(tool_name)
        description = (base_description or "").strip()
        if not hints:
            return description
        if not description:
            return " ".join(hints)
        return f"{description} {' '.join(hints)}"

    def _matching_tool_hints(self, tool_name: str) -> List[str]:
        raw_hints = self._config.tool_description_hints or {}
        hints: List[str] = []
        for pattern, hint in raw_hints.items():
            if not isinstance(pattern, str) or not isinstance(hint, str):
                continue
            hint_text = hint.strip()
            if not hint_text:
                continue
            if pattern.endswith("*"):
                prefix = pattern[:-1]
                if prefix and tool_name.startswith(prefix):
                    hints.append(hint_text)
                continue
            if pattern == tool_name:
                hints.append(hint_text)
        return hints


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MCP TUI chat client")
    parser.add_argument(
        "--config",
        default="mcp_chat_config.json",
        help="Path to MCP TUI config JSON",
    )
    parser.add_argument(
        "--mouse",
        action="store_true",
        help="Enable mouse capture (default is off to allow terminal text selection).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override llm.model for this run only (e.g. gpt-4o, gpt-5.3-codex).",
    )
    return parser.parse_args()


def _best_effort_terminal_restore() -> None:
    """Attempt to restore terminal private modes after interrupted TUI exit."""
    try:
        # Disable common mouse tracking modes, focus events, bracketed paste,
        # reset attributes, show cursor, and leave application cursor mode.
        os.write(
            1,
            (
                b"\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1004l\x1b[?1005l"
                b"\x1b[?1006l\x1b[?1015l\x1b[?2004l\x1b[?1l\x1b[?25h\x1b[0m"
            ),
        )
    except Exception:
        pass
    try:
        subprocess.run(["stty", "sane"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _is_known_stdio_asyncgen_unraisable(unraisable: Any) -> bool:
    err_msg = getattr(unraisable, "err_msg", "") or ""
    if "closing of asynchronous generator" not in str(err_msg):
        return False

    obj = getattr(unraisable, "object", None)
    try:
        object_repr = repr(obj)
    except Exception:
        object_repr = ""
    if "stdio_client" not in object_repr:
        return False

    exc = getattr(unraisable, "exc_value", None)
    return isinstance(exc, BaseException) and _is_ignorable_stdio_close_error(exc)


def _is_known_stdio_asyncgen_shutdown_context(context: Dict[str, Any]) -> bool:
    message = context.get("message", "") or ""
    if "closing of asynchronous generator" not in str(message):
        return False

    asyncgen = context.get("asyncgen")
    try:
        asyncgen_repr = repr(asyncgen)
    except Exception:
        asyncgen_repr = ""
    if "stdio_client" not in asyncgen_repr:
        return False

    exc = context.get("exception")
    return isinstance(exc, BaseException) and _is_ignorable_stdio_close_error(exc)


@contextmanager
def _suppress_known_stdio_asyncgen_unraisables():
    previous_hook = sys.unraisablehook

    def _hook(unraisable: Any) -> None:
        if _is_known_stdio_asyncgen_unraisable(unraisable):
            return
        previous_hook(unraisable)

    sys.unraisablehook = _hook
    try:
        yield
    finally:
        sys.unraisablehook = previous_hook


def main() -> None:
    args = _parse_args()
    # Prefer the safer keyboard path by default; advanced kitty protocol can
    # cause dropped/doubled keypresses on some terminal+Textual combinations.
    if (
        "EDGAR_TUI_FORCE_KITTY_KEYBOARD" not in os.environ
        and "EDGAR_TUI_DISABLE_KITTY_KEYBOARD" not in os.environ
    ):
        os.environ["EDGAR_TUI_DISABLE_KITTY_KEYBOARD"] = "1"
    config = load_config(args.config, model_override=args.model)
    app = ChatApp(config)
    try:
        with _suppress_known_stdio_asyncgen_unraisables():
            app.run(mouse=args.mouse)
    except KeyboardInterrupt:
        _best_effort_terminal_restore()
        raise
    except BaseException:
        _best_effort_terminal_restore()
        raise
    finally:
        _best_effort_terminal_restore()


if __name__ == "__main__":
    main()
