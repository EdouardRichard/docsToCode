"""Story integration test for US2: DDL FK recall (T024).

Validates AS2.1-2.3: scoped FK recall of referencing/referenced tables,
unrecalled targets expand via FK edges, cross-project isolation.

This test MUST FAIL before the engine is complete (TDD).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from rag_mcp.graph.expansion import GraphExpansionEngine
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

CREATE TABLE payments (
    id INT PRIMARY KEY,
    order_id INT,
    FOREIGN KEY (order_id) REFERENCES orders(id)
);
"""


def _make_ddl_chunks():
    return {
        "users": {"chunk_id": generate_id(), "symbol_path": "table:users",
                  "symbol_type": "table", "content_text": "users", "start_line": 1, "end_line": 4},
        "orders": {"chunk_id": generate_id(), "symbol_path": "table:orders",
                   "symbol_type": "table", "content_text": "orders", "start_line": 6, "end_line": 11},
        "payments": {"chunk_id": generate_id(), "symbol_path": "table:payments",
                     "symbol_type": "table", "content_text": "payments", "start_line": 13, "end_line": 18},
    }


@pytest.fixture
async def us2_scope(db_session):
    sa = generate_id(); pa = generate_id(); va = generate_id()
    src_a = await _setup_scope(db_session, sa, pa, va)
    chunks = _make_ddl_chunks()
    for c in chunks.values():
        await _insert_chunk(db_session, c["chunk_id"], sa, va, src_a)
    extractor = DdlFkExtractor()
    store = PostgresGraphStore(db_session)
    edges = extractor.extract(_DDL_SOURCE, list(chunks.values()), GraphScope(sa, pa, 1))
    await store.write_edges(edges, GraphScope(sa, pa, 1))
    await db_session.commit()
    return {"scope": GraphScope(sa, pa, 1), "chunks": chunks, "sa": sa}


@pytest.mark.asyncio
async def test_as2_1_recall_fk_referencing_tables(db_session, us2_scope):
    """AS2.1: query referencing-users recalls FK-referencing tables."""
    scope = us2_scope["scope"]
    engine = GraphExpansionEngine(db_session)
    users = us2_scope["chunks"]["users"]["chunk_id"]

    results = await engine.expand(start_chunk_ids=[users], scope=scope, hop=1, budget=20)
    result_ids = {r.chunk_id for r in results}
    # orders references users -> orders should be reachable
    assert us2_scope["chunks"]["orders"]["chunk_id"] in result_ids


@pytest.mark.asyncio
async def test_as2_2_unrecalled_target_expands_via_fk(db_session, us2_scope):
    """AS2.2: multi-hop FK chain users<-orders<-payments traversed."""
    scope = us2_scope["scope"]
    engine = GraphExpansionEngine(db_session)
    users = us2_scope["chunks"]["users"]["chunk_id"]

    results = await engine.expand(start_chunk_ids=[users], scope=scope, hop=2, budget=20)
    result_ids = {r.chunk_id for r in results}
    # payments references orders which references users -> reachable in 2 hops
    assert us2_scope["chunks"]["payments"]["chunk_id"] in result_ids


@pytest.mark.asyncio
async def test_as2_2_candidates_carry_fk_path(db_session, us2_scope):
    """AS2.2: FK candidates carry edge_path with fk relation types."""
    scope = us2_scope["scope"]
    engine = GraphExpansionEngine(db_session)
    users = us2_scope["chunks"]["users"]["chunk_id"]

    results = await engine.expand(start_chunk_ids=[users], scope=scope, hop=2, budget=20)
    # At least one candidate path should involve an fk relation type
    fk_found = False
    for r in results:
        for step in r.edge_path:
            if step.get("relation_type", "").startswith("fk_"):
                fk_found = True
    assert fk_found, "Should have FK relation types in edge paths"


@pytest.mark.asyncio
async def test_as2_3_cross_project_fk_isolation(db_session, us2_scope):
    """AS2.3: FK edges written for this scope carry the isolation triple."""
    scope = us2_scope["scope"]
    chunk_ids = [c["chunk_id"] for c in us2_scope["chunks"].values()]
    # All edges involving this scope's chunks must carry this scope's triple
    result = await db_session.execute(text(
        "SELECT knowledge_scope_id, project_id, index_version FROM graph_edge "
        "WHERE source_chunk_id = ANY(:cids) OR target_chunk_id = ANY(:cids)"
    ), {"cids": chunk_ids})
    rows = result.fetchall()
    assert len(rows) > 0, "Should have FK edges for this scope"
    for ksid, pid, iv in rows:
        assert ksid == scope.knowledge_scope_id, "FK edge scope mismatch"
        assert pid == scope.project_id, "FK edge project mismatch"
        assert iv == 1
