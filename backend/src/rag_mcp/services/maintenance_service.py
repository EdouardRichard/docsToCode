"""MaintenanceService: periodic housekeeping for append-only run records.

Implements the RetrievalRun TTL cleanup required by blueprint §20: run state
and trace records are retained for a bounded window (default 7 days, set via
``expires_at``) and then purged. Purging is the only code path allowed to
delete ``retrieval_runs`` rows (the table is otherwise append-only).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.models.retrieval_run import RetrievalRun

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
