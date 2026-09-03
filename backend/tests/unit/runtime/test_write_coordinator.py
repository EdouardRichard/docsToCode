"""Unit tests for the WriteCoordinator state machine (T013, RED first).

Covers the PostgreSQL single-writer lease semantics (data-model §3.3,
FR-002/FR-003, clarification Q2):

- acquire succeeds and inserts an active lease row
- a second writer is rejected with the holder instance_id + expiry
- renew updates renewed_at/expires_at
- release moves the row to 'released' (immediately re-acquirable)
- an expired active row is reclaimed by a new writer
- renewal/expiry windows are parameterizable for testing

Runs against the live PostgreSQL (the partial unique index
idx_lease_single_active is the double-write=0 guarantee under test).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_mcp.models import InstanceRegistry, WriterLease


@pytest_asyncio.fixture
async def session_factory(engine) -> async_sessionmaker:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _cleanup_rows(session: AsyncSession, instance_ids: list[uuid.UUID]) -> None:
    await session.execute(
        delete(WriterLease).where(WriterLease.holder_instance_id.in_(instance_ids))
    )
    await session.execute(
        delete(InstanceRegistry).where(InstanceRegistry.instance_id.in_(instance_ids))
    )
    await session.commit()


@pytest.fixture
def registry_rows():
    """Track instance ids created by the test for cleanup."""
    return []


@pytest_asyncio.fixture(autouse=True)
async def _isolate_lease_table(engine, registry_rows):
    """Isolate the writer_lease table per test (shared dev DB).

    Before: drop any leftover lease rows (e.g. from crashed runs; no real
    writer is expected to hold a lease during unit tests). After: remove the
    rows this test created.
    """
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await session.execute(delete(WriterLease))
        await session.commit()
    yield
    if registry_rows:
        async with factory() as session:
            await _cleanup_rows(session, registry_rows)
    else:
        async with factory() as session:
            await session.execute(delete(WriterLease))
            await session.commit()


@pytest_asyncio.fixture
async def make_instance(engine, registry_rows):
    """Register an instance_registry row (lease FK requirement)."""

    async def _make(worker_id: int = 0, mode: str = "writer") -> uuid.UUID:
        instance_id = uuid.uuid4()
        registry_rows.append(instance_id)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            session.add(
                InstanceRegistry(
                    instance_id=instance_id,
                    worker_id=worker_id,
                    instance_mode=mode,
                    process_role="management",
                    state="active",
                    last_heartbeat_at=datetime.now(timezone.utc),
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                )
            )
            await session.commit()
        return instance_id

    return _make


# ---------------------------------------------------------------------- imports


def test_import_write_coordinator() -> None:
    from rag_mcp.runtime.write_coordinator import (  # noqa: F401
        PostgresLeaseWriteCoordinator,
        WriteCoordinator,
    )


def test_lease_acquisition_dataclass() -> None:
    from rag_mcp.runtime.write_coordinator import LeaseAcquisition

    result = LeaseAcquisition(lease_id=0, acquired=False)
    assert result.acquired is False


# ------------------------------------------------------------------ acquire


@pytest.mark.asyncio
async def test_acquire_inserts_active_lease(db_session, make_instance, session_factory):
    from rag_mcp.runtime.write_coordinator import PostgresLeaseWriteCoordinator

    holder = await make_instance(worker_id=0)
    coordinator = PostgresLeaseWriteCoordinator(session_factory=session_factory)
    result = await coordinator.acquire(
        holder_instance_id=holder,
        renew_interval_s=5,
        expiry_window_s=15,
    )
    assert result.acquired is True
    assert result.lease_id > 0

    row = (
        await db_session.execute(select(WriterLease).where(WriterLease.lease_id == result.lease_id))
    ).scalar_one()
    assert row.state == "active"
    assert row.holder_instance_id == holder
    assert row.renewed_at is not None and row.expires_at is not None
    assert row.expires_at > row.renewed_at


@pytest.mark.asyncio
async def test_second_writer_rejected_with_holder_info(
    db_session, make_instance, session_factory
):
    """FR-002: the second writer is 100% rejected, error carries holder info."""
    from rag_mcp.runtime.write_coordinator import PostgresLeaseWriteCoordinator

    holder_a = await make_instance(worker_id=0)
    holder_b = await make_instance(worker_id=1)
    coordinator = PostgresLeaseWriteCoordinator(session_factory=session_factory)

    first = await coordinator.acquire(holder_instance_id=holder_a, renew_interval_s=5, expiry_window_s=60)
    assert first.acquired is True

    second = await coordinator.acquire(holder_instance_id=holder_b, renew_interval_s=5, expiry_window_s=15)
    assert second.acquired is False
    # Rejection carries the current holder instance_id and its expiry
    assert str(holder_a) in (second.error or "")
    assert second.holder_instance_id == holder_a
    assert second.holder_expires_at is not None
    # No second active lease row was created for holder_b
    rows = (
        await db_session.execute(
            select(WriterLease).where(WriterLease.holder_instance_id == holder_b)
        )
    ).scalars().all()
    assert rows == []


# ------------------------------------------------------------------- renew


@pytest.mark.asyncio
async def test_renew_updates_renewed_and_expires(db_session, make_instance, session_factory):
    from rag_mcp.runtime.write_coordinator import PostgresLeaseWriteCoordinator

    holder = await make_instance(worker_id=0)
    coordinator = PostgresLeaseWriteCoordinator(session_factory=session_factory)
    result = await coordinator.acquire(
        holder_instance_id=holder, renew_interval_s=5, expiry_window_s=15
    )
    assert result.acquired

    before = (
        await db_session.execute(select(WriterLease).where(WriterLease.lease_id == result.lease_id))
    ).scalar_one()
    # Capture plain values: re-selecting returns the SAME identity-mapped
    # object, so attribute comparisons against the object would alias.
    before_renewed = before.renewed_at
    before_expires = before.expires_at
    ok = await coordinator.renew(lease_id=result.lease_id, expiry_window_s=60)
    assert ok is True

    # The coordinator commits through its own session; drop the identity-map
    # cache so the re-select sees the committed row.
    db_session.expire_all()
    after = (
        await db_session.execute(select(WriterLease).where(WriterLease.lease_id == result.lease_id))
    ).scalar_one()
    assert after.state == "active"
    assert after.renewed_at >= before_renewed
    assert after.expires_at > before_expires


@pytest.mark.asyncio
async def test_renew_unknown_lease_returns_false(session_factory):
    from rag_mcp.runtime.write_coordinator import PostgresLeaseWriteCoordinator

    coordinator = PostgresLeaseWriteCoordinator(session_factory=session_factory)
    assert await coordinator.renew(lease_id=999999999, expiry_window_s=15) is False


# ----------------------------------------------------------------- release


@pytest.mark.asyncio
async def test_release_makes_lease_reacquirable(db_session, make_instance, session_factory):
    from rag_mcp.runtime.write_coordinator import PostgresLeaseWriteCoordinator

    holder = await make_instance(worker_id=0)
    coordinator = PostgresLeaseWriteCoordinator(session_factory=session_factory)
    result = await coordinator.acquire(
        holder_instance_id=holder, renew_interval_s=5, expiry_window_s=60
    )
    await coordinator.release(lease_id=result.lease_id)

    db_session.expire_all()
    row = (
        await db_session.execute(select(WriterLease).where(WriterLease.lease_id == result.lease_id))
    ).scalar_one()
    assert row.state == "released"
    assert row.released_at is not None

    # A released lease is immediately re-acquirable
    holder_b = await make_instance(worker_id=1)
    again = await coordinator.acquire(
        holder_instance_id=holder_b, renew_interval_s=5, expiry_window_s=60
    )
    assert again.acquired is True


# --------------------------------------------------------- expiry reclamation


@pytest.mark.asyncio
async def test_expired_lease_reclaimed_by_new_writer(
    db_session, make_instance, session_factory
):
    """FR-003: an expired active row is marked expired and re-acquirable."""
    from rag_mcp.runtime.write_coordinator import PostgresLeaseWriteCoordinator

    stale_holder = await make_instance(worker_id=0)
    coordinator = PostgresLeaseWriteCoordinator(session_factory=session_factory)
    stale = await coordinator.acquire(
        holder_instance_id=stale_holder, renew_interval_s=5, expiry_window_s=60
    )

    # Simulate a crashed writer: active row whose expires_at is in the past
    past = datetime.now(timezone.utc) - timedelta(seconds=120)
    await db_session.execute(
        update(WriterLease)
        .where(WriterLease.lease_id == stale.lease_id)
        .values(expires_at=past)
    )
    await db_session.commit()

    new_holder = await make_instance(worker_id=1)
    result = await coordinator.acquire(
        holder_instance_id=new_holder, renew_interval_s=5, expiry_window_s=60
    )
    assert result.acquired is True

    db_session.expire_all()
    old_row = (
        await db_session.execute(select(WriterLease).where(WriterLease.lease_id == stale.lease_id))
    ).scalar_one()
    assert old_row.state == "expired"


# ----------------------------------------------------------- observer queries


@pytest.mark.asyncio
async def test_get_active_lease(make_instance, session_factory):
    from rag_mcp.runtime.write_coordinator import PostgresLeaseWriteCoordinator

    coordinator = PostgresLeaseWriteCoordinator(session_factory=session_factory)
    assert (await coordinator.get_active_lease()) is None

    holder = await make_instance(worker_id=0)
    result = await coordinator.acquire(
        holder_instance_id=holder, renew_interval_s=5, expiry_window_s=60
    )
    active = await coordinator.get_active_lease()
    assert active is not None
    assert active.lease_id == result.lease_id
    assert active.holder_instance_id == holder
