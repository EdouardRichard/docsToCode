"""Single-writer lease coordination (006, T014).

`WriteCoordinator` is the deployment evolution interface (blueprint §21.2):
the first implementation is the PostgreSQL lease row specified in
data-model §3 — an INSERT-acquired `writer_lease` row guarded by the partial
unique index `idx_lease_single_active` (state='active'), renewed every 30s
(default) and expiring after a 90s window (default). PostgreSQL/Redis-style
distributed coordinators can replace it later without touching callers.

Semantics (FR-002/FR-003, clarification Q2):

- acquire = (a) mark expired any active row past expires_at, (b) INSERT a
  new active row; if the insert hits the unique index while an active row
  still stands, acquisition FAILS with the holder instance_id and expiry —
  never a silent downgrade to reader.
- renew = UPDATE own row's renewed_at/expires_at (idempotent per cycle).
- release = mark own row released (worker can be re-acquired immediately).
- The read path never consults the lease (SC-003).

All timestamps come from the PostgreSQL clock (research §2: DB clock
arbitrates lease/heartbeat times).
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_mcp.models import WriterLease
from rag_mcp.utils.snowflake import generate_id

logger = logging.getLogger(__name__)

DEFAULT_RENEW_INTERVAL_S = 30
DEFAULT_EXPIRY_WINDOW_S = 90


@dataclass(frozen=True)
class LeaseAcquisition:
    """Result of an acquire() attempt."""

    lease_id: int = 0
    acquired: bool = False
    holder_instance_id: uuid.UUID | None = None
    holder_expires_at: datetime | None = None
    error: str | None = None


@dataclass(frozen=True)
class LeaseInfo:
    """Observer view of the active lease (who is the writer)."""

    lease_id: int
    holder_instance_id: uuid.UUID
    state: str
    renewed_at: datetime
    expires_at: datetime


class WriteCoordinator(ABC):
    """Deployment evolution interface for write-ownership arbitration."""

    @abstractmethod
    async def acquire(
        self,
        holder_instance_id: uuid.UUID,
        renew_interval_s: int = DEFAULT_RENEW_INTERVAL_S,
        expiry_window_s: int = DEFAULT_EXPIRY_WINDOW_S,
    ) -> LeaseAcquisition:
        """Try to become the writer. Never degrades silently."""

    @abstractmethod
    async def renew(self, lease_id: int, expiry_window_s: int) -> bool:
        """Roll renewed_at/expires_at forward; False when the lease is gone."""

    @abstractmethod
    async def release(self, lease_id: int) -> bool:
        """Release the lease (graceful shutdown); False when not active."""

    @abstractmethod
    async def get_active_lease(self) -> LeaseInfo | None:
        """Return the active lease row, if any (observability)."""


class PostgresLeaseWriteCoordinator(WriteCoordinator):
    """writer_lease-row implementation (data-model §3).

    One transaction per operation: expire-stale -> INSERT; the partial
    unique index is the single-writer guarantee, so correctness never
    depends on serialization between competing processes.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker,
        id_generator: Callable[[], int] = generate_id,
    ) -> None:
        self._session_factory = session_factory
        self._id_generator = id_generator

    async def _db_now(self, session: AsyncSession) -> datetime:
        return (await session.execute(select(func.now()))).scalar()

    async def acquire(
        self,
        holder_instance_id: uuid.UUID,
        renew_interval_s: int = DEFAULT_RENEW_INTERVAL_S,
        expiry_window_s: int = DEFAULT_EXPIRY_WINDOW_S,
    ) -> LeaseAcquisition:
        for attempt in range(2):
            async with self._session_factory() as session:
                now = await self._db_now(session)
                # (a) reclaim: any active row past its expiry -> expired
                await session.execute(
                    update(WriterLease)
                    .where(
                        WriterLease.state == "active",
                        WriterLease.expires_at < now,
                    )
                    .values(state="expired")
                )
                # (b) insert our active row
                lease = WriterLease(
                    lease_id=self._id_generator(),
                    holder_instance_id=holder_instance_id,
                    state="active",
                    acquired_at=now,
                    renewed_at=now,
                    expires_at=now + timedelta(seconds=expiry_window_s),
                )
                session.add(lease)
                try:
                    await session.commit()
                    logger.info(
                        "writer lease %s acquired by %s (renew=%ds, expiry=%ds)",
                        lease.lease_id, holder_instance_id,
                        renew_interval_s, expiry_window_s,
                    )
                    return LeaseAcquisition(lease_id=lease.lease_id, acquired=True)
                except IntegrityError:
                    await session.rollback()
                    # (c) unique conflict while a live active row stands:
                    # reject loudly with the holder's identity + expiry.
                    conflict = await self._live_conflict(session)
                    if conflict is not None:
                        holder, expires_at = conflict
                        return LeaseAcquisition(
                            acquired=False,
                            holder_instance_id=holder,
                            holder_expires_at=expires_at,
                            error=(
                                f"writer lease already held by instance "
                                f"{holder} until {expires_at.isoformat()}; "
                                f"refusing to enter write mode (FR-002)"
                            ),
                        )
                    # The conflicting row expired between the insert and the
                    # check (or a concurrent reclaim) -> retry once.
                    logger.warning(
                        "lease insert conflicted with a concurrently-expired row "
                        "(attempt %d); retrying", attempt + 1,
                    )
        return LeaseAcquisition(
            acquired=False,
            error="could not acquire the writer lease after retry",
        )

    async def _live_conflict(
        self, session: AsyncSession
    ) -> tuple[uuid.UUID, datetime] | None:
        now = await self._db_now(session)
        row = (
            await session.execute(
                select(WriterLease).where(WriterLease.state == "active")
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        if row.expires_at <= now:
            return None
        return row.holder_instance_id, row.expires_at

    async def renew(self, lease_id: int, expiry_window_s: int) -> bool:
        async with self._session_factory() as session:
            now = await self._db_now(session)
            result = await session.execute(
                update(WriterLease)
                .where(WriterLease.lease_id == lease_id, WriterLease.state == "active")
                .values(
                    renewed_at=now,
                    expires_at=now + timedelta(seconds=expiry_window_s),
                )
            )
            await session.commit()
            return result.rowcount == 1

    async def release(self, lease_id: int) -> bool:
        async with self._session_factory() as session:
            now = await self._db_now(session)
            result = await session.execute(
                update(WriterLease)
                .where(WriterLease.lease_id == lease_id, WriterLease.state == "active")
                .values(state="released", released_at=now)
            )
            await session.commit()
            return result.rowcount == 1

    async def get_active_lease(self) -> LeaseInfo | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(WriterLease).where(WriterLease.state == "active")
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return LeaseInfo(
                lease_id=row.lease_id,
                holder_instance_id=row.holder_instance_id,
                state=row.state,
                renewed_at=row.renewed_at,
                expires_at=row.expires_at,
            )

    async def purge_all_for_tests(self) -> int:
        """Delete every lease row (unit-test isolation only)."""
        async with self._session_factory() as session:
            result = await session.execute(delete(WriterLease))
            await session.commit()
            return result.rowcount or 0
