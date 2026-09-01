"""Unit test for PostgresGraphStore recursive CTE + guardrails (T010).

Validates WITH RECURSIVE 1-3 hop expansion, total-budget truncation by
structure_weight, and scope isolation (no cross-scope edges returned).

This test MUST FAIL before the store is implemented (TDD).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.graph.store.base import GraphScope
from rag_mcp.graph.store.postgres_graph_store import PostgresGraphStore
from rag_mcp.utils.snowflake import generate_id


async def _setup_scope(session: AsyncSession, scope_id: int, project_id: int,
                       version_id: int) -> int:
    """Insert a knowledge_scope, project, knowledge_source, and knowledge_version.
    Returns the source_id."""
    await session.execute(text(
        "INSERT INTO knowledge_scopes (scope_id, scope_type, name, status) "
        "VALUES (:sid, 'project', :name, 'active') "
        "ON CONFLICT (scope_id) DO NOTHING"
    ), {"sid": scope_id, "name": f"test-scope-{scope_id}"})
    await session.execute(text(
        "INSERT INTO projects (project_id, name, knowledge_scope_id) "
        "VALUES (:pid, :name, :sid) "
        "ON CONFLICT (project_id) DO NOTHING"
    ), {"pid": project_id, "name": f"test-proj-{project_id}", "sid": scope_id})
    source_id = generate_id()
    await session.execute(text(
        "INSERT INTO knowledge_sources (source_id, knowledge_scope_id, filename, "
        "content_hash, format, size_bytes, status) "
        "VALUES (:sid, :ksid, :fn, :ch, 'java', 100, 'published') "
        "ON CONFLICT (source_id) DO NOTHING"
    ), {"sid": source_id, "ksid": scope_id,
        "fn": f"test-{scope_id}.java", "ch": f"hash-{scope_id}"})
    await session.execute(text(
        "INSERT INTO knowledge_versions (version_id, knowledge_scope_id, version_number, status) "
        "VALUES (:vid, :sid, 1, 'published') "
        "ON CONFLICT (version_id) DO NOTHING"
    ), {"vid": version_id, "sid": scope_id})
    return source_id


async def _insert_chunk(session: AsyncSession, chunk_id: int, scope_id: int,
                        version_id: int, source_id: int) -> None:
    """Insert a minimal chunk for testing."""
    await session.execute(text(
        "INSERT INTO chunks (chunk_id, source_id, version_id, knowledge_scope_id, "
        "content_text, position_path, chunk_type, start_line, end_line, "
        "token_count, embedding_model, index_version) "
        "VALUES (:cid, :sid, :vid, :ksid, :content, :path, 'method', 1, 10, 50, 'test', 1) "
        "ON CONFLICT (chunk_id) DO NOTHING"
    ), {
        "cid": chunk_id, "sid": source_id, "vid": version_id,
        "ksid": scope_id, "content": f"chunk-{chunk_id}",
        "path": f"path-{chunk_id}",
    })


async def _insert_edge(session: AsyncSession, scope_id: int, project_id: int,
                       src: int, tgt: int, rel_type: str = "calls",
                       version: int = 1) -> int:
    """Insert a graph_edge and return the edge_id."""
    edge_id = generate_id()
    await session.execute(text(
        "INSERT INTO graph_edge (edge_id, knowledge_scope_id, project_id, "
        "index_version, source_chunk_id, target_chunk_id, relation_type, "
        "direction, is_hard, version, parse_evidence) "
        "VALUES (:eid, :ksid, :pid, 1, :src, :tgt, :rt, 'out', true, :v, :pe) "
        "ON CONFLICT DO NOTHING"
    ), {
        "eid": edge_id, "ksid": scope_id, "pid": project_id,
        "src": src, "tgt": tgt, "rt": rel_type, "v": version,
        "pe": '{"source_format":"java","locator":"x","extractor":"e"}',
    })
    return edge_id


@pytest.fixture
async def graph_test_data(db_session):
    """Set up two scopes with chunks and edges for isolation testing."""
    scope_a = generate_id()
    scope_b = generate_id()
    proj_a = generate_id()
    proj_b = generate_id()
    ver_a = generate_id()
    ver_b = generate_id()

    src_a = await _setup_scope(db_session, scope_a, proj_a, ver_a)
    src_b = await _setup_scope(db_session, scope_b, proj_b, ver_b)

    # Scope A: chunk with 15 fan-out edges (high fan-out)
    hub_a = generate_id()
    await _insert_chunk(db_session, hub_a, scope_a, ver_a, src_a)
    targets_a = []
    for i in range(15):
        tgt = generate_id()
        await _insert_chunk(db_session, tgt, scope_a, ver_a, src_a)
        await _insert_edge(db_session, scope_a, proj_a, hub_a, tgt, "calls")
        targets_a.append(tgt)

    # Scope A: a 2-hop chain hub_a -> mid -> far
    mid = generate_id()
    far = generate_id()
    await _insert_chunk(db_session, mid, scope_a, ver_a, src_a)
    await _insert_chunk(db_session, far, scope_a, ver_a, src_a)
    await _insert_edge(db_session, scope_a, proj_a, hub_a, mid, "calls")
    await _insert_edge(db_session, scope_a, proj_a, mid, far, "called_by")

    # Scope B: chunk with edges (should NOT be returned in scope A expansion)
    hub_b = generate_id()
    tgt_b = generate_id()
    await _insert_chunk(db_session, hub_b, scope_b, ver_b, src_b)
    await _insert_chunk(db_session, tgt_b, scope_b, ver_b, src_b)
    await _insert_edge(db_session, scope_b, proj_b, hub_b, tgt_b, "calls")

    await db_session.commit()

    yield {
        "scope_a": GraphScope(scope_a, proj_a, 1),
        "scope_b": GraphScope(scope_b, proj_b, 1),
        "hub_a": hub_a,
        "hub_b": hub_b,
        "targets_a": targets_a,
        "mid": mid,
        "far": far,
        "tgt_b": tgt_b,
    }


class TestExpandBudgetTruncation:
    @pytest.mark.asyncio
    async def test_candidates_within_budget(self, db_session, graph_test_data):
        """High fan-out: candidates MUST be ≤ budget (FR-017 total budget)."""
        store = PostgresGraphStore(db_session)
        scope = graph_test_data["scope_a"]
        results = await store.expand(
            start_chunk_ids=[graph_test_data["hub_a"]],
            scope=scope, hop=2, budget=10, direction="bidirectional",
        )
        assert len(results) <= 10, f"Expected ≤10 candidates, got {len(results)}"

    @pytest.mark.asyncio
    async def test_candidates_sorted_by_weight(self, db_session, graph_test_data):
        """Candidates MUST be sorted by structure_weight descending."""
        store = PostgresGraphStore(db_session)
        scope = graph_test_data["scope_a"]
        results = await store.expand(
            start_chunk_ids=[graph_test_data["hub_a"]],
            scope=scope, hop=2, budget=20, direction="bidirectional",
        )
        weights = [r.structure_weight for r in results]
        assert weights == sorted(weights, reverse=True), (
            f"Results must be sorted by weight desc: {weights}"
        )

    @pytest.mark.asyncio
    async def test_1hop_higher_than_2hop(self, db_session, graph_test_data):
        """1-hop candidates (weight 1.0) must rank above 2-hop (weight 0.5)."""
        store = PostgresGraphStore(db_session)
        scope = graph_test_data["scope_a"]
        results = await store.expand(
            start_chunk_ids=[graph_test_data["hub_a"]],
            scope=scope, hop=2, budget=20, direction="bidirectional",
        )
        # Find the 2-hop candidate (far)
        far = graph_test_data["far"]
        far_candidate = next((r for r in results if r.chunk_id == far), None)
        if far_candidate:
            # 2-hop weight should be 0.5 (hard, hop 2)
            assert far_candidate.structure_weight <= 0.51, (
                f"2-hop weight should be ~0.5, got {far_candidate.structure_weight}"
            )
        # 1-hop candidates should have weight 1.0
        one_hop = [r for r in results if r.hop_count == 1]
        for r in one_hop:
            assert abs(r.structure_weight - 1.0) < 0.01, (
                f"1-hop weight should be 1.0, got {r.structure_weight}"
            )


class TestScopeIsolation:
    @pytest.mark.asyncio
    async def test_no_cross_scope_edges(self, db_session, graph_test_data):
        """Scope B edges MUST NOT appear in scope A expansion (FR-010)."""
        store = PostgresGraphStore(db_session)
        scope_a = graph_test_data["scope_a"]
        results = await store.expand(
            start_chunk_ids=[graph_test_data["hub_a"]],
            scope=scope_a, hop=3, budget=20, direction="bidirectional",
        )
        tgt_b = graph_test_data["tgt_b"]
        result_ids = {r.chunk_id for r in results}
        assert tgt_b not in result_ids, (
            "Cross-scope edge leaked into scope A expansion!"
        )

    @pytest.mark.asyncio
    async def test_bidirectional_finds_callers_and_callees(self, db_session, graph_test_data):
        """Bidirectional default MUST find both outgoing and incoming edges."""
        store = PostgresGraphStore(db_session)
        scope = graph_test_data["scope_a"]
        # Expand from 'mid' which has incoming from hub_a and outgoing to far
        results = await store.expand(
            start_chunk_ids=[graph_test_data["mid"]],
            scope=scope, hop=1, budget=20, direction="bidirectional",
        )
        result_ids = {r.chunk_id for r in results}
        # hub_a calls mid → mid is called_by hub_a (reverse direction)
        assert graph_test_data["hub_a"] in result_ids, (
            "Bidirectional should find caller (hub_a) of mid"
        )
        # mid called_by far → mid has outgoing called_by to far
        assert graph_test_data["far"] in result_ids, (
            "Bidirectional should find callee/target (far) of mid"
        )
