"""Tests for configuration loading."""

from comfy_mcp.config.load_config import load_config


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
