"""Per-capability concurrency limiting (006, T046).

FR-009: each Provider capability (LLM / Embedding / Reranker) has an
independent concurrency ceiling (clarification Q2: LLM 4/8, Embedding 8/16,
Reranker 2/4). A bounded asyncio semaphore queues callers beyond the limit;
the three ceilings never interfere with one another.
"""

from __future__ import annotations

import asyncio

from rag_mcp.config.provider_config import ProviderSettings


class ConcurrencyLimiter:
    """Async context-manager bound to one capability's concurrency ceiling."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError(f"concurrency limit must be >= 1, got {limit}")
        self._limit = limit
        self._semaphore = asyncio.Semaphore(limit)

    @property
    def limit(self) -> int:
        return self._limit

    async def __aenter__(self) -> "ConcurrencyLimiter":
        await self._semaphore.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._semaphore.release()


def build_limiters(providers: ProviderSettings) -> dict[str, ConcurrencyLimiter]:
    """Assemble one independent limiter per capability from run config."""
    return {
        "embedding": ConcurrencyLimiter(providers.embedding.concurrency_limit),
        "reranker": ConcurrencyLimiter(providers.reranker.concurrency_limit),
        "llm": ConcurrencyLimiter(providers.llm.concurrency_limit),
    }
