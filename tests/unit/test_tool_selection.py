from __future__ import annotations

from comfy_mcp.tui_client.tool_selection import ToolSelector


def _tool(name: str, description: str = "", params: list[str] | None = None) -> dict:
    properties = {
        param: {"type": "string"}
        for param in (params or [])
    }
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties},
        },
    }


def test_tool_priority_boosts_digest_listing_for_latest_digests_request() -> None:
    text = "list the latest digests"
    digest_score = ToolSelector.tool_priority(
        name="digester_list_recent_digests",
        text=text,
        recent_tool_names=set(),
    )
    workflow_score = ToolSelector.tool_priority(
        name="workflows_list",
        text=text,
        recent_tool_names=set(),
    )
    assert digest_score > workflow_score


def test_selector_keeps_digest_tools_when_toolset_is_large() -> None:
    tools = [_tool(f"workflows_tool_{index:03d}") for index in range(180)]
    tools.append(_tool("digester_list_recent_digests"))
    tools.append(_tool("digester_search_digests"))
    tools.append(_tool("digester_get_story_queue"))

    selector = ToolSelector(tools)
    selected = selector.select_tools_for_user_text(
        user_text="show me the latest digests",
        recent_tool_names=set(),
        max_tools_per_request=128,
    )
    selected_names = {
        str(item.get("function", {}).get("name", ""))
        for item in selected
    }
    assert "digester_list_recent_digests" in selected_names


def test_selector_uses_context_to_keep_signal_create_for_short_follow_up() -> None:
    tools = [_tool(f"photarium_tool_{index:03d}") for index in range(180)]
    tools.append(_tool("digester_digests_get"))
    tools.append(_tool("digester_get_signal"))
    tools.append(_tool("digester_signals_create"))
    tools.append(_tool("digester_create_ad_hoc_signal"))

    selector = ToolSelector(tools)
    selected = selector.select_tools_for_user_text(
        user_text="1 and use the digest title",
        context_text=(
            "I will create a new signal from this digest item using digester_signals_create."
        ),
        recent_tool_names={"digester_digests_get", "digester_get_signal"},
        max_tools_per_request=128,
    )
    selected_names = {
        str(item.get("function", {}).get("name", ""))
        for item in selected
    }
    assert "digester_signals_create" in selected_names


def test_selector_retrieves_by_description_tokens_in_large_toolset() -> None:
    tools = [
        _tool(f"photarium_tool_{index:03d}", description="Generic catalog utility")
        for index in range(190)
    ]
    tools.append(
        _tool(
            "misc_lookup_resolver",
            description="Resolve sitemap slug into canonical story identifier",
        )
    )

    selector = ToolSelector(tools)
    selected = selector.select_tools_for_user_text(
        user_text="resolve sitemap slug",
        recent_tool_names=set(),
        max_tools_per_request=64,
    )
    selected_names = {
        str(item.get("function", {}).get("name", ""))
        for item in selected
    }
    assert "misc_lookup_resolver" in selected_names


def test_selector_retrieves_by_parameter_name_tokens_in_large_toolset() -> None:
    tools = [_tool(f"workflows_tool_{index:03d}") for index in range(190)]
    tools.append(
        _tool(
            "misc_resume_lookup",
            description="Resume helper",
            params=["resume_token", "lineage_run_id"],
        )
    )

    selector = ToolSelector(tools)
    selected = selector.select_tools_for_user_text(
        user_text="resume token for prior run",
        recent_tool_names=set(),
        max_tools_per_request=64,
    )
    selected_names = {
        str(item.get("function", {}).get("name", ""))
        for item in selected
    }
    assert "misc_resume_lookup" in selected_names


def test_selector_backfills_when_index_recall_is_low() -> None:
    tools = [_tool(f"workflows_tool_{index:03d}") for index in range(220)]
    tools.extend(_tool(f"photarium_tool_{index:03d}") for index in range(30))

    selector = ToolSelector(tools)
    selected = selector.select_tools_for_user_text(
        user_text="the and if",
        recent_tool_names=set(),
        max_tools_per_request=50,
    )
    selected_names = [
        str(item.get("function", {}).get("name", ""))
        for item in selected
    ]
    assert len(selected_names) == 50
    assert "workflows_tool_000" in selected_names


def test_selector_pins_digest_signal_companion_tools_when_intent_detected() -> None:
    tools = [_tool(f"photarium_tool_{index:03d}", description="Generic utility") for index in range(240)]
    tools.extend(
        [
            _tool("digester_update_status"),
            _tool("digester_signals_create"),
            _tool("digester_create_ad_hoc_signal"),
            _tool("digester_digests_list"),
        ]
    )

    selector = ToolSelector(tools)
    selected = selector.select_tools_for_user_text(
        user_text="use the digest title to create a signal and mark it accepted",
        recent_tool_names={"digester_get_signal"},
        max_tools_per_request=64,
    )
    selected_names = {
        str(item.get("function", {}).get("name", ""))
        for item in selected
    }
    assert "digester_update_status" in selected_names
    assert "digester_signals_create" in selected_names
    assert "digester_create_ad_hoc_signal" in selected_names
    assert "digester_digests_list" in selected_names


def test_digest_followup_keeps_digest_read_tools_visible() -> None:
    tools = [_tool(f"workflows_tool_{index:03d}") for index in range(180)]
    tools.append(_tool("digester_digests_list"))
    tools.append(_tool("digester_digests_get"))
    tools.append(_tool("digester_list_recent_digests"))

    selector = ToolSelector(tools)
    selected = selector.select_tools_for_user_text(
        user_text="do you have a summary for it?",
        recent_tool_names={"digester_digests_list"},
        max_tools_per_request=128,
    )
    selected_names = {
        str(item.get("function", {}).get("name", ""))
        for item in selected
    }
    assert "digester_digests_get" in selected_names


def test_selector_prefers_editorial_content_drafting_tools_for_feature_issue_requests() -> None:
    tools = [_tool(f"workflows_tool_{index:03d}") for index in range(220)]
    tools.append(_tool("editorial_draft_generate", description="Generate a draft via drafter API"))
    tools.append(_tool("editorial_content_draft_from_brief", description="Draft MDX for nfl-editorial content"))
    tools.append(_tool("editorial_content_create_stub", description="Create content stub"))
    tools.append(_tool("editorial_content_apply_edits", description="Apply edits to content file"))

    selector = ToolSelector(tools)
    selected = selector.select_tools_for_user_text(
        user_text="Draft a feature for issue 2 based on these signals",
        recent_tool_names=set(),
        max_tools_per_request=64,
    )
    selected_names = {str(item.get("function", {}).get("name", "")) for item in selected}
    assert "editorial_content_draft_from_brief" in selected_names
    assert "editorial_content_create_stub" in selected_names
    assert "editorial_content_apply_edits" in selected_names
