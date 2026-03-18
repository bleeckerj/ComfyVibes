from __future__ import annotations

from pathlib import Path

import pytest

from comfy_mcp.workspace_server.fs_tools import WorkspaceFsTools, WorkspacePolicy


def test_workspace_policy_denies_paths_outside_roots(tmp_path: Path) -> None:
    policy = WorkspacePolicy(roots=[tmp_path])
    tools = WorkspaceFsTools(policy)
    with pytest.raises(RuntimeError, match="Path not allowed"):
        tools.workspace_path_stat("/Users")


def test_workspace_file_write_dry_run_does_not_create_file(tmp_path: Path) -> None:
    policy = WorkspacePolicy(roots=[tmp_path])
    tools = WorkspaceFsTools(policy)
    target = tmp_path / "hello.txt"
    result = tools.workspace_file_write(str(target), content="hi", dry_run=True)
    assert result["dry_run"] is True
    assert not target.exists()


def test_workspace_file_copy_respects_overwrite(tmp_path: Path) -> None:
    policy = WorkspacePolicy(roots=[tmp_path])
    tools = WorkspaceFsTools(policy)
    src = tmp_path / "a.txt"
    dst = tmp_path / "b.txt"
    src.write_text("a", encoding="utf-8")
    dst.write_text("b", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Destination exists"):
        tools.workspace_file_copy(str(src), str(dst))
    tools.workspace_file_copy(str(src), str(dst), overwrite=True)
    assert dst.read_text(encoding="utf-8") == "a"

