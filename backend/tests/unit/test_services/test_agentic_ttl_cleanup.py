"""Tests for 005 runtime-table TTL cleanup (T066 Red).

Periodic cleanup purges expired agentic_retrieval_run rows together with
their cascading evidence_ledger_entry / agent_judgment / context_selection_list
rows (blueprint §20):
  - expired runs (and only their rows) are deleted
  - unexpired runs and other projects' unexpired data stay untouched
  - run state never enters the vector store / is never written back to the
    knowledge base (FR-011/SC-014)
  - cleanup is idempotent

This test MUST FAIL before the 005 cleanup exists (TDD Red).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select as sa_select
from sqlalchemy import text as sa_text

from rag_mcp.orchestration.models import (
    AgenticRetrievalRun,
    AgentJudgment,
    ContextSelectionList,
    EvidenceLedgerEntry,
)
from rag_mcp.utils.snowflake import generate_id


@pytest_asyncio.fixture
async def ttl_setup(db_session):
    """Two projects; four runs (2 expired, 2 unexpired) with child rows."""
    from rag_mcp.schemas.project import ProjectCreate
    from rag_mcp.services.project_service import ProjectService

    svc = ProjectService(db_session)
    proj_a = await svc.create_project(ProjectCreate(name="TTL A", alias=f"ttl-a-{generate_id()}"))
    proj_b = await svc.create_project(ProjectCreate(name="TTL B", alias=f"ttl-b-{generate_id()}"))
    await db_session.commit()

    now = datetime.now(timezone.utc)
    expired_at = now - timedelta(days=1)
    future_at = now + timedelta(days=7)

    runs = {}
    for key, (proj, expires) in {
        "a_expired": (proj_a, expired_at),
        "a_fresh": (proj_a, future_at),
        "b_expired": (proj_b, expired_at),
        "b_fresh": (proj_b, future_at),
    }.items():
        run_id = generate_id()
        request_id = f"req-{key}"
        scope_id = proj.knowledge_scope_id
        run = AgenticRetrievalRun(
            run_id=run_id,
            request_id=request_id,
            project_scope=[str(proj.project_id)],
            knowledge_scope_ids=[str(scope_id)],
            task_context=None,
            run_config={},
            completion_status="complete",
            max_rounds=2,
            rounds_completed=1,
            guardrail_state={},
            sub_path_timings={},
            agent_outputs_ref={
                "query_planner": {"sub_problems": [], "schema_valid": True},
                "evidence_analyst": {"judgment_ids": [], "schema_valid_all": True},
                "context_orchestrator": {"context_result_id": "", "selection_list": [], "schema_valid": True},
            },
            ledger_ref={"ledger_entry_ids": [], "rounds": []},
            schema_valid_all=True,
        )
        db_session.add(run)

        ledger_id = generate_id()
        db_session.add(EvidenceLedgerEntry(
            ledger_entry_id=ledger_id,
            request_id=request_id,
            run_id=str(run_id),
            round_index=0,
            sub_problem_id=1,
            evidence_id=f"ev-{key}",
            retrieval_query="q",
            retriever="dense",
            score=0.5,
            source_version=1,
            source_position=f"pos-{key}",
            knowledge_scope_id=scope_id,
            knowledge_scope_type="project",
            project_id=proj.project_id,
            index_version=1,
            referenced_by_agent="evidence_analyst",
        ))
        db_session.add(AgentJudgment(
            judgment_id=generate_id(),
            run_id=str(run_id),
            round_index=0,
            coverage_state="covered",
            conflict_type="none",
            uncovered_sub_problem_ids=[],
            needs_supplementary=False,
            gap_descriptions=[],
            model_and_version="m",
            schema_valid=True,
        ))
        # Flush ledger first: context_selection_list carries an FK onto it
        await db_session.flush()
        db_session.add(ContextSelectionList(
            context_result_id=f"cr-{key}",
            run_id=str(run_id),
            ledger_entry_id=ledger_id,
            decision="selected",
        ))
        await db_session.flush()
        runs[key] = {"run_id": run_id, "ledger_id": ledger_id, "scope_id": scope_id, "project": proj}

    await db_session.commit()
    await db_session.execute(sa_text("UPDATE agentic_retrieval_run SET ttl_expires_at = :e WHERE run_id = :r"),
                             {"e": expired_at, "r": runs["a_expired"]["run_id"]})
    await db_session.execute(sa_text("UPDATE agentic_retrieval_run SET ttl_expires_at = :e WHERE run_id = :r"),
                             {"e": future_at, "r": runs["a_fresh"]["run_id"]})
    await db_session.execute(sa_text("UPDATE agentic_retrieval_run SET ttl_expires_at = :e WHERE run_id = :r"),
                             {"e": expired_at, "r": runs["b_expired"]["run_id"]})
    await db_session.execute(sa_text("UPDATE agentic_retrieval_run SET ttl_expires_at = :e WHERE run_id = :r"),
                             {"e": future_at, "r": runs["b_fresh"]["run_id"]})
    await db_session.commit()
    return {"runs": runs, "now": now}


async def _counts(db_session, run_id):
    ledger = (await db_session.execute(sa_text(
        "SELECT count(*) FROM evidence_ledger_entry WHERE run_id = :r"), {"r": str(run_id)})).scalar()
    judgment = (await db_session.execute(sa_text(
        "SELECT count(*) FROM agent_judgment WHERE run_id = :r"), {"r": str(run_id)})).scalar()
    selection = (await db_session.execute(sa_text(
        "SELECT count(*) FROM context_selection_list WHERE run_id = :r"), {"r": str(run_id)})).scalar()
    run = (await db_session.execute(sa_text(
        "SELECT count(*) FROM agentic_retrieval_run WHERE run_id = :r"), {"r": int(run_id)})).scalar()
    return {"ledger": ledger, "judgment": judgment, "selection": selection, "run": run}


class TestAgenticTtlCleanup:
    @pytest.mark.asyncio
    async def test_expired_runs_purged_with_children(self, db_session, ttl_setup):
        from rag_mcp.services.maintenance_service import purge_expired_agentic_runs

        runs = ttl_setup["runs"]
        counts = await purge_expired_agentic_runs(db_session, now=ttl_setup["now"])
        await db_session.commit()

        # >= 2: this fixture's expired runs plus any leftovers from earlier
        # sessions; the per-run checks below are the authoritative assertion.
        assert counts["runs"] >= 2
        for key in ("a_expired", "b_expired"):
            c = await _counts(db_session, runs[key]["run_id"])
            assert c == {"ledger": 0, "judgment": 0, "selection": 0, "run": 0}, f"{key} not purged"

    @pytest.mark.asyncio
    async def test_unexpired_and_other_projects_untouched(self, db_session, ttl_setup):
        from rag_mcp.services.maintenance_service import purge_expired_agentic_runs

        runs = ttl_setup["runs"]
        await purge_expired_agentic_runs(db_session, now=ttl_setup["now"])
        await db_session.commit()

        for key in ("a_fresh", "b_fresh"):
            c = await _counts(db_session, runs[key]["run_id"])
            assert c == {"ledger": 1, "judgment": 1, "selection": 1, "run": 1}, f"{key} damaged"

    @pytest.mark.asyncio
    async def test_no_writeback_to_knowledge_base(self, db_session, ttl_setup):
        """Cleanup deletes runtime rows only; KB tables stay untouched (FR-011)."""
        from rag_mcp.services.maintenance_service import purge_expired_agentic_runs

        before_chunks = (await db_session.execute(sa_text("SELECT count(*) FROM chunks"))).scalar()
        before_versions = (await db_session.execute(sa_text("SELECT count(*) FROM knowledge_versions"))).scalar()
        await purge_expired_agentic_runs(db_session, now=ttl_setup["now"])
        await db_session.commit()
        after_chunks = (await db_session.execute(sa_text("SELECT count(*) FROM chunks"))).scalar()
        after_versions = (await db_session.execute(sa_text("SELECT count(*) FROM knowledge_versions"))).scalar()
        assert before_chunks == after_chunks
        assert before_versions == after_versions

    @pytest.mark.asyncio
    async def test_cleanup_idempotent(self, db_session, ttl_setup):
        from rag_mcp.services.maintenance_service import purge_expired_agentic_runs

        first = await purge_expired_agentic_runs(db_session, now=ttl_setup["now"])
        await db_session.commit()
        second = await purge_expired_agentic_runs(db_session, now=ttl_setup["now"])
        await db_session.commit()
        assert first["runs"] >= 2
        assert second["runs"] == 0

    @pytest.mark.asyncio
    async def test_maintenance_service_facade(self, db_session, ttl_setup):
        from rag_mcp.services.maintenance_service import MaintenanceService

        svc = MaintenanceService(db_session)
        counts = await svc.purge_expired_agentic_runs(now=ttl_setup["now"])
        assert counts["runs"] >= 2
        runs = ttl_setup["runs"]
        for key in ("a_expired", "b_expired"):
            c = await _counts(db_session, runs[key]["run_id"])
            assert c == {"ledger": 0, "judgment": 0, "selection": 0, "run": 0}, f"{key} not purged"
