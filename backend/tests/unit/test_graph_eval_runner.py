"""Unit test for graph-enhanced eval comparison runner (T025).

Validates three_gate_pass computation, structural_subset_metrics,
reproducibility tolerance, and report schema compliance (FR-022/FR-023/FR-024,
SC-001/SC-002/SC-013, research sec 0).

This test MUST FAIL before the runner is implemented (TDD).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema
import pytest

# Ensure eval/ is importable
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))

from eval.graph_comparison_runner import GraphComparisonRunner
from tests.contract._graph_schema_helper import (
    common_schema,
    eval_report_schema,
    graph_relations_schema,
    graph_trace_schema,
    inline_refs,
)


@pytest.fixture
def report_schema():
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


class TestBuildReport:
    def test_valid_report_passes_schema(self, report_schema):
        runner = GraphComparisonRunner(_config())
        report = runner.build_report(
            baseline_metrics={
                "recall_at_k": _metric(), "mrr": _metric(0.9, 0.5, 1.0),
                "ndcg_at_k": _metric(0.93, 0.6, 1.0), "latency_ms": _latency(),
            },
            graph_metrics={
                "recall_at_k": _metric(), "mrr": _metric(0.95, 0.5, 1.0),
                "ndcg_at_k": _metric(0.96, 0.6, 1.0), "latency_ms": _latency(160, 210),
            },
            structural_metrics={
                "baseline_mrr_mean": 0.7, "graph_mrr_mean": 0.75,
                "baseline_ndcg_mean": 0.7, "graph_ndcg_mean": 0.75,
                "recall_non_decreasing": True,
            },
            sc001_improvement_pct=7.14,
            sc002_noninferior=True,
            sc013_noninferior=True,
            per_query=[],
            hard_constraints={
                "cross_project_leakage_events": 0,
                "schema_validity_rate": 1.0,
                "source_locatability_rate": 1.0,
            },
            reproducibility={
                "non_latency_reproducible": True,
                "tolerance": 0.01,
                "checks": [],
            },
        )
        jsonschema.validate(report, report_schema)
        assert report["report_type"] == "graph_enhanced_comparison"
        assert report["enters_default_path"] is True


class TestThreeGatePass:
    def test_all_pass_when_all_gates_and_hard_constraints_pass(self, report_schema):
        runner = GraphComparisonRunner(_config())
        report = runner.build_report(
            baseline_metrics={"recall_at_k": _metric(), "mrr": _metric(),
                               "ndcg_at_k": _metric(), "latency_ms": _latency()},
            graph_metrics={"recall_at_k": _metric(), "mrr": _metric(),
                           "ndcg_at_k": _metric(), "latency_ms": _latency()},
            structural_metrics={"baseline_mrr_mean": 0.7, "graph_mrr_mean": 0.75,
                                "baseline_ndcg_mean": 0.7, "graph_ndcg_mean": 0.75,
                                "recall_non_decreasing": True},
            sc001_improvement_pct=7.14,
            sc002_noninferior=True,
            sc013_noninferior=True,
            per_query=[],
            hard_constraints={"cross_project_leakage_events": 0,
                               "schema_validity_rate": 1.0,
                               "source_locatability_rate": 1.0},
            reproducibility={"non_latency_reproducible": True, "tolerance": 0.01, "checks": []},
        )
        assert report["three_gate_pass"]["sc001_structural_improvement"] is True
        assert report["three_gate_pass"]["all_passed"] is True
        assert report["enters_default_path"] is True

    def test_fails_when_sc001_improvement_below_3pct(self, report_schema):
        runner = GraphComparisonRunner(_config())
        report = runner.build_report(
            baseline_metrics={"recall_at_k": _metric(), "mrr": _metric(),
                               "ndcg_at_k": _metric(), "latency_ms": _latency()},
            graph_metrics={"recall_at_k": _metric(), "mrr": _metric(),
                           "ndcg_at_k": _metric(), "latency_ms": _latency()},
            structural_metrics={"baseline_mrr_mean": 0.7, "graph_mrr_mean": 0.71,
                                "baseline_ndcg_mean": 0.7, "graph_ndcg_mean": 0.71,
                                "recall_non_decreasing": True},
            sc001_improvement_pct=1.43,
            sc002_noninferior=True,
            sc013_noninferior=True,
            per_query=[],
            hard_constraints={"cross_project_leakage_events": 0,
                               "schema_validity_rate": 1.0,
                               "source_locatability_rate": 1.0},
            reproducibility={"non_latency_reproducible": True, "tolerance": 0.01, "checks": []},
        )
        assert report["three_gate_pass"]["sc001_structural_improvement"] is False
        assert report["three_gate_pass"]["all_passed"] is False
        assert report["enters_default_path"] is False

    def test_fails_when_hard_constraints_fail(self, report_schema):
        runner = GraphComparisonRunner(_config())
        report = runner.build_report(
            baseline_metrics={"recall_at_k": _metric(), "mrr": _metric(),
                               "ndcg_at_k": _metric(), "latency_ms": _latency()},
            graph_metrics={"recall_at_k": _metric(), "mrr": _metric(),
                           "ndcg_at_k": _metric(), "latency_ms": _latency()},
            structural_metrics={"baseline_mrr_mean": 0.7, "graph_mrr_mean": 0.75,
                                "baseline_ndcg_mean": 0.7, "graph_ndcg_mean": 0.75,
                                "recall_non_decreasing": True},
            sc001_improvement_pct=7.14,
            sc002_noninferior=True,
            sc013_noninferior=True,
            per_query=[],
            hard_constraints={"cross_project_leakage_events": 1,
                               "schema_validity_rate": 0.9,
                               "source_locatability_rate": 1.0},
            reproducibility={"non_latency_reproducible": True, "tolerance": 0.01, "checks": []},
        )
        assert report["three_gate_pass"]["hard_constraints_passed"] is False
        assert report["three_gate_pass"]["all_passed"] is False
        assert report["enters_default_path"] is False


class TestStructuralSubsetMetrics:
    def test_relative_improvement_computed(self, report_schema):
        runner = GraphComparisonRunner(_config())
        report = runner.build_report(
            baseline_metrics={"recall_at_k": _metric(), "mrr": _metric(),
                               "ndcg_at_k": _metric(), "latency_ms": _latency()},
            graph_metrics={"recall_at_k": _metric(), "mrr": _metric(),
                           "ndcg_at_k": _metric(), "latency_ms": _latency()},
            structural_metrics={"baseline_mrr_mean": 0.70, "graph_mrr_mean": 0.75,
                                "baseline_ndcg_mean": 0.70, "graph_ndcg_mean": 0.76,
                                "recall_non_decreasing": True},
            sc001_improvement_pct=7.14,
            sc002_noninferior=True,
            sc013_noninferior=True,
            per_query=[],
            hard_constraints={"cross_project_leakage_events": 0,
                               "schema_validity_rate": 1.0,
                               "source_locatability_rate": 1.0},
            reproducibility={"non_latency_reproducible": True, "tolerance": 0.01, "checks": []},
        )
        ssm = report["structural_subset_metrics"]
        assert ssm["baseline_mrr_mean"] == 0.70
        assert ssm["graph_mrr_mean"] == 0.75
        assert abs(ssm["mrr_relative_improvement"] - 7.14) < 0.01
        assert abs(ssm["ndcg_relative_improvement"] - 8.57) < 0.01
        assert ssm["recall_at_k_non_decreasing"] is True


class TestReproducibility:
    def test_tolerance_is_0_01(self, report_schema):
        runner = GraphComparisonRunner(_config())
        report = runner.build_report(
            baseline_metrics={"recall_at_k": _metric(), "mrr": _metric(),
                               "ndcg_at_k": _metric(), "latency_ms": _latency()},
            graph_metrics={"recall_at_k": _metric(), "mrr": _metric(),
                           "ndcg_at_k": _metric(), "latency_ms": _latency()},
            structural_metrics={"baseline_mrr_mean": 0.7, "graph_mrr_mean": 0.75,
                                "baseline_ndcg_mean": 0.7, "graph_ndcg_mean": 0.75,
                                "recall_non_decreasing": True},
            sc001_improvement_pct=7.14,
            sc002_noninferior=True,
            sc013_noninferior=True,
            per_query=[],
            hard_constraints={"cross_project_leakage_events": 0,
                               "schema_validity_rate": 1.0,
                               "source_locatability_rate": 1.0},
            reproducibility={"non_latency_reproducible": True, "tolerance": 0.01, "checks": []},
        )
        assert report["reproducibility"]["tolerance"] == 0.01
