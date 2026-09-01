"""Story integration test for US3: graph-enhanced eval comparison (T027).

Validates AS3.1-3.3: structural subset >=3% improvement gate; 001 11-query
non-inferior gate; hard constraints; three_gate_pass -> enters_default_path.
Uses the GraphComparisonRunner (T025) with representative metrics.

This test MUST FAIL before the eval runner is complete (TDD).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure eval/ (repo root) is importable
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))

from eval.graph_comparison_runner import GraphComparisonRunner


def _config():
    return {
        "embedding_model": "BAAI/bge-m3",
        "reranker_model": "BAAI/bge-reranker-v2-m3",
        "hybrid_collection": "chunks_hybrid_bge-m3_v1",
        "fusion_algorithm": "rrf",
        "dataset_path": "eval/eval_dataset.json",
        "num_queries": 24,
        "structural_subset_size": 7,
        "graph_candidate_budget": 10,
        "graph_total_timeout_ms": 30000,
    }


def _metric(vals):
    return {"mean": sum(vals) / len(vals), "min": min(vals), "max": max(vals)}


def _latency(p50, p95, mean):
    return {"p50": p50, "p95": p95, "mean": mean}


def _baseline_metrics():
    return {
        "recall_at_k": _metric([1.0] * 18),
        "mrr": _metric([0.97] * 18),
        "ndcg_at_k": _metric([0.98] * 18),
        "latency_ms": _latency(152.0, 189.0, 165.0),
    }


def _graph_metrics():
    return {
        "recall_at_k": _metric([1.0] * 18),
        "mrr": _metric([0.97] * 18),
        "ndcg_at_k": _metric([0.98] * 18),
        "latency_ms": _latency(210.0, 250.0, 225.0),
    }


def _structural(improve=True):
    """Structural subset metrics. improve=True -> >=3% relative improvement."""
    if improve:
        return {"baseline_mrr_mean": 0.70, "graph_mrr_mean": 0.75,
                "baseline_ndcg_mean": 0.70, "graph_ndcg_mean": 0.75,
                "recall_non_decreasing": True}
    return {"baseline_mrr_mean": 0.70, "graph_mrr_mean": 0.71,
            "baseline_ndcg_mean": 0.70, "graph_ndcg_mean": 0.71,
            "recall_non_decreasing": True}


def _hc_pass():
    return {"cross_project_leakage_events": 0,
            "schema_validity_rate": 1.0,
            "source_locatability_rate": 1.0}


def _repro():
    return {"non_latency_reproducible": True, "tolerance": 0.01,
            "checks": [{"metric": "recall_at_k.mean", "run_1": 1.0, "run_2": 1.0,
                        "relative_delta": 0.0, "tolerance": 0.01, "passed": True}]}


def _build(structural, sc001_pct, sc002, sc013, hc):
    runner = GraphComparisonRunner(_config())
    return runner.build_report(
        baseline_metrics=_baseline_metrics(), graph_metrics=_graph_metrics(),
        structural_metrics=structural,
        sc001_improvement_pct=sc001_pct,
        sc002_noninferior=sc002, sc013_noninferior=sc013,
        per_query=[], hard_constraints=hc, reproducibility=_repro(),
    )


class TestThreeGatePass:
    def test_all_gates_pass_enter_default_path(self):
        """AS3.1/AS3.3: all gates + hard constraints pass -> enters default path."""
        report = _build(_structural(improve=True), sc001_pct=7.14,
                        sc002=True, sc013=True, hc=_hc_pass())
        assert report["three_gate_pass"]["sc001_structural_improvement"] is True
        assert report["three_gate_pass"]["sc002_001_noninferior"] is True
        assert report["three_gate_pass"]["sc013_002_nonstructural_noninferior"] is True
        assert report["three_gate_pass"]["all_passed"] is True
        assert report["enters_default_path"] is True

    def test_structural_improvement_below_3pct_not_enter(self):
        """AS3.1: structural improvement < 3% -> sc001 fails -> optional path."""
        report = _build(_structural(improve=False), sc001_pct=1.4,
                        sc002=True, sc013=True, hc=_hc_pass())
        assert report["three_gate_pass"]["sc001_structural_improvement"] is False
        assert report["three_gate_pass"]["all_passed"] is False
        assert report["enters_default_path"] is False

    def test_001_noninferior_gate(self):
        """AS3.3: sc002 (001 non-inferior) reflected in three_gate_pass."""
        report = _build(_structural(improve=True), sc001_pct=7.14,
                        sc002=False, sc013=True, hc=_hc_pass())
        assert report["three_gate_pass"]["sc002_001_noninferior"] is False
        assert report["enters_default_path"] is False

    def test_hard_constraint_leakage_blocks(self):
        """Hard constraint: leakage > 0 blocks default path (Constitution hard constraint)."""
        hc = _hc_pass()
        hc["cross_project_leakage_events"] = 1
        report = _build(_structural(improve=True), sc001_pct=7.14,
                        sc002=True, sc013=True, hc=hc)
        assert report["three_gate_pass"]["hard_constraints_passed"] is False
        assert report["enters_default_path"] is False

    def test_structural_relative_improvement_computed(self):
        """structural_subset_metrics reports the relative improvement."""
        report = _build(_structural(improve=True), sc001_pct=7.14,
                        sc002=True, sc013=True, hc=_hc_pass())
        ssm = report["structural_subset_metrics"]
        # 0.70 -> 0.75 = 7.14% relative improvement
        assert ssm["mrr_relative_improvement"] == pytest.approx(7.14, abs=0.01)
        assert ssm["ndcg_relative_improvement"] == pytest.approx(7.14, abs=0.01)


# ---------------------------------------------------------------------------
# T048: the real comparison report artifact (produced by
# eval/run_graph_comparison.py) — measured data, not fabricated metrics.
# ---------------------------------------------------------------------------

_REPORT_PATH = _REPO_ROOT / "eval" / "graph_enhanced_comparison_report.json"


@pytest.fixture(scope="module")
def real_report():
    if not _REPORT_PATH.exists():
        pytest.skip("real report not generated yet; run eval/run_graph_comparison.py")
    with open(_REPORT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def report_schema():
    import sys as _sys
    _sys.path.insert(0, str(_REPO_ROOT / "backend"))
    from tests.contract._graph_schema_helper import (
        common_schema,
        eval_report_schema,
        graph_relations_schema,
        graph_trace_schema,
        inline_refs,
    )
    return inline_refs(
        eval_report_schema(),
        common_schema(),
        graph_relations_schema(),
        graph_trace_schema(),
        eval_report_schema(),
    )


class TestRealComparisonReport:
    """T048: the persisted report MUST be schema-valid and internally consistent."""

    def test_report_conforms_to_contract_schema(self, real_report, report_schema):
        import jsonschema

        jsonschema.validate(real_report, report_schema)

    def test_report_type_and_gate_structure(self, real_report):
        assert real_report["report_type"] == "graph_enhanced_comparison"
        gate = real_report["three_gate_pass"]
        for key in (
            "sc001_structural_improvement",
            "sc002_001_noninferior",
            "sc013_002_nonstructural_noninferior",
            "hard_constraints_passed",
            "all_passed",
        ):
            assert isinstance(gate[key], bool)
        assert real_report["enters_default_path"] == gate["all_passed"]

    def test_per_query_covers_full_dataset(self, real_report):
        cfg = real_report["config"]
        per_query = real_report["per_query_comparison"]
        assert len(per_query) == cfg["num_queries"]
        structural = [q for q in per_query if q.get("is_structural_benefit")]
        assert len(structural) == cfg["structural_subset_size"]
        assert cfg["structural_subset_size"] >= 6

    def test_per_query_carries_graph_path_scores(self, real_report):
        """FR-023: graph-hit queries carry structure weight/hops/edge path."""
        graph_hits = [
            q for q in real_report["per_query_comparison"]
            if q.get("graph_recall_structure_weight") is not None
        ]
        for q in graph_hits:
            assert q["graph_edge_path_summary"], "graph hit must explain its path"
            assert 1 <= q["graph_recall_hop_count"] <= 3

    def test_hard_constraints_measured(self, real_report):
        hc = real_report["hard_constraints"]
        assert hc["cross_project_leakage_events"] == 0
        assert hc["schema_validity_rate"] == 1.0
        assert hc["source_locatability_rate"] == 1.0
        assert hc["all_passed"] is True

    def test_reproducibility_checked(self, real_report):
        repro = real_report["reproducibility"]
        assert repro["tolerance"] == 0.01
        assert repro["checks"], "reproducibility must be checked against a second run"
        assert isinstance(repro["non_latency_reproducible"], bool)

