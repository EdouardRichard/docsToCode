"""T089 regression guard: integration tests must not delete the shared eval collection.

The capability-isolation and rebuild-sparse fixtures ingest test data into the
shared `chunks_hybrid_{index_version}` collection (the same collection that
holds the committed eval corpus vectors). Their teardown MUST delete only their
own scope's points — never the whole collection — so a full `pytest` run does
not wipe the eval baseline (Constitution X fixed baseline + 002 SC-006 /
003 SC-010 / 004 SC-007 / 005 SC-008 eval reproducibility).

This test pins the scope-scoped cleanup contract directly at the QdrantStore
boundary: deleting one scope's points must leave another scope's points intact.
"""

from __future__ import annotations

import pytest

from rag_mcp.config import get_settings
from rag_mcp.indexing.qdrant_client import QdrantStore
from rag_mcp.services.ingestion_service import _derive_index_version
from rag_mcp.utils.snowflake import generate_id


def _hybrid_collection() -> str:
    return f"chunks_hybrid_{_derive_index_version(get_settings().embedding_model)}"


def _seed_sentinel(store: QdrantStore, collection: str, scope_id: int, point_id: int) -> None:
    """Seed a sentinel point simulating a foreign (eval-corpus) scope."""
    store.upsert_hybrid(
        collection=collection,
        point_id=point_id,
        dense_vector=[0.25] * 1024,
        sparse_vector={"indices": [0], "values": [1.0]},
        payload={
            "knowledge_scope_id": str(scope_id),
            "source_id": "0",
            "version_id": "0",
            "chunk_id": str(point_id),
            "chunk_type": "symbol",
            "position_path": "",
            "start_line": 0,
            "end_line": 0,
            "index_version": _derive_index_version(get_settings().embedding_model),
            "embedding_model": get_settings().embedding_model,
        },
    )


class TestSharedCollectionIsolation:
    """T089: scope-scoped cleanup must preserve other scopes and the collection."""

    def test_delete_points_by_scope_preserves_foreign_scope(self):
        store = QdrantStore()
        col = _hybrid_collection()
        if not store.collection_exists(col):
            store.create_hybrid_collection(col, dimension=1024)

        sentinel_scope = generate_id()
        sentinel_id = generate_id()
        _seed_sentinel(store, col, sentinel_scope, sentinel_id)

        try:
            # Simulate the fixture teardown for a DIFFERENT scope.
            other_scope = generate_id()
            store.delete_points_by_scope(col, other_scope)

            # The collection and the foreign-scope sentinel must survive.
            assert store.collection_exists(col), (
                "shared hybrid collection must not be deleted by scope-scoped cleanup"
            )
            dense_results, _sparse_results = store.query_hybrid(
                col,
                dense_vector=[0.25] * 1024,
                sparse_vector={"indices": [0], "values": [1.0]},
                scope_ids=[sentinel_scope],
                limit=5,
            )
            assert any(r["id"] == sentinel_id for r in dense_results), (
                "foreign-scope point must survive deleting another scope's points"
            )
        finally:
            # Self-clean the sentinel so the shared collection is left untouched.
            store.delete_points_by_scope(col, sentinel_scope)
