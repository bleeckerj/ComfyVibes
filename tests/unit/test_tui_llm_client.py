from __future__ import annotations

from dataclasses import dataclass

from comfy_mcp.tui_client import llm_client
from comfy_mcp.tui_client.config import LLMConfig


@dataclass
class FakeFunction:
    name: str
    arguments: str


@dataclass
class FakeToolCall:
    id: str
    function: FakeFunction


def test_parse_tool_calls_from_dict():
    message = {
        "tool_calls": [
            {
                "id": "call_1",
                "function": {"name": "workflows.list", "arguments": "{\"limit\": 3}"},
            }
        ]
    }
    calls = llm_client._parse_tool_calls(message)
    assert len(calls) == 1
    assert calls[0].call_id == "call_1"
    assert calls[0].name == "workflows.list"
    assert calls[0].arguments == {"limit": 3}


def test_parse_tool_calls_from_objects():
    message = type("Msg", (), {})()
    message.tool_calls = [
        FakeToolCall(id="call_2", function=FakeFunction(name="comfy.nodes.list", arguments="{}"))
    ]
    calls = llm_client._parse_tool_calls(message)
    assert len(calls) == 1
    assert calls[0].call_id == "call_2"
    assert calls[0].name == "comfy.nodes.list"
    assert calls[0].arguments == {}


def test_parse_tool_calls_legacy_function_call():
    message = {"function_call": {"name": "workflows.get", "arguments": "{\"workflow_id\": \"abc\"}"}}
    calls = llm_client._parse_tool_calls(message)
    assert len(calls) == 1
    assert calls[0].name == "workflows.get"
    assert calls[0].arguments == {"workflow_id": "abc"}


def test_api_key_prefers_config_over_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    cfg = LLMConfig(
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        api_key="config-key",
        model="gpt-4o-mini",
    )
    assert llm_client._get_api_key(cfg) == "config-key"
