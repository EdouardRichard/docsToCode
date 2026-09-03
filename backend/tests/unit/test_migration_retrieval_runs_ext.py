"""Unit tests for migration 0061_extend_retrieval_runs (T005, RED first).

Reflection assertions for the retrieval_runs column extension (data-model
§4.1): tool / instance_id / instance_mode / error_summary /
trace_body_recorded / provider_usage columns, query_text made nullable, and
the aggregation composite indexes.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.unit.test_migrations_helper import get_check_constraints, get_columns, table_exists


async def _index_defs(session, table_name: str) -> dict[str, str]:
    result = await session.execute(
        text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname='public' AND tablename=:t"
        ),
        {"t": table_name},
    )
    return {row[0]: row[1] for row in result}


async def _columns_of_index(session, index_name: str) -> list[str]:
    """Ordered column list of an index (indkey subscript order)."""
    result = await session.execute(
        text(
            "SELECT a.attname FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid "
            "JOIN LATERAL generate_subscripts(i.indkey, 1) AS sub ON true "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = i.indkey[sub] "
            "WHERE c.relname = :idx "
            "ORDER BY sub"
        ),
        {"idx": index_name},
    )
    return [row[0] for row in result]


@pytest.mark.asyncio
async def test_new_columns_exist(db_session):
    assert await table_exists(db_session, "retrieval_runs"), "retrieval_runs must exist"
    cols = await get_columns(db_session, "retrieval_runs")
    for name in (
        "tool",
        "instance_id",
        "instance_mode",
        "error_summary",
        "trace_body_recorded",
        "provider_usage",
    ):
        assert name in cols, f"retrieval_runs.{name} missing (migration 0061 not applied)"


@pytest.mark.asyncio
async def test_tool_column_definition(db_session):
    cols = await get_columns(db_session, "retrieval_runs")
    if "tool" not in cols:
        pytest.fail("retrieval_runs.tool missing (migration 0061 not applied)")
    assert cols["tool"]["data_type"] == "character varying"
    assert cols["tool"]["char_max_length"] == 16
    assert cols["tool"]["is_nullable"] == "NO"
    assert "search_knowledge" in str(cols["tool"]["column_default"])


@pytest.mark.asyncio
async def test_tool_check_constraint(db_session):
    checks = await get_check_constraints(db_session, "retrieval_runs")
    joined = " | ".join(checks)
    assert "tool" in joined
    assert "search_knowledge" in joined and "get_evidence" in joined


@pytest.mark.asyncio
async def test_instance_columns_nullable(db_session):
    cols = await get_columns(db_session, "retrieval_runs")
    if "instance_id" not in cols or "instance_mode" not in cols:
        pytest.fail("instance columns missing (migration 0061 not applied)")
    assert cols["instance_id"]["data_type"] == "uuid"
    assert cols["instance_id"]["is_nullable"] == "YES"
    assert cols["instance_mode"]["data_type"] == "character varying"
    assert cols["instance_mode"]["char_max_length"] == 8
    assert cols["instance_mode"]["is_nullable"] == "YES"
    # instance_mode CHECK IN (writer, reader) — NULL allowed for legacy rows
    checks = await get_check_constraints(db_session, "retrieval_runs")
    joined = " | ".join(checks)
    assert "instance_mode" in joined and "writer" in joined and "reader" in joined


@pytest.mark.asyncio
async def test_query_text_nullable(db_session):
    """query_text: former NOT NULL -> NULLABLE (FR-018 trace-body switch)."""
    cols = await get_columns(db_session, "retrieval_runs")
    assert cols["query_text"]["is_nullable"] == "YES", (
        "migration 0061 must make query_text nullable"
    )


@pytest.mark.asyncio
async def test_trace_body_recorded_default_true(db_session):
    cols = await get_columns(db_session, "retrieval_runs")
    if "trace_body_recorded" not in cols:
        pytest.fail("trace_body_recorded missing (migration 0061 not applied)")
    assert cols["trace_body_recorded"]["data_type"] == "boolean"
    assert cols["trace_body_recorded"]["is_nullable"] == "NO"
    assert "true" in str(cols["trace_body_recorded"]["column_default"]).lower()


@pytest.mark.asyncio
async def test_jsonb_columns_nullable(db_session):
    cols = await get_columns(db_session, "retrieval_runs")
    for name in ("error_summary", "provider_usage"):
        assert name in cols, f"{name} missing (migration 0061 not applied)"
        assert cols[name]["data_type"] == "jsonb"
        assert cols[name]["is_nullable"] == "YES"


@pytest.mark.asyncio
async def test_aggregation_composite_indexes(db_session):
    """(instance_mode, tool, created_at) and (completion_status, created_at)."""
    if "instance_mode" not in await get_columns(db_session, "retrieval_runs"):
        pytest.fail("instance_mode missing (migration 0061 not applied)")
    defs = await _index_defs(db_session, "retrieval_runs")
    mode_tool_idx = [
        name
        for name, d in defs.items()
        if "instance_mode" in d and "tool" in d and "created_at" in d
    ]
    assert mode_tool_idx, (
        f"(instance_mode, tool, created_at) composite index missing: {list(defs)}"
    )
    cols = await _columns_of_index(db_session, mode_tool_idx[0])
    assert cols == ["instance_mode", "tool", "created_at"]

    status_idx = [
        name
        for name, d in defs.items()
        if "completion_status" in d and "created_at" in d
    ]
    assert status_idx, (
        f"(completion_status, created_at) composite index missing: {list(defs)}"
    )
    cols = await _columns_of_index(db_session, status_idx[0])
    assert cols == ["completion_status", "created_at"]
