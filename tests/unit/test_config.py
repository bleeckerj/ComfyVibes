"""Tests for configuration loading."""

from comfy_mcp.config.load_config import load_config
from comfy_mcp.mcp_server.comfy_org_auth import build_comfy_org_extra_data


def test_load_config_overrides_values() -> None:
    """Load config should apply explicit overrides."""
    config = load_config({"bind_port": 9000, "readonly_mode": True})
    assert config.bind_port == 9000
    assert config.readonly_mode is True


def test_resolved_workflow_root_expands_user() -> None:
    """Workflow root should expand a user home path."""
    config = load_config({"workflow_library_root": "~/tmp/comfy-mcp"})
    resolved = config.resolved_workflow_root()
    assert resolved.name == "comfy-mcp"


def test_resolved_run_workflow_root_expands_user() -> None:
    config = load_config({"run_workflow_root": "~/tmp/comfy-run-workflows"})
    resolved = config.resolved_run_workflow_root()
    assert resolved.name == "comfy-run-workflows"


def test_load_config_defaults_extra_roots_off() -> None:
    """Extra workflow roots should be opt-in to avoid list ambiguity."""
    config = load_config({"workflow_library_root": "~/tmp/comfy-mcp"})
    assert config.include_extra_workflow_roots is False


def test_comfy_org_auth_extra_data_prefers_env_secret_over_file(tmp_path) -> None:
    token_file = tmp_path / "comfy-org-token"
    api_key_file = tmp_path / "comfy-org-api-key"
    token_file.write_text("file-token\n", encoding="utf-8")
    api_key_file.write_text("file-api-key\n", encoding="utf-8")

    config = load_config(
        {
            "workflow_library_root": "~/tmp/comfy-mcp",
            "comfy_org_auth_token": "env-token",
            "comfy_org_auth_token_file": token_file,
            "comfy_org_api_key_file": api_key_file,
        }
    )

    assert build_comfy_org_extra_data(
        auth_token=config.comfy_org_auth_token,
        auth_token_file=config.comfy_org_auth_token_file,
        api_key=config.comfy_org_api_key,
        api_key_file=config.comfy_org_api_key_file,
    ) == {
        "auth_token_comfy_org": "env-token",
        "api_key_comfy_org": "file-api-key",
    }
