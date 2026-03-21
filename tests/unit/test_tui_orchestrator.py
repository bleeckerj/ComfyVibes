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
class RepeatingSignalLookupLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        return LLMResponse(
            content="I will keep looking up this signal id.",
            tool_calls=[
                ToolCall(
                    call_id=f"sig_lookup_{self.calls}",
                    name="editorial_signals_get_text",
                    arguments={
                        "signal_id": "abc123missing",
                        "max_string_chars": 2000 if self.calls % 2 == 0 else 12000,
                        "include_raw": self.calls % 3 == 0,
                    },
                )
            ],
        )

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class SignalNotFoundRouter:
    async def call_tool(self, name, arguments):
        signal_id = (arguments or {}).get("signal_id", "unknown")
        raise RuntimeError(f"Error: Signal not found: {signal_id}")


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
class PreviewToolLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        call_id="prev1",
                        name="editorial_ads_preview",
                        arguments={"adIds": ["a", "b"], "mode": "catalog"},
                    )
                ],
            )
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class LargePreviewResultRouter:
    async def call_tool(self, name, arguments):
        # Intentionally place the URL fields late so a naive truncate-from-start
        # would drop them from the LLM tool window.
        return {
            "criteria": {
                "contextPreview": {
                    "resolvedPath": "/features/issue/1/example",
                    "title": "Example",
                    "dek": "Example dek",
                    "paragraphs": ["x" * 50_000],
                }
            },
            "selectedIds": ["a", "b"],
            "previewUrl": "http://127.0.0.1:8788/ads/preview?ids=a,b&limit=2",
            "previewUrls": {
                "catalog": "http://127.0.0.1:8788/ads/preview?ids=a,b&limit=2",
                "single": "http://127.0.0.1:8788/ads/preview/a",
            },
        }


@dataclass
class StrictNoCallLLM:
    async def chat(self, messages, tools):
        raise AssertionError("LLM should not be called for strict preview commands")

    async def chat_stream(self, messages, tools, on_token=None):
        raise AssertionError("LLM should not be called for strict preview commands")


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
class SequenceRouter:
    responses: list
    calls: list[tuple[str, dict]] | None = None

    def __post_init__(self):
        if self.calls is None:
            self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments or {})))
        if not self.responses:
            return {}
        return self.responses.pop(0)


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


@dataclass
class ToolWindowCaptureLLM:
    seen_tool_names: list[str] | None = None

    async def chat(self, messages, tools):
        self.seen_tool_names = [
            str(tool.get("function", {}).get("name", ""))
            for tool in tools
            if isinstance(tool, dict)
        ]
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class MissingOverridesWorkflowRunLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCall(call_id="wf1", name="workflows_run", arguments={"workflow_id": "image_edit"})],
            )
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class MissingOverridesVariationWorkflowRunLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(call_id="wf1", name="workflows_run", arguments={"workflow_id": "image_variation_maker"})
                ],
            )
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class MissingOverridesStitchWorkflowRunLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        call_id="wf1",
                        name="workflows_run",
                        arguments={"workflow_id": "flux_kontext_multi_image_stitching"},
                    )
                ],
            )
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class FlattenedOverridesWorkflowRunLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        call_id="wf1",
                        name="workflows_run",
                        arguments={
                            "workflow_id": "add_tank_tracks",
                            "image": "/tmp/source.png",
                            "filename_prefix": "AddTankTracks_test",
                            "wait_timeout_s": 300,
                        },
                    )
                ],
            )
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class WrappedOverridesWorkflowRunLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        call_id="wf1",
                        name="workflows_run",
                        arguments={
                            "input": {
                                "workflow_id": "add_tank_tracks",
                                "image": "/tmp/source.png",
                                "filename_prefix": "AddTankTracks_test",
                                "wait_timeout_s": 300,
                            }
                        },
                    )
                ],
            )
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class TokenWrappedOverridesWorkflowRunLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        call_id="wf1",
                        name="workflows_run",
                        arguments={
                            "token": {
                                "workflow_id": "add_tank_tracks",
                                "image": "/tmp/source.png",
                                "filename_prefix": "AddTankTracks_test",
                                "wait_timeout_s": 300,
                            }
                        },
                    )
                ],
            )
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class TokenStringWorkflowRunLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        call_id="wf1",
                        name="workflows_run",
                        arguments={
                            "workflow_id": "add_tank_tracks",
                            "token": "secret-token",
                            "image": "/tmp/source.png",
                            "filename_prefix": "AddTankTracks_test",
                        },
                    )
                ],
            )
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class StringifiedOverridesWorkflowRunLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        call_id="wf1",
                        name="workflows_run",
                        arguments={
                            "workflow_id": "add_tank_tracks",
                            "overrides": '{"image":"/tmp/source.png","filename_prefix":"AddTankTracks_test"}',
                            "wait_timeout_s": 300,
                        },
                    )
                ],
            )
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class InvalidOverridesTypeWorkflowRunLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        call_id="wf1",
                        name="workflows_run",
                        arguments={
                            "workflow_id": "add_tank_tracks",
                            "overrides": "not-json",
                        },
                    )
                ],
            )
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class MissingOverridesTankTracksWorkflowRunLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCall(call_id="wf1", name="workflows_run", arguments={"workflow_id": "add_tank_tracks"})],
            )
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class TankTracksUploadLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        call_id="tt1",
                        name="photarium_upload_from_path",
                        arguments={
                            "filePath": "/tmp/out.png",
                            "name": "AddTankTracks",
                            "tags": ["existing-tag"],
                        },
                    )
                ],
            )
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class WorkflowRunWithOutputLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        call_id="wf1",
                        name="workflows_run",
                        arguments={
                            "workflow_id": "z-image-turbo-text_to_image",
                            "overrides": {"prompt": "bar reader", "filename_prefix": "BarReader"},
                        },
                    )
                ],
            )
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class WorkflowRunWithSourceImageLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        call_id="wf1",
                        name="workflows_run",
                        arguments={
                            "workflow_id": "flux_2_klein_4B_prompt_guided_image_edit",
                            "overrides": {
                                "image": "/tmp/source-image.png",
                                "prompt": "make it foggy",
                                "filename_prefix": "FoggyEdit",
                            },
                        },
                    )
                ],
            )
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class WrappedImportFromArtifactLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        call_id="import1",
                        name="workflows_import_from_artifact",
                        arguments={
                            "input": {
                                "path": "/tmp/ComfyUI_01065.png",
                                "workflow_id": "qwen-image-edit-nunchaku",
                            }
                        },
                    )
                ],
            )
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


@dataclass
class AliasedImportFromArtifactLLM:
    calls: int = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        call_id="import1",
                        name="workflows_import_from_artifact",
                        arguments={
                            "artifactPath": "/tmp/ComfyUI_01065.png",
                            "workflow": "qwen-image-edit-nunchaku",
                        },
                    )
                ],
            )
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream(self, messages, tools, on_token=None):
        return await self.chat(messages, tools)


def test_set_tools_repairs_workflows_run_schema_when_overrides_missing():
    orch = ChatOrchestrator("system", EchoLLM(), FakeRouter())
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "workflows_run",
                    "description": "Run workflow",
                    "parameters": {
                        "type": "object",
                        "properties": {"workflow_id": {"type": "string"}},
                        "required": ["workflow_id"],
                    },
                },
            }
        ]
    )

    schema = orch._tool_input_schema_by_name["workflows_run"]
    assert "overrides" in schema["properties"]
    assert schema["properties"]["overrides"]["type"] == "object"
    assert schema["properties"]["overrides"]["additionalProperties"] is True
    assert "workflow_id" in schema["required"]
    assert "overrides" in schema["required"]


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


def test_orchestrator_detects_unresolved_tool_calls():
    messages = [
        {"role": "system", "content": "system"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "workflows.list", "arguments": "{}"},
                }
            ],
        },
    ]
    with pytest.raises(RuntimeError, match="Unresolved tool_call_ids"):
        ChatOrchestrator._assert_no_unresolved_tool_calls(messages)


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
async def test_orchestrator_compacts_editorial_preview_tool_for_llm():
    llm = PreviewToolLLM()
    router = LargePreviewResultRouter()
    orch = ChatOrchestrator(
        "system",
        llm,
        router,
        max_non_system_messages=20,
        max_total_content_chars=200_000,
        max_message_content_chars=1000,
        max_tool_content_chars=220,
    )
    orch.set_tools([])

    answer, _events = await orch.process("preview ads")
    assert answer == "done"

    tool_messages = [m for m in orch._messages if m.get("role") == "tool"]
    assert tool_messages
    # The compacted payload should keep previewUrl visible even with a tight tool window.
    assert "previewUrl" in tool_messages[-1]["content"]
    assert "127.0.0.1:8788/ads/preview" in tool_messages[-1]["content"]


@pytest.mark.asyncio
async def test_strict_preview_ad_by_index_bypasses_llm_and_returns_tool_result_only():
    llm = StrictNoCallLLM()
    router = SequenceRouter(
        responses=[
            {
                "previewUrl": "http://127.0.0.1:8788/ads/preview/resonance-field-expedition-01",
                "selectedIds": ["resonance-field-expedition-01"],
            }
        ]
    )
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "editorial_ads_preview_by_index",
                    "description": "Preview ad by inventory index",
                    "parameters": {"type": "object", "properties": {"index": {"type": "integer"}}},
                },
            }
        ]
    )

    answer, events = await orch.process("preview ad 53", stop_after_tool_calls=True)

    assert answer is None
    assert len(events) == 1
    assert events[0].name == "editorial_ads_preview_by_index"
    assert router.calls == [("editorial_ads_preview_by_index", {"index": 53})]


@pytest.mark.asyncio
async def test_strict_preview_ad_by_index_fails_closed_when_valid_index_has_no_preview_url():
    llm = StrictNoCallLLM()
    router = SequenceRouter(
        responses=[
            {},
            {"items": [{"id": f"ad-{i:03d}"} for i in range(1, 54)]},
        ]
    )
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "editorial_ads_preview_by_index",
                    "description": "Preview ad by inventory index",
                    "parameters": {"type": "object", "properties": {"index": {"type": "integer"}}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "editorial_ads_list_inventory",
                    "description": "List ad inventory",
                    "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}},
                },
            },
        ]
    )

    answer, events = await orch.process("preview ad 53")

    assert answer == (
        "Strict preview command failed: preview tool returned no preview URL "
        "for valid inventory index 53."
    )
    assert [event.name for event in events] == [
        "editorial_ads_preview_by_index",
        "editorial_ads_list_inventory",
    ]
    assert router.calls == [
        ("editorial_ads_preview_by_index", {"index": 53}),
        ("editorial_ads_list_inventory", {"limit": 53}),
    ]


@pytest.mark.asyncio
async def test_strict_preview_ad_by_id_does_not_autocorrect_and_returns_unknown_for_missing_id():
    llm = StrictNoCallLLM()
    router = SequenceRouter(
        responses=[
            {},
            {},
        ]
    )
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "editorial_ads_preview",
                    "description": "Preview ad by id",
                    "parameters": {"type": "object", "properties": {"adId": {"type": "string"}}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "editorial_ads_get_json",
                    "description": "Get ad JSON by id",
                    "parameters": {"type": "object", "properties": {"adId": {"type": "string"}}},
                },
            },
        ]
    )

    answer, events = await orch.process("preview ad fashion-8-bit-pants-skyscraper")

    assert answer == "Unknown ad id: fashion-8-bit-pants-skyscraper."
    assert [event.name for event in events] == [
        "editorial_ads_preview",
        "editorial_ads_get_json",
    ]
    assert router.calls == [
        ("editorial_ads_preview", {"adId": "fashion-8-bit-pants-skyscraper"}),
        ("editorial_ads_get_json", {"adId": "fashion-8-bit-pants-skyscraper"}),
    ]


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
async def test_orchestrator_stops_repeated_signal_not_found_lookup_loops():
    llm = RepeatingSignalLookupLLM()
    router = SignalNotFoundRouter()
    orch = ChatOrchestrator(
        "system",
        llm,
        router,
        max_rounds=20,
    )
    orch.set_tools([])

    answer, events = await orch.process("open this signal")

    assert answer is not None
    assert "Stopped repeated signal lookup failures" in answer
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


@pytest.mark.asyncio
async def test_orchestrator_preflights_missing_workflows_run_overrides_using_shorthand_and_photarium_check():
    llm = MissingOverridesWorkflowRunLLM()

    @dataclass
    class _Router:
        calls: list[tuple[str, dict]]

        async def call_tool(self, name, arguments):
            self.calls.append((name, dict(arguments or {})))
            if name == "photarium_get":
                return {"id": "xyz", "namespace": "cf-default"}
            return {"ok": True}

    router = _Router(calls=[])
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "workflows_run",
                    "description": "Run workflow",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "workflow_id": {"type": "string"},
                            "overrides": {"type": "object"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "photarium_get",
                    "description": "Get image metadata",
                    "parameters": {
                        "type": "object",
                        "properties": {"imageId": {"type": "string"}},
                    },
                },
            },
        ]
    )

    answer, events = await orch.process("edit image xyz")

    assert answer == "done"
    assert len(events) == 1
    assert events[0].name == "workflows_run"
    assert events[0].error is not None
    assert "missing required 'overrides' object" in events[0].error
    assert "verified via photarium_get" in events[0].error
    assert router.calls == [("photarium_get", {"imageId": "xyz"})]


@pytest.mark.asyncio
async def test_orchestrator_synthesizes_missing_workflows_run_overrides_for_text_to_image() -> None:
    llm = MissingOverridesWorkflowRunLLM()

    @dataclass
    class _Router:
        calls: list[tuple[str, dict]]

        async def call_tool(self, name, arguments):
            self.calls.append((name, dict(arguments or {})))
            if name == "workflows_params_get":
                return {
                    "params": {
                        "params": [
                            {"name": "prompt", "type": "string"},
                            {"name": "seed", "type": "int"},
                            {"name": "filename_prefix", "type": "string"},
                        ]
                    }
                }
            if name == "workflows_run":
                return {"ok": True}
            return {"ok": True}

    router = _Router(calls=[])
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "workflows_run",
                    "description": "Run workflow",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "workflow_id": {"type": "string"},
                            "overrides": {"type": "object"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "workflows_params_get",
                    "description": "Get workflow params",
                    "parameters": {
                        "type": "object",
                        "properties": {"workflow_id": {"type": "string"}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "photarium_get",
                    "description": "Get image metadata",
                    "parameters": {
                        "type": "object",
                        "properties": {"imageId": {"type": "string"}},
                    },
                },
            },
        ]
    )

    answer, events = await orch.process('create an image using prompt: "retro diner in rain"')

    assert answer == "done"
    assert len(events) == 1
    assert events[0].name == "workflows_run"
    assert events[0].error is None
    assert isinstance(events[0].arguments.get("overrides"), dict)
    overrides = events[0].arguments["overrides"]
    assert overrides.get("prompt") == "retro diner in rain"
    assert isinstance(overrides.get("seed"), int)
    assert isinstance(overrides.get("filename_prefix"), str)
    assert overrides["filename_prefix"].startswith("image_edit_")
    assert [name for name, _ in router.calls] == ["workflows_params_get", "workflows_run"]


@pytest.mark.asyncio
async def test_orchestrator_keeps_missing_overrides_block_for_image_input_workflow() -> None:
    llm = MissingOverridesWorkflowRunLLM()

    @dataclass
    class _Router:
        calls: list[tuple[str, dict]]

        async def call_tool(self, name, arguments):
            self.calls.append((name, dict(arguments or {})))
            if name == "workflows_params_get":
                return {"params": {"params": [{"name": "image", "type": "string"}, {"name": "seed", "type": "int"}]}}
            return {"ok": True}

    router = _Router(calls=[])
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "workflows_run",
                    "description": "Run workflow",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "workflow_id": {"type": "string"},
                            "overrides": {"type": "object"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "workflows_params_get",
                    "description": "Get workflow params",
                    "parameters": {
                        "type": "object",
                        "properties": {"workflow_id": {"type": "string"}},
                    },
                },
            },
        ]
    )

    answer, events = await orch.process("edit image xyz")

    assert answer == "done"
    assert len(events) == 1
    assert events[0].name == "workflows_run"
    assert events[0].error is not None
    assert "missing required 'overrides' object" in events[0].error
    assert [name for name, _ in router.calls] == ["workflows_params_get"]


@pytest.mark.asyncio
async def test_orchestrator_repairs_flattened_workflows_run_overrides():
    llm = FlattenedOverridesWorkflowRunLLM()
    router = RecordingRouter(result_payload={"ok": True})
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "workflows_run",
                    "description": "Run workflow",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "workflow_id": {"type": "string"},
                            "overrides": {"type": "object"},
                            "wait_timeout_s": {"type": "number"},
                        },
                    },
                },
            }
        ]
    )

    answer, events = await orch.process("run tank tracks")

    assert answer == "done"
    assert len(events) == 1
    assert events[0].error is None
    assert events[0].arguments == {
        "workflow_id": "add_tank_tracks",
        "wait_timeout_s": 300,
        "overrides": {
            "image": "/tmp/source.png",
            "filename_prefix": "AddTankTracks_test",
        },
    }
    assert router.last_arguments == events[0].arguments


@pytest.mark.asyncio
async def test_orchestrator_repairs_wrapped_workflows_run_overrides():
    llm = WrappedOverridesWorkflowRunLLM()
    router = RecordingRouter(result_payload={"ok": True})
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "workflows_run",
                    "description": "Run workflow",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "workflow_id": {"type": "string"},
                            "overrides": {"type": "object"},
                            "wait_timeout_s": {"type": "number"},
                        },
                    },
                },
            }
        ]
    )

    answer, events = await orch.process("run tank tracks")

    assert answer == "done"
    assert len(events) == 1
    assert events[0].error is None
    assert events[0].arguments == {
        "workflow_id": "add_tank_tracks",
        "wait_timeout_s": 300,
        "overrides": {
            "image": "/tmp/source.png",
            "filename_prefix": "AddTankTracks_test",
        },
    }
    assert router.last_arguments == events[0].arguments


@pytest.mark.asyncio
async def test_orchestrator_repairs_token_wrapped_workflows_run_overrides():
    llm = TokenWrappedOverridesWorkflowRunLLM()
    router = RecordingRouter(result_payload={"ok": True})
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "workflows_run",
                    "description": "Run workflow",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "workflow_id": {"type": "string"},
                            "overrides": {"type": "object"},
                            "wait_timeout_s": {"type": "number"},
                        },
                    },
                },
            }
        ]
    )

    answer, events = await orch.process("run tank tracks")

    assert answer == "done"
    assert len(events) == 1
    assert events[0].error is None
    assert events[0].arguments == {
        "workflow_id": "add_tank_tracks",
        "wait_timeout_s": 300,
        "overrides": {
            "image": "/tmp/source.png",
            "filename_prefix": "AddTankTracks_test",
        },
    }
    assert router.last_arguments == events[0].arguments


@pytest.mark.asyncio
async def test_orchestrator_preserves_string_auth_token_when_repairing_workflows_run_overrides():
    llm = TokenStringWorkflowRunLLM()
    router = RecordingRouter(result_payload={"ok": True})
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "workflows_run",
                    "description": "Run workflow",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "workflow_id": {"type": "string"},
                            "token": {"type": "string"},
                            "overrides": {"type": "object"},
                        },
                    },
                },
            }
        ]
    )

    answer, events = await orch.process("run tank tracks")

    assert answer == "done"
    assert len(events) == 1
    assert events[0].error is None
    assert events[0].arguments == {
        "workflow_id": "add_tank_tracks",
        "token": "secret-token",
        "overrides": {
            "image": "/tmp/source.png",
            "filename_prefix": "AddTankTracks_test",
        },
    }
    assert router.last_arguments == events[0].arguments


@pytest.mark.asyncio
async def test_orchestrator_repairs_stringified_workflows_run_overrides():
    llm = StringifiedOverridesWorkflowRunLLM()
    router = RecordingRouter(result_payload={"ok": True})
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "workflows_run",
                    "description": "Run workflow",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "workflow_id": {"type": "string"},
                            "overrides": {"type": "object"},
                            "wait_timeout_s": {"type": "number"},
                        },
                    },
                },
            }
        ]
    )

    answer, events = await orch.process("run tank tracks")

    assert answer == "done"
    assert len(events) == 1
    assert events[0].error is None
    assert events[0].arguments == {
        "workflow_id": "add_tank_tracks",
        "wait_timeout_s": 300,
        "overrides": {
            "image": "/tmp/source.png",
            "filename_prefix": "AddTankTracks_test",
        },
    }
    assert router.last_arguments == events[0].arguments


@pytest.mark.asyncio
async def test_orchestrator_blocks_non_object_workflows_run_overrides():
    llm = InvalidOverridesTypeWorkflowRunLLM()
    router = RecordingRouter(result_payload={"ok": True})
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "workflows_run",
                    "description": "Run workflow",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "workflow_id": {"type": "string"},
                            "overrides": {"type": "object"},
                        },
                    },
                },
            }
        ]
    )

    answer, events = await orch.process("run tank tracks")

    assert answer == "done"
    assert len(events) == 1
    assert events[0].error is not None
    assert "overrides' must be an object" in events[0].error
    assert router.last_name is None


@pytest.mark.asyncio
async def test_orchestrator_repairs_tanktracks_workflows_run_missing_overrides_with_download():
    llm = MissingOverridesTankTracksWorkflowRunLLM()

    @dataclass
    class _Router:
        calls: list[tuple[str, dict]]

        async def call_tool(self, name, arguments):
            payload = dict(arguments or {})
            self.calls.append((name, payload))
            if name == "photarium_download_image":
                return {"savedPath": "/tmp/tanktracks_source.png"}
            return {"ok": True}

    router = _Router(calls=[])
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "workflows_run",
                    "description": "Run workflow",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "workflow_id": {"type": "string"},
                            "overrides": {"type": "object"},
                            "wait_timeout_s": {"type": "number"},
                            "wait_poll_ms": {"type": "integer"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "photarium_download_image",
                    "description": "Download image",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "imageId": {"type": "string"},
                            "savePath": {"type": "string"},
                            "includeBase64": {"type": "boolean"},
                        },
                    },
                },
            },
        ]
    )

    answer, events = await orch.process(
        "TANK TRACKS FLOW REQUEST\n"
        "Source catalog image ID: 44a4417e-662f-4275-413a-9dba9a6de200\n"
        "Workflow preference: add_tank_tracks\n"
    )

    assert answer == "done"
    assert len(events) == 1
    assert events[0].name == "workflows_run"
    assert events[0].error is None
    assert events[0].arguments["workflow_id"] == "add_tank_tracks"
    assert events[0].arguments["wait_timeout_s"] == 300
    assert events[0].arguments["wait_poll_ms"] == 1000
    assert events[0].arguments["overrides"]["image"] == "/tmp/tanktracks_source.png"
    assert isinstance(events[0].arguments["overrides"]["filename_prefix"], str)
    assert events[0].arguments["overrides"]["filename_prefix"].startswith("AddTankTracks_")
    assert router.calls[0][0] == "photarium_download_image"
    assert router.calls[1][0] == "workflows_run"


@pytest.mark.asyncio
async def test_orchestrator_repairs_raw_tanktracks_command_missing_overrides_with_download():
    llm = MissingOverridesTankTracksWorkflowRunLLM()

    @dataclass
    class _Router:
        calls: list[tuple[str, dict]]

        async def call_tool(self, name, arguments):
            payload = dict(arguments or {})
            self.calls.append((name, payload))
            if name == "photarium_download_image":
                return {"savedPath": "/tmp/tanktracks_source.png"}
            return {"ok": True}

    router = _Router(calls=[])
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "workflows_run",
                    "description": "Run workflow",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "workflow_id": {"type": "string"},
                            "overrides": {"type": "object"},
                            "wait_timeout_s": {"type": "number"},
                            "wait_poll_ms": {"type": "integer"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "photarium_download_image",
                    "description": "Download image",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "imageId": {"type": "string"},
                            "savePath": {"type": "string"},
                            "includeBase64": {"type": "boolean"},
                        },
                    },
                },
            },
        ]
    )

    answer, events = await orch.process("/tanktracks 44a4417e-662f-4275-413a-9dba9a6de200")

    assert answer == "done"
    assert len(events) == 1
    assert events[0].name == "workflows_run"
    assert events[0].error is None
    assert events[0].arguments["workflow_id"] == "add_tank_tracks"
    assert events[0].arguments["overrides"]["image"] == "/tmp/tanktracks_source.png"
    assert isinstance(events[0].arguments["overrides"]["filename_prefix"], str)
    assert router.calls[0][0] == "photarium_download_image"
    assert router.calls[1][0] == "workflows_run"


@pytest.mark.asyncio
async def test_strict_tanktracks_flow_bypasses_llm_and_runs_tools_directly():
    @dataclass
    class _LLM:
        calls: int = 0

        async def chat(self, messages, tools):
            self.calls += 1
            return LLMResponse(content="should not be called", tool_calls=[])

        async def chat_stream(self, messages, tools, on_token=None):
            self.calls += 1
            return LLMResponse(content="should not be called", tool_calls=[])

    @dataclass
    class _Router:
        calls: list[tuple[str, dict]]

        async def call_tool(self, name, arguments):
            payload = dict(arguments or {})
            self.calls.append((name, payload))
            if name == "photarium_get":
                return {
                    "imageId": "44a4417e-662f-4275-413a-9dba9a6de200",
                    "parentId": "deaf6608-6cfc-45dd-abb8-559833291800",
                    "namespace": "cf-default",
                }
            if name == "photarium_download_image":
                return {"savedPath": "/tmp/tanktracks_source.png"}
            if name == "workflows_run":
                return {
                    "output_images": [
                        {
                            "filename": "AddTankTracks_result.png",
                            "local_path": "/tmp/AddTankTracks_result.png",
                        }
                    ]
                }
            if name == "photarium_upload_from_path":
                return {"imageId": "uploaded-123", "parentId": payload.get("parentId")}
            return {"ok": True}

    llm = _LLM()
    router = _Router(calls=[])
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
                    "name": "photarium_download_image",
                    "description": "Download image",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "imageId": {"type": "string"},
                            "savePath": {"type": "string"},
                            "includeBase64": {"type": "boolean"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "workflows_run",
                    "description": "Run workflow",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "workflow_id": {"type": "string"},
                            "overrides": {"type": "object"},
                            "wait_timeout_s": {"type": "number"},
                            "wait_poll_ms": {"type": "integer"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "photarium_upload_from_path",
                    "description": "Upload image",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filePath": {"type": "string"},
                            "parentId": {"type": "string"},
                            "name": {"type": "string"},
                            "tags": {"type": "array"},
                            "namespace": {"type": "string"},
                        },
                    },
                },
            },
        ]
    )

    answer, events = await orch.process(
        "TANK TRACKS FLOW REQUEST\n"
        "Run this as a deterministic, minimal-branch flow inside the TUI.\n\n"
        "Source catalog image ID: 44a4417e-662f-4275-413a-9dba9a6de200\n"
        "Requested upload target image ID: 44a4417e-662f-4275-413a-9dba9a6de200\n"
        "Workflow preference: add_tank_tracks\n"
        "Seed override: use workflow default seed\n"
        "Denoise override: use workflow default denoise\n"
        "Post aspect ratio adjustment: none\n"
    )

    assert llm.calls == 0
    assert "uploaded-123" in answer
    assert len(events) == 4
    assert [event.name for event in events] == [
        "photarium_get",
        "photarium_download_image",
        "workflows_run",
        "photarium_upload_from_path",
    ]
    assert router.calls[0][0] == "photarium_get"
    assert router.calls[1][0] == "photarium_download_image"
    assert router.calls[2][0] == "workflows_run"
    assert router.calls[3][0] == "photarium_upload_from_path"
    assert router.calls[3][1]["parentId"] == "deaf6608-6cfc-45dd-abb8-559833291800"
    assert router.calls[3][1]["tags"] == ["tank tracks", "caterpillar tracks", "tracks"]


@pytest.mark.asyncio
async def test_strict_tanktracks_flow_extracts_outputs_from_watch_history():
    @dataclass
    class _LLM:
        calls: int = 0

        async def chat(self, messages, tools):
            self.calls += 1
            return LLMResponse(content="should not be called", tool_calls=[])

        async def chat_stream(self, messages, tools, on_token=None):
            self.calls += 1
            return LLMResponse(content="should not be called", tool_calls=[])

    @dataclass
    class _Router:
        calls: list[tuple[str, dict]]

        async def call_tool(self, name, arguments):
            payload = dict(arguments or {})
            self.calls.append((name, payload))
            if name == "photarium_get":
                return {
                    "imageId": "44a4417e-662f-4275-413a-9dba9a6de200",
                    "parentId": "deaf6608-6cfc-45dd-abb8-559833291800",
                    "namespace": "cf-default",
                }
            if name == "photarium_download_image":
                return {"savedPath": "/tmp/tanktracks_source.png"}
            if name == "workflows_run":
                return {"status": "complete", "prompt_id": "abc123", "output_images": []}
            if name == "workflows_watch":
                return {
                    "status": "complete",
                    "prompt_id": "abc123",
                    "history": {
                        "abc123": {
                            "outputs": {
                                "79": {
                                    "images": [
                                        {
                                            "filename": "AddTankTracks_watch.png",
                                            "subfolder": "2026-03-20",
                                            "type": "output",
                                        }
                                    ]
                                }
                            }
                        }
                    },
                }
            if name == "comfy_download_image":
                return {"savedPath": "/tmp/AddTankTracks_watch.png"}
            if name == "photarium_upload_from_path":
                return {"imageId": "uploaded-watch", "parentId": payload.get("parentId")}
            return {"ok": True}

    llm = _LLM()
    router = _Router(calls=[])
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
                    "name": "photarium_download_image",
                    "description": "Download image",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "imageId": {"type": "string"},
                            "savePath": {"type": "string"},
                            "includeBase64": {"type": "boolean"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "workflows_run",
                    "description": "Run workflow",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "workflow_id": {"type": "string"},
                            "overrides": {"type": "object"},
                            "wait_timeout_s": {"type": "number"},
                            "wait_poll_ms": {"type": "integer"},
                            "force": {"type": "boolean"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "workflows_watch",
                    "description": "Watch workflow",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "prompt_id": {"type": "string"},
                            "inactivity_timeout_s": {"type": "number"},
                            "include_history": {"type": "boolean"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "comfy_download_image",
                    "description": "Download Comfy output",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filename": {"type": "string"},
                            "subfolder": {"type": "string"},
                            "image_type": {"type": "string"},
                            "save_path": {"type": "string"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "photarium_upload_from_path",
                    "description": "Upload image",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filePath": {"type": "string"},
                            "parentId": {"type": "string"},
                            "name": {"type": "string"},
                            "tags": {"type": "array"},
                            "namespace": {"type": "string"},
                        },
                    },
                },
            },
        ]
    )

    answer, events = await orch.process(
        "TANK TRACKS FLOW REQUEST\n"
        "Run this as a deterministic, minimal-branch flow inside the TUI.\n\n"
        "Source catalog image ID: 44a4417e-662f-4275-413a-9dba9a6de200\n"
        "Requested upload target image ID: 44a4417e-662f-4275-413a-9dba9a6de200\n"
        "Workflow preference: add_tank_tracks\n"
        "Seed override: use workflow default seed\n"
        "Denoise override: use workflow default denoise\n"
        "Post aspect ratio adjustment: none\n"
    )

    assert llm.calls == 0
    assert "uploaded-watch" in answer
    assert [event.name for event in events] == [
        "photarium_get",
        "photarium_download_image",
        "workflows_run",
        "workflows_watch",
        "photarium_upload_from_path",
    ]
    assert router.calls[3][0] == "workflows_watch"
    assert router.calls[4][0] == "comfy_download_image"
    assert router.calls[5][0] == "photarium_upload_from_path"


@pytest.mark.asyncio
async def test_orchestrator_repairs_variation_workflows_run_with_downloaded_source_image():
    llm = MissingOverridesVariationWorkflowRunLLM()

    @dataclass
    class _Router:
        calls: list[tuple[str, dict]]

        async def call_tool(self, name, arguments):
            payload = dict(arguments or {})
            self.calls.append((name, payload))
            if name == "photarium_download_image":
                save_path = payload.get("savePath")
                return {"savedPath": save_path}
            return {"ok": True}

    router = _Router(calls=[])
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "workflows_run",
                    "description": "Run workflow",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "workflow_id": {"type": "string"},
                            "overrides": {"type": "object"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "photarium_download_image",
                    "description": "Download image",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "imageId": {"type": "string"},
                            "savePath": {"type": "string"},
                            "includeBase64": {"type": "boolean"},
                        },
                    },
                },
            },
        ]
    )

    answer, events = await orch.process(
        "IMAGE VARIATION FLOW REQUEST\n"
        "Source catalog image ID: 7d64bf7b-4cc4-442c-12ba-ab9b24b58e00\n"
    )

    assert answer == "done"
    assert len(events) == 1
    assert events[0].name == "workflows_run"
    assert events[0].error is None
    assert events[0].arguments["workflow_id"] == "image_variation_maker"
    assert "overrides" in events[0].arguments
    assert "image" in events[0].arguments["overrides"]
    assert isinstance(events[0].arguments["overrides"]["image"], str)
    assert events[0].arguments["overrides"]["image"].strip()
    assert router.calls[0][0] == "photarium_download_image"
    assert router.calls[0][1]["imageId"] == "7d64bf7b-4cc4-442c-12ba-ab9b24b58e00"
    assert router.calls[1][0] == "workflows_run"


@pytest.mark.asyncio
async def test_orchestrator_repairs_stitch_workflows_run_missing_overrides_with_downloads():
    llm = MissingOverridesStitchWorkflowRunLLM()

    @dataclass
    class _Router:
        calls: list[tuple[str, dict]]

        async def call_tool(self, name, arguments):
            payload = dict(arguments or {})
            self.calls.append((name, payload))
            if name == "photarium_download_image":
                image_id = str(payload.get("imageId") or "unknown")
                return {"savedPath": f"/tmp/{image_id}.png"}
            return {"ok": True}

    router = _Router(calls=[])
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "workflows_run",
                    "description": "Run workflow",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "workflow_id": {"type": "string"},
                            "overrides": {"type": "object"},
                            "wait_timeout_s": {"type": "number"},
                            "wait_poll_ms": {"type": "integer"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "photarium_download_image",
                    "description": "Download image",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "imageId": {"type": "string"},
                            "savePath": {"type": "string"},
                            "includeBase64": {"type": "boolean"},
                        },
                    },
                },
            },
        ]
    )

    source_1 = "44a4417e-662f-4275-413a-9dba9a6de200"
    source_2 = "7d64bf7b-4cc4-442c-12ba-ab9b24b58e00"
    answer, events = await orch.process(
        "IMAGE STITCHING FLOW REQUEST\n"
        f"Source catalog image IDs: {source_1}, {source_2}\n"
        "Workflow preference: flux_kontext_multi_image_stitching\n"
    )

    assert answer == "done"
    assert len(events) == 1
    assert events[0].name == "workflows_run"
    assert events[0].error is None
    assert events[0].arguments["workflow_id"] == "flux_kontext_multi_image_stitching"
    assert events[0].arguments["wait_timeout_s"] == 300
    assert events[0].arguments["wait_poll_ms"] == 1000
    assert events[0].arguments["overrides"]["image"] == f"/tmp/{source_1}.png"
    assert events[0].arguments["overrides"]["image_2"] == f"/tmp/{source_2}.png"
    assert router.calls[0][0] == "photarium_download_image"
    assert router.calls[0][1]["imageId"] == source_1
    assert router.calls[1][0] == "photarium_download_image"
    assert router.calls[1][1]["imageId"] == source_2
    assert router.calls[2][0] == "workflows_run"


@pytest.mark.asyncio
async def test_orchestrator_auto_uploads_workflow_outputs_from_local_path():
    llm = WorkflowRunWithOutputLLM()

    @dataclass
    class _Router:
        calls: list[tuple[str, dict]]

        async def call_tool(self, name, arguments):
            payload = dict(arguments or {})
            self.calls.append((name, payload))
            if name == "workflows_run":
                return {
                    "prompt_id": "p-1",
                    "output_images": [
                        {
                            "filename": "BarReader_00001_.png",
                            "type": "output",
                            "subfolder": "2026-03-04",
                            "local_path": "/tmp/BarReader_00001_.png",
                        }
                    ],
                }
            if name == "photarium_upload_from_path":
                return {"image_id": "img-123", "namespace": "cf-default"}
            return {"ok": True}

    router = _Router(calls=[])
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "workflows_run",
                    "description": "Run workflow",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "workflow_id": {"type": "string"},
                            "overrides": {"type": "object"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "photarium_upload_from_path",
                    "description": "Upload path",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filePath": {"type": "string"},
                            "name": {"type": "string"},
                            "namespace": {"type": "string"},
                            "prompt": {"type": "string"},
                        },
                    },
                },
            },
        ]
    )

    answer, events = await orch.process("create z-image prompt bar reader")

    assert answer == "done"
    assert len(events) == 1
    assert events[0].name == "workflows_run"
    assert isinstance(events[0].result, dict)
    assert events[0].result["auto_upload"]["status"] == "uploaded"
    assert router.calls[0][0] == "workflows_run"
    assert router.calls[1][0] == "photarium_upload_from_path"
    assert router.calls[1][1]["filePath"] == "/tmp/BarReader_00001_.png"
    assert router.calls[1][1]["namespace"] == "cf-default"
    assert router.calls[1][1]["prompt"] == "bar reader"


@pytest.mark.asyncio
async def test_orchestrator_skips_auto_upload_for_tanktracks_flow_request():
    llm = WorkflowRunWithOutputLLM()

    @dataclass
    class _Router:
        calls: list[tuple[str, dict]]

        async def call_tool(self, name, arguments):
            payload = dict(arguments or {})
            self.calls.append((name, payload))
            if name == "workflows_run":
                return {
                    "prompt_id": "p-1",
                    "output_images": [
                        {
                            "filename": "AddTankTracks_00001_.png",
                            "type": "output",
                            "subfolder": "2026-03-04",
                            "local_path": "/tmp/AddTankTracks_00001_.png",
                        }
                    ],
                }
            return {"ok": True}

    router = _Router(calls=[])
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "workflows_run",
                    "description": "Run workflow",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "workflow_id": {"type": "string"},
                            "overrides": {"type": "object"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "photarium_upload_from_path",
                    "description": "Upload path",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filePath": {"type": "string"},
                            "name": {"type": "string"},
                            "namespace": {"type": "string"},
                        },
                    },
                },
            },
        ]
    )

    answer, events = await orch.process(
        "TANK TRACKS FLOW REQUEST\n"
        "Source catalog image ID: 44a4417e-662f-4275-413a-9dba9a6de200\n"
        "Workflow preference: add_tank_tracks\n"
    )

    assert answer == "done"
    assert len(events) == 1
    assert events[0].name == "workflows_run"
    assert isinstance(events[0].result, dict)
    assert events[0].result["auto_upload"]["status"] == "skipped"
    assert events[0].result["auto_upload"]["reason"] == "tanktracks_flow_handles_upload"
    assert [name for name, _ in router.calls] == ["workflows_run"]


@pytest.mark.asyncio
async def test_orchestrator_auto_upload_downloads_output_when_local_path_missing():
    llm = WorkflowRunWithOutputLLM()

    @dataclass
    class _Router:
        calls: list[tuple[str, dict]]

        async def call_tool(self, name, arguments):
            payload = dict(arguments or {})
            self.calls.append((name, payload))
            if name == "workflows_run":
                return {
                    "prompt_id": "p-1",
                    "output_images": [
                        {
                            "filename": "BarReader_00001_.png",
                            "type": "output",
                            "subfolder": "2026-03-04",
                        }
                    ],
                }
            if name == "comfy_download_image":
                return {"local_path": "/tmp/downloaded_BarReader_00001_.png"}
            if name == "photarium_upload_from_path":
                return {"image_id": "img-456", "namespace": "cf-default"}
            return {"ok": True}

    router = _Router(calls=[])
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "workflows_run",
                    "description": "Run workflow",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "workflow_id": {"type": "string"},
                            "overrides": {"type": "object"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "comfy_download_image",
                    "description": "Download image",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filename": {"type": "string"},
                            "image_type": {"type": "string"},
                            "subfolder": {"type": "string"},
                            "save_path": {"type": "string"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "photarium_upload_from_path",
                    "description": "Upload path",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filePath": {"type": "string"},
                            "name": {"type": "string"},
                            "namespace": {"type": "string"},
                        },
                    },
                },
            },
        ]
    )

    answer, events = await orch.process("create z-image prompt bar reader")

    assert answer == "done"
    assert len(events) == 1
    assert events[0].name == "workflows_run"
    assert isinstance(events[0].result, dict)
    assert events[0].result["auto_upload"]["status"] == "uploaded"
    assert router.calls[0][0] == "workflows_run"
    assert router.calls[1][0] == "comfy_download_image"
    assert router.calls[2][0] == "photarium_upload_from_path"
    assert router.calls[2][1]["filePath"] == "/tmp/downloaded_BarReader_00001_.png"


@pytest.mark.asyncio
async def test_orchestrator_auto_uploads_workflow_outputs_as_variants_of_source_image():
    llm = WorkflowRunWithSourceImageLLM()

    @dataclass
    class _Router:
        calls: list[tuple[str, dict]]

        async def call_tool(self, name, arguments):
            payload = dict(arguments or {})
            self.calls.append((name, payload))
            if name == "workflows_run":
                return {
                    "prompt_id": "p-2",
                    "output_images": [
                        {
                            "filename": "FoggyEdit_00001_.png",
                            "type": "output",
                            "subfolder": "2026-03-06",
                            "local_path": "/tmp/FoggyEdit_00001_.png",
                        }
                    ],
                }
            if name == "photarium_get":
                return {
                    "id": "src-123",
                    "namespace": "cf-autotrader",
                    "parentId": "family-root-999",
                }
            if name == "photarium_upload_from_path":
                return {"image_id": "img-789", "namespace": "cf-autotrader"}
            return {"ok": True}

    router = _Router(calls=[])
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "workflows_run",
                    "description": "Run workflow",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "workflow_id": {"type": "string"},
                            "overrides": {"type": "object"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "photarium_get",
                    "description": "Get image metadata",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "imageId": {"type": "string"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "photarium_upload_from_path",
                    "description": "Upload path",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filePath": {"type": "string"},
                            "name": {"type": "string"},
                            "namespace": {"type": "string"},
                            "parentId": {"type": "string"},
                            "prompt": {"type": "string"},
                        },
                    },
                },
            },
        ]
    )
    orch._record_source_image_local_path("/tmp/source-image.png", "src-123")

    answer, events = await orch.process(
        "IMAGE EDIT FLOW REQUEST\n"
        "Source catalog image ID: src-123\n"
        "Workflow preference: flux_2_klein_4B_prompt_guided_image_edit\n"
    )

    assert answer == "done"
    assert len(events) == 1
    assert events[0].name == "workflows_run"
    assert isinstance(events[0].result, dict)
    assert events[0].result["auto_upload"]["status"] == "uploaded"
    assert router.calls[0][0] == "workflows_run"
    assert router.calls[1][0] == "photarium_get"
    assert router.calls[2][0] == "photarium_upload_from_path"
    assert router.calls[2][1]["filePath"] == "/tmp/FoggyEdit_00001_.png"
    assert router.calls[2][1]["parentId"] == "family-root-999"
    assert router.calls[2][1]["namespace"] == "cf-autotrader"
    assert router.calls[2][1]["prompt"] == "make it foggy"


@pytest.mark.asyncio
async def test_orchestrator_auto_upload_uses_family_root_when_source_is_variant_without_parent_id():
    llm = WorkflowRunWithSourceImageLLM()

    @dataclass
    class _Router:
        calls: list[tuple[str, dict]]

        async def call_tool(self, name, arguments):
            payload = dict(arguments or {})
            self.calls.append((name, payload))
            if name == "workflows_run":
                return {
                    "prompt_id": "p-3",
                    "output_images": [
                        {
                            "filename": "FoggyEdit_00001_.png",
                            "type": "output",
                            "subfolder": "2026-03-06",
                            "local_path": "/tmp/FoggyEdit_00001_.png",
                        }
                    ],
                }
            if name == "photarium_get":
                return {
                    "id": "src-variant-123",
                    "namespace": "cf-autotrader",
                    "isVariant": True,
                    "parentId": None,
                    "familyRootId": "family-root-555",
                }
            if name == "photarium_upload_from_path":
                return {"image_id": "img-790", "namespace": "cf-autotrader"}
            return {"ok": True}

    router = _Router(calls=[])
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "workflows_run",
                    "description": "Run workflow",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "workflow_id": {"type": "string"},
                            "overrides": {"type": "object"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "photarium_get",
                    "description": "Get image metadata",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "imageId": {"type": "string"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "photarium_upload_from_path",
                    "description": "Upload path",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filePath": {"type": "string"},
                            "name": {"type": "string"},
                            "namespace": {"type": "string"},
                            "parentId": {"type": "string"},
                            "prompt": {"type": "string"},
                        },
                    },
                },
            },
        ]
    )
    orch._record_source_image_local_path("/tmp/source-image.png", "src-variant-123")

    answer, events = await orch.process(
        "IMAGE EDIT FLOW REQUEST\n"
        "Source catalog image ID: src-variant-123\n"
        "Workflow preference: flux_2_klein_4B_prompt_guided_image_edit\n"
    )

    assert answer == "done"
    assert len(events) == 1
    assert events[0].name == "workflows_run"
    assert isinstance(events[0].result, dict)
    assert events[0].result["auto_upload"]["status"] == "uploaded"
    assert router.calls[1][0] == "photarium_get"
    assert router.calls[2][0] == "photarium_upload_from_path"
    assert router.calls[2][1]["parentId"] == "family-root-555"


@pytest.mark.asyncio
async def test_orchestrator_repairs_wrapped_workflows_import_from_artifact_arguments():
    llm = WrappedImportFromArtifactLLM()
    router = RecordingRouter(result_payload={"ok": True})
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "workflows_import_from_artifact",
                    "description": "Import workflow from artifact",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "workflow_id": {"type": "string"},
                        },
                        "required": ["path", "workflow_id"],
                    },
                },
            }
        ]
    )

    answer, events = await orch.process("import this workflow from artifact")

    assert answer == "done"
    assert len(events) == 1
    assert events[0].error is None
    assert events[0].arguments["path"] == "/tmp/ComfyUI_01065.png"
    assert events[0].arguments["workflow_id"] == "qwen-image-edit-nunchaku"
    assert router.last_arguments is not None
    assert router.last_arguments["path"] == "/tmp/ComfyUI_01065.png"
    assert router.last_arguments["workflow_id"] == "qwen-image-edit-nunchaku"


@pytest.mark.asyncio
async def test_orchestrator_repairs_aliased_workflows_import_from_artifact_arguments():
    llm = AliasedImportFromArtifactLLM()
    router = RecordingRouter(result_payload={"ok": True})
    orch = ChatOrchestrator("system", llm, router)
    orch.set_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "workflows_import_from_artifact",
                    "description": "Import workflow from artifact",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "workflow_id": {"type": "string"},
                        },
                        "required": ["path", "workflow_id"],
                    },
                },
            }
        ]
    )

    answer, events = await orch.process("import this workflow from artifact")

    assert answer == "done"
    assert len(events) == 1
    assert events[0].error is None
    assert events[0].arguments["path"] == "/tmp/ComfyUI_01065.png"
    assert events[0].arguments["workflow_id"] == "qwen-image-edit-nunchaku"
    assert router.last_arguments is not None
    assert router.last_arguments["path"] == "/tmp/ComfyUI_01065.png"
    assert router.last_arguments["workflow_id"] == "qwen-image-edit-nunchaku"


@pytest.mark.asyncio
async def test_tanktracks_upload_conventions_add_tags_and_preserve_display_name():
    llm = TankTracksUploadLLM()
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
                            "tags": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
            }
        ]
    )

    answer, events = await orch.process(
        "TANK TRACKS FLOW REQUEST\nUpload result as variant.\n"
    )

    assert answer == "done"
    assert len(events) == 1
    assert events[0].name == "photarium_upload_from_path"
    assert router.last_arguments is not None
    assert "name" not in router.last_arguments
    assert router.last_arguments["tags"] == [
        "existing-tag",
        "tank tracks",
        "caterpillar tracks",
        "tracks",
    ]


@pytest.mark.asyncio
async def test_orchestrator_caps_tool_window_at_api_limit():
    llm = ToolWindowCaptureLLM()
    router = RecordingRouter(result_payload={"ok": True})
    orch = ChatOrchestrator("system", llm, router)

    tools = []
    for index in range(150):
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": f"photarium_tool_{index:03d}",
                    "description": "photarium test tool",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        )
    tools.append(
        {
            "type": "function",
            "function": {
                "name": "workflows_import_from_artifact",
                "description": "Import workflow from artifact",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    )
    tools.append(
        {
            "type": "function",
            "function": {
                "name": "workflows_extract_from_artifact",
                "description": "Extract workflow from artifact",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    )
    for index in range(14):
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": f"editorial_tool_{index:03d}",
                    "description": "editorial test tool",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        )

    orch.set_tools(tools)
    answer, events = await orch.process("extract this workflow and add it to the workflow corpus")

    assert answer == "done"
    assert events == []
    assert llm.seen_tool_names is not None
    assert len(llm.seen_tool_names) == 128
    assert "workflows_import_from_artifact" in llm.seen_tool_names
    assert "workflows_extract_from_artifact" in llm.seen_tool_names


@pytest.mark.asyncio
async def test_orchestrator_retrieves_tool_by_description_and_params_under_cap():
    llm = ToolWindowCaptureLLM()
    router = RecordingRouter(result_payload={"ok": True})
    orch = ChatOrchestrator("system", llm, router)

    tools = []
    for index in range(200):
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": f"photarium_tool_{index:03d}",
                    "description": "Generic catalog utility",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        )
    tools.append(
        {
            "type": "function",
            "function": {
                "name": "misc_resume_lookup",
                "description": "Resolve lineage entries using resume token",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "resume_token": {"type": "string"},
                        "lineage_run_id": {"type": "string"},
                    },
                },
            },
        }
    )

    orch.set_tools(tools)
    answer, events = await orch.process("find resume token for this lineage run")

    assert answer == "done"
    assert events == []
    assert llm.seen_tool_names is not None
    assert len(llm.seen_tool_names) == 128
    assert "misc_resume_lookup" in llm.seen_tool_names


@pytest.mark.asyncio
async def test_orchestrator_uses_recent_context_for_tool_window_selection():
    llm = ToolWindowCaptureLLM()
    router = RecordingRouter(result_payload={"ok": True})
    orch = ChatOrchestrator("system", llm, router)

    tools = []
    for index in range(180):
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": f"photarium_tool_{index:03d}",
                    "description": "photarium test tool",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        )
    tools.extend(
        [
            {
                "type": "function",
                "function": {
                    "name": "digester_digests_get",
                    "description": "Get digest by id",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "digester_get_signal",
                    "description": "Get signal text",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "digester_signals_create",
                    "description": "Create signal",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]
    )
    orch.set_tools(tools)
    orch._messages.append(
        {
            "role": "assistant",
            "content": (
                "I will create one signal from the digest item using digester_signals_create."
            ),
        }
    )

    answer, events = await orch.process("1 and use the digest title")

    assert answer == "done"
    assert events == []
    assert llm.seen_tool_names is not None
    assert "digester_signals_create" in llm.seen_tool_names
