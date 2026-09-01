"""Story integration test for US4: soft relation distinguishable (T030).

Validates AS4.1-4.3: soft relations carry 5 metadata and are locatable;
hard/soft conflict returned distinguishably (soft never silently overrides
hard); low-confidence soft relations excluded from default path (FR-005).

This test MUST FAIL before the annotation + inference are complete (TDD).
"""

from __future__ import annotations

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
