"""Textual TUI for MCP tool orchestration."""

from __future__ import annotations

import asyncio
import argparse
import json
import os
import platform
import re
import subprocess
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal

from textual.app import App, ComposeResult
from textual import events
from textual.driver import Driver
from textual.widgets import Footer, Header, RichLog, TextArea

from comfy_mcp.tui_client.config import DEFAULT_SYSTEM_PROMPT, ChatClientConfig, load_config
from comfy_mcp.tui_client.llm_client import OpenAIClient
from comfy_mcp.tui_client.orchestrator import ChatOrchestrator
from comfy_mcp.tui_client.mcp_router import MCPToolRouter
from comfy_mcp.tui_client.http_router import HTTPToolRouter
from comfy_mcp.tui_client.sanitize import sanitize_result

try:
    from textual.drivers.linux_driver import LinuxDriver as _TextualLinuxDriver
except Exception:  # pragma: no cover - platform/import dependent
    _TextualLinuxDriver = None


class HistoryTextArea(TextArea):
    """TextArea with shell-style history traversal on Up/Down at boundaries."""

    def __init__(
        self,
        *args: Any,
        on_history_prev: Callable[[], None] | None = None,
        on_history_next: Callable[[], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._on_history_prev = on_history_prev
        self._on_history_next = on_history_next

    def action_cursor_up(self) -> None:
        row, _ = self.cursor_location
        if row == 0 and self._on_history_prev is not None:
            self._on_history_prev()
            return
        super().action_cursor_up()

    def action_cursor_down(self) -> None:
        row, _ = self.cursor_location
        last_row = max(0, self.document.line_count - 1)
        if row >= last_row and self._on_history_next is not None:
            self._on_history_next()
            return
        super().action_cursor_down()

    async def _on_key(self, event: events.Key) -> None:
        logger = getattr(self.app, "_key_debug_write", None)
        if callable(logger):
            logger(
                "input_key",
                key=event.key,
                name=event.name,
                character=event.character,
                is_printable=bool(getattr(event, "is_printable", False)),
                is_repeat=getattr(event, "is_repeat", None),
            )
        await super()._on_key(event)

    def on_focus(self, event: events.Focus) -> None:
        logger = getattr(self.app, "_key_debug_write", None)
        if callable(logger):
            logger("input_focus")

    def on_blur(self, event: events.Blur) -> None:
        logger = getattr(self.app, "_key_debug_write", None)
        if callable(logger):
            logger("input_blur")


class PassiveRichLog(RichLog):
    """Log pane that won't steal keyboard focus from the input composer."""

    can_focus = False


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


def _clipboard_copy(text: str) -> bool:
    """Copy text to system clipboard. Returns True on success."""
    system = platform.system()
    if system == "Darwin" and shutil.which("pbcopy"):
        cmd = ["pbcopy"]
    elif system == "Linux" and shutil.which("xclip"):
        cmd = ["xclip", "-selection", "clipboard"]
    elif system == "Linux" and shutil.which("xsel"):
        cmd = ["xsel", "--clipboard", "--input"]
    elif system == "Linux" and shutil.which("wl-copy"):
        cmd = ["wl-copy"]
    elif shutil.which("clip.exe"):  # WSL
        cmd = ["clip.exe"]
    else:
        return False
    try:
        subprocess.run(cmd, input=text.encode("utf-8"), check=True, timeout=5)
        return True
    except Exception:
        return False


def _strip_border_glyphs(text: str) -> str:
    """Strip common Textual border glyphs from copied text lines."""
    cleaned_lines: List[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"^\s*[│┃║]\s?", "", raw_line.rstrip())
        line = re.sub(r"\s*[▁▂▃▄▅▆▇█]?\s*[│┃║]\s*[▁▂▃▄▅▆▇█]?$", "", line)
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


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


def _build_router(config: ChatClientConfig):
    """Pick HTTP or stdio router based on server config."""
    http_servers = [s for s in config.servers if s.transport == "http" or s.http_url]
    stdio_servers = [s for s in config.servers if s.transport != "http" and not s.http_url]
    if stdio_servers:
        # If any server needs stdio, fall back to the original MCPToolRouter
        return MCPToolRouter(config.servers)
    # All servers are HTTP — use the lightweight HTTP router
    return HTTPToolRouter(http_servers)


class ChatApp(App):
    _DEFAULT_INPUT_HEIGHT = 6
    _MIN_INPUT_HEIGHT = 4
    _MAX_INPUT_HEIGHT = 18
    _LOG_MAX_LINES = 2000
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
        self._router = _build_router(config)
        self._llm = OpenAIClient(config.llm)
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
        self._prompt_history: List[str] = []
        self._prompt_history_cursor: int | None = None
        self._prompt_history_draft: str = ""
        self._active_pane: Literal["chat", "tools"] = "chat"
        self._session_log_path: Path | None = None
        self._workflow_progress_active_calls: int = 0
        self._workflow_progress_monitor_generation: int = 0
        self._ui_prefs_path = Path.cwd() / self._UI_PREFS_FILENAME
        self._input_height_lines: int = self._DEFAULT_INPUT_HEIGHT
        self._key_debug_enabled = os.environ.get("EDGAR_TUI_KEY_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
        self._key_debug_log_path = Path.cwd() / ".mcp_chat_logs" / "edgar_key_debug.jsonl"
        self._load_ui_preferences()

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
        self._init_session_log()
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

    async def _startup(self) -> None:
        self._render_startup_banner()
        self._write_chat("[bold]Connecting to MCP servers...[/bold]", "Connecting to MCP servers...")
        try:
            await self._router.connect()
        except Exception as exc:
            self._write_chat(f"[red]Failed to connect: {exc}[/red]", f"Failed to connect: {exc}")
            return
        await self._render_connected_servers()

        self._tools = [self._to_openai_tool(spec) for spec in self._router.list_tool_specs()]
        self._available_tool_names = {
            str(tool.get("function", {}).get("name", ""))
            for tool in self._tools
            if isinstance(tool, dict)
        }
        self._orchestrator.set_tools(self._tools)
        self._write_tools("[bold]Tools ready:[/bold]", "Tools ready:")
        for tool in self._tools:
            self._write_tools(f"- {tool['function']['name']}", f"- {tool['function']['name']}")
        self._is_ready = True
        self._write_chat("[green]Ready. Type a request below.[/green]", "Ready. Type a request below.")
        self._write_chat(
            "[dim]Tip: Opt+Left/Right moves by word. Ctrl+Shift+Up/Down resizes the input box.[/dim]",
            "Tip: Opt+Left/Right moves by word. Ctrl+Shift+Up/Down resizes the input box.",
        )

    async def _render_connected_servers(self) -> None:
        health_fetcher = getattr(self._router, "get_server_health_statuses", None)
        if not callable(health_fetcher):
            return
        try:
            statuses = await health_fetcher()
        except Exception as exc:
            self._write_chat(
                f"[yellow]Connected, but failed to read server health details: {exc}[/yellow]",
                f"Connected, but failed to read server health details: {exc}",
            )
            return
        if not isinstance(statuses, list) or not statuses:
            return

        self._write_chat("[bold]Connected servers:[/bold]", "Connected servers:")
        for status in statuses:
            if not isinstance(status, dict):
                continue
            name = str(status.get("name") or "unknown")
            base_url = str(status.get("base_url") or "")
            ok = bool(status.get("ok"))
            payload = status.get("payload")
            payload_dict = payload if isinstance(payload, dict) else {}
            runtime_status = str(payload_dict.get("status") or ("ok" if ok else "unknown"))
            version = payload_dict.get("service_version")
            commit = payload_dict.get("git_commit")
            branch = payload_dict.get("git_branch")
            dirty = payload_dict.get("git_dirty")
            detail_parts: list[str] = [f"status={runtime_status}"]
            if isinstance(version, str) and version:
                detail_parts.append(f"version={version}")
            if isinstance(commit, str) and commit:
                detail_parts.append(f"commit={commit}")
            if isinstance(branch, str) and branch:
                detail_parts.append(f"branch={branch}")
            if isinstance(dirty, bool):
                detail_parts.append(f"dirty={'yes' if dirty else 'no'}")
            details = ", ".join(detail_parts)
            plain = f"- {name} ({base_url}) {details}"
            self._write_chat(f"[dim]{plain}[/dim]", plain)

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

        self.run_worker(self._process_message(user_text), exclusive=False)

    def _handle_local_command(self, user_text: str) -> bool:
        command = user_text.strip().lower()
        command_name = command.split(maxsplit=1)[0] if command else ""
        if command_name == "/reset":
            self._reset_conversation()
            return True
        if command_name == "/help":
            self._show_help()
            return True
        if command_name == "/status":
            self._show_status()
            return True
        if command_name in {"/aspect", "/ar", "/aspectratio"}:
            self._run_aspect_flow(user_text)
            return True
        if command_name in {"/importwf", "/importworkflow"}:
            self._run_import_workflow_flow(user_text)
            return True
        if command_name == "/moodboard":
            self._run_moodboard_flow(user_text)
            return True
        if command_name == "/tanktracks":
            self._run_tanktracks_flow(user_text)
            return True
        return False

    def _show_help(self) -> None:
        self._write_chat("[bold]Local commands:[/bold]", "Local commands:")
        self._write_chat("- [cyan]/help[/cyan] show available local commands", "- /help show available local commands")
        self._write_chat("- [cyan]/status[/cyan] show current TUI session status", "- /status show current TUI session status")
        self._write_chat("- [cyan]/reset[/cyan] clear chat/tools and reset LLM context", "- /reset clear chat/tools and reset LLM context")
        self._write_chat(
            "- [cyan]/moodboard <brief>[/cyan] run an agentic mood-board flow",
            "- /moodboard <brief> run an agentic mood-board flow",
        )
        self._write_chat(
            "- [cyan]/aspect <image_id> targets=...[/cyan] (aliases: [cyan]/ar[/cyan], [cyan]/aspectratio[/cyan]) run source-anchored aspect-ratio flow",
            "- /aspect <image_id> targets=... (aliases: /ar, /aspectratio) run source-anchored aspect-ratio flow",
        )
        self._write_chat(
            "- [cyan]/importwf <image_id> [id=workflow_id][/cyan] (alias: [cyan]/importworkflow[/cyan]) import embedded Photarium workflow into catalog",
            "- /importwf <image_id> [id=workflow_id] (alias: /importworkflow) import embedded Photarium workflow into catalog",
        )
        self._write_chat(
            "- [cyan]/tanktracks <image_id>[/cyan] run the add-tank-tracks variant flow",
            "- /tanktracks <image_id> run the add-tank-tracks variant flow",
        )
        self._write_chat("[bold]Keyboard shortcuts:[/bold]", "Keyboard shortcuts:")
        self._write_chat("- [cyan]F1 / F2[/cyan] focus Chat / Tools pane", "- F1 / F2 focus Chat / Tools pane")
        self._write_chat(
            "- [cyan]Alt+Up / Alt+Down[/cyan] scroll active pane",
            "- Alt+Up / Alt+Down scroll active pane",
        )
        self._write_chat(
            "- [cyan]Alt+Shift+Up / Alt+Shift+Down[/cyan] page active pane",
            "- Alt+Shift+Up / Alt+Shift+Down page active pane",
        )
        self._write_chat(
            "- [cyan]Cmd+Up / Cmd+Down[/cyan] jump to top / bottom of active pane",
            "- Cmd+Up / Cmd+Down jump to top / bottom of active pane",
        )
        self._write_chat("- [cyan]Opt+Left / Opt+Right[/cyan] move cursor by word", "- Opt+Left / Opt+Right move cursor by word")
        self._write_chat(
            "- [cyan]Opt+Backspace / Opt+D[/cyan] delete previous / next word",
            "- Opt+Backspace / Opt+D delete previous / next word",
        )
        self._write_chat(
            "- [cyan]Ctrl+Shift+Up / Ctrl+Shift+Down[/cyan] increase/decrease input height",
            "- Ctrl+Shift+Up / Ctrl+Shift+Down increase/decrease input height",
        )
        self._write_chat("- [cyan]F5 / Ctrl+S[/cyan] send message", "- F5 / Ctrl+S send message")
        self._write_chat("- [cyan]Ctrl+Enter[/cyan] send message (when terminal supports it)", "- Ctrl+Enter send message (when terminal supports it)")
        self._write_chat("- [cyan]F6 / F7 / F8[/cyan] copy Chat / Tools / All", "- F6 / F7 / F8 copy Chat / Tools / All")
        self._write_chat("[dim]History: Up/Down in input recalls prior prompts.[/dim]", "History: Up/Down in input recalls prior prompts.")
        self._write_chat("[dim]Composer: Enter newline, use F5/Ctrl+S to send (Ctrl+Enter when supported).[/dim]", "Composer: Enter newline, use F5/Ctrl+S to send (Ctrl+Enter when supported).")

    @staticmethod
    def _extract_local_field(text: str, pattern: str) -> tuple[str | None, str]:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            return None, text
        value = (match.group(1) or "").strip()
        updated = f"{text[:match.start()]} {text[match.end():]}".strip()
        updated = re.sub(r"\s+", " ", updated)
        return value or None, updated

    @staticmethod
    def _is_local_help_request(text: str) -> bool:
        return text.strip().lower() in {"help", "-h", "--help", "/?"}

    def _show_moodboard_usage(self) -> None:
        self._write_chat("[bold]Moodboard Flow Usage[/bold]", "Moodboard Flow Usage")
        self._write_chat(
            "[dim]/moodboard <brief text> [count=12] [palette=#87CEEB] [refs=id1,id2] [workflow=image_edit] [novelty=high][/dim]",
            "/moodboard <brief text> [count=12] [palette=#87CEEB] [refs=id1,id2] [workflow=image_edit] [novelty=high]",
        )
        self._write_chat(
            "[dim]Example: /moodboard premium athleisure campaign count=16 palette=#C7A66A[/dim]",
            "Example: /moodboard premium athleisure campaign count=16 palette=#C7A66A",
        )

    def _show_aspect_usage(self) -> None:
        self._write_chat("[bold]Aspect Flow Usage[/bold]", "Aspect Flow Usage")
        self._write_chat(
            (
                "[dim]/aspect|/ar|/aspectratio <image_id> targets=16:9,4:5,3:2,9:16 "
                "[workflow=aspect_ratio_adjustment] [parent=<image_id>] [source=1:1] "
                "[max_delta=0.45] [denoise=1.0] [sweep=3] [preserve=\"...\"] [negative=\"...\"][/dim]"
            ),
            (
                "/aspect|/ar|/aspectratio <image_id> targets=16:9,4:5,3:2,9:16 "
                "[workflow=aspect_ratio_adjustment] [parent=<image_id>] [source=1:1] "
                "[max_delta=0.45] [denoise=1.0] [sweep=3] [preserve=\"...\"] [negative=\"...\"]"
            ),
        )
        self._write_chat(
            "[dim]Example: /aspectratio 75e92a7e-2838-45a7-6f2c-32a5fde6c300 target=9:16 denoise 1 sweep three different seed values[/dim]",
            "Example: /aspectratio 75e92a7e-2838-45a7-6f2c-32a5fde6c300 target=9:16 denoise 1 sweep three different seed values",
        )

    def _show_tanktracks_usage(self) -> None:
        self._write_chat("[bold]Tank Tracks Flow Usage[/bold]", "Tank Tracks Flow Usage")
        self._write_chat(
            "[dim]/tanktracks <image_id> [parent=<image_id>] [workflow=add_tank_tracks] [prompt=\"...\"][/dim]",
            "/tanktracks <image_id> [parent=<image_id>] [workflow=add_tank_tracks] [prompt=\"...\"]",
        )
        self._write_chat(
            "[dim]Example: /tanktracks 1cc224eb-022b-4ce9-0dd8-3f274f4f4300[/dim]",
            "Example: /tanktracks 1cc224eb-022b-4ce9-0dd8-3f274f4f4300",
        )

    def _show_import_workflow_usage(self) -> None:
        self._write_chat("[bold]Import Workflow Flow Usage[/bold]", "Import Workflow Flow Usage")
        self._write_chat(
            (
                "[dim]/importwf|/importworkflow <image_id> [id=workflow_id] [name=\"...\"] "
                "[tags=tag1,tag2] [desc=\"...\"] [url=http://127.0.0.1:8787][/dim]"
            ),
            (
                "/importwf|/importworkflow <image_id> [id=workflow_id] [name=\"...\"] "
                "[tags=tag1,tag2] [desc=\"...\"] [url=http://127.0.0.1:8787]"
            ),
        )
        self._write_chat(
            "[dim]Example: /importwf b287f5ef-2901-4e27-f6b4-b483fc4a7e00 id=tank_tracks_v1 tags=photarium,imported desc=\"Tank tracks edit workflow\"[/dim]",
            "Example: /importwf b287f5ef-2901-4e27-f6b4-b483fc4a7e00 id=tank_tracks_v1 tags=photarium,imported desc=\"Tank tracks edit workflow\"",
        )

    @staticmethod
    def _normalize_ratio_token(value: str) -> str | None:
        match = re.search(r"(\d+)\s*[:xX]\s*(\d+)", value)
        if not match:
            return None
        width = int(match.group(1))
        height = int(match.group(2))
        if width <= 0 or height <= 0:
            return None
        return f"{width}:{height}"

    @staticmethod
    def _normalize_ratio_list(value: str | None) -> List[str]:
        if not value:
            return []
        normalized: List[str] = []
        for chunk in value.split(","):
            token = ChatApp._normalize_ratio_token(chunk.strip())
            if token and token not in normalized:
                normalized.append(token)
        return normalized

    def _run_moodboard_flow(self, user_text: str) -> None:
        raw = user_text.strip()
        parts = raw.split(maxsplit=1)
        remainder = parts[1].strip() if len(parts) > 1 else ""
        if self._is_local_help_request(remainder):
            self._show_moodboard_usage()
            return

        count_value, remainder = self._extract_local_field(
            remainder,
            r"(?:^|\s)(?:count|n)\s*=\s*(\d{1,2})(?=\s|$)",
        )
        palette_value, remainder = self._extract_local_field(
            remainder,
            r"(?:^|\s)(?:palette|color)\s*=\s*(#[0-9a-fA-F]{6})(?=\s|$)",
        )
        refs_value, remainder = self._extract_local_field(
            remainder,
            r"(?:^|\s)(?:refs|images)\s*=\s*([A-Za-z0-9,_-]+)(?=\s|$)",
        )
        workflow_value, remainder = self._extract_local_field(
            remainder,
            r"(?:^|\s)workflow\s*=\s*([A-Za-z0-9._-]+)(?=\s|$)",
        )
        novelty_value, remainder = self._extract_local_field(
            remainder,
            r"(?:^|\s)novelty\s*=\s*(low|medium|high)(?=\s|$)",
        )

        brief = remainder.strip()
        refs = [item.strip() for item in (refs_value or "").split(",") if item.strip()]
        count = 12
        if count_value is not None:
            try:
                count = max(3, min(40, int(count_value)))
            except ValueError:
                count = 12

        if not brief and not refs:
            self._show_moodboard_usage()
            return

        flow_prompt = self._build_moodboard_flow_prompt(
            brief=brief,
            count=count,
            palette=palette_value,
            refs=refs,
            workflow_id=workflow_value,
            novelty=novelty_value or "high",
        )

        summary = brief or "reference-led mood board"
        self._write_chat(
            f"[bold cyan]Moodboard Flow:[/bold cyan] {summary} (targets {count} variants)",
            f"Moodboard Flow: {summary} (targets {count} variants)",
        )
        if not self._is_ready:
            self._write_chat("[yellow]Still connecting. Please wait.[/yellow]", "Still connecting. Please wait.")
            return
        self.run_worker(self._process_message(flow_prompt), exclusive=False)

    def _run_aspect_flow(self, user_text: str) -> None:
        raw = user_text.strip()
        parts = raw.split(maxsplit=1)
        remainder = parts[1].strip() if len(parts) > 1 else ""
        if self._is_local_help_request(remainder):
            self._show_aspect_usage()
            return

        targets_value, remainder = self._extract_local_field(
            remainder,
            r"(?:^|\s)(?:targets|target|ratios|ratio)\s*=\s*([0-9xX:,_-]+)(?=\s|$)",
        )
        workflow_value, remainder = self._extract_local_field(
            remainder,
            r"(?:^|\s)workflow\s*=\s*([A-Za-z0-9._-]+)(?=\s|$)",
        )
        parent_value, remainder = self._extract_local_field(
            remainder,
            r"(?:^|\s)(?:parent|variant_of|upload_to)\s*=\s*([A-Za-z0-9-]+)(?=\s|$)",
        )
        source_ratio_value, remainder = self._extract_local_field(
            remainder,
            r"(?:^|\s)(?:source|source_ratio)\s*=\s*([0-9xX:]+)(?=\s|$)",
        )
        max_delta_value, remainder = self._extract_local_field(
            remainder,
            r"(?:^|\s)(?:max_delta|max_step)\s*=\s*([0-9]*\.?[0-9]+)(?=\s|$)",
        )
        denoise_value, remainder = self._extract_local_field(
            remainder,
            r"(?:^|\s)denoise\s*=\s*([0-9]*\.?[0-9]+)(?=\s|$)",
        )
        if denoise_value is None:
            denoise_value, remainder = self._extract_local_field(
                remainder,
                r"(?:^|\s)denoise\s+([0-9]*\.?[0-9]+)(?=\s|$)",
            )
        seed_values_text, remainder = self._extract_local_field(
            remainder,
            r"(?:^|\s)(?:seeds?|seed_values?)\s*=\s*([0-9,\s]+)(?=\s|$)",
        )
        sweep_value, remainder = self._extract_local_field(
            remainder,
            r"(?:^|\s)sweep\s*=\s*(\d{1,2})(?=\s|$)",
        )
        if sweep_value is None:
            sweep_value, remainder = self._extract_local_field(
                remainder,
                r"(?:^|\s)sweep\s+(\d{1,2})(?=\s|$)",
            )
        if sweep_value is None:
            sweep_word, remainder = self._extract_local_field(
                remainder,
                r"(?:^|\s)sweep\s+([A-Za-z]+)\b",
            )
            if sweep_word:
                sweep_value = str(self._NUMBER_WORDS.get(sweep_word.lower(), ""))
        preserve_value, remainder = self._extract_local_field(
            remainder,
            r'(?:^|\s)(?:preserve|positive|guidance)\s*=\s*"([^"]+)"(?=\s|$)',
        )
        if preserve_value is None:
            preserve_value, remainder = self._extract_local_field(
                remainder,
                r"(?:^|\s)(?:preserve|positive|guidance)\s*=\s*'([^']+)'(?=\s|$)",
            )
        negative_value, remainder = self._extract_local_field(
            remainder,
            r'(?:^|\s)(?:negative|avoid)\s*=\s*"([^"]+)"(?=\s|$)',
        )
        if negative_value is None:
            negative_value, remainder = self._extract_local_field(
                remainder,
                r"(?:^|\s)(?:negative|avoid)\s*=\s*'([^']+)'(?=\s|$)",
            )

        source_id = remainder.split(maxsplit=1)[0] if remainder else ""
        source_id = source_id.strip()
        target_ratios = self._normalize_ratio_list(targets_value)
        source_ratio_hint = self._normalize_ratio_token(source_ratio_value or "")
        seed_values = self._parse_seed_values(seed_values_text)
        seed_sweep_count = 0
        if seed_values:
            seed_sweep_count = len(seed_values)
        elif sweep_value:
            try:
                seed_sweep_count = max(0, min(24, int(sweep_value)))
            except ValueError:
                seed_sweep_count = 0
        denoise_override: float | None = None
        if denoise_value is not None:
            try:
                denoise_override = max(0.0, min(1.0, float(denoise_value)))
            except ValueError:
                denoise_override = None

        max_delta = 0.45
        if max_delta_value is not None:
            try:
                max_delta = max(0.1, min(1.5, float(max_delta_value)))
            except ValueError:
                max_delta = 0.45

        if not source_id or not target_ratios:
            self._show_aspect_usage()
            return

        workflow_id = workflow_value or "aspect_ratio_adjustment"
        variant_of = parent_value or source_id
        flow_prompt = self._build_aspect_flow_prompt(
            source_id=source_id,
            target_ratios=target_ratios,
            variant_of=variant_of,
            workflow_id=workflow_id,
            source_ratio_hint=source_ratio_hint,
            max_delta=max_delta,
            denoise_override=denoise_override,
            seed_sweep_count=seed_sweep_count,
            seed_values=seed_values,
            preserve_guidance=preserve_value,
            negative_guidance=negative_value,
        )
        self._write_chat(
            f"[bold cyan]Aspect Flow:[/bold cyan] source={source_id} targets={', '.join(target_ratios)}",
            f"Aspect Flow: source={source_id} targets={', '.join(target_ratios)}",
        )
        if not self._is_ready:
            self._write_chat("[yellow]Still connecting. Please wait.[/yellow]", "Still connecting. Please wait.")
            return
        self.run_worker(self._process_message(flow_prompt), exclusive=False)

    def _run_tanktracks_flow(self, user_text: str) -> None:
        raw = user_text.strip()
        parts = raw.split(maxsplit=1)
        remainder = parts[1].strip() if len(parts) > 1 else ""
        if self._is_local_help_request(remainder):
            self._show_tanktracks_usage()
            return

        variant_value, remainder = self._extract_local_field(
            remainder,
            r"(?:^|\s)(?:parent|variant_of|upload_to)\s*=\s*([A-Za-z0-9-]+)(?=\s|$)",
        )
        workflow_value, remainder = self._extract_local_field(
            remainder,
            r"(?:^|\s)workflow\s*=\s*([A-Za-z0-9._-]+)(?=\s|$)",
        )
        prompt_value, remainder = self._extract_local_field(
            remainder,
            r'(?:^|\s)(?:prompt|instruction)\s*=\s*"([^"]+)"(?=\s|$)',
        )
        if prompt_value is None:
            prompt_value, remainder = self._extract_local_field(
                remainder,
                r"(?:^|\s)(?:prompt|instruction)\s*=\s*'([^']+)'(?=\s|$)",
            )

        source_id = remainder.split(maxsplit=1)[0] if remainder else ""
        source_id = source_id.strip()
        if not source_id:
            self._show_tanktracks_usage()
            return

        variant_of = variant_value or source_id
        workflow_id = workflow_value or "add_tank_tracks"

        flow_prompt = self._build_tanktracks_flow_prompt(
            source_id=source_id,
            variant_of=variant_of,
            workflow_id=workflow_id,
            prompt_override=prompt_value,
        )
        self._write_chat(
            f"[bold cyan]Tank Tracks Flow:[/bold cyan] source={source_id} variant_of={variant_of}",
            f"Tank Tracks Flow: source={source_id} variant_of={variant_of}",
        )
        if not self._is_ready:
            self._write_chat("[yellow]Still connecting. Please wait.[/yellow]", "Still connecting. Please wait.")
            return
        self.run_worker(self._process_message(flow_prompt), exclusive=False)

    def _run_import_workflow_flow(self, user_text: str) -> None:
        raw = user_text.strip()
        parts = raw.split(maxsplit=1)
        remainder = parts[1].strip() if len(parts) > 1 else ""
        if self._is_local_help_request(remainder):
            self._show_import_workflow_usage()
            return

        workflow_id_value, remainder = self._extract_local_field(
            remainder,
            r"(?:^|\s)(?:id|workflow|workflow_id)\s*=\s*([A-Za-z0-9_-]+)(?=\s|$)",
        )
        tags_value, remainder = self._extract_local_field(
            remainder,
            r"(?:^|\s)tags\s*=\s*([A-Za-z0-9,_-]+)(?=\s|$)",
        )
        mcp_url_value, remainder = self._extract_local_field(
            remainder,
            r"(?:^|\s)(?:url|mcp|photarium_url)\s*=\s*(https?://[^\s]+)(?=\s|$)",
        )
        name_value, remainder = self._extract_local_field(
            remainder,
            r'(?:^|\s)name\s*=\s*"([^"]+)"(?=\s|$)',
        )
        if name_value is None:
            name_value, remainder = self._extract_local_field(
                remainder,
                r"(?:^|\s)name\s*=\s*'([^']+)'(?=\s|$)",
            )
        desc_value, remainder = self._extract_local_field(
            remainder,
            r'(?:^|\s)(?:desc|description)\s*=\s*"([^"]+)"(?=\s|$)',
        )
        if desc_value is None:
            desc_value, remainder = self._extract_local_field(
                remainder,
                r"(?:^|\s)(?:desc|description)\s*=\s*'([^']+)'(?=\s|$)",
            )

        image_id = remainder.split(maxsplit=1)[0] if remainder else ""
        image_id = image_id.strip()
        if not image_id:
            self._show_import_workflow_usage()
            return

        workflow_id = workflow_id_value or f"photarium_{image_id.split('-')[0]}_workflow"
        workflow_name = name_value or workflow_id
        tags = [item.strip() for item in (tags_value or "photarium,imported").split(",") if item.strip()]
        mcp_url = mcp_url_value or "http://127.0.0.1:8787"

        flow_prompt = self._build_import_workflow_flow_prompt(
            image_id=image_id,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            tags=tags,
            description=desc_value,
            photarium_mcp_url=mcp_url,
        )
        self._write_chat(
            f"[bold cyan]Import Workflow Flow:[/bold cyan] image={image_id} id={workflow_id}",
            f"Import Workflow Flow: image={image_id} id={workflow_id}",
        )
        if not self._is_ready:
            self._write_chat("[yellow]Still connecting. Please wait.[/yellow]", "Still connecting. Please wait.")
            return
        self.run_worker(self._process_message(flow_prompt), exclusive=False)

    @staticmethod
    def _build_moodboard_flow_prompt(
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
    def _build_aspect_flow_prompt(
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
            "Keep the same subject, scene, composition intent, lighting, and style. "
            "Only adjust framing/outpaint to reach the target ratio."
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
    def _parse_seed_values(raw: str | None) -> List[int]:
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
    def _build_tanktracks_flow_prompt(
        *,
        source_id: str,
        variant_of: str,
        workflow_id: str,
        prompt_override: str | None,
    ) -> str:
        prompt_text = prompt_override or "use workflow default prompt"
        return (
            "TANK TRACKS FLOW REQUEST\n"
            "Run this as an agentic multi-step flow inside the TUI.\n\n"
            f"Source catalog image ID: {source_id}\n"
            f"Requested upload target image ID: {variant_of}\n"
            f"Workflow preference: {workflow_id}\n"
            f"Prompt override: {prompt_text}\n\n"
            "Execution rules:\n"
            "1. Retrieve the source image from Photarium by canonical image ID (not display name).\n"
            "2. Confirm workflow capability and parameters before execution.\n"
            "3. Determine source dimensions before running: prefer Photarium width/height metadata; otherwise call workflows_image_info on the downloaded file.\n"
            "4. Run the selected image-edit workflow against the downloaded image.\n"
            "5. Preserve source aspect ratio by setting workflow aspect controls to match source ratio (custom_ratio=true, custom_aspect_ratio=W:H, and nearest valid aspect_ratio anchor if required).\n"
            "6. Apply prompt override if provided; otherwise keep workflow defaults.\n"
            "7. Resolve effective upload parent before uploading: call photarium_get on the source image; if source has parent_id, use that parent_id, otherwise use source image ID.\n"
            "8. If upload to requested target fails parent/variant validation, retry once using the resolved effective parent from step 7.\n"
            "9. Upload the best output image as a variant of the effective parent image ID.\n"
            "10. Report the uploaded catalog image ID, effective parent ID, source ratio used, and concise tool-call trace.\n"
            "11. Do not ask for CLI scripts or manual user steps; complete with available MCP tools.\n"
        )

    @staticmethod
    def _build_import_workflow_flow_prompt(
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
        self._write_chat(f"- discovered tools: {len(self._tools)}", f"- discovered tools: {len(self._tools)}")
        self._write_chat(
            f"- conversation messages: {stats['message_count']} (chars={stats['total_content_chars']})",
            f"- conversation messages: {stats['message_count']} (chars={stats['total_content_chars']})",
        )
        self._write_chat(f"- chat lines: {len(self._chat_history)}", f"- chat lines: {len(self._chat_history)}")
        self._write_chat(f"- tool lines: {len(self._tool_history)}", f"- tool lines: {len(self._tool_history)}")
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
        self._write_chat(
            "[yellow]Conversation reset. Context cleared.[/yellow]",
            "Conversation reset. Context cleared.",
        )

    async def on_shutdown(self) -> None:
        self._append_session_log("SYSTEM", "Session shutdown.")
        await self._router.close()

    async def _process_message(self, user_text: str) -> None:
        self._write_chat("[dim]Processing request...[/dim]", "Processing request...")

        # Accumulator for streamed LLM tokens (reset each round)
        _stream_buf: list[str] = []
        _stream_shown = False  # has the prefix been written for this round?
        _assistant_content_emitted = False

        def _on_progress(kind: str, payload: Dict[str, Any]) -> None:
            nonlocal _stream_buf, _stream_shown, _assistant_content_emitted

            if kind == "llm_request":
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

            elif kind == "tool_call_start":
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
                self._write_tools(f"[red]{name} failed.[/red]", f"{name} failed.")
                if self._is_progress_monitored_tool(name):
                    self._stop_workflow_progress_monitor()

        try:
            assistant_text, tool_events = await self._orchestrator.process(
                user_text,
                on_progress=_on_progress,
                stop_after_tool_calls=self._config.strict_tool_facts,
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
        if editorial_inventory_events and not self._config.strict_tool_facts:
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
        if editorial_preview_events and not self._config.strict_tool_facts:
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

        if tool_events and self._config.strict_tool_facts:
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
    def _is_progress_monitored_tool(tool_name: str) -> bool:
        return tool_name in {"workflows_run", "workflows_run_aspect_ratio_adjustment"}

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
        last_signature: tuple[Any, ...] | None = None
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
                signature = (prompt_id, node, percent, event_type, queue_running, queue_pending, state)
                if signature != last_signature:
                    percent_text = f"{float(percent):.1f}%" if isinstance(percent, (int, float)) else "n/a"
                    prompt_short = str(prompt_id)[:8] if isinstance(prompt_id, str) else "n/a"
                    node_text = str(node) if node is not None else "n/a"
                    message = (
                        f"Comfy progress: state={state} prompt={prompt_short} "
                        f"node={node_text} progress={percent_text} "
                        f"queue(running={queue_running}, pending={queue_pending})"
                    )
                    self._write_tools(f"[dim]{message}[/dim]", message)
                    last_signature = signature
            await asyncio.sleep(0.5)
        self._write_tools("[dim]ComfyUI progress monitor stopped.[/dim]", "ComfyUI progress monitor stopped.")

    def _write_chat(self, markup_text: str, plain_text: str) -> None:
        chat_log = self.query_one("#chat_log", RichLog)
        chat_log.write(markup_text)
        self._chat_history.append(plain_text)
        self._append_session_log("CHAT", plain_text)

    def _write_tools(self, markup_text: str, plain_text: str) -> None:
        tool_log = self.query_one("#tool_log", RichLog)
        tool_log.write(markup_text)
        self._tool_history.append(plain_text)
        self._append_session_log("TOOL", plain_text)

    def _init_session_log(self) -> None:
        if self._session_log_path is not None:
            return
        logs_dir = Path.cwd() / ".mcp_chat_logs"
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        session_log_path = logs_dir / f"chat_{timestamp}.log"
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
            header = "\n".join(
                [
                    f"Session started: {datetime.now().isoformat(timespec='seconds')}",
                    f"Model: {self._config.llm.model}",
                    "===",
                    "",
                ]
            )
            session_log_path.write_text(header, encoding="utf-8")
            self._session_log_path = session_log_path
        except Exception:
            # Logging should never block chat operation.
            self._session_log_path = None

    def _append_session_log(self, channel: str, text: str) -> None:
        if self._session_log_path is None:
            return
        timestamp = datetime.now().isoformat(timespec="seconds")
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
        line = f"[{timestamp}] {channel}: {normalized}\n"
        try:
            with self._session_log_path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except Exception:
            return

    def _load_ui_preferences(self) -> None:
        try:
            payload = json.loads(self._ui_prefs_path.read_text(encoding="utf-8"))
        except Exception:
            return
        raw_height = payload.get("input_height_lines")
        if isinstance(raw_height, (int, float)):
            clamped = max(self._MIN_INPUT_HEIGHT, min(self._MAX_INPUT_HEIGHT, int(raw_height)))
            self._input_height_lines = clamped
        raw_prompt_history = payload.get("prompt_history")
        if isinstance(raw_prompt_history, list):
            normalized: List[str] = []
            for item in raw_prompt_history:
                if not isinstance(item, str):
                    continue
                text = item.strip()
                if not text:
                    continue
                normalized.append(text)
            if normalized:
                self._prompt_history = normalized[-self._PROMPT_HISTORY_MAX :]

    def _save_ui_preferences(self) -> None:
        payload = {
            "input_height_lines": self._input_height_lines,
            "prompt_history": self._prompt_history[-self._PROMPT_HISTORY_MAX :],
        }
        try:
            self._ui_prefs_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        except Exception:
            return

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
        return {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": description,
                "parameters": spec.input_schema or {"type": "object", "properties": {}},
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


def main() -> None:
    args = _parse_args()
    config = load_config(args.config, model_override=args.model)
    app = ChatApp(config)
    app.run(mouse=args.mouse)


if __name__ == "__main__":
    main()
