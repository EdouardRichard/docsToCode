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

# ---------------------------------------------------------------------------
# T047: graph cleanup wired into delete/clear/version-supersede flows
# ---------------------------------------------------------------------------


class TestApiCleanupGraphWiring:
    """T047: API purge paths MUST delete graph derived data (FR-016/AS5.3)."""

    @pytest.mark.asyncio
    async def test_scope_clear_purges_graph_relations(self, db_session, two_project_scopes):
        from rag_mcp.api.knowledge_sources import _purge_scope_derived_data
        from tests.integration.graph_ingest_helpers import MockQdrantStore

        sa = two_project_scopes["sa"]
        sb = two_project_scopes["sb"]
        await _purge_scope_derived_data(db_session, sa, qdrant_store=MockQdrantStore())
        await db_session.commit()

        count_a = (await db_session.execute(text(
            "SELECT count(*) FROM graph_edge WHERE knowledge_scope_id = :s"
        ), {"s": sa})).scalar()
        assert count_a == 0, "cleared scope graph edges must be deleted"

        soft_a = (await db_session.execute(text(
            "SELECT count(*) FROM soft_relation WHERE knowledge_scope_id = :s"
        ), {"s": sa})).scalar()
        assert soft_a == 0

        ready = (await db_session.execute(text(
            "SELECT bool_or(graph_ready) FROM knowledge_versions "
            "WHERE knowledge_scope_id = :s"
        ), {"s": sa})).scalar()
        assert not ready, "graph must be marked non-retrievable"

        count_b = (await db_session.execute(text(
            "SELECT count(*) FROM graph_edge WHERE knowledge_scope_id = :s"
        ), {"s": sb})).scalar()
        assert count_b > 0, "other scope graph edges must survive"

    @pytest.mark.asyncio
    async def test_source_delete_purges_graph_rows_and_paths(
        self, db_session, two_project_scopes
    ):
        from rag_mcp.api.knowledge_sources import _purge_source_derived_data
        from tests.integration.graph_ingest_helpers import MockQdrantStore

        env = two_project_scopes
        sa = env["sa"]
        # Find the source owning scope-A chunks and add a graph_expansion_path
        # row referencing one of them (FK-safe cleanup requirement).
        source_id = (await db_session.execute(text(
            "SELECT source_id FROM chunks WHERE knowledge_scope_id = :s LIMIT 1"
        ), {"s": sa})).scalar()
        chunk_id = env["hub_a"]
        run_id = generate_id()
        await db_session.execute(text(
            "INSERT INTO retrieval_runs (run_id, query_text, project_scopes, "
            "completion_status, evidence_count, duration_ms, retrieval_mode, "
            "subpath_timings) "
            "VALUES (:rid, 'q', '[]', 'complete', 1, 10, 'graph_enhanced', "
            "CAST(:t AS jsonb))"
        ), {"rid": run_id, "t": '{"graph_recall_ms": 0.5}'})
        await db_session.execute(text(
            "INSERT INTO graph_expansion_path (request_id, evidence_id, chunk_id, "
            "start_chunk_id, edge_path, hop_count, structure_weight, graph_rank) "
            "VALUES (:rid, :eid, :cid, :scid, CAST(:ep AS jsonb), 1, 1.0, 1)"
        ), {"rid": run_id, "eid": chunk_id, "cid": chunk_id, "scid": chunk_id,
            "ep": '[{"hop":1,"edge_id":"1","relation_type":"calls","direction":"out","is_hard":true}]'})
        await db_session.commit()

        await _purge_source_derived_data(db_session, source_id, qdrant_store=MockQdrantStore())
        await db_session.commit()

        remaining_chunks = (await db_session.execute(text(
            "SELECT count(*) FROM chunks WHERE source_id = :s"
        ), {"s": source_id})).scalar()
        assert remaining_chunks == 0

        remaining_paths = (await db_session.execute(text(
            "SELECT count(*) FROM graph_expansion_path WHERE chunk_id = :c"
        ), {"c": chunk_id})).scalar()
        assert remaining_paths == 0, "expansion paths must be purged before chunks"

    @pytest.mark.asyncio
    async def test_superseded_version_graph_rows_purged(self, db_session):
        """Publishing v2 MUST purge the superseded v1 graph rows (T047)."""
        from rag_mcp.services.ingestion_service import IngestionService
        from tests.integration.graph_ingest_helpers import (
            FakeEmbeddingProvider,
            MockQdrantStore,
            setup_graph_scope,
            upload_source_file,
        )

        scope_id = generate_id()
        project_id = generate_id()
        src1 = generate_id()
        src2 = generate_id()
        java = "package com.example;\npublic class S { void a() { b(); } void b() {} }\n"
        await setup_graph_scope(db_session, scope_id, project_id)
        await upload_source_file(db_session, scope_id, src1, "S.java", java, "java")
        await db_session.commit()

        svc = IngestionService(db_session, FakeEmbeddingProvider(), MockQdrantStore())
        await svc.ingest(src1)  # v1 with edges index_version=1
        v1_edges = (await db_session.execute(text(
            "SELECT count(*) FROM graph_edge WHERE knowledge_scope_id = :s "
            "AND index_version = 1"
        ), {"s": scope_id})).scalar()
        assert v1_edges > 0

        # Re-ingest a second source -> publishes v2, supersedes v1
        await upload_source_file(db_session, scope_id, src2, "S.java", java, "java")
        await db_session.commit()
        await svc.reprocess(src2)

        v1_after = (await db_session.execute(text(
            "SELECT count(*) FROM graph_edge WHERE knowledge_scope_id = :s "
            "AND index_version = 1"
        ), {"s": scope_id})).scalar()
        assert v1_after == 0, "superseded version graph rows must be purged"
        v2_edges = (await db_session.execute(text(
            "SELECT count(*) FROM graph_edge WHERE knowledge_scope_id = :s "
            "AND index_version = 2"
        ), {"s": scope_id})).scalar()
        assert v2_edges > 0, "new version graph rows must exist"

