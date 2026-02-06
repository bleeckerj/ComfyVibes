"""Tests for ComfyUI model and embedding endpoints."""

import httpx
import pytest

from comfy_mcp.comfy_client.client import ComfyClient


@pytest.mark.asyncio
async def test_get_model_types_calls_expected_path() -> None:
    """ComfyClient should call `/models` and return JSON payload."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/models"
        return httpx.Response(200, json=["checkpoints", "vae"])

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://example.com", transport=transport) as client:
        comfy = ComfyClient(base_url="http://example.com", http_client=client)
        payload = await comfy.get_model_types()
        assert payload == ["checkpoints", "vae"]


@pytest.mark.asyncio
async def test_get_models_in_folder_calls_expected_path() -> None:
    """ComfyClient should call `/models/{folder}` and return JSON payload."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/models/checkpoints"
        return httpx.Response(200, json=["model.safetensors"])

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://example.com", transport=transport) as client:
        comfy = ComfyClient(base_url="http://example.com", http_client=client)
        payload = await comfy.get_models_in_folder("checkpoints")
        assert payload == ["model.safetensors"]


@pytest.mark.asyncio
async def test_get_embeddings_calls_expected_path() -> None:
    """ComfyClient should call `/embeddings` and return JSON payload."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/embeddings"
        return httpx.Response(200, json=["embeddingA", "embeddingB"])

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://example.com", transport=transport) as client:
        comfy = ComfyClient(base_url="http://example.com", http_client=client)
        payload = await comfy.get_embeddings()
        assert payload == ["embeddingA", "embeddingB"]
