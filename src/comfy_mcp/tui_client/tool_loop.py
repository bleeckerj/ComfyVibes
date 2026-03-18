"""Wrapper for the orchestrator tool loop."""

from __future__ import annotations

from typing import Any, Callable, Dict


class ToolLoop:
    def __init__(self, orchestrator: Any) -> None:
        self._orchestrator = orchestrator

    async def process(
        self,
        user_text: str,
        on_progress: Callable[[str, Dict[str, Any]], None] | None = None,
        stop_after_tool_calls: bool = False,
    ) -> tuple[str | None, list[Any]]:
        return await self._orchestrator._process_loop(
            user_text=user_text,
            on_progress=on_progress,
            stop_after_tool_calls=stop_after_tool_calls,
        )
