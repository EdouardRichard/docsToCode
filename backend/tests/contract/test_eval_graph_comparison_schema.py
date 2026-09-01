"""Contract test for eval-graph-comparison-report.schema.json (T018).

Validates report samples with three_gate_pass, per_query graph fields,
and reproducibility tolerance=0.01 (DM-2, FR-022/FR-023/FR-024).

Schema: specs/003-structured-asset-expansion/contracts/eval-graph-comparison-report.schema.json
"""

from __future__ import annotations

import jsonschema
import pytest

from tests.contract._graph_schema_helper import (
    common_schema,
    eval_report_schema,
    graph_relations_schema,
    graph_trace_schema,
    inline_refs,
)


@pytest.fixture
def schema() -> dict:
    raw = eval_report_schema()
    return inline_refs(raw, common_schema(), graph_relations_schema(), graph_trace_schema(), raw)


def _metric(mean=1.0, mn=1.0, mx=1.0):
    return {"mean": mean, "min": mn, "max": mx}


def _latency(p50=100, p95=200, mean=150):
    return {"p50": p50, "p95": p95, "mean": mean}


def _config():
    return {
        "embedding_model": "BAAI/bge-m3",
        "reranker_model": "BAAI/bge-reranker-v2-m3",
        "hybrid_collection": "chunks_hybrid_bge-m3_v1",
        "fusion_algorithm": "rrf",
        "dataset_path": "eval/eval_dataset.json",
        "num_queries": 24,
        "structural_subset_size": 6,
        "graph_candidate_budget": 10,
        "graph_total_timeout_ms": 30000,
    }


def _complete_report():
    return {
        "report_type": "graph_enhanced_comparison",
        "generated_at": "2026-09-02T12:00:00Z",
        "config": _config(),
        "baseline_metrics": {
            "recall_at_k": _metric(), "mrr": _metric(0.9, 0.5, 1.0),
            "ndcg_at_k": _metric(0.93, 0.6, 1.0), "latency_ms": _latency(),
        },
        "graph_metrics": {
            "recall_at_k": _metric(), "mrr": _metric(0.95, 0.5, 1.0),
            "ndcg_at_k": _metric(0.96, 0.6, 1.0), "latency_ms": _latency(160, 210),
        },
        "structural_subset_metrics": {
            "baseline_mrr_mean": 0.7, "graph_mrr_mean": 0.75,
            "mrr_relative_improvement": 7.14,
            "baseline_ndcg_mean": 0.7, "graph_ndcg_mean": 0.75,
            "ndcg_relative_improvement": 7.14,
            "recall_at_k_non_decreasing": True,
        },
        "deltas": {"mrr_mean_delta": 0.05, "ndcg_mean_delta": 0.03, "recall_mean_delta": 0.0},
        "hard_constraints": {
            "cross_project_leakage_events": 0, "schema_validity_rate": 1.0,
            "source_locatability_rate": 1.0, "all_passed": True,
        },
        "three_gate_pass": {
            "sc001_structural_improvement": True,
            "sc002_001_noninferior": True,
            "sc013_002_nonstructural_noninferior": True,
            "hard_constraints_passed": True,
            "all_passed": True,
        },
        "per_query_comparison": [
            {
                "query_index": 0, "query": "who calls validateToken",
                "is_structural_benefit": True, "expected_evidence_ids": ["300"],
                "baseline_rank": 2, "graph_rank": 1, "rank_improved": True,
                "graph_edge_path_summary": [
                    {"hop": 1, "edge_id": "500", "relation_type": "calls",
                     "direction": "out", "is_hard": True}
                ],
            },
        ],
        "reproducibility": {
            "non_latency_reproducible": True, "tolerance": 0.01,
            "checks": [
                {"metric": "recall_at_k.mean", "run_1": 1.0, "run_2": 1.0,
                 "relative_delta": 0.0, "tolerance": 0.01, "passed": True},
            ],
        },
        "enters_default_path": True,
    }


class TestValidReport:
    def test_complete_report(self, schema):
        jsonschema.validate(_complete_report(), schema)

    def test_report_not_entering_default_path(self, schema):
        r = _complete_report()
        r["three_gate_pass"]["all_passed"] = False
        r["enters_default_path"] = False
        jsonschema.validate(r, schema)


class TestRequiredFields:
    @pytest.mark.parametrize("missing", [
        "report_type", "generated_at", "config", "baseline_metrics",
        "graph_metrics", "structural_subset_metrics", "deltas",
        "hard_constraints", "three_gate_pass", "per_query_comparison",
        "enters_default_path",
    ])
    def test_missing_required_rejected(self, schema, missing):
        r = _complete_report()
        del r[missing]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(r, schema)


class TestReproducibilityTolerance:
    def test_tolerance_is_0_01(self, schema):
        r = _complete_report()
        jsonschema.validate(r, schema)
        # The schema const-enforces tolerance=0.01
        r["reproducibility"]["tolerance"] = 0.02
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(r, schema)
