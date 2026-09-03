"""Unit tests for the TTL maintenance log (T059, RED first).

FR-016: the writer TTL purge writes an append-only runtime_maintenance_log
row recording the purged row counts; only INSERT happens (no update/delete).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from rag_mcp.models import RetrievalRun, RuntimeMaintenanceLog


def _now():
    return datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_purge_records_maintenance_log(db_session):
    from rag_mcp.services.maintenance_service import record_ttl_purge

    log = await record_ttl_purge(
        db_session,
        purged_retrieval_runs=5,
        purged_agentic_runs=2,
        purged_maintenance_logs=1,
    )
    await db_session.commit()
    assert log.log_id > 0

    row = (
        await db_session.execute(
            select(RuntimeMaintenanceLog).where(RuntimeMaintenanceLog.log_id == log.log_id)
        )
    ).scalar_one()
    assert row.event_type == "ttl_purge"
    assert row.purged_retrieval_runs == 5
    assert row.purged_agentic_runs == 2
    assert row.purged_maintenance_logs == 1
    await db_session.execute(
        delete(RuntimeMaintenanceLog).where(RuntimeMaintenanceLog.log_id == log.log_id)
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_run_ttl_purge_writes_log_with_counts(db_session):
    """run_ttl_purge purges expired rows AND records the counts (T060)."""
    from rag_mcp.services.maintenance_service import run_ttl_purge

    run_id = uuid.uuid4().int & 0x7FFFFFFFFFFFFFFF
    db_session.add(RetrievalRun(
        run_id=run_id,
        query_text="stale",
        project_scopes=["100"],
        completion_status="complete",
        evidence_count=0,
        duration_ms=1,
        retrieval_mode="dense",
        evidence_ref_ids=[],
        created_at=_now() - timedelta(days=30),
        expires_at=_now() - timedelta(days=1),
    ))
    await db_session.commit()

    result = await run_ttl_purge(db_session)
    assert result["purged_retrieval_runs"] >= 1
    await db_session.commit()

    logs = (
        await db_session.execute(
            select(RuntimeMaintenanceLog).order_by(RuntimeMaintenanceLog.log_id.desc()).limit(1)
        )
    ).scalars().all()
    assert logs, "a maintenance log row must be recorded"
    assert logs[0].event_type == "ttl_purge"
    assert logs[0].purged_retrieval_runs >= 1
    # cleanup
    for l in logs:
        await db_session.execute(delete(RuntimeMaintenanceLog).where(RuntimeMaintenanceLog.log_id == l.log_id))
    await db_session.commit()


@pytest.mark.asyncio
async def test_retrieval_run_expires_at_driven_by_ttl_config(monkeypatch, db_session):
    """FR-019: expires_at = write time + RETRIEVAL_TTL_DAYS (configurable)."""
    monkeypatch.setenv("RETRIEVAL_TTL_DAYS", "3")
    from rag_mcp.services.maintenance_service import compute_expires_at

    expires = compute_expires_at()
    delta = expires - _now()
    # ~3 days (allow clock skew)
    assert timedelta(days=2.9) <= delta <= timedelta(days=3.1)
