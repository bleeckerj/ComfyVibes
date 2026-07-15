"""Live integration tests for ComfyClient against a running ComfyUI server."""

import os

import pytest

from comfy_mcp.comfy_client.client import ComfyClient


def _get_base_url() -> str | None:
    return os.getenv("COMFY_MCP_TEST_URL") or os.getenv("COMFY_MCP_COMFY_BASE_URL")


def _require_live_tests() -> None:
    if os.getenv("COMFY_MCP_LIVE_TESTS") != "1":
        pytest.skip("Set COMFY_MCP_LIVE_TESTS=1 for read-only remote ComfyUI smoke tests.")


@pytest.mark.asyncio
async def test_live_object_info() -> None:
    """Live test: `/object_info` should return a dict with node info."""
    _require_live_tests()
    base_url = _get_base_url()
    if not base_url:
        pytest.skip("Set COMFY_MCP_TEST_URL to run live ComfyUI tests.")

    async with ComfyClient(base_url=base_url) as client:
        payload = await client.get_object_info()

    assert isinstance(payload, dict)
    assert payload, "Expected non-empty object_info payload"


@pytest.mark.asyncio
async def test_live_queue() -> None:
    """Live test: `/queue` should return a dict payload."""
    _require_live_tests()
    base_url = _get_base_url()
    if not base_url:
        pytest.skip("Set COMFY_MCP_TEST_URL to run live ComfyUI tests.")

    async with ComfyClient(base_url=base_url) as client:
        payload = await client.get_queue()

    assert isinstance(payload, dict)


@pytest.mark.asyncio
async def test_live_history() -> None:
    """Live test: `/history` should return a dict payload."""
    _require_live_tests()
    base_url = _get_base_url()
    if not base_url:
        pytest.skip("Set COMFY_MCP_TEST_URL to run live ComfyUI tests.")

    async with ComfyClient(base_url=base_url) as client:
        payload = await client.get_history()

    assert isinstance(payload, dict)
