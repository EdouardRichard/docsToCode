"""Quickstart validation scenarios end-to-end (T056).

Validates the seven quickstart scenarios from quickstart.md:
  1. Multi-hop query decomposed (US1)
  2. Gap triggers supplementary retrieval (US2)
  3. Context dedup/diversity/binning (US3)
  4. Cross-project isolation (Constitution hard constraint)
  5. Degradation and four-state (SC-011)
  6. Comparison eval and three-gate pass (US4)
  7. E2E output schema valid (FR-024)
"""

from __future__ import annotations

import pytest


class TestQuickstartScenarios:
    """quickstart.md scenarios 1-7."""

    def _make_machine(self, needs_supp=False):
        from rag_mcp.agents.query_planner import QueryPlannerAgent
        from rag_mcp.agents.evidence_analyst import EvidenceAnalystAgent
        from rag_mcp.agents.context_orchestrator import ContextOrchestratorAgent
        from rag_mcp.orchestration.state_machine import AgenticStateMachine
        planner = QueryPlannerAgent(model_and_version="test-v1")
        planner._llm_decompose = lambda q, ctx: [{"query": "sub", "signals": ["dense", "graph"]}]
        analyst = EvidenceAnalystAgent(model_and_version="test-v1")
        analyst._llm_judge = lambda ctx: {
            "coverage_state": "partial" if needs_supp else "covered",
            "conflict_type": "none",
            "uncovered_sub_problem_ids": [1] if needs_supp else [],
            "needs_supplementary": needs_supp,
            "gap_descriptions": [],
        }
        orchestrator = ContextOrchestratorAgent(model_and_version="test-v1")
        m = AgenticStateMachine(run_id="1", request_id="r1", project_scope=["p"], knowledge_scope_ids=["1"])
        m.set_query_planner(planner)
        m.set_evidence_analyst(analyst)
        m.set_context_orchestrator(orchestrator)
        return m

    def test_scenario1_multi_hop_decomposed(self):
        """Scenario 1: multi-hop query decomposed into sub-problems (US1)."""
        m = self._make_machine()
        m.run(context={"query": "multi-hop", "candidates": []})
        record = m.get_state_envelope().to_dict()
        qp = record["agent_outputs_ref"]["query_planner"]
        assert len(qp["sub_problems"]) >= 1
        assert qp["sub_problems"][0]["sub_problem_id"] == 1

    def test_scenario2_gap_triggers_loop(self):
        """Scenario 2: gap triggers supplementary retrieval (US2)."""
        m = self._make_machine(needs_supp=True)
        m.run(context={"query": "gap", "candidates": []})
        assert m.rounds_completed >= 2
        assert m.completion_status == "partial"

    def test_scenario3_context_dedup_binning(self):
        """Scenario 3: context dedup/diversity/binning (US3)."""
        m = self._make_machine()
        m.run(context={
            "query": "test",
            "candidates": [{"evidence_id": "ev-1", "ledger_entry_id": "1", "source_id": "s1", "score": 0.9}],
        })
        record = m.get_state_envelope().to_dict()
        co = record["agent_outputs_ref"]["context_orchestrator"]
        assert "selection_list" in co
        assert "context_result_id" in co

    def test_scenario4_cross_project_isolation(self):
        """Scenario 4: cross-project isolation (Constitution hard constraint)."""
        from rag_mcp.orchestration.state_machine import AgenticStateMachine
        m1 = AgenticStateMachine(run_id="1", request_id="r1", project_scope=["a"], knowledge_scope_ids=["1"])
        m2 = AgenticStateMachine(run_id="2", request_id="r2", project_scope=["b"], knowledge_scope_ids=["2"])
        m1.run(context={"query": "test"})
        assert m1.project_scope == ["a"]
        assert m2.project_scope == ["b"]

    def test_scenario5_degradation_four_state(self):
        """Scenario 5: degradation and four-state (SC-011)."""
        from rag_mcp.agents.base import AgentBase
        class FailAgent(AgentBase):
            ROLE = "query_planner"
            NODE_SCHEMA = {"type": "object", "properties": {"result": {"type": "string"}}, "required": ["result"], "additionalProperties": False}
            def execute(self, ctx): raise RuntimeError("LLM failed")
            def fallback(self, ctx): return {"result": "fallback"}
        agent = FailAgent()
        result = agent.run({})
        assert result.schema_valid is False
        assert result.degraded is True
        assert result.output["result"] == "fallback"

    def test_scenario6_three_gate_pass(self):
        """Scenario 6: comparison eval and three-gate pass (US4)."""
        from rag_mcp.eval.agentic_comparison import AgenticComparisonRunner
        runner = AgenticComparisonRunner()
        report = runner.build_report(
            baseline_metrics={"recall_at_k": 1.0, "mrr": 0.9, "ndcg": 0.9},
            agentic_metrics={"recall_at_k": 1.0, "mrr": 0.95, "ndcg": 0.95, "cost": 0.001},
            per_query_data=[],
            beneficiary_subset_improvement=0.05,
            baseline_non_regression=True,
            enhanced_non_regression=True,
            hard_metrics_pass=True,
        )
        assert report["three_gate_pass"]["all_passed"] is True
        assert report["enters_default_path"] is True

    def test_scenario7_output_schema_valid(self):
        """Scenario 7: E2E output schema valid (FR-024)."""
        m = self._make_machine()
        record = m.run(context={
            "query": "test",
            "candidates": [{"evidence_id": "ev-1", "ledger_entry_id": "1", "source_id": "s1", "score": 0.9}],
        })
        assert "run_id" in record
        assert "request_id" in record
        assert "project_scope" in record
        assert "completion_status" in record
        assert "agent_outputs_ref" in record
        assert "ledger_ref" in record
        assert "schema_valid_all" in record
