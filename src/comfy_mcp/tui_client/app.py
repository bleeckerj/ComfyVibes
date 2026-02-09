"""Textual TUI for MCP tool orchestration."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Input, RichLog

from comfy_mcp.tui_client.config import DEFAULT_SYSTEM_PROMPT, ChatClientConfig, load_config
from comfy_mcp.tui_client.llm_client import OpenAIClient
from comfy_mcp.tui_client.orchestrator import ChatOrchestrator
from comfy_mcp.tui_client.mcp_router import MCPToolRouter


class ChatApp(App):
    CSS = """
    Screen {
        layout: vertical;
    }

    #chat_log {
        width: 2fr;
        border: round #4455aa;
    }
    #chat_log.pane-active {
        border: round #88aaff;
    }

    #tool_log {
        width: 1fr;
        border: round #2a8a4a;
    }
    #tool_log.pane-active {
        border: round #6bf08f;
    }

    #input {
        border: round #666666;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+c", "quit", "Quit"),
        ("f10", "quit", "Quit"),
        ("escape", "quit", "Quit"),
        ("f1", "focus_chat", "Chat Pane"),
        ("f2", "focus_tools", "Tools Pane"),
        ("alt+up", "pane_scroll_up", "Pane Up"),
        ("alt+down", "pane_scroll_down", "Pane Down"),
        ("alt+pageup", "pane_page_up", "Pane Page Up"),
        ("alt+pagedown", "pane_page_down", "Pane Page Down"),
        ("alt+home", "pane_home", "Pane Top"),
        ("alt+end", "pane_end", "Pane Bottom"),
        ("f6", "copy_chat", "Copy Chat"),
        ("f7", "copy_tools", "Copy Tools"),
        ("f8", "copy_all", "Copy All"),
        ("f9", "export_transcript", "Export Transcript"),
    ]

    def __init__(self, config: ChatClientConfig):
        super().__init__()
        self._config = config
        self._router = MCPToolRouter(config.servers)
        self._llm = OpenAIClient(config.llm)
        self._tools: List[Dict[str, Any]] = []
        self._orchestrator = ChatOrchestrator(
            config.system_prompt or DEFAULT_SYSTEM_PROMPT,
            self._llm,
            self._router,
        )
        self._is_ready = False
        self._chat_history: List[str] = []
        self._tool_history: List[str] = []
        self._active_pane: Literal["chat", "tools"] = "chat"

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield RichLog(id="chat_log", wrap=True, markup=True)
            yield RichLog(id="tool_log", wrap=True, markup=True)
        yield Input(id="input", placeholder="Ask for a workflow, search, or tool call...")
        yield Footer()

    async def on_mount(self) -> None:
        self._set_active_pane("chat")
        # Connect in the app task so teardown happens in the same task context.
        await self._startup()

    async def _startup(self) -> None:
        self._write_chat("[bold]Connecting to MCP servers...[/bold]", "Connecting to MCP servers...")
        try:
            await self._router.connect()
        except Exception as exc:
            self._write_chat(f"[red]Failed to connect: {exc}[/red]", f"Failed to connect: {exc}")
            return

        self._tools = [self._to_openai_tool(spec) for spec in self._router.list_tool_specs()]
        self._orchestrator.set_tools(self._tools)
        self._write_tools("[bold]Tools ready:[/bold]", "Tools ready:")
        for tool in self._tools:
            self._write_tools(f"- {tool['function']['name']}", f"- {tool['function']['name']}")
        self._is_ready = True
        self._write_chat("[green]Ready. Type a request below.[/green]", "Ready. Type a request below.")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        user_text = event.value.strip()
        if not user_text:
            return
        event.input.value = ""
        self._write_chat(f"[bold cyan]You:[/bold cyan] {user_text}", f"You: {user_text}")

        if not self._is_ready:
            self._write_chat("[yellow]Still connecting. Please wait.[/yellow]", "Still connecting. Please wait.")
            return

        self.run_worker(self._process_message(user_text), exclusive=False)

    async def on_shutdown(self) -> None:
        await self._router.close()

    async def _process_message(self, user_text: str) -> None:
        self._write_chat("[dim]Processing request...[/dim]", "Processing request...")

        def _on_progress(kind: str, payload: Dict[str, Any]) -> None:
            if kind == "llm_request":
                self._write_chat("[dim]Assistant is thinking...[/dim]", "Assistant is thinking...")
            elif kind == "tool_call_start":
                name = payload.get("name", "unknown_tool")
                self._write_tools(f"[dim]Running {name}...[/dim]", f"Running {name}...")
            elif kind == "tool_call_result":
                name = payload.get("name", "unknown_tool")
                self._write_tools(f"[dim]{name} completed.[/dim]", f"{name} completed.")
            elif kind == "tool_call_error":
                name = payload.get("name", "unknown_tool")
                self._write_tools(f"[red]{name} failed.[/red]", f"{name} failed.")

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
                result_text = json.dumps(event.result, indent=2)
                self._write_tools(result_text, result_text)

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
                    result_text = json.dumps(event.result, indent=2)
                    self._write_chat(result_text, result_text)
            return

        if assistant_text:
            self._write_chat(
                f"[bold green]Assistant:[/bold green] {assistant_text}",
                f"Assistant: {assistant_text}",
            )
            return

        self._write_chat("[yellow]No response content returned.[/yellow]", "No response content returned.")

    def _write_chat(self, markup_text: str, plain_text: str) -> None:
        chat_log = self.query_one("#chat_log", RichLog)
        chat_log.write(markup_text)
        self._chat_history.append(plain_text)

    def _write_tools(self, markup_text: str, plain_text: str) -> None:
        tool_log = self.query_one("#tool_log", RichLog)
        tool_log.write(markup_text)
        self._tool_history.append(plain_text)

    def _copy_text(self, text: str, label: str) -> None:
        if not text.strip():
            self.notify(f"No {label} content to copy.", severity="warning")
            return
        self.copy_to_clipboard(text)
        self.notify(f"Copied {label} to clipboard.")

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
            tool_log.remove_class("pane-active")
        else:
            tool_log.add_class("pane-active")
            chat_log.remove_class("pane-active")

    def action_focus_chat(self) -> None:
        self._set_active_pane("chat")
        self.notify("Active pane: chat")

    def action_focus_tools(self) -> None:
        self._set_active_pane("tools")
        self.notify("Active pane: tools")

    def action_pane_scroll_up(self) -> None:
        self._active_log().action_scroll_up()

    def action_pane_scroll_down(self) -> None:
        self._active_log().action_scroll_down()

    def action_pane_page_up(self) -> None:
        self._active_log().action_page_up()

    def action_pane_page_down(self) -> None:
        self._active_log().action_page_down()

    def action_pane_home(self) -> None:
        self._active_log().action_scroll_home()

    def action_pane_end(self) -> None:
        self._active_log().action_scroll_end()

    def _to_openai_tool(self, spec) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.input_schema or {"type": "object", "properties": {}},
            },
        }


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
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    config = load_config(args.config)
    app = ChatApp(config)
    app.run(mouse=args.mouse)


if __name__ == "__main__":
    main()
