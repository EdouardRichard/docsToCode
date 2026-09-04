"""Story integration test for US4: soft relation distinguishable (T030).

Validates AS4.1-4.3: soft relations carry 5 metadata and are locatable;
hard/soft conflict returned distinguishably (soft never silently overrides
hard); low-confidence soft relations excluded from default path (FR-005).

This test MUST FAIL before the annotation + inference are complete (TDD).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from rag_mcp.graph.models import GraphEdge, SoftRelation
from rag_mcp.graph.soft_relation_inference import SoftRelationInference
from rag_mcp.graph.store.base import GraphScope
from rag_mcp.services.evidence_service import EvidenceService
from rag_mcp.utils.snowflake import generate_id
from tests.unit.test_postgres_graph_store import _insert_chunk, _setup_scope


@pytest.fixture
async def us4_scope(db_session):
    sa = generate_id(); pa = generate_id(); va = generate_id()
    src_a = await _setup_scope(db_session, sa, pa, va)
    src_chunk = generate_id(); tgt_chunk = generate_id()
    for cid in (src_chunk, tgt_chunk):
        await _insert_chunk(db_session, cid, sa, va, src_a)
    await db_session.commit()
    return {"scope": GraphScope(sa, pa, 1), "src": src_chunk, "tgt": tgt_chunk}


@pytest.mark.asyncio
async def test_as4_1_soft_relation_five_metadata_locatable(db_session, us4_scope):
    """AS4.1: soft relation carries 5 metadata and is independently locatable."""
    scope = us4_scope["scope"]
    inference = SoftRelationInference()
    relations = inference.infer(
        chunks=[], scope=scope,
        llm=lambda c: [(us4_scope["src"], us4_scope["tgt"], 0.85, [999])],
        model_and_version="local-llm-v1", inference_source="llm-offline",
        direction="out", version=1,
    )
    rel = relations[0]
    # All 5 metadata present
    assert rel.inference_source == "llm-offline"
    assert float(rel.confidence) == 0.85
    assert rel.model_and_version == "local-llm-v1"
    assert rel.generated_at is not None
    assert rel.supporting_evidence_ids == [999]
    assert rel.lifecycle_state == "active"


@pytest.mark.asyncio
async def test_as4_2_hard_soft_distinguishable_no_silent_override(db_session, us4_scope):
    """AS4.2: hard/soft evidence annotated distinguishably; soft never overrides hard."""
    evidence_service = EvidenceService(db_session)

    hard_edge = GraphEdge(
        edge_id=generate_id(), knowledge_scope_id=us4_scope["scope"].knowledge_scope_id,
        project_id=us4_scope["scope"].project_id, index_version=1,
        source_chunk_id=us4_scope["src"], target_chunk_id=us4_scope["tgt"],
        relation_type="calls", direction="out", is_hard=True, version=1,
        parse_evidence={"source_format": "java", "locator": "x", "extractor": "java_call_graph"},
    )
    soft_rel = SoftRelation(
        edge_id=generate_id(), knowledge_scope_id=us4_scope["scope"].knowledge_scope_id,
        project_id=us4_scope["scope"].project_id, index_version=1,
        source_chunk_id=us4_scope["src"], target_chunk_id=us4_scope["tgt"],
        relation_type="inferred", direction="out", is_hard=False, version=1,
        inference_source="llm", confidence=0.8, model_and_version="m",
        generated_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        supporting_evidence_ids=[999], lifecycle_state="active",
    )

    # Annotate with hard only
    hard_evidence = {"evidence_id": "1", "full_content": "x", "source_version": 1,
                     "source_position": "p", "knowledge_scope_id": "s",
                     "knowledge_scope_type": "project", "status": "available"}
    hard_annotated = evidence_service.annotate_evidence(dict(hard_evidence), relation_edge=hard_edge)
    assert hard_annotated["relation"]["type"] == "hard"
    assert hard_annotated["relation"]["is_hard"] is True

    # Annotate with soft only
    soft_annotated = evidence_service.annotate_evidence(dict(hard_evidence), soft_relation=soft_rel)
    assert soft_annotated["relation"]["type"] == "soft"
    assert soft_annotated["relation"]["is_hard"] is False

    # Distinguishable
    assert hard_annotated["relation"]["type"] != soft_annotated["relation"]["type"]

    # When both provided, hard wins (soft never silently overrides hard)
    both_annotated = evidence_service.annotate_evidence(
        dict(hard_evidence), relation_edge=hard_edge, soft_relation=soft_rel)
    assert both_annotated["relation"]["type"] == "hard", (
        "Soft relation must not silently override hard relation (Constitution III)"
    )


@pytest.mark.asyncio
async def test_as4_3_low_confidence_not_active(db_session, us4_scope):
    """AS4.3: low-confidence soft relations not active (FR-005)."""
    scope = us4_scope["scope"]
    inference = SoftRelationInference()
    relations = inference.infer(
        chunks=[], scope=scope,
        llm=lambda c: [(us4_scope["src"], us4_scope["tgt"], 0.3, [999])],
        model_and_version="local-llm-v1", inference_source="llm-offline",
        direction="out", version=1,
    )
    rel = relations[0]
    assert rel.lifecycle_state != "active", (
        "Low-confidence (<0.6) soft relation must not be active"
    )


# ---------------------------------------------------------------------------
# T046: soft-relation evidence annotation flows through the search path
# ---------------------------------------------------------------------------


class _SoftFakeEmbedding:
    async def embed_query(self, q):
        return [0.1] * 8

    async def embed_texts(self, texts):
        return [[0.1] * 8 for _ in texts]

    def get_dimension(self):
        return 8


class _SoftMockQdrant:
    def __init__(self, hits):
        self._hits = hits

    def collection_exists(self, name):
        return True

    def query_hybrid(self, collection, dense_vector, sparse_vector,
                     scope_ids, version_id, limit):
        return list(self._hits), list(self._hits)

    def search_dense_named(self, collection, vector, scope_ids=None,
                           version_id=None, limit=5):
        return list(self._hits)

    def search_sparse(self, collection, sparse_vector, scope_ids=None,
                      version_id=None, limit=5):
        return list(self._hits)

    def search(self, collection, vector, scope_ids, limit):
        return list(self._hits)


def _soft_hit(chunk_id, version_id, scope_id, source_id):
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
async def soft_search_env(db_session):
    from datetime import datetime, timezone

    sa = generate_id(); pa = generate_id(); va = generate_id()
    src = await _setup_scope(db_session, sa, pa, va)
    chunk_a = generate_id()
    chunk_b = generate_id()
    chunk_c = generate_id()
    await _insert_chunk(db_session, chunk_a, sa, va, src, content="orderService placeOrder")
    await _insert_chunk(db_session, chunk_b, sa, va, src, content="userService verifyIdentity")
    await _insert_chunk(db_session, chunk_c, sa, va, src, content="paymentService charge")
    await db_session.execute(text(
        "UPDATE knowledge_versions SET capabilities = :caps, graph_ready = true "
        "WHERE version_id = :vid"
    ), {"caps": '{"dense_ready": true, "lexical_ready": true}', "vid": va})

    # Active soft relation A -> B (five metadata + active state)
    edge_id = generate_id()
    await db_session.execute(text(
        "INSERT INTO soft_relation (edge_id, knowledge_scope_id, project_id, "
        "index_version, source_chunk_id, target_chunk_id, relation_type, "
        "direction, is_hard, version, inference_source, confidence, "
        "model_and_version, generated_at, supporting_evidence_ids, lifecycle_state) "
        "VALUES (:eid, :ksid, :pid, 1, :src, :tgt, 'inferred', 'out', false, 1, "
        "'llm-offline', 0.9, 'offline-llm-v1', :now, CAST(:ev AS jsonb), 'active')"
    ), {"eid": edge_id, "ksid": sa, "pid": pa, "src": chunk_a, "tgt": chunk_b,
        "now": datetime.now(timezone.utc), "ev": json.dumps([str(chunk_a)])})

    # Low-confidence inferred relation A -> C must NOT participate (FR-005)
    await db_session.execute(text(
        "INSERT INTO soft_relation (edge_id, knowledge_scope_id, project_id, "
        "index_version, source_chunk_id, target_chunk_id, relation_type, "
        "direction, is_hard, version, inference_source, confidence, "
        "model_and_version, generated_at, supporting_evidence_ids, lifecycle_state) "
        "VALUES (:eid, :ksid, :pid, 1, :src, :tgt, 'inferred', 'out', false, 1, "
        "'llm-offline', 0.2, 'offline-llm-v1', :now, CAST(:ev AS jsonb), 'inferred')"
    ), {"eid": generate_id(), "ksid": sa, "pid": pa, "src": chunk_a, "tgt": chunk_c,
        "now": datetime.now(timezone.utc), "ev": json.dumps([str(chunk_a)])})
    await db_session.commit()

    return {"sa": sa, "pa": pa, "va": va, "src": src,
            "a": chunk_a, "b": chunk_b, "c": chunk_c, "edge_id": edge_id}


class TestSoftRelationAnnotationInSearch:
    """T046: active soft relations join expansion as low-weight candidates and
    their evidence is annotated as inferred — distinguishable from hard."""

    @pytest.mark.asyncio
    async def test_soft_evidence_annotated_as_inferred(self, db_session, soft_search_env, monkeypatch):
        import jsonschema
        from tests.contract._graph_schema_helper import load_schema

        from rag_mcp.services.retrieval_service import RetrievalService

        env = soft_search_env
        monkeypatch.setenv("GRAPH_ENHANCED_RETRIEVAL_ENABLED", "true")
        qdrant = _SoftMockQdrant([_soft_hit(env["a"], env["va"], env["sa"], env["src"])])
        svc = RetrievalService(db_session, qdrant, _SoftFakeEmbedding())

        resp = await svc.search("placeOrder", [str(env["pa"])], top_k=5)
        assert resp["completion_status"] == "complete"

        b_item = next(
            (i for i in resp["evidence"] if i["evidence_id"] == str(env["b"])), None
        )
        assert b_item is not None, "active soft relation MUST recall the target chunk"
        rel = b_item.get("relation")
        assert rel is not None, "soft-recalled evidence MUST carry annotation"
        assert rel["type"] == "soft"
        assert rel["is_hard"] is False
        assert rel["relation_type"] == "inferred"
        assert rel["confidence"] >= 0.6
        assert rel["model_and_version"] == "offline-llm-v1"
        assert rel["lifecycle_state"] == "active"

        schema = load_schema("mcp-search-output.graph-annotation.schema.json")
        jsonschema.validate(resp, schema)

    @pytest.mark.asyncio
    async def test_low_confidence_soft_not_recalled(self, db_session, soft_search_env, monkeypatch):
        """FR-005: lifecycle_state='inferred' (low confidence) soft relations
        MUST NOT enter the retrieval path, while the active edge still works."""
        from rag_mcp.services.retrieval_service import RetrievalService

        env = soft_search_env
        monkeypatch.setenv("GRAPH_ENHANCED_RETRIEVAL_ENABLED", "true")
        # Seed from A: active A->B recalls B; low-confidence A->C recalls nothing
        qdrant = _SoftMockQdrant([_soft_hit(env["a"], env["va"], env["sa"], env["src"])])
        svc = RetrievalService(db_session, qdrant, _SoftFakeEmbedding())

        resp = await svc.search("placeOrder", [str(env["pa"])], top_k=5)
        ids = {i["evidence_id"] for i in resp["evidence"]}
        assert str(env["c"]) not in ids, (
            "low-confidence inferred soft relation must not recall evidence"
        )
        assert str(env["b"]) in ids, "active soft relation must still recall"

