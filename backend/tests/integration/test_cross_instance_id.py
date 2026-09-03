"""Integration test: cross-instance ID uniqueness (T067/T068).

SC-013/FR-030: a writer + two readers allocate distinct worker_ids; their
snowflake IDs never collide across concurrent batches (primary-key zero
conflict); a second instance with the same explicit WORKER_ID is rejected
with the conflicting instance identifier.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_mcp.models import InstanceRegistry


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def _isolate(engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await session.execute(text("UPDATE instance_registry SET state='expired' WHERE state='active'"))
        await session.commit()
    yield


@pytest.mark.asyncio
async def test_three_instances_distinct_worker_ids(session_factory):
    from rag_mcp.runtime.instance_registry import InstanceRegistryService

    registry = InstanceRegistryService(session_factory)
    ids = [uuid.uuid4() for _ in range(3)]
    results = []
    for i, iid in enumerate(ids):
        results.append(await registry.register(
            instance_id=iid,
            instance_mode="reader" if i else "writer",
            process_role="mcp",
            worker_id=None,  # auto-assign lowest free
            expiry_window_s=90,
        ))
    worker_ids = [r.worker_id for r in results]
    assert all(r.registered for r in results)
    assert len(set(worker_ids)) == 3, f"worker_ids must be distinct: {worker_ids}"
    # single unconfigured instance gets 0; three get 0,1,2
    assert set(worker_ids) == {0, 1, 2}
    for iid in ids:
        await registry.deregister(iid)


@pytest.mark.asyncio
async def test_concurrent_ids_never_collide(session_factory):
    """Snowflake IDs across three worker_ids never collide (SC-013)."""
    from rag_mcp.utils.snowflake import SnowflakeGenerator

    gens = [SnowflakeGenerator(worker_id=w) for w in (0, 1, 2)]
    ids = set()

    async def gen(worker):
        for _ in range(500):
            ids.add(worker.generate())
            await asyncio.sleep(0)

    await asyncio.gather(*[gen(g) for g in gens])
    assert len(ids) == 1500  # zero collisions across 1500 ids


@pytest.mark.asyncio
async def test_same_explicit_worker_id_rejected(session_factory):
    from rag_mcp.runtime.instance_registry import InstanceRegistryService

    registry = InstanceRegistryService(session_factory)
    a = uuid.uuid4()
    b = uuid.uuid4()
    first = await registry.register(instance_id=a, instance_mode="reader", process_role="mcp", worker_id=42, expiry_window_s=90)
    assert first.registered
    second = await registry.register(instance_id=b, instance_mode="reader", process_role="mcp", worker_id=42, expiry_window_s=90)
    assert second.registered is False
    assert second.conflicting_instance_id == a
    assert str(a) in second.error
    await registry.deregister(a)
