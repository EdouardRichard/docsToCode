"""Unit test for graph expansion engine (T011).

Validates total-budget truncation, structure weight decay, bidirectional
default, and edge_path structure (research §2/§3, FR-008/FR-017).

This test MUST FAIL before the expansion engine is implemented (TDD).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.graph.expansion import GraphExpansionEngine
from rag_mcp.graph.store.base import GraphScope
from rag_mcp.utils.snowflake import generate_id

# Reuse test helpers from T010
from tests.unit.test_postgres_graph_store import (
    _insert_chunk,
    _insert_edge,
    _setup_scope,
)


@pytest.fixture
async def expansion_data(db_session):
    """Set up a scope with a 3-hop chain and high fan-out."""
    scope_id = generate_id()
    project_id = generate_id()
    version_id = generate_id()
    source_id = await _setup_scope(db_session, scope_id, project_id, version_id)

    # hub -> A -> B -> C (3-hop chain)
    hub = generate_id()
    a = generate_id()
    b = generate_id()
    c = generate_id()
    for cid in (hub, a, b, c):
        await _insert_chunk(db_session, cid, scope_id, version_id, source_id)

    await _insert_edge(db_session, scope_id, project_id, hub, a, "calls")
    await _insert_edge(db_session, scope_id, project_id, a, b, "calls")
    await _insert_edge(db_session, scope_id, project_id, b, c, "called_by")

    await db_session.commit()
    return {
        "scope": GraphScope(scope_id, project_id, 1),
        "hub": hub,
        "a": a,
        "b": b,
        "c": c,
    }


class TestExpansionEngine:
    @pytest.mark.asyncio
    async def test_expand_returns_candidates(self, db_session, expansion_data):
        """Engine.expand MUST return candidates from the graph."""
        engine = GraphExpansionEngine(db_session)
        results = await engine.expand(
            start_chunk_ids=[expansion_data["hub"]],
            scope=expansion_data["scope"],
        )
        assert len(results) > 0, "Expected candidates from expansion"

    @pytest.mark.asyncio
    async def test_default_budget_from_config(self, db_session, expansion_data):
        """Default budget MUST come from GraphConfig (10)."""
        engine = GraphExpansionEngine(db_session)
        results = await engine.expand(
            start_chunk_ids=[expansion_data["hub"]],
            scope=expansion_data["scope"],
        )
        # Default candidate_budget=10
        assert len(results) <= 10

    @pytest.mark.asyncio
    async def test_edge_path_structure(self, db_session, expansion_data):
        """Each candidate MUST carry an edge_path with HopStep structure."""
        engine = GraphExpansionEngine(db_session)
        results = await engine.expand(
            start_chunk_ids=[expansion_data["hub"]],
            scope=expansion_data["scope"],
        )
        for cand in results:
            assert len(cand.edge_path) >= 1
            for hop_step in cand.edge_path:
                assert "hop" in hop_step
                assert "edge_id" in hop_step
                assert "relation_type" in hop_step
                assert "direction" in hop_step
                assert "is_hard" in hop_step

    @pytest.mark.asyncio
    async def test_graph_rank_starts_at_1(self, db_session, expansion_data):
        """graph_rank MUST start at 1 and be sequential."""
        engine = GraphExpansionEngine(db_session)
        results = await engine.expand(
            start_chunk_ids=[expansion_data["hub"]],
            scope=expansion_data["scope"],
        )
        ranks = [c.graph_rank for c in results]
        assert ranks[0] == 1
        assert ranks == list(range(1, len(results) + 1))

    @pytest.mark.asyncio
    async def test_structure_weight_decay(self, db_session, expansion_data):
        """1-hop weight MUST be 1.0, 2-hop 0.5, 3-hop 0.25 (hard relations)."""
        engine = GraphExpansionEngine(db_session)
        results = await engine.expand(
            start_chunk_ids=[expansion_data["hub"]],
            scope=expansion_data["scope"],
            hop=3,
            budget=20,
        )
        # Find 1-hop, 2-hop, 3-hop candidates
        by_hop = {}
        for c in results:
            by_hop.setdefault(c.hop_count, c)
        if 1 in by_hop:
            assert abs(by_hop[1].structure_weight - 1.0) < 0.01
        if 2 in by_hop:
            assert abs(by_hop[2].structure_weight - 0.5) < 0.01
        if 3 in by_hop:
            assert abs(by_hop[3].structure_weight - 0.25) < 0.01

    @pytest.mark.asyncio
    async def test_bidirectional_default(self, db_session, expansion_data):
        """Default direction MUST be bidirectional (finds callers + callees)."""
        engine = GraphExpansionEngine(db_session)
        results = await engine.expand(
            start_chunk_ids=[expansion_data["b"]],
            scope=expansion_data["scope"],
            hop=1,
            budget=20,
        )
        result_ids = {c.chunk_id for c in results}
        # b is called_by a (incoming) and b called_by c (outgoing)
        assert expansion_data["a"] in result_ids, "Should find caller (a) of b"
        assert expansion_data["c"] in result_ids, "Should find target (c) of b"
