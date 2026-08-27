"""Unit tests for deletion/clear derived-data cleanup (T059 / FR-012 / US-4).

Verifies that purging a deleted source / cleared scope removes Qdrant points
(via ``QdrantStore.delete_points_by_source`` / ``delete_points_by_scope``) and
deletes the PostgreSQL ``chunks`` rows, and is idempotent / tolerant of Qdrant
failures.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from rag_mcp.api.knowledge_sources import (
    _purge_scope_derived_data,
    _purge_source_derived_data,
)


def _select_result(rows: list[str]) -> MagicMock:
    """Build a mock SELECT result whose ``scalars().all()`` returns *rows*."""
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = list(rows)
    result.scalars.return_value = scalars
    return result


class TestPurgeSourceDerivedData:
    @pytest.mark.asyncio
    async def test_removes_qdrant_points_and_pg_chunks(self):
        """Purge deletes Qdrant points for the source collection and PG chunks."""
        session = AsyncMock()
        session.execute.side_effect = [
            _select_result(["bge-m3_v1"]),  # SELECT distinct index_version
            MagicMock(),  # DELETE chunks
        ]
        qdrant = MagicMock()

        await _purge_source_derived_data(session, 123, qdrant_store=qdrant)

        qdrant.delete_points_by_source.assert_called_once_with(
            "chunks_dense_bge-m3_v1", 123
        )
        assert session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_multiple_index_versions(self):
        """Purge deletes points from every affected collection."""
        session = AsyncMock()
        session.execute.side_effect = [
            _select_result(["a_v1", "b_v1"]),
            MagicMock(),
        ]
        qdrant = MagicMock()

        await _purge_source_derived_data(session, 456, qdrant_store=qdrant)

        assert qdrant.delete_points_by_source.call_count == 2
        qdrant.delete_points_by_source.assert_any_call("chunks_dense_a_v1", 456)
        qdrant.delete_points_by_source.assert_any_call("chunks_dense_b_v1", 456)

    @pytest.mark.asyncio
    async def test_no_chunks_skips_qdrant(self):
        """With no chunk rows there are no Qdrant points to delete."""
        session = AsyncMock()
        session.execute.side_effect = [_select_result([]), MagicMock()]
        qdrant = MagicMock()

        await _purge_source_derived_data(session, 789, qdrant_store=qdrant)

        qdrant.delete_points_by_source.assert_not_called()
        assert session.execute.call_count == 2  # DELETE is a harmless no-op

    @pytest.mark.asyncio
    async def test_qdrant_failure_does_not_raise(self):
        """A Qdrant outage must not block PG chunk deletion (idempotent retry)."""
        session = AsyncMock()
        session.execute.side_effect = [
            _select_result(["bge-m3_v1"]),
            MagicMock(),
        ]
        qdrant = MagicMock()
        qdrant.delete_points_by_source.side_effect = RuntimeError("qdrant down")

        await _purge_source_derived_data(session, 123, qdrant_store=qdrant)

        assert session.execute.call_count == 2


class TestPurgeScopeDerivedData:
    @pytest.mark.asyncio
    async def test_removes_qdrant_points_and_pg_chunks(self):
        """Clearing a scope deletes all its Qdrant points and PG chunks."""
        session = AsyncMock()
        session.execute.side_effect = [
            _select_result(["bge-m3_v1"]),
            MagicMock(),
        ]
        qdrant = MagicMock()

        await _purge_scope_derived_data(session, 999, qdrant_store=qdrant)

        qdrant.delete_points_by_scope.assert_called_once_with(
            "chunks_dense_bge-m3_v1", 999
        )
        assert session.execute.call_count == 2
