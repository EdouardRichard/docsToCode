"""MaintenanceService: periodic housekeeping for append-only run records.

Implements the RetrievalRun TTL cleanup required by blueprint §20: run state
and trace records are retained for a bounded window (default 7 days, set via
"expires_at") and then purged. Purging is the only code path allowed to
delete "retrieval_runs" rows (the table is otherwise append-only).

005 (T066): the four Agent-orchestration runtime tables are TTL-bounded the
same way — expired "agentic_retrieval_run" rows are purged together with
their cascading "evidence_ledger_entry" / "agent_judgment" /
"context_selection_list" rows. Purging is the only deletion path for these
otherwise append-only tables; unexpired runs and other projects' data are
never touched, and no runtime state is written back to the knowledge base
(FR-011/SC-014, blueprint §20).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.models.retrieval_run import RetrievalRun
from rag_mcp.orchestration.models import (
    AgenticRetrievalRun,
    AgentJudgment,
    ContextSelectionList,
    EvidenceLedgerEntry,
)

logger = logging.getLogger(__name__)


async def purge_expired_retrieval_runs(
    session: AsyncSession,
    now: datetime | None = None,
) -> int:
    """Delete retrieval run records whose ``expires_at`` has passed.

    Args:
        session: Async SQLAlchemy session (caller manages transaction scope).
        now: Reference timestamp; defaults to current UTC time. Injectable for
            deterministic testing.

    Returns:
        Number of rows deleted.

    Notes:
        The caller is responsible for committing the transaction. This keeps
        the helper composable inside larger maintenance transactions.
    """
    reference = now or datetime.now(timezone.utc)
    result = await session.execute(
        delete(RetrievalRun).where(RetrievalRun.expires_at < reference)
    )
    deleted = result.rowcount or 0
    if deleted:
        logger.info("Purged %d expired retrieval_runs (before %s)", deleted, reference)
    return deleted


async def purge_expired_agentic_runs(
    session: AsyncSession,
    now: datetime | None = None,
) -> dict[str, int]:
    """Delete expired 005 runtime rows in FK-safe order (T066, blueprint §20).

    Order: context_selection_list (FK -> ledger) -> evidence_ledger_entry ->
    agent_judgment -> agentic_retrieval_run. Only rows belonging to runs
    whose ttl_expires_at has passed are removed; unexpired runs and other
    projects' data stay untouched. No knowledge-base or vector-store writes
    happen here (FR-011/SC-014).

    Args:
        session: Async SQLAlchemy session (caller manages transaction scope).
        now: Reference timestamp; defaults to current UTC time. Injectable
            for deterministic testing.

    Returns:
        Per-table deleted row counts.
    """
    reference = now or datetime.now(timezone.utc)
    result = await session.execute(
        select(AgenticRetrievalRun.run_id).where(
            AgenticRetrievalRun.ttl_expires_at < reference
        )
    )
    run_ids = [row[0] for row in result.all()]
    if not run_ids:
        return {"runs": 0, "ledger_entries": 0, "judgments": 0, "selections": 0}

    run_id_strs = [str(run_id) for run_id in run_ids]
    sel = await session.execute(
        delete(ContextSelectionList).where(ContextSelectionList.run_id.in_(run_id_strs))
    )
    led = await session.execute(
        delete(EvidenceLedgerEntry).where(EvidenceLedgerEntry.run_id.in_(run_id_strs))
    )
    jud = await session.execute(
        delete(AgentJudgment).where(AgentJudgment.run_id.in_(run_id_strs))
    )
    runs = await session.execute(
        delete(AgenticRetrievalRun).where(AgenticRetrievalRun.run_id.in_(run_ids))
    )
    counts = {
        "runs": runs.rowcount or 0,
        "ledger_entries": led.rowcount or 0,
        "judgments": jud.rowcount or 0,
        "selections": sel.rowcount or 0,
    }
    logger.info(
        "Purged expired agentic runtime rows (before %s): %s", reference, counts,
    )
    return counts


class MaintenanceService:
    """Service facade for scheduled maintenance operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def purge_expired_retrieval_runs(
        self,
        now: datetime | None = None,
    ) -> int:
        """Purge expired retrieval runs and commit the transaction."""
        deleted = await purge_expired_retrieval_runs(self._session, now=now)
        await self._session.commit()
        return deleted

    async def purge_expired_agentic_runs(
        self,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """Purge expired 005 runtime rows and commit the transaction (T066)."""
        counts = await purge_expired_agentic_runs(self._session, now=now)
        await self._session.commit()
        return counts
