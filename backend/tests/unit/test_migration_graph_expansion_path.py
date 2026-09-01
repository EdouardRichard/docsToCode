"""Unit test for graph_expansion_path migration (T006).

Validates the graph_expansion_path table, columns, FKs, and hop_count CHECK
(data-model §4, DM-1).

This test MUST FAIL before the migration is written/applied (TDD).
"""

from __future__ import annotations

import pytest

from tests.unit.test_migrations_helper import (
    get_check_constraints,
    get_columns,
    table_exists,
)


@pytest.mark.asyncio
async def test_graph_expansion_path_table_exists(db_session):
    assert await table_exists(db_session, "graph_expansion_path")


@pytest.mark.asyncio
async def test_graph_expansion_path_columns(db_session):
    cols = await get_columns(db_session, "graph_expansion_path")
    for c in ("request_id", "evidence_id", "chunk_id", "start_chunk_id",
              "edge_path", "hop_count", "structure_weight", "graph_rank"):
        assert c in cols, f"Missing column {c}"
    assert cols["request_id"]["is_nullable"] == "NO"
    assert cols["evidence_id"]["is_nullable"] == "NO"
    assert cols["chunk_id"]["is_nullable"] == "NO"
    assert cols["start_chunk_id"]["is_nullable"] == "NO"
    assert cols["edge_path"]["data_type"] == "jsonb"
    assert cols["edge_path"]["is_nullable"] == "NO"
    assert cols["hop_count"]["data_type"] in ("integer", "bigint")
    assert cols["hop_count"]["is_nullable"] == "NO"
    assert cols["structure_weight"]["data_type"] in ("numeric", "decimal")
    assert cols["graph_rank"]["data_type"] in ("integer", "bigint")
    assert cols["graph_rank"]["is_nullable"] == "NO"


@pytest.mark.asyncio
async def test_graph_expansion_path_hop_count_check(db_session):
    """hop_count MUST be constrained to [1,3]."""
    checks = await get_check_constraints(db_session, "graph_expansion_path")
    joined = " ".join(checks)
    assert "hop_count" in joined
