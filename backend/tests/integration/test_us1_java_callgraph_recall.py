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

# ---------------------------------------------------------------------------
# T043: graph expansion wired into the hybrid retrieval path (config-gated)
# ---------------------------------------------------------------------------


class _SearchFakeEmbedding:
    async def embed_query(self, q):
        return [0.1] * 8

    async def embed_texts(self, texts):
        return [[0.1] * 8 for _ in texts]

    def get_dimension(self):
        return 8


class _SearchMockQdrant:
    """Returns only the validateToken chunk from Dense/Sparse recall."""

    def __init__(self, hits):
        self._hits = hits  # list of result dicts

    def collection_exists(self, name):
        return True

    def query_hybrid(self, collection, dense_vector, sparse_vector,
                     scope_ids, version_id, limit):
        return list(self._hits), list(self._hits)

    def search(self, collection, vector, scope_ids, limit):
        return list(self._hits)


def _hit(chunk_id, version_id, scope_id, source_id):
    return {
        "id": chunk_id,
        "score": 0.9,
        "payload": {
            "chunk_id": str(chunk_id),
            "version_id": str(version_id),
            "knowledge_scope_id": str(scope_id),
            "source_id": str(source_id),
            "position_path": "sym",
            "index_version": "bge-m3_v1",
        },
    }


@pytest.fixture
async def us1_search_env(db_session):
    """Graph-ready published version + chunks + edges for a real search()."""
    sa = generate_id(); pa = generate_id(); va = generate_id()
    src_a = await _setup_scope(db_session, sa, pa, va)
    chunks = _make_token_chunks(sa, va, src_a)
    for c in chunks.values():
        await _insert_chunk(
            db_session, c["chunk_id"], sa, va, src_a, content=c["content_text"]
        )
    # Capabilities for the hybrid path + graph_ready declaration
    await db_session.execute(text(
        "UPDATE knowledge_versions SET capabilities = :caps, graph_ready = true "
        "WHERE version_id = :vid"
    ), {"caps": '{"dense_ready": true, "lexical_ready": true}', "vid": va})

    extractor = JavaCallGraphExtractor()
    store = PostgresGraphStore(db_session)
    edges = extractor.extract(_JAVA_SOURCE, list(chunks.values()), GraphScope(sa, pa, 1))
    await store.write_edges(edges, GraphScope(sa, pa, 1))
    await db_session.commit()

    vt = chunks["validateToken"]["chunk_id"]
    return {"sa": sa, "pa": pa, "va": va, "src_a": src_a,
            "chunks": chunks, "vt": vt}


class TestGraphEnhancedRetrievalPath:
    """T043: graph expansion is the 3rd fusion input, behind a config switch.

    Default OFF keeps the deterministic 001/002 path untouched; enabling
    GRAPH_ENHANCED_RETRIEVAL_ENABLED=true adds graph-recalled evidence.
    """

    @pytest.mark.asyncio
    async def test_graph_off_default_path_unchanged(self, db_session, us1_search_env, monkeypatch):
        """Switch OFF (default): only Dense/Sparse hits are returned."""
        from rag_mcp.services.retrieval_service import RetrievalService

        env = us1_search_env
        monkeypatch.delenv("GRAPH_ENHANCED_RETRIEVAL_ENABLED", raising=False)
        qdrant = _SearchMockQdrant([_hit(env["vt"], env["va"], env["sa"], env["src_a"])])
        svc = RetrievalService(db_session, qdrant, _SearchFakeEmbedding())

        resp = await svc.search("validateToken", [str(env["pa"])], top_k=5)
        assert resp["completion_status"] == "complete"
        ids = {e["evidence_id"] for e in resp["evidence"]}
        assert ids == {str(env["vt"])}, "default path must not add graph evidence"

    @pytest.mark.asyncio
    async def test_graph_on_recalls_callers_and_callees(self, db_session, us1_search_env, monkeypatch):
        """Switch ON: callers/callees of validateToken appear via graph (AS1.1)."""
        from rag_mcp.services.retrieval_service import RetrievalService

        env = us1_search_env
        monkeypatch.setenv("GRAPH_ENHANCED_RETRIEVAL_ENABLED", "true")
        qdrant = _SearchMockQdrant([_hit(env["vt"], env["va"], env["sa"], env["src_a"])])
        svc = RetrievalService(db_session, qdrant, _SearchFakeEmbedding())

        resp = await svc.search("validateToken", [str(env["pa"])], top_k=5)
        assert resp["completion_status"] == "complete"
        ids = {e["evidence_id"] for e in resp["evidence"]}
        expected_graph = {
            str(env["chunks"]["processRequest"]["chunk_id"]),
            str(env["chunks"]["logAccess"]["chunk_id"]),
            str(env["chunks"]["checkSignature"]["chunk_id"]),
        }
        assert expected_graph & ids, f"graph expansion added no evidence: {ids}"

    @pytest.mark.asyncio
    async def test_graph_on_records_subpath_timing(self, db_session, us1_search_env, monkeypatch):
        """RetrievalRun.subpath_timings MUST include graph_recall_ms (FR-026)."""
        import json

        from rag_mcp.services.retrieval_service import RetrievalService

        env = us1_search_env
        monkeypatch.setenv("GRAPH_ENHANCED_RETRIEVAL_ENABLED", "true")
        qdrant = _SearchMockQdrant([_hit(env["vt"], env["va"], env["sa"], env["src_a"])])
        svc = RetrievalService(db_session, qdrant, _SearchFakeEmbedding())
        await svc.search("validateToken", [str(env["pa"])], top_k=5)

        row = (await db_session.execute(text(
            "SELECT subpath_timings, retrieval_mode FROM retrieval_runs "
            "ORDER BY run_id DESC LIMIT 1"
        ))).fetchone()
        timings = row[0]
        if isinstance(timings, str):
            timings = json.loads(timings)
        assert "graph_recall_ms" in timings
        assert row[1] == "graph_enhanced"

    @pytest.mark.asyncio
    async def test_graph_failure_degrades_to_partial(self, db_session, us1_search_env, monkeypatch):
        """Graph sub-path failure -> partial with hybrid evidence kept (FR-018)."""
        from rag_mcp.services.retrieval_service import RetrievalService

        async def boom(*args, **kwargs):
            raise RuntimeError("graph store unavailable")

        monkeypatch.setenv("GRAPH_ENHANCED_RETRIEVAL_ENABLED", "true")
        monkeypatch.setattr(
            "rag_mcp.graph.store.postgres_graph_store.PostgresGraphStore.expand", boom
        )

        env = us1_search_env
        qdrant = _SearchMockQdrant([_hit(env["vt"], env["va"], env["sa"], env["src_a"])])
        svc = RetrievalService(db_session, qdrant, _SearchFakeEmbedding())

        resp = await svc.search("validateToken", [str(env["pa"])], top_k=5)
        assert resp["completion_status"] == "partial"
        ids = {e["evidence_id"] for e in resp["evidence"]}
        assert str(env["vt"]) in ids, "hybrid evidence must survive graph failure"
        assert any("graph" in g["description"] for g in resp.get("gaps", []))

    @pytest.mark.asyncio
    async def test_no_graph_ready_version_skips_graph(self, db_session, us1_search_env, monkeypatch):
        """FR-014: without a graph_ready version the graph path is skipped."""
        from rag_mcp.services.retrieval_service import RetrievalService

        env = us1_search_env
        await db_session.execute(text(
            "UPDATE knowledge_versions SET graph_ready = false WHERE version_id = :vid"
        ), {"vid": env["va"]})
        await db_session.commit()

        monkeypatch.setenv("GRAPH_ENHANCED_RETRIEVAL_ENABLED", "true")
        qdrant = _SearchMockQdrant([_hit(env["vt"], env["va"], env["sa"], env["src_a"])])
        svc = RetrievalService(db_session, qdrant, _SearchFakeEmbedding())

        resp = await svc.search("validateToken", [str(env["pa"])], top_k=5)
        assert resp["completion_status"] == "complete"
        ids = {e["evidence_id"] for e in resp["evidence"]}
        assert ids == {str(env["vt"])}, "non-graph_ready version must stay hybrid-only"

class TestGraphEvidenceRelationAnnotation:
    """T046: graph-recalled evidence carries hard/soft relation annotations."""

    @pytest.mark.asyncio
    async def test_graph_evidence_carries_hard_annotation(self, db_session, us1_search_env, monkeypatch):
        import jsonschema
        from tests.contract._graph_schema_helper import load_schema

        from rag_mcp.services.retrieval_service import RetrievalService

        env = us1_search_env
        monkeypatch.setenv("GRAPH_ENHANCED_RETRIEVAL_ENABLED", "true")
        qdrant = _SearchMockQdrant([_hit(env["vt"], env["va"], env["sa"], env["src_a"])])
        svc = RetrievalService(db_session, qdrant, _SearchFakeEmbedding())

        resp = await svc.search("validateToken", [str(env["pa"])], top_k=5)
        assert resp["completion_status"] == "complete"

        graph_chunk_ids = {
            str(env["chunks"]["processRequest"]["chunk_id"]),
            str(env["chunks"]["logAccess"]["chunk_id"]),
            str(env["chunks"]["checkSignature"]["chunk_id"]),
        }
        annotated = {}
        for item in resp["evidence"]:
            if "relation" in item:
                annotated[item["evidence_id"]] = item["relation"]
        assert annotated, "graph-recalled evidence MUST carry relation annotation"
        for eid, rel in annotated.items():
            assert eid in graph_chunk_ids, "only graph-recalled evidence is annotated"
            assert rel["type"] == "hard"
            assert rel["is_hard"] is True
            assert rel["relation_type"] in (
                "calls", "called_by", "fk_references", "fk_referenced_by", "other_hard"
            )
            assert rel["edge_id"]
            assert rel["parse_evidence"]["extractor"] == "java_call_graph"

        # The dense-only hit stays unannotated (annotation is additive per path)
        vt_item = next(i for i in resp["evidence"] if i["evidence_id"] == str(env["vt"]))
        assert "relation" not in vt_item

        # Whole response validates against the 004 annotation extension schema
        schema = load_schema("mcp-search-output.graph-annotation.schema.json")
        jsonschema.validate(resp, schema)


