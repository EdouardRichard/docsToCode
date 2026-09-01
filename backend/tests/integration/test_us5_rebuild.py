"""Integration test for US5: graph rebuild from source (T033).

Validates that graph derived data can be rebuilt from original source +
version info, and the result matches the original (FR-016, blueprint sec 8.4).

This test MUST FAIL before rebuild is implemented (TDD).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from rag_mcp.graph.extractors.java_call_graph import JavaCallGraphExtractor
from rag_mcp.graph.store.base import GraphScope
from rag_mcp.graph.store.postgres_graph_store import PostgresGraphStore
from rag_mcp.utils.snowflake import generate_id
from tests.unit.test_postgres_graph_store import _insert_chunk, _setup_scope


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
async def rebuild_scope(db_session):
    scope_id = generate_id()
    project_id = generate_id()
    version_id = generate_id()
    source_id = await _setup_scope(db_session, scope_id, project_id, version_id)

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
        "source_id": source_id,
        "chunks": chunks,
    }


@pytest.mark.asyncio
async def test_rebuild_produces_same_edges(db_session, rebuild_scope):
    """Rebuild MUST produce the same edges as the original extraction."""
    scope = rebuild_scope["scope"]
    store = PostgresGraphStore(db_session)
    extractor = JavaCallGraphExtractor()

    # Original extraction
    edges = extractor.extract(_JAVA_SOURCE, rebuild_scope["chunks"], scope)
    await store.write_edges(edges, scope)
    await db_session.commit()

    # Count original edges
    result = await db_session.execute(text(
        "SELECT count(*) FROM graph_edge WHERE knowledge_scope_id = :sid"
    ), {"sid": scope.knowledge_scope_id})
    original_count = result.scalar()

    # Delete all graph data
    await store.delete_graph_relations(scope)
    await db_session.commit()

    # Verify deleted
    result = await db_session.execute(text(
        "SELECT count(*) FROM graph_edge WHERE knowledge_scope_id = :sid"
    ), {"sid": scope.knowledge_scope_id})
    assert result.scalar() == 0

    # Rebuild from source
    rebuilt_edges = extractor.extract(_JAVA_SOURCE, rebuild_scope["chunks"], scope)
    await store.write_edges(rebuilt_edges, scope)
    await db_session.commit()

    # Count rebuilt edges
    result = await db_session.execute(text(
        "SELECT count(*) FROM graph_edge WHERE knowledge_scope_id = :sid"
    ), {"sid": scope.knowledge_scope_id})
    rebuilt_count = result.scalar()

    assert rebuilt_count == original_count, (
        f"Rebuild should produce same edge count: {rebuilt_count} != {original_count}"
    )


@pytest.mark.asyncio
async def test_rebuild_deterministic(db_session, rebuild_scope):
    """Rebuild MUST be deterministic (same source = same edges)."""
    scope = rebuild_scope["scope"]
    store = PostgresGraphStore(db_session)
    extractor = JavaCallGraphExtractor()

    # First extraction
    edges1 = extractor.extract(_JAVA_SOURCE, rebuild_scope["chunks"], scope)
    await store.write_edges(edges1, scope)
    await db_session.commit()

    # Get edge pairs
    result = await db_session.execute(text(
        "SELECT source_chunk_id, target_chunk_id, relation_type "
        "FROM graph_edge WHERE knowledge_scope_id = :sid ORDER BY source_chunk_id, target_chunk_id"
    ), {"sid": scope.knowledge_scope_id})
    pairs1 = {(r[0], r[1], r[2]) for r in result}

    # Delete and rebuild
    await store.delete_graph_relations(scope)
    await db_session.commit()

    edges2 = extractor.extract(_JAVA_SOURCE, rebuild_scope["chunks"], scope)
    await store.write_edges(edges2, scope)
    await db_session.commit()

    result = await db_session.execute(text(
        "SELECT source_chunk_id, target_chunk_id, relation_type "
        "FROM graph_edge WHERE knowledge_scope_id = :sid ORDER BY source_chunk_id, target_chunk_id"
    ), {"sid": scope.knowledge_scope_id})
    pairs2 = {(r[0], r[1], r[2]) for r in result}

    assert pairs1 == pairs2, "Rebuild should produce identical edge set"


@pytest.mark.asyncio
async def test_rebuild_no_auto_migration(db_session, rebuild_scope):
    """Rebuild is user-triggered, not auto batch migration (FR-027)."""
    scope = rebuild_scope["scope"]
    store = PostgresGraphStore(db_session)

    # rebuild_graph_edges is a method that must be explicitly called
    assert hasattr(store, "rebuild_graph_edges"), (
        "Store must expose rebuild_graph_edges for user-triggered rebuild (FR-027)"
    )

    # Calling rebuild should work
    result = await store.rebuild_graph_edges(
        source_code=_JAVA_SOURCE,
        chunks=rebuild_scope["chunks"],
        scope=scope,
        format="java",
    )
    assert result > 0, "Rebuild should produce edges"
