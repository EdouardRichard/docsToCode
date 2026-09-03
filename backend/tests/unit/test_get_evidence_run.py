"""Unit tests for get_evidence run recording (T084, RED first).

data-model §4.1 / FR-016: get_evidence calls must land in retrieval_runs
with tool='get_evidence' so runtime metrics aggregate by Tool (request totals
/ status distribution cover both search_knowledge and get_evidence).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from rag_mcp.models import RetrievalRun
from rag_mcp.services.evidence_service import EvidenceService


@pytest.mark.asyncio
async def test_get_evidence_not_found_records_run(db_session):
    service = EvidenceService(db_session)
    evidence_id = str(uuid.uuid4().int & 0x7FFFFFFFFFFFFFFF)
    result = await service.get_evidence(evidence_id=evidence_id, project_scopes=["100"])
    await db_session.commit()

    assert result["status"] == "unavailable"
    rows = (
        await db_session.execute(
            select(RetrievalRun).where(RetrievalRun.tool == "get_evidence")
        )
    ).scalars().all()
    ours = [
        r for r in rows
        if r.error_summary and evidence_id in r.error_summary.get("message", "")
    ]
    assert ours, "expected a get_evidence retrieval_runs row for the not-found call"
    run = ours[-1]
    assert run.tool == "get_evidence"
    assert run.completion_status == "failed"
    assert run.evidence_count == 0
    assert run.error_summary["code"] == "EVIDENCE_NOT_FOUND"
    await db_session.execute(delete(RetrievalRun).where(RetrievalRun.run_id == run.run_id))
    await db_session.commit()


@pytest.mark.asyncio
async def test_get_evidence_available_records_run(db_session):
    # A get_evidence call that reaches the service records a run; the chunk
    # lookup is a real query so an arbitrary id yields 'unavailable' — the
    # run row is still written with the get_evidence tool attribution.
    service = EvidenceService(db_session)
    evidence_id = str(uuid.uuid4().int & 0x7FFFFFFFFFFFFFFF)
    await service.get_evidence(evidence_id=evidence_id, project_scopes=["100"])
    await db_session.commit()

    rows = (
        await db_session.execute(
            select(RetrievalRun).where(RetrievalRun.tool == "get_evidence")
        )
    ).scalars().all()
    ours = [
        r for r in rows
        if r.error_summary and evidence_id in r.error_summary.get("message", "")
    ]
    assert ours
    run = ours[-1]
    assert run.tool == "get_evidence"
    await db_session.execute(delete(RetrievalRun).where(RetrievalRun.run_id == run.run_id))
    await db_session.commit()
