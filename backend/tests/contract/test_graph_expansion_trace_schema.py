"""Contract test for graph-expansion-trace.schema.json (T016).

Validates trace samples with graph_candidates/edge_path/fused_candidates,
partial→failed_paths requirement (DM-1 bridge).

Schema: specs/003-structured-asset-expansion/contracts/graph-expansion-trace.schema.json
"""

from __future__ import annotations

import jsonschema
import pytest

from tests.contract._graph_schema_helper import (
    common_schema,
    graph_relations_schema,
    graph_trace_schema,
    inline_refs,
)


@pytest.fixture
def schema() -> dict:
    raw = graph_trace_schema()
    return inline_refs(raw, common_schema(), graph_relations_schema(), raw)


def _complete_trace():
    return {
        "request_id": "req-001",
        "retrieval_mode": "hybrid",
        "knowledge_scope_ids": ["100"],
        "completion_status": "complete",
        "guardrails": {
            "hop_default": 2,
            "hop_max": 3,
            "candidate_budget": 10,
            "graph_sub_timeout_ms": 3000,
            "total_timeout_ms": 30000,
            "direction_default": "bidirectional",
        },
        "subpath_timings": {
            "dense_recall_ms": 10.0, "sparse_recall_ms": 10.0,
            "graph_recall_ms": 5.0, "fusion_ms": 1.0,
            "rerank_ms": 2.0, "total_ms": 28.0,
        },
        "graph_candidates": [
            {
                "chunk_id": "300", "knowledge_scope_id": "100",
                "start_chunk_id": "301",
                "edge_path": [
                    {"hop": 1, "edge_id": "500", "relation_type": "calls",
                     "direction": "out", "is_hard": True}
                ],
                "hop_count": 1, "structure_weight": 1.0, "graph_rank": 1,
                "relation_is_hard": True, "evidence_id": "300",
            },
        ],
        "fused_candidates": [
            {
                "chunk_id": "300", "knowledge_scope_id": "100",
                "source_retrievers": ["dense", "graph"],
                "dense_score": 0.9, "graph_rank": 1,
                "fused_score": 0.05, "final_rank": 1,
            },
        ],
        "evidence_ref_ids": ["300"],
    }


class TestValidTrace:
    def test_complete_trace(self, schema):
        jsonschema.validate(_complete_trace(), schema)

    def test_trace_without_graph_candidates(self, schema):
        t = _complete_trace()
        del t["graph_candidates"]
        jsonschema.validate(t, schema)


class TestPartialFailedPaths:
    def test_partial_requires_failed_paths(self, schema):
        t = _complete_trace()
        t["completion_status"] = "partial"
        # Do NOT add failed_paths — partial status requires it
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(t, schema)

    def test_partial_with_failed_paths_valid(self, schema):
        t = _complete_trace()
        t["completion_status"] = "partial"
        t["failed_paths"] = ["graph_recall_timeout"]
        jsonschema.validate(t, schema)


class TestNoEvidence:
    def test_no_evidence_valid(self, schema):
        t = _complete_trace()
        t["completion_status"] = "no_evidence"
        t["fused_candidates"] = []
        t["evidence_ref_ids"] = []
        del t["graph_candidates"]
        jsonschema.validate(t, schema)
