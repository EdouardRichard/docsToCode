"""Story integration test for US5: graph_ready lifecycle (T034).

Validates AS5.1-5.4: rebuild grants graph_ready, unready not retrievable;
two projects' graph_ready versions scope-isolated; cleanup stops retrieval
before async delete; rebuild from source.

This test MUST FAIL before capabilities/cleanup/rebuild are complete (TDD).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from rag_mcp.graph.capabilities import can_enter_graph_expansion, is_graph_ready_version
from rag_mcp.graph.extractors.java_call_graph import JavaCallGraphExtractor
from rag_mcp.graph.store.base import GraphScope
from rag_mcp.graph.store.postgres_graph_store import PostgresGraphStore
from rag_mcp.models.knowledge_version import KnowledgeVersion
from rag_mcp.utils.snowflake import generate_id
from tests.unit.test_postgres_graph_store import _insert_chunk, _setup_scope


_JAVA = """package com.example;
public class Svc {
    public void a() { b(); }
    public void b() {}
}
"""


def _chunks(scope_id, version_id, source_id):
    return {
        "cls": {"chunk_id": generate_id(), "symbol_path": "com.example.Svc",
                "symbol_type": "class", "content_text": _JAVA, "start_line": 2, "end_line": 5},
        "a": {"chunk_id": generate_id(), "symbol_path": "com.example.Svc#a",
              "symbol_type": "method", "content_text": "a", "start_line": 3, "end_line": 3},
        "b": {"chunk_id": generate_id(), "symbol_path": "com.example.Svc#b",
              "symbol_type": "method", "content_text": "b", "start_line": 4, "end_line": 4},
    }


@pytest.fixture
async def us5_scope(db_session):
    sa = generate_id(); pa = generate_id(); va = generate_id()
    src = await _setup_scope(db_session, sa, pa, va)
    chunks = _chunks(sa, va, src)
    for c in chunks.values():
        await _insert_chunk(db_session, c["chunk_id"], sa, va, src)
    await db_session.commit()
    return {"scope": GraphScope(sa, pa, 1), "version_id": va,
            "scope_id": sa, "chunks": chunks, "src": src}


@pytest.mark.asyncio
async def test_as5_1_rebuild_grants_graph_ready(db_session, us5_scope):
    """AS5.1: rebuild grants graph_ready; unready not retrievable."""
    scope = us5_scope["scope"]
    store = PostgresGraphStore(db_session)

    # Initially not graph_ready
    result = await db_session.execute(text(
        "SELECT graph_ready FROM knowledge_versions WHERE version_id = :vid"
    ), {"vid": us5_scope["version_id"]})
    assert result.scalar() is False, "Initially not graph_ready"

    # Rebuild (user-triggered, FR-027)
    count = await store.rebuild_graph_edges(
        source_code=_JAVA, chunks=list(us5_scope["chunks"].values()),
        scope=scope, format="java")
    assert count > 0

    # Simulate publishing graph_ready after successful rebuild, with the
    # FR-015 implication (dense+lexical ready) declared in capabilities.
    caps = json.dumps({"dense_ready": True, "lexical_ready": True, "graph_ready": True})
    await db_session.execute(text(
        "UPDATE knowledge_versions SET graph_ready = true, "
        "capabilities = CAST(:caps AS jsonb) WHERE version_id = :vid"
    ), {"vid": us5_scope["version_id"], "caps": caps})
    await db_session.commit()

    # Now graph_ready and has edges -> can enter expansion
    version = await db_session.get(KnowledgeVersion, us5_scope["version_id"])
    has_edges = await store._has_edges_for_scope(scope)
    assert can_enter_graph_expansion(version, has_edges) is True


@pytest.mark.asyncio
async def test_as5_3_cleanup_stops_then_deletes(db_session, us5_scope):
    """AS5.3: cleanup marks non-retrievable first, then deletes (other scope safe)."""
    scope = us5_scope["scope"]
    store = PostgresGraphStore(db_session)

    # Build edges + mark graph_ready
    await store.rebuild_graph_edges(
        source_code=_JAVA, chunks=list(us5_scope["chunks"].values()),
        scope=scope, format="java")
    await db_session.execute(text(
        "UPDATE knowledge_versions SET graph_ready = true WHERE version_id = :vid"
    ), {"vid": us5_scope["version_id"]})
    await db_session.commit()

    # Cleanup: mark first
    await store.mark_graph_unretrievable(scope)
    result = await db_session.execute(text(
        "SELECT graph_ready FROM knowledge_versions WHERE version_id = :vid"
    ), {"vid": us5_scope["version_id"]})
    assert result.scalar() is False, "Cleanup must mark non-retrievable first"

    # Then delete
    await store.delete_graph_relations(scope)
    await db_session.commit()
    result = await db_session.execute(text(
        "SELECT count(*) FROM graph_edge WHERE knowledge_scope_id = :sid"
    ), {"sid": us5_scope["scope_id"]})
    assert result.scalar() == 0, "Graph edges must be deleted after cleanup"


@pytest.mark.asyncio
async def test_as5_4_rebuild_from_source(db_session, us5_scope):
    """AS5.4: graph derived data rebuildable from original source."""
    scope = us5_scope["scope"]
    store = PostgresGraphStore(db_session)

    count = await store.rebuild_graph_edges(
        source_code=_JAVA, chunks=list(us5_scope["chunks"].values()),
        scope=scope, format="java")
    assert count > 0, "Rebuild from source must produce edges"


@pytest.mark.asyncio
async def test_as5_2_two_projects_isolated(db_session, us5_scope):
    """AS5.2: two graph_ready projects expand only within own scope."""
    from rag_mcp.graph.expansion import GraphExpansionEngine

    # Second project
    sb = generate_id(); pb = generate_id(); vb = generate_id()
    src_b = await _setup_scope(db_session, sb, pb, vb)
    chunks_b = _chunks(sb, vb, src_b)
    for c in chunks_b.values():
        await _insert_chunk(db_session, c["chunk_id"], sb, vb, src_b)

    scope_a = us5_scope["scope"]
    scope_b = GraphScope(sb, pb, 1)
    store = PostgresGraphStore(db_session)
    await store.rebuild_graph_edges(_JAVA, list(us5_scope["chunks"].values()), scope_a, "java")
    await store.rebuild_graph_edges(_JAVA, list(chunks_b.values()), scope_b, "java")
    await db_session.commit()

    engine = GraphExpansionEngine(db_session)
    a_chunk = us5_scope["chunks"]["a"]["chunk_id"]
    results = await engine.expand(start_chunk_ids=[a_chunk], scope=scope_a, hop=2, budget=20)
    result_ids = {r.chunk_id for r in results}
    for c in chunks_b.values():
        assert c["chunk_id"] not in result_ids, "Project B chunks leaked into project A"

# ---------------------------------------------------------------------------
# T044: publish-time capability gating + user-triggered graph_ready declaration
# ---------------------------------------------------------------------------


_JAVA_FOR_PUBLISH = """package com.example;
public class Svc {
    public void a() { b(); }
    public void b() {}
}
"""


class TestPublishTimeCapabilityGating:
    """T044: FR-013 readiness gate + FR-015 implication enforced at publish."""

    @pytest.mark.asyncio
    async def test_publish_rejects_broken_capability_implication(self, db_session):
        """FR-015: graph_ready without dense_ready MUST be rejected at publish."""
        from rag_mcp.models.knowledge_version import KnowledgeVersion
        from rag_mcp.services.ingestion_service import IngestionService
        from tests.integration.graph_ingest_helpers import setup_graph_scope

        scope_id = generate_id()
        project_id = generate_id()
        await setup_graph_scope(db_session, scope_id, project_id)
        version_id = generate_id()
        await db_session.execute(text(
            "INSERT INTO knowledge_versions (version_id, knowledge_scope_id, "
            "version_number, status, capabilities) "
            "VALUES (:vid, :sid, 1, 'draft', CAST(:caps AS jsonb))"
        ), {"vid": version_id, "sid": scope_id,
            "caps": '{"graph_ready": true, "dense_ready": false, "lexical_ready": true}'})
        await db_session.commit()

        version = await db_session.get(KnowledgeVersion, version_id)
        svc = IngestionService(db_session, None, None)
        with pytest.raises(ValueError, match="dense_ready"):
            await svc._publish_version(version, scope_id)

    @pytest.mark.asyncio
    async def test_ingest_declares_graph_ready_when_edges_ready(self, db_session):
        """FR-013/FR-027: user-declared graph_ready granted when edges exist."""
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
            db_session, scope_id, source_id, "Svc.java", _JAVA_FOR_PUBLISH, "java"
        )
        await db_session.commit()

        svc = IngestionService(db_session, FakeEmbeddingProvider(), MockQdrantStore())
        await svc.ingest(source_id, graph_ready=True)

        row = (await db_session.execute(text(
            "SELECT graph_ready, capabilities, status FROM knowledge_versions "
            "WHERE knowledge_scope_id = :s ORDER BY version_number DESC LIMIT 1"
        ), {"s": scope_id})).fetchone()
        assert row[0] is True, "graph_ready column must be set"
        caps = row[1]
        if isinstance(caps, str):
            caps = json.loads(caps)
        assert caps.get("graph_ready") is True
        assert caps.get("dense_ready") is True and caps.get("lexical_ready") is True
        assert row[2] == "published"

    @pytest.mark.asyncio
    async def test_ingest_graph_ready_refused_without_edges(self, db_session):
        """FR-013: declaring graph_ready with no graph relations MUST NOT publish."""
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
        # Markdown produces no hard relations -> graph_ready cannot be granted
        await upload_source_file(
            db_session, scope_id, source_id, "notes.md",
            "# A\n\nsome text without relations\n", "markdown"
        )
        await db_session.commit()

        svc = IngestionService(db_session, FakeEmbeddingProvider(), MockQdrantStore())
        with pytest.raises(ValueError, match="graph"):
            await svc.ingest(source_id, graph_ready=True)

        row = (await db_session.execute(text(
            "SELECT status FROM knowledge_sources WHERE source_id = :s"
        ), {"s": source_id})).scalar()
        assert row == "failed", "source must fail when graph relations are absent"
        published = (await db_session.execute(text(
            "SELECT count(*) FROM knowledge_versions "
            "WHERE knowledge_scope_id = :s AND status = 'published' AND graph_ready"
        ), {"s": scope_id})).scalar()
        assert published == 0

