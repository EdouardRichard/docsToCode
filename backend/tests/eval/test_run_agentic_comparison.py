"""Tests for the agentic comparison eval runner (T061 Red).

The runner (eval/run_agentic_comparison.py) must:
  - rerun the deterministic baseline first, then the agentic path, in the
    same session (FR-030)
  - compute Recall@K / MRR / nDCG / P50/P95 latency / cost
  - record per-query dual-path ranks + Agent judgment (sub-problems/signals/
    directions/gaps/supplementary rounds/orchestration decision) + sub-path
    timings + ledger refs (FR-028/SC-009)
  - apply the three-gate pass decision (SC-001/SC-002/SC-015) + hard metrics
  - emit enters_default_path (FR-029) and reproducibility (SC-008)

This test MUST FAIL before the runner exists (TDD Red).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "eval"), str(_REPO_ROOT / "backend" / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _entry(query, expected, structural=False, category=None):
    e = {
        "query": query,
        "project_scope": ["100"],
        "expected_evidence_ids": expected,
    }
    if structural:
        e["is_structural_benefit"] = True
    if category:
        e["category"] = category
    return e


def _pq(expected, retrieved, latency=10.0, extra=None):
    d = {
        "expected_evidence_ids": expected,
        "retrieved_evidence_ids": retrieved,
        "latency_ms": latency,
        "status": "complete" if retrieved else "no_evidence",
        "scopes": ["100"],
    }
    if extra:
        d.update(extra)
    return d


class TestRunnerExists:
    def test_module_importable(self):
        import run_agentic_comparison as runner  # noqa: F401

    def test_exposes_report_entry(self):
        from run_agentic_comparison import build_comparison_report

        assert callable(build_comparison_report)


class TestReportAssembly:
    """build_comparison_report with injected synthetic path results."""

    def _synthetic(self, agentic_improves=True):
        from run_agentic_comparison import build_comparison_report

        dataset = [
            _entry("q1", ["1"], structural=False),
            _entry("q2", ["2"], structural=True),
            _entry("q3", ["3"], category="multi_hop"),
        ]
        baseline_results = [
            _pq(["1"], ["1", "9"]),
            _pq(["2"], ["9", "2"]),
            _pq(["3"], ["9"]),
        ]
        if agentic_improves:
            agentic_results = [
                _pq(["1"], ["1", "9"], latency=12.0, extra={"request_id": "r1", "record": {"agent": True}}),
                _pq(["2"], ["2", "9"], latency=12.0, extra={"request_id": "r2", "record": {"agent": True}}),
                _pq(["3"], ["3", "9"], latency=12.0, extra={"request_id": "r3", "record": {"agent": True}}),
            ]
        else:
            agentic_results = [
                _pq(["1"], ["9"], latency=12.0, extra={"request_id": "r1", "record": {"agent": True}}),
                _pq(["2"], ["9", "2"], latency=12.0, extra={"request_id": "r2", "record": {"agent": True}}),
                _pq(["3"], ["9"], latency=12.0, extra={"request_id": "r3", "record": {"agent": True}}),
            ]
        hard_metrics = {
            "cross_project_leakage_events": 0,
            "schema_validity_rate": 1.0,
            "source_locatability_rate": 1.0,
        }
        return build_comparison_report(
            dataset=dataset,
            baseline_results=baseline_results,
            agentic_results=agentic_results,
            hard_metrics=hard_metrics,
            top_k=5,
            baseline_query_count_001=1,
        )

    def test_report_shape(self):
        report = self._synthetic()
        assert report["report_type"] == "agentic_comparison"
        for key in ("baseline_metrics", "agentic_metrics", "per_query_comparison",
                    "three_gate_pass", "enters_default_path", "reproducibility"):
            assert key in report, f"missing {key}"
        for m in (report["baseline_metrics"], report["agentic_metrics"]):
            for key in ("recall_at_k", "mrr", "ndcg_at_k", "p50_latency_ms", "p95_latency_ms"):
                assert key in m, f"metric {key} missing"
        assert "total_cost" in report["agentic_metrics"]

    def test_three_gate_pass_when_improving(self):
        report = self._synthetic(agentic_improves=True)
        gate = report["three_gate_pass"]
        assert gate["sc001_pass"] is True
        assert gate["hard_metrics_pass"] is True
        assert gate["all_passed"] is True
        assert report["enters_default_path"] is True

    def test_no_default_path_when_regressing(self):
        report = self._synthetic(agentic_improves=False)
        assert report["three_gate_pass"]["sc002_pass"] is False or \
               report["three_gate_pass"]["sc015_pass"] is False or \
               report["three_gate_pass"]["sc001_pass"] is False
        assert report["enters_default_path"] is False

    def test_per_query_carries_agent_judgment_slots(self):
        report = self._synthetic()
        for pq in report["per_query_comparison"]:
            assert "query" in pq
            assert "baseline_rank" in pq
            assert "agentic_rank" in pq
            assert "agent_judgment" in pq, "per-query agent judgment slot (FR-028)"
            assert "ledger_ref" in pq, "per-query ledger bridge ref (SC-009)"
            assert "sub_path_timings" in pq

    def test_cold_pass_overrides_latency_cost_tokens(self):
        """T069/T072: latency/cost/token/degraded come from the cold pass;
        rankings stay from the warm metric pass."""
        from run_agentic_comparison import build_comparison_report

        dataset = [_entry("q1", ["1"])]
        baseline_results = [_pq(["1"], ["1"], latency=5.0)]
        agentic_results = [
            _pq(["1"], ["1"], latency=1.0,
                extra={"request_id": "r1", "record": {"total_cost": 0.0}}),
        ]
        cold_agentic = [
            _pq(["1"], ["1"], latency=25.0,
                extra={"request_id": "r1",
                       "record": {"total_cost": 0.5, "total_llm_tokens": 100.0},
                       "degraded_to_deterministic": True}),
        ]
        cold_baseline = [_pq(["1"], ["1"], latency=6.0)]
        hard_metrics = {
            "cross_project_leakage_events": 0,
            "schema_validity_rate": 1.0,
            "source_locatability_rate": 1.0,
        }
        report = build_comparison_report(
            dataset=dataset,
            baseline_results=baseline_results,
            agentic_results=agentic_results,
            hard_metrics=hard_metrics,
            top_k=5,
            baseline_query_count_001=1,
            cold_agentic=cold_agentic,
            cold_baseline=cold_baseline,
        )
        assert report["agentic_metrics"]["p95_latency_ms"] == pytest.approx(25.0)
        assert report["agentic_metrics"]["total_cost"] == pytest.approx(0.5)
        assert report["agentic_metrics"]["estimated_llm_tokens"] == pytest.approx(100.0)
        assert report["agentic_degraded_queries"] == 1
        assert report["per_query_comparison"][0]["agentic_latency_ms"] == pytest.approx(25.0)
        assert report["per_query_comparison"][0]["baseline_latency_ms"] == pytest.approx(6.0)
        assert report["measurement"]["latency_cost_source"]
        # rankings still come from the warm metric pass
        assert report["per_query_comparison"][0]["agentic_rank"] == 1

    def test_fairness_order_baseline_first(self):
        """Same-session fairness: baseline rerun completes before agentic (FR-030)."""
        from run_agentic_comparison import run_fair_paths

        order = []

        async def baseline_fn(entries):
            order.append("baseline_start")
            out = [_pq(["1"], ["1"])] * len(entries)
            order.append("baseline_end")
            return out

        async def agentic_fn(entries):
            order.append("agentic_start")
            out = [_pq(["1"], ["1"], extra={"request_id": f"r{i}", "record": {}})
                   for i in range(len(entries))]
            order.append("agentic_end")
            return out

        import asyncio

        dataset = [_entry("q1", ["1"])]
        baseline, agentic = asyncio.run(
            run_fair_paths(dataset, baseline_fn, agentic_fn)
        )
        assert order == ["baseline_start", "baseline_end", "agentic_start", "agentic_end"]
        assert len(baseline) == 1 and len(agentic) == 1
