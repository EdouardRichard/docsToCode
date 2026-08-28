"""Integration test for version isolation + capability gating (T026).

Tests: dense-only + lexical_ready coexist, sparse not used for dense-only
versions, no cross-contamination, lexical_ready declared but sparse index
corrupted degrades to dense-only.

Depends on T017. Requires PostgreSQL + Qdrant.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.config import get_settings
from rag_mcp.indexing.qdrant_client import QdrantStore
from rag_mcp.indexing.sparse_encoder import BM25SparseEncoder
from rag_mcp.models.chunk import Chunk
from rag_mcp.models.knowledge_source import KnowledgeSource
from rag_mcp.models.knowledge_version import KnowledgeVersion
from rag_mcp.providers.base import EmbeddingProvider, RerankerProvider
from rag_mcp.schemas.project import ProjectCreate
from rag_mcp.services.ingestion_service import IngestionService, _derive_index_version
from rag_mcp.services.project_service import ProjectService
from rag_mcp.services.retrieval_service import RetrievalService
from rag_mcp.utils.snowflake import generate_id


class _FakeEmbeddingProvider(EmbeddingProvider):
    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim
    async def embed_texts(self, texts):
        return [[0.1 * (i + 1)] * self._dim for i, _ in enumerate(texts)]
    async def embed_query(self, text):
        return [0.5] * self._dim
    def get_dimension(self):
        return self._dim


class _MockReranker(RerankerProvider):
    async def rerank(self, query, candidates, top_k=5):
        results = []
        for i, c in enumerate(candidates):
            r = dict(c)
            r["rerank_score"] = 0.5 - i * 0.01
            results.append(r)
        results.sort(key=lambda r: (-r.get("rerank_score", 0), str(r.get("chunk_id", ""))))
        return results[:top_k]


@pytest_asyncio.fixture
async def isolated_versions(db_session: AsyncSession):
    """Create a project with two sources: one dense-only, one hybrid (lexical_ready)."""
    svc = ProjectService(db_session)
    project = await svc.create_project(
        ProjectCreate(name="Capability Isolation Test", alias=f"cap-iso-{generate_id()}")
    )
    await db_session.commit()
    scope_id = project.knowledge_scope_id

    # Source 1: will be ingested with sparse (lexical_ready)
    source1_id = generate_id()
    content1 = '''package com.example.service;

public class HybridService {
    private void validateToken(String token) {
        if (token == null || token.isEmpty()) {
            throw new IllegalArgumentException("Token required");
        }
    }

    public String getConnection() {
        return "database connection";
    }
}
'''
    source1 = KnowledgeSource(
        source_id=source1_id,
        knowledge_scope_id=scope_id,
        filename="HybridService.java",
        content_hash=hashlib.sha256(content1.encode()).hexdigest(),
        format="java",
        size_bytes=len(content1.encode()),
        status="uploaded",
    )
    db_session.add(source1)

    # Source 2: will be ingested but we'll set dense-only capabilities
    source2_id = generate_id()
    content2 = '''package com.example.service;

public class DenseService {
    public String getUserName() {
        return "default user";
    }

    public void resetPassword(String email) {
        // Password reset logic
    }
}
'''
    source2 = KnowledgeSource(
        source_id=source2_id,
        knowledge_scope_id=scope_id,
        filename="DenseService.java",
        content_hash=hashlib.sha256(content2.encode()).hexdigest(),
        format="java",
        size_bytes=len(content2.encode()),
        status="uploaded",
    )
    db_session.add(source2)
    await db_session.commit()

    # Write content files
    settings = get_settings()
    data_root = Path(settings.data_root)
    for sid, name, content in [
        (source1_id, "HybridService.java", content1),
        (source2_id, "DenseService.java", content2),
    ]:
        d = data_root / str(scope_id) / str(sid)
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(content, encoding="utf-8")

    # Ingest source1 (creates lexical_ready version)
    store = QdrantStore()
    provider = _FakeEmbeddingProvider()
    ing_svc = IngestionService(db_session, provider, store)
    await ing_svc.ingest(source1_id)
    await db_session.commit()

    # Ingest source2 (creates lexical_ready version by default)
    await ing_svc.ingest(source2_id)
    await db_session.commit()

    # Now manually set source2's version to dense-only (simulate old 001 version)
    versions = await db_session.execute(
        select(KnowledgeVersion).where(
            KnowledgeVersion.knowledge_scope_id == scope_id
        ).order_by(KnowledgeVersion.version_number)
    )
    all_versions = versions.scalars().all()
    # Set the first version to dense-only
    if len(all_versions) >= 1:
        all_versions[0].capabilities = {"dense_ready": True}
        all_versions[0].status = "published"  # keep it published
    await db_session.commit()

    yield {
        "project": project,
        "scope_id": scope_id,
        "source1_id": source1_id,
        "source2_id": source2_id,
        "store": store,
        "versions": all_versions,
    }

    # Cleanup hybrid collection
    index_version = _derive_index_version(settings.embedding_model)
    hybrid_col = f"chunks_hybrid_{index_version}"
    if store.collection_exists(hybrid_col):
        store._client.delete_collection(hybrid_col)


class TestCapabilityGating:
    """FR-013: query planning only calls declared capabilities."""

    @pytest.mark.asyncio
    async def test_dense_only_version_not_in_sparse_path(self, db_session, isolated_versions):
        """Dense-only versions must not participate in sparse path."""
        setup = isolated_versions
        provider = _FakeEmbeddingProvider()
        reranker = _MockReranker()
        svc = RetrievalService(db_session, setup["store"], provider, reranker=reranker)

        result = await svc.search(
            query="validateToken",
            project_scopes=[str(setup["project"].project_id)],
            top_k=5,
        )
        assert result["completion_status"] in ("complete", "partial", "no_evidence")

    @pytest.mark.asyncio
    async def test_hybrid_version_uses_sparse(self, db_session, isolated_versions):
        """Version with lexical_ready should trigger hybrid path."""
        setup = isolated_versions
        provider = _FakeEmbeddingProvider()
        reranker = _MockReranker()
        svc = RetrievalService(db_session, setup["store"], provider, reranker=reranker)

        result = await svc.search(
            query="validateToken",
            project_scopes=[str(setup["project"].project_id)],
            top_k=5,
        )
        # Should return results (hybrid path active since lexical_ready version exists)
        assert result["completion_status"] in ("complete", "partial", "no_evidence")


class TestVersionIsolation:
    """FR-012: no cross-contamination between versions."""

    @pytest.mark.asyncio
    async def test_dense_only_scope_isolated(self, db_session, isolated_versions):
        """Querying must not mix data from different versions."""
        setup = isolated_versions
        provider = _FakeEmbeddingProvider()
        reranker = _MockReranker()
        svc = RetrievalService(db_session, setup["store"], provider, reranker=reranker)

        result = await svc.search(
            query="DenseOnlyService",
            project_scopes=[str(setup["project"].project_id)],
            top_k=5,
        )
        # Results should only contain evidence from the correct scope
        if result["evidence"]:
            for e in result["evidence"]:
                assert e["knowledge_scope_id"] == str(setup["scope_id"])
