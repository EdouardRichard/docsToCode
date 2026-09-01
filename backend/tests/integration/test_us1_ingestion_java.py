"""Integration test for US1: Java call-graph extraction at ingest (T020).

Validates that Java source ingestion triggers call-graph extraction and
writes graph_edge records with isolation fields (FR-001/FR-010, Constitution I).

This test MUST FAIL before the extraction hook is wired into ingestion (TDD).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.graph.store.base import GraphScope
from rag_mcp.graph.store.postgres_graph_store import PostgresGraphStore
from rag_mcp.utils.snowflake import generate_id

# Reuse test helpers
from tests.unit.test_postgres_graph_store import (
    _insert_chunk,
    _insert_edge,
    _setup_scope,
)


_JAVA_SOURCE = """package com.example;

public class Calculator {
    public int compute(int x) {
        return square(add(x, 1));
    }
    public int add(int a, int b) {
        return a + b;
    }
    public int square(int x) {
        return x * x;
    }
}
"""


@pytest.fixture
async def java_scope(db_session):
    """Set up a scope with Java chunks."""
    scope_id = generate_id()
    project_id = generate_id()
    version_id = generate_id()
    source_id = await _setup_scope(db_session, scope_id, project_id, version_id)

    # Create chunks matching the Java source
    chunks = [
        {"chunk_id": generate_id(), "symbol_path": "com.example.Calculator",
         "symbol_type": "class", "content_text": _JAVA_SOURCE, "start_line": 3, "end_line": 14},
        {"chunk_id": generate_id(), "symbol_path": "com.example.Calculator#compute",
         "symbol_type": "method", "content_text": "compute body", "start_line": 4, "end_line": 6},
        {"chunk_id": generate_id(), "symbol_path": "com.example.Calculator#add",
         "symbol_type": "method", "content_text": "add body", "start_line": 7, "end_line": 9},
        {"chunk_id": generate_id(), "symbol_path": "com.example.Calculator#square",
         "symbol_type": "method", "content_text": "square body", "start_line": 10, "end_line": 12},
    ]
    for c in chunks:
        await _insert_chunk(db_session, c["chunk_id"], scope_id, version_id, source_id)

    await db_session.commit()
    return {
        "scope": GraphScope(scope_id, project_id, 1),
        "scope_id": scope_id,
        "project_id": project_id,
        "chunks": chunks,
        "source_id": source_id,
    }


@pytest.mark.asyncio
async def test_java_ingestion_writes_graph_edges(db_session, java_scope):
    """Ingesting Java source MUST write calls/called_by edges to graph_edge."""
    from rag_mcp.graph.extractors.java_call_graph import JavaCallGraphExtractor

    scope = java_scope["scope"]
    extractor = JavaCallGraphExtractor()
    edges = extractor.extract(_JAVA_SOURCE, java_scope["chunks"], scope)
    assert len(edges) > 0, "Expected graph edges from Java call graph"

    # Write edges to DB
    store = PostgresGraphStore(db_session)
    count = await store.write_edges(edges, scope)
    await db_session.commit()

    assert count > 0, "Expected edges written to graph_edge"

    # Verify edges exist in DB with isolation fields
    result = await db_session.execute(text(
        "SELECT relation_type, source_chunk_id, target_chunk_id, "
        "is_hard, knowledge_scope_id, project_id, index_version "
        "FROM graph_edge WHERE knowledge_scope_id = :ksid"
    ), {"ksid": java_scope["scope_id"]})
    rows = result.fetchall()
    assert len(rows) > 0

    for row in rows:
        rel_type, src, tgt, is_hard, ksid, pid, iv = row
        assert rel_type in ("calls", "called_by")
        assert is_hard is True
        assert ksid == java_scope["scope_id"]
        assert pid == java_scope["project_id"]
        assert iv == 1

    # Verify both calls and called_by exist
    rel_types = {row[0] for row in rows}
    assert "calls" in rel_types
    assert "called_by" in rel_types


@pytest.mark.asyncio
async def test_ast_degradation_records_reason(db_session, java_scope):
    """AST failure MUST not fabricate edges (Constitution III)."""
    from rag_mcp.graph.extractors.java_call_graph import JavaCallGraphExtractor

    scope = java_scope["scope"]
    extractor = JavaCallGraphExtractor()
    # Invalid Java source -> AST failure -> no edges
    edges = extractor.extract("this is not valid java {{{", java_scope["chunks"], scope)
    assert len(edges) == 0, "AST failure must not fabricate edges"


@pytest.mark.asyncio
async def test_isolation_triple_on_edges(db_session, java_scope):
    """Graph edges MUST carry the full isolation triple (FR-010)."""
    from rag_mcp.graph.extractors.java_call_graph import JavaCallGraphExtractor

    scope = java_scope["scope"]
    extractor = JavaCallGraphExtractor()
    edges = extractor.extract(_JAVA_SOURCE, java_scope["chunks"], scope)
    store = PostgresGraphStore(db_session)
    await store.write_edges(edges, scope)
    await db_session.commit()

    # Verify isolation triple on all edges
    result = await db_session.execute(text(
        "SELECT knowledge_scope_id, project_id, index_version, parse_evidence "
        "FROM graph_edge WHERE knowledge_scope_id = :ksid"
    ), {"ksid": java_scope["scope_id"]})
    for row in result:
        ksid, pid, iv, pe = row
        assert ksid == java_scope["scope_id"]
        assert pid == java_scope["project_id"]
        assert iv == 1
        assert pe is not None

class TestIngestPipelineGraphWiring:
    """T042: the ingestion pipeline itself MUST trigger call-graph extraction.

    Unlike the extractor-level tests above, these drive a real
    IngestionService.ingest() end-to-end (FR-001: hard relations are
    extracted *in the ingestion flow*), with a fake embedding provider and an
    in-memory Qdrant stand-in.
    """

    @pytest.mark.asyncio
    async def test_ingest_java_source_writes_graph_edges(self, db_session):
        from rag_mcp.services.ingestion_service import IngestionService
        from tests.integration.graph_ingest_helpers import (
            FakeEmbeddingProvider,
            MockQdrantStore,
            setup_graph_scope,
            upload_source_file,
        )

        scope_id = generate_id()
        project_id = generate_id()
        source_id = generate_id()
        await setup_graph_scope(db_session, scope_id, project_id)
        await upload_source_file(
            db_session, scope_id, source_id, "Calculator.java", _JAVA_SOURCE, "java"
        )
        await db_session.commit()

        svc = IngestionService(db_session, FakeEmbeddingProvider(), MockQdrantStore())
        await svc.ingest(source_id)

        rows = (await db_session.execute(text(
            "SELECT relation_type, is_hard, knowledge_scope_id, project_id, "
            "index_version, parse_evidence "
            "FROM graph_edge WHERE knowledge_scope_id = :k"
        ), {"k": scope_id})).fetchall()
        assert rows, "ingest() MUST write graph_edge rows for Java sources (T042)"

        rel_types = {r[0] for r in rows}
        assert "calls" in rel_types and "called_by" in rel_types
        for rel_type, is_hard, ksid, pid, iv, pe in rows:
            assert is_hard is True
            assert ksid == scope_id
            assert pid == project_id
            assert iv == 1, "graph index_version MUST equal the version_number"
            assert pe.get("extractor") == "java_call_graph"

    @pytest.mark.asyncio
    async def test_ingest_records_graph_relations_stage(self, db_session):
        """ProcessingRun.stages MUST include the graph_relations stage (FR-026)."""
        import json

        from rag_mcp.services.ingestion_service import IngestionService
        from tests.integration.graph_ingest_helpers import (
            FakeEmbeddingProvider,
            MockQdrantStore,
            setup_graph_scope,
            upload_source_file,
        )

        scope_id = generate_id()
        project_id = generate_id()
        source_id = generate_id()
        await setup_graph_scope(db_session, scope_id, project_id)
        await upload_source_file(
            db_session, scope_id, source_id, "Calculator.java", _JAVA_SOURCE, "java"
        )
        await db_session.commit()

        svc = IngestionService(db_session, FakeEmbeddingProvider(), MockQdrantStore())
        await svc.ingest(source_id)

        row = (await db_session.execute(text(
            "SELECT stages FROM processing_runs WHERE source_id = :s "
            "ORDER BY started_at DESC NULLS LAST LIMIT 1"
        ), {"s": source_id})).fetchone()
        assert row is not None
        stages = row[0]
        if isinstance(stages, str):
            stages = json.loads(stages)
        names = [s.get("stage") for s in stages]
        assert "graph_relations" in names, f"missing graph_relations stage: {names}"

