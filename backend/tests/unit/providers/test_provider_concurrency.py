"""Unit tests for Provider concurrency limits (T045, RED first).

FR-009: each capability (LLM / Embedding / Reranker) has an independent
concurrency ceiling; exceeding the limit is bounded (queued by a semaphore),
and the three ceilings never interfere with one another.
"""

from __future__ import annotations

import asyncio

import pytest


def test_import_concurrency_limiter() -> None:
    from rag_mcp.providers.concurrency import ConcurrencyLimiter, build_limiters  # noqa: F401


def test_limiters_built_from_settings() -> None:
    from rag_mcp.providers.concurrency import build_limiters

    from rag_mcp.config import get_settings

    settings = get_settings()
    limiters = build_limiters(settings.providers)
    assert set(limiters.keys()) == {"embedding", "reranker", "llm"}
    assert limiters["llm"].limit == 4
    assert limiters["embedding"].limit == 8
    assert limiters["reranker"].limit == 2


@pytest.mark.asyncio
async def test_limit_respected() -> None:
    """At most `limit` concurrent acquisitions at once (FR-009)."""
    from rag_mcp.providers.concurrency import ConcurrencyLimiter

    limiter = ConcurrencyLimiter(limit=2)
    active = 0
    peak = 0
    released = 0

    async def worker():
        nonlocal active, peak, released
        async with limiter:
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            released += 1

    await asyncio.gather(*[worker() for _ in range(8)])
    assert peak <= 2
    assert released == 8


@pytest.mark.asyncio
async def test_limits_are_independent() -> None:
    """LLM/Embedding/Reranker ceilings never interfere (FR-009)."""
    from rag_mcp.providers.concurrency import ConcurrencyLimiter

    llm = ConcurrencyLimiter(limit=4)
    embedding = ConcurrencyLimiter(limit=8)
    reranker = ConcurrencyLimiter(limit=2)

    async def acquire(limiter):
        async with limiter:
            return True

    # Saturating reranker (limit 2) and llm (limit 4) never blocks embedding
    # (limit 8): all 14 acquisitions complete.
    results = await asyncio.gather(
        *[acquire(reranker) for _ in range(2)],
        *[acquire(embedding) for _ in range(8)],
        *[acquire(llm) for _ in range(4)],
    )
    assert all(results)
    assert len(results) == 14


@pytest.mark.asyncio
async def test_limiter_rejects_invalid_limit() -> None:
    from rag_mcp.providers.concurrency import ConcurrencyLimiter

    with pytest.raises(ValueError):
        ConcurrencyLimiter(limit=0)
    with pytest.raises(ValueError):
        ConcurrencyLimiter(limit=-1)
