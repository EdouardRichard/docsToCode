"""Unit test for runtime graph-expansion-trace recorder (T041).

Validates that the trace recorder produces a dict conforming to
graph-expansion-trace.schema.json, with evidence_id backfill (DM-1)
and partial->failed_paths requirement (FR-026).

This test MUST FAIL before the recorder is implemented (TDD).
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from rag_mcp.graph.trace_recorder import GraphTraceRecorder

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONTRACTS = _REPO_ROOT / "specs" / "003-structured-asset-expansion" / "contracts"


def _load_schema(name):
    with open(_CONTRACTS / name, "r", encoding="utf-8") as f:
        return json.load(f)


def _inline_refs(target, *ref_schemas):
    import copy
    result = copy.deepcopy(target)
    result.setdefault("definitions", {})
    for ref_schema in ref_schemas:
        for key, val in ref_schema.get("definitions", {}).items():
            result["definitions"].setdefault(key, val)
    schema_str = json.dumps(result)
    for prefix in [
        "https://ai-engineering-rag-mcp.local/schemas/003/common.schema.json#/definitions/",
        "https://ai-engineering-rag-mcp.local/schemas/004/graph-relations.schema.json#/definitions/",
        "https://ai-engineering-rag-mcp.local/schemas/004/graph-expansion-trace.schema.json#/definitions/",
    ]:
        schema_str = schema_str.replace(prefix, "#/definitions/")
    return json.loads(schema_str)


@pytest.fixture
def trace_schema():
    raw = _load_schema("graph-expansion-trace.schema.json")
    common = _load_schema("common.schema.json")
    graph_rel = _load_schema("graph-relations.schema.json")
    return _inline_refs(raw, common, graph_rel, raw)


def _make_guardrails():
    return {
        "hop_default": 2,
        "hop_max": 3,
        "candidate_budget": 10,
        "graph_sub_timeout_ms": 3000,
        "total_timeout_ms": 30000,
        "direction_default": "bidirectional",
    }


class TestTraceRecorder:
    def test_complete_trace_validates(self, trace_schema):
        """A complete trace MUST validate against the schema."""
        rec = GraphTraceRecorder(
            request_id="req-001",
            knowledge_scope_ids=["100"],
            guardrails=_make_guardrails(),
        )
        rec.record_timings({
            "dense_recall_ms": 10.0, "sparse_recall_ms": 10.0,
            "graph_recall_ms": 5.0, "fusion_ms": 1.0,
            "rerank_ms": 2.0, "total_ms": 28.0,
        })
        rec.record_graph_candidates([{
            "chunk_id": "300", "knowledge_scope_id": "100",
            "start_chunk_id": "301",
            "edge_path": [{"hop": 1, "edge_id": "500",
                           "relation_type": "calls", "direction": "out",
                           "is_hard": True}],
            "hop_count": 1, "structure_weight": 1.0, "graph_rank": 1,
            "relation_is_hard": True,
        }])
        rec.record_fused_candidates([{
            "chunk_id": "300", "knowledge_scope_id": "100",
            "source_retrievers": ["dense", "graph"],
            "dense_score": 0.9, "graph_rank": 1,
            "fused_score": 0.05, "final_rank": 1,
        }])
        rec.set_completion_status("complete")
        rec.set_evidence_ref_ids(["300"])

        trace = rec.to_trace_dict()
        jsonschema.validate(trace, trace_schema)

    def test_partial_requires_failed_paths(self, trace_schema):
        """Partial status MUST have non-empty failed_paths."""
        rec = GraphTraceRecorder(
            request_id="req-002",
            knowledge_scope_ids=["100"],
            guardrails=_make_guardrails(),
        )
        rec.record_timings({
            "dense_recall_ms": 10.0, "sparse_recall_ms": 10.0,
            "graph_recall_ms": 0.0, "fusion_ms": 1.0,
            "rerank_ms": 2.0, "total_ms": 13.0,
        })
        rec.record_failed_path("graph_recall_timeout")
        rec.set_completion_status("partial")
        rec.set_evidence_ref_ids(["300"])

        trace = rec.to_trace_dict()
        assert trace["completion_status"] == "partial"
        assert "failed_paths" in trace
        assert len(trace["failed_paths"]) > 0
        jsonschema.validate(trace, trace_schema)

    def test_evidence_id_backfill(self, trace_schema):
        """Surviving candidates MUST get evidence_id backfilled (DM-1)."""
        rec = GraphTraceRecorder(
            request_id="req-003",
            knowledge_scope_ids=["100"],
            guardrails=_make_guardrails(),
        )
        rec.record_timings({
            "dense_recall_ms": 5.0, "sparse_recall_ms": 5.0,
            "graph_recall_ms": 3.0, "fusion_ms": 1.0,
            "rerank_ms": 1.0, "total_ms": 15.0,
        })
        rec.record_graph_candidates([
            {"chunk_id": "300", "knowledge_scope_id": "100",
             "start_chunk_id": "301",
             "edge_path": [{"hop": 1, "edge_id": "500",
                            "relation_type": "calls", "direction": "out",
                            "is_hard": True}],
             "hop_count": 1, "structure_weight": 1.0, "graph_rank": 1,
             "relation_is_hard": True},
            {"chunk_id": "302", "knowledge_scope_id": "100",
             "start_chunk_id": "301",
             "edge_path": [{"hop": 1, "edge_id": "501",
                            "relation_type": "called_by", "direction": "in",
                            "is_hard": True}],
             "hop_count": 1, "structure_weight": 1.0, "graph_rank": 2,
             "relation_is_hard": True},
        ])
        rec.record_fused_candidates([])
        rec.set_completion_status("complete")
        rec.set_evidence_ref_ids(["300"])

        # Backfill: chunk 300 survived as evidence with evidence_id "300"
        rec.backfill_evidence_ids({"300": "300"})

        trace = rec.to_trace_dict()
        gc = trace["graph_candidates"]
        # chunk 300 should have evidence_id backfilled
        c300 = next(c for c in gc if c["chunk_id"] == "300")
        assert c300.get("evidence_id") == "300"
        # chunk 302 should NOT have evidence_id (didn't survive)
        c302 = next(c for c in gc if c["chunk_id"] == "302")
        assert "evidence_id" not in c302 or c302.get("evidence_id") is None
        jsonschema.validate(trace, trace_schema)

    def test_no_evidence_trace(self, trace_schema):
        """no_evidence status with empty results MUST validate."""
        rec = GraphTraceRecorder(
            request_id="req-004",
            knowledge_scope_ids=["100"],
            guardrails=_make_guardrails(),
        )
        rec.set_completion_status("no_evidence")
        rec.record_fused_candidates([])
        rec.set_evidence_ref_ids([])

        trace = rec.to_trace_dict()
        jsonschema.validate(trace, trace_schema)
