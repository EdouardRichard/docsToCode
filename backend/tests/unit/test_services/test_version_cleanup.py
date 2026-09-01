"""Unit tests for supersede-time derived-data cleanup (orphan-vector fix).

Covers:
- ``IngestionService._cleanup_version_derived_data``: purging a superseded
  version's Qdrant points (by version_id) and PG chunks.
- ``QdrantStore.delete_points_by_version``: the version_id payload filter.
- ``LocalCPUEmbeddingProvider.warmup``: eager model load trigger.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from rag_mcp.services.ingestion_service import IngestionService


def _select_result(rows: list[str]) -> MagicMock:
    """Build a mock SELECT result whose ``scalars().all()`` returns *rows*."""
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = list(rows)
    result.scalars.return_value = scalars
    return result


class TestCleanupVersionDerivedData:
    def _svc(self, session, qdrant) -> IngestionService:
        return IngestionService(
            session=session, embedding_provider=None, qdrant_store=qdrant
        )

    @pytest.mark.asyncio
    async def test_deletes_qdrant_points_and_pg_chunks(self):
        """Supersede cleanup deletes the version's Qdrant points and PG chunks."""
        session = AsyncMock()
        session.execute.side_effect = [
            _select_result(["bge-m3_v1"]),  # SELECT distinct index_version
            MagicMock(),  # DELETE chunks
        ]
        qdrant = MagicMock()

        await self._svc(session, qdrant)._cleanup_version_derived_data(777)

        qdrant.delete_points_by_version.assert_called_once_with(
            "chunks_hybrid_bge-m3_v1", 777
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

        await self._svc(session, qdrant)._cleanup_version_derived_data(888)

        assert qdrant.delete_points_by_version.call_count == 2
        qdrant.delete_points_by_version.assert_any_call("chunks_hybrid_a_v1", 888)
        qdrant.delete_points_by_version.assert_any_call("chunks_hybrid_b_v1", 888)

    @pytest.mark.asyncio
    async def test_qdrant_failure_does_not_block_pg_cleanup(self):
        """A Qdrant outage must not block PG chunk deletion (idempotent retry)."""
        session = AsyncMock()
        session.execute.side_effect = [
            _select_result(["bge-m3_v1"]),
            MagicMock(),
        ]
        qdrant = MagicMock()
        qdrant.delete_points_by_version.side_effect = RuntimeError("qdrant down")

        await self._svc(session, qdrant)._cleanup_version_derived_data(999)

        assert session.execute.call_count == 2


class TestDeletePointsByVersion:
    def test_filters_by_version_id_string(self):
        """delete_points_by_version issues a delete filtered on string version_id."""
        from rag_mcp.indexing.qdrant_client import QdrantStore

        store = QdrantStore.__new__(QdrantStore)  # bypass __init__ (no client connect)
        store._client = MagicMock()

        store.delete_points_by_version("coll", 123)

        store._client.delete.assert_called_once()
        _, kwargs = store._client.delete.call_args
        assert kwargs["collection_name"] == "coll"
        selector = kwargs["points_selector"]
        assert len(selector.must) == 1
        assert selector.must[0].key == "version_id"
        assert selector.must[0].match.value == "123"


class TestLocalCPUWarmup:
    def test_warmup_triggers_model_load(self):
        """warmup() eagerly loads the model so first request does not block."""
        from rag_mcp.providers.local_cpu import LocalCPUEmbeddingProvider

        provider = LocalCPUEmbeddingProvider(model_name="dummy")
        provider._ensure_model = MagicMock()  # type: ignore[method-assign]

        provider.warmup()

        provider._ensure_model.assert_called_once()
