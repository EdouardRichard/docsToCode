"""Contract test for graph-relations.schema.json (T015).

Validates hard/soft relation allOf constraints, four-state superseded_by
requirement, and active confidence gating (data-model §2/§3, FR-002..FR-005).

Schema: specs/003-structured-asset-expansion/contracts/graph-relations.schema.json
"""

from __future__ import annotations

import jsonschema
import pytest

from tests.contract._graph_schema_helper import (
    common_schema,
    graph_relations_schema,
    inline_refs,
)


@pytest.fixture
def schema() -> dict:
    raw = graph_relations_schema()
    return inline_refs(raw, common_schema(), raw)


class TestValidHardEdge:
    def test_valid_hard_calls_edge(self, schema):
        edge = {
            "edge_id": "123456",
            "knowledge_scope_id": "100",
            "project_id": "200",
            "index_version": 1,
            "source_chunk_id": "300",
            "target_chunk_id": "301",
            "relation_type": "calls",
            "direction": "out",
            "is_hard": True,
            "version": 1,
            "parse_evidence": {
                "source_format": "java",
                "locator": "com.example.Service#method",
                "extractor": "java_call_graph",
            },
        }
        jsonschema.validate(edge, schema)

    def test_valid_hard_fk_edge(self, schema):
        edge = {
            "edge_id": "123457",
            "knowledge_scope_id": "100",
            "project_id": "200",
            "index_version": 1,
            "source_chunk_id": "302",
            "target_chunk_id": "303",
            "relation_type": "fk_references",
            "direction": "out",
            "is_hard": True,
            "version": 2,
            "parse_evidence": {
                "source_format": "ddl",
                "locator": "table:orders.fk:user_id",
                "extractor": "ddl_fk",
            },
        }
        jsonschema.validate(edge, schema)


class TestValidSoftRelation:
    def test_valid_active_soft_relation(self, schema):
        rel = {
            "edge_id": "123458",
            "knowledge_scope_id": "100",
            "project_id": "200",
            "index_version": 1,
            "source_chunk_id": "300",
            "target_chunk_id": "305",
            "relation_type": "inferred",
            "direction": "out",
            "is_hard": False,
            "version": 1,
            "inference_source": "llm-offline",
            "confidence": 0.85,
            "model_and_version": "local-llm-v1",
            "generated_at": "2026-09-01T12:00:00Z",
            "supporting_evidence_ids": ["400"],
            "lifecycle_state": "active",
        }
        jsonschema.validate(rel, schema)

    def test_valid_superseded_soft_relation(self, schema):
        rel = {
            "edge_id": "123459",
            "knowledge_scope_id": "100",
            "project_id": "200",
            "index_version": 1,
            "source_chunk_id": "300",
            "target_chunk_id": "305",
            "relation_type": "inferred",
            "direction": "out",
            "is_hard": False,
            "version": 1,
            "inference_source": "llm-offline",
            "confidence": 0.7,
            "model_and_version": "local-llm-v1",
            "generated_at": "2026-09-01T12:00:00Z",
            "supporting_evidence_ids": ["400"],
            "lifecycle_state": "superseded",
            "superseded_by": "123460",
        }
        jsonschema.validate(rel, schema)


class TestInvalidHardEdge:
    def test_hard_edge_with_inferred_type_rejected(self, schema):
        edge = {
            "edge_id": "1", "knowledge_scope_id": "100", "project_id": "200",
            "index_version": 1, "source_chunk_id": "300", "target_chunk_id": "301",
            "relation_type": "inferred", "direction": "out",
            "is_hard": True, "version": 1,
            "parse_evidence": {"source_format": "java", "locator": "x", "extractor": "e"},
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(edge, schema)

    def test_hard_edge_without_parse_evidence_rejected(self, schema):
        edge = {
            "edge_id": "1", "knowledge_scope_id": "100", "project_id": "200",
            "index_version": 1, "source_chunk_id": "300", "target_chunk_id": "301",
            "relation_type": "calls", "direction": "out",
            "is_hard": True, "version": 1,
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(edge, schema)


class TestInvalidSoftRelation:
    def test_soft_with_parse_evidence_rejected(self, schema):
        rel = {
            "edge_id": "1", "knowledge_scope_id": "100", "project_id": "200",
            "index_version": 1, "source_chunk_id": "300", "target_chunk_id": "301",
            "relation_type": "inferred", "direction": "out",
            "is_hard": False, "version": 1,
            "inference_source": "s", "confidence": 0.8,
            "model_and_version": "m", "generated_at": "2026-09-01T12:00:00Z",
            "supporting_evidence_ids": ["400"], "lifecycle_state": "active",
            "parse_evidence": {"source_format": "java", "locator": "x", "extractor": "e"},
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(rel, schema)

    def test_active_low_confidence_rejected(self, schema):
        rel = {
            "edge_id": "1", "knowledge_scope_id": "100", "project_id": "200",
            "index_version": 1, "source_chunk_id": "300", "target_chunk_id": "301",
            "relation_type": "inferred", "direction": "out",
            "is_hard": False, "version": 1,
            "inference_source": "s", "confidence": 0.3,
            "model_and_version": "m", "generated_at": "2026-09-01T12:00:00Z",
            "supporting_evidence_ids": ["400"], "lifecycle_state": "active",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(rel, schema)

    def test_superseded_without_superseded_by_rejected(self, schema):
        rel = {
            "edge_id": "1", "knowledge_scope_id": "100", "project_id": "200",
            "index_version": 1, "source_chunk_id": "300", "target_chunk_id": "301",
            "relation_type": "inferred", "direction": "out",
            "is_hard": False, "version": 1,
            "inference_source": "s", "confidence": 0.8,
            "model_and_version": "m", "generated_at": "2026-09-01T12:00:00Z",
            "supporting_evidence_ids": ["400"], "lifecycle_state": "superseded",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(rel, schema)
