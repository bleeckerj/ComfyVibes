"""Reusable Textual widgets for the TUI app."""

from __future__ import annotations

from typing import Any, Callable

from textual import events
from textual.widgets import RichLog, TextArea


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
        if callable(logger) and bool(getattr(self.app, "_key_debug_enabled", False)):
            logger(
                "input_key",
                key=event.key,
                name=event.name,
                character=event.character,
                is_printable=bool(getattr(event, "is_printable", False)),
                is_repeat=getattr(event, "is_repeat", None),
            )
        await super()._on_key(event)


class PassiveRichLog(RichLog):
    """Log pane that won't steal keyboard focus from the input composer."""

    can_focus = False
