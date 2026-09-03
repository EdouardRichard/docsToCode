"""Unit tests for the remote API reranker provider (T041, RED first).

FR-015: OpenAI-compatible /rerank adapter (Jina/Cohere-compatible shape).
Asserts the request shape, score normalization, and failure degradation
(passthrough of candidates) without raising into the state machine.
"""

from __future__ import annotations

import json

import httpx
import pytest

from rag_mcp.providers.remote_api_reranker import RemoteAPIRerankerProvider


def _provider(handler):
    transport = httpx.MockTransport(handler)
    return RemoteAPIRerankerProvider(
        endpoint="http://api.test/v1",
        model="test-reranker",
        api_key_env=None,
        timeout_s=2.0,
        transport=transport,
    )


def test_import_remote_reranker() -> None:
    assert RemoteAPIRerankerProvider is not None


@pytest.mark.asyncio
async def test_rerank_call_shape_and_normalization() -> None:
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={"results": [
            {"index": 1, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.2},
        ]})

    provider = _provider(handler)
    candidates = [
        {"text": "a", "id": 1},
        {"text": "b", "id": 2},
    ]
    result = await provider.rerank("query", candidates, top_k=2)
    assert captured["url"] == "http://api.test/v1/rerank"
    assert captured["json"]["model"] == "test-reranker"
    assert captured["json"]["query"] == "query"
    assert captured["json"]["documents"] == ["a", "b"]
    # highest score first (index 1 then 0), scores normalized to [0, 1]
    assert result[0]["id"] == 2
    assert result[1]["id"] == 1
    for item in result:
        assert 0.0 <= item["rerank_score"] <= 1.0


@pytest.mark.asyncio
async def test_failure_passthrough() -> None:
    def handler(request):
        return httpx.Response(500, text="down")

    provider = _provider(handler)
    candidates = [{"text": "a", "id": 1}, {"text": "b", "id": 2}]
    result = await provider.rerank("query", candidates, top_k=2)
    # Degradation: candidates returned unchanged (never raises into the state machine)
    assert result == candidates
    assert provider.last_error is not None


@pytest.mark.asyncio
async def test_malformed_body_passthrough() -> None:
    def handler(request):
        return httpx.Response(200, text="garbage")

    provider = _provider(handler)
    candidates = [{"text": "a"}]
    result = await provider.rerank("q", candidates, top_k=1)
    assert result == candidates
