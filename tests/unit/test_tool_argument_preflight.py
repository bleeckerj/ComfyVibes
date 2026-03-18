from __future__ import annotations

import pytest

from comfy_mcp.tui_client.tool_argument_preflight import ToolArgumentPreflight


class _NoopWorkflowRepairs:
    async def prepare_workflows_run_arguments(self, orchestrator, tool_name, arguments, *, user_text):  # noqa: ANN001
        return arguments

    def repair_workflows_import_from_artifact_arguments(self, tool_name, arguments, *, user_text):  # noqa: ANN001
        return arguments

    async def preflight_workflows_run_arguments(self, orchestrator, tool_name, arguments, *, user_text):  # noqa: ANN001
        return None

    def preflight_workflows_import_from_artifact_arguments(self, tool_name, arguments):  # noqa: ANN001
        return None


class _NoopBinaryTransfer:
    async def maybe_convert_upload_url_to_from_path(self, orchestrator, tool_name, arguments, *, user_text):  # noqa: ANN001
        return None

    def apply_tanktracks_upload_conventions(self, tool_name, arguments, *, user_text):  # noqa: ANN001
        return arguments


@pytest.mark.asyncio
async def test_preflight_blocks_digest_like_signal_lookup_id() -> None:
    preflight = ToolArgumentPreflight(_NoopWorkflowRepairs(), _NoopBinaryTransfer())
    with pytest.raises(RuntimeError, match="appears to be a digest id/path"):
        await preflight.prepare_tool_execution(
            orchestrator=object(),
            tool_name="digester_get_signal",
            arguments={"signal_id": "03082026_105127_digest-this"},
            user_text="open this signal",
        )


@pytest.mark.asyncio
async def test_preflight_allows_normal_signal_lookup_id() -> None:
    preflight = ToolArgumentPreflight(_NoopWorkflowRepairs(), _NoopBinaryTransfer())
    exec_name, exec_args, temp_file = await preflight.prepare_tool_execution(
        orchestrator=object(),
        tool_name="digester_get_signal",
        arguments={"signal_id": "1bbb21158471"},
        user_text="open this signal",
    )
    assert exec_name == "digester_get_signal"
    assert exec_args == {"signal_id": "1bbb21158471"}
    assert temp_file is None


@pytest.mark.asyncio
async def test_preflight_recent_signal_list_coerces_broad_defaults() -> None:
    preflight = ToolArgumentPreflight(_NoopWorkflowRepairs(), _NoopBinaryTransfer())
    exec_name, exec_args, temp_file = await preflight.prepare_tool_execution(
        orchestrator=object(),
        tool_name="digester_signals_list_summaries",
        arguments={"days": 30},
        user_text="give me the last 5 signsl with summary",
    )
    assert exec_name == "digester_signals_list_summaries"
    assert exec_args["limit"] == 5
    assert exec_args["status"] == "all"
    assert exec_args["sort_by"] == "date"
    assert exec_args["min_potential"] == 0
    assert exec_args["days"] is None
    assert temp_file is None


@pytest.mark.asyncio
async def test_preflight_recent_signal_list_keeps_explicit_window() -> None:
    preflight = ToolArgumentPreflight(_NoopWorkflowRepairs(), _NoopBinaryTransfer())
    exec_name, exec_args, temp_file = await preflight.prepare_tool_execution(
        orchestrator=object(),
        tool_name="digester_signals_list_summaries",
        arguments={"days": 7},
        user_text="show latest 3 signals from the last 7 days",
    )
    assert exec_name == "digester_signals_list_summaries"
    assert exec_args["limit"] == 3
    assert exec_args["status"] == "all"
    assert exec_args["sort_by"] == "date"
    assert exec_args["days"] == 7
    assert temp_file is None


@pytest.mark.asyncio
async def test_preflight_blocks_signal_create_for_retrieval_queries() -> None:
    preflight = ToolArgumentPreflight(_NoopWorkflowRepairs(), _NoopBinaryTransfer())
    with pytest.raises(RuntimeError, match="Signal-create preflight blocked"):
        await preflight.prepare_tool_execution(
            orchestrator=object(),
            tool_name="digester_signals_create",
            arguments={"note_text": "should not run"},
            user_text="give me the last 3 signals",
        )
