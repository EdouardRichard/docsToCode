"""Contract test for hybrid-retrieval-trace schema (T018)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SPECS_DIR = _REPO_ROOT / "specs" / "002-hybrid-retrieval-precision" / "contracts"


def _load_schema(name: str) -> dict:
    with open(_SPECS_DIR / name, "r", encoding="utf-8") as f:
        return json.load(f)


def _inline_refs(trace_schema: dict, common_schema: dict) -> dict:
    """Inline common.schema.json definitions into trace schema for validation."""
    schema = json.loads(json.dumps(trace_schema))
    schema["definitions"] = common_schema.get("definitions", {})
    schema_str = json.dumps(schema)
    schema_str = schema_str.replace(
        "./common.schema.json#/definitions/", "#/definitions/"
    )
    return json.loads(schema_str)


@pytest.fixture
def common_schema() -> dict:
    return _load_schema("common.schema.json")


@pytest.fixture
def trace_schema(common_schema) -> dict:
    raw = _load_schema("hybrid-retrieval-trace.schema.json")
    return _inline_refs(raw, common_schema)


def _validate(instance, schema):
    jsonschema.validate(instance, schema)


class TestValidTraces:
    def test_complete_hybrid_trace(self, trace_schema):
        trace = {
            "request_id": "req-123", "retrieval_mode": "hybrid",
            "knowledge_scope_ids": ["100"], "completion_status": "complete",
            "subpath_timings": {"dense_recall_ms": 12.3, "sparse_recall_ms": 8.1,
                "fusion_ms": 0.4, "rerank_ms": 245.7, "total_ms": 266.5},
            "fused_candidates": [{"chunk_id": "351194693498830853",
                "knowledge_scope_id": "100", "source_retrievers": ["dense", "sparse"],
                "dense_score": 0.6133, "sparse_score": 12.5, "dense_rank": 2,
                "sparse_rank": 1, "fused_score": 0.0325, "rerank_score": 0.85,
                "final_rank": 1}],
            "rerank_budget": 20, "evidence_ref_ids": ["351194693498830853"],
        }
        _validate(trace, trace_schema)

    def test_partial_trace_with_failed_paths(self, trace_schema):
        trace = {
            "request_id": "req-456", "retrieval_mode": "hybrid",
            "knowledge_scope_ids": ["200"], "completion_status": "partial",
            "subpath_timings": {"dense_recall_ms": 12.0, "sparse_recall_ms": 0.0,
                "fusion_ms": 0.1, "rerank_ms": 0.0, "total_ms": 12.1},
            "failed_paths": ["sparse_recall_failed"],
            "fused_candidates": [{"chunk_id": "300", "knowledge_scope_id": "200",
                "source_retrievers": ["dense"], "fused_score": 0.0164, "final_rank": 1}],
            "evidence_ref_ids": ["300"],
        }
        _validate(trace, trace_schema)


class TestConditionalRequirements:
    def test_hybrid_requires_subpath_timings(self, trace_schema):
        trace = {"request_id": "r1", "retrieval_mode": "hybrid",
            "knowledge_scope_ids": ["100"], "completion_status": "complete",
            "fused_candidates": [], "evidence_ref_ids": []}
        with pytest.raises(jsonschema.ValidationError):
            _validate(trace, trace_schema)

    def test_partial_requires_failed_paths(self, trace_schema):
        trace = {"request_id": "r2", "retrieval_mode": "hybrid",
            "knowledge_scope_ids": ["100"], "completion_status": "partial",
            "subpath_timings": {"dense_recall_ms": 1.0, "sparse_recall_ms": 0.0,
                "fusion_ms": 0.0, "rerank_ms": 0.0, "total_ms": 1.0},
            "fused_candidates": [], "evidence_ref_ids": []}
        with pytest.raises(jsonschema.ValidationError):
            _validate(trace, trace_schema)

    def test_complete_no_failed_paths_needed(self, trace_schema):
        trace = {"request_id": "r3", "retrieval_mode": "hybrid",
            "knowledge_scope_ids": ["100"], "completion_status": "complete",
            "subpath_timings": {"dense_recall_ms": 1.0, "sparse_recall_ms": 1.0,
                "fusion_ms": 0.1, "rerank_ms": 0.0, "total_ms": 2.1},
            "fused_candidates": [], "evidence_ref_ids": []}
        _validate(trace, trace_schema)


class TestRequiredFields:
    @pytest.mark.parametrize("missing", [
        "request_id", "retrieval_mode", "knowledge_scope_ids",
        "completion_status", "fused_candidates", "evidence_ref_ids",
    ])
    def test_missing_required_rejected(self, trace_schema, missing):
        trace = {"request_id": "r1", "retrieval_mode": "hybrid",
            "knowledge_scope_ids": ["100"], "completion_status": "complete",
            "subpath_timings": {"dense_recall_ms": 1.0, "sparse_recall_ms": 1.0,
                "fusion_ms": 0.1, "rerank_ms": 0.0, "total_ms": 2.1},
            "fused_candidates": [], "evidence_ref_ids": []}
        del trace[missing]
        with pytest.raises(jsonschema.ValidationError):
            _validate(trace, trace_schema)


class TestFusedCandidateStructure:
    def test_minimal_candidate(self, trace_schema):
        trace = {"request_id": "r1", "retrieval_mode": "hybrid",
            "knowledge_scope_ids": ["100"], "completion_status": "complete",
            "subpath_timings": {"dense_recall_ms": 1.0, "sparse_recall_ms": 1.0,
                "fusion_ms": 0.1, "rerank_ms": 0.0, "total_ms": 2.1},
            "fused_candidates": [{"chunk_id": "300", "knowledge_scope_id": "100",
                "source_retrievers": ["dense"], "fused_score": 0.016, "final_rank": 1}],
            "evidence_ref_ids": ["300"]}
        _validate(trace, trace_schema)

    def test_no_extra_fields(self, trace_schema):
        trace = {"request_id": "r1", "retrieval_mode": "hybrid",
            "knowledge_scope_ids": ["100"], "completion_status": "complete",
            "subpath_timings": {"dense_recall_ms": 1.0, "sparse_recall_ms": 1.0,
                "fusion_ms": 0.1, "rerank_ms": 0.0, "total_ms": 2.1},
            "fused_candidates": [{"chunk_id": "300", "knowledge_scope_id": "100",
                "source_retrievers": ["dense"], "fused_score": 0.016, "final_rank": 1,
                "extra": "no"}],
            "evidence_ref_ids": ["300"]}
        with pytest.raises(jsonschema.ValidationError):
            _validate(trace, trace_schema)
