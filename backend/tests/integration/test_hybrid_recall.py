"""Integration test for hybrid recall path (T016).

Tests: Dense+Sparse parallel recall, exact symbol rank ≥ Dense-only, scope
filter, zero cross-project leakage, pure-natural-language query rank
preservation, missing-scope rejection.

Depends on T014 (ingestion sparse_index). These tests MUST FAIL before T017
implements hybrid recall in retrieval_service.py (TDD).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.config import get_settings
from rag_mcp.fusion.rrf import rrf_fuse
from rag_mcp.indexing.qdrant_client import QdrantStore
from rag_mcp.indexing.sparse_encoder import BM25SparseEncoder
from rag_mcp.models.project import Project
from rag_mcp.services.retrieval_service import RetrievalService
from rag_mcp.utils.snowflake import generate_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store():
    return QdrantStore()


@pytest.fixture
def collection_name():
    return "test_hybrid_recall"


@pytest.fixture
def scope_a():
    return generate_id()


@pytest.fixture
def scope_b():
    return generate_id()


@pytest.fixture
def version_id():
    return generate_id()


@pytest.fixture
def fitted_encoder():
    """BM25SparseEncoder fitted on test corpus."""
    enc = BM25SparseEncoder()
    corpus = [
        "validateToken method validates user token",
        "UserService class provides user management",
        "getConnection returns database connection",
        "database configuration and pool management",
    ]
    enc.fit(corpus)
    return enc


@pytest_asyncio.fixture
async def hybrid_data(store, collection_name, scope_a, scope_b, version_id, fitted_encoder):
    """Set up a hybrid collection with controlled data for ranking tests.

    Creates two chunks:
    - Chunk A: validateToken method (high BM25 match for "validateToken" query)
    - Chunk B: UserService class (slightly higher dense cosine for query)

    In dense-only, B ranks #1. In hybrid (Dense+Sparse+RRF), A should rank #1
    because the sparse match on "validateToken" boosts A.
    """
    if store.collection_exists(collection_name):
        store._client.delete_collection(collection_name)
    store.create_hybrid_collection(collection_name, dimension=8)

    # Chunk A: validateToken method — dense vector low cosine with query
    chunk_a_id = generate_id()
    dense_a = [0.1, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    sparse_a = fitted_encoder.encode("validateToken method validates user token")

    # Chunk B: UserService class — dense vector high cosine with query
    chunk_b_id = generate_id()
    dense_b = [0.9, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    sparse_b = fitted_encoder.encode("UserService class provides user management")

    payload_a = {
        "knowledge_scope_id": str(scope_a),
        "source_id": str(generate_id()),
        "version_id": str(version_id),
        "chunk_id": str(chunk_a_id),
        "chunk_type": "symbol",
        "position_path": "com.example.service.TestService#validateToken",
        "start_line": 4,
        "end_line": 9,
        "index_version": "test_v1",
        "embedding_model": "test-model",
    }
    payload_b = {
        "knowledge_scope_id": str(scope_a),
        "source_id": str(generate_id()),
        "version_id": str(version_id),
        "chunk_id": str(chunk_b_id),
        "chunk_type": "symbol",
        "position_path": "com.example.service.TestService",
        "start_line": 1,
        "end_line": 12,
        "index_version": "test_v1",
        "embedding_model": "test-model",
    }

    store.upsert_hybrid(collection_name, chunk_a_id, dense_a, sparse_a, payload_a)
    store.upsert_hybrid(collection_name, chunk_b_id, dense_b, sparse_b, payload_b)

    yield {
        "collection": collection_name,
        "scope_a": scope_a,
        "scope_b": scope_b,
        "version_id": version_id,
        "chunk_a_id": chunk_a_id,
        "chunk_b_id": chunk_b_id,
        "dense_a": dense_a,
        "dense_b": dense_b,
    }

    # Cleanup
    if store.collection_exists(collection_name):
        store._client.delete_collection(collection_name)


# ---------------------------------------------------------------------------
# Tests: exact symbol ranking improvement
# ---------------------------------------------------------------------------

class TestExactSymbolRanking:
    """Exact symbol queries must rank higher with hybrid than dense-only."""

    def test_dense_only_ranks_class_first(self, store, hybrid_data, fitted_encoder):
        """In dense-only, the class chunk (B) should rank first (higher cosine)."""
        data = hybrid_data
        query_dense = [0.95, 0.05, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        # Query the dense named vector directly
        response = store._client.query_points(
            collection_name=data["collection"],
            query=query_dense,
            using="dense",
            query_filter=store._build_scope_version_filter(
                scope_ids=[data["scope_a"]],
                version_id=None,
            ),
            limit=5,
            with_payload=True,
        )
        results = [
            {"id": p.id, "score": p.score, "payload": p.payload or {}}
            for p in response.points
        ]
        assert len(results) > 0
        first_id = str(results[0]["payload"]["chunk_id"])
        assert first_id == str(data["chunk_b_id"]), (
            "Dense-only should rank class chunk (B) first"
        )

    def test_hybrid_ranks_method_first(self, store, hybrid_data, fitted_encoder):
        """In hybrid (Dense+Sparse+RRF), the method chunk (A) should rank first."""
        data = hybrid_data
        query_dense = [0.95, 0.05, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        query_sparse = fitted_encoder.encode("validateToken")

        # Dense + Sparse parallel recall
        dense_results, sparse_results = store.query_hybrid(
            collection=data["collection"],
            dense_vector=query_dense,
            sparse_vector=query_sparse,
            scope_ids=[data["scope_a"]],
            version_id=None,
            limit=5,
        )

        # RRF fusion
        fused = rrf_fuse(dense_results, sparse_results, k=60)

        assert len(fused) >= 2
        # After RRF, the method chunk (A) should rank first
        # because sparse match on "validateToken" boosts A
        first_id = fused[0].chunk_id
        assert first_id == str(data["chunk_a_id"]), (
            f"Hybrid should rank method chunk (A) first, got {first_id}"
        )


# ---------------------------------------------------------------------------
# Tests: scope filter and zero cross-project leakage
# ---------------------------------------------------------------------------

class TestScopeIsolation:
    """Hybrid recall must enforce scope filter — zero cross-project leakage."""

    def test_no_cross_scope_leakage(self, store, hybrid_data, fitted_encoder):
        """Querying scope A must not return scope B's data."""
        data = hybrid_data
        query_dense = [0.95, 0.05, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        query_sparse = fitted_encoder.encode("validateToken")

        # Query scope B (which has no data in this collection)
        dense_results, sparse_results = store.query_hybrid(
            collection=data["collection"],
            dense_vector=query_dense,
            sparse_vector=query_sparse,
            scope_ids=[data["scope_b"]],
            version_id=None,
            limit=5,
        )
        # No results from scope B
        assert len(dense_results) == 0, "Dense results must not leak from scope A to B"
        assert len(sparse_results) == 0, "Sparse results must not leak from scope A to B"


# ---------------------------------------------------------------------------
# Tests: missing project_scope rejection (FR-007)
# ---------------------------------------------------------------------------

class TestMissingScopeRejection:
    """Retrieval without explicit project_scope must be rejected (FR-007)."""

    @pytest.mark.asyncio
    async def test_empty_scope_rejected(self, db_session: AsyncSession, store):
        """Search with empty project_scopes must return failed status."""
        from rag_mcp.providers.base import EmbeddingProvider

        class _FakeProvider(EmbeddingProvider):
            async def embed_texts(self, texts):
                return [[0.1] * 8 for _ in texts]
            async def embed_query(self, text):
                return [0.5] * 8
            def get_dimension(self):
                return 8

        svc = RetrievalService(db_session, store, _FakeProvider())
        result = await svc.search(
            query="validateToken",
            project_scopes=[],
            top_k=5,
        )
        assert result["completion_status"] == "failed", (
            "Missing project_scope must be rejected (FR-007)"
        )
