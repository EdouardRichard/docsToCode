"""Integration test for rebuild with sparse_index (T027).

Tests: reprocess triggers full rebuild including sparse_index, old version
stays searchable during rebuild, derived indexes rebuildable from source.

Depends on T015. Requires PostgreSQL + Qdrant.
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
from rag_mcp.models.knowledge_source import KnowledgeSource
from rag_mcp.models.knowledge_version import KnowledgeVersion
from rag_mcp.models.processing_run import ProcessingRun
from rag_mcp.providers.base import EmbeddingProvider
from rag_mcp.schemas.project import ProjectCreate
from rag_mcp.services.ingestion_service import IngestionService, _derive_index_version
from rag_mcp.services.project_service import ProjectService
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


@pytest_asyncio.fixture
async def rebuild_setup(db_session: AsyncSession):
    """Create a project, source, and ingest it."""
    svc = ProjectService(db_session)
    project = await svc.create_project(
        ProjectCreate(name="Rebuild Test", alias=f"rebuild-{generate_id()}")
    )
    await db_session.commit()
    scope_id = project.knowledge_scope_id
    source_id = generate_id()

    content = '''package com.example;

public class RebuildService {
    public void validateToken(String token) {
        if (token == null) throw new IllegalArgumentException("required");
    }
    public String getConfig() { return "config"; }
}
'''
    source = KnowledgeSource(
        source_id=source_id,
        knowledge_scope_id=scope_id,
        filename="RebuildService.java",
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        format="java",
        size_bytes=len(content.encode()),
        status="uploaded",
    )
    db_session.add(source)
    await db_session.commit()

    # Write content file
    settings = get_settings()
    data_root = Path(settings.data_root)
    file_dir = data_root / str(scope_id) / str(source_id)
    file_dir.mkdir(parents=True, exist_ok=True)
    (file_dir / "RebuildService.java").write_text(content, encoding="utf-8")

    # Initial ingestion
    store = QdrantStore()
    provider = _FakeEmbeddingProvider()
    ing_svc = IngestionService(db_session, provider, store)
    await ing_svc.ingest(source_id)
    await db_session.commit()

    yield {
        "project": project,
        "scope_id": scope_id,
        "source_id": source_id,
        "store": store,
        "ing_svc": ing_svc,
    }

    # Cleanup this test scope's points only, preserving the shared eval corpus (T089)
    index_version = _derive_index_version(settings.embedding_model)
    hybrid_col = f"chunks_hybrid_{index_version}"
    if store.collection_exists(hybrid_col):
        store.delete_points_by_scope(hybrid_col, scope_id)


class TestRebuildSparse:
    """FR-014: rebuild re-runs all stages including sparse_index."""

    @pytest.mark.asyncio
    async def test_reprocess_creates_new_version_with_lexical_ready(self, db_session, rebuild_setup):
        """Reprocess should create a new version with lexical_ready=true."""
        setup = rebuild_setup
        await setup["ing_svc"].reprocess(setup["source_id"])
        await db_session.commit()

        # Check new version has lexical_ready
        result = await db_session.execute(
            select(KnowledgeVersion).where(
                KnowledgeVersion.knowledge_scope_id == setup["scope_id"]
            ).order_by(KnowledgeVersion.version_number.desc())
        )
        versions = result.scalars().all()
        assert len(versions) >= 1
        latest = versions[0]
        assert latest.capabilities.get("lexical_ready") is True, (
            "Reprocessed version must have lexical_ready=true (FR-014)"
        )

    @pytest.mark.asyncio
    async def test_reprocess_includes_sparse_index_stage(self, db_session, rebuild_setup):
        """Reprocess ProcessingRun must include sparse_index stage."""
        setup = rebuild_setup
        await setup["ing_svc"].reprocess(setup["source_id"])
        await db_session.commit()

        result = await db_session.execute(
            select(ProcessingRun).where(
                ProcessingRun.source_id == setup["source_id"]
            ).order_by(ProcessingRun.run_id.desc())
        )
        run = result.scalars().first()
        assert run is not None
        stages = run.stages or []
        stage_names = [s.get("stage") for s in stages]
        assert "sparse_index" in stage_names, (
            f"Reprocess must include sparse_index stage, got: {stage_names}"
        )

    @pytest.mark.asyncio
    async def test_reprocess_old_version_superseded(self, db_session, rebuild_setup):
        """After reprocess, the old version should be superseded (FR-023)."""
        setup = rebuild_setup
        # Get the old version number
        result_before = await db_session.execute(
            select(KnowledgeVersion).where(
                KnowledgeVersion.knowledge_scope_id == setup["scope_id"],
                KnowledgeVersion.status == "published",
            )
        )
        old_version = result_before.scalars().first()
        assert old_version is not None
        old_version_number = old_version.version_number

        # Reprocess
        await setup["ing_svc"].reprocess(setup["source_id"])
        await db_session.commit()

        # Old version should be superseded
        result_after = await db_session.execute(
            select(KnowledgeVersion).where(
                KnowledgeVersion.version_id == old_version.version_id
            )
        )
        old_version_after = result_after.scalars().first()
        assert old_version_after.status == "superseded", (
            "Old version must be superseded after reprocess (FR-023)"
        )
