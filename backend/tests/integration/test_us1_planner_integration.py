"""Integration test for query planner wiring into state machine step 3 (T022 Red, US1).

Tests that the query planner is wired into the state machine:
  - Step 3 (query_planning) calls the QueryPlannerAgent (blueprint sec 12)
  - agent_outputs_ref.query_planner.sub_problems is written to the run record
  - Step 4 (parallel_retrieval) uses sub-problem queries

This test MUST FAIL before the planner is wired into state_machine.py (TDD Red).
"""

from __future__ import annotations

import pytest


class TestPlannerWiredIntoStateMachine:
    """Step 3 calls the planner; output is recorded (blueprint sec 12)."""

    def _make_machine_with_planner(self):
        from rag_mcp.agents.query_planner import QueryPlannerAgent
        from rag_mcp.orchestration.state_machine import AgenticStateMachine

        planner = QueryPlannerAgent(model_and_version="test-v1")
        # Mock the LLM decomposition
        planner._llm_decompose = lambda query, ctx: [
            {"query": "who calls validateToken", "signals": ["dense", "graph"]},
            {"query": "what does validateToken depend on", "signals": ["sparse"]},
        ]

        machine = AgenticStateMachine(
            run_id="999",
            request_id="req-1",
            project_scope=["proj-a"],
            knowledge_scope_ids=["100"],
        )
        machine.set_query_planner(planner)
        return machine, planner

    def test_step_3_calls_planner(self):
        """Step 3 (query_planning) should call the planner agent."""
        machine, planner = self._make_machine_with_planner()
        machine.run(context={"query": "multi-hop query"})
        executed = machine.get_executed_steps()
        assert "query_planning" in executed

    def test_planner_output_in_run_record(self):
        """agent_outputs_ref.query_planner.sub_problems should be in the run record."""
        machine, planner = self._make_machine_with_planner()
        machine.run(context={"query": "multi-hop query"})
        record = machine.get_state_envelope().to_dict()
        qp_ref = record["agent_outputs_ref"]["query_planner"]
        assert "sub_problems" in qp_ref
        assert len(qp_ref["sub_problems"]) >= 2
        # sub_problem_id should start from 1
        assert qp_ref["sub_problems"][0]["sub_problem_id"] == 1

    def test_planner_output_has_schema_valid(self):
        """query_planner ref should have schema_valid flag."""
        machine, planner = self._make_machine_with_planner()
        machine.run(context={"query": "multi-hop query"})
        record = machine.get_state_envelope().to_dict()
        qp_ref = record["agent_outputs_ref"]["query_planner"]
        assert "schema_valid" in qp_ref
        assert qp_ref["schema_valid"] is True

    def test_sub_problems_carry_signals(self):
        """Each sub-problem should carry its signals (FR-001)."""
        machine, planner = self._make_machine_with_planner()
        machine.run(context={"query": "multi-hop query"})
        record = machine.get_state_envelope().to_dict()
        qp_ref = record["agent_outputs_ref"]["query_planner"]
        for sp in qp_ref["sub_problems"]:
            assert "signals" in sp
            assert len(sp["signals"]) > 0
            for s in sp["signals"]:
                assert s in ("dense", "sparse", "graph")

    def test_sub_problems_carry_queries(self):
        """Each sub-problem should carry its query text."""
        machine, planner = self._make_machine_with_planner()
        machine.run(context={"query": "multi-hop query"})
        record = machine.get_state_envelope().to_dict()
        qp_ref = record["agent_outputs_ref"]["query_planner"]
        for sp in qp_ref["sub_problems"]:
            assert "query" in sp
            assert len(sp["query"]) > 0

    def test_step4_uses_sub_problem_queries(self):
        """Step 4 (parallel_retrieval) should use sub-problem queries (blueprint sec 12)."""
        machine, planner = self._make_machine_with_planner()
        machine.run(context={"query": "multi-hop query"})
        # The machine should have recorded sub-problem queries for retrieval
        sub_queries = machine.get_retrieval_queries()
        assert len(sub_queries) >= 2
        assert "who calls validateToken" in sub_queries
        assert "what does validateToken depend on" in sub_queries

    def test_trace_recorder_records_planner(self):
        """Trace recorder should record the planner output reference."""
        machine, planner = self._make_machine_with_planner()
        machine.run(context={"query": "multi-hop query"})
        record = machine.get_state_envelope().to_dict()
        # agent_outputs_ref should include query_planner
        assert "query_planner" in record["agent_outputs_ref"]

    def test_sub_problem_id_monotonic_in_record(self):
        """sub_problem_id must be monotonic in the run record (FR-032)."""
        machine, planner = self._make_machine_with_planner()
        machine.run(context={"query": "multi-hop query"})
        record = machine.get_state_envelope().to_dict()
        qp_ref = record["agent_outputs_ref"]["query_planner"]
        ids = [sp["sub_problem_id"] for sp in qp_ref["sub_problems"]]
        for i in range(1, len(ids)):
            assert ids[i] > ids[i-1], f"sub_problem_id not monotonic: {ids}"
