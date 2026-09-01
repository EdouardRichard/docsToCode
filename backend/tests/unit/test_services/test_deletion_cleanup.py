"""Unit tests for deletion/clear derived-data cleanup (T059 / FR-012 / US-4).

Verifies that purging a deleted source / cleared scope removes Qdrant points
(via QdrantStore.delete_points_by_source / delete_points_by_scope), deletes
the PostgreSQL chunks rows, and is idempotent / tolerant of Qdrant failures.

004 extension (T047): purge paths also stop graph retrieval and delete graph
derived data (graph_expansion_path before chunk FKs, then graph_edge and
soft_relation rows) per FR-016/AS5.3.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from rag_mcp.api.knowledge_sources import (
    _purge_scope_derived_data,
    _purge_source_derived_data,
)


def _select_result(rows: list[str]) -> MagicMock:
    """Build a mock SELECT result whose scalars().all() returns rows."""
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = list(rows)
    result.scalars.return_value = scalars
    return result


def _pairs_result(pairs: list[tuple]) -> MagicMock:
    """Build a mock result whose fetchall() returns (scope, version) pairs."""
    result = MagicMock()
    result.fetchall.return_value = list(pairs)
    return result


def _source_session(index_versions: list[str], pairs: list[tuple]) -> AsyncMock:
    """AsyncMock session answering the source-purge query sequence."""
    session = AsyncMock()

    def execute_side_effect(stmt, *args, **kwargs):
        text_sql = str(stmt)
        if "version_number" in text_sql and "knowledge_versions" in text_sql:
            return _pairs_result(pairs)
        if "index_version" in text_sql:
            return _select_result(index_versions)
        return MagicMock()

    session.execute = AsyncMock(side_effect=execute_side_effect)
    return session


def _scope_session(index_versions: list[str]) -> AsyncMock:
    session = AsyncMock()

    def execute_side_effect(stmt, *args, **kwargs):
        if "index_version" in str(stmt):
            return _select_result(index_versions)
        return MagicMock()

    session.execute = AsyncMock(side_effect=execute_side_effect)
    return session


def _executed_sql(session: AsyncMock) -> list[str]:
    return [str(call.args[0]) for call in session.execute.await_args_list]


class TestPurgeSourceDerivedData:
    @pytest.mark.asyncio
    async def test_removes_qdrant_points_and_pg_chunks(self):
        """Purge deletes Qdrant points for the source collection and PG chunks."""
        session = _source_session(["bge-m3_v1"], [(11, 1)])
        qdrant = MagicMock()

        await _purge_source_derived_data(session, 123, qdrant_store=qdrant)

        qdrant.delete_points_by_source.assert_called_once_with(
            "chunks_dense_bge-m3_v1", 123
        )

    @pytest.mark.asyncio
    async def test_source_purge_deletes_graph_relations(self):
        """004: source purge deletes graph rows of the source's versions."""
        session = _source_session(["bge-m3_v1"], [(11, 1)])
        qdrant = MagicMock()

        await _purge_source_derived_data(session, 123, qdrant_store=qdrant)

        sql = _executed_sql(session)
        assert any("graph_ready = false" in s for s in sql), (
            "graph retrieval must be stopped before deletion"
        )
        assert any("DELETE FROM graph_expansion_path" in s for s in sql), (
            "expansion paths must be purged before chunk FKs"
        )
        assert any("DELETE FROM graph_edge" in s for s in sql)
        assert any("DELETE FROM soft_relation" in s for s in sql)

    @pytest.mark.asyncio
    async def test_multiple_index_versions(self):
        """Purge deletes points from every affected collection."""
        session = _source_session(["a_v1", "b_v1"], [])
        qdrant = MagicMock()

        await _purge_source_derived_data(session, 456, qdrant_store=qdrant)

        assert qdrant.delete_points_by_source.call_count == 2
        qdrant.delete_points_by_source.assert_any_call("chunks_dense_a_v1", 456)
        qdrant.delete_points_by_source.assert_any_call("chunks_dense_b_v1", 456)

    @pytest.mark.asyncio
    async def test_no_chunks_skips_qdrant(self):
        """With no chunk rows there are no Qdrant points to delete."""
        session = _source_session([], [])
        qdrant = MagicMock()

        await _purge_source_derived_data(session, 789, qdrant_store=qdrant)

        qdrant.delete_points_by_source.assert_not_called()

    @pytest.mark.asyncio
    async def test_qdrant_failure_does_not_raise(self):
        """A Qdrant outage must not block PG chunk deletion (idempotent retry)."""
        session = _source_session(["bge-m3_v1"], [(11, 1)])
        qdrant = MagicMock()
        qdrant.delete_points_by_source.side_effect = RuntimeError("qdrant down")

        await _purge_source_derived_data(session, 123, qdrant_store=qdrant)

        sql = _executed_sql(session)
        assert any("DELETE FROM chunks" in s or "chunks" in s for s in sql)


class TestPurgeScopeDerivedData:
    @pytest.mark.asyncio
    async def test_removes_qdrant_points_and_pg_chunks(self):
        """Clearing a scope deletes all its Qdrant points and PG chunks."""
        session = _scope_session(["bge-m3_v1"])
        qdrant = MagicMock()

        await _purge_scope_derived_data(session, 999, qdrant_store=qdrant)

        qdrant.delete_points_by_scope.assert_called_once_with(
            "chunks_dense_bge-m3_v1", 999
        )

    @pytest.mark.asyncio
    async def test_scope_purge_deletes_graph_relations(self):
        """004: scope clear stops graph retrieval then deletes graph data."""
        session = _scope_session(["bge-m3_v1"])
        qdrant = MagicMock()

        await _purge_scope_derived_data(session, 999, qdrant_store=qdrant)

        sql = _executed_sql(session)
        assert any("graph_ready = false" in s for s in sql)
        assert any("DELETE FROM graph_expansion_path" in s for s in sql)
        assert any("DELETE FROM soft_relation" in s for s in sql)
        assert any("DELETE FROM graph_edge" in s for s in sql)
        # Mark-then-delete order: graph_ready update precedes deletions
        mark_idx = next(i for i, s in enumerate(sql) if "graph_ready = false" in s)
        del_idx = next(i for i, s in enumerate(sql) if "DELETE FROM graph_edge" in s)
        assert mark_idx < del_idx
