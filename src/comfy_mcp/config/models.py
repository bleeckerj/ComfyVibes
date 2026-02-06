"""Configuration models for ComfyMCP."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import AnyUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    """Application configuration loaded from env, file, or overrides."""

    model_config = SettingsConfigDict(
        env_prefix="COMFY_MCP_",
        env_file=".env",
        extra="ignore",
    )

    comfy_base_url: AnyUrl = Field(default="http://127.0.0.1:8188")
    workflow_library_root: Path = Field(default=Path("~/.comfy-mcp/workflows"))
    api_token: Optional[str] = Field(default=None)
    readonly_mode: bool = Field(default=False)
    max_workflow_bytes: int = Field(default=5_000_000)
    bind_host: str = Field(default="127.0.0.1")
    bind_port: int = Field(default=7337)

    def resolved_workflow_root(self) -> Path:
        """Return expanded path for workflow library root."""
        return self.workflow_library_root.expanduser().resolve()
