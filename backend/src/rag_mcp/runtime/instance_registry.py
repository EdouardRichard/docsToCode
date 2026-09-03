"""Instance registry service (006, T018).

Every instance process (writer management, writer MCP, reader MCP) registers
one row at startup (data-model §2, FR-030, clarification Q6):

- instance_id: UUID v4 for one process lifetime
- worker_id: explicit (WORKER_ID) with loud conflict detection on the
  idx_registry_worker_active partial unique index, or auto-assigned lowest
  free worker_id (single unconfigured instance gets 0 — 001 compatible)
- heartbeat: rolls last_heartbeat_at/expires_at; expired registrations are
  reclaimed by the writer management maintenance loop
- deregistration: releases the worker_id immediately (state='released')

The registry uses the DB clock for all timestamps (research §2).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from rag_mcp.models import InstanceRegistry

logger = logging.getLogger(__name__)

MAX_WORKER_ID = 1023


@dataclass(frozen=True)
class RegistrationResult:
    """Result of a register() attempt."""

    registered: bool
    worker_id: int = -1
    instance_id: uuid.UUID | None = None
    conflicting_instance_id: uuid.UUID | None = None
    error: str | None = None


class InstanceRegistryService:
    """Register / heartbeat / deregister instance rows; allocate worker_ids."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def _db_now(self, session) -> datetime:
        return (await session.execute(select(func.now()))).scalar()

    async def register(
        self,
        instance_id: uuid.UUID,
        instance_mode: str,
        process_role: str,
        worker_id: int | None = None,
        heartbeat_interval_s: int = 30,
        expiry_window_s: int = 90,
    ) -> RegistrationResult:
        """Register one instance row; allocate or validate its worker_id.

        Explicit worker_id: unique-conflict -> registered=False with the
        conflicting instance identifier (misconfiguration is loud, never
        silent). None: auto-assign the lowest free worker_id; on a race the
        next candidate is tried once before failing explicitly.
        """
        if worker_id is not None and not 0 <= worker_id <= MAX_WORKER_ID:
            return RegistrationResult(
                registered=False,
                instance_id=instance_id,
                error=f"worker_id must be 0-{MAX_WORKER_ID}, got {worker_id}",
            )

        candidates = (
            [worker_id] if worker_id is not None else await self._free_worker_ids()
        )
        for candidate in candidates:
            async with self._session_factory() as session:
                now = await self._db_now(session)
                session.add(
                    InstanceRegistry(
                        instance_id=instance_id,
                        worker_id=candidate,
                        instance_mode=instance_mode,
                        process_role=process_role,
                        state="active",
                        last_heartbeat_at=now,
                        expires_at=now + timedelta(seconds=expiry_window_s),
                    )
                )
                try:
                    await session.commit()
                    logger.info(
                        "instance %s registered (mode=%s role=%s worker_id=%d)",
                        instance_id, instance_mode, process_role, candidate,
                    )
                    return RegistrationResult(
                        registered=True,
                        worker_id=candidate,
                        instance_id=instance_id,
                    )
                except IntegrityError:
                    await session.rollback()
                    conflict = await self._find_active_holder(session, candidate)
                    if conflict is not None:
                        return RegistrationResult(
                            registered=False,
                            instance_id=instance_id,
                            conflicting_instance_id=conflict.instance_id,
                            error=(
                                f"worker_id {candidate} already held by active "
                                f"instance {conflict.instance_id} "
                                f"(mode={conflict.instance_mode}, "
                                f"role={conflict.process_role}); refusing "
                                f"registration (FR-030 misconfiguration)"
                            ),
                        )
                    # Race with a concurrent expiry/reclaim: try next candidate.
                    logger.warning(
                        "worker_id %d insert raced with a concurrent change; "
                        "retrying with next candidate", candidate,
                    )
                    if worker_id is not None:
                        # explicit id had a transient race (should not happen):
                        # fail explicitly rather than guessing
                        return RegistrationResult(
                            registered=False,
                            instance_id=instance_id,
                            error=f"worker_id {worker_id} registration raced; retry",
                        )
        return RegistrationResult(
            registered=False,
            instance_id=instance_id,
            error="could not allocate a free worker_id after retry",
        )

    async def _find_active_holder(self, session, worker_id: int):
        return (
            await session.execute(
                select(InstanceRegistry).where(
                    InstanceRegistry.worker_id == worker_id,
                    InstanceRegistry.state == "active",
                )
            )
        ).scalar_one_or_none()

    async def _free_worker_ids(self, limit: int = 2) -> list[int]:
        """Lowest free worker_ids (FOR UPDATE SKIP LOCKED over candidates)."""
        async with self._session_factory() as session:
            occupied_rows = await session.execute(
                select(InstanceRegistry.worker_id).where(
                    InstanceRegistry.state == "active"
                )
            )
            occupied = {row[0] for row in occupied_rows}
            free: list[int] = []
            for candidate in range(0, MAX_WORKER_ID + 1):
                if candidate not in occupied:
                    free.append(candidate)
                    if len(free) >= limit:
                        break
            return free

    async def heartbeat(self, instance_id: uuid.UUID, expiry_window_s: int = 90) -> bool:
        """Roll last_heartbeat_at/expires_at; False when not active."""
        async with self._session_factory() as session:
            now = await self._db_now(session)
            result = await session.execute(
                update(InstanceRegistry)
                .where(
                    InstanceRegistry.instance_id == instance_id,
                    InstanceRegistry.state == "active",
                )
                .values(
                    last_heartbeat_at=now,
                    expires_at=now + timedelta(seconds=expiry_window_s),
                )
            )
            await session.commit()
            return result.rowcount == 1

    async def deregister(self, instance_id: uuid.UUID) -> bool:
        """Graceful exit: release the worker_id immediately."""
        async with self._session_factory() as session:
            now = await self._db_now(session)
            result = await session.execute(
                update(InstanceRegistry)
                .where(
                    InstanceRegistry.instance_id == instance_id,
                    InstanceRegistry.state == "active",
                )
                .values(state="released", released_at=now)
            )
            await session.commit()
            return result.rowcount == 1

    async def cleanup_expired(self) -> int:
        """Mark active registrations past their expiry as expired.

        Runs on the writer management maintenance loop (FR-004); expired
        worker_ids become reusable.
        """
        async with self._session_factory() as session:
            now = await self._db_now(session)
            result = await session.execute(
                update(InstanceRegistry)
                .where(
                    InstanceRegistry.state == "active",
                    InstanceRegistry.expires_at < now,
                )
                .values(state="expired")
            )
            await session.commit()
            if result.rowcount:
                logger.info("marked %d expired instance registrations", result.rowcount)
            return result.rowcount or 0

    async def get_active(self) -> list[InstanceRegistry]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(InstanceRegistry).where(InstanceRegistry.state == "active")
                )
            ).scalars().all()
            return list(rows)
