"""Persistence wiring for the agentic run flow (T059).

Persists runtime state during an agentic run into the 005 tables:
  - evidence_ledger_entry  : every recalled evidence (append-only, FR-008/009)
  - agent_judgment         : every evidence-analyst judgment per round
  - context_selection_list : step-8 selection decisions (FR-017)
  - agentic_retrieval_run  : the run record at run end (FR-031)

All writes carry the isolation triple (knowledge_scope_id, project_id,
index_version); cross-scope writes are rejected (FR-022). The bridge key
(request_id, evidence_id) resolves ledger entries (SC-006).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.models.knowledge_version import KnowledgeVersion
from rag_mcp.models.project import Project
from rag_mcp.orchestration.context_selection import ContextSelectionStore
from rag_mcp.orchestration.judgment_store import JudgmentStore
from rag_mcp.orchestration.ledger import EvidenceLedgerStore
from rag_mcp.orchestration.models import AgenticRetrievalRun

logger = logging.getLogger(__name__)


def derive_retriever(candidate: dict[str, Any]) -> str:
    """Map a fused candidate to the single ledger retriever enum value."""
    if candidate.get("rerank_score") is not None:
        return "rerank"
    retrievers = candidate.get("retrievers") or []
    if len(retrievers) > 1:
        return "fusion"
    if retrievers and retrievers[0] in ("dense", "sparse", "graph"):
        return retrievers[0]
    return "fusion"


class AgenticPersistence:
    """Run-scoped persistence for the four 005 runtime tables (T059)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._ledger = EvidenceLedgerStore(session)
        self._judgments = JudgmentStore(session)
        self._selections = ContextSelectionStore(session)
        self._triples: list[dict[str, Any]] | None = None

    # ------------------------------------------------------------------
    # Scope resolution / isolation
    # ------------------------------------------------------------------

    async def resolve_scope_triples(self, scope_ids: list[int]) -> list[dict[str, Any]]:
        """Authoritative isolation triples for the request scopes.

        Every published version of every requested scope yields one triple
        (knowledge_scope_id, project_id, index_version). Ledger entries must
        match one of these or the write is rejected (FR-022).
        """
        if self._triples is not None:
            return self._triples
        triples: list[dict[str, Any]] = []
        if scope_ids:
            proj_rows = await self._session.execute(
                sa_select(Project.knowledge_scope_id, Project.project_id).where(
                    Project.knowledge_scope_id.in_(scope_ids)
                )
            )
            project_map = {sid: pid for sid, pid in proj_rows.all()}
            ver_rows = await self._session.execute(
                sa_select(
                    KnowledgeVersion.knowledge_scope_id,
                    KnowledgeVersion.version_number,
                ).where(
                    KnowledgeVersion.knowledge_scope_id.in_(scope_ids),
                    KnowledgeVersion.status == "published",
                )
            )
            for sid, vnum in ver_rows.all():
                triples.append({
                    "knowledge_scope_id": sid,
                    "project_id": project_map.get(sid, 0),
                    "index_version": vnum,
                })
        self._triples = triples
        return triples

    # ------------------------------------------------------------------
    # Ledger
    # ------------------------------------------------------------------

    async def persist_round_candidates(
        self,
        *,
        request_id: str,
        run_id: str,
        round_index: int,
        candidates: list[dict[str, Any]],
        scope_ids: list[int],
        referenced_by_agent: str = "evidence_analyst",
    ) -> dict[str, str]:
        """Append one ledger entry per recalled candidate (FR-008/FR-009).

        Returns evidence_id -> ledger_entry_id so candidates can carry the
        bridge id into context orchestration. Parent-context supplements
        (T065) are persisted with referenced_by_agent='context_orchestrator'.
        """
        triples = await self.resolve_scope_triples(scope_ids)
        id_map: dict[str, str] = {}
        for cand in candidates:
            entry_data = {
                "ledger_entry_id": self._ledger.generate_ledger_entry_id(),
                "request_id": request_id,
                "run_id": str(run_id),
                "round_index": round_index,
                "sub_problem_id": int(cand.get("sub_problem_id", 1)),
                "evidence_id": str(cand.get("evidence_id", "")),
                "retrieval_query": str(cand.get("retrieval_query", "")),
                "retriever": derive_retriever(cand),
                "score": float(cand.get("score", 0.0)),
                "source_version": int(cand.get("source_version", 1)),
                "source_position": str(cand.get("source_position", "")),
                "knowledge_scope_id": int(cand.get("knowledge_scope_id", 0)),
                "knowledge_scope_type": cand.get("knowledge_scope_type") or "project",
                "project_id": int(cand.get("project_id", 0)),
                "index_version": int(cand.get("index_version", 1)),
                "referenced_by_agent": referenced_by_agent,
            }
            entry = await self._ledger.insert_entry(entry_data, triples)
            id_map[entry_data["evidence_id"]] = str(entry.ledger_entry_id)
        await self._session.flush()
        return id_map

    # ------------------------------------------------------------------
    # Judgments
    # ------------------------------------------------------------------

    async def persist_judgment(self, judgment: dict[str, Any]) -> str:
        """Append one agent_judgment row (FR-013). Returns the judgment_id."""
        record = self._judgments.to_judgment_record(judgment)
        await self._judgments.insert_judgment(record)
        return str(record["judgment_id"])

    # ------------------------------------------------------------------
    # Selection list
    # ------------------------------------------------------------------

    async def persist_selections(
        self,
        *,
        context_result_id: str,
        run_id: str,
        selection_list: list[dict[str, Any]],
    ) -> int:
        """Append selection decisions (FR-017). Returns the persisted count."""
        count = 0
        for sel in selection_list:
            ledger_entry_id = str(sel.get("ledger_entry_id", ""))
            decision = sel.get("decision", "")
            if not ledger_entry_id.isdigit() or decision not in (
                "selected", "truncated", "deduped",
            ):
                continue
            await self._selections.insert_selection({
                "context_result_id": context_result_id,
                "run_id": run_id,
                "ledger_entry_id": ledger_entry_id,
                "decision": decision,
            })
            count += 1
        await self._session.flush()
        return count

    # ------------------------------------------------------------------
    # Run record
    # ------------------------------------------------------------------

    async def persist_run_record(self, record: dict[str, Any]) -> AgenticRetrievalRun:
        """Append the agentic_retrieval_run row at run end (FR-031)."""
        run = AgenticRetrievalRun(
            run_id=int(record["run_id"]),
            request_id=str(record["request_id"]),
            project_scope=list(record.get("project_scope", [])),
            knowledge_scope_ids=list(record.get("knowledge_scope_ids", [])),
            task_context=record.get("task_context"),
            run_config=dict(record.get("run_config", {})),
            completion_status=str(record["completion_status"]),
            max_rounds=int(record.get("max_rounds", 2)),
            rounds_completed=int(record.get("rounds_completed", 0)),
            guardrail_state=dict(record.get("guardrail_state", {})),
            sub_path_timings=dict(record.get("sub_path_timings", {})),
            agent_outputs_ref=dict(record.get("agent_outputs_ref", {})),
            ledger_ref=dict(record.get("ledger_ref", {})),
            total_cost=record.get("total_cost"),
            schema_valid_all=bool(record.get("schema_valid_all", True)),
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        try:
            await self._session.rollback()
        except Exception:  # noqa: BLE001
            logger.error("Rollback failed", exc_info=True)
