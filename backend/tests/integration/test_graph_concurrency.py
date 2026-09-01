"""Concurrency isolation test for graph expansion (T036).

Validates 5 concurrent requests are request-level isolated: scope /
evidence ledger / graph expansion intermediate state do not cross-talk
(FR-020, blueprint sec 21.1).

This test MUST FAIL before request-level isolation is verified (TDD).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import text

from rag_mcp.graph.expansion import GraphExpansionEngine
from rag_mcp.graph.store.base import GraphScope
from rag_mcp.graph.store.postgres_graph_store import PostgresGraphStore
from rag_mcp.utils.snowflake import generate_id
from tests.unit.test_postgres_graph_store import _insert_chunk, _setup_scope


async def _make_scope_with_edges(db_session):
    """Create an isolated scope with a small call chain; return (scope, hub)."""
    sa = generate_id(); pa = generate_id(); va = generate_id()
    src = await _setup_scope(db_session, sa, pa, va)
    hub = generate_id(); t1 = generate_id(); t2 = generate_id()
    for cid in (hub, t1, t2):
        await _insert_chunk(db_session, cid, sa, va, src)
    for (s, t) in ((hub, t1), (t1, t2)):
        await db_session.execute(text(
            "INSERT INTO graph_edge (edge_id, knowledge_scope_id, project_id, "
            "index_version, source_chunk_id, target_chunk_id, relation_type, "
            "direction, is_hard, version, parse_evidence) "
            "VALUES (:eid, :ksid, :pid, 1, :src, :tgt, 'calls', 'out', true, 1, "
            "CAST(:pe AS jsonb))"
        ), {"eid": generate_id(), "ksid": sa, "pid": pa, "src": s, "tgt": t,
            "pe": json.dumps({"source_format": "java", "locator": "x", "extractor": "e"})})
    await db_session.commit()
    return GraphScope(sa, pa, 1), hub, {t1, t2}


@pytest.mark.asyncio
async def test_five_concurrent_requests_isolated(db_session):
    """5 concurrent expansions on distinct scopes must not cross-talk (FR-020)."""
    # Set up 5 independent scopes
    setups = []
    for _ in range(5):
        scope, hub, targets = await _make_scope_with_edges(db_session)
        setups.append((scope, hub, targets))

    async def expand_one(scope, hub):
        engine = GraphExpansionEngine(db_session)
        return await engine.expand(start_chunk_ids=[hub], scope=scope, hop=2, budget=10)

    # Run 5 concurrent expansions
    tasks = [expand_one(scope, hub) for scope, hub, _ in setups]
    results = await asyncio.gather(*tasks)

    # Each result must contain ONLY its own scope's chunks (no cross-talk)
    for i, ((scope, hub, own_targets), result) in enumerate(zip(setups, results)):
        result_ids = {r.chunk_id for r in result}
        for cand in result:
            assert cand.knowledge_scope_id == scope.knowledge_scope_id, (
                f"Request {i}: candidate leaked from another scope"
            )
        # Each scope's expansion found its own targets
        assert own_targets & result_ids, (
            f"Request {i} did not find its own targets (isolation broken)"
        )


@pytest.mark.asyncio
async def test_concurrent_same_scope_deterministic(db_session):
    """Concurrent expansions on the SAME scope return identical results (determinism)."""
    scope, hub, targets = await _make_scope_with_edges(db_session)

    async def expand_once():
        engine = GraphExpansionEngine(db_session)
        return await engine.expand(start_chunk_ids=[hub], scope=scope, hop=2, budget=10)

    results = await asyncio.gather(*[expand_once() for _ in range(5)])
    # All 5 must agree on the set of chunk_ids
    first = {r.chunk_id for r in results[0]}
    for i, result in enumerate(results[1:], start=1):
        assert {r.chunk_id for r in result} == first, (
            f"Concurrent run {i} differs — non-deterministic (FR-019)"
        )
