"""Unit tests for 006 runtime ORM models (T007, RED first).

Asserts the InstanceRegistry / WriterLease / RuntimeMaintenanceLog ORM models
(rag_mcp.models.runtime, T008) map every column from data-model §2/§3/§5 —
snowflake BIGINT PKs, UUID columns, CHECK enums, TIMESTAMPTZ, and the
holder_instance_id FK relationship onto instance_registry.
"""

from __future__ import annotations

import pytest
from sqlalchemy import BigInteger, String, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID


def _import_models():
    from rag_mcp.models.runtime import (
        InstanceRegistry,
        RuntimeMaintenanceLog,
        WriterLease,
    )

    return InstanceRegistry, WriterLease, RuntimeMaintenanceLog


def test_import_models() -> None:
    _import_models()


# ------------------------------------------------------------ InstanceRegistry


def test_instance_registry_tablename() -> None:
    InstanceRegistry, _, _ = _import_models()
    assert InstanceRegistry.__tablename__ == "instance_registry"


def test_instance_registry_columns() -> None:
    InstanceRegistry, _, _ = _import_models()
    cols = InstanceRegistry.__table__.columns
    assert "instance_id" in cols and "worker_id" in cols
    assert "instance_mode" in cols and "process_role" in cols
    assert "state" in cols and "started_at" in cols
    assert "last_heartbeat_at" in cols and "expires_at" in cols
    assert "released_at" in cols

    assert isinstance(cols["instance_id"].type, UUID)
    assert cols["instance_id"].primary_key

    assert isinstance(cols["worker_id"].type, BigInteger) or str(
        cols["worker_id"].type
    ).upper() in ("SMALLINT", "INTEGER", "BIGINT")

    for name, length in (("instance_mode", 8), ("process_role", 12), ("state", 16)):
        assert isinstance(cols[name].type, String)
        assert cols[name].type.length == length

    assert cols["instance_mode"].nullable is False
    assert cols["process_role"].nullable is False
    assert cols["state"].nullable is False
    assert str(cols["state"].server_default.arg).strip("'") == "active"
    assert cols["released_at"].nullable is True
    for ts in ("started_at", "last_heartbeat_at", "expires_at"):
        assert isinstance(cols[ts].type, TIMESTAMP)
        assert cols[ts].nullable is False


def test_instance_registry_check_constraints() -> None:
    InstanceRegistry, _, _ = _import_models()
    checks = [
        c for c in InstanceRegistry.__table__.constraints if isinstance(c, __import__("sqlalchemy").CheckConstraint)
    ]
    exprs = " ".join(
        getattr(c.sqltext, "text", str(c.sqltext)) for c in checks
    )
    assert "writer" in exprs and "reader" in exprs
    assert "management" in exprs and "mcp" in exprs
    assert "released" in exprs and "expired" in exprs
    assert "worker_id" in exprs and "1023" in exprs


def test_instance_registry_partial_indexes() -> None:
    """idx_registry_worker_active (UNIQUE WHERE active) + idx_registry_expires."""
    InstanceRegistry, _, _ = _import_models()
    indexes = {idx.name: idx for idx in InstanceRegistry.__table__.indexes}
    assert "idx_registry_worker_active" in indexes
    assert "idx_registry_expires" in indexes

    worker_active = indexes["idx_registry_worker_active"]
    assert worker_active.unique
    assert [c.name for c in worker_active.columns] == ["worker_id"]
    postgresql_where = str(
        worker_active.dialect_options["postgresql"]["where"].text
    ).replace(" ", "")
    assert "state='active'" in postgresql_where

    expires = indexes["idx_registry_expires"]
    assert [c.name for c in expires.columns] == ["expires_at"]
    where = str(expires.dialect_options["postgresql"]["where"].text).replace(" ", "")
    assert "state='active'" in where


# ------------------------------------------------------------------ WriterLease


def test_writer_lease_tablename() -> None:
    _, WriterLease, _ = _import_models()
    assert WriterLease.__tablename__ == "writer_lease"


def test_writer_lease_columns() -> None:
    _, WriterLease, _ = _import_models()
    cols = WriterLease.__table__.columns
    assert "lease_id" in cols and "holder_instance_id" in cols
    assert "state" in cols and "acquired_at" in cols
    assert "renewed_at" in cols and "expires_at" in cols
    assert "released_at" in cols

    assert isinstance(cols["lease_id"].type, BigInteger)
    assert cols["lease_id"].primary_key
    assert isinstance(cols["holder_instance_id"].type, UUID)
    assert cols["holder_instance_id"].nullable is False
    assert str(cols["state"].server_default.arg).strip("'") == "active"
    for ts in ("acquired_at", "renewed_at", "expires_at"):
        assert isinstance(cols[ts].type, TIMESTAMP)
        assert cols[ts].nullable is False
    assert cols["released_at"].nullable is True


def test_writer_lease_holder_fk() -> None:
    _, WriterLease, _ = _import_models()
    fks = list(WriterLease.__table__.foreign_keys)
    assert len(fks) == 1
    fk = fks[0]
    assert fk.parent.name == "holder_instance_id"
    assert fk.column.table.name == "instance_registry"
    assert fk.column.name == "instance_id"


def test_writer_lease_partial_indexes() -> None:
    _, WriterLease, _ = _import_models()
    indexes = {idx.name: idx for idx in WriterLease.__table__.indexes}
    assert "idx_lease_single_active" in indexes
    assert "idx_lease_expires" in indexes

    single_active = indexes["idx_lease_single_active"]
    assert single_active.unique
    where = str(
        single_active.dialect_options["postgresql"]["where"].text
    ).replace(" ", "")
    assert "state='active'" in where


# -------------------------------------------------------- RuntimeMaintenanceLog


def test_maintenance_log_tablename() -> None:
    _, _, RuntimeMaintenanceLog = _import_models()
    assert RuntimeMaintenanceLog.__tablename__ == "runtime_maintenance_log"


def test_maintenance_log_columns() -> None:
    _, _, RuntimeMaintenanceLog = _import_models()
    cols = RuntimeMaintenanceLog.__table__.columns
    assert "log_id" in cols and "event_type" in cols
    assert "purged_retrieval_runs" in cols
    assert "purged_agentic_runs" in cols
    assert "purged_maintenance_logs" in cols
    assert "created_at" in cols

    assert isinstance(cols["log_id"].type, BigInteger)
    assert cols["log_id"].primary_key
    assert isinstance(cols["event_type"].type, String)
    assert cols["event_type"].type.length == 32
    for c in ("purged_retrieval_runs", "purged_agentic_runs", "purged_maintenance_logs"):
        assert cols[c].nullable is False
        assert cols[c].server_default is not None
    assert isinstance(cols["created_at"].type, TIMESTAMP)


def test_models_exported_from_package() -> None:
    from rag_mcp.models import InstanceRegistry, WriterLease, RuntimeMaintenanceLog  # noqa: F401
