"""Unit tests for instance_registry allocation/heartbeat (T017, RED first).

Covers FR-030 / clarification Q6 / data-model §2.4:

- two instances with the same explicit WORKER_ID -> unique constraint
  conflict rejected explicitly with the conflicting instance identifier
- unconfigured auto-assignment picks the lowest free worker_id
  (single instance gets 0 — 001 compatible)
- heartbeat rolls last_heartbeat_at / expires_at forward
- expired registrations are reclaimable (state -> 'expired')
- graceful deregistration releases the worker_id immediately

Runs against live PostgreSQL: the partial unique index
idx_registry_worker_active is the actual misconfiguration detection point.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_mcp.models import InstanceRegistry


@pytest_asyncio.fixture
async def session_factory(engine) -> async_sessionmaker:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
def created():
    return []


@pytest_asyncio.fixture
async def registry_service(engine, created):
    from rag_mcp.runtime.instance_registry import InstanceRegistryService

    return InstanceRegistryService(session_factory=async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False))


@pytest_asyncio.fixture(autouse=True)
async def _cleanup(engine, created):
    """Isolate the registry per test (shared dev DB).

    Before: expire any leftover active registrations so worker_id
    auto-assignment starts from a clean slate (no real instance is expected
    to be registered during unit tests). After: remove rows this test made.
    """
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    from sqlalchemy import update

    async with factory() as session:
        await session.execute(
            update(InstanceRegistry)
            .where(InstanceRegistry.state == "active")
            .values(state="expired")
        )
        await session.commit()
    yield
    async with factory() as session:
        if created:
            await session.execute(
                delete(InstanceRegistry).where(InstanceRegistry.instance_id.in_(created))
            )
        await session.commit()


def _uuid() -> uuid.UUID:
    value = uuid.uuid4()
    return value


# --------------------------------------------------------------------- imports


def test_import_instance_registry() -> None:
    from rag_mcp.runtime.instance_registry import InstanceRegistryService  # noqa: F401


# ---------------------------------------------------------------- registration


@pytest.mark.asyncio
async def test_register_single_instance_auto_worker_zero(
    db_session, registry_service, created
):
    instance_id = _uuid()
    created.append(instance_id)
    result = await registry_service.register(
        instance_id=instance_id,
        instance_mode="writer",
        process_role="management",
        worker_id=None,
        heartbeat_interval_s=30,
        expiry_window_s=90,
    )
    assert result.worker_id == 0
    assert result.registered is True

    row = (
        await db_session.execute(
            select(InstanceRegistry).where(InstanceRegistry.instance_id == instance_id)
        )
    ).scalar_one()
    assert row.state == "active"
    assert row.worker_id == 0


@pytest.mark.asyncio
async def test_explicit_worker_id_conflict_rejected_with_identifier(
    registry_service, created
):
    """Same explicit WORKER_ID on two instances -> explicit rejection."""
    first_id = _uuid()
    created.append(first_id)
    result = await registry_service.register(
        instance_id=first_id,
        instance_mode="reader",
        process_role="mcp",
        worker_id=5,
        heartbeat_interval_s=30,
        expiry_window_s=90,
    )
    assert result.registered is True
    assert result.worker_id == 5

    second_id = _uuid()
    created.append(second_id)
    conflict = await registry_service.register(
        instance_id=second_id,
        instance_mode="reader",
        process_role="mcp",
        worker_id=5,
        heartbeat_interval_s=30,
        expiry_window_s=90,
    )
    assert conflict.registered is False
    assert conflict.error is not None
    # Error carries the conflicting instance identifier
    assert str(first_id) in conflict.error
    assert conflict.conflicting_instance_id == first_id


@pytest.mark.asyncio
async def test_auto_assignment_lowest_free_worker_id(registry_service, created):
    """未配置自动补位：取未被活跃注册占用的最低 worker_id。"""
    busy = _uuid()
    created.append(busy)
    result = await registry_service.register(
        instance_id=busy,
        instance_mode="writer",
        process_role="mcp",
        worker_id=0,
        heartbeat_interval_s=30,
        expiry_window_s=90,
    )
    assert result.worker_id == 0

    auto = _uuid()
    created.append(auto)
    second = await registry_service.register(
        instance_id=auto,
        instance_mode="reader",
        process_role="mcp",
        worker_id=None,
        heartbeat_interval_s=30,
        expiry_window_s=90,
    )
    # 0 is taken by an active registration -> lowest free is 1
    assert second.registered is True
    assert second.worker_id == 1


@pytest.mark.asyncio
async def test_released_worker_id_reusable(db_session, registry_service, created):
    first_id = _uuid()
    created.append(first_id)
    await registry_service.register(
        instance_id=first_id,
        instance_mode="reader",
        process_role="mcp",
        worker_id=3,
        heartbeat_interval_s=30,
        expiry_window_s=90,
    )
    ok = await registry_service.deregister(instance_id=first_id)
    assert ok is True

    row = (
        await db_session.execute(
            select(InstanceRegistry).where(InstanceRegistry.instance_id == first_id)
        )
    ).scalar_one()
    assert row.state == "released"
    assert row.released_at is not None

    # worker_id 3 is immediately reusable
    second_id = _uuid()
    created.append(second_id)
    result = await registry_service.register(
        instance_id=second_id,
        instance_mode="reader",
        process_role="mcp",
        worker_id=3,
        heartbeat_interval_s=30,
        expiry_window_s=90,
    )
    assert result.registered is True
    assert result.worker_id == 3


# ------------------------------------------------------------------ heartbeat


@pytest.mark.asyncio
async def test_heartbeat_rolls_expiry_forward(db_session, registry_service, created):
    instance_id = _uuid()
    created.append(instance_id)
    await registry_service.register(
        instance_id=instance_id,
        instance_mode="reader",
        process_role="mcp",
        worker_id=1,
        heartbeat_interval_s=30,
        expiry_window_s=90,
    )
    before = (
        await db_session.execute(
            select(InstanceRegistry).where(InstanceRegistry.instance_id == instance_id)
        )
    ).scalar_one()

    ok = await registry_service.heartbeat(instance_id=instance_id, expiry_window_s=90)
    assert ok is True

    after = (
        await db_session.execute(
            select(InstanceRegistry).where(InstanceRegistry.instance_id == instance_id)
        )
    ).scalar_one()
    assert after.last_heartbeat_at >= before.last_heartbeat_at
    assert after.expires_at >= before.expires_at


@pytest.mark.asyncio
async def test_heartbeat_unknown_instance_returns_false(registry_service):
    assert await registry_service.heartbeat(instance_id=uuid.uuid4(), expiry_window_s=90) is False


# ------------------------------------------------------------ expiry reclaim


@pytest.mark.asyncio
async def test_expired_registrations_marked_expired(db_session, registry_service, created):
    instance_id = _uuid()
    created.append(instance_id)
    await registry_service.register(
        instance_id=instance_id,
        instance_mode="reader",
        process_role="mcp",
        worker_id=2,
        heartbeat_interval_s=30,
        expiry_window_s=90,
    )
    past = datetime.now(timezone.utc) - timedelta(seconds=200)
    await db_session.execute(
        update(InstanceRegistry)
        .where(InstanceRegistry.instance_id == instance_id)
        .values(expires_at=past)
    )
    await db_session.commit()

    purged = await registry_service.cleanup_expired()
    assert purged >= 1

    row = (
        await db_session.execute(
            select(InstanceRegistry).where(InstanceRegistry.instance_id == instance_id)
        )
    ).scalar_one()
    assert row.state == "expired"
