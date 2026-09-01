"""Integration test for US5: isolation enforcement + cleanup (T032).

Validates cross-project graph isolation (SC-003) and cleanup lifecycle:
mark non-retrievable first, then async delete; other projects unaffected
(blueprint sec 5).

This test MUST FAIL before cleanup methods are implemented (TDD).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from rag_mcp.graph.store.base import GraphScope
from rag_mcp.graph.store.postgres_graph_store import PostgresGraphStore
from rag_mcp.utils.snowflake import generate_id
from tests.unit.test_postgres_graph_store import _insert_chunk, _insert_edge, _setup_scope


@pytest.fixture
async def two_project_scopes(db_session):
    """Create two independent project scopes with graph edges."""
    sa = generate_id(); pa = generate_id(); va = generate_id()
    sb = generate_id(); pb = generate_id(); vb = generate_id()
    src_a = await _setup_scope(db_session, sa, pa, va)
    src_b = await _setup_scope(db_session, sb, pb, vb)

    # Scope A edges
    hub_a = generate_id(); tgt_a = generate_id()
    for cid in (hub_a, tgt_a):
        await _insert_chunk(db_session, cid, sa, va, src_a)
    await _insert_edge(db_session, sa, pa, hub_a, tgt_a, "calls")

    # Scope B edges
    hub_b = generate_id(); tgt_b = generate_id()
    for cid in (hub_b, tgt_b):
        await _insert_chunk(db_session, cid, sb, vb, src_b)
    await _insert_edge(db_session, sb, pb, hub_b, tgt_b, "calls")

    await db_session.commit()
    return {
        "scope_a": GraphScope(sa, pa, 1), "scope_b": GraphScope(sb, pb, 1),
        "sa": sa, "sb": sb, "pa": pa, "pb": pb,
        "hub_a": hub_a, "hub_b": hub_b,
    }


@pytest.mark.asyncio
async def test_cross_project_isolation(db_session, two_project_scopes):
    """Scope A expansion MUST NOT return scope B edges (SC-003)."""
    store = PostgresGraphStore(db_session)
    results_a = await store.expand(
        [two_project_scopes["hub_a"]], two_project_scopes["scope_a"],
        hop=3, budget=20)
    result_ids = {r.chunk_id for r in results_a}
    # hub_a should find tgt_a (same scope), not hub_b/tgt_b (scope B)
    assert two_project_scopes["hub_b"] not in result_ids, "Cross-project leak!"


@pytest.mark.asyncio
async def test_cleanup_marks_then_deletes(db_session, two_project_scopes):
    """Cleanup MUST mark non-retrievable first, then delete (blueprint sec 5)."""
    store = PostgresGraphStore(db_session)
    sa = two_project_scopes["sa"]

    # Step 1: Mark non-retrievable (mark graph_ready=false for the version)
    await store.mark_graph_unretrievable(two_project_scopes["scope_a"])
    await db_session.commit()

    # After marking, graph_ready should be false
    result = await db_session.execute(text(
        "SELECT graph_ready FROM knowledge_versions WHERE version_id IN "
        "(SELECT version_id FROM chunks WHERE knowledge_scope_id = :sid LIMIT 1)"
    ), {"sid": sa})
    # May be multiple rows; at least the marked one should be false
    rows = result.fetchall()
    assert any(r[0] is False for r in rows), "graph_ready should be marked false"

    # Step 2: Delete graph relations
    deleted = await store.delete_graph_relations(two_project_scopes["scope_a"])
    await db_session.commit()
    assert deleted > 0, "Should have deleted graph edges"

    # Verify scope A edges are gone
    result = await db_session.execute(text(
        "SELECT count(*) FROM graph_edge WHERE knowledge_scope_id = :sid"
    ), {"sid": sa})
    assert result.scalar() == 0, "Scope A graph edges should be deleted"


@pytest.mark.asyncio
async def test_cleanup_does_not_affect_other_projects(db_session, two_project_scopes):
    """Cleaning scope A MUST NOT affect scope B's graph relations."""
    store = PostgresGraphStore(db_session)
    sb = two_project_scopes["sb"]

    # Cleanup scope A
    await store.delete_graph_relations(two_project_scopes["scope_a"])
    await db_session.commit()

    # Scope B edges should still exist
    result = await db_session.execute(text(
        "SELECT count(*) FROM graph_edge WHERE knowledge_scope_id = :sid"
    ), {"sid": sb})
    assert result.scalar() > 0, "Scope B graph edges should be unaffected"


@pytest.mark.asyncio
async def test_cleanup_orchestration(db_session, two_project_scopes):
    """cleanup_scope() orchestrates mark-then-delete."""
    store = PostgresGraphStore(db_session)
    sa = two_project_scopes["sa"]

    await store.cleanup_scope(two_project_scopes["scope_a"])
    await db_session.commit()

    # Edges should be deleted
    result = await db_session.execute(text(
        "SELECT count(*) FROM graph_edge WHERE knowledge_scope_id = :sid"
    ), {"sid": sa})
    assert result.scalar() == 0
