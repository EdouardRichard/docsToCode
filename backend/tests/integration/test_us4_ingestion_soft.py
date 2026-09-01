"""Integration test for US4: soft relation inference at ingest (T029).

Validates that ingestion triggers soft relation inference, writes soft_relation
with 5 metadata and state, and active soft relations enter retrieval as
low-weight supplement (structure_weight 0.3) (FR-003/FR-005).

This test MUST FAIL before the inference is wired (TDD).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from rag_mcp.graph.soft_relation_inference import SoftRelationInference
from rag_mcp.graph.store.base import GraphScope
from rag_mcp.utils.snowflake import generate_id
from tests.unit.test_postgres_graph_store import _insert_chunk, _setup_scope


@pytest.fixture
async def soft_scope(db_session):
    scope_id = generate_id()
    project_id = generate_id()
    version_id = generate_id()
    source_id = await _setup_scope(db_session, scope_id, project_id, version_id)

    src_chunk = generate_id()
    tgt_chunk = generate_id()
    for cid in (src_chunk, tgt_chunk):
        await _insert_chunk(db_session, cid, scope_id, version_id, source_id)
    await db_session.commit()
    return {
        "scope": GraphScope(scope_id, project_id, 1),
        "src": src_chunk, "tgt": tgt_chunk,
        "source_id": source_id, "version_id": version_id,
    }


def _mock_llm(src, tgt):
    """Mock LLM that returns a single inferred relation."""
    return [(src, tgt, 0.85, [999])]


@pytest.mark.asyncio
async def test_soft_relation_written_at_ingest(db_session, soft_scope):
    """Ingestion MUST trigger soft relation inference and write soft_relation."""
    scope = soft_scope["scope"]
    inference = SoftRelationInference()

    relations = inference.infer(
        chunks=[{"chunk_id": soft_scope["src"]}, {"chunk_id": soft_scope["tgt"]}],
        scope=scope,
        llm=lambda chunks: _mock_llm(soft_scope["src"], soft_scope["tgt"]),
        model_and_version="local-llm-v1",
        inference_source="llm-offline",
        direction="out",
        version=1,
    )
    assert len(relations) > 0

    # Write to DB
    for rel in relations:
        db_session.add(rel)
    await db_session.commit()

    # Verify soft_relation record
    result = await db_session.execute(text(
        "SELECT relation_type, is_hard, lifecycle_state, confidence, "
        "model_and_version, inference_source, supporting_evidence_ids "
        "FROM soft_relation WHERE knowledge_scope_id = :sid"
    ), {"sid": scope.knowledge_scope_id})
    rows = result.fetchall()
    assert len(rows) > 0

    for row in rows:
        rt, is_hard, state, conf, mv, ins, ev = row
        assert rt == "inferred"
        assert is_hard is False
        assert state in ("inferred", "active")
        assert float(conf) >= 0.6 if state == "active" else True
        assert mv == "local-llm-v1"
        assert ins == "llm-offline"
        assert ev is not None


@pytest.mark.asyncio
async def test_active_soft_relation_low_weight(db_session, soft_scope):
    """Active soft relations MUST enter retrieval as low-weight (0.3) supplement."""
    scope = soft_scope["scope"]
    inference = SoftRelationInference()

    relations = inference.infer(
        chunks=[{"chunk_id": soft_scope["src"]}, {"chunk_id": soft_scope["tgt"]}],
        scope=scope,
        llm=lambda chunks: _mock_llm(soft_scope["src"], soft_scope["tgt"]),
        model_and_version="local-llm-v1",
        inference_source="llm-offline",
        direction="out",
        version=1,
    )

    # Active relation should have confidence >= 0.6
    for rel in relations:
        if rel.lifecycle_state == "active":
            assert float(rel.confidence) >= 0.6
            assert rel.is_hard is False


@pytest.mark.asyncio
async def test_low_confidence_not_active(db_session, soft_scope):
    """Low confidence (< 0.6) MUST NOT enter active state (FR-005)."""
    scope = soft_scope["scope"]
    inference = SoftRelationInference()

    relations = inference.infer(
        chunks=[{"chunk_id": soft_scope["src"]}, {"chunk_id": soft_scope["tgt"]}],
        scope=scope,
        llm=lambda chunks: [(soft_scope["src"], soft_scope["tgt"], 0.3, [999])],
        model_and_version="local-llm-v1",
        inference_source="llm-offline",
        direction="out",
        version=1,
    )
    for rel in relations:
        assert rel.lifecycle_state != "active", "Low confidence must not be active"

_JAVA_CALCULATOR_SOURCE = "package com.example;\n\npublic class Calculator {\n    public int compute(int x) {\n        return square(add(x, 1));\n    }\n    public int add(int a, int b) {\n        return a + b;\n    }\n    public int square(int x) {\n        return x * x;\n    }\n}\n"


class TestIngestPipelineSoftWiring:
    """T042: ingestion MUST run offline soft-relation inference when an LLM is
    configured, persisting the five mandatory metadata (FR-003)."""

    @pytest.mark.asyncio
    async def test_ingest_with_llm_writes_soft_relations(self, db_session):
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
            db_session, scope_id, source_id, "Calculator.java",
            _JAVA_CALCULATOR_SOURCE, "java",
        )
        await db_session.commit()

        def fake_llm(chunks):
            ids = [c["chunk_id"] for c in chunks]
            if len(ids) < 2:
                return []
            return [(ids[0], ids[1], 0.85, [ids[0]])]

        svc = IngestionService(
            db_session, FakeEmbeddingProvider(), MockQdrantStore(),
            soft_relation_llm=fake_llm,
        )
        await svc.ingest(source_id)

        rows = (await db_session.execute(text(
            "SELECT relation_type, is_hard, lifecycle_state, confidence, "
            "model_and_version, inference_source, supporting_evidence_ids, "
            "knowledge_scope_id, project_id, index_version "
            "FROM soft_relation WHERE knowledge_scope_id = :k"
        ), {"k": scope_id})).fetchall()
        assert rows, "ingest() MUST write soft_relation rows when LLM configured (T042)"
        for rt, is_hard, state, conf, mv, ins, ev, ksid, pid, iv in rows:
            assert rt == "inferred"
            assert is_hard is False
            assert state in ("inferred", "active")
            assert mv
            assert ins
            assert ev is not None
            assert ksid == scope_id
            assert pid == project_id
            assert iv == 1

    @pytest.mark.asyncio
    async def test_ingest_without_llm_skips_soft_relations(self, db_session):
        """No configured LLM -> no soft rows and ingestion still succeeds."""
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
            db_session, scope_id, source_id, "Calculator.java",
            _JAVA_CALCULATOR_SOURCE, "java",
        )
        await db_session.commit()

        svc = IngestionService(db_session, FakeEmbeddingProvider(), MockQdrantStore())
        await svc.ingest(source_id)

        count = (await db_session.execute(text(
            "SELECT count(*) FROM soft_relation WHERE knowledge_scope_id = :k"
        ), {"k": scope_id})).scalar()
        assert count == 0

