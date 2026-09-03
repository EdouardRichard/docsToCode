"""Unit tests for runtime metrics aggregation (T057, RED first).

FR-016/FR-017: query-time aggregation over retrieval_runs + the maintenance
log, grouped by instance_mode/tool, within a TTL window. All fields are
aggregated numbers and identifiers — no query/evidence body. Tests insert
rows with a future created_at and aggregate over an explicit window so the
shared-DB legacy rows (instance_mode NULL) never affect the assertion.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.models import RetrievalRun, RuntimeMaintenanceLog


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest_asyncio.fixture(autouse=True)
async def _tracker(db_session):
    """Clean slate before each test (delete leaked 006 test rows, which all
    carry instance_mode IS NOT NULL — no production writer/reader is running
    during unit tests), then ALWAYS delete this test's rows in teardown."""
    await db_session.execute(
        text("DELETE FROM retrieval_runs WHERE instance_mode IS NOT NULL OR created_at > NOW()")
    )
    await db_session.commit()
    tracker = {"run_ids": [], "log_ids": []}
    yield tracker
    if tracker["run_ids"]:
        await db_session.execute(
            delete(RetrievalRun).where(RetrievalRun.run_id.in_(tracker["run_ids"]))
        )
    if tracker["log_ids"]:
        await db_session.execute(
            delete(RuntimeMaintenanceLog).where(RuntimeMaintenanceLog.log_id.in_(tracker["log_ids"]))
        )
    await db_session.commit()


@pytest.mark.asyncio
async def _insert_run(session, tracker, *, mode, tool, status, duration, provider_usage, created_at):
    run_id = uuid.uuid4().int & 0x7FFFFFFFFFFFFFFF
    session.add(RetrievalRun(
        run_id=run_id,
        query_text="q",
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
        provider_usage=provider_usage,
        created_at=created_at,
        expires_at=created_at + timedelta(days=7),
    ))
    tracker["run_ids"].append(run_id)


async def _window():
    base = _now()
    return base + timedelta(minutes=30), base + timedelta(hours=2)


@pytest.mark.asyncio
async def test_request_totals_grouped_by_mode_and_tool(db_session, _tracker):
    from rag_mcp.runtime.metrics import aggregate_request_totals

    from_ts, to_ts = await _window()
    created = from_ts + timedelta(minutes=30)
    await _insert_run(db_session, _tracker, mode="reader", tool="search_knowledge", status="complete", duration=100, provider_usage={"embedding_calls": 1}, created_at=created)
    await _insert_run(db_session, _tracker, mode="reader", tool="search_knowledge", status="complete", duration=120, provider_usage={"embedding_calls": 1}, created_at=created)
    await _insert_run(db_session, _tracker, mode="writer", tool="search_knowledge", status="complete", duration=80, provider_usage={"embedding_calls": 1}, created_at=created)
    await db_session.commit()

    totals = await aggregate_request_totals(db_session, from_ts, to_ts)
    by_key = {(r["instance_mode"], r["tool"]): r["requests"] for r in totals}
    assert by_key[("reader", "search_knowledge")] == 2
    assert by_key[("writer", "search_knowledge")] == 1


@pytest.mark.asyncio
async def test_status_distribution(db_session, _tracker):
    from rag_mcp.runtime.metrics import aggregate_status_distribution

    from_ts, to_ts = await _window()
    created = from_ts + timedelta(minutes=30)
    await _insert_run(db_session, _tracker, mode="reader", tool="search_knowledge", status="complete", duration=100, provider_usage=None, created_at=created)
    await _insert_run(db_session, _tracker, mode="reader", tool="search_knowledge", status="failed", duration=50, provider_usage=None, created_at=created)
    await db_session.commit()

    dist = await aggregate_status_distribution(db_session, from_ts, to_ts)
    by_key = {(r["instance_mode"], r["status"]): r["count"] for r in dist}
    assert by_key[("reader", "complete")] == 1
    assert by_key[("reader", "failed")] == 1


@pytest.mark.asyncio
async def test_latency_percentiles(db_session, _tracker):
    from rag_mcp.runtime.metrics import aggregate_latency

    from_ts, to_ts = await _window()
    created = from_ts + timedelta(minutes=30)
    for duration in (50, 100, 150, 200):
        await _insert_run(db_session, _tracker, mode="reader", tool="search_knowledge", status="complete", duration=duration, provider_usage=None, created_at=created)
    await db_session.commit()

    latency = await aggregate_latency(db_session, from_ts, to_ts)
    row = [r for r in latency if r["tool"] == "search_knowledge"][0]
    # percentile_cont(0.5) of [50,100,150,200] = 125 (linear interpolation);
    # p95 interpolates near the top of the distribution.
    assert row["p50_ms"] == 125
    assert row["p95_ms"] >= 125


@pytest.mark.asyncio
async def test_provider_usage_sums(db_session, _tracker):
    from rag_mcp.runtime.metrics import aggregate_provider_usage

    from_ts, to_ts = await _window()
    created = from_ts + timedelta(minutes=30)
    await _insert_run(db_session, _tracker, mode="reader", tool="search_knowledge", status="complete", duration=100, provider_usage={"embedding_calls": 2, "rerank_calls": 1, "llm_calls": 1, "llm_prompt_chars": 5, "llm_completion_chars": 3}, created_at=created)
    await _insert_run(db_session, _tracker, mode="reader", tool="search_knowledge", status="complete", duration=100, provider_usage={"embedding_calls": 3, "rerank_calls": 0, "llm_calls": 0, "llm_prompt_chars": 0, "llm_completion_chars": 0}, created_at=created)
    await db_session.commit()

    usage = await aggregate_provider_usage(db_session, from_ts, to_ts)
    assert usage["embedding_calls"] == 5
    assert usage["rerank_calls"] == 1
    assert usage["llm_calls"] == 1
    assert usage["llm_prompt_chars"] == 5
    assert usage["llm_completion_chars"] == 3


@pytest.mark.asyncio
async def test_ttl_purge_sums(db_session, _tracker):
    from rag_mcp.runtime.metrics import aggregate_ttl_purge

    from_ts, to_ts = await _window()
    created = from_ts + timedelta(minutes=30)
    log_id = uuid.uuid4().int & 0x7FFFFFFFFFFFFFFF
    db_session.add(RuntimeMaintenanceLog(
        log_id=log_id,
        event_type="ttl_purge",
        purged_retrieval_runs=7,
        purged_agentic_runs=2,
        purged_maintenance_logs=1,
        created_at=created,
    ))
    _tracker["log_ids"].append(log_id)
    await db_session.commit()

    purge = await aggregate_ttl_purge(db_session, from_ts, to_ts)
    assert purge["purged_retrieval_runs"] == 7
    assert purge["purged_agentic_runs"] == 2
    assert purge["purged_maintenance_logs"] == 1


@pytest.mark.asyncio
async def test_build_metrics_shape_matches_schema(db_session):
    from rag_mcp.runtime.metrics import build_runtime_metrics
    from rag_mcp.config import get_settings

    metrics = await build_runtime_metrics(db_session, get_settings())
    for key in (
        "generated_at", "window", "request_totals", "completion_status_distribution",
        "latency", "subpath_timings_ms", "provider_usage", "ttl_purge", "active_instances",
    ):
        assert key in metrics, f"missing {key}"
    assert "ttl_days" in metrics["window"]
