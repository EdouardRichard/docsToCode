"""Unit tests for migration 0060_create_runtime_tables (T003, RED first).

Reflection assertions against the live database for the three 006 runtime
tables (data-model §2/§3/§5): instance_registry, writer_lease,
runtime_maintenance_log — including the partial unique indexes that provide
the DB-level single-writer guarantee (idx_lease_single_active) and the
worker_id misconfiguration detection point (idx_registry_worker_active).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.unit.test_migrations_helper import (
    get_check_constraints,
    get_columns,
    table_exists,
)


async def _index_defs(session, table_name: str) -> dict[str, str]:
    """Return {index_name: indexdef} for one table (indexdef includes
    UNIQUE and WHERE clauses of partial indexes)."""
    result = await session.execute(
        text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname='public' AND tablename=:t"
        ),
        {"t": table_name},
    )
    return {row[0]: row[1] for row in result}


async def _fk_referenced_tables(session, table_name: str) -> list[str]:
    """Return referenced table names of all FK constraints on one table."""
    oid_result = await session.execute(
        text(
            "SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE c.relname = :t AND c.relkind='r' AND n.nspname='public'"
        ),
        {"t": table_name},
    )
    table_oid = oid_result.scalar()
    if not table_oid:
        return []
    result = await session.execute(
        text(
            "SELECT confrelid::regclass::text FROM pg_constraint "
            "WHERE contype='f' AND conrelid=:oid"
        ),
        {"oid": table_oid},
    )
    return [row[0] for row in result]


async def _primary_key_columns(session, table_name: str) -> set[str]:
    oid_result = await session.execute(
        text(
            "SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE c.relname = :t AND c.relkind='r' AND n.nspname='public'"
        ),
        {"t": table_name},
    )
    table_oid = oid_result.scalar()
    if not table_oid:
        return set()
    result = await session.execute(
        text(
            "SELECT a.attname FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = :oid AND i.indisprimary"
        ),
        {"oid": table_oid},
    )
    return {row[0] for row in result}


# ------------------------------------------------------------ instance_registry


@pytest.mark.asyncio
async def test_instance_registry_table_exists(db_session):
    assert await table_exists(db_session, "instance_registry"), (
        "migration 0060 must create instance_registry"
    )


@pytest.mark.asyncio
async def test_instance_registry_columns(db_session):
    if not await table_exists(db_session, "instance_registry"):
        pytest.fail("instance_registry missing (migration 0060 not applied)")
    cols = await get_columns(db_session, "instance_registry")
    # instance_id: UUID PK
    assert cols["instance_id"]["data_type"] == "uuid"
    assert cols["instance_id"]["is_nullable"] == "NO"
    # worker_id: SMALLINT NOT NULL (0-1023 enforced by CHECK)
    assert cols["worker_id"]["data_type"] == "smallint"
    assert cols["worker_id"]["is_nullable"] == "NO"
    # instance_mode: VARCHAR(8) NOT NULL CHECK IN (writer, reader)
    assert cols["instance_mode"]["data_type"] == "character varying"
    assert cols["instance_mode"]["char_max_length"] == 8
    assert cols["instance_mode"]["is_nullable"] == "NO"
    # process_role: VARCHAR(12) NOT NULL CHECK IN (management, mcp)
    assert cols["process_role"]["char_max_length"] == 12
    assert cols["process_role"]["is_nullable"] == "NO"
    # state: VARCHAR(16) NOT NULL DEFAULT 'active'
    assert cols["state"]["char_max_length"] == 16
    assert cols["state"]["is_nullable"] == "NO"
    assert "active" in str(cols["state"]["column_default"]).lower()
    # timestamps: TIMESTAMPTZ NOT NULL
    for ts_col in ("started_at", "last_heartbeat_at", "expires_at"):
        assert cols[ts_col]["data_type"] == "timestamp with time zone"
        assert cols[ts_col]["is_nullable"] == "NO"
    # released_at nullable
    assert cols["released_at"]["data_type"] == "timestamp with time zone"
    assert cols["released_at"]["is_nullable"] == "YES"


@pytest.mark.asyncio
async def test_instance_registry_check_constraints(db_session):
    if not await table_exists(db_session, "instance_registry"):
        pytest.fail("instance_registry missing (migration 0060 not applied)")
    checks = await get_check_constraints(db_session, "instance_registry")
    joined = " | ".join(checks)
    assert "instance_mode" in joined and "writer" in joined and "reader" in joined
    assert "process_role" in joined and "management" in joined and "mcp" in joined
    assert "state" in joined and "released" in joined and "expired" in joined
    assert "worker_id" in joined and "1023" in joined


@pytest.mark.asyncio
async def test_instance_registry_worker_active_partial_unique(db_session):
    """idx_registry_worker_active: UNIQUE on (worker_id) WHERE state='active'.

    This partial unique index is the same-worker_id misconfiguration
    detection point (FR-030, data-model §2.2).
    """
    if not await table_exists(db_session, "instance_registry"):
        pytest.fail("instance_registry missing (migration 0060 not applied)")
    defs = await _index_defs(db_session, "instance_registry")
    assert "idx_registry_worker_active" in defs
    d = defs["idx_registry_worker_active"]
    assert "UNIQUE" in d.upper()
    assert "worker_id" in d
    # partial: WHERE state = 'active' (PG normalizes the cast form)
    assert "WHERE" in d.upper() and "state" in d and "'active'" in d


@pytest.mark.asyncio
async def test_instance_registry_expires_partial_index(db_session):
    if not await table_exists(db_session, "instance_registry"):
        pytest.fail("instance_registry missing (migration 0060 not applied)")
    defs = await _index_defs(db_session, "instance_registry")
    assert "idx_registry_expires" in defs
    d = defs["idx_registry_expires"]
    assert "expires_at" in d
    assert "WHERE" in d.upper() and "state" in d and "'active'" in d


@pytest.mark.asyncio
async def test_instance_registry_primary_key(db_session):
    if not await table_exists(db_session, "instance_registry"):
        pytest.fail("instance_registry missing (migration 0060 not applied)")
    pk = await _primary_key_columns(db_session, "instance_registry")
    assert pk == {"instance_id"}


# ----------------------------------------------------------------- writer_lease


@pytest.mark.asyncio
async def test_writer_lease_table_exists(db_session):
    assert await table_exists(db_session, "writer_lease"), (
        "migration 0060 must create writer_lease"
    )


@pytest.mark.asyncio
async def test_writer_lease_columns(db_session):
    if not await table_exists(db_session, "writer_lease"):
        pytest.fail("writer_lease missing (migration 0060 not applied)")
    cols = await get_columns(db_session, "writer_lease")
    # lease_id: BIGINT PK (snowflake)
    assert cols["lease_id"]["data_type"] == "bigint"
    assert cols["lease_id"]["is_nullable"] == "NO"
    # holder_instance_id: UUID NOT NULL FK
    assert cols["holder_instance_id"]["data_type"] == "uuid"
    assert cols["holder_instance_id"]["is_nullable"] == "NO"
    # state: VARCHAR(16) NOT NULL DEFAULT 'active'
    assert cols["state"]["char_max_length"] == 16
    assert cols["state"]["is_nullable"] == "NO"
    assert "active" in str(cols["state"]["column_default"]).lower()
    for ts_col in ("acquired_at", "renewed_at", "expires_at"):
        assert cols[ts_col]["data_type"] == "timestamp with time zone"
        assert cols[ts_col]["is_nullable"] == "NO"
    assert cols["released_at"]["is_nullable"] == "YES"


@pytest.mark.asyncio
async def test_writer_lease_holder_fk_to_instance_registry(db_session):
    if not await table_exists(db_session, "writer_lease"):
        pytest.fail("writer_lease missing (migration 0060 not applied)")
    refs = await _fk_referenced_tables(db_session, "writer_lease")
    assert any("instance_registry" in r for r in refs), (
        f"writer_lease.holder_instance_id must FK->instance_registry, got {refs}"
    )


@pytest.mark.asyncio
async def test_writer_lease_single_active_partial_unique(db_session):
    """idx_lease_single_active: UNIQUE on (state) WHERE state='active'.

    The DB-level guarantee that at most one active writer lease exists
    (FR-002, double-write = 0).
    """
    if not await table_exists(db_session, "writer_lease"):
        pytest.fail("writer_lease missing (migration 0060 not applied)")
    defs = await _index_defs(db_session, "writer_lease")
    assert "idx_lease_single_active" in defs
    d = defs["idx_lease_single_active"]
    assert "UNIQUE" in d.upper()
    assert "WHERE" in d.upper() and "state" in d and "'active'" in d


@pytest.mark.asyncio
async def test_writer_lease_expires_partial_index(db_session):
    if not await table_exists(db_session, "writer_lease"):
        pytest.fail("writer_lease missing (migration 0060 not applied)")
    defs = await _index_defs(db_session, "writer_lease")
    assert "idx_lease_expires" in defs
    d = defs["idx_lease_expires"]
    assert "expires_at" in d
    assert "WHERE" in d.upper() and "state" in d and "'active'" in d


# ------------------------------------------------------ runtime_maintenance_log


@pytest.mark.asyncio
async def test_runtime_maintenance_log_table_exists(db_session):
    assert await table_exists(db_session, "runtime_maintenance_log"), (
        "migration 0060 must create runtime_maintenance_log"
    )


@pytest.mark.asyncio
async def test_runtime_maintenance_log_columns(db_session):
    if not await table_exists(db_session, "runtime_maintenance_log"):
        pytest.fail("runtime_maintenance_log missing (migration 0060 not applied)")
    cols = await get_columns(db_session, "runtime_maintenance_log")
    assert cols["log_id"]["data_type"] == "bigint"
    assert cols["log_id"]["is_nullable"] == "NO"
    assert cols["event_type"]["char_max_length"] == 32
    assert cols["event_type"]["is_nullable"] == "NO"
    for c in ("purged_retrieval_runs", "purged_agentic_runs", "purged_maintenance_logs"):
        assert cols[c]["data_type"] == "integer"
        assert cols[c]["is_nullable"] == "NO"
        assert str(cols[c]["column_default"]) == "0"
    assert cols["created_at"]["data_type"] == "timestamp with time zone"
    assert "now()" in str(cols["created_at"]["column_default"]).lower()


@pytest.mark.asyncio
async def test_runtime_maintenance_log_event_type_check(db_session):
    if not await table_exists(db_session, "runtime_maintenance_log"):
        pytest.fail("runtime_maintenance_log missing (migration 0060 not applied)")
    checks = await get_check_constraints(db_session, "runtime_maintenance_log")
    joined = " | ".join(checks)
    assert "event_type" in joined and "ttl_purge" in joined
