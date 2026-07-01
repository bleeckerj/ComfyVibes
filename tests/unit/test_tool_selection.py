from __future__ import annotations

import pytest

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


def test_selector_keeps_editorial_content_tools_for_plain_article_requests() -> None:
    tools = [_tool(f"backoffice_tool_{index:03d}") for index in range(220)]
    tools.append(_tool("digester_generate_draft", description="Generate a detached draft"))
    tools.append(_tool("editorial_content_draft_from_brief", description="Draft MDX for nfl-editorial content"))
    tools.append(_tool("editorial_content_create_stub", description="Create content stub"))
    tools.append(_tool("editorial_content_apply_edits", description="Apply edits to content file"))

    selector = ToolSelector(tools)
    selected = selector.select_tools_for_user_text(
        user_text="Write an article draft from this brief and create the MDX file",
        recent_tool_names=set(),
        max_tools_per_request=64,
    )
    selected_names = {str(item.get("function", {}).get("name", "")) for item in selected}
    assert "editorial_content_draft_from_brief" in selected_names
    assert "editorial_content_create_stub" in selected_names
    assert "editorial_content_apply_edits" in selected_names


def test_selector_keeps_stdio_newsletter_tools_for_newsletter_issue_requests() -> None:
    tools = [_tool(f"workflows_tool_{index:03d}") for index in range(220)]
    tools.extend(
        [
            _tool("workspace_file_write", description="Write a workspace file"),
            _tool("newsletter_get", description="Load newsletter draft"),
            _tool("newsletter_get_section_schema", description="Get newsletter section schema"),
            _tool("newsletter_add_item", description="Add newsletter item"),
            _tool("get_section_types", description="List newsletter section types"),
        ]
    )

    selector = ToolSelector(tools)
    selected = selector.select_tools_for_user_text(
        user_text="Add a food-for-thought item to newsletter issue w19-y26 after checking the section schema",
        recent_tool_names=set(),
        max_tools_per_request=64,
    )
    selected_names = {str(item.get("function", {}).get("name", "")) for item in selected}
    assert "newsletter_get" in selected_names
    assert "newsletter_get_section_schema" in selected_names
    assert "newsletter_add_item" in selected_names
    assert "get_section_types" in selected_names


def test_selector_supports_backoffice_newsletter_http_names_too() -> None:
    tools = [_tool(f"photarium_tool_{index:03d}") for index in range(180)]
    tools.extend(
        [
            _tool("backoffice_newsletter_get", description="Load newsletter draft"),
            _tool("backoffice_newsletter_add_signal", description="Add signal to newsletter"),
            _tool("backoffice_newsletter_validate", description="Validate newsletter"),
        ]
    )

    selector = ToolSelector(tools)
    selected = selector.select_tools_for_user_text(
        user_text="Add signal to newsletter issue mw15-y26 and validate it",
        recent_tool_names=set(),
        max_tools_per_request=64,
    )
    selected_names = {str(item.get("function", {}).get("name", "")) for item in selected}
    assert "backoffice_newsletter_get" in selected_names
    assert "backoffice_newsletter_add_signal" in selected_names
    assert "backoffice_newsletter_validate" in selected_names


def test_domain_classifier_covers_core_domains() -> None:
    cases = [
        ("Add a food-for-thought item to newsletter issue mw15-y26", "newsletter"),
        ("Show the latest digests", "digests"),
        ("Create a signal from this digest item", "signals"),
        ("Run the ComfyUI workflow with aspect ratio targets", "workflows"),
        ("Upload this Photarium variant into the catalog namespace", "photarium"),
        ("Draft a feature for issue 2 in src/content", "editorial_content"),
        ("Preview ad inventory for this campaign", "editorial_ads"),
        ("Search the backoffice content source pack", "backoffice_content"),
        ("Read this workspace file path", "workspace"),
    ]

    for text, expected_domain in cases:
        matches = ToolSelector.classify_domains(text=text.lower(), recent_tool_names=set())
        domain_names = {match.domain.name for match in matches}
        assert expected_domain in domain_names


def test_domain_classifier_uses_recent_tool_handoff() -> None:
    matches = ToolSelector.classify_domains(
        text="add it to the same section",
        recent_tool_names={"newsletter_get"},
    )

    domain_names = {match.domain.name for match in matches}
    assert "newsletter" in domain_names


@pytest.mark.parametrize(
    ("prompt", "expected_tools"),
    [
        (
            "Run the ComfyUI workflow after checking params",
            {"workflows_params_get", "workflows_run"},
        ),
        (
            "Upload a Photarium variant and inspect its catalog metadata",
            {"photarium_upload_from_path", "photarium_get"},
        ),
        (
            "Draft a feature for issue 2 from this brief",
            {"editorial_content_draft_from_brief", "editorial_content_create_stub"},
        ),
        (
            "Show the latest digests and then get the selected digest",
            {"digester_list_recent_digests", "digester_digests_get"},
        ),
        (
            "List recent signals and open one signal",
            {"digester_signals_list_summaries", "digester_get_signal"},
        ),
    ],
)
def test_domain_selector_keeps_critical_tools_in_large_surface(prompt: str, expected_tools: set[str]) -> None:
    tools = [_tool(f"misc_tool_{index:03d}", description="Generic utility") for index in range(220)]
    tools.extend(
        [
            _tool("workflows_params_get", description="Get workflow parameters"),
            _tool("workflows_run", description="Run workflow"),
            _tool("photarium_upload_from_path", description="Upload local image"),
            _tool("photarium_get", description="Get catalog image metadata"),
            _tool("editorial_content_draft_from_brief", description="Draft MDX content"),
            _tool("editorial_content_create_stub", description="Create MDX content stub"),
            _tool("digester_list_recent_digests", description="List recent digests"),
            _tool("digester_digests_get", description="Get digest"),
            _tool("digester_signals_list_summaries", description="List signal summaries"),
            _tool("digester_get_signal", description="Get signal"),
            _tool("list_tools", description="List available tools"),
            _tool("tool_schema_get", description="Get tool schema"),
        ]
    )

    selector = ToolSelector(tools)
    selected = selector.select_tools_for_user_text(
        user_text=prompt,
        recent_tool_names=set(),
        max_tools_per_request=64,
    )

    selected_names = {str(item.get("function", {}).get("name", "")) for item in selected}
    assert expected_tools.issubset(selected_names)
    assert selector.last_selection_debug["active_domains"]
    assert not selector.last_selection_debug["omitted_critical_tools"]


@pytest.mark.parametrize(
    ("prompt", "domain_tool"),
    [
        (
            "Add a food-for-thought item to newsletter issue w19-y26",
            "newsletter_add_item",
        ),
        (
            "Draft a feature for issue 2 and create the MDX file",
            "editorial_content_create_stub",
        ),
    ],
)
def test_selector_suppresses_generic_file_writes_for_protected_domains(prompt: str, domain_tool: str) -> None:
    tools = [_tool(f"misc_tool_{index:03d}", description="Generic utility") for index in range(220)]
    tools.extend(
        [
            _tool("workspace_file_write", description="Write a workspace file"),
            _tool("workspace_file_copy", description="Copy a workspace file"),
            _tool("newsletter_get", description="Load newsletter draft"),
            _tool("newsletter_add_item", description="Add newsletter item"),
            _tool("editorial_content_create_stub", description="Create MDX content stub"),
            _tool("editorial_content_draft_from_brief", description="Draft MDX content"),
        ]
    )

    selector = ToolSelector(tools)
    selected = selector.select_tools_for_user_text(
        user_text=prompt,
        recent_tool_names=set(),
        max_tools_per_request=64,
    )

    selected_names = {str(item.get("function", {}).get("name", "")) for item in selected}
    assert domain_tool in selected_names
    assert "workspace_file_write" not in selected_names
    assert "workspace_file_write" in selector.last_selection_debug["suppressed_tools"]
