"""Unit tests for the unified trace-body switch (T053, RED first).

FR-018/FR-019/SC-007: when TRACE_BODY_ENABLED=false, run records for every
retrieval mode store query_text IS NULL and trace_body_recorded=FALSE while
ID / completion_status / duration / error are preserved at 100%. The 005
AGENTIC_TRACE_BODY_ENABLED alias keeps working.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.models import RetrievalRun


class StubEmbedding:
    async def embed_texts(self, texts):
        return [[0.0] for _ in texts]

    def get_dimension(self) -> int:
        return 1

    async def embed_query(self, text):
        return [0.0]


class StubQdrant:
    pass


async def _record(service, run_id, query="secret query body", **kw):
    await service._record_retrieval_run(
        query=query,
        project_scopes=["100"],
        completion_status="complete",
        evidence_count=1,
        duration_ms=42,
        retrieval_mode="dense",
        run_id=run_id,
        **kw,
    )


async def _fetch(db_session, run_id):
    return (
        await db_session.execute(
            select(RetrievalRun).where(RetrievalRun.run_id == run_id)
        )
    ).scalar_one()


@pytest.fixture(autouse=True)
def _cleanup(engine):
    run_ids = []
    yield run_ids
    # best-effort cleanup below in tests


def _make_service(db_session, monkeypatch, trace="true"):
    monkeypatch.setenv("TRACE_BODY_ENABLED", trace)
    from rag_mcp.services.retrieval_service import RetrievalService

    return RetrievalService(db_session, StubQdrant(), StubEmbedding())


@pytest.mark.asyncio
async def test_trace_disabled_nulls_query_and_marks(db_session, monkeypatch):
    service = _make_service(db_session, monkeypatch, trace="false")
    run_id = uuid.uuid4().int & 0x7FFFFFFFFFFFFFFF
    await _record(service, run_id)
    row = await _fetch(db_session, run_id)
    assert row.query_text is None
    assert row.trace_body_recorded is False
    # ID / status / duration / evidence preserved at 100% (FR-019)
    assert row.run_id == run_id
    assert row.completion_status == "complete"
    assert row.duration_ms == 42
    assert row.evidence_count == 1
    await db_session.execute(delete(RetrievalRun).where(RetrievalRun.run_id == run_id))
    await db_session.commit()


@pytest.mark.asyncio
async def test_trace_enabled_keeps_query(db_session, monkeypatch):
    service = _make_service(db_session, monkeypatch, trace="true")
    run_id = uuid.uuid4().int & 0x7FFFFFFFFFFFFFFF
    await _record(service, run_id, query="visible body")
    row = await _fetch(db_session, run_id)
    assert row.query_text == "visible body"
    assert row.trace_body_recorded is True
    await db_session.execute(delete(RetrievalRun).where(RetrievalRun.run_id == run_id))
    await db_session.commit()


@pytest.mark.asyncio
async def test_agentic_alias_still_works(db_session, monkeypatch):
    """AGENTIC_TRACE_BODY_ENABLED stays a compatible alias (research §1.7)."""
    monkeypatch.setenv("TRACE_BODY_ENABLED", "")
    monkeypatch.setenv("AGENTIC_TRACE_BODY_ENABLED", "false")
    from rag_mcp.services.retrieval_service import RetrievalService

    service = RetrievalService(db_session, StubQdrant(), StubEmbedding())
    run_id = uuid.uuid4().int & 0x7FFFFFFFFFFFFFFF
    await _record(service, run_id)
    row = await _fetch(db_session, run_id)
    assert row.query_text is None
    assert row.trace_body_recorded is False
    await db_session.execute(delete(RetrievalRun).where(RetrievalRun.run_id == run_id))
    await db_session.commit()


@pytest.mark.asyncio
async def test_tool_column_recorded(db_session, monkeypatch):
    """data-model §4.1: search runs record tool='search_knowledge'."""
    service = _make_service(db_session, monkeypatch, trace="true")
    run_id = uuid.uuid4().int & 0x7FFFFFFFFFFFFFFF
    await _record(service, run_id)
    row = await _fetch(db_session, run_id)
    assert row.tool == "search_knowledge"
    await db_session.execute(delete(RetrievalRun).where(RetrievalRun.run_id == run_id))
    await db_session.commit()


@pytest.mark.asyncio
async def test_error_summary_recorded_on_failure(db_session, monkeypatch):
    service = _make_service(db_session, monkeypatch, trace="true")
    run_id = uuid.uuid4().int & 0x7FFFFFFFFFFFFFFF
    await service._record_retrieval_run(
        query="q",
        project_scopes=["100"],
        completion_status="failed",
        evidence_count=0,
        duration_ms=5,
        retrieval_mode="dense",
        run_id=run_id,
        error_summary={"code": "SYSTEM_ERROR", "message": "boom", "failed_paths": ["dense"]},
    )
    row = await _fetch(db_session, run_id)
    assert row.error_summary["code"] == "SYSTEM_ERROR"
    assert row.error_summary["failed_paths"] == ["dense"]
    await db_session.execute(delete(RetrievalRun).where(RetrievalRun.run_id == run_id))
    await db_session.commit()


@pytest.mark.asyncio
async def test_instance_attribution_recorded(db_session, monkeypatch):
    """US3: runs carry instance_id / instance_mode from the instance context."""
    service = _make_service(db_session, monkeypatch, trace="true")
    from rag_mcp.runtime.instance_context import instance_scope

    run_id = uuid.uuid4().int & 0x7FFFFFFFFFFFFFFF
    iid = uuid.uuid4()
    with instance_scope(iid, "reader", worker_id=1):
        await _record(service, run_id)
    row = await _fetch(db_session, run_id)
    assert row.instance_id == iid
    assert row.instance_mode == "reader"
    await db_session.execute(delete(RetrievalRun).where(RetrievalRun.run_id == run_id))
    await db_session.commit()
