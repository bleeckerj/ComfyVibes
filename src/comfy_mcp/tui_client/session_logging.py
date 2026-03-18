"""Session logging and UI preference persistence helpers."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any


class SessionLogManager:
    def init_session_log(self, app: Any) -> None:
        if app._session_log_path is not None:
            return
        logs_dir = Path.cwd() / ".mcp_chat_logs"
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        session_log_path = logs_dir / f"chat_{timestamp}.log"
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
            header = "\n".join(
                [
                    f"Session started: {datetime.now().isoformat(timespec='seconds')}",
                    f"Model: {app._config.llm.model}",
                    "===",
                    "",
                ]
            )
            session_log_path.write_text(header, encoding="utf-8")
            app._session_log_path = session_log_path
        except Exception:
            app._session_log_path = None

    def append_session_log(self, app: Any, channel: str, text: str) -> None:
        if app._session_log_path is None:
            return
        timestamp = datetime.now().isoformat(timespec="seconds")
        clipped = app._clip_text_for_session_log(text)
        normalized = clipped.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
        line = f"[{timestamp}] {channel}: {normalized}\n"
        app._session_log_buffer.append(line)
        if len(app._session_log_buffer) >= 64:
            self.flush_session_log_buffer(app)
            return
        if app._session_log_flush_handle is not None:
            return
        try:
            app._session_log_flush_handle = asyncio.get_running_loop().call_later(
                app._SESSION_LOG_FLUSH_DELAY_S,
                app._flush_session_log_buffer,
            )
        except Exception:
            self.flush_session_log_buffer(app)

    def flush_session_log_buffer(self, app: Any) -> None:
        if app._session_log_flush_handle is not None:
            try:
                app._session_log_flush_handle.cancel()
            except Exception:
                pass
            app._session_log_flush_handle = None
        if app._session_log_path is None or not app._session_log_buffer:
            return
        payload = "".join(app._session_log_buffer)
        app._session_log_buffer.clear()
        try:
            with app._session_log_path.open("a", encoding="utf-8") as handle:
                handle.write(payload)
        except Exception:
            return

    def load_ui_preferences(self, app: Any) -> None:
        try:
            payload = json.loads(app._ui_prefs_path.read_text(encoding="utf-8"))
        except Exception:
            return
        raw_height = payload.get("input_height_lines")
        if isinstance(raw_height, (int, float)):
            clamped = max(app._MIN_INPUT_HEIGHT, min(app._MAX_INPUT_HEIGHT, int(raw_height)))
            app._input_height_lines = clamped
        raw_prompt_history = payload.get("prompt_history")
        if isinstance(raw_prompt_history, list):
            normalized = []
            for item in raw_prompt_history:
                if not isinstance(item, str):
                    continue
                text = item.strip()
                if not text:
                    continue
                normalized.append(text)
            if normalized:
                app._prompt_history = normalized[-app._PROMPT_HISTORY_MAX :]
        raw_response_verbosity = payload.get("response_verbosity")
        if isinstance(raw_response_verbosity, str):
            normalized_mode = raw_response_verbosity.strip().lower()
            if normalized_mode in {"loud", "lowkey", "quiet"}:
                app._response_verbosity = normalized_mode
        raw_server_enabled = payload.get("server_enabled")
        if isinstance(raw_server_enabled, dict) and hasattr(app, "_server_enabled"):
            for key, value in raw_server_enabled.items():
                if not isinstance(key, str):
                    continue
                if key not in app._server_enabled:
                    continue
                app._server_enabled[key] = bool(value)

    def save_ui_preferences(self, app: Any) -> None:
        payload = {
            "input_height_lines": app._input_height_lines,
            "prompt_history": app._prompt_history[-app._PROMPT_HISTORY_MAX :],
            "response_verbosity": app._response_verbosity,
            "server_enabled": getattr(app, "_server_enabled", {}),
        }
        try:
            app._ui_prefs_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        except Exception:
            return
