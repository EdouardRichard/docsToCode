"""Integration test for the runtime graph-expansion trace ledger (T041/T045).

Validates that a real graph-enhanced search() records the full trace
(FR-026): subpath timings with graph_recall_ms, graph candidates with
edge_path, fused candidates, completion status, evidence_id backfill and the
DM-1 bridge rows in graph_expansion_path. A failing graph sub-path MUST
produce a partial trace with non-empty failed_paths.

This test MUST FAIL before the trace recorder is wired into the retrieval
path (TDD).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

import jsonschema

from rag_mcp.graph.extractors.java_call_graph import JavaCallGraphExtractor
from rag_mcp.graph.store.base import GraphScope
from rag_mcp.graph.store.postgres_graph_store import PostgresGraphStore
from rag_mcp.utils.snowflake import generate_id
from tests.contract._graph_schema_helper import graph_trace_schema, inline_refs, common_schema, graph_relations_schema
from tests.unit.test_postgres_graph_store import _insert_chunk, _setup_scope


_JAVA_SOURCE = """package com.example.service;

public class TokenService {
    public void processRequest(String req) {
        validateToken(req);
    }
    private boolean validateToken(String token) {
        return checkSignature(token);
    }
    private boolean checkSignature(String token) {
        return token != null;
    }
}
"""


class _FakeEmbedding:
    async def embed_query(self, q):
        return [0.1] * 8

    async def embed_texts(self, texts):
        return [[0.1] * 8 for _ in texts]

    def get_dimension(self):
        return 8


class _MockQdrant:
    def __init__(self, hits):
        self._hits = hits

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


def _chunks(scope_id, version_id, source_id):
    return {
        "cls": {"chunk_id": generate_id(), "symbol_path": "com.example.service.TokenService",
                "symbol_type": "class", "content_text": _JAVA_SOURCE, "start_line": 3, "end_line": 13},
        "processRequest": {"chunk_id": generate_id(), "symbol_path": "com.example.service.TokenService#processRequest",
                "symbol_type": "method", "content_text": "processRequest validateToken", "start_line": 4, "end_line": 6},
        "validateToken": {"chunk_id": generate_id(), "symbol_path": "com.example.service.TokenService#validateToken",
                "symbol_type": "method", "content_text": "validateToken checkSignature", "start_line": 7, "end_line": 9},
        "checkSignature": {"chunk_id": generate_id(), "symbol_path": "com.example.service.TokenService#checkSignature",
                "symbol_type": "method", "content_text": "checkSignature", "start_line": 10, "end_line": 12},
    }


@pytest.fixture
async def trace_env(db_session):
    sa = generate_id(); pa = generate_id(); va = generate_id()
    src = await _setup_scope(db_session, sa, pa, va)
    chunks = _chunks(sa, va, src)
    for c in chunks.values():
        await _insert_chunk(db_session, c["chunk_id"], sa, va, src, content=c["content_text"])
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
    return {"sa": sa, "pa": pa, "va": va, "src": src, "chunks": chunks, "vt": vt}


@pytest.fixture
def trace_schema():
    return inline_refs(graph_trace_schema(), common_schema(), graph_relations_schema(), graph_trace_schema())


@pytest.mark.asyncio
async def test_graph_search_records_complete_trace(db_session, trace_env, trace_schema, monkeypatch):
    """One graph-enhanced search MUST record the full runtime trace (FR-026)."""
    from rag_mcp.services.retrieval_service import RetrievalService

    env = trace_env
    monkeypatch.setenv("GRAPH_ENHANCED_RETRIEVAL_ENABLED", "true")
    qdrant = _MockQdrant([_hit(env["vt"], env["va"], env["sa"], env["src"])])
    svc = RetrievalService(db_session, qdrant, _FakeEmbedding())

    resp = await svc.search("validateToken", [str(env["pa"])], top_k=5)
    assert resp["completion_status"] == "complete"
    evidence_ids = {e["evidence_id"] for e in resp["evidence"]}

    # 1. RetrievalRun ledger: graph_enhanced mode + graph_recall_ms timing
    run_row = (await db_session.execute(text(
        "SELECT run_id, retrieval_mode, subpath_timings FROM retrieval_runs "
        "ORDER BY run_id DESC LIMIT 1"
    ))).fetchone()
    run_id, mode, timings = run_row
    assert mode == "graph_enhanced"
    assert "graph_recall_ms" in (timings or {})

    # 2. DM-1 bridge: graph_expansion_path rows for surviving evidence
    path_rows = (await db_session.execute(text(
        "SELECT evidence_id, chunk_id, start_chunk_id, edge_path, hop_count, "
        "structure_weight, graph_rank FROM graph_expansion_path "
        "WHERE request_id = :rid"
    ), {"rid": run_id})).fetchall()
    assert path_rows, "surviving graph candidates MUST be bridged to evidence"
    for eid, cid, scid, edge_path, hops, weight, rank in path_rows:
        assert str(eid) in evidence_ids, "bridged evidence_id must be returned evidence"
        assert eid == cid
        assert edge_path and isinstance(edge_path, list)
        assert 1 <= hops <= 3
        assert weight > 0
        assert rank >= 1

    # 3. Recorder trace dict conforms to graph-expansion-trace.schema.json
    trace = getattr(svc, "_last_graph_trace", None)
    assert trace is not None, "service must expose the recorded graph trace"
    jsonschema.validate(trace, trace_schema)
    assert trace["completion_status"] == "complete"
    assert trace["graph_candidates"], "trace must carry graph candidates"
    assert any(c.get("evidence_id") for c in trace["graph_candidates"]), (
        "surviving candidates must have evidence_id backfilled (DM-1)"
    )
    assert trace["fused_candidates"], "trace must carry fused candidates"


@pytest.mark.asyncio
async def test_failed_graph_trace_is_partial_with_failed_paths(
    db_session, trace_env, trace_schema, monkeypatch
):
    """Graph sub-path failure -> partial trace with non-empty failed_paths."""
    from rag_mcp.services.retrieval_service import RetrievalService

    async def boom(*args, **kwargs):
        raise RuntimeError("graph store unavailable")

    env = trace_env
    monkeypatch.setenv("GRAPH_ENHANCED_RETRIEVAL_ENABLED", "true")
    monkeypatch.setattr(
        "rag_mcp.graph.store.postgres_graph_store.PostgresGraphStore.expand", boom
    )
    qdrant = _MockQdrant([_hit(env["vt"], env["va"], env["sa"], env["src"])])
    svc = RetrievalService(db_session, qdrant, _FakeEmbedding())

    resp = await svc.search("validateToken", [str(env["pa"])], top_k=5)
    assert resp["completion_status"] == "partial"

    trace = getattr(svc, "_last_graph_trace", None)
    assert trace is not None
    jsonschema.validate(trace, trace_schema)
    assert trace["completion_status"] == "partial"
    assert trace.get("failed_paths"), "partial trace MUST carry failed_paths"
