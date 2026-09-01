"""Reproducibility test for graph expansion (T037).

Validates SC-007: two consecutive runs produce identical graph-expansion
results (Recall/rank in 1% relative tolerance); latency is environment-
sensitive and not a veto.

This test MUST FAIL before determinism is guaranteed (TDD).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from rag_mcp.graph.expansion import GraphExpansionEngine
from rag_mcp.graph.store.base import GraphScope
from rag_mcp.utils.snowflake import generate_id
from tests.unit.test_postgres_graph_store import _insert_chunk, _setup_scope

_TOLERANCE = 0.01  # SC-007 / research sec 6


@pytest.fixture
async def repro_scope(db_session):
    sa = generate_id(); pa = generate_id(); va = generate_id()
    src = await _setup_scope(db_session, sa, pa, va)
    # Build a small chain hub -> a -> b -> c with a fan from hub
    nodes = {k: generate_id() for k in ("hub", "a", "b", "c", "d", "e")}
    for cid in nodes.values():
        await _insert_chunk(db_session, cid, sa, va, src)
    edges = [("hub", "a"), ("a", "b"), ("b", "c"), ("hub", "d"), ("hub", "e")]
    for s, t in edges:
        await db_session.execute(text(
            "INSERT INTO graph_edge (edge_id, knowledge_scope_id, project_id, "
            "index_version, source_chunk_id, target_chunk_id, relation_type, "
            "direction, is_hard, version, parse_evidence) "
            "VALUES (:eid, :ksid, :pid, 1, :src, :tgt, 'calls', 'out', true, 1, "
            "CAST(:pe AS jsonb))"
        ), {"eid": generate_id(), "ksid": sa, "pid": pa,
            "src": nodes[s], "tgt": nodes[t],
            "pe": json.dumps({"source_format": "java", "locator": "x", "extractor": "e"})})
    await db_session.commit()
    return {"scope": GraphScope(sa, pa, 1), "hub": nodes["hub"]}


@pytest.mark.asyncio
async def test_two_runs_identical_results(db_session, repro_scope):
    """SC-007: two consecutive runs produce identical candidate sets + ranks."""
    engine = GraphExpansionEngine(db_session)
    scope = repro_scope["scope"]
    hub = repro_scope["hub"]

    run1 = await engine.expand(start_chunk_ids=[hub], scope=scope, hop=3, budget=20)
    run2 = await engine.expand(start_chunk_ids=[hub], scope=scope, hop=3, budget=20)

    # Identical chunk_id ordering
    ids1 = [r.chunk_id for r in run1]
    ids2 = [r.chunk_id for r in run2]
    assert ids1 == ids2, "Two runs must return identical candidate order"

    # Identical ranks
    ranks1 = [r.graph_rank for r in run1]
    ranks2 = [r.graph_rank for r in run2]
    assert ranks1 == ranks2

    # Identical structure weights (within tolerance)
    w1 = [r.structure_weight for r in run1]
    w2 = [r.structure_weight for r in run2]
    assert len(w1) == len(w2)
    for a, b in zip(w1, w2):
        assert abs(a - b) <= _TOLERANCE * max(abs(a), 1e-9), (
            f"Structure weight differs beyond tolerance: {a} vs {b}"
        )


@pytest.mark.asyncio
async def test_reproducibility_recall_parity(db_session, repro_scope):
    """SC-007: recall (set of reachable chunks) identical across runs."""
    engine = GraphExpansionEngine(db_session)
    scope = repro_scope["scope"]
    hub = repro_scope["hub"]

    sets = []
    for _ in range(3):
        results = await engine.expand(start_chunk_ids=[hub], scope=scope, hop=3, budget=20)
        sets.append(frozenset(r.chunk_id for r in results))

    assert sets[0] == sets[1] == sets[2], "Reachable chunk set must be reproducible"
