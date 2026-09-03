"""Unit tests for the remote API embedding provider (T039, RED first).

FR-015: OpenAI-compatible /embeddings adapter. Asserts the request shape,
dimension return, and that HTTP errors / timeouts / malformed bodies
degrade explicitly (return empty vectors) rather than raising into the
retrieval state machine.
"""

from __future__ import annotations

import json

import httpx
import pytest

from rag_mcp.providers.remote_api_embedding import RemoteAPIEmbeddingProvider


def _provider(handler, timeout_s=2.0):
    transport = httpx.MockTransport(handler)
    return RemoteAPIEmbeddingProvider(
        endpoint="http://api.test/v1",
        model="test-embedder",
        api_key_env=None,
        timeout_s=timeout_s,
        transport=transport,
    )


def test_import_remote_embedding() -> None:
    assert RemoteAPIEmbeddingProvider is not None


@pytest.mark.asyncio
async def test_embeddings_call_shape_and_dimension() -> None:
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3], "index": 0}]})

    provider = _provider(handler)
    vectors = await provider.embed_texts(["hello world"])
    assert vectors == [[0.1, 0.2, 0.3]]
    assert captured["url"] == "http://api.test/v1/embeddings"
    assert captured["json"]["model"] == "test-embedder"
    assert captured["json"]["input"] == ["hello world"]
    assert provider.get_dimension() == 3


@pytest.mark.asyncio
async def test_http_error_degrades() -> None:
    def handler(request):
        return httpx.Response(500, text="boom")

    provider = _provider(handler)
    vectors = await provider.embed_texts(["x"])
    assert vectors == []
    assert provider.last_error is not None


@pytest.mark.asyncio
async def test_timeout_degrades() -> None:
    def handler(request):
        raise httpx.TimeoutException("timed out")

    provider = _provider(handler)
    vectors = await provider.embed_query("x")
    assert vectors == []
    assert provider.last_error is not None


@pytest.mark.asyncio
async def test_malformed_body_degrades() -> None:
    def handler(request):
        return httpx.Response(200, text="not-json")

    provider = _provider(handler)
    vectors = await provider.embed_texts(["x"])
    assert vectors == []
    assert provider.last_error is not None


@pytest.mark.asyncio
async def test_embed_query_returns_single_vector() -> None:
    def handler(request):
        return httpx.Response(200, json={"data": [{"embedding": [1.0, 2.0], "index": 0}]})

    provider = _provider(handler)
    vec = await provider.embed_query("query")
    assert vec == [1.0, 2.0]
