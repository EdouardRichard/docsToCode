"""T073 Red: the regenerated agentic comparison report must pass SC-001.

T073 acceptance (Phase 9 convergence, SC-001/FR-029): after the query
planner signal-selection improvement, the regenerated
eval/agentic_comparison_report.json must record:

  - sc001_pass=True — the Agent beneficiary subset reaches the >=3%
    relative MRR/nDCG threshold with Recall@K not degraded (SC-001);
  - sc002_pass / sc015_pass / hard_metrics_pass all True (three-gate
    decision unchanged on the non-regression side);
  - reproducibility=True (SC-008), P95 <= 30s (SC-007), non-zero cost;
  - enters_default_path=True (all gates pass -> the Agent orchestration
    path is promoted to the default retrieval path, FR-029).

This test MUST FAIL before the improved eval report is produced (TDD Red).
The report is a committed eval artifact; this test pins its acceptance
state (the same way 001/002/004 pin their baseline reports).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_REPORT_PATH = _REPO_ROOT / "eval" / "agentic_comparison_report.json"

SC001_THRESHOLD_PCT = 3.0
TOTAL_TIMEOUT_GUARDRAIL_MS = 30_000


@pytest.fixture(scope="module")
def report() -> dict:
    assert _REPORT_PATH.exists(), (
        "eval/agentic_comparison_report.json must exist (T061/T073 eval artifact)"
    )
    with open(_REPORT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


class TestReportSC001Threshold:
    """T073: the Agent path must reach the SC-001 threshold (SC-001/FR-029)."""

    def test_sc001_gate_passes(self, report):
        gate = report["three_gate_pass"]
        assert gate["sc001_pass"] is True, (
            "SC-001 must pass: Agent beneficiary-subset MRR/nDCG relative "
            f"improvement must reach >= {SC001_THRESHOLD_PCT}% with Recall@K "
            "not degraded (T073; current: "
            f"mrr={gate.get('sc001_relative_mrr_improvement_pct')}%, "
            f"ndcg={gate.get('sc001_relative_ndcg_improvement_pct')}%)"
        )

    def test_sc001_threshold_reached(self, report):
        gate = report["three_gate_pass"]
        mrr = gate["sc001_relative_mrr_improvement_pct"]
        ndcg = gate["sc001_relative_ndcg_improvement_pct"]
        assert max(mrr, ndcg) >= SC001_THRESHOLD_PCT, (
            f"max(mrr={mrr}%, ndcg={ndcg}%) must be >= {SC001_THRESHOLD_PCT}%"
        )


class TestReportNonRegressionGates:
    """T073: the non-regression side of the three-gate decision must hold."""

    def test_sc002_001_baseline_non_inferior(self, report):
        assert report["three_gate_pass"]["sc002_pass"] is True

    def test_sc015_enhanced_non_regression(self, report):
        assert report["three_gate_pass"]["sc015_pass"] is True

    def test_hard_metrics_all_pass(self, report):
        hard = report["three_gate_pass"]
        assert hard["hard_metrics_pass"] is True
        assert hard["hard_metrics"]["cross_project_leakage_events"] == 0
        assert hard["hard_metrics"]["schema_validity_rate"] == 1.0
        assert hard["hard_metrics"]["source_locatability_rate"] == 1.0


class TestReportOperationalMetrics:
    """T073: reproducibility/latency/cost invariants must not regress."""

    def test_reproducible(self, report):
        assert report["reproducibility"]["reproducible"] is True

    def test_p95_within_total_timeout_guardrail(self, report):
        p95 = report["agentic_metrics"]["latency_ms"]["p95"]
        assert p95 <= TOTAL_TIMEOUT_GUARDRAIL_MS, (
            f"agentic P95 {p95}ms must stay within the 30s total-timeout "
            "guardrail (SC-007)"
        )

    def test_total_cost_non_zero(self, report):
        assert report["agentic_metrics"]["total_cost"] > 0

    def test_dataset_size_unchanged(self, report):
        assert report["dataset_size"] == 44
        assert report["beneficiary_subset_size"] == 14

    def test_enters_default_path_true(self, report):
        assert report["enters_default_path"] is True, (
            "All gates pass -> the Agent orchestration path enters the default "
            "retrieval path (FR-029)"
        )
