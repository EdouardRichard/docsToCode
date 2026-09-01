"""Unit test for graph_edge migration (T004).

Validates the graph_edge table, columns, indexes, and CHECK constraints
exist after the migration is applied (data-model §2).

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
async def test_graph_edge_table_exists(db_session):
    assert await table_exists(db_session, "graph_edge")


@pytest.mark.asyncio
async def test_graph_edge_columns(db_session):
    cols = await get_columns(db_session, "graph_edge")
    # edge_id PK
    assert "edge_id" in cols
    assert cols["edge_id"]["data_type"] in ("bigint", "integer")
    assert cols["edge_id"]["is_nullable"] == "NO"
    # isolation triple
    for c in ("knowledge_scope_id", "project_id", "index_version"):
        assert c in cols, f"Missing column {c}"
        assert cols[c]["is_nullable"] == "NO"
    assert cols["knowledge_scope_id"]["data_type"] == "bigint"
    assert cols["project_id"]["data_type"] == "bigint"
    assert cols["index_version"]["data_type"] in ("integer", "bigint")
    # source/target chunk
    for c in ("source_chunk_id", "target_chunk_id"):
        assert c in cols
        assert cols[c]["data_type"] == "bigint"
        assert cols[c]["is_nullable"] == "NO"
    # relation_type, direction, is_hard, version
    assert cols["relation_type"]["is_nullable"] == "NO"
    assert cols["direction"]["is_nullable"] == "NO"
    assert cols["is_hard"]["data_type"] == "boolean"
    assert cols["is_hard"]["is_nullable"] == "NO"
    assert cols["version"]["data_type"] in ("integer", "bigint")
    assert cols["version"]["is_nullable"] == "NO"
    # parse_evidence JSONB
    assert cols["parse_evidence"]["data_type"] == "jsonb"
    assert cols["parse_evidence"]["is_nullable"] == "NO"
    # created_at
    assert "created_at" in cols
    assert cols["created_at"]["is_nullable"] == "NO"


@pytest.mark.asyncio
async def test_graph_edge_indexes(db_session):
    idx = await get_indexes(db_session, "graph_edge")
    assert "idx_graph_edge_source" in idx
    assert "idx_graph_edge_target" in idx
    assert "uniq_graph_edge" in idx


@pytest.mark.asyncio
async def test_graph_edge_relation_type_check(db_session):
    """relation_type CHECK must restrict to hard-relation enum."""
    checks = await get_check_constraints(db_session, "graph_edge")
    # At least one check constraint mentions the hard-relation types
    joined = " ".join(checks)
    assert "calls" in joined
    assert "fk_references" in joined
    assert "inferred" not in joined or "other_hard" in joined
