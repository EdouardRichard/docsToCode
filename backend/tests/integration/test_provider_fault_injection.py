"""Integration test: remote Provider fault injection (T051/T052).

SC-012/FR-015: connection failures, timeouts and HTTP errors on remote
embedding / reranker / llm providers degrade to a valid four-state
completion_status — the Provider layer adds zero hard failures and the
retrieval state machine is never blocked.
"""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class StubQdrant:
    pass


@pytest.mark.asyncio
async def test_remote_embedding_fault_degrades_to_four_state(session_factory):
    """Connection failure on remote embedding -> valid four-state, no raise."""
    from rag_mcp.providers.remote_api_embedding import RemoteAPIEmbeddingProvider
    from rag_mcp.mcp.search_knowledge import search_knowledge_core
    from rag_mcp.indexing.qdrant_client import QdrantStore
    from sqlalchemy import select, text

    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(handler)
    provider = RemoteAPIEmbeddingProvider(
        endpoint="http://down.test/v1", model="m", transport=transport
    )

    # discover a scope (any) so scope validation passes before retrieval
    async with session_factory() as session:
        row = (
            await session.execute(
                text("SELECT knowledge_scope_id FROM chunks LIMIT 1")
            )
        ).first()
    if row is None:
        pytest.skip("no chunks in shared DB")

    result = await search_knowledge_core(
        query="fault injection query",
        project_scope=[str(row[0])],
        top_k=5,
        task_context=None,
        session_factory=session_factory,
        qdrant_store=QdrantStore(),
        embedding_provider=provider,
        reranker=None,
    )
    assert result["completion_status"] in ("complete", "partial", "no_evidence", "failed")
    assert provider.last_error is not None


@pytest.mark.asyncio
async def test_remote_reranker_fault_passthrough():
    from rag_mcp.providers.remote_api_reranker import RemoteAPIRerankerProvider

    def handler(request):
        raise httpx.TimeoutException("timed out")

    transport = httpx.MockTransport(handler)
    provider = RemoteAPIRerankerProvider(
        endpoint="http://down.test/v1", model="m", transport=transport
    )
    candidates = [{"text": "a", "id": 1}, {"text": "b", "id": 2}]
    result = await provider.rerank("q", candidates, top_k=2)
    # Degradation: passthrough, no hard failure
    assert result == candidates
    assert provider.last_error is not None


def test_remote_llm_fault_returns_none():
    """LLM connection failure -> None (005 deterministic fallback), no raise."""
    from rag_mcp.agents.llm_client import LLMClient

    client = LLMClient(
        base_url="http://127.0.0.1:1",  # nothing listens -> immediate refusal
        api_key="",
        model="m",
        timeout_s=1.0,
    )
    result = client.chat_json("system", {"q": "x"})
    assert result is None


def test_remote_llm_no_base_url_returns_none():
    from rag_mcp.agents.llm_client import LLMClient

    client = LLMClient(base_url="", api_key="", model="m")
    assert client.chat_json("s", "u") is None
