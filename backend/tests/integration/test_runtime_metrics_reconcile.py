"""Integration test: runtime metrics reconciliation (T063/T064).

SC-006: after a known batch of retrieval runs (across writer and reader
instances, distinct modes and completion statuses), the metrics endpoint
aggregates requests / status distribution / provider usage / latency with
reconciliation deviation = 0 against the batch.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_mcp.models import RetrievalRun


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def _clean_slate(engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await session.execute(
            text("DELETE FROM retrieval_runs WHERE instance_mode IS NOT NULL")
        )
        await session.commit()
    yield


@pytest.mark.asyncio
async def test_metrics_reconcile_zero_deviation(session_factory):
    from rag_mcp.runtime.metrics import build_runtime_metrics
    from rag_mcp.config import get_settings

    now = datetime.now(timezone.utc)
    # Known batch: 3 reader complete + 1 reader failed + 2 writer complete
    batch = [
        ("reader", "search_knowledge", "complete", 100, {"embedding_calls": 1}),
        ("reader", "search_knowledge", "complete", 120, {"embedding_calls": 2}),
        ("reader", "search_knowledge", "complete", 140, {"embedding_calls": 1}),
        ("reader", "search_knowledge", "failed", 60, {"embedding_calls": 1}),
        ("writer", "search_knowledge", "complete", 90, {"embedding_calls": 3}),
        ("writer", "search_knowledge", "complete", 110, {"embedding_calls": 1}),
    ]
    run_ids = []
    async with session_factory() as session:
        for mode, tool, status, duration, usage in batch:
            run_id = uuid.uuid4().int & 0x7FFFFFFFFFFFFFFF
            run_ids.append(run_id)
            session.add(RetrievalRun(
                run_id=run_id,
                query_text="batch query",
                project_scopes=["100"],
                completion_status=status,
                evidence_count=1,
                duration_ms=duration,
                retrieval_mode="dense",
                evidence_ref_ids=[],
                tool=tool,
                instance_id=uuid.uuid4(),
                instance_mode=mode,
                trace_body_recorded=True,
                provider_usage=usage,
                created_at=now,
                expires_at=now + timedelta(days=7),
            ))
        await session.commit()

    try:
        async with session_factory() as session:
            metrics = await build_runtime_metrics(session, get_settings())

        totals = {(r["instance_mode"], r["tool"]): r["requests"] for r in metrics["request_totals"]}
        assert totals[("reader", "search_knowledge")] == 4
        assert totals[("writer", "search_knowledge")] == 2

        dist = {(r["instance_mode"], r["status"]): r["count"] for r in metrics["completion_status_distribution"]}
        assert dist[("reader", "complete")] == 3
        assert dist[("reader", "failed")] == 1
        assert dist[("writer", "complete")] == 2

        # provider_usage sums across the batch: embedding_calls = 1+2+1+1+3+1 = 9
        assert metrics["provider_usage"]["embedding_calls"] == 9
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(RetrievalRun).where(RetrievalRun.run_id.in_(run_ids))
            )
            await session.commit()
