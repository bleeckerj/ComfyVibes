from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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


@dataclass
class UploadLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        call_id="upload1",
                        name="photarium_upload_url",
                        arguments={
                            "url": (
                                "http://127.0.0.1:8188/view?"
                                "filename=NeonCityStreetRainReflections__4x5__s7__a1b2c3d4_00001_.png&type=output"
                            )
                        },
                    )
                ],
            )
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class GetThenUploadLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCall(call_id="get1", name="photarium_get", arguments={"imageId": "src-123"})],
            )
        if self.calls == 2:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        call_id="upload2",
                        name="photarium_upload_from_path",
                        arguments={
                            "filePath": "/tmp/generated.png",
                            "parentId": "src-123",
                            "name": "VariantImage",
                        },
                    )
                ],
            )
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class UploadFromPathLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        call_id="upload1",
                        name="photarium_upload_from_path",
                        arguments={"filePath": "/tmp/generated.png", "name": "VariantImage"},
                    )
                ],
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


@pytest.mark.asyncio
async def test_orchestrator_infers_camelcase_name_for_photarium_upload_call():
    llm = UploadLLM()
    router = RecordingRouter(result_payload={"ok": True})
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "photarium_upload_url",
                    "description": "Upload image by URL",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "name": {"type": "string"},
                        },
                    },
                },
            }
        ]
    )

    answer, events = await orch.process("upload this image")

    assert answer == "done"
    assert len(events) == 1
    assert router.last_name == "photarium_upload_url"
    assert router.last_arguments == {
        "url": (
            "http://127.0.0.1:8188/view?"
            "filename=NeonCityStreetRainReflections__4x5__s7__a1b2c3d4_00001_.png&type=output"
        ),
        "name": "NeonCityStreetRainReflections",
    }


@pytest.mark.asyncio
async def test_orchestrator_converts_upload_url_to_upload_from_path_with_clean_filename(tmp_path: Path):
    llm = UploadLLM()
    router = RecordingRouter(result_payload={"ok": True})
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "photarium_upload_url",
                    "description": "Upload image by URL",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "name": {"type": "string"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "photarium_upload_from_path",
                    "description": "Upload image by local path",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filePath": {"type": "string"},
                            "name": {"type": "string"},
                            "immutable_filename": {"type": "string"},
                        },
                    },
                },
            },
        ]
    )

    downloaded = tmp_path / "raw_download.png"
    downloaded.write_bytes(b"png")

    async def _fake_download(url: str, label: str) -> Path:
        assert "filename=NeonCityStreetRainReflections" in url
        assert label == "NeonCityStreetRainReflections"
        return downloaded

    async def _fake_vision_label(image_path: Path) -> str | None:
        assert image_path.exists()
        return "NeonCityStreetRainReflections"

    orch._download_url_to_temp_file = _fake_download  # type: ignore[method-assign]
    orch._vision_semantic_label_from_image = _fake_vision_label  # type: ignore[method-assign]

    answer, events = await orch.process("upload this image")

    assert answer == "done"
    assert len(events) == 1
    assert router.last_name == "photarium_upload_from_path"
    assert router.last_arguments is not None
    assert router.last_arguments["name"] == "NeonCityStreetRainReflections"
    assert router.last_arguments["immutable_filename"] == "NeonCityStreetRainReflections"
    assert router.last_arguments["filePath"].endswith("NeonCityStreetRainReflections.png")


@pytest.mark.asyncio
async def test_orchestrator_infers_upload_namespace_from_source_image_namespace():
    llm = GetThenUploadLLM()
    router = RecordingRouter()

    async def _call_tool(name, arguments):
        router.last_name = name
        router.last_arguments = dict(arguments or {})
        if name == "photarium_get":
            return {"id": "src-123", "namespace": "campaign-a"}
        return {"ok": True}

    router.call_tool = _call_tool  # type: ignore[method-assign]

    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "photarium_get",
                    "description": "Get image metadata",
                    "parameters": {"type": "object", "properties": {"imageId": {"type": "string"}}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "photarium_upload_from_path",
                    "description": "Upload image by local path",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filePath": {"type": "string"},
                            "parentId": {"type": "string"},
                            "name": {"type": "string"},
                            "namespace": {"type": "string"},
                        },
                    },
                },
            },
        ]
    )

    answer, events = await orch.process("create variant from src-123")

    assert answer == "done"
    assert len(events) == 2
    assert events[1].name == "photarium_upload_from_path"
    assert events[1].arguments["namespace"] == "campaign-a"
    assert router.last_arguments is not None
    assert router.last_arguments["namespace"] == "campaign-a"


@pytest.mark.asyncio
async def test_orchestrator_defaults_upload_namespace_to_cf_default_when_missing():
    llm = UploadFromPathLLM()
    router = RecordingRouter(result_payload={"ok": True})
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "photarium_upload_from_path",
                    "description": "Upload image by local path",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filePath": {"type": "string"},
                            "name": {"type": "string"},
                            "namespace": {"type": "string"},
                        },
                    },
                },
            }
        ]
    )

    answer, events = await orch.process("upload this image")

    assert answer == "done"
    assert len(events) == 1
    assert events[0].arguments["namespace"] == "cf-default"
    assert router.last_arguments is not None
    assert router.last_arguments["namespace"] == "cf-default"
