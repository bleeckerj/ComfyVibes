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

    async def chat_stream(self, messages, tools, on_token=None):
        """Streaming version — delegates to chat() for test simplicity."""
        return await self.chat(messages, tools)


@dataclass
class FakeRouter:
    async def call_tool(self, name, arguments):
        return {"name": name, "args": arguments}


@dataclass
class EchoLLM:
    async def chat(self, messages, tools):
        return LLMResponse(content="ok", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class ToolThenDoneLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCall(call_id="c1", name="workflows.list", arguments={"limit": 1})],
            )
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class LargeResultRouter:
    async def call_tool(self, name, arguments):
        return {"blob": "x" * 5000}


@dataclass
class BurstToolLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls % 2 == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(call_id=f"round_{self.calls}_a", name="workflows.list", arguments={"limit": 1}),
                    ToolCall(call_id=f"round_{self.calls}_b", name="workflows.list", arguments={"limit": 2}),
                    ToolCall(call_id=f"round_{self.calls}_c", name="workflows.list", arguments={"limit": 3}),
                ],
            )
        return LLMResponse(content="ok", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class RepeatingToolLLM:
    async def chat(self, messages, tools):
        return LLMResponse(
            content="I will keep trying the same thing.",
            tool_calls=[ToolCall(call_id="repeat", name="workflows_search", arguments={"query": "nunchaku"})],
        )

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class ColorSearchLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCall(call_id="c1", name="photarium_search_color", arguments={"color": "sky blue"})],
            )
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class RecordingRouter:
    last_name: str | None = None
    last_arguments: dict | None = None
    result_payload: dict | None = None

    async def call_tool(self, name, arguments):
        self.last_name = name
        self.last_arguments = dict(arguments or {})
        return self.result_payload or {}


@dataclass
class SemanticSearchLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCall(call_id="c1", name="photarium_search", arguments={"query": "jackets"})],
            )
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


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


@pytest.mark.asyncio
async def test_orchestrator_prunes_old_messages():
    llm = EchoLLM()
    router = FakeRouter()
    orch = ChatOrchestrator(
        "system",
        llm,
        router,
        max_non_system_messages=4,
        max_total_content_chars=10_000,
        max_message_content_chars=1000,
        max_tool_content_chars=1000,
    )
    orch.set_tools([])

    for i in range(12):
        answer, _ = await orch.process(f"message-{i}")
        assert answer == "ok"

    assert orch._messages[0]["role"] == "system"
    assert len(orch._messages) <= 5


@pytest.mark.asyncio
async def test_orchestrator_truncates_large_tool_content():
    llm = ToolThenDoneLLM()
    router = LargeResultRouter()
    orch = ChatOrchestrator(
        "system",
        llm,
        router,
        max_non_system_messages=20,
        max_total_content_chars=10_000,
        max_message_content_chars=1000,
        max_tool_content_chars=80,
    )
    orch.set_tools([])

    answer, events = await orch.process("run tool")

    assert answer == "done"
    assert len(events) == 1
    tool_messages = [m for m in orch._messages if m.get("role") == "tool"]
    assert tool_messages
    assert "...[truncated]" in tool_messages[-1]["content"]


@pytest.mark.asyncio
async def test_orchestrator_pruning_preserves_tool_call_sequence_integrity():
    llm = BurstToolLLM()
    router = LargeResultRouter()
    orch = ChatOrchestrator(
        "system",
        llm,
        router,
        max_non_system_messages=18,
        max_total_content_chars=7_000,
        max_message_content_chars=1_000,
        max_tool_content_chars=1_500,
    )
    orch.set_tools([])

    for i in range(12):
        answer, _events = await orch.process(f"message-{i}")
        assert answer == "ok"

    messages = orch._messages
    assert messages[0]["role"] == "system"

    for index, message in enumerate(messages):
        if message.get("role") != "assistant" or not message.get("tool_calls"):
            continue
        expected_ids = [call.get("id") for call in message.get("tool_calls", []) if call.get("id")]
        tool_ids = []
        cursor = index + 1
        while cursor < len(messages) and messages[cursor].get("role") == "tool":
            tool_ids.append(messages[cursor].get("tool_call_id"))
            cursor += 1
        assert all(call_id in tool_ids for call_id in expected_ids)

    for index, message in enumerate(messages):
        if message.get("role") != "tool":
            continue
        cursor = index - 1
        while cursor >= 0 and messages[cursor].get("role") == "tool":
            cursor -= 1
        assert cursor >= 0
        assistant = messages[cursor]
        assert assistant.get("role") == "assistant"
        tool_call_ids = [call.get("id") for call in assistant.get("tool_calls", []) if call.get("id")]
        assert message.get("tool_call_id") in tool_call_ids


@pytest.mark.asyncio
async def test_orchestrator_stops_repeated_identical_tool_rounds():
    llm = RepeatingToolLLM()
    router = FakeRouter()
    orch = ChatOrchestrator(
        "system",
        llm,
        router,
        max_rounds=20,
        max_repeated_tool_rounds=2,
    )
    orch.set_tools([])

    answer, events = await orch.process("keep searching")

    assert answer is not None
    assert "Stopped repeated identical tool-call rounds" in answer
    assert len(events) >= 2


@pytest.mark.asyncio
async def test_orchestrator_normalizes_color_search_argument_to_hex():
    llm = ColorSearchLLM()
    router = RecordingRouter(result_payload={"results": []})
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools([])

    answer, events = await orch.process("find sky blue images")

    assert answer == "done"
    assert router.last_name == "photarium_search_color"
    assert router.last_arguments == {"color": "#87ceeb"}
    assert len(events) == 1
    assert events[0].arguments == {"color": "#87ceeb"}


@pytest.mark.asyncio
async def test_orchestrator_surfaces_image_ids_in_semantic_search_results():
    llm = SemanticSearchLLM()
    router = RecordingRouter(
        result_payload={
            "results": [
                {"id": "img_1", "name": "alpha"},
                {"imageId": "img_2", "name": "beta"},
            ]
        }
    )
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools([])

    _answer, events = await orch.process("search images")

    assert len(events) == 1
    result = events[0].result
    assert result["image_ids"] == ["img_1", "img_2"]
    assert result["primary_image_id"] == "img_1"
    assert result["results"][0]["image_id"] == "img_1"
    assert result["results"][1]["image_id"] == "img_2"
