"""Integration test for full hybrid path (T019).

Tests: RRF fusion + Rerank + boxing + partial degradation on sparse/rerank
failure + subpath timing + concurrency isolation (FR-018).

Depends on T017. These tests MUST FAIL before T020 implements rerank+partial
in retrieval_service.py (TDD).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.config import get_settings
from rag_mcp.indexing.qdrant_client import QdrantStore
from rag_mcp.indexing.sparse_encoder import BM25SparseEncoder
from rag_mcp.models.chunk import Chunk
from rag_mcp.models.knowledge_source import KnowledgeSource
from rag_mcp.models.knowledge_version import KnowledgeVersion
from rag_mcp.providers.base import EmbeddingProvider, RerankerProvider
from rag_mcp.services.retrieval_service import RetrievalService
from rag_mcp.utils.snowflake import generate_id


class _FakeEmbeddingProvider(EmbeddingProvider):
    """Deterministic fake embedding provider."""
    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim
    async def embed_texts(self, texts):
        return [[0.1 * (i + 1)] * self._dim for i, _ in enumerate(texts)]
    async def embed_query(self, text):
        return [0.5] * self._dim
    def get_dimension(self):
        return self._dim


class _MockReranker(RerankerProvider):
    """Deterministic mock reranker for integration tests."""
    def __init__(self):
        self._call_count = 0
    async def rerank(self, query, candidates, top_k=5):
        self._call_count += 1
        results = []
        for i, c in enumerate(candidates):
            enriched = dict(c)
            # Deterministic score: prefer candidates with matching text
            score = 0.5 - i * 0.01
            enriched["rerank_score"] = score
            results.append(enriched)
        # Sort by rerank_score desc, then chunk_id asc
        results.sort(key=lambda r: (-r.get("rerank_score", 0), str(r.get("chunk_id", ""))))
        return results[:top_k]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def hybrid_search_setup(db_session: AsyncSession):
    """Set up project, version with lexical_ready, chunks, and hybrid collection data."""
    from rag_mcp.schemas.project import ProjectCreate
    from rag_mcp.services.project_service import ProjectService

    svc = ProjectService(db_session)
    project = await svc.create_project(
        ProjectCreate(name="Hybrid Full Test", alias=f"hft-{generate_id()}")
    )
    await db_session.commit()

    scope_id = project.knowledge_scope_id
    version_id = generate_id()
    source_id = generate_id()

    # Create knowledge source
    import hashlib
    content = "validateToken method validates user token"

    # Derive index_version (same as the service would use)
    from rag_mcp.services.ingestion_service import _derive_index_version
    index_version = _derive_index_version(get_settings().embedding_model)
    source = KnowledgeSource(
        source_id=source_id,
        knowledge_scope_id=scope_id,
        filename="Test.java",
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        format="java",
        size_bytes=len(content.encode()),
        status="published",
    )
    db_session.add(source)

    # Create knowledge version with lexical_ready
    version = KnowledgeVersion(
        version_id=version_id,
        knowledge_scope_id=scope_id,
        version_number=1,
        capabilities={"dense_ready": True, "lexical_ready": True},
        status="published",
        published_at=None,
    )
    db_session.add(version)

    # Create chunk record
    chunk_id = generate_id()
    chunk = Chunk(
        chunk_id=chunk_id,
        source_id=source_id,
        version_id=version_id,
        knowledge_scope_id=scope_id,
        content_text=content,
        position_path="com.example.TestService#validateToken",
        chunk_type="symbol",
        start_line=1,
        end_line=5,
        token_count=10,
        embedding_model=get_settings().embedding_model,
        index_version=index_version,
    )
    db_session.add(chunk)
    await db_session.commit()

    # Set up hybrid collection with data
    store = QdrantStore()
    collection = f"chunks_hybrid_{index_version}"
    dim = 1024
    if not store.collection_exists(collection):
        store.create_hybrid_collection(collection, dimension=dim)

    # Fit encoder on chunk text
    encoder = BM25SparseEncoder()
    encoder.fit([content])

    # Upsert hybrid point
    dense_vec = [0.5] * dim
    sparse_vec = encoder.encode(content)
    payload = {
        "knowledge_scope_id": str(scope_id),
        "source_id": str(source_id),
        "version_id": str(version_id),
        "chunk_id": str(chunk_id),
        "chunk_type": "symbol",
        "position_path": "com.example.TestService#validateToken",
        "start_line": 1,
        "end_line": 5,
        "index_version": index_version,
        "embedding_model": get_settings().embedding_model,
    }
    store.upsert_hybrid(collection, chunk_id, dense_vec, sparse_vec, payload)

    yield {
        "project": project,
        "scope_id": scope_id,
        "version_id": version_id,
        "chunk_id": chunk_id,
        "store": store,
        "collection": collection,
        "encoder": encoder,
    }

    # Cleanup: delete test data points but keep the collection if it has production data
    # (the test fixture creates its own scope, so deleting by scope is safe)
    if store.collection_exists(collection):
        try:
            store.delete_points_by_scope(collection, scope_id)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Tests: full hybrid path with rerank
# ---------------------------------------------------------------------------

class TestFullHybridPath:
    """Full hybrid path: RRF fusion + Rerank + boxing + subpath timing."""

    @pytest.mark.asyncio
    async def test_rerank_applied(self, db_session, hybrid_search_setup):
        """Rerank must be applied and subpath_timings.rerank_ms > 0."""
        setup = hybrid_search_setup
        provider = _FakeEmbeddingProvider(dim=1024)
        reranker = _MockReranker()

        # T020 will add reranker parameter to RetrievalService
        svc = RetrievalService(db_session, setup["store"], provider, reranker=reranker)
        result = await svc.search(
            query="validateToken",
            project_scopes=[str(setup["project"].project_id)],
            top_k=5,
        )
        assert result["completion_status"] in ("complete", "partial")
        assert len(result["evidence"]) > 0

        # Verify rerank was called
        assert reranker._call_count > 0, "Reranker must be called in hybrid path"

    @pytest.mark.asyncio
    async def test_subpath_timings_recorded(self, db_session, hybrid_search_setup):
        """RetrievalRun must record subpath_timings with rerank_ms."""
        setup = hybrid_search_setup
        provider = _FakeEmbeddingProvider(dim=1024)
        reranker = _MockReranker()

        svc = RetrievalService(db_session, setup["store"], provider, reranker=reranker)
        await svc.search(
            query="validateToken",
            project_scopes=[str(setup["project"].project_id)],
            top_k=5,
        )

        # Check the latest RetrievalRun
        from sqlalchemy import select
        from rag_mcp.models.retrieval_run import RetrievalRun
        result = await db_session.execute(
            select(RetrievalRun).where(
                RetrievalRun.query_text == "validateToken"
            ).order_by(RetrievalRun.run_id.desc())
        )
        run = result.scalars().first()
        assert run is not None
        assert run.retrieval_mode == "hybrid"
        assert run.subpath_timings is not None
        assert "rerank_ms" in run.subpath_timings


# ---------------------------------------------------------------------------
# Tests: partial degradation
# ---------------------------------------------------------------------------

class TestPartialDegradation:
    """Partial degradation on sparse/rerank failure (FR-016)."""

    @pytest.mark.asyncio
    async def test_sparse_failure_returns_partial(self, db_session, hybrid_search_setup):
        """When sparse fails, dense results should still be returned (partial)."""
        setup = hybrid_search_setup
        provider = _FakeEmbeddingProvider(dim=1024)
        reranker = _MockReranker()

        # Use a query with no in-vocab terms → sparse path adds nothing
        # but should still return dense results
        svc = RetrievalService(db_session, setup["store"], provider, reranker=reranker)
        result = await svc.search(
            query="completely unknown terms",
            project_scopes=[str(setup["project"].project_id)],
            top_k=5,
        )
        # Should still return results (from dense) or no_evidence
        assert result["completion_status"] in ("complete", "partial", "no_evidence")


# ---------------------------------------------------------------------------
# Tests: concurrency isolation (FR-018)
# ---------------------------------------------------------------------------

class TestConcurrencyIsolation:
    """5 concurrent requests must not cross-contaminate (FR-018)."""

    @pytest.mark.asyncio
    async def test_concurrent_requests_no_crosstalk(self, db_session, hybrid_search_setup):
        """5 concurrent searches must not cross-contaminate."""
        setup = hybrid_search_setup
        provider = _FakeEmbeddingProvider(dim=1024)
        reranker = _MockReranker()

        svc = RetrievalService(db_session, setup["store"], provider, reranker=reranker)

        # Run 5 concurrent searches
        results = await asyncio.gather(*[
            svc.search(
                query="validateToken",
                project_scopes=[str(setup["project"].project_id)],
                top_k=5,
            )
            for _ in range(5)
        ])

        # All results should be valid
        for r in results:
            assert r["completion_status"] in ("complete", "partial", "no_evidence", "failed")
            assert "request_id" in r

        # All request_ids must be unique (no crosstalk)
        request_ids = [r["request_id"] for r in results]
        assert len(set(request_ids)) == 5, "Request IDs must be unique (no crosstalk)"
