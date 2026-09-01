"""Unit test for soft_relation migration (T005).

Validates the soft_relation table, columns, indexes, and CHECK constraints
(data-model §3).

This test MUST FAIL before the migration is written/applied (TDD).
"""

from __future__ import annotations

import pytest

from tests.unit.test_migrations_helper import (
    get_check_constraints,
    get_columns,
    get_indexes,
    table_exists,
)


@pytest.mark.asyncio
async def test_soft_relation_table_exists(db_session):
    assert await table_exists(db_session, "soft_relation")


@pytest.mark.asyncio
async def test_soft_relation_columns(db_session):
    cols = await get_columns(db_session, "soft_relation")
    # edge_id PK + isolation
    assert "edge_id" in cols
    assert cols["edge_id"]["is_nullable"] == "NO"
    for c in ("knowledge_scope_id", "project_id", "index_version"):
        assert c in cols
        assert cols[c]["is_nullable"] == "NO"
    # source/target
    for c in ("source_chunk_id", "target_chunk_id"):
        assert c in cols
        assert cols[c]["is_nullable"] == "NO"
    # relation_type, direction, is_hard
    assert cols["relation_type"]["is_nullable"] == "NO"
    assert cols["direction"]["is_nullable"] == "NO"
    assert cols["is_hard"]["data_type"] == "boolean"
    assert cols["is_hard"]["is_nullable"] == "NO"
    # version
    assert cols["version"]["is_nullable"] == "NO"
    # five metadata
    for c in ("inference_source", "confidence", "model_and_version", "generated_at", "supporting_evidence_ids"):
        assert c in cols, f"Missing metadata column {c}"
        assert cols[c]["is_nullable"] == "NO"
    assert cols["confidence"]["data_type"] in ("numeric", "decimal")
    assert cols["supporting_evidence_ids"]["data_type"] == "jsonb"
    # lifecycle_state
    assert cols["lifecycle_state"]["is_nullable"] == "NO"
    # superseded_by / superseded_at (nullable)
    assert "superseded_by" in cols
    assert cols["superseded_by"]["is_nullable"] == "YES"
    assert "superseded_at" in cols
    assert cols["superseded_at"]["is_nullable"] == "YES"


@pytest.mark.asyncio
async def test_soft_relation_indexes(db_session):
    idx = await get_indexes(db_session, "soft_relation")
    assert "idx_soft_relation_pair" in idx
    assert "idx_soft_relation_active" in idx


@pytest.mark.asyncio
async def test_soft_relation_lifecycle_check(db_session):
    """lifecycle_state CHECK must cover 4 states."""
    checks = await get_check_constraints(db_session, "soft_relation")
    joined = " ".join(checks)
    for state in ("inferred", "active", "superseded", "retired"):
        assert state in joined
