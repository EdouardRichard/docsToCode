"""Unit tests for EvidenceService.annotate_evidence (T014).

Verifies hard (GraphEdge) and soft (SoftRelation) relation annotation onto an
evidence dict produced by get_evidence(). The annotation is strictly ADDITIVE:
a `relation` field is attached so that hard-relation evidence is marked
verifiable (is_hard=true) and soft-relation evidence is marked inferred
(is_hard=false), the two are distinguishable (FR-004, SC-009), existing MCP
contract fields are never changed or removed (FR-011, Constitution VII), and a
no-relation call returns the dict unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

# Import graph models through the rag_mcp.models re-export so the models
# package (which defines Base before re-exporting the graph models) loads in
# the correct order, avoiding the graph.models <-> models circular import.
from rag_mcp.models import GraphEdge, SoftRelation
from rag_mcp.services.evidence_service import EvidenceService


def _base_evidence() -> dict:
    """Canonical get_evidence() success output (mcp-get-evidence.schema.json)."""
    return {
        "evidence_id": "42",
        "full_content": "public Optional<User> findById(Long id) { ... }",
        "source_version": 3,
        "source_position": "UserService.java#L119",
        "knowledge_scope_id": "7",
        "knowledge_scope_type": "project",
        "status": "available",
        "parent_context": "class UserService { ... }",
    }


def _make_hard_edge() -> GraphEdge:
    """A deterministic (AST-extracted) hard relation edge."""
    return GraphEdge(
        edge_id=1001,
        knowledge_scope_id=7,
        project_id=1,
        index_version=3,
        source_chunk_id=42,
        target_chunk_id=88,
        relation_type="calls",
        direction="out",
        is_hard=True,
        version=1,
        parse_evidence={
            "kind": "ast",
            "file": "UserService.java",
            "line": 119,
            "callee": "repository.findById",
        },
    )


def _make_soft_relation() -> SoftRelation:
    """An LLM-inferred soft relation with the five mandatory metadata fields."""
    return SoftRelation(
        edge_id=2002,
        knowledge_scope_id=7,
        project_id=1,
        index_version=3,
        source_chunk_id=42,
        target_chunk_id=91,
        relation_type="inferred",
        direction="out",
        is_hard=False,
        version=1,
        inference_source="llm",
        confidence=0.85,
        model_and_version="gpt-4o-mini-2024-07-18",
        generated_at=datetime(2024, 7, 18, 12, 0, 0, tzinfo=timezone.utc),
        supporting_evidence_ids=[42, 55],
        lifecycle_state="active",
    )


def _service() -> EvidenceService:
    """annotate_evidence is pure and needs no DB session."""
    return EvidenceService(session=None)


# --------------------------------------------------------------------------- #
# Red phase: every assertion below should fail until annotate_evidence exists.
# --------------------------------------------------------------------------- #


def test_annotate_hard_relation_adds_relation_field():
    result = _service().annotate_evidence(_base_evidence(), relation_edge=_make_hard_edge())

    assert "relation" in result
    relation = result["relation"]
    assert relation["type"] == "hard"
    assert relation["relation_type"] == "calls"
    assert relation["edge_id"] == "1001"
    assert relation["is_hard"] is True
    assert relation["parse_evidence"] == {
        "kind": "ast",
        "file": "UserService.java",
        "line": 119,
        "callee": "repository.findById",
    }


def test_annotate_soft_relation_adds_relation_field():
    result = _service().annotate_evidence(_base_evidence(), soft_relation=_make_soft_relation())

    assert "relation" in result
    relation = result["relation"]
    assert relation["type"] == "soft"
    assert relation["relation_type"] == "inferred"
    assert relation["edge_id"] == "2002"
    assert relation["is_hard"] is False
    assert relation["confidence"] == 0.85
    assert relation["model_and_version"] == "gpt-4o-mini-2024-07-18"
    assert relation["lifecycle_state"] == "active"


def test_hard_and_soft_are_distinguishable():
    hard = _service().annotate_evidence(_base_evidence(), relation_edge=_make_hard_edge())["relation"]
    soft = _service().annotate_evidence(_base_evidence(), soft_relation=_make_soft_relation())["relation"]

    assert hard["type"] != soft["type"]
    assert hard["type"] == "hard"
    assert soft["type"] == "soft"
    assert hard["is_hard"] is True
    assert soft["is_hard"] is False


def test_annotation_preserves_existing_mcp_fields():
    original = _base_evidence()
    hard = _service().annotate_evidence(dict(original), relation_edge=_make_hard_edge())
    soft = _service().annotate_evidence(dict(original), soft_relation=_make_soft_relation())

    expected_keys = set(original.keys())
    for result in (hard, soft):
        # No existing field removed or altered (FR-011, Constitution VII).
        for key in expected_keys:
            assert key in result, f"existing field '{key}' removed by annotation"
            assert result[key] == original[key], f"existing field '{key}' changed by annotation"
        # Only the additive 'relation' field is appended.
        assert set(result.keys()) == expected_keys | {"relation"}, (
            "annotation must be strictly additive (only a 'relation' key added)"
        )


def test_annotate_no_relation_returns_dict_unchanged():
    evidence = _base_evidence()
    result = _service().annotate_evidence(evidence)

    assert result == evidence
    assert "relation" not in result
