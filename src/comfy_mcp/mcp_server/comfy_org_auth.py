"""Comfy Org hidden auth loading for ComfyUI prompt submissions."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from pydantic import SecretStr


def build_comfy_org_extra_data(
    *,
    auth_token: SecretStr | str | None = None,
    auth_token_file: Path | None = None,
    api_key: SecretStr | str | None = None,
    api_key_file: Path | None = None,
) -> dict[str, str]:
    """Return ComfyUI extra_data keys for Comfy Org hidden auth inputs."""
    extra_data: dict[str, str] = {}
    token_value = _secret_or_file_value(auth_token, auth_token_file)
    if token_value:
        extra_data["auth_token_comfy_org"] = token_value
    api_key_value = _secret_or_file_value(api_key, api_key_file)
    if api_key_value:
        extra_data["api_key_comfy_org"] = api_key_value
    return extra_data


def merge_comfy_org_extra_data(
    base: Mapping[str, object] | None,
    comfy_org_extra_data: Mapping[str, str],
) -> dict[str, object] | None:
    """Merge configured Comfy Org auth into an existing extra_data payload."""
    if not base and not comfy_org_extra_data:
        return None
    merged: dict[str, object] = dict(base or {})
    merged.update(comfy_org_extra_data)
    return merged


def _secret_or_file_value(secret: SecretStr | str | None, file_path: Path | None) -> str | None:
    value = _secret_value(secret)
    if value:
        return value
    if file_path is None:
        return None
    path = file_path.expanduser()
    if not path.exists():
        raise ValueError(f"Comfy Org auth secret file does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"Comfy Org auth secret path is not a file: {path}")
    file_value = path.read_text(encoding="utf-8").strip()
    return file_value or None


def _secret_value(secret: SecretStr | str | None) -> str | None:
    if secret is None:
        return None
    if isinstance(secret, SecretStr):
        value = secret.get_secret_value()
    else:
        value = str(secret)
    value = value.strip()
    return value or None
