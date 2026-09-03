"""006 convergence T078/T079: retrieval run record wiring (RED first).

FR-016/FR-020: the search path must populate provider_usage (embedding/rerank
call accounting) and error_summary (error backtrace) on the RetrievalRun
record, not leave them NULL.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_service():
    from rag_mcp.services.retrieval_service import RetrievalService

    embed = MagicMock()
    embed.embed_query = AsyncMock(return_value=[0.1] * 1024)
    embed.get_dimension.return_value = 1024

    qdrant = MagicMock()
    qdrant.search.return_value = []

    service = RetrievalService(
        session=MagicMock(),
        qdrant_store=qdrant,
        embedding_provider=embed,
        reranker=None,
    )
    service._record_retrieval_run = AsyncMock()
    return service


@pytest.mark.asyncio
async def test_search_records_provider_usage():
    service = _make_service()
    service.resolve_project_refs = AsyncMock(return_value=([1], None))
    service._try_hybrid_recall = AsyncMock(return_value=None)

    await service.search("q", ["proj"], top_k=5)

    call = service._record_retrieval_run.call_args
    assert call is not None, "search must record a retrieval run"
    usage = call.kwargs.get("provider_usage")
    assert usage is not None
    assert usage["embedding_calls"] >= 1


@pytest.mark.asyncio
async def test_search_records_error_summary_on_scope_failure():
    service = _make_service()
    service.resolve_project_refs = AsyncMock(
        return_value=(
            [],
            {"code": "MISSING_PROJECT_SCOPE", "message": "no scope", "candidates": []},
        )
    )

    await service.search("q", ["proj"], top_k=5)

    call = service._record_retrieval_run.call_args
    assert call is not None
    summary = call.kwargs.get("error_summary")
    assert summary is not None
    assert summary["code"] == "MISSING_PROJECT_SCOPE"
