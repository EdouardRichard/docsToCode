"""Story integration test for US1: Java call-chain recall (T021).

Validates AS1.1-1.3: scoped 1-3 hop recall of callers/callees with source
metadata; validateToken method-level recall; cross-project isolation (AS1.3).

This test MUST FAIL before the engine is complete (TDD).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from rag_mcp.graph.expansion import GraphExpansionEngine
from rag_mcp.graph.extractors.java_call_graph import JavaCallGraphExtractor
from rag_mcp.graph.store.base import GraphScope
from rag_mcp.graph.store.postgres_graph_store import PostgresGraphStore
from rag_mcp.utils.snowflake import generate_id
from tests.unit.test_postgres_graph_store import _insert_chunk, _insert_edge, _setup_scope


_JAVA_SOURCE = """package com.example.service;

public class TokenService {
    public void processRequest(String req) {
        validateToken(req);
        logAccess(req);
    }
    private boolean validateToken(String token) {
        return checkSignature(token);
    }
    private boolean checkSignature(String token) {
        return token != null;
    }
    private void logAccess(String req) {
        validateToken(req);
    }
}
"""


@pytest.fixture
async def us1_two_projects(db_session):
    """Two projects, each with a Java TokenService corpus."""
    # Project A
    sa = generate_id(); pa = generate_id(); va = generate_id()
    src_a = await _setup_scope(db_session, sa, pa, va)
    chunks_a = _make_token_chunks(sa, va, src_a)
    for c in chunks_a.values():
        await _insert_chunk(db_session, c["chunk_id"], sa, va, src_a)
    # Project B
    sb = generate_id(); pb = generate_id(); vb = generate_id()
    src_b = await _setup_scope(db_session, sb, pb, vb)
    chunks_b = _make_token_chunks(sb, vb, src_b)
    for c in chunks_b.values():
        await _insert_chunk(db_session, c["chunk_id"], sb, vb, src_b)

    # Extract and write edges for both
    extractor = JavaCallGraphExtractor()
    store = PostgresGraphStore(db_session)
    edges_a = extractor.extract(_JAVA_SOURCE, list(chunks_a.values()), GraphScope(sa, pa, 1))
    await store.write_edges(edges_a, GraphScope(sa, pa, 1))
    edges_b = extractor.extract(_JAVA_SOURCE, list(chunks_b.values()), GraphScope(sb, pb, 1))
    await store.write_edges(edges_b, GraphScope(sb, pb, 1))
    await db_session.commit()

    return {
        "scope_a": GraphScope(sa, pa, 1), "scope_b": GraphScope(sb, pb, 1),
        "sa": sa, "sb": sb,
        "chunks_a": chunks_a, "chunks_b": chunks_b,
    }


def _make_token_chunks(scope_id, version_id, source_id):
    return {
        "cls": {"chunk_id": generate_id(), "symbol_path": "com.example.service.TokenService",
                "symbol_type": "class", "content_text": _JAVA_SOURCE, "start_line": 3, "end_line": 18},
        "processRequest": {"chunk_id": generate_id(), "symbol_path": "com.example.service.TokenService#processRequest",
                "symbol_type": "method", "content_text": "processRequest", "start_line": 4, "end_line": 7},
        "validateToken": {"chunk_id": generate_id(), "symbol_path": "com.example.service.TokenService#validateToken",
                "symbol_type": "method", "content_text": "validateToken", "start_line": 8, "end_line": 10},
        "checkSignature": {"chunk_id": generate_id(), "symbol_path": "com.example.service.TokenService#checkSignature",
                "symbol_type": "method", "content_text": "checkSignature", "start_line": 11, "end_line": 13},
        "logAccess": {"chunk_id": generate_id(), "symbol_path": "com.example.service.TokenService#logAccess",
                "symbol_type": "method", "content_text": "logAccess", "start_line": 14, "end_line": 16},
    }


@pytest.mark.asyncio
async def test_as1_1_recall_callers_and_callees(db_session, us1_two_projects):
    """AS1.1: scoped query on validateToken recalls callers + callees within 1-3 hops."""
    scope = us1_two_projects["scope_a"]
    engine = GraphExpansionEngine(db_session)
    vt = us1_two_projects["chunks_a"]["validateToken"]["chunk_id"]

    results = await engine.expand(
        start_chunk_ids=[vt], scope=scope, hop=2, budget=20, direction="bidirectional")

    result_ids = {r.chunk_id for r in results}
    # validateToken calls checkSignature (callee)
    assert us1_two_projects["chunks_a"]["checkSignature"]["chunk_id"] in result_ids
    # validateToken is called by processRequest and logAccess (callers)
    assert us1_two_projects["chunks_a"]["processRequest"]["chunk_id"] in result_ids
    assert us1_two_projects["chunks_a"]["logAccess"]["chunk_id"] in result_ids


@pytest.mark.asyncio
async def test_as1_1_candidates_carry_edge_path(db_session, us1_two_projects):
    """AS1.1: each recalled candidate carries an edge_path (FR-008)."""
    scope = us1_two_projects["scope_a"]
    engine = GraphExpansionEngine(db_session)
    vt = us1_two_projects["chunks_a"]["validateToken"]["chunk_id"]

    results = await engine.expand(start_chunk_ids=[vt], scope=scope, hop=2, budget=20)
    for r in results:
        assert len(r.edge_path) >= 1
        for step in r.edge_path:
            assert "edge_id" in step and "relation_type" in step


@pytest.mark.asyncio
async def test_as1_3_cross_project_leakage_zero(db_session, us1_two_projects):
    """AS1.3: scope A expansion must NOT return scope B chunks (leakage=0)."""
    scope_a = us1_two_projects["scope_a"]
    engine = GraphExpansionEngine(db_session)
    vt_a = us1_two_projects["chunks_a"]["validateToken"]["chunk_id"]

    results = await engine.expand(start_chunk_ids=[vt_a], scope=scope_a, hop=3, budget=20)
    result_ids = {r.chunk_id for r in results}

    # No chunk from project B should appear
    for key, chunk_b in us1_two_projects["chunks_b"].items():
        assert chunk_b["chunk_id"] not in result_ids, (
            f"Cross-project leak: scope B chunk '{key}' in scope A results!"
        )


@pytest.mark.asyncio
async def test_as1_2_hops_bounded(db_session, us1_two_projects):
    """AS1.2: expansion respects hop guardrail (1-3)."""
    scope = us1_two_projects["scope_a"]
    engine = GraphExpansionEngine(db_session)
    vt = us1_two_projects["chunks_a"]["validateToken"]["chunk_id"]

    results = await engine.expand(start_chunk_ids=[vt], scope=scope, hop=3, budget=20)
    for r in results:
        assert 1 <= r.hop_count <= 3
