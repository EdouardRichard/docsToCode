"""Unit test for agentic comparison eval report (T038 Red, US4).

Tests the AgenticComparisonRunner that produces a comparison report:
  - Metrics: Recall@K/MRR/nDCG/P50/P95/cost (FR-026)
  - per_query_comparison: deterministic vs Agent (FR-028)
  - three_gate_pass: SC-001/SC-002/SC-015/hard_metrics (FR-029)
  - enters_default_path: boolean (FR-029, Constitution X)

This test MUST FAIL before agentic_comparison.py is implemented (TDD Red).
"""

from __future__ import annotations

import pytest


class TestAgenticComparisonImport:
    def test_import_runner(self):
        from rag_mcp.eval.agentic_comparison import AgenticComparisonRunner
        assert AgenticComparisonRunner is not None


class TestReportMetrics:
    """FR-026: report contains Recall@K/MRR/nDCG/P50/P95/cost."""

    def _make_report(self):
        from rag_mcp.eval.agentic_comparison import AgenticComparisonRunner
        runner = AgenticComparisonRunner()
        return runner.build_report(
            baseline_metrics={"recall_at_k": 1.0, "mrr": 0.909, "ndcg": 0.933, "p50_ms": 138, "p95_ms": 185},
            agentic_metrics={"recall_at_k": 1.0, "mrr": 0.936, "ndcg": 0.961, "p50_ms": 420, "p95_ms": 510, "cost": 0.001},
            per_query_data=[],
            beneficiary_subset_improvement=0.03,
            baseline_non_regression=True,
            enhanced_non_regression=True,
            hard_metrics_pass=True,
        )

    def test_report_has_recall_at_k(self):
        report = self._make_report()
        assert "agentic_metrics" in report
        assert "recall_at_k" in report["agentic_metrics"]

    def test_report_has_mrr(self):
        report = self._make_report()
        assert "mrr" in report["agentic_metrics"]

    def test_report_has_ndcg(self):
        report = self._make_report()
        assert "ndcg" in report["agentic_metrics"]

    def test_report_has_p50_p95(self):
        report = self._make_report()
        assert "p50_ms" in report["agentic_metrics"]
        assert "p95_ms" in report["agentic_metrics"]

    def test_report_has_cost(self):
        report = self._make_report()
        assert "cost" in report["agentic_metrics"]

    def test_report_has_baseline_metrics(self):
        report = self._make_report()
        assert "baseline_metrics" in report


class TestPerQueryComparison:
    """FR-028: per_query_comparison with deterministic vs Agent."""

    def test_report_has_per_query_comparison(self):
        from rag_mcp.eval.agentic_comparison import AgenticComparisonRunner
        runner = AgenticComparisonRunner()
        report = runner.build_report(
            baseline_metrics={"recall_at_k": 1.0, "mrr": 0.9, "ndcg": 0.9},
            agentic_metrics={"recall_at_k": 1.0, "mrr": 0.93, "ndcg": 0.93},
            per_query_data=[{"query": "test", "baseline_rank": 1, "agentic_rank": 1}],
            beneficiary_subset_improvement=0.03,
            baseline_non_regression=True,
            enhanced_non_regression=True,
            hard_metrics_pass=True,
        )
        assert "per_query_comparison" in report
        assert isinstance(report["per_query_comparison"], list)


class TestThreeGatePass:
    """FR-029: three_gate_pass with SC-001/SC-002/SC-015/hard_metrics."""

    def test_report_has_three_gate_pass(self):
        report = self._make_report_static()
        assert "three_gate_pass" in report
        assert isinstance(report["three_gate_pass"], dict)

    def test_three_gate_has_sc001(self):
        report = self._make_report_static()
        assert "sc001_pass" in report["three_gate_pass"]

    def test_three_gate_has_sc002(self):
        report = self._make_report_static()
        assert "sc002_pass" in report["three_gate_pass"]

    def test_three_gate_has_sc015(self):
        report = self._make_report_static()
        assert "sc015_pass" in report["three_gate_pass"]

    def test_three_gate_has_hard_metrics(self):
        report = self._make_report_static()
        assert "hard_metrics_pass" in report["three_gate_pass"]

    def test_three_gate_has_all_passed(self):
        report = self._make_report_static()
        assert "all_passed" in report["three_gate_pass"]

    def _make_report_static(self):
        from rag_mcp.eval.agentic_comparison import AgenticComparisonRunner
        runner = AgenticComparisonRunner()
        return runner.build_report(
            baseline_metrics={"recall_at_k": 1.0, "mrr": 0.9, "ndcg": 0.9},
            agentic_metrics={"recall_at_k": 1.0, "mrr": 0.93, "ndcg": 0.93, "cost": 0.001},
            per_query_data=[],
            beneficiary_subset_improvement=0.03,
            baseline_non_regression=True,
            enhanced_non_regression=True,
            hard_metrics_pass=True,
        )


class TestEntersDefaultPath:
    """FR-029: enters_default_path based on three_gate_pass (Constitution X)."""

    def test_all_pass_enters_default(self):
        from rag_mcp.eval.agentic_comparison import AgenticComparisonRunner
        runner = AgenticComparisonRunner()
        report = runner.build_report(
            baseline_metrics={"recall_at_k": 1.0, "mrr": 0.9, "ndcg": 0.9},
            agentic_metrics={"recall_at_k": 1.0, "mrr": 0.93, "ndcg": 0.93, "cost": 0.001},
            per_query_data=[],
            beneficiary_subset_improvement=0.05,
            baseline_non_regression=True,
            enhanced_non_regression=True,
            hard_metrics_pass=True,
        )
        assert report["enters_default_path"] is True

    def test_sc001_fail_does_not_enter(self):
        from rag_mcp.eval.agentic_comparison import AgenticComparisonRunner
        runner = AgenticComparisonRunner()
        report = runner.build_report(
            baseline_metrics={"recall_at_k": 1.0, "mrr": 0.9, "ndcg": 0.9},
            agentic_metrics={"recall_at_k": 1.0, "mrr": 0.91, "ndcg": 0.91, "cost": 0.001},
            per_query_data=[],
            beneficiary_subset_improvement=0.01,  # < 3%
            baseline_non_regression=True,
            enhanced_non_regression=True,
            hard_metrics_pass=True,
        )
        assert report["enters_default_path"] is False

    def test_hard_metrics_fail_does_not_enter(self):
        from rag_mcp.eval.agentic_comparison import AgenticComparisonRunner
        runner = AgenticComparisonRunner()
        report = runner.build_report(
            baseline_metrics={"recall_at_k": 1.0, "mrr": 0.9, "ndcg": 0.9},
            agentic_metrics={"recall_at_k": 1.0, "mrr": 0.95, "ndcg": 0.95, "cost": 0.001},
            per_query_data=[],
            beneficiary_subset_improvement=0.05,
            baseline_non_regression=True,
            enhanced_non_regression=True,
            hard_metrics_pass=False,  # hard metrics failed
        )
        assert report["enters_default_path"] is False
