"""Integration test for US2: DDL FK extraction at ingest (T023).

Validates that DDL source ingestion triggers FK extraction and writes
graph_edge records with fk_references/fk_referenced_by (FR-001/FR-010).

This test MUST FAIL before the extraction hook is wired (TDD).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from rag_mcp.graph.extractors.ddl_fk import DdlFkExtractor
from rag_mcp.graph.store.base import GraphScope
from rag_mcp.graph.store.postgres_graph_store import PostgresGraphStore
from rag_mcp.utils.snowflake import generate_id
from tests.unit.test_postgres_graph_store import _insert_chunk, _setup_scope


_DDL_SOURCE = """CREATE TABLE users (
    id INT PRIMARY KEY,
    email VARCHAR(255)
);

CREATE TABLE orders (
    id INT PRIMARY KEY,
    user_id INT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
"""


@pytest.fixture
async def ddl_scope(db_session):
    scope_id = generate_id()
    project_id = generate_id()
    version_id = generate_id()
    source_id = await _setup_scope(db_session, scope_id, project_id, version_id)

    chunks = [
        {"chunk_id": generate_id(), "symbol_path": "table:users",
         "symbol_type": "table", "content_text": "users table", "start_line": 1, "end_line": 4},
        {"chunk_id": generate_id(), "symbol_path": "table:orders",
         "symbol_type": "table", "content_text": "orders table", "start_line": 6, "end_line": 11},
    ]
    for c in chunks:
        await _insert_chunk(db_session, c["chunk_id"], scope_id, version_id, source_id)
    await db_session.commit()
    return {"scope": GraphScope(scope_id, project_id, 1), "chunks": chunks}


@pytest.mark.asyncio
async def test_ddl_ingestion_writes_fk_edges(db_session, ddl_scope):
    """Ingesting DDL MUST write fk_references/fk_referenced_by edges."""
    scope = ddl_scope["scope"]
    extractor = DdlFkExtractor()
    edges = extractor.extract(_DDL_SOURCE, ddl_scope["chunks"], scope)
    assert len(edges) > 0, "Expected FK edges from DDL"

    store = PostgresGraphStore(db_session)
    count = await store.write_edges(edges, scope)
    await db_session.commit()
    assert count > 0

    result = await db_session.execute(text(
        "SELECT relation_type FROM graph_edge WHERE knowledge_scope_id = :ksid"
    ), {"ksid": scope.knowledge_scope_id})
    rel_types = {row[0] for row in result}
    assert "fk_references" in rel_types
    assert "fk_referenced_by" in rel_types


@pytest.mark.asyncio
async def test_no_fk_produces_no_edges(db_session, ddl_scope):
    """DDL with no foreign keys MUST produce no edges (Edge Case)."""
    scope = ddl_scope["scope"]
    extractor = DdlFkExtractor()
    edges = extractor.extract(
        "CREATE TABLE simple (id INT PRIMARY KEY);",
        ddl_scope["chunks"], scope)
    assert len(edges) == 0


@pytest.mark.asyncio
async def test_fk_edges_isolation(db_session, ddl_scope):
    """FK edges MUST carry the isolation triple."""
    scope = ddl_scope["scope"]
    extractor = DdlFkExtractor()
    edges = extractor.extract(_DDL_SOURCE, ddl_scope["chunks"], scope)
    store = PostgresGraphStore(db_session)
    await store.write_edges(edges, scope)
    await db_session.commit()

    result = await db_session.execute(text(
        "SELECT knowledge_scope_id, project_id, index_version, is_hard "
        "FROM graph_edge WHERE knowledge_scope_id = :ksid"
    ), {"ksid": scope.knowledge_scope_id})
    for row in result:
        assert row[0] == scope.knowledge_scope_id
        assert row[1] == scope.project_id
        assert row[2] == 1
        assert row[3] is True

class TestIngestPipelineGraphWiring:
    """T042: the ingestion pipeline itself MUST trigger DDL FK extraction."""

    @pytest.mark.asyncio
    async def test_ingest_ddl_source_writes_fk_edges(self, db_session):
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
            db_session, scope_id, source_id, "schema.sql", _DDL_SOURCE, "ddl"
        )
        await db_session.commit()

        svc = IngestionService(db_session, FakeEmbeddingProvider(), MockQdrantStore())
        await svc.ingest(source_id)

        rows = (await db_session.execute(text(
            "SELECT relation_type, is_hard, knowledge_scope_id, project_id, "
            "index_version, parse_evidence "
            "FROM graph_edge WHERE knowledge_scope_id = :k"
        ), {"k": scope_id})).fetchall()
        assert rows, "ingest() MUST write graph_edge rows for DDL sources (T042)"

        rel_types = {r[0] for r in rows}
        assert "fk_references" in rel_types
        assert "fk_referenced_by" in rel_types
        for rel_type, is_hard, ksid, pid, iv, pe in rows:
            assert is_hard is True
            assert ksid == scope_id
            assert pid == project_id
            assert iv == 1
            assert pe.get("extractor") == "ddl_fk"

