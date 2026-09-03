"""Integration test: writer lease recovery (T031/T032).

FR-003: after a writer is killed without releasing its lease, the expiry
window must elapse before a new writer can reclaim it; during the recovery
window any second writer is still rejected; after expiry the new writer
acquires and renews. Reads are unaffected (the read path never consults the
lease, SC-003).

Uses shortened windows (expiry 1s) for a fast, deterministic cycle.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from rag_mcp.models import WriterLease


@pytest_asyncio.fixture
async def engines():
    from rag_mcp.config import get_settings

    url = get_settings().database_url
    eng_a = create_async_engine(url, pool_size=2)
    eng_b = create_async_engine(url, pool_size=2)
    yield eng_a, eng_b
    await eng_a.dispose()
    await eng_b.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _isolate(engines):
    eng_a, _ = engines
    factory = async_sessionmaker(eng_a, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await session.execute(delete(WriterLease))
        await session.commit()
    yield


async def _active_count(engine) -> int:
    async with engine.connect() as conn:
        return (
            await conn.execute(text("SELECT COUNT(*) FROM writer_lease WHERE state='active'"))
        ).scalar()


@pytest.mark.asyncio
async def test_lease_recovery_cycle(engines):
    from rag_mcp.runtime.instance_registry import InstanceRegistryService
    from rag_mcp.runtime.write_coordinator import PostgresLeaseWriteCoordinator

    eng_a, eng_b = engines
    factory_a = async_sessionmaker(eng_a, class_=AsyncSession, expire_on_commit=False)
    factory_b = async_sessionmaker(eng_b, class_=AsyncSession, expire_on_commit=False)

    registry_a = InstanceRegistryService(factory_a)
    id_a = uuid.uuid4()
    assert (await registry_a.register(instance_id=id_a, instance_mode="writer", process_role="management", worker_id=20)).registered
    id_b = uuid.uuid4()
    assert (await registry_a.register(instance_id=id_b, instance_mode="writer", process_role="management", worker_id=21)).registered
    id_c = uuid.uuid4()
    assert (await registry_a.register(instance_id=id_c, instance_mode="writer", process_role="management", worker_id=22)).registered

    coord_a = PostgresLeaseWriteCoordinator(factory_a)
    coord_b = PostgresLeaseWriteCoordinator(factory_b)

    # Writer A acquires with a 1-second expiry window (crashes without renew)
    lease_a = await coord_a.acquire(holder_instance_id=id_a, renew_interval_s=1, expiry_window_s=1)
    assert lease_a.acquired

    # Within the recovery window: writer B is rejected
    second_early = await coord_b.acquire(holder_instance_id=id_b, renew_interval_s=1, expiry_window_s=1)
    assert second_early.acquired is False
    assert await _active_count(eng_a) == 1

    # Wait for the expiry window to elapse (crashed writer's lease expires)
    await asyncio.sleep(1.5)

    # After expiry: writer B reclaims the lease
    reclaimed = await coord_b.acquire(holder_instance_id=id_b, renew_interval_s=1, expiry_window_s=1)
    assert reclaimed.acquired is True

    # The crashed writer's row is now expired (not active), single active lease
    from rag_mcp.models import WriterLease as WL

    async with factory_a() as session:
        old = (
            await session.execute(select(WL).where(WL.lease_id == lease_a.lease_id))
        ).scalar_one()
        assert old.state == "expired"
    assert await _active_count(eng_b) == 1

    # Writer B can renew its reclaimed lease
    assert await coord_b.renew(lease_id=reclaimed.lease_id, expiry_window_s=1) is True

    # Cleanup
    await coord_b.release(reclaimed.lease_id)
    for iid in (id_a, id_b, id_c):
        await registry_a.deregister(iid)
