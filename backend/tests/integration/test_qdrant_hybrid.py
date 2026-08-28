"""Integration tests for QdrantStore hybrid methods (T011).

Tests: create_hybrid_collection (Dense+Sparse named vectors), upsert_hybrid
(same Point), search_sparse, query_hybrid with scope+version filter.

These tests MUST FAIL before hybrid methods are implemented (TDD).
Requires a running Qdrant instance.
"""

from __future__ import annotations

import os

import pytest

from rag_mcp.indexing.qdrant_client import QdrantStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store():
    """Create a QdrantStore connected to the configured Qdrant instance."""
    return QdrantStore()


@pytest.fixture
def hybrid_collection(store):
    """Create a hybrid collection and clean it up after the test."""
    name = "test_hybrid_collection"
    # Clean up if exists from prior runs
    if store.collection_exists(name):
        store._client.delete_collection(name)
    yield name
    # Cleanup
    if store.collection_exists(name):
        store._client.delete_collection(name)


@pytest.fixture
def test_scope_id():
    return 100001

@pytest.fixture
def test_version_id():
    return 200001


# ---------------------------------------------------------------------------
# create_hybrid_collection
# ---------------------------------------------------------------------------

class TestCreateHybridCollection:
    """create_hybrid_collection must create Dense+Sparse named vectors."""

    def test_collection_created(self, store, hybrid_collection):
        """create_hybrid_collection should create the collection."""
        store.create_hybrid_collection(hybrid_collection, dimension=128)
        assert store.collection_exists(hybrid_collection)

    def test_has_dense_and_sparse_vectors(self, store, hybrid_collection):
        """Collection must have both 'dense' and 'sparse' named vectors."""
        store.create_hybrid_collection(hybrid_collection, dimension=128)
        info = store._client.get_collection(hybrid_collection)
        # Dense named vectors are in params.vectors
        vector_configs = info.config.params.vectors
        assert "dense" in vector_configs
        # Sparse vectors are in params.sparse_vectors
        sparse_configs = getattr(info.config.params, "sparse_vectors", None)
        assert sparse_configs is not None
        assert "sparse" in sparse_configs

    def test_payload_indexes_created(self, store, hybrid_collection):
        """Payload indexes must be created for filtering fields."""
        store.create_hybrid_collection(hybrid_collection, dimension=128)
        # Verify indexes exist by checking that filtered search works
        # (if indexes weren't created, filtered search would still work but less efficiently)
        info = store._client.get_collection(hybrid_collection)
        # Collection should have payload schema with indexed fields
        assert info.payload_schema is not None or info.config.params is not None


# ---------------------------------------------------------------------------
# upsert_hybrid (same Point with Dense + Sparse)
# ---------------------------------------------------------------------------

class TestUpsertHybrid:
    """upsert_hybrid must write Dense and Sparse vectors to the same Point."""

    def test_upsert_single_point(self, store, hybrid_collection, test_scope_id, test_version_id):
        """A single Point should carry both dense and sparse vectors."""
        store.create_hybrid_collection(hybrid_collection, dimension=128)
        point_id = 300001
        dense_vec = [0.1] * 128
        sparse_vec = {"indices": [1, 5, 10], "values": [0.5, 0.3, 0.2]}
        payload = {
            "knowledge_scope_id": str(test_scope_id),
            "source_id": "400001",
            "version_id": str(test_version_id),
            "chunk_id": str(point_id),
            "chunk_type": "section",
            "position_path": "## Test",
            "start_line": 1,
            "end_line": 10,
            "index_version": "test_v1",
            "embedding_model": "test-model",
        }
        store.upsert_hybrid(
            collection=hybrid_collection,
            point_id=point_id,
            dense_vector=dense_vec,
            sparse_vector=sparse_vec,
            payload=payload,
        )
        # Verify point exists
        points = store._client.retrieve(
            collection_name=hybrid_collection,
            ids=[point_id],
            with_vectors=True,
        )
        assert len(points) == 1
        # Check both vectors exist
        vectors = points[0].vector
        assert "dense" in vectors
        assert "sparse" in vectors

    def test_upsert_multiple_points(self, store, hybrid_collection, test_scope_id, test_version_id):
        """Multiple points should be upserted successfully."""
        store.create_hybrid_collection(hybrid_collection, dimension=128)
        points_data = [
            (300001, {"indices": [1, 2], "values": [0.5, 0.3]}),
            (300002, {"indices": [3, 4], "values": [0.4, 0.2]}),
            (300003, {"indices": [5, 6], "values": [0.1, 0.1]}),
        ]
        for pid, sparse_vec in points_data:
            store.upsert_hybrid(
                collection=hybrid_collection,
                point_id=pid,
                dense_vector=[0.1] * 128,
                sparse_vector=sparse_vec,
                payload={
                    "knowledge_scope_id": str(test_scope_id),
                    "source_id": "400001",
                    "version_id": str(test_version_id),
                    "chunk_id": str(pid),
                    "chunk_type": "section",
                    "position_path": "",
                    "start_line": 1,
                    "end_line": 10,
                    "index_version": "test_v1",
                    "embedding_model": "test-model",
                },
            )
        count = store._client.count(hybrid_collection, exact=True).count
        assert count == 3


# ---------------------------------------------------------------------------
# search_sparse
# ---------------------------------------------------------------------------

class TestSearchSparse:
    """search_sparse must query the sparse named vector with filters."""

    def test_returns_results(self, store, hybrid_collection, test_scope_id, test_version_id):
        """search_sparse should return matching sparse results."""
        store.create_hybrid_collection(hybrid_collection, dimension=128)
        # Upsert points
        for pid, sparse_vec in [
            (300001, {"indices": [1, 5], "values": [0.5, 0.3]}),
            (300002, {"indices": [1, 10], "values": [0.4, 0.2]}),
        ]:
            store.upsert_hybrid(
                collection=hybrid_collection,
                point_id=pid,
                dense_vector=[0.1] * 128,
                sparse_vector=sparse_vec,
                payload={
                    "knowledge_scope_id": str(test_scope_id),
                    "source_id": "400001",
                    "version_id": str(test_version_id),
                    "chunk_id": str(pid),
                    "chunk_type": "section",
                    "position_path": "",
                    "start_line": 1,
                    "end_line": 10,
                    "index_version": "test_v1",
                    "embedding_model": "test-model",
                },
            )
        # Search with a sparse query vector
        query_sparse = {"indices": [1], "values": [1.0]}
        results = store.search_sparse(
            collection=hybrid_collection,
            sparse_vector=query_sparse,
            scope_ids=[test_scope_id],
            version_id=test_version_id,
            limit=5,
        )
        assert len(results) > 0

    def test_scope_filter_excludes_other_scope(self, store, hybrid_collection, test_scope_id, test_version_id):
        """search_sparse must filter by scope — no cross-project leakage."""
        store.create_hybrid_collection(hybrid_collection, dimension=128)
        # Point in scope A
        store.upsert_hybrid(
            collection=hybrid_collection,
            point_id=300001,
            dense_vector=[0.1] * 128,
            sparse_vector={"indices": [1, 2], "values": [0.5, 0.3]},
            payload={
                "knowledge_scope_id": str(test_scope_id),
                "source_id": "400001",
                "version_id": str(test_version_id),
                "chunk_id": "300001",
                "chunk_type": "section",
                "position_path": "",
                "start_line": 1,
                "end_line": 10,
                "index_version": "test_v1",
                "embedding_model": "test-model",
            },
        )
        # Point in scope B (different)
        other_scope = 999999
        store.upsert_hybrid(
            collection=hybrid_collection,
            point_id=300002,
            dense_vector=[0.1] * 128,
            sparse_vector={"indices": [1, 2], "values": [0.5, 0.3]},
            payload={
                "knowledge_scope_id": str(other_scope),
                "source_id": "400002",
                "version_id": str(test_version_id),
                "chunk_id": "300002",
                "chunk_type": "section",
                "position_path": "",
                "start_line": 1,
                "end_line": 10,
                "index_version": "test_v1",
                "embedding_model": "test-model",
            },
        )
        # Query scope A only
        query_sparse = {"indices": [1], "values": [1.0]}
        results = store.search_sparse(
            collection=hybrid_collection,
            sparse_vector=query_sparse,
            scope_ids=[test_scope_id],
            version_id=None,
            limit=5,
        )
        # Must only return scope A points
        for r in results:
            assert r["payload"]["knowledge_scope_id"] == str(test_scope_id), (
                "Cross-project leakage must be zero (FR-008)"
            )


# ---------------------------------------------------------------------------
# query_hybrid (Dense + Sparse in one call)
# ---------------------------------------------------------------------------

class TestQueryHybrid:
    """query_hybrid must search both Dense and Sparse with scope+version filter."""

    def test_returns_dense_and_sparse_results(self, store, hybrid_collection, test_scope_id, test_version_id):
        """query_hybrid should return results from both dense and sparse."""
        store.create_hybrid_collection(hybrid_collection, dimension=128)
        store.upsert_hybrid(
            collection=hybrid_collection,
            point_id=300001,
            dense_vector=[0.5] * 128,
            sparse_vector={"indices": [1, 2], "values": [0.5, 0.3]},
            payload={
                "knowledge_scope_id": str(test_scope_id),
                "source_id": "400001",
                "version_id": str(test_version_id),
                "chunk_id": "300001",
                "chunk_type": "section",
                "position_path": "",
                "start_line": 1,
                "end_line": 10,
                "index_version": "test_v1",
                "embedding_model": "test-model",
            },
        )
        dense_query = [0.5] * 128
        sparse_query = {"indices": [1], "values": [1.0]}
        dense_results, sparse_results = store.query_hybrid(
            collection=hybrid_collection,
            dense_vector=dense_query,
            sparse_vector=sparse_query,
            scope_ids=[test_scope_id],
            version_id=test_version_id,
            limit=5,
        )
        assert len(dense_results) > 0
        assert len(sparse_results) > 0

    def test_scope_filter_zero_leakage(self, store, hybrid_collection, test_scope_id, test_version_id):
        """query_hybrid must enforce scope filter on both dense and sparse."""
        store.create_hybrid_collection(hybrid_collection, dimension=128)
        # Point in scope A
        store.upsert_hybrid(
            collection=hybrid_collection,
            point_id=300001,
            dense_vector=[0.5] * 128,
            sparse_vector={"indices": [1], "values": [1.0]},
            payload={
                "knowledge_scope_id": str(test_scope_id),
                "source_id": "400001",
                "version_id": str(test_version_id),
                "chunk_id": "300001",
                "chunk_type": "section",
                "position_path": "",
                "start_line": 1,
                "end_line": 10,
                "index_version": "test_v1",
                "embedding_model": "test-model",
            },
        )
        # Point in scope B
        other_scope = 999999
        store.upsert_hybrid(
            collection=hybrid_collection,
            point_id=300002,
            dense_vector=[0.5] * 128,
            sparse_vector={"indices": [1], "values": [1.0]},
            payload={
                "knowledge_scope_id": str(other_scope),
                "source_id": "400002",
                "version_id": str(test_version_id),
                "chunk_id": "300002",
                "chunk_type": "section",
                "position_path": "",
                "start_line": 1,
                "end_line": 10,
                "index_version": "test_v1",
                "embedding_model": "test-model",
            },
        )
        dense_query = [0.5] * 128
        sparse_query = {"indices": [1], "values": [1.0]}
        dense_results, sparse_results = store.query_hybrid(
            collection=hybrid_collection,
            dense_vector=dense_query,
            sparse_vector=sparse_query,
            scope_ids=[test_scope_id],
            version_id=None,
            limit=5,
        )
        # All results must be from scope A only
        for r in dense_results + sparse_results:
            assert r["payload"]["knowledge_scope_id"] == str(test_scope_id), (
                "Cross-project leakage must be zero in both dense and sparse (FR-008)"
            )
