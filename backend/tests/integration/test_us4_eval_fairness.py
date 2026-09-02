"""Integration test for same-session fairness + repeatability (T042 Red, US4).

Tests the fairness orchestration:
  - Same-session rerun baseline then agentic (FR-030)
  - Non-latency metrics consistent within 1% tolerance (SC-008)
  - Latency is environment-sensitive (not a gate)

This test MUST FAIL before fairness orchestration is implemented (TDD Red).
"""

from __future__ import annotations

import pytest


class TestFairnessOrchestration:
    """FR-030/SC-008: same-session fairness + repeatability."""

    def test_runner_has_run_fair_comparison(self):
        """AgenticComparisonRunner must have run_fair_comparison method."""
        from rag_mcp.eval.agentic_comparison import AgenticComparisonRunner
        runner = AgenticComparisonRunner()
        assert hasattr(runner, "run_fair_comparison")
        assert callable(runner.run_fair_comparison)

    def test_fair_comparison_reruns_baseline_first(self):
        """Fair comparison should rerun baseline first, then agentic (FR-030)."""
        from rag_mcp.eval.agentic_comparison import AgenticComparisonRunner
        runner = AgenticComparisonRunner()
        result = runner.run_fair_comparison(
            baseline_fn=lambda: {"recall_at_k": 1.0, "mrr": 0.909, "ndcg": 0.933},
            agentic_fn=lambda: {"recall_at_k": 1.0, "mrr": 0.936, "ndcg": 0.961, "cost": 0.001},
            per_query_data=[],
            beneficiary_subset_improvement=0.03,
            baseline_non_regression=True,
            enhanced_non_regression=True,
            hard_metrics_pass=True,
        )
        assert "report" in result
        assert "rerun_baseline_metrics" in result
        assert "agentic_metrics" in result

    def test_repeatability_tolerance(self):
        """Non-latency metrics should be consistent within 1% tolerance (SC-008)."""
        from rag_mcp.eval.agentic_comparison import AgenticComparisonRunner
        runner = AgenticComparisonRunner()
        result = runner.run_fair_comparison(
            baseline_fn=lambda: {"recall_at_k": 1.0, "mrr": 0.909, "ndcg": 0.933},
            agentic_fn=lambda: {"recall_at_k": 1.0, "mrr": 0.936, "ndcg": 0.961, "cost": 0.001},
            per_query_data=[],
            beneficiary_subset_improvement=0.03,
            baseline_non_regression=True,
            enhanced_non_regression=True,
            hard_metrics_pass=True,
        )
        assert "repeatability" in result
        assert isinstance(result["repeatability"], dict)
        assert "tolerance" in result["repeatability"]
        assert result["repeatability"]["tolerance"] == 0.01  # 1%

    def test_latency_is_environment_sensitive(self):
        """Latency should be marked as environment-sensitive (not a gate)."""
        from rag_mcp.eval.agentic_comparison import AgenticComparisonRunner
        runner = AgenticComparisonRunner()
        result = runner.run_fair_comparison(
            baseline_fn=lambda: {"recall_at_k": 1.0, "mrr": 0.909, "ndcg": 0.933, "p50_ms": 138, "p95_ms": 185},
            agentic_fn=lambda: {"recall_at_k": 1.0, "mrr": 0.936, "ndcg": 0.961, "p50_ms": 420, "p95_ms": 510, "cost": 0.001},
            per_query_data=[],
            beneficiary_subset_improvement=0.03,
            baseline_non_regression=True,
            enhanced_non_regression=True,
            hard_metrics_pass=True,
        )
        assert "latency_sensitive" in result.get("repeatability", {})
        assert result["repeatability"]["latency_sensitive"] is True

    def test_fair_comparison_produces_report(self):
        """Fair comparison should produce a valid comparison report."""
        from rag_mcp.eval.agentic_comparison import AgenticComparisonRunner
        runner = AgenticComparisonRunner()
        result = runner.run_fair_comparison(
            baseline_fn=lambda: {"recall_at_k": 1.0, "mrr": 0.909, "ndcg": 0.933},
            agentic_fn=lambda: {"recall_at_k": 1.0, "mrr": 0.936, "ndcg": 0.961, "cost": 0.001},
            per_query_data=[],
            beneficiary_subset_improvement=0.05,
            baseline_non_regression=True,
            enhanced_non_regression=True,
            hard_metrics_pass=True,
        )
        report = result["report"]
        assert "three_gate_pass" in report
        assert "enters_default_path" in report
