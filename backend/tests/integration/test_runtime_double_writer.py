"""Integration test: double-writer rejection (T027/T028).

Two independent async engines (two "writer processes") compete for the
single-writer lease over the live shared PostgreSQL. The second writer is
100% rejected with the holder's identity; at no instant are there two
active leases (double-write events = 0, SC-002).

Exercises the real chain: server._acquire_writer_lease -> instance
registration -> PostgresLeaseWriteCoordinator.acquire -> partial unique
index idx_lease_single_active.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from rag_mcp.models import InstanceRegistry, WriterLease


@pytest_asyncio.fixture
async def engine_pair():
    """Two independent engines -> two independent connection pools."""
    from rag_mcp.config import get_settings

    url = get_settings().database_url
    eng_a = create_async_engine(url, pool_size=2)
    eng_b = create_async_engine(url, pool_size=2)
    yield eng_a, eng_b
    await eng_a.dispose()
    await eng_b.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _isolate(engine_pair):
    eng_a, _ = engine_pair
    factory = async_sessionmaker(eng_a, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await session.execute(delete(WriterLease))
        await session.commit()
    yield


@pytest_asyncio.fixture
async def writer_settings(monkeypatch):
    """Frozen Settings-like object with distinct worker_ids per process."""
    from rag_mcp.config import get_settings

    base = get_settings()

    class S:
        worker_id = None
        lease_renew_interval_s = 5
        lease_expiry_window_s = 60
        instance_mode = "writer"

    return S()


async def _active_lease_count(engine) -> int:
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text("SELECT COUNT(*) FROM writer_lease WHERE state='active'")
            )
        ).scalar()


def _import():
    from rag_mcp import server as server_mod
    from rag_mcp import db as db_mod

    return server_mod, db_mod


@pytest.mark.asyncio
async def test_second_writer_rejected_chain(engine_pair, writer_settings, monkeypatch):
    """SC-002: the second writer is 100% rejected; double-write events = 0."""
    server_mod, db_mod = _import()
    eng_a, eng_b = engine_pair
    factory_a = async_sessionmaker(eng_a, class_=AsyncSession, expire_on_commit=False)
    factory_b = async_sessionmaker(eng_b, class_=AsyncSession, expire_on_commit=False)

    # Process A acquires the lease
    monkeypatch.setattr(db_mod, "get_session_factory", lambda: factory_a)
    lease_a = await server_mod._acquire_writer_lease(writer_settings)
    assert lease_a.acquired is True
    assert await _active_lease_count(eng_a) == 1

    # Process B (independent pool) is rejected with holder info
    monkeypatch.setattr(db_mod, "get_session_factory", lambda: factory_b)
    with pytest.raises(RuntimeError) as excinfo:
        await server_mod._acquire_writer_lease(writer_settings)
    message = str(excinfo.value)
    assert "refusing to enter write mode" in message
    assert str(lease_a.holder_instance_id) in message

    # At no instant are there two active leases (double-write events = 0)
    assert await _active_lease_count(eng_b) == 1

    # Clean up: release A's lease + deregister
    from rag_mcp.runtime.instance_registry import InstanceRegistryService
    from rag_mcp.runtime.write_coordinator import PostgresLeaseWriteCoordinator

    coordinator = PostgresLeaseWriteCoordinator(factory_a)
    await coordinator.release(lease_a.lease_id)
    registry = InstanceRegistryService(factory_a)
    await registry.deregister(lease_a.holder_instance_id)


@pytest.mark.asyncio
async def test_two_engines_active_lease_never_two(engine_pair, writer_settings):
    """Direct coordinator-level double-writer guarantee across two pools."""
    from rag_mcp.runtime.write_coordinator import PostgresLeaseWriteCoordinator

    eng_a, eng_b = engine_pair
    factory_a = async_sessionmaker(eng_a, class_=AsyncSession, expire_on_commit=False)
    factory_b = async_sessionmaker(eng_b, class_=AsyncSession, expire_on_commit=False)

    # register two holder instances
    from rag_mcp.runtime.instance_registry import InstanceRegistryService

    registry_a = InstanceRegistryService(factory_a)
    id_a = uuid.uuid4()
    r = await registry_a.register(instance_id=id_a, instance_mode="writer", process_role="management", worker_id=10)
    assert r.registered
    id_b = uuid.uuid4()
    r = await registry_a.register(instance_id=id_b, instance_mode="writer", process_role="management", worker_id=11)
    assert r.registered

    coord_a = PostgresLeaseWriteCoordinator(factory_a)
    coord_b = PostgresLeaseWriteCoordinator(factory_b)

    first = await coord_a.acquire(holder_instance_id=id_a, renew_interval_s=5, expiry_window_s=60)
    assert first.acquired
    assert await _active_lease_count(eng_a) == 1

    second = await coord_b.acquire(holder_instance_id=id_b, renew_interval_s=5, expiry_window_s=60)
    assert second.acquired is False
    assert second.holder_instance_id == id_a
    assert second.holder_expires_at is not None
    assert await _active_lease_count(eng_b) == 1

    # cleanup
    await coord_a.release(first.lease_id)
    await registry_a.deregister(id_a)
    await registry_a.deregister(id_b)
