"""Contract test for the 004 search-output graph annotation extension (T046).

Validates mcp-search-output.graph-annotation.schema.json: additive relation
annotation on evidence items, hard/soft branches, and that unannotated 001
responses remain valid under the extension (backward compatible).
"""

from __future__ import annotations

import copy

import jsonschema
import pytest

from tests.contract._graph_schema_helper import load_schema


@pytest.fixture
def schema():
    return load_schema("mcp-search-output.graph-annotation.schema.json")


def _base_response():
    return {
        "completion_status": "complete",
        "evidence": [
            {
                "evidence_id": "352016027946582016",
                "content_excerpt": "public void validateToken() {}",
                "source_version": 1,
                "source_position": "com.example.UserService#validateToken",
                "knowledge_scope_id": "351193748123680768",
                "knowledge_scope_type": "project",
                "relevance_score": 0.91,
            }
        ],
        "request_id": "req-1",
    }


def _hard_annotation():
    return {
        "type": "hard",
        "relation_type": "called_by",
        "is_hard": True,
        "edge_id": "353000000000000001",
        "parse_evidence": {
            "source_format": "java",
            "locator": "method:processRequest:line:5",
            "extractor": "java_call_graph",
        },
    }


def _soft_annotation():
    return {
        "type": "soft",
        "relation_type": "inferred",
        "is_hard": False,
        "edge_id": "353000000000000002",
        "confidence": 0.85,
        "model_and_version": "offline-llm-v1",
        "lifecycle_state": "active",
    }


class TestGraphAnnotationSchema:
    def test_unannotated_response_stays_valid(self, schema):
        """Backward compat: a plain 001 response validates unchanged."""
        jsonschema.validate(_base_response(), schema)

    def test_hard_annotated_evidence_valid(self, schema):
        resp = _base_response()
        resp["evidence"][0]["relation"] = _hard_annotation()
        jsonschema.validate(resp, schema)

    def test_soft_annotated_evidence_valid(self, schema):
        resp = _base_response()
        resp["evidence"][0]["relation"] = _soft_annotation()
        jsonschema.validate(resp, schema)

    def test_hard_annotation_requires_parse_evidence(self, schema):
        resp = _base_response()
        rel = _hard_annotation()
        del rel["parse_evidence"]
        resp["evidence"][0]["relation"] = rel
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(resp, schema)

    def test_soft_annotation_requires_confidence_and_provenance(self, schema):
        resp = _base_response()
        rel = _soft_annotation()
        del rel["confidence"]
        resp["evidence"][0]["relation"] = rel
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(resp, schema)

    def test_soft_must_not_claim_hard_type(self, schema):
        resp = _base_response()
        rel = _soft_annotation()
        rel["is_hard"] = True  # soft masquerading as hard (Constitution III)
        resp["evidence"][0]["relation"] = rel
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(resp, schema)

    def test_unknown_relation_field_rejected(self, schema):
        resp = _base_response()
        rel = copy.deepcopy(_hard_annotation())
        rel["unexpected"] = "x"
        resp["evidence"][0]["relation"] = rel
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(resp, schema)
