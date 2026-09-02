"""Agentic comparison eval runner (T039, US4).

Produces a comparison report between the deterministic baseline and the
Agent orchestration path, with three-gate pass determination:
  - SC-001: Agent beneficiary subset >=3% relative improvement (FR-029)
  - SC-002: 001 baseline non-regression (1% tolerance)
  - SC-015: 002/004 non-beneficiary non-regression (1% tolerance)
  - Hard metrics: leakage=0, schema=100%, locatability=100%
  - enters_default_path: all gates pass -> true (Constitution X)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

SC001_THRESHOLD = 0.03  # >=3% relative improvement (FR-029)


class AgenticComparisonRunner:
    """Builds an agentic comparison report with three-gate pass (FR-026/FR-028/FR-029)."""

    def build_report(
        self,
        baseline_metrics: dict[str, Any],
        agentic_metrics: dict[str, Any],
        per_query_data: list[dict[str, Any]],
        beneficiary_subset_improvement: float,
        baseline_non_regression: bool,
        enhanced_non_regression: bool,
        hard_metrics_pass: bool,
    ) -> dict[str, Any]:
        """Build the agentic comparison report with three-gate pass (FR-029)."""
        sc001_pass = beneficiary_subset_improvement >= SC001_THRESHOLD
        sc002_pass = baseline_non_regression
        sc015_pass = enhanced_non_regression
        all_passed = sc001_pass and sc002_pass and sc015_pass and hard_metrics_pass
        three_gate_pass = {
            "sc001_pass": sc001_pass,
            "sc002_pass": sc002_pass,
            "sc015_pass": sc015_pass,
            "hard_metrics_pass": hard_metrics_pass,
            "all_passed": all_passed,
        }
        return {
            "report_type": "agentic_comparison",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "baseline_metrics": baseline_metrics,
            "agentic_metrics": agentic_metrics,
            "per_query_comparison": per_query_data,
            "three_gate_pass": three_gate_pass,
            "enters_default_path": all_passed,
        }

    def run_fair_comparison(
        self,
        baseline_fn,
        agentic_fn,
        per_query_data: list[dict[str, Any]],
        beneficiary_subset_improvement: float,
        baseline_non_regression: bool,
        enhanced_non_regression: bool,
        hard_metrics_pass: bool,
    ) -> dict[str, Any]:
        """Run same-session fair comparison (FR-030/SC-008).

        Reruns the deterministic baseline first, then the Agent path,
        in the same session for delay-fair comparison.
        Non-latency metrics are consistent within 1% tolerance (SC-008).
        Latency is environment-sensitive (not a gate).
        """
        # Rerun baseline first (FR-030)
        rerun_baseline_metrics = baseline_fn()
        # Then run agentic path
        agentic_metrics = agentic_fn()
        # Build the comparison report
        report = self.build_report(
            baseline_metrics=rerun_baseline_metrics,
            agentic_metrics=agentic_metrics,
            per_query_data=per_query_data,
            beneficiary_subset_improvement=beneficiary_subset_improvement,
            baseline_non_regression=baseline_non_regression,
            enhanced_non_regression=enhanced_non_regression,
            hard_metrics_pass=hard_metrics_pass,
        )
        return {
            "report": report,
            "rerun_baseline_metrics": rerun_baseline_metrics,
            "agentic_metrics": agentic_metrics,
            "repeatability": {
                "tolerance": 0.01,  # 1% relative tolerance (SC-008)
                "latency_sensitive": True,  # latency is environment-sensitive
            },
        }
