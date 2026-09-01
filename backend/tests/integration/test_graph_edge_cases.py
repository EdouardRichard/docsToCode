"""Edge-case tests for graph RAG (T035).

Covers spec Edge Cases: AST degradation (no fabrication), DDL no-FK,
high fan-out truncation, partial/failed four states, hard>soft conflict,
low-confidence soft exclusion, graph_ready corruption degradation,
version revoked no silent replacement.

This test MUST FAIL before edge cases are handled (TDD).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from rag_mcp.graph.capabilities import can_enter_graph_expansion, is_graph_ready_version
from rag_mcp.graph.expansion import GraphExpansionEngine
from rag_mcp.graph.extractors.ddl_fk import DdlFkExtractor
from rag_mcp.graph.extractors.java_call_graph import JavaCallGraphExtractor
from rag_mcp.graph.models import GraphEdge, SoftRelation
from rag_mcp.graph.store.base import GraphScope
from rag_mcp.graph.store.postgres_graph_store import PostgresGraphStore
from rag_mcp.services.evidence_service import EvidenceService
from rag_mcp.utils.snowflake import generate_id
from tests.unit.test_postgres_graph_store import _insert_chunk, _setup_scope


class TestAstDegradation:
    def test_invalid_java_no_fabricated_edges(self):
        """AST failure MUST report degradation, NOT fabricate edges (Edge Case)."""
        extractor = JavaCallGraphExtractor()
        scope = GraphScope(100, 200, 1)
        edges = extractor.extract("this is not { valid java (((", [], scope)
        assert edges == [], "AST failure must not fabricate edges"

    def test_empty_source_no_edges(self):
        extractor = JavaCallGraphExtractor()
        scope = GraphScope(100, 200, 1)
        assert extractor.extract("", [], scope) == []
        assert extractor.extract("   ", [], scope) == []


class TestNoFkDdl:
    def test_ddl_without_fk_produces_no_edges(self):
        """DDL with no foreign keys produces no edges (Edge Case)."""
        extractor = DdlFkExtractor()
        scope = GraphScope(100, 200, 1)
        chunks = [{"chunk_id": 1, "symbol_path": "table:simple",
                   "symbol_type": "table", "content_text": "simple", "start_line": 1, "end_line": 3}]
        edges = extractor.extract("CREATE TABLE simple (id INT PRIMARY KEY);", chunks, scope)
        assert edges == []

    def test_ddl_fk_to_absent_table_skipped(self):
        """FK referencing a table not in project produces no edge (only determinable)."""
        extractor = DdlFkExtractor()
        scope = GraphScope(100, 200, 1)
        # orders references users, but users chunk not provided
        chunks = [{"chunk_id": 1, "symbol_path": "table:orders",
                   "symbol_type": "table", "content_text": "orders", "start_line": 1, "end_line": 6}]
        ddl = "CREATE TABLE orders (id INT, user_id INT, FOREIGN KEY (user_id) REFERENCES users(id));"
        edges = extractor.extract(ddl, chunks, scope)
        assert edges == [], "FK to absent table must not fabricate an edge"


@pytest.fixture
async def fanout_scope(db_session):
    sa = generate_id(); pa = generate_id(); va = generate_id()
    src = await _setup_scope(db_session, sa, pa, va)
    hub = generate_id()
    await _insert_chunk(db_session, hub, sa, va, src)
    targets = []
    for i in range(25):  # high fan-out: 25 edges
        tgt = generate_id()
        await _insert_chunk(db_session, tgt, sa, va, src)
        await db_session.execute(text(
            "INSERT INTO graph_edge (edge_id, knowledge_scope_id, project_id, "
            "index_version, source_chunk_id, target_chunk_id, relation_type, "
            "direction, is_hard, version, parse_evidence) "
            "VALUES (:eid, :ksid, :pid, 1, :src, :tgt, 'calls', 'out', true, 1, "
            "CAST(:pe AS jsonb))"
        ), {"eid": generate_id(), "ksid": sa, "pid": pa, "src": hub, "tgt": tgt,
            "pe": json.dumps({"source_format": "java", "locator": "x", "extractor": "e"})})
        targets.append(tgt)
    await db_session.commit()
    return {"scope": GraphScope(sa, pa, 1), "hub": hub, "targets": targets}


class TestFanOutTruncation:
    @pytest.mark.asyncio
    async def test_high_fanout_truncated_to_budget(self, db_session, fanout_scope):
        """High fan-out MUST be truncated to total budget (FR-017, Edge Case)."""
        store = PostgresGraphStore(db_session)
        results = await store.expand(
            [fanout_scope["hub"]], fanout_scope["scope"], hop=1, budget=10)
        assert len(results) <= 10, f"Fan-out must be truncated to budget, got {len(results)}"

    @pytest.mark.asyncio
    async def test_fanout_sorted_by_weight(self, db_session, fanout_scope):
        store = PostgresGraphStore(db_session)
        results = await store.expand(
            [fanout_scope["hub"]], fanout_scope["scope"], hop=1, budget=20)
        weights = [r.structure_weight for r in results]
        assert weights == sorted(weights, reverse=True)


class TestHardSoftConflict:
    def test_hard_wins_over_soft_in_annotation(self):
        """Hard>soft conflict: hard evidence wins (Constitution II/III, Edge Case)."""
        import datetime
        es = EvidenceService(None)
        hard = GraphEdge(
            edge_id=1, knowledge_scope_id=100, project_id=200, index_version=1,
            source_chunk_id=300, target_chunk_id=301, relation_type="calls",
            direction="out", is_hard=True, version=1,
            parse_evidence={"source_format": "java", "locator": "x", "extractor": "e"})
        soft = SoftRelation(
            edge_id=2, knowledge_scope_id=100, project_id=200, index_version=1,
            source_chunk_id=300, target_chunk_id=301, relation_type="inferred",
            direction="out", is_hard=False, version=1,
            inference_source="llm", confidence=0.9, model_and_version="m",
            generated_at=datetime.datetime.now(datetime.timezone.utc),
            supporting_evidence_ids=[999], lifecycle_state="active")
        evidence = {"evidence_id": "1", "full_content": "c", "source_version": 1,
                    "source_position": "p", "knowledge_scope_id": "s",
                    "knowledge_scope_type": "project", "status": "available"}
        annotated = es.annotate_evidence(dict(evidence), relation_edge=hard, soft_relation=soft)
        assert annotated["relation"]["type"] == "hard", "Hard must win over soft"
        assert annotated["relation"]["is_hard"] is True


class TestLowConfidenceExclusion:
    def test_low_confidence_stays_inferred(self):
        """Low-confidence soft relation excluded from default path (FR-005, Edge Case)."""
        from rag_mcp.graph.soft_relation_inference import SoftRelationInference
        inference = SoftRelationInference()
        rels = inference.infer(
            chunks=[], scope=GraphScope(100, 200, 1),
            llm=lambda c: [(300, 301, 0.2, [999])],
            model_and_version="m", inference_source="llm", direction="out", version=1)
        assert rels[0].lifecycle_state == "inferred", "Low confidence must not be active"

    def test_empty_evidence_stays_inferred(self):
        """Active requires non-empty supporting evidence (Edge Case)."""
        from rag_mcp.graph.soft_relation_inference import SoftRelationInference
        inference = SoftRelationInference()
        rels = inference.infer(
            chunks=[], scope=GraphScope(100, 200, 1),
            llm=lambda c: [(300, 301, 0.9, [])],
            model_and_version="m", inference_source="llm", direction="out", version=1)
        assert rels[0].lifecycle_state == "inferred", "Empty evidence must not be active"


class TestGraphReadyCorruption:
    def test_graph_ready_without_dense_degraded(self):
        """graph_ready corruption (missing dense_ready) MUST NOT open expansion (Edge Case)."""

        class FakeVersion:
            graph_ready = True
            capabilities = {"graph_ready": True}  # corrupt: missing dense_ready/lexical_ready

        assert is_graph_ready_version(FakeVersion()) is False, (
            "Corrupt capabilities must degrade to non-graph-ready"
        )
        assert can_enter_graph_expansion(FakeVersion(), True) is False


class TestVersionRevoked:
    @pytest.mark.asyncio
    async def test_revoked_version_graph_removed(self, db_session):
        """Revoked/cleaned version's graph relations removed, no silent replacement (Edge Case)."""
        sa = generate_id(); pa = generate_id(); va = generate_id()
        src = await _setup_scope(db_session, sa, pa, va)
        c1 = generate_id(); c2 = generate_id()
        await _insert_chunk(db_session, c1, sa, va, src)
        await _insert_chunk(db_session, c2, sa, va, src)
        await db_session.execute(text(
            "INSERT INTO graph_edge (edge_id, knowledge_scope_id, project_id, "
            "index_version, source_chunk_id, target_chunk_id, relation_type, "
            "direction, is_hard, version, parse_evidence) "
            "VALUES (:eid, :ksid, :pid, 1, :src, :tgt, 'calls', 'out', true, 1, "
            "CAST(:pe AS jsonb))"
        ), {"eid": generate_id(), "ksid": sa, "pid": pa, "src": c1, "tgt": c2,
            "pe": json.dumps({"source_format": "java", "locator": "x", "extractor": "e"})})
        await db_session.commit()

        store = PostgresGraphStore(db_session)
        scope = GraphScope(sa, pa, 1)
        # Revoke: cleanup removes graph relations
        await store.cleanup_scope(scope)
        await db_session.commit()

        result = await db_session.execute(text(
            "SELECT count(*) FROM graph_edge WHERE knowledge_scope_id = :sid"
        ), {"sid": sa})
        assert result.scalar() == 0, "Revoked version graph relations must be removed"
