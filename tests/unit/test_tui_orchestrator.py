from __future__ import annotations

from dataclasses import dataclass

import pytest

from comfy_mcp.tui_client.orchestrator import ChatOrchestrator
from comfy_mcp.tui_client.llm_client import LLMResponse, ToolCall


@dataclass
class FakeLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCall(call_id="c1", name="workflows.list", arguments={"limit": 1})],
            )
        return LLMResponse(content="Done", tool_calls=[])


@dataclass
class FakeRouter:
    async def call_tool(self, name, arguments):
        return {"name": name, "args": arguments}


@pytest.mark.asyncio
async def test_orchestrator_tool_then_answer():
    llm = FakeLLM()
    router = FakeRouter()
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools([])

    answer, events = await orch.process("list workflows")
    assert answer == "Done"
    assert len(events) == 1
    assert events[0].name == "workflows.list"
    assert events[0].result == {"name": "workflows.list", "args": {"limit": 1}}


@pytest.mark.asyncio
async def test_orchestrator_progress_events():
    llm = FakeLLM()
    router = FakeRouter()
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools([])
    progress = []

    def on_progress(kind, payload):
        progress.append(kind)

    answer, _events = await orch.process("list workflows", on_progress=on_progress)
    assert answer == "Done"
    assert "llm_request" in progress
    assert "tool_call_start" in progress
    assert "tool_call_result" in progress
    assert "complete" in progress
