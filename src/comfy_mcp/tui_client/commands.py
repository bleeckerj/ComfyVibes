"""Local command parsing and flow dispatch for the TUI app."""

from __future__ import annotations

import re
from typing import Any


class LocalCommandHandler:
    def handle(self, app: Any, user_text: str) -> bool:
        command = user_text.strip().lower()
        command_name = command.split(maxsplit=1)[0] if command else ""
        if self.handle_runtime_verbosity_command(app, user_text):
            return True
        if command_name == "/reset":
            app._reset_conversation()
            return True
        if command_name == "/help":
            self.show_help(app)
            return True
        if command_name == "/status":
            app._show_status()
            return True
        if command_name in {"/showtoolstate", "/toolstate", "/toolsstate"}:
            app._show_tool_state()
            return True
        if command_name in {"/toolselection", "/selection", "/lasttools"}:
            app._show_last_tool_selection()
            return True
        if command_name in {"/turnoff", "/turnon"}:
            self._handle_tool_toggle(app, user_text, command_name == "/turnon")
            return True
        if command_name in {"/aspect", "/ar", "/aspectratio"}:
            self.run_aspect_flow(app, user_text)
            return True
        if command_name in {"/importwf", "/importworkflow"}:
            self.run_import_workflow_flow(app, user_text)
            return True
        if command_name in {"/imageedit", "/imgedit", "/editimg"}:
            self.run_imageedit_flow(app, user_text)
            return True
        if command_name in {"/vary", "/variation", "/variations"}:
            self.run_variation_flow(app, user_text)
            return True
        if command_name == "/moodboard":
            self.run_moodboard_flow(app, user_text)
            return True
        if command_name in {"/tanktracks", "/tanktrack"}:
            self.run_tanktracks_flow(app, user_text)
            return True
        return False

    def _handle_tool_toggle(self, app: Any, user_text: str, enabled: bool) -> None:
        raw = user_text.strip()
        _, _, remainder = raw.partition(" ")
        token = remainder.strip()
        if not token:
            app._write_chat(
                "[yellow]Usage:[/yellow] /turnon <server> or /turnoff <server> (try /showtoolstate)",
                "Usage: /turnon <server> or /turnoff <server> (try /showtoolstate)",
            )
            return
        resolved = app._resolve_server_token(token)
        if not resolved:
            app._write_chat(
                f"[red]Unknown server:[/red] {token}. Try /showtoolstate",
                f"Unknown server: {token}. Try /showtoolstate",
            )
            return
        if not app._set_server_enabled(resolved, enabled):
            app._write_chat(
                f"[red]Unable to update server state:[/red] {resolved}",
                f"Unable to update server state: {resolved}",
            )
            return
        state = "ON" if enabled else "OFF"
        color = "green" if enabled else "red"
        app._write_chat(f"[{color}]Server {resolved}: {state}.[/{color}]", f"Server {resolved}: {state}.")
        # Block new prompts immediately so requests cannot race with the reconnect worker.
        app._is_ready = False
        app.run_worker(app._reconnect_tools(), exclusive=False)

    def handle_runtime_verbosity_command(self, app: Any, user_text: str) -> bool:
        raw = user_text.strip()
        lowered = raw.lower()
        if not lowered:
            return False
        if lowered.startswith("/verbosity"):
            _, _, remainder = raw.partition(" ")
            return self._apply_verbosity_mode(app, remainder)
        match = re.fullmatch(
            r"(?:/edgar\s+)?edgar\s+(?:be|just\s+be|just)\s+(loud|verbose|lowkey|quiet|quieter|whisper|whispering)\.?",
            lowered,
        )
        if match:
            return self._apply_verbosity_mode(app, match.group(1))
        return False

    @staticmethod
    def _apply_verbosity_mode(app: Any, requested_mode: str) -> bool:
        mode = LocalCommandHandler.normalize_verbosity_mode(requested_mode)
        if mode is None:
            app._write_chat(
                "[yellow]Verbosity modes:[/yellow] loud, lowkey, quiet.",
                "Verbosity modes: loud, lowkey, quiet.",
            )
            return True
        app._set_response_verbosity(mode)
        return True

    @staticmethod
    def normalize_verbosity_mode(value: str | None) -> str | None:
        lowered = str(value or "").strip().lower()
        alias_map = {
            "loud": "loud",
            "verbose": "loud",
            "lowkey": "lowkey",
            "low-key": "lowkey",
            "quiet": "quiet",
            "quieter": "quiet",
            "whisper": "quiet",
            "whispering": "quiet",
        }
        return alias_map.get(lowered)

    def show_help(self, app: Any) -> None:
        app._write_chat("[bold]Local commands:[/bold]", "Local commands:")
        app._write_chat("- [cyan]/help[/cyan] show available local commands", "- /help show available local commands")
        app._write_chat("- [cyan]/status[/cyan] show current TUI session status", "- /status show current TUI session status")
        app._write_chat("- [cyan]/reset[/cyan] clear chat/tools and reset LLM context", "- /reset clear chat/tools and reset LLM context")
        app._write_chat("- [cyan]/showtoolstate[/cyan] show which servers are ON/OFF", "- /showtoolstate show which servers are ON/OFF")
        app._write_chat(
            "- [cyan]/toolselection[/cyan] show selector diagnostics for the last turn",
            "- /toolselection show selector diagnostics for the last turn",
        )
        app._write_chat("- [cyan]/turnon <server>[/cyan] enable a tool server", "- /turnon <server> enable a tool server")
        app._write_chat("- [cyan]/turnoff <server>[/cyan] disable a tool server", "- /turnoff <server> disable a tool server")
        app._write_chat(
            "- [cyan]/verbosity loud|lowkey|quiet[/cyan] change EDGAR response style at runtime",
            "- /verbosity loud|lowkey|quiet change EDGAR response style at runtime",
        )
        app._write_chat(
            "- [cyan]Natural language:[/cyan] `EDGAR be loud`, `EDGAR be lowkey`, `EDGAR just whisper`",
            "- Natural language: `EDGAR be loud`, `EDGAR be lowkey`, `EDGAR just whisper`",
        )
        app._write_chat(
            "- [cyan]/moodboard <brief>[/cyan] run an agentic mood-board flow",
            "- /moodboard <brief> run an agentic mood-board flow",
        )
        app._write_chat(
            "- [cyan]/aspect <image_id> targets=...[/cyan] (aliases: [cyan]/ar[/cyan], [cyan]/aspectratio[/cyan]) run source-anchored aspect-ratio flow",
            "- /aspect <image_id> targets=... (aliases: /ar, /aspectratio) run source-anchored aspect-ratio flow",
        )
        app._write_chat(
            "- [cyan]/importwf <image_id> [id=workflow_id][/cyan] (alias: [cyan]/importworkflow[/cyan]) import embedded Photarium workflow into catalog",
            "- /importwf <image_id> [id=workflow_id] (alias: /importworkflow) import embedded Photarium workflow into catalog",
        )
        app._write_chat(
            "- [cyan]/tanktracks|/tanktrack <image_id>[/cyan] run the add-tank-tracks variant flow",
            "- /tanktracks|/tanktrack <image_id> run the add-tank-tracks variant flow (supports seed/denoise sweeps)",
        )
        app._write_chat(
            "- [cyan]/imageedit <image_id> <edit request>[/cyan] run a flexible image-edit workflow flow",
            "- /imageedit <image_id> <edit request> run a flexible image-edit workflow flow",
        )
        app._write_chat(
            "- [cyan]/vary <image_id>[/cyan] run the image-variation flow",
            "- /vary <image_id> run the image-variation flow",
        )
        app._write_chat(
            "- [cyan]Natural language:[/cyan] `run the workflow from image <id|path|url>` uses lineage recovery via `workflows_run_from_source`",
            "- Natural language: `run the workflow from image <id|path|url>` uses lineage recovery via `workflows_run_from_source`",
        )
        app._write_chat("[bold]Keyboard shortcuts:[/bold]", "Keyboard shortcuts:")
        app._write_chat("- [cyan]F1 / F2[/cyan] focus Chat / Tools pane", "- F1 / F2 focus Chat / Tools pane")
        app._write_chat(
            "- [cyan]Alt+Up / Alt+Down[/cyan] scroll active pane",
            "- Alt+Up / Alt+Down scroll active pane",
        )
        app._write_chat(
            "- [cyan]Alt+Shift+Up / Alt+Shift+Down[/cyan] page active pane",
            "- Alt+Shift+Up / Alt+Shift+Down page active pane",
        )
        app._write_chat(
            "- [cyan]Cmd+Up / Cmd+Down[/cyan] jump to top / bottom of active pane",
            "- Cmd+Up / Cmd+Down jump to top / bottom of active pane",
        )
        app._write_chat("- [cyan]Opt+Left / Opt+Right[/cyan] move cursor by word", "- Opt+Left / Opt+Right move cursor by word")
        app._write_chat(
            "- [cyan]Opt+Backspace / Opt+D[/cyan] delete previous / next word",
            "- Opt+Backspace / Opt+D delete previous / next word",
        )
        app._write_chat(
            "- [cyan]Ctrl+Shift+Up / Ctrl+Shift+Down[/cyan] increase/decrease input height",
            "- Ctrl+Shift+Up / Ctrl+Shift+Down increase/decrease input height",
        )
        app._write_chat("- [cyan]F5 / Ctrl+S[/cyan] send message", "- F5 / Ctrl+S send message")
        app._write_chat("- [cyan]Ctrl+Enter[/cyan] send message (when terminal supports it)", "- Ctrl+Enter send message (when terminal supports it)")
        app._write_chat("- [cyan]F6 / F7 / F8[/cyan] copy Chat / Tools / All", "- F6 / F7 / F8 copy Chat / Tools / All")
        app._write_chat("[dim]History: Up/Down in input recalls prior prompts.[/dim]", "History: Up/Down in input recalls prior prompts.")
        app._write_chat("[dim]Composer: Enter newline, use F5/Ctrl+S to send (Ctrl+Enter when supported).[/dim]", "Composer: Enter newline, use F5/Ctrl+S to send (Ctrl+Enter when supported).")

    @staticmethod
    def extract_local_field(text: str, pattern: str) -> tuple[str | None, str]:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            return None, text
        value = (match.group(1) or "").strip()
        updated = f"{text[:match.start()]} {text[match.end():]}".strip()
        updated = re.sub(r"\s+", " ", updated)
        return value or None, updated

    @staticmethod
    def is_local_help_request(text: str) -> bool:
        return text.strip().lower() in {"help", "-h", "--help", "/?"}

    def show_moodboard_usage(self, app: Any) -> None:
        app._write_chat("[bold]Moodboard Flow Usage[/bold]", "Moodboard Flow Usage")
        app._write_chat(
            "[dim]/moodboard <brief text> [count=12] [palette=#87CEEB] [refs=id1,id2] [workflow=image_edit] [novelty=high][/dim]",
            "/moodboard <brief text> [count=12] [palette=#87CEEB] [refs=id1,id2] [workflow=image_edit] [novelty=high]",
        )
        app._write_chat(
            "[dim]Example: /moodboard premium athleisure campaign count=16 palette=#C7A66A[/dim]",
            "Example: /moodboard premium athleisure campaign count=16 palette=#C7A66A",
        )

    def show_aspect_usage(self, app: Any) -> None:
        app._write_chat("[bold]Aspect Flow Usage[/bold]", "Aspect Flow Usage")
        app._write_chat(
            (
                "[dim]/aspect|/ar <image_id> targets=16:9,4:5,3:2,9:16 "
                "[workflow=aspect_ratio_adjustment] [parent=<image_id>] [source=1:1] "
                "[max_delta=0.45] [denoise=1.0] [sweep=3] [preserve=\"...\"] [negative=\"...\"][/dim]"
            ),
            (
                "/aspect|/ar <image_id> targets=16:9,4:5,3:2,9:16 "
                "[workflow=aspect_ratio_adjustment] [parent=<image_id>] [source=1:1] "
                "[max_delta=0.45] [denoise=1.0] [sweep=3] [preserve=\"...\"] [negative=\"...\"]"
            ),
        )
        app._write_chat(
            "[dim]Alias:[/dim] /aspectratio (same as /aspect)",
            "Alias: /aspectratio (same as /aspect)",
        )
        app._write_chat(
            "[dim]Example: /aspectratio 75e92a7e-2838-45a7-6f2c-32a5fde6c300 target=9:16 denoise 1 sweep three different seed values[/dim]",
            "Example: /aspectratio 75e92a7e-2838-45a7-6f2c-32a5fde6c300 target=9:16 denoise 1 sweep three different seed values",
        )

    def show_tanktracks_usage(self, app: Any) -> None:
        app._write_chat("[bold]Tank Tracks Flow Usage[/bold]", "Tank Tracks Flow Usage")
        app._write_chat(
            (
                "[dim]/tanktracks|/tanktrack <image_id> [parent=<image_id>] [workflow=add_tank_tracks] "
                "[runs=4] [sweep=seed|denoise] [seed=123] [denoise=0.7] [post_aspect=4:5][/dim]"
            ),
            (
                "/tanktracks|/tanktrack <image_id> [parent=<image_id>] [workflow=add_tank_tracks] "
                "[runs=4] [sweep=seed|denoise] [seed=123] [denoise=0.7] [post_aspect=4:5]"
            ),
        )
        app._write_chat(
            "[dim]Example: /tanktracks 1cc224eb-022b-4ce9-0dd8-3f274f4f4300[/dim]",
            "Example: /tanktracks 1cc224eb-022b-4ce9-0dd8-3f274f4f4300",
        )
        app._write_chat(
            "[dim]Example: /tanktracks 1cc224eb-022b-4ce9-0dd8-3f274f4f4300 runs=5 sweep=denoise denoise 0.4->0.8 step 0.1[/dim]",
            "Example: /tanktracks 1cc224eb-022b-4ce9-0dd8-3f274f4f4300 runs=5 sweep=denoise denoise 0.4->0.8 step 0.1",
        )

    def show_variation_usage(self, app: Any) -> None:
        app._write_chat("[bold]Image Variation Flow Usage[/bold]", "Image Variation Flow Usage")
        app._write_chat(
            (
                "[dim]/vary|/variation|/variations <image_id> [parent=<image_id>] "
                "[workflow=image_variation_maker] [analysis=\"...\"] [upscale=true|false] "
                "[runs=3] [sweep=seed|prompt|denoise] [denoise=0.4,0.6,0.8][/dim]"
            ),
            (
                "/vary|/variation|/variations <image_id> [parent=<image_id>] "
                "[workflow=image_variation_maker] [analysis=\"...\"] [upscale=true|false] "
                "[runs=3] [sweep=seed|prompt|denoise] [denoise=0.4,0.6,0.8]"
            ),
        )
        app._write_chat(
            "[dim]Example: /vary 75e92a7e-2838-45a7-6f2c-32a5fde6c300 analysis=\"Describe materials and lighting\"[/dim]",
            "Example: /vary 75e92a7e-2838-45a7-6f2c-32a5fde6c300 analysis=\"Describe materials and lighting\"",
        )
        app._write_chat(
            "[dim]Example: /vary 75e92a7e-2838-45a7-6f2c-32a5fde6c300 run it 3 times varying the prompt[/dim]",
            "Example: /vary 75e92a7e-2838-45a7-6f2c-32a5fde6c300 run it 3 times varying the prompt",
        )
        app._write_chat(
            "[dim]Example: /vary 75e92a7e-2838-45a7-6f2c-32a5fde6c300 denoise 0.4->0.8 step 0.1[/dim]",
            "Example: /vary 75e92a7e-2838-45a7-6f2c-32a5fde6c300 denoise 0.4->0.8 step 0.1",
        )

    def show_imageedit_usage(self, app: Any) -> None:
        app._write_chat("[bold]Image Edit Flow Usage[/bold]", "Image Edit Flow Usage")
        app._write_chat(
            (
                "[dim]/imageedit|/imgedit <image_id> <natural language edit request> "
                "[workflow=image_edit] [parent=<image_id>] [analysis=\"...\"] "
                "[runs=4] [sweep=seed] [seed=123] [post_aspect=4:5][/dim]"
            ),
            (
                "/imageedit|/imgedit <image_id> <natural language edit request> "
                "[workflow=image_edit] [parent=<image_id>] [analysis=\"...\"] "
                "[runs=4] [sweep=seed] [seed=123] [post_aspect=4:5]"
            ),
        )
        app._write_chat(
            "[dim]Example: /imageedit 75e9... make it look like polished brass with softer studio lighting[/dim]",
            "Example: /imageedit 75e9... make it look like polished brass with softer studio lighting",
        )

    def echo_local_command(self, app: Any, user_text: str) -> None:
        raw = user_text.strip()
        app._write_chat(f"[dim]Command: {raw}[/dim]", f"Command: {raw}")

    def show_import_workflow_usage(self, app: Any) -> None:
        app._write_chat("[bold]Import Workflow Flow Usage[/bold]", "Import Workflow Flow Usage")
        app._write_chat(
            (
                "[dim]/importwf|/importworkflow <image_id> [id=workflow_id] [name=\"...\"] "
                "[tags=tag1,tag2] [desc=\"...\"] [url=http://127.0.0.1:8787][/dim]"
            ),
            (
                "/importwf|/importworkflow <image_id> [id=workflow_id] [name=\"...\"] "
                "[tags=tag1,tag2] [desc=\"...\"] [url=http://127.0.0.1:8787]"
            ),
        )
        app._write_chat(
            "[dim]Example: /importwf b287f5ef-2901-4e27-f6b4-b483fc4a7e00 id=tank_tracks_v1 tags=photarium,imported desc=\"Tank tracks edit workflow\"[/dim]",
            "Example: /importwf b287f5ef-2901-4e27-f6b4-b483fc4a7e00 id=tank_tracks_v1 tags=photarium,imported desc=\"Tank tracks edit workflow\"",
        )

    @staticmethod
    def normalize_ratio_token(value: str) -> str | None:
        match = re.search(r"(\d+)\s*[:xX]\s*(\d+)", value)
        if not match:
            return None
        width = int(match.group(1))
        height = int(match.group(2))
        if width <= 0 or height <= 0:
            return None
        return f"{width}:{height}"

    def normalize_ratio_list(self, value: str | None) -> list[str]:
        if not value:
            return []
        normalized: list[str] = []
        for chunk in value.split(","):
            token = self.normalize_ratio_token(chunk.strip())
            if token and token not in normalized:
                normalized.append(token)
        return normalized

    @staticmethod
    def find_uuid_token(text: str) -> str | None:
        match = re.search(
            r"\b[0-9a-fA-F]{8}-"
            r"[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{12}\b",
            text,
        )
        if not match:
            return None
        return match.group(0)

    @staticmethod
    def strip_variation_source_prefix(text: str) -> str:
        if not text:
            return text
        normalized = re.sub(
            r"^\s*(?:image\s+id|image_id|image|id)\b\s*(?:[:=]\s*)?",
            "",
            text,
            flags=re.IGNORECASE,
        )
        return normalized.strip()

    def run_moodboard_flow(self, app: Any, user_text: str) -> None:
        raw = user_text.strip()
        parts = raw.split(maxsplit=1)
        remainder = parts[1].strip() if len(parts) > 1 else ""
        if self.is_local_help_request(remainder):
            self.show_moodboard_usage(app)
            return
        self.echo_local_command(app, user_text)
        count_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:count|n)\s*=\s*(\d{1,2})(?=\s|$)")
        palette_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:palette|color)\s*=\s*(#[0-9a-fA-F]{6})(?=\s|$)")
        refs_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:refs|images)\s*=\s*([A-Za-z0-9,_-]+)(?=\s|$)")
        workflow_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)workflow\s*=\s*([A-Za-z0-9._-]+)(?=\s|$)")
        novelty_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)novelty\s*=\s*(low|medium|high)(?=\s|$)")
        brief = remainder.strip()
        refs = [item.strip() for item in (refs_value or "").split(",") if item.strip()]
        count = 12
        if count_value is not None:
            try:
                count = max(3, min(40, int(count_value)))
            except ValueError:
                count = 12
        if not brief and not refs:
            self.show_moodboard_usage(app)
            return
        flow_prompt = app._build_moodboard_flow_prompt(
            brief=brief,
            count=count,
            palette=palette_value,
            refs=refs,
            workflow_id=workflow_value,
            novelty=novelty_value or "high",
        )
        summary = brief or "reference-led mood board"
        app._write_chat(
            f"[bold cyan]Moodboard Flow:[/bold cyan] {summary} (targets {count} variants)",
            f"Moodboard Flow: {summary} (targets {count} variants)",
        )
        if not app._is_ready:
            app._write_chat("[yellow]Still connecting. Please wait.[/yellow]", "Still connecting. Please wait.")
            return
        app._enqueue_request(flow_prompt)

    def run_aspect_flow(self, app: Any, user_text: str) -> None:
        raw = user_text.strip()
        parts = raw.split(maxsplit=1)
        remainder = parts[1].strip() if len(parts) > 1 else ""
        if self.is_local_help_request(remainder):
            self.show_aspect_usage(app)
            return
        self.echo_local_command(app, user_text)
        patterns = [
            r'(?:^|\s)(?:targets|target|ratios|ratio)\s*=\s*"([0-9xX:,\s_-]+)"?(?=\s|$)',
            r"(?:^|\s)(?:targets|target|ratios|ratio)\s*=\s*'([0-9xX:,\s_-]+)'?(?=\s|$)",
            r"(?:^|\s)(?:targets|target|ratios|ratio)\s*=\s*([0-9xX:,_-]+)(?=\s|$)",
            r'(?:^|\s)(?:targets|target|ratios|ratio)\s+"([0-9xX:,\s_-]+)"?(?=\s|$)',
            r"(?:^|\s)(?:targets|target|ratios|ratio)\s+'([0-9xX:,\s_-]+)'?(?=\s|$)",
            r"(?:^|\s)(?:targets|target|ratios|ratio)\s+([0-9xX:,_-]+)(?=\s|$)",
        ]
        targets_value = None
        for pattern in patterns:
            if targets_value is None:
                targets_value, remainder = self.extract_local_field(remainder, pattern)
        workflow_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)workflow\s*=\s*([A-Za-z0-9._-]+)(?=\s|$)")
        parent_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:parent|variant_of|upload_to)\s*=\s*([A-Za-z0-9-]+)(?=\s|$)")
        source_ratio_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:source|source_ratio)\s*=\s*([0-9xX:]+)(?=\s|$)")
        max_delta_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:max_delta|max_step)\s*=\s*([0-9]*\.?[0-9]+)(?=\s|$)")
        denoise_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)denoise\s*=\s*([0-9]*\.?[0-9]+)(?=\s|$)")
        if denoise_value is None:
            denoise_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)denoise\s+([0-9]*\.?[0-9]+)(?=\s|$)")
        seed_values_text, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:seeds?|seed_values?)\s*=\s*([0-9,\s]+)(?=\s|$)")
        sweep_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)sweep\s*=\s*(\d{1,2})(?=\s|$)")
        if sweep_value is None:
            sweep_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)sweep\s+(\d{1,2})(?=\s|$)")
        if sweep_value is None:
            sweep_word, remainder = self.extract_local_field(remainder, r"(?:^|\s)sweep\s+([A-Za-z]+)\b")
            if sweep_word:
                sweep_value = str(app._NUMBER_WORDS.get(sweep_word.lower(), ""))
        preserve_value, remainder = self.extract_local_field(remainder, r'(?:^|\s)(?:preserve|positive|guidance)\s*=\s*"([^"]+)"(?=\s|$)')
        if preserve_value is None:
            preserve_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:preserve|positive|guidance)\s*=\s*'([^']+)'(?=\s|$)")
        negative_value, remainder = self.extract_local_field(remainder, r'(?:^|\s)(?:negative|avoid)\s*=\s*"([^"]+)"(?=\s|$)')
        if negative_value is None:
            negative_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:negative|avoid)\s*=\s*'([^']+)'(?=\s|$)")
        remainder = self.strip_variation_source_prefix(remainder)
        inferred_source = self.find_uuid_token(remainder) if remainder else None
        if inferred_source:
            source_id = inferred_source
            extra_text = re.sub(rf"\b{re.escape(inferred_source)}\b", " ", remainder, count=1)
            extra_text = re.sub(r"\s+", " ", extra_text).strip()
        else:
            source_id = remainder.split(maxsplit=1)[0] if remainder else ""
            source_id = source_id.strip()
            extra_text = remainder[len(source_id) :].strip() if remainder and source_id else ""
        if not targets_value and extra_text:
            targets_value = app._infer_aspect_targets_from_text(extra_text)
        target_ratios = self.normalize_ratio_list(targets_value)
        source_ratio_hint = self.normalize_ratio_token(source_ratio_value or "")
        seed_values = app._parse_seed_values(seed_values_text)
        seed_sweep_count = len(seed_values) if seed_values else 0
        if not seed_values and sweep_value:
            try:
                seed_sweep_count = max(0, min(24, int(sweep_value)))
            except ValueError:
                seed_sweep_count = 0
        if seed_sweep_count > 0:
            if seed_values_text:
                app._write_chat(
                    "[yellow]Seed sweep policy: ignoring fixed seed lists and generating fresh random seeds for this run.[/yellow]",
                    "Seed sweep policy: ignoring fixed seed lists and generating fresh random seeds for this run.",
                )
            seed_values = app._generate_random_seed_values(seed_sweep_count)
        denoise_override = None
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
            self.show_aspect_usage(app)
            return
        workflow_id = workflow_value or "aspect_ratio_adjustment"
        variant_of = parent_value or source_id
        flow_prompt = app._build_aspect_flow_prompt(
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
        app._write_chat(
            f"[bold cyan]Aspect Flow:[/bold cyan] source={source_id} targets={', '.join(target_ratios)}",
            f"Aspect Flow: source={source_id} targets={', '.join(target_ratios)}",
        )
        if not app._is_ready:
            app._write_chat("[yellow]Still connecting. Please wait.[/yellow]", "Still connecting. Please wait.")
            return
        app._enqueue_request(flow_prompt)

    def run_tanktracks_flow(self, app: Any, user_text: str) -> None:
        raw = user_text.strip()
        parts = raw.split(maxsplit=1)
        remainder = parts[1].strip() if len(parts) > 1 else ""
        if self.is_local_help_request(remainder):
            self.show_tanktracks_usage(app)
            return
        self.echo_local_command(app, user_text)
        variant_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:parent|variant_of|upload_to)\s*=\s*([A-Za-z0-9-]+)(?=\s|$)")
        workflow_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)workflow\s*=\s*([A-Za-z0-9._-]+)(?=\s|$)")
        runs_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:runs?|count)\s*=\s*(\d{1,2})(?=\s|$)")
        if runs_value is None:
            runs_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:runs?|count)\s+(\d{1,2})(?=\s|$)")
        sweep_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:sweep|vary|variation)\s*=\s*([A-Za-z0-9._-]+)(?=\s|$)")
        if sweep_value is None:
            sweep_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:sweep|vary|variation)\s+([A-Za-z0-9._-]+)(?=\s|$)")
        post_aspect_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:post_aspect|post_ar|output_aspect|aspect)\s*=\s*([0-9xX:]+)(?=\s|$)")
        seed_start_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:seed_start|start_seed|seed)\s*=\s*(\d{1,20})(?=\s|$)")
        denoise_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)denoise\s*=\s*([0-9]*\.?[0-9]+)(?=\s|$)")
        if denoise_value is None:
            denoise_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)denoise\s+([0-9]*\.?[0-9]+)(?=\s|$)")
        prompt_value, remainder = self.extract_local_field(remainder, r'(?:^|\s)(?:prompt|instruction)\s*=\s*"([^"]+)"(?=\s|$)')
        if prompt_value is None:
            prompt_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:prompt|instruction)\s*=\s*'([^']+)'(?=\s|$)")
        if prompt_value is not None:
            app._write_chat(
                "[yellow]/tanktracks uses a fixed workflow prompt. Remove prompt=... and retry.[/yellow]",
                "/tanktracks uses a fixed workflow prompt. Remove prompt=... and retry.",
            )
            return
        source_id = remainder.split(maxsplit=1)[0] if remainder else ""
        source_id = source_id.strip()
        if not source_id:
            self.show_tanktracks_usage(app)
            return
        variant_of = variant_value or source_id
        workflow_id = workflow_value or "add_tank_tracks"
        extra_text = remainder[len(source_id) :].strip() if remainder else ""
        run_count = None
        if runs_value is not None:
            try:
                run_count = max(1, min(24, int(runs_value)))
            except ValueError:
                run_count = None
        if run_count is None and extra_text:
            run_count = app._parse_run_count_from_text(extra_text)
        if run_count is None:
            run_count = 1
        sweep_target = app._normalize_sweep_target(sweep_value)
        if sweep_target is None and extra_text:
            sweep_target = app._infer_variation_sweep(extra_text)
        if sweep_target is None and run_count > 1:
            sweep_target = "seed"
        sweep_param, sweep_values = app._parse_sweep_values(extra_text)
        if sweep_param and sweep_target is None:
            sweep_target = app._normalize_sweep_target(sweep_param)
        if sweep_values and run_count == 1:
            run_count = len(sweep_values)
        post_aspect_ratio = self.normalize_ratio_token(post_aspect_value or "")
        if post_aspect_ratio is None:
            inferred_post_aspect = app._infer_aspect_targets_from_text(extra_text)
            post_aspect_ratio = self.normalize_ratio_token(inferred_post_aspect or "")
        seed_start = None
        if seed_start_value is not None:
            try:
                seed_start = int(seed_start_value)
            except ValueError:
                seed_start = None
        denoise_override = None
        if denoise_value is not None:
            try:
                denoise_override = max(0.0, min(1.0, float(denoise_value)))
            except ValueError:
                denoise_override = None
        denoise_sweep_values: list[float] = []
        if sweep_target == "denoise":
            if sweep_param == "denoise" and sweep_values:
                denoise_sweep_values = sweep_values[:24]
            elif denoise_override is not None:
                denoise_sweep_values = [denoise_override]
            if denoise_sweep_values:
                run_count = len(denoise_sweep_values)
            elif run_count > 1:
                app._write_chat(
                    "[yellow]Denoise sweep requested but no denoise values were found. Running a single pass with workflow default denoise.[/yellow]",
                    "Denoise sweep requested but no denoise values were found. Running a single pass with workflow default denoise.",
                )
                run_count = 1
                sweep_target = None
        if not app._is_ready:
            app._write_chat("[yellow]Still connecting. Please wait.[/yellow]", "Still connecting. Please wait.")
            return
        if run_count > 1 and sweep_target == "seed":
            if seed_start_value is not None:
                app._write_chat(
                    "[yellow]Seed sweep policy: ignoring seed_start/seed for sweeps and generating fresh random seeds.[/yellow]",
                    "Seed sweep policy: ignoring seed_start/seed for sweeps and generating fresh random seeds.",
                )
            random_seeds = app._generate_random_seed_values(run_count)
            app._write_chat(
                (
                    f"[bold cyan]Tank Tracks Flow:[/bold cyan] source={source_id} variant_of={variant_of} "
                    f"runs={run_count} sweep=seed"
                    + (f" post_aspect={post_aspect_ratio}" if post_aspect_ratio else "")
                ),
                (
                    f"Tank Tracks Flow: source={source_id} variant_of={variant_of} runs={run_count} sweep=seed"
                    + (f" post_aspect={post_aspect_ratio}" if post_aspect_ratio else "")
                ),
            )
            for run_index, seed_value in enumerate(random_seeds):
                flow_prompt = app._build_tanktracks_flow_prompt(
                    source_id=source_id,
                    variant_of=variant_of,
                    workflow_id=workflow_id,
                    seed_override=seed_value,
                    denoise_override=denoise_override,
                    post_aspect_ratio=post_aspect_ratio,
                    run_index=run_index + 1,
                    run_count=run_count,
                )
                app._enqueue_request(flow_prompt)
            return
        if run_count > 1 and sweep_target == "denoise":
            app._write_chat(
                (
                    f"[bold cyan]Tank Tracks Flow:[/bold cyan] source={source_id} variant_of={variant_of} "
                    f"runs={run_count} sweep=denoise"
                    + (f" post_aspect={post_aspect_ratio}" if post_aspect_ratio else "")
                ),
                (
                    f"Tank Tracks Flow: source={source_id} variant_of={variant_of} runs={run_count} sweep=denoise"
                    + (f" post_aspect={post_aspect_ratio}" if post_aspect_ratio else "")
                ),
            )
            for run_index, denoise_per_run in enumerate(denoise_sweep_values):
                flow_prompt = app._build_tanktracks_flow_prompt(
                    source_id=source_id,
                    variant_of=variant_of,
                    workflow_id=workflow_id,
                    seed_override=seed_start,
                    denoise_override=denoise_per_run,
                    post_aspect_ratio=post_aspect_ratio,
                    run_index=run_index + 1,
                    run_count=run_count,
                )
                app._enqueue_request(flow_prompt)
            return
        flow_prompt = app._build_tanktracks_flow_prompt(
            source_id=source_id,
            variant_of=variant_of,
            workflow_id=workflow_id,
            seed_override=seed_start,
            denoise_override=denoise_override,
            post_aspect_ratio=post_aspect_ratio,
            run_index=None,
            run_count=None,
        )
        app._write_chat(
            (
                f"[bold cyan]Tank Tracks Flow:[/bold cyan] source={source_id} variant_of={variant_of}"
                + (f" post_aspect={post_aspect_ratio}" if post_aspect_ratio else "")
            ),
            (
                f"Tank Tracks Flow: source={source_id} variant_of={variant_of}"
                + (f" post_aspect={post_aspect_ratio}" if post_aspect_ratio else "")
            ),
        )
        app._enqueue_request(flow_prompt)

    def run_variation_flow(self, app: Any, user_text: str) -> None:
        raw = user_text.strip()
        parts = raw.split(maxsplit=1)
        remainder = parts[1].strip() if len(parts) > 1 else ""
        if self.is_local_help_request(remainder):
            self.show_variation_usage(app)
            return
        self.echo_local_command(app, user_text)
        variant_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:parent|variant_of|upload_to)\s*=\s*([A-Za-z0-9-]+)(?=\s|$)")
        workflow_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)workflow\s*=\s*([A-Za-z0-9._-]+)(?=\s|$)")
        analysis_value, remainder = self.extract_local_field(
            remainder,
            r'(?:^|\s)(?:analysis|analysis_prompt|analysis_instructions|image_analysis_prompt|image_analysis_instructions|prompt|instruction)\s*=\s*"([^"]+)"(?=\s|$)',
        )
        if analysis_value is None:
            analysis_value, remainder = self.extract_local_field(
                remainder,
                r"(?:^|\s)(?:analysis|analysis_prompt|analysis_instructions|image_analysis_prompt|image_analysis_instructions|prompt|instruction)\s*=\s*'([^']+)'(?=\s|$)",
            )
        upscale_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)upscale\s*=\s*(true|false|1|0|yes|no)(?=\s|$)")
        if upscale_value is None:
            upscale_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)upscale\s+(true|false|1|0|yes|no)(?=\s|$)")
        run_count_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:runs?|run_count|count)\s*=\s*(\d{1,2})(?=\s|$)")
        if run_count_value is None:
            run_count_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:runs?|run_count|count)\s+(\d{1,2})(?=\s|$)")
        sweep_target_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:sweep|vary|variation)\s*=\s*([A-Za-z0-9._-]+)(?=\s|$)")
        if sweep_target_value is None:
            sweep_target_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:sweep|vary|variation)\s+([A-Za-z0-9._-]+)(?=\s|$)")
        remainder = self.strip_variation_source_prefix(remainder)
        source_id = remainder.split(maxsplit=1)[0] if remainder else ""
        source_id = source_id.strip()
        if not source_id:
            self.show_variation_usage(app)
            return
        extra_text = remainder[len(source_id) :].strip() if remainder else ""
        upscale_flag = None
        if upscale_value is not None:
            normalized = upscale_value.strip().lower()
            if normalized in {"1", "true", "yes", "y", "on"}:
                upscale_flag = True
            elif normalized in {"0", "false", "no", "n", "off"}:
                upscale_flag = False
        run_count = None
        if run_count_value is not None:
            try:
                run_count = max(1, min(24, int(run_count_value)))
            except ValueError:
                run_count = None
        if run_count is None and extra_text:
            run_count = app._parse_run_count_from_text(extra_text)
        if sweep_target_value and str(sweep_target_value).strip().isdigit():
            if run_count is None:
                try:
                    run_count = max(1, min(24, int(str(sweep_target_value).strip())))
                except ValueError:
                    run_count = None
            sweep_target_value = None
        sweep_target = app._normalize_sweep_target(sweep_target_value)
        if sweep_target is None and extra_text:
            sweep_target = app._infer_variation_sweep(extra_text)
        sweep_param, sweep_values = app._parse_sweep_values(extra_text)
        if sweep_param and sweep_target is None:
            sweep_target = app._normalize_sweep_target(sweep_param)
        if sweep_values:
            sweep_values = sweep_values[:24]
            if run_count is None:
                run_count = len(sweep_values)
        if run_count and run_count > 1 and sweep_target is None:
            sweep_target = "seed"
        random_seed_values: list[int] = []
        if run_count and run_count > 1 and sweep_target == "seed":
            random_seed_values = app._generate_random_seed_values(run_count)
        variant_of = variant_value or source_id
        workflow_id = workflow_value or "image_variation_maker"
        flow_prompt = app._build_variation_flow_prompt(
            source_id=source_id,
            variant_of=variant_of,
            workflow_id=workflow_id,
            analysis_prompt=analysis_value,
            upscale=upscale_flag,
            run_count=run_count,
            sweep_target=sweep_target,
            sweep_values=sweep_values,
            seed_values=random_seed_values,
            extra_instructions=extra_text,
        )
        app._write_chat(
            f"[bold cyan]Variation Flow:[/bold cyan] source={source_id} variant_of={variant_of}",
            f"Variation Flow: source={source_id} variant_of={variant_of}",
        )
        if not app._is_ready:
            app._write_chat("[yellow]Still connecting. Please wait.[/yellow]", "Still connecting. Please wait.")
            return
        app._enqueue_request(flow_prompt)

    def run_import_workflow_flow(self, app: Any, user_text: str) -> None:
        raw = user_text.strip()
        parts = raw.split(maxsplit=1)
        remainder = parts[1].strip() if len(parts) > 1 else ""
        if self.is_local_help_request(remainder):
            self.show_import_workflow_usage(app)
            return
        self.echo_local_command(app, user_text)
        workflow_id_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:id|workflow|workflow_id)\s*=\s*([A-Za-z0-9_-]+)(?=\s|$)")
        tags_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)tags\s*=\s*([A-Za-z0-9,_-]+)(?=\s|$)")
        mcp_url_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:url|mcp|photarium_url)\s*=\s*(https?://[^\s]+)(?=\s|$)")
        name_value, remainder = self.extract_local_field(remainder, r'(?:^|\s)name\s*=\s*"([^"]+)"(?=\s|$)')
        if name_value is None:
            name_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)name\s*=\s*'([^']+)'(?=\s|$)")
        desc_value, remainder = self.extract_local_field(remainder, r'(?:^|\s)(?:desc|description)\s*=\s*"([^"]+)"(?=\s|$)')
        if desc_value is None:
            desc_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:desc|description)\s*=\s*'([^']+)'(?=\s|$)")
        image_id = remainder.split(maxsplit=1)[0] if remainder else ""
        image_id = image_id.strip()
        if not image_id:
            self.show_import_workflow_usage(app)
            return
        workflow_id = workflow_id_value or f"photarium_{image_id.split('-')[0]}_workflow"
        workflow_name = name_value or workflow_id
        tags = [item.strip() for item in (tags_value or "photarium,imported").split(",") if item.strip()]
        mcp_url = mcp_url_value or "http://127.0.0.1:8787"
        flow_prompt = app._build_import_workflow_flow_prompt(
            image_id=image_id,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            tags=tags,
            description=desc_value,
            photarium_mcp_url=mcp_url,
        )
        app._write_chat(
            f"[bold cyan]Import Workflow Flow:[/bold cyan] image={image_id} id={workflow_id}",
            f"Import Workflow Flow: image={image_id} id={workflow_id}",
        )
        if not app._is_ready:
            app._write_chat("[yellow]Still connecting. Please wait.[/yellow]", "Still connecting. Please wait.")
            return
        app._enqueue_request(flow_prompt)

    def run_imageedit_flow(self, app: Any, user_text: str) -> None:
        raw = user_text.strip()
        parts = raw.split(maxsplit=1)
        remainder = parts[1].strip() if len(parts) > 1 else ""
        if self.is_local_help_request(remainder):
            self.show_imageedit_usage(app)
            return
        self.echo_local_command(app, user_text)
        variant_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:parent|variant_of|upload_to)\s*=\s*([A-Za-z0-9-]+)(?=\s|$)")
        workflow_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)workflow\s*=\s*([A-Za-z0-9._-]+)(?=\s|$)")
        runs_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:runs?|count)\s*=\s*(\d{1,2})(?=\s|$)")
        if runs_value is None:
            runs_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:runs?|count)\s+(\d{1,2})(?=\s|$)")
        sweep_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:sweep|vary|variation)\s*=\s*([A-Za-z0-9._-]+)(?=\s|$)")
        post_aspect_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:post_aspect|post_ar|output_aspect|aspect)\s*=\s*([0-9xX:]+)(?=\s|$)")
        seed_start_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:seed_start|start_seed|seed)\s*=\s*(\d{1,20})(?=\s|$)")
        analysis_value, remainder = self.extract_local_field(remainder, r'(?:^|\s)(?:analysis|analysis_prompt)\s*=\s*"([^"]+)"(?=\s|$)')
        if analysis_value is None:
            analysis_value, remainder = self.extract_local_field(remainder, r"(?:^|\s)(?:analysis|analysis_prompt)\s*=\s*'([^']+)'(?=\s|$)")
        remainder = self.strip_variation_source_prefix(remainder)
        source_id = remainder.split(maxsplit=1)[0] if remainder else ""
        source_id = source_id.strip()
        if not source_id:
            self.show_imageedit_usage(app)
            return
        edit_request = remainder[len(source_id) :].strip() if remainder else ""
        if not edit_request:
            self.show_imageedit_usage(app)
            return
        variant_of = variant_value or source_id
        workflow_id = workflow_value or "image_edit"
        run_count = None
        if runs_value is not None:
            try:
                run_count = max(1, min(24, int(runs_value)))
            except ValueError:
                run_count = None
        if run_count is None and edit_request:
            run_count = app._parse_run_count_from_text(edit_request)
        if run_count is None:
            run_count = 1
        sweep_target = app._normalize_sweep_target(sweep_value)
        if sweep_target is None and edit_request:
            sweep_target = app._infer_variation_sweep(edit_request)
        if sweep_target is None and run_count > 1:
            sweep_target = "seed"
        post_aspect_ratio = self.normalize_ratio_token(post_aspect_value or "")
        seed_start = None
        if seed_start_value is not None:
            try:
                seed_start = int(seed_start_value)
            except ValueError:
                seed_start = None
        if not app._is_ready:
            app._write_chat("[yellow]Still connecting. Please wait.[/yellow]", "Still connecting. Please wait.")
            return
        if run_count > 1 and sweep_target == "seed":
            if seed_start_value is not None:
                app._write_chat(
                    "[yellow]Seed sweep policy: ignoring seed_start/seed for sweeps and generating fresh random seeds.[/yellow]",
                    "Seed sweep policy: ignoring seed_start/seed for sweeps and generating fresh random seeds.",
                )
            random_seeds = app._generate_random_seed_values(run_count)
            app._write_chat(
                (
                    f"[bold cyan]Image Edit Flow:[/bold cyan] source={source_id} workflow={workflow_id} "
                    f"runs={run_count} sweep=seed"
                    + (f" post_aspect={post_aspect_ratio}" if post_aspect_ratio else "")
                ),
                (
                    f"Image Edit Flow: source={source_id} workflow={workflow_id} runs={run_count} sweep=seed"
                    + (f" post_aspect={post_aspect_ratio}" if post_aspect_ratio else "")
                ),
            )
            for run_index, seed_value in enumerate(random_seeds):
                flow_prompt = app._build_imageedit_flow_prompt(
                    source_id=source_id,
                    variant_of=variant_of,
                    workflow_id=workflow_id,
                    edit_request=edit_request,
                    analysis_prompt=analysis_value,
                    seed_override=seed_value,
                    post_aspect_ratio=post_aspect_ratio,
                    run_index=run_index + 1,
                    run_count=run_count,
                )
                app._enqueue_request(flow_prompt)
            return
        flow_prompt = app._build_imageedit_flow_prompt(
            source_id=source_id,
            variant_of=variant_of,
            workflow_id=workflow_id,
            edit_request=edit_request,
            analysis_prompt=analysis_value,
            seed_override=seed_start,
            post_aspect_ratio=post_aspect_ratio,
            run_index=None,
            run_count=None,
        )
        app._write_chat(
            (
                f"[bold cyan]Image Edit Flow:[/bold cyan] source={source_id} workflow={workflow_id}"
                + (f" post_aspect={post_aspect_ratio}" if post_aspect_ratio else "")
            ),
            (
                f"Image Edit Flow: source={source_id} workflow={workflow_id}"
                + (f" post_aspect={post_aspect_ratio}" if post_aspect_ratio else "")
            ),
        )
        app._enqueue_request(flow_prompt)
